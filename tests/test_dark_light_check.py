"""
The dark calibration checks that the camera is really dark (core/dark_light.py).

Rig, 2026-10-22: OK was clicked at "Please turn off the laser" with the laser
still on, and nothing noticed. The user's decision (2026-10-26): check it, and
stop the run when it fails. What these tests hold the window to:

  - the reference is the live picture's ⟨I⟩ *before* the laser prompt — the
    prompt keeps delivering frames, and they turn dark once the laser goes;
  - each of the first dark frames is judged on its own: with the trigger off
    the camera runs at its maximum rate while the Arduino keeps flashing the
    laser, so only some frames are lit;
  - frames captured before OK do not count;
  - the finished dark mean is checked too (a laser switched on part-way);
  - a failure undoes the run first, then shows the red window;
  - no black level → skipped, never guessed.

Since Vika's answer to open question 21 (2026-10-28), light above the limit is
not always a stop: a *steady* leak opens the amber window and the operator
chooses (Stop is the default); the laser — light at half the live picture or
more — and a *flickering* leak still stop the run.
"""

import os
import sys
import time

import h5py
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core import dark_light
from core.recorder import CALIBRATION_FILENAME
from core.session import State
from gui import safety_dialog
from gui.main_window import MainWindow

BLACK = 100.0                 # the rig cameras' black level, DU
LIT = BLACK + 33.0            # the weak laser light of 2026-10-22 → limit 3.3 DU
SIZE = 16


def _frame(level: float) -> np.ndarray:
    return np.full((SIZE, SIZE), level, dtype=np.uint16)


class _FakeCamera(QObject):
    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def __init__(self, black_level=BLACK):
        super().__init__()
        self.calls = []
        self.frames_emitted = 0
        self.black_level = black_level

    def set_max_frame_rate(self, enabled):
        self.calls.append(("max", enabled))
        return 118.0 if enabled else self.frame_rate

    def set_trigger(self, on, delay=0):
        self.calls.append(("trigger", on))

    def set_playback_source(self, source): return False
    def stop(self, *a, **k):             pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def stopped(monkeypatch):
    """The red window: records what it was shown with, and the window's
    state at that moment (the run must already be undone)."""
    seen = []

    def _show(parent, measured, limit):
        seen.append({"measured": measured, "limit": limit,
                     "state": parent._state,
                     "collector": parent._dark_cal_collector,
                     "calls": list(parent.camera.calls)})
    monkeypatch.setattr(safety_dialog, "show_dark_not_dark", _show)
    return seen


class _LeakWindow:
    """The amber light-leak window: records what it was shown with and
    answers `answer` (True = Continue). `during(w)` runs while it is open —
    the nested event loop of a real window keeps delivering frames."""

    def __init__(self):
        self.seen = []
        self.answer = False
        self.during = None

    def __call__(self, parent, measured, limit):
        self.seen.append({"measured": measured, "limit": limit,
                          "state": parent._state})
        if self.during is not None:
            self.during(parent)
        return self.answer


@pytest.fixture
def leak(monkeypatch):
    window = _LeakWindow()
    monkeypatch.setattr(safety_dialog, "ask_continue_with_leak", window)
    return window


def _make_window(cam, monkeypatch, tmp_path, n1=10, during_prompt=None):
    def _question(*a, **k):
        if during_prompt is not None and w._state is State.PREVIEW:
            during_prompt(w)
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    w = MainWindow(camera=cam)
    w._output_root = tmp_path
    w.spn_n1.setValue(n1)
    w._set_state(State.PREVIEW)
    return w


@pytest.fixture
def cam():
    return _FakeCamera()


@pytest.fixture
def make(cam, monkeypatch, tmp_path):
    made = []

    def _make(**kw):
        w = _make_window(kw.pop("camera", cam), monkeypatch, tmp_path, **kw)
        made.append(w)
        return w
    yield _make
    for w in made:
        w._set_state(State.PREVIEW)
        w.close()


def _preview(w, level, n=4, age=0.0):
    """Live picture frames, as the stats block samples them (every 0.5 s)."""
    t0 = time.monotonic() - age
    for i in range(n):
        w._last_stats_time = 0.0
        w.camera.frames_emitted = w._frames_seen + 1
        w._on_scos_frame(_frame(level), t0 - 0.1 * (n - 1 - i))


def _start(w):
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.setText("Stop SCOS")
    w.btn_start_scos.blockSignals(False)
    w._start_dark_cal()
    assert w._state is State.DARK_CAL
    w._skip_frames_until = 0          # deliver only the frames each test gives
    w.camera.calls.clear()


def _dark(w, levels, t0=None, dt=0.05):
    """Dark-calibration frames captured after OK (t0 = the cutoff)."""
    t0 = w._dark_light_after if t0 is None else t0
    for i, level in enumerate(levels):
        if w._state is not State.DARK_CAL:
            return
        w.camera.frames_emitted = w._frames_seen + 1
        w._on_scos_frame(_frame(level), t0 + dt * i)


def _dark_attrs(w):
    with h5py.File(w._session_folder / CALIBRATION_FILENAME, "r") as f:
        return dict(f["dark"].attrs)


class TestLimit:

    def test_ninety_percent_drop_from_the_live_picture(self):
        assert dark_light.light_limit(114.0) == (pytest.approx(11.4), "live picture")

    def test_never_below_the_floor(self):
        assert dark_light.light_limit(8.0)[0] == dark_light.FLOOR_DU

    def test_fixed_limit_without_a_reference(self):
        assert dark_light.light_limit(None) == (dark_light.FIXED_LIMIT_DU, "fixed")
        assert dark_light.light_limit(1.0) == (dark_light.FIXED_LIMIT_DU, "fixed")

    def test_only_the_roi_counts(self):
        frame = np.full((4, 4), 100.0)
        frame[0, :] = 900.0                       # light outside the ROI
        mask = np.zeros((4, 4), dtype=bool)
        mask[2:, :] = True
        assert dark_light.roi_light(frame, mask, 100.0) == 0.0


class TestDarkRoomPasses:

    def test_dark_frames_pass_and_the_check_is_recorded(self, make, stopped):
        w = make()
        _preview(w, LIT)
        _start(w)
        assert w._dark_light_ref == pytest.approx(33.0)
        assert w._dark_light_limit == pytest.approx(3.3)
        _dark(w, [BLACK + 1] * 10)
        assert stopped == []
        assert w._state is State.BRIGHT_CAL
        attrs = _dark_attrs(w)
        assert attrs["light_check"] == "passed"
        assert attrs["light_check_basis"] == "live picture"
        assert attrs["light_check_reference_du"] == pytest.approx(33.0)
        assert attrs["light_check_limit_du"] == pytest.approx(3.3)
        assert attrs["light_check_mean_du"] == pytest.approx(1.0)
        assert attrs["light_check_first_frames_max_du"] == pytest.approx(1.0)


class TestLaserOnStops:

    def test_every_frame_lit(self, make, stopped):
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LIT] * 10)
        assert len(stopped) == 1
        assert stopped[0]["measured"] == pytest.approx(33.0)
        assert stopped[0]["limit"] == pytest.approx(3.3)
        assert w._state is State.PREVIEW
        assert w.btn_start_scos.text() == "Start SCOS"
        assert w._session_folder is None

    def test_flicker_one_frame_in_six(self, make, stopped):
        # The camera untriggered at its maximum rate, the Arduino still
        # flashing: their average would be only 5.5 DU above, but the lit
        # frame itself is 33.
        w = make(n1=60)
        _preview(w, LIT)
        _start(w)
        _dark(w, [LIT if i % 6 == 5 else BLACK for i in range(60)])
        assert len(stopped) == 1
        assert stopped[0]["measured"] == pytest.approx(33.0)
        assert w._state is State.PREVIEW

    def test_stopped_at_the_first_lit_frame_not_at_the_end(self, make, stopped):
        w = make(n1=60)
        _preview(w, LIT)
        _start(w)
        _dark(w, [BLACK, BLACK, LIT] + [BLACK] * 57)
        assert len(stopped) == 1
        assert w._frames_seen == 4 + 3     # 4 preview frames + 3 dark ones

    def test_run_undone_before_the_window_opens(self, make, stopped, cam):
        # Undo first, window second (todo K2/K3): the red window runs a nested
        # event loop; still in DARK_CAL, every frame would re-enter the check.
        w = make()
        w.chk_trigger.blockSignals(True)
        w.chk_trigger.setChecked(True)
        w.chk_trigger.blockSignals(False)
        _preview(w, LIT)
        _start(w)
        _dark(w, [LIT])
        assert stopped[0]["state"] is State.PREVIEW
        assert stopped[0]["collector"] is None
        # The set rate back, then the trigger — both before the window.
        assert stopped[0]["calls"] == [("max", False), ("trigger", True)]

    def test_laser_switched_on_part_way_caught_at_the_end(self, make, stopped, caplog):
        # The first second is dark, so the per-frame check passes and ends;
        # the finished dark mean is still checked before anything is written.
        w = make(n1=40)
        _preview(w, LIT)
        _start(w)
        with caplog.at_level("INFO"):
            _dark(w, [BLACK] * 22 + [LIT] * 18)
        assert "first 21 frames dark" in caplog.text   # 1.0 s at 20 Hz
        assert "the dark calibration's mean is" in caplog.text
        assert len(stopped) == 1
        assert stopped[0]["measured"] == pytest.approx(33.0 * 18 / 40)
        assert w._state is State.PREVIEW
        assert w.processor.dark_mean is None
        cal = list(w._output_root.rglob(CALIBRATION_FILENAME))
        assert cal == []

    def test_light_check_failure_logged(self, make, stopped, caplog):
        w = make()
        _preview(w, LIT)
        _start(w)
        with caplog.at_level("WARNING"):
            _dark(w, [LIT])
        assert "Dark calibration STOPPED" in caplog.text


class TestFramesBeforeOkIgnored:

    def test_a_lit_frame_captured_before_ok_does_not_stop_the_run(self, make, stopped):
        # A frame still queued from before the prompt: captured before the
        # cutoff, so the per-frame check must not see it. (With 50 frames its
        # share of the mean, 0.66 DU, stays under the 3.3 DU limit.)
        w = make(n1=50)
        _preview(w, LIT)
        _start(w)
        cutoff = w._dark_light_after
        w.camera.frames_emitted = w._frames_seen + 1
        w._on_scos_frame(_frame(LIT), cutoff - 0.6)
        _dark(w, [BLACK] * 49)
        assert stopped == []
        assert w._state is State.BRIGHT_CAL

    def test_cutoff_is_after_ok(self, make, stopped):
        w = make()
        _preview(w, LIT)
        before = time.monotonic()
        _start(w)
        assert w._dark_light_after >= before + w._DARK_LIGHT_SETTLE_S


class TestReference:

    def test_taken_before_the_prompt(self, make, stopped):
        # While the prompt is open, frames keep arriving — dark once the laser
        # is off. They must not become the reference.
        w = make(during_prompt=lambda w: _preview(w, BLACK))
        _preview(w, LIT)
        _start(w)
        assert w._dark_light_ref == pytest.approx(33.0)

    def test_dark_live_picture_falls_back_to_the_fixed_limit(self, make, stopped):
        w = make()
        _preview(w, BLACK + 1)
        _start(w)
        assert w._dark_light_basis == "fixed"
        _dark(w, [BLACK + 4] * 10)                # under 5 DU: passes
        assert stopped == []
        assert _dark_attrs(w)["light_check_basis"] == "fixed"

    def test_fixed_limit_still_catches_light(self, make, stopped):
        w = make()
        _start(w)                                  # no live picture at all
        assert w._dark_light_ref is None
        _dark(w, [BLACK + 12] * 10)                # leaky probe, 2026-10-19
        assert len(stopped) == 1

    def test_only_the_live_picture_is_kept(self, make, stopped):
        # Dark-calibration frames are not the live picture: a run stopped and
        # started again at once must not take them as its reference.
        w = make(n1=60)
        _preview(w, LIT)
        _start(w)
        kept = list(w._preview_light)
        for i in range(4):
            w._last_stats_time = 0.0
            _dark(w, [BLACK], t0=w._dark_light_after + i)
        assert w._preview_light == kept

    def test_stale_live_picture_is_no_reference(self, make, stopped):
        w = make()
        _preview(w, LIT, age=10.0)
        _start(w)
        assert w._dark_light_ref is None
        assert w._dark_light_basis == "fixed"

    def test_roi_change_drops_the_reference(self, make, stopped):
        w = make()
        _preview(w, LIT)
        mask = np.ones((SIZE, SIZE), dtype=bool)
        w._on_roi_changed(mask, {"cx": 8, "cy": 8, "r": 0})
        _start(w)
        assert w._dark_light_ref is None


class TestSkipped:

    def test_no_black_level_skips_and_says_so(self, make, stopped, monkeypatch):
        w = make(camera=_FakeCamera(black_level=None))
        _preview(w, LIT)
        _start(w)
        assert w._dark_light_watching is False
        _dark(w, [LIT] * 10)                       # cannot be judged
        assert stopped == []
        assert w._state is State.BRIGHT_CAL
        assert _dark_attrs(w)["light_check"] == "skipped - no black level"


# A leak of 10 DU with the weak laser light of 2026-10-22 (33 DU): above the
# 3.3 DU limit, under the laser's 16.5 DU — a leak, not the laser.
LEAK = BLACK + 10.0


class TestJudge:
    """The rules themselves, at their edges (core/dark_light.py)."""

    def test_laser_ceiling_is_half_the_live_picture(self):
        assert dark_light.laser_ceiling(33.0) == pytest.approx(16.5)

    def test_no_ceiling_without_a_reference(self):
        assert dark_light.laser_ceiling(None) is None
        assert dark_light.laser_ceiling(1.0) is None

    def test_under_the_limit_is_dark_whatever_it_flickers(self):
        # The camera's own jitter in the dark is shared with the measurement.
        assert dark_light.judge(3.2, 3.3, 16.5, 1.0) == dark_light.DARK

    def test_steady_leak(self):
        assert dark_light.judge(10.0, 3.3, 16.5, 1e-5) == dark_light.STEADY_LEAK

    def test_flicker_just_over_the_limit_stops(self):
        assert dark_light.judge(10.0, 3.3, 16.5, 1.01e-5) == dark_light.FLICKER

    def test_the_laser_stops_even_when_steady(self):
        assert dark_light.judge(16.5, 3.3, 16.5, 0.0) == dark_light.LASER

    def test_no_reference_stops(self):
        assert dark_light.judge(12.0, 5.0, None, None) == dark_light.NO_REFERENCE

    def test_flicker_effect(self):
        # The 2026-10-19 leak, scaled from the rig: σ_f² ≈ 0.5 DU² at 33 DU.
        sf2, k2 = dark_light.flicker_effect(0.5, 0.0, 1000, 33.0)
        assert sf2 == pytest.approx(0.5)
        assert k2 == pytest.approx(0.5 / 33.0 ** 2)

    def test_pixel_noise_is_taken_off(self):
        # 1000 pixels of variance 2 scatter their mean by 0.002 DU² — not flicker.
        sf2, k2 = dark_light.flicker_effect(0.002, 2.0, 1000, 33.0)
        assert sf2 == pytest.approx(0.0, abs=1e-15)
        assert k2 == pytest.approx(0.0, abs=1e-15)

    def test_frame_light_matches_numpy(self):
        values = [10.0, 10.5, 9.0, 11.25, 10.0]
        acc = dark_light.FrameLight()
        for v in values:
            acc.add(v)
        assert acc.n == 5
        assert acc.mean == pytest.approx(np.mean(values))
        assert acc.var == pytest.approx(np.var(values, ddof=1))


class TestSteadyLeak:

    def test_steady_leak_asks_and_stop_is_the_default(self, make, stopped, leak):
        # The patched window answers False — what Enter / Escape / the X give.
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        assert len(leak.seen) == 1
        assert leak.seen[0]["measured"] == pytest.approx(10.0)
        assert leak.seen[0]["limit"] == pytest.approx(3.3)
        assert stopped == []                      # no red window on top
        assert w._state is State.PREVIEW
        assert w.btn_start_scos.text() == "Start SCOS"
        assert w._session_folder is None
        assert list(w._output_root.rglob(CALIBRATION_FILENAME)) == []
        assert w.processor.dark_mean is None

    def test_continue_measures_and_records_the_leak(self, make, stopped, leak):
        leak.answer = True
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        assert len(leak.seen) == 1
        assert stopped == []
        assert w._state is State.BRIGHT_CAL
        attrs = _dark_attrs(w)
        assert attrs["light_check"] == "steady leak - operator continued"
        assert attrs["light_check_mean_du"] == pytest.approx(10.0)
        assert attrs["light_check_laser_du"] == pytest.approx(16.5)
        assert attrs["light_check_flicker_k2"] == pytest.approx(0.0, abs=1e-12)
        assert attrs["light_check_flicker_limit_k2"] == dark_light.FLICKER_LIMIT_K2

    def test_the_leak_goes_into_the_results_file_too(self, make, stopped, leak):
        leak.answer = True
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        w._start_recorder()
        w._stop_recorder()
        path = next(w._session_folder.glob("rBFi_*.h5"))
        with h5py.File(path, "r") as f:
            meta = dict(f["metadata"].attrs)
        assert meta["dark_light_check"] == "steady leak - operator continued"
        assert meta["dark_light_check_mean_du"] == pytest.approx(10.0)

    def test_a_dark_run_records_passed_in_the_results_file(self, make, stopped, leak):
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [BLACK + 1] * 10)
        w._start_recorder()
        w._stop_recorder()
        path = next(w._session_folder.glob("rBFi_*.h5"))
        with h5py.File(path, "r") as f:
            assert f["metadata"].attrs["dark_light_check"] == "passed"
        assert leak.seen == []

    def test_frames_behind_the_window_are_dropped(self, make, stopped, leak):
        # A real window runs a nested event loop: still DARK_CAL, collector
        # finished. Those frames must neither join the calibration nor open
        # a second window.
        leak.answer = True
        leak.during = lambda w: _dark(w, [LIT] * 5, t0=w._dark_light_after + 5)
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        assert len(leak.seen) == 1
        assert stopped == []
        assert w._state is State.BRIGHT_CAL
        attrs = _dark_attrs(w)
        assert attrs["n_frames"] == 10
        assert attrs["light_check_mean_du"] == pytest.approx(10.0)
        assert w._dark_light_deciding is False

    def test_stopped_behind_the_window_then_continue(self, make, stopped, leak):
        # The camera fails while the window is open: _on_camera_error presses
        # Stop Video, which abandons the calibration. Continue afterwards must
        # find nothing to go on with — not a None collector in a frame slot.
        leak.answer = True
        leak.during = lambda w: w._abandon_calibration("Stopped behind the window")
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        assert len(leak.seen) == 1
        assert w._state is State.PREVIEW
        assert w._session_folder is None
        assert list(w._output_root.rglob(CALIBRATION_FILENAME)) == []
        assert w.processor.dark_mean is None

    def test_a_window_that_cannot_open_stops_the_run(self, make, stopped, monkeypatch):
        # No answer is not consent: the safe answer, Stop.
        def _broken(*a, **k):
            raise RuntimeError("no display")
        monkeypatch.setattr(safety_dialog, "ask_continue_with_leak", _broken)
        w = make()
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 10)
        assert w._state is State.PREVIEW
        assert w._session_folder is None
        assert w._dark_light_deciding is False

    def test_a_leak_does_not_stop_at_the_first_frame(self, make, stopped, leak):
        # Above the limit, under the laser: the first-frames check lets it
        # through; the decision comes once the calibration is complete.
        w = make(n1=40)
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK] * 20)
        assert w._state is State.DARK_CAL
        assert leak.seen == [] and stopped == []

    def test_a_leak_at_the_lasers_level_stops_at_once(self, make, stopped, leak):
        w = make(n1=40)
        _preview(w, LIT)
        _start(w)
        _dark(w, [BLACK + 17] * 3 + [BLACK] * 37)    # 17 ≥ 16.5 DU
        assert len(stopped) == 1
        assert w._frames_seen == 4 + 1
        assert leak.seen == []


class TestFlickeringLeak:

    def test_flickering_leak_stops_without_asking(self, make, stopped, leak, caplog):
        # 8 and 12 DU in turn: σ_f² ≈ 4 DU², κ² moved by ≈ 4 / 23² — far over.
        w = make(n1=20)
        _preview(w, LIT)
        _start(w)
        with caplog.at_level("WARNING"):
            _dark(w, [BLACK + (8 if i % 2 else 12) for i in range(20)])
        assert leak.seen == []
        assert len(stopped) == 1
        assert stopped[0]["measured"] == pytest.approx(10.0)
        assert stopped[0]["state"] is State.PREVIEW
        assert "flickering light" in caplog.text
        assert w._session_folder is None

    def test_a_little_flicker_still_counts_as_steady(self, make, stopped, leak):
        # One frame in 100 is 1 DU brighter: σ_f² ≈ 0.01 DU², κ² moved by
        # ≈ 0.01 / 23² = 1.9e-5 — over the limit. One in 600: 3.1e-6 — steady.
        leak.answer = True
        w = make(n1=600)
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK + 1] + [LEAK] * 599, dt=0.002)
        assert len(leak.seen) == 1
        k2 = _dark_attrs(w)["light_check_flicker_k2"]
        assert 1e-6 < k2 < dark_light.FLICKER_LIMIT_K2

    def test_a_few_brighter_frames_are_flicker(self, make, stopped, leak):
        w = make(n1=100)
        _preview(w, LIT)
        _start(w)
        _dark(w, [LEAK + 1] + [LEAK] * 99, dt=0.01)
        assert leak.seen == []
        assert len(stopped) == 1
