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
