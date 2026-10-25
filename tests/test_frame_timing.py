"""
The Arduino program sent at launch, and the warning when frames arrive at
another rate than the Frame Rate field (rig finding, 2026-10-22).

On the rig the app was opened with External Trigger already ticked from the
last session. It sent no program, so the Arduino ran someone else's: frames
every 100 ms instead of 50, while the results file said 20 Hz. Two fixes, each
tested here:

* the program is sent at launch when the tick is restored — real camera only;
  until it has arrived the camera runs on its own clock (a triggered camera
  without pulses gives up after 10 s) and Start SCOS is refused (the trigger
  switching on later would restart the camera mid dark calibration);
* the median time between capture times over 3 s is compared with the set
  rate, so any cause of a wrong rate is caught — also those the lost-frame
  count cannot see (a skipped trigger pulse never becomes a frame).
"""

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


class _StubCamera(QObject):
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
    persists_settings = True            # the real camera's marker

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0
        self.trigger_calls: list[bool] = []

    def set_trigger(self, enabled, *a, **k):
        self.trigger_calls.append(bool(enabled))
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {}


class _FolderLikeCamera(_StubCamera):
    """A playback source: has no Arduino and sets no real pace."""
    def get_calibration_mat(self):
        return None


def _save_tick(cfg_dir: Path, on: bool = True) -> None:
    (cfg_dir / MainWindow.LOCAL_CONFIG_FILENAME).write_text(
        json.dumps({"external_trigger": on}), encoding="utf-8")


@pytest.fixture
def uploads(monkeypatch):
    """Record _upload_arduino calls instead of compiling and sending a sketch."""
    calls: list[int] = []
    monkeypatch.setattr(MainWindow, "_upload_arduino",
                        lambda self: calls.append(1))
    return calls


@pytest.fixture
def warnings_shown(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "_show_frame_timing_window",
                        lambda self, msg: shown.append(msg))
    return shown


def _close(w):
    w._arduino_debounce.stop()
    w._set_state(State.PREVIEW)      # never close mid-measurement (tests/CLAUDE.md)
    w.close()


@pytest.fixture
def window(uploads, warnings_shown):
    w = MainWindow(camera=_StubCamera())
    w.spn_fps.setValue(20.0)
    w._set_state(State.PREVIEW)
    yield w
    _close(w)


def _feed(w, start: float, period: float, seconds: float, skip_every: int = 0) -> float:
    """Capture times from `start`, one every `period` s, for `seconds`;
    every `skip_every`-th frame lost. Returns the time after the last one."""
    t = start
    n = 0
    while t < start + seconds:
        n += 1
        if not (skip_every and n % skip_every == 0):
            w._check_frame_timing(t)
        t += period
    return t


def _after_settle() -> float:
    return time.monotonic() + MainWindow._RATE_SETTLE_S + 0.5


# ---------------------------------------------------------------------------
# The Arduino program at launch
# ---------------------------------------------------------------------------

class TestUploadAtLaunch:
    def test_restored_tick_sends_the_program(self, isolated_config, uploads):
        _save_tick(isolated_config, True)
        w = MainWindow(camera=_StubCamera())
        try:
            assert w.chk_trigger.isChecked()
            assert uploads == [1]
        finally:
            _close(w)

    def test_no_tick_sends_nothing(self, isolated_config, uploads):
        _save_tick(isolated_config, False)
        w = MainWindow(camera=_StubCamera())
        try:
            assert uploads == []
        finally:
            _close(w)

    def test_playback_never_sends(self, isolated_config, uploads):
        _save_tick(isolated_config, True)
        w = MainWindow(camera=_FolderLikeCamera())
        try:
            assert uploads == []
        finally:
            _close(w)


class TestWhileTheProgramIsOnItsWay:
    def test_start_video_runs_untriggered_while_pending(self, window):
        window.chk_trigger.blockSignals(True)
        window.chk_trigger.setChecked(True)
        window.chk_trigger.blockSignals(False)
        window._arduino_debounce.start()             # an upload is coming
        window._toggle_video(True)
        assert window.camera.trigger_mode == "Off"

    def test_start_video_triggered_once_sent(self, window):
        window.chk_trigger.blockSignals(True)
        window.chk_trigger.setChecked(True)
        window.chk_trigger.blockSignals(False)
        window._toggle_video(True)
        assert window.camera.trigger_mode == "On"

    def test_start_scos_refused_while_pending(self, window, monkeypatch):
        told: list[str] = []
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda _p, title, _t, *a, **k: told.append(title)))
        monkeypatch.setattr(MainWindow, "_prepare_gain",
                            lambda self: pytest.fail("the run must not start"))
        window._arduino_debounce.start()
        window.btn_start_scos.setChecked(True)
        assert told == ["Arduino Not Ready"]
        assert not window.btn_start_scos.isChecked()
        assert window._state == State.PREVIEW

    def test_unticked_during_the_upload_stays_untriggered(self, window):
        # A refused Start SCOS (or Stop Video) unlocks the box; the operator
        # unticks it before the program has arrived.
        window.chk_trigger.blockSignals(True)
        window.chk_trigger.setChecked(False)
        window.chk_trigger.blockSignals(False)
        window._on_arduino_done(True, "Arduino ready")
        assert True not in window.camera.trigger_calls

    def test_trigger_delay_keeps_trigger_off_while_pending(self, window):
        window.chk_trigger.blockSignals(True)
        window.chk_trigger.setChecked(True)
        window.chk_trigger.blockSignals(False)
        window._arduino_debounce.start()
        window.spn_trigger_delay.setValue(100.0)
        assert window.camera.trigger_calls[-1] is False


# ---------------------------------------------------------------------------
# Frame timing vs the set rate
# ---------------------------------------------------------------------------

class TestFrameTiming:
    def test_right_rate_no_warning(self, window, warnings_shown, caplog):
        with caplog.at_level(logging.INFO, logger="gui.main_window"):
            _feed(window, _after_settle(), 0.050, 5.0)
        assert warnings_shown == []
        assert "Frame timing OK: 20.00 Hz measured" in caplog.text

    def test_stale_arduino_program_warns_once(self, window, warnings_shown):
        window.chk_trigger.blockSignals(True)
        window.chk_trigger.setChecked(True)
        window.chk_trigger.blockSignals(False)
        _feed(window, _after_settle(), 0.100, 10.0)  # the 2026-10-22 rig case
        assert len(warnings_shown) == 1
        assert "10.0 Hz" in warnings_shown[0] and "20 Hz" in warnings_shown[0]
        assert "old program" in warnings_shown[0]

    def test_unreachable_rate_free_running(self, window, warnings_shown):
        window.spn_fps.setValue(150.0)               # changed during preview
        _feed(window, _after_settle(), 1 / 76.5, 4.0)   # USB limit, rig 2026-10-19
        assert len(warnings_shown) == 1
        assert "exposure is too long" in warnings_shown[0]

    def test_too_slow_names_lost_frames(self, window, warnings_shown):
        _feed(window, _after_settle(), 0.100, 4.0)
        assert "frame(s) lost" in warnings_shown[0]

    def test_too_fast_warns_too(self, window, warnings_shown):
        _feed(window, _after_settle(), 0.025, 4.0)
        assert len(warnings_shown) == 1
        # Lost frames, a long exposure or a loose wire cannot make it faster.
        assert "frame(s) lost" not in warnings_shown[0]
        assert "exposure is too long" not in warnings_shown[0]
        assert "frame-rate limit is off" in warnings_shown[0]

    def test_warning_during_a_run_says_the_run_goes_on(self, window, warnings_shown):
        window._set_state(State.MEASURING)
        _feed(window, _after_settle(), 0.100, 4.0)
        assert len(warnings_shown) == 1
        assert "The run goes on" in warnings_shown[0]

    def test_occasional_lost_frames_are_not_a_wrong_rate(self, window, warnings_shown):
        _feed(window, _after_settle(), 0.050, 6.0, skip_every=10)
        assert warnings_shown == []

    def test_arduino_whole_milliseconds_within_tolerance(self, window, warnings_shown):
        # 150 Hz asks the Arduino for round(6.67) = 7 ms → 142.9 Hz, 4.8 % off.
        window.spn_fps.setValue(150.0)
        _feed(window, _after_settle(), 0.007, 4.0)
        assert warnings_shown == []

    def test_dark_calibration_is_fast_on_purpose(self, window, warnings_shown):
        window._set_state(State.DARK_CAL)
        _feed(window, _after_settle(), 1 / 124.0, 4.0)
        assert warnings_shown == []

    def test_not_while_the_arduino_program_is_on_its_way(self, window, warnings_shown):
        window._arduino_debounce.start()
        _feed(window, _after_settle(), 0.100, 0.9)   # the 1 s debounce
        window._arduino_debounce.stop()
        assert warnings_shown == []
        assert len(window._rate_times) == 0

    def test_playback_is_not_checked(self, uploads, warnings_shown):
        w = MainWindow(camera=_FolderLikeCamera())
        try:
            w.spn_fps.setValue(40.0)
            w._set_state(State.PREVIEW)
            _feed(w, _after_settle(), 1 / 21.0, 4.0)   # dev PC playback speed
            assert warnings_shown == []
        finally:
            _close(w)

    def test_queued_frames_from_before_a_change_are_ignored(self, window, warnings_shown):
        # 20 Hz frames captured before the operator set 10 Hz still wait in
        # Qt's queue when the change is made; only the new pace may be judged.
        window._rate_ignore_before = 0.0               # preview has run a while
        now = time.monotonic()
        window.spn_fps.setValue(10.0)
        _feed(window, now - 3.0, 0.050, 3.0)          # old pace, already captured
        _feed(window, _after_settle(), 0.100, 4.0)    # new pace
        assert warnings_shown == []

    def test_warning_rearms_after_the_rate_recovers(self, window, warnings_shown):
        t = _feed(window, _after_settle(), 0.100, 4.0)
        t = _feed(window, t, 0.050, 4.0)
        _feed(window, t, 0.100, 4.0)
        assert len(warnings_shown) == 2

    def test_a_wrong_rate_through_a_whole_run_warns_once(self, window, warnings_shown):
        # Not again in every phase — the window would come back over the
        # laser prompts each time.
        t = _after_settle()
        for state in (State.BRIGHT_CAL, State.PREVIEW, State.MEASURING_INIT,
                      State.MEASURING, State.FINISHED, State.PREVIEW):
            t = _feed(window, max(t, _after_settle()), 0.100, 4.0)
            window._set_state(state)
        assert len(warnings_shown) == 1

    def test_a_settings_change_rearms(self, window, warnings_shown):
        _feed(window, _after_settle(), 0.100, 4.0)
        window.spn_exposure.setValue(9.0)            # the operator tries a fix
        _feed(window, _after_settle(), 0.100, 4.0)   # still wrong
        assert len(warnings_shown) == 2

    def test_not_over_a_modal_dialog(self, window, warnings_shown, monkeypatch):
        monkeypatch.setattr(QApplication, "activeModalWidget",
                            staticmethod(lambda: object()))    # a laser prompt
        t = _feed(window, _after_settle(), 0.100, 4.0)
        assert warnings_shown == []
        monkeypatch.setattr(QApplication, "activeModalWidget",
                            staticmethod(lambda: None))        # closed
        _feed(window, t, 0.100, 0.5)
        assert len(warnings_shown) == 1

    def test_wired_into_the_frame_handler(self, window, warnings_shown):
        frame = np.zeros((16, 16), dtype=np.uint16)
        t = _after_settle()
        for _ in range(50):
            window._on_scos_frame(frame, t)
            t += 0.100
        assert len(warnings_shown) == 1
