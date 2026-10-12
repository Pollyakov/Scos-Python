"""
The dark calibration runs at the camera's maximum frame rate (todo U11).

Vika (2026-10-08): the spacing between dark frames does not matter, so the
camera's frame-rate limit is switched off for DARK_CAL. What these tests hold
the window to — docs/Plan_U11_Dark_Cal_Max_Rate.md, section 4:

  - every way out of DARK_CAL puts the set rate back, and before the external
    trigger is switched back on (pitfalls P1, P2, P3);
  - when the GUI thread falls behind the camera, surplus dark frames are
    dropped before any expensive work, so Qt's queue cannot grow (decision D1);
    the collector still gets exactly N1 frames;
  - Calibration.h5 records how the dark calibration ran (decision D2).
"""

import os
import sys

import h5py
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow

N1 = 10                                   # the Dark Frames box's minimum
FRAME = np.full((16, 16), 100, dtype=np.uint16)


class _FakeCamera(QObject):
    """Records, in order, every call that changes how the camera runs."""

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

    def __init__(self, reported_rate=118.0):
        super().__init__()
        self.calls = []
        self.frames_emitted = 0
        self.reported_rate = reported_rate

    def set_max_frame_rate(self, enabled):
        self.calls.append(("max", enabled))
        return self.reported_rate if enabled else self.frame_rate

    def set_trigger(self, on, delay=0):
        self.calls.append(("trigger", on))

    def stop(self, *a, **k):
        self.calls.append(("stop",))

    def set_playback_source(self, source): return False
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def cam():
    return _FakeCamera()


@pytest.fixture
def win(cam, monkeypatch, tmp_path):
    # OK at both laser prompts: the dark one in _start_dark_cal and the bright
    # one that _finish_dark_cal opens at once.
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = MainWindow(camera=cam)
    w._output_root = tmp_path
    w.spn_n1.setValue(N1)
    yield w
    w._set_state(State.PREVIEW)     # not mid-calibration: no probe window at close
    w.close()


def _start(w, cam, trigger_was_on=False):
    w.chk_trigger.blockSignals(True)
    w.chk_trigger.setChecked(trigger_was_on)
    w.chk_trigger.blockSignals(False)
    # The Start SCOS button as Start SCOS leaves it, so Stop SCOS can be pressed.
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.setText("Stop SCOS")
    w.btn_start_scos.blockSignals(False)
    w._start_dark_cal()
    assert w._state is State.DARK_CAL
    # The flush at the laser prompt discards the next couple of arrivals as
    # captured before it; these tests deliver only post-prompt frames.
    w._skip_frames_until = 0
    cam.calls.clear()


def _feed(w, cam, n, behind=0):
    """Deliver n frames as the camera thread would, `behind` frames behind it."""
    for i in range(n):
        cam.frames_emitted = w._frames_seen + 1 + behind
        w._on_scos_frame(FRAME, 0.05 * i)


class TestSwitchedOnAndBack:

    def test_on_for_the_dark_calibration_after_the_trigger_is_off(self, win, cam):
        win._start_dark_cal()
        assert win._state is State.DARK_CAL
        assert cam.calls == [("trigger", False), ("max", True)]

    def test_back_before_the_trigger_is_switched_on_again(self, win, cam):
        # With the trigger On the camera's restart writes no frame-rate setting,
        # so the set rate must already be back when it happens (pitfall P1).
        _start(win, cam, trigger_was_on=True)
        _feed(win, cam, N1)
        assert win._state is State.BRIGHT_CAL
        assert cam.calls == [("max", False), ("trigger", True)]

    def test_back_with_the_trigger_off_too(self, win, cam):
        _start(win, cam, trigger_was_on=False)
        _feed(win, cam, N1)
        assert cam.calls == [("max", False)]

    def test_back_on_stop_scos_with_the_trigger_off(self, win, cam):
        # The restore sits under "dark calibration pending" alone, not under
        # the trigger condition next to it (pitfall P2).
        _start(win, cam, trigger_was_on=False)
        _feed(win, cam, 3)
        win.btn_start_scos.setChecked(False)
        assert win._state is State.PREVIEW
        assert cam.calls == [("max", False)]

    def test_back_on_stop_scos_with_the_trigger_on(self, win, cam):
        _start(win, cam, trigger_was_on=True)
        win.btn_start_scos.setChecked(False)
        assert cam.calls == [("max", False), ("trigger", True)]

    def test_back_after_a_camera_error(self, win, cam):
        # No frames for 10 s → Stop Video → Stop SCOS → _abandon_calibration.
        win.btn_start_video.blockSignals(True)
        win.btn_start_video.setChecked(True)
        win.btn_start_video.blockSignals(False)
        _start(win, cam)
        win._on_camera_error("No frames received for 10 s")
        assert ("max", False) in cam.calls
        assert win._state is State.IDLE

    def test_back_after_a_collector_error(self, win, cam, monkeypatch):
        class _Failing:
            n_collected = n_target = N1
            done, window_size = True, 7
            def add_frame(self, frame): pass
            def result(self, **kw): raise RuntimeError("not enough frames")

        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        _start(win, cam, trigger_was_on=True)
        win._dark_cal_collector = _Failing()
        win._finish_dark_cal()
        assert win._state is State.PREVIEW
        assert cam.calls[0] == ("max", False)
        assert cam.calls.index(("trigger", True)) > 0

    def test_back_when_the_window_is_closed_mid_calibration(self, cam, monkeypatch,
                                                            tmp_path):
        # A camera keeps the setting until it is powered off (pitfall P3).
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        w = MainWindow(camera=cam)
        w._output_root = tmp_path
        _start(w, cam)
        w.close()
        assert cam.calls.index(("max", False)) < cam.calls.index(("stop",))

    def test_a_frame_source_without_the_switch_is_left_alone(self, monkeypatch, tmp_path):
        class _Old(_FakeCamera):
            set_max_frame_rate = None
        cam = _Old()
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        w = MainWindow(camera=cam)
        w._output_root = tmp_path
        w._start_dark_cal()
        assert w._state is State.DARK_CAL
        w._set_state(State.PREVIEW)
        w.close()

    def test_a_failed_restore_stops_the_run(self, win, cam, monkeypatch):
        # Measuring on would record 20 Hz in Params while the camera ran at its
        # maximum. Nothing may raise out of the Qt slot, and the run must not
        # reach the bright calibration.
        def _broken(enabled):
            if not enabled:
                raise RuntimeError("node not writable")
            return None
        shown = []
        monkeypatch.setattr(QMessageBox, "critical",
                            staticmethod(lambda *a, **k: shown.append(a[1])))
        _start(win, cam)
        cam.set_max_frame_rate = _broken
        _feed(win, cam, N1)
        assert win._state is State.PREVIEW
        assert shown == ["Frame Rate Not Restored"]
        assert win._session_folder is None, "the run's folder was not dealt with"

    def test_a_restore_that_works_the_next_time_clears_the_failure(self, win, cam,
                                                                  monkeypatch):
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        broken = {"on": True}
        original = cam.set_max_frame_rate

        def _sometimes(enabled):
            if not enabled and broken["on"]:
                raise RuntimeError("node not writable")
            return original(enabled)
        cam.set_max_frame_rate = _sometimes
        _start(win, cam)
        _feed(win, cam, N1)                 # first run: stopped
        assert win._state is State.PREVIEW
        broken["on"] = False
        _start(win, cam)                    # second run: the restore works
        _feed(win, cam, N1)
        assert win._state is State.BRIGHT_CAL


class TestSurplusDarkFramesAreDropped:

    def test_frames_far_behind_the_camera_are_skipped(self, win, cam):
        _start(win, cam)
        _feed(win, cam, 1)                                  # sets the floor at 0
        _feed(win, cam, 4, behind=win._DARK_MAX_BACKLOG + 5)
        assert win._dark_cal_collector.n_collected == 1
        assert win._dark_skipped == 4

    def test_a_small_backlog_is_still_collected(self, win, cam):
        _start(win, cam)
        _feed(win, cam, 1)
        _feed(win, cam, 4, behind=win._DARK_MAX_BACKLOG)
        assert win._dark_cal_collector.n_collected == 5
        assert win._dark_skipped == 0

    def test_the_collector_still_gets_exactly_n1(self, win, cam):
        # Alternate far behind / caught up: half the frames are dropped, and
        # the calibration ends after N1 collected, not N1 delivered.
        _start(win, cam)
        _feed(win, cam, 1)
        while win._state is State.DARK_CAL:
            _feed(win, cam, 1, behind=20)
            if win._state is State.DARK_CAL:
                _feed(win, cam, 1)
        assert win._dark_skipped == N1 - 1
        with h5py.File(win._session_folder / "Calibration.h5", "r") as f:
            assert f["dark"].attrs["n_frames"] == N1

    def test_skipped_frames_cost_nothing(self, win, cam, monkeypatch):
        # Dropped before the intensity statistics and the DU conversion —
        # otherwise dropping would not let the thread catch up.
        _start(win, cam)
        _feed(win, cam, 1)
        converted = []
        monkeypatch.setattr(win, "_to_du", lambda f: converted.append(1) or f)
        win._last_stats_time = 0.0                          # stats due now
        _feed(win, cam, 1, behind=20)
        assert converted == [] and win._dark_skipped == 1

    def test_a_constant_counter_offset_never_stalls_the_calibration(self, win, cam):
        # If the camera's count and the window's ever disagreed by a constant,
        # every frame would look late. Counted from the smallest backlog seen,
        # the offset cancels and the calibration finishes.
        _start(win, cam)
        _feed(win, cam, N1, behind=50)
        assert win._state is State.BRIGHT_CAL
        assert win._dark_skipped == 0

    def test_only_dark_frames_are_ever_dropped(self, win, cam):
        _start(win, cam)
        _feed(win, cam, N1)
        assert win._state is State.BRIGHT_CAL
        win._skip_frames_until = 0
        _feed(win, cam, 3, behind=20)
        assert win._bright_cal_collector.n_collected == 3


class TestCalibrationFileRecordsTheRate:

    def _dark_attrs(self, win):
        with h5py.File(win._session_folder / "Calibration.h5", "r") as f:
            return dict(f["dark"].attrs)

    def test_duration_and_reported_rate_are_saved(self, win, cam):
        _start(win, cam)
        _feed(win, cam, N1)
        attrs = self._dark_attrs(win)
        assert attrs["duration_s"] == pytest.approx(0.05 * (N1 - 1))
        assert attrs["camera_frame_rate_hz"] == pytest.approx(118.0)
        assert attrs["n_frames"] == N1

    def test_a_rate_the_camera_does_not_report_is_left_out(self, monkeypatch, tmp_path):
        # The pylon emulator reports none; never guessed.
        cam = _FakeCamera(reported_rate=None)
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        w = MainWindow(camera=cam)
        w._output_root = tmp_path
        w.spn_n1.setValue(N1)
        _start(w, cam)
        _feed(w, cam, N1)
        attrs = self._dark_attrs(w)
        assert "camera_frame_rate_hz" not in attrs
        assert "duration_s" in attrs
        w._set_state(State.PREVIEW)
        w.close()
