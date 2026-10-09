"""
End-of-session laser-off check (todo.md E3 / merged_worklist task 11).

When a measurement ends the operator is told to switch the laser off, and the
app then verifies that it actually went off: one fresh frame is captured and
its mean ROI intensity must have fallen by at least 90 % against the intensity
the measurement itself was seeing.

The point is not accuracy against MATLAB. It is that a session ruined by a
laser left running gets noticed at the rig, with the operator still standing
there, rather than during analysis weeks later.

Two details carry most of the weight, and both are deliberate:

  * **The comparison is dark-subtracted**, exactly as process() computes the
    Intensity written to the results file. The camera's black level (100 DU on
    this rig) does not go away when the laser does, so a raw-DU reading could
    never fall by 90 % no matter how dark the room got — the check would fail
    every single time.

  * **The check can never cost the session its data.** It runs before
    _finish_session() writes rBFi, so every way it can go wrong — no reference
    intensity, no frame from the camera, an operator who declines it — has to
    end with the recording still saved.

Since U2 (2026-10-09) a failed check opens a large red window
(`safety_dialog.LaserStillOnDialog`, "Check again" / "Continue anyway"), and
once the session is saved a probe-removal window says when the probe may come
off: green after a passed check, amber when the check could not run, red after
"Continue anyway" (the user's idea and decisions). The windows themselves are
tested in tests/test_laser_safety.py; here they are replaced by recorders.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# QApplication must exist before gui.main_window is imported: that import pulls
# in pyqtgraph, and building the application afterwards kills the interpreter
# outright — no traceback, exit code 127. See CLAUDE.md.
_app = QApplication.instance() or QApplication([])

from gui.main_window import MainWindow
from gui import safety_dialog
from core.session import BrightCalCollector, State


FRAME_SHAPE = (64, 64)
DARK_LEVEL  = 100.0     # camera black level, as on the rig (BL100DU)


class _FakeCamera(QObject):
    """Frame source stand-in. Emits only when the test tells it to."""

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

    def __init__(self):
        super().__init__()
        self.is_synthetic   = False
        self.frames_emitted = 0

    def emit_frame(self, level: float):
        """Hand the window one uniform frame at `level` raw DU."""
        frame = np.full(FRAME_SHAPE, level, dtype=np.uint16)
        self.frames_emitted += 1
        self.frame_ready.emit(frame, float(self.frames_emitted))

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass

    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    """Answer every modal this path opens, and record what it said.

    conftest.py blocks these suite-wide so a test can never hang on a window
    nobody can click; a test that drives one has to say so explicitly. This
    path opens: the "turn off the laser" prompt (information), the red
    "laser may still be on" window (`laser_on`, with the measured and expected
    numbers it was given), the probe-removal window (`probe`, the outcome it
    was shown for), and the output-folder picker.

    `continue_replies` is a queue of answers to the red window — True is
    "Continue anyway", False "Check again"; one is popped per call while more
    than one remains, which is what lets a test drive the check-again loop.
    """
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)),
    )
    seen = {"information": [], "question": [], "warning": [], "critical": []}
    question_replies = [QMessageBox.StandardButton.Yes]

    def _grab(kind, queue=None):
        def _fn(parent, title, text, *a, **k):
            seen[kind].append((title, text))
            if queue is None:
                return QMessageBox.StandardButton.Ok
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return staticmethod(_fn)

    monkeypatch.setattr(QMessageBox, "information", _grab("information"))
    monkeypatch.setattr(QMessageBox, "question",    _grab("question", question_replies))
    monkeypatch.setattr(QMessageBox, "warning",     _grab("warning"))
    monkeypatch.setattr(QMessageBox, "critical",    _grab("critical"))
    seen["question_replies"] = question_replies

    seen["laser_on"] = []
    seen["probe"] = []
    continue_replies = [True]

    def _laser_on(parent, measured, expected):
        seen["laser_on"].append((measured, expected))
        return (continue_replies.pop(0) if len(continue_replies) > 1
                else continue_replies[0])

    monkeypatch.setattr(safety_dialog, "ask_continue_with_laser_on", _laser_on)
    monkeypatch.setattr(safety_dialog, "show_probe_removal",
                        lambda parent, outcome: seen["probe"].append(outcome))
    seen["continue_replies"] = continue_replies
    return seen


@pytest.fixture
def window(dialogs):
    """A MainWindow parked mid-measurement with a fake camera.

    The state is set directly rather than driven through calibration: what is
    under test is the stop path, not the way in.
    """
    cam = _FakeCamera()
    w = MainWindow(camera=cam)
    w._mask      = np.ones(FRAME_SHAPE, dtype=bool)
    w._scos_mask = np.ones(FRAME_SHAPE, dtype=bool)
    w.processor.dark_mean = np.full(FRAME_SHAPE, DARK_LEVEL)
    w.processor.scale     = 1.0
    w._set_state(State.MEASURING)
    yield w
    # Closing mid-measurement now runs the whole Stop path (laser prompt, a
    # 2 s wait for a frame, the probe window); it has its own tests below.
    w._set_state(State.PREVIEW)
    w.close()


def _run_check(window, ref_intensity, off_level):
    """Give the window a measurement history, then run the laser-off check.

    `ref_intensity` is dark-subtracted, the way process() reports it.
    `off_level` is the raw DU the fake camera emits once the laser is "off";
    the check subtracts dark_mean from it before comparing.
    """
    for i in range(10):
        window._intensity_history.append((float(i), ref_intensity))

    # The window waits for its frame inside a nested event loop, so frames have
    # to be delivered *from* the event loop, not from this stack. A free-running
    # camera is also the honest model: the check may run more than once (No on
    # "Continue anyway?"), and each pass needs its own fresh frame, so a fixed
    # number of one-shot emissions would not do.
    feeder = QTimer()
    feeder.setInterval(5)
    feeder.timeout.connect(lambda: window.camera.emit_frame(off_level))
    feeder.start()
    try:
        return window._laser_off_check(window._scos_mask)
    finally:
        feeder.stop()


def test_laser_off_passes_when_intensity_drops(window, dialogs):
    """Laser genuinely off: prompt shown, check passes, no warning."""
    # The run saw 500 DU above dark; the laser-off frame reads the black level
    # and nothing more, so the drop is total.
    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL)

    assert len(dialogs["information"]) == 1, "the operator was told to switch the laser off"
    assert "turn off the laser" in dialogs["information"][0][1].lower()
    assert dialogs["laser_on"] == [], "a passing check must not warn"
    assert window._laser_off_outcome == safety_dialog.PROBE_PASSED


def test_laser_still_on_warns_with_the_measured_numbers(window, dialogs):
    """Laser left on: the red window opens with measured and expected.

    (That the window prints both numbers is tested on the window itself, in
    tests/test_laser_safety.py.)
    """
    # 500 DU above dark during the run, 400 above dark afterwards — a 20 %
    # fall, nowhere near the 90 % required.
    note = _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL + 400)

    assert len(dialogs["laser_on"]) == 1, "the operator was warned"
    measured, expected = dialogs["laser_on"][0]
    assert measured == pytest.approx(400.0), "the measured value is passed on"
    assert expected == pytest.approx(50.0), "and the ceiling, 10 % of 500"
    assert dialogs["question"] == [], "the old plain question box is gone"
    assert "LASER-OFF CHECK FAILED" in note, "Continue anyway still reports the failure"
    assert window._laser_off_outcome == safety_dialog.PROBE_FAILED


def test_check_again_re_runs_the_check(window, dialogs):
    """'Check again' measures again; it never discards the data."""
    dialogs["continue_replies"][:] = [
        False,    # first warning: Check again
        True,     # second warning: Continue anyway
    ]
    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL + 400)

    assert len(dialogs["laser_on"]) == 2, "Check again ran the check a second time"
    assert len(dialogs["information"]) == 2, "and re-prompted for the laser"


def test_no_reference_intensity_skips_the_check(window, dialogs):
    """A run stopped before any result: prompt still shown, nothing crashes."""
    window._intensity_history = []
    window._laser_off_check(window._scos_mask)

    assert len(dialogs["information"]) == 1, "the operator is still told to kill the laser"
    assert dialogs["laser_on"] == [], "nothing to compare against, so nothing to warn about"
    assert window._laser_off_outcome == safety_dialog.PROBE_SKIPPED


def test_no_frame_arriving_does_not_hang(window, dialogs, monkeypatch):
    """A camera that has already stopped must time out, not strand the operator."""
    monkeypatch.setattr(type(window), "_LASER_OFF_TIMEOUT_MS", 50)
    for i in range(10):
        window._intensity_history.append((float(i), 500.0))

    window._laser_off_check(window._scos_mask)   # no frame is ever emitted

    assert len(dialogs["information"]) == 1
    assert dialogs["laser_on"] == [], "a timeout is not a failed check"
    assert window._laser_off_outcome == safety_dialog.PROBE_SKIPPED


def test_reference_is_the_trailing_average_not_the_last_value(window):
    """One noisy final frame must not set the reference on its own.

    This is the open question to the supervisor, answered by a default: a
    momentary shadow on the last frame would otherwise drag the reference down
    and let a laser that is still on sail through the check.
    """
    window._intensity_history = [(t, 500.0) for t in range(10)]
    window._intensity_history.append((10.0, 5.0))    # one bad final reading

    ref = window._reference_intensity()
    assert ref > 300.0, "a single outlier must not define the reference"


def _press_stop(window):
    """Drive Stop SCOS from MEASURING, the way the button does."""
    window.btn_start_scos.blockSignals(True)
    window.btn_start_scos.setChecked(True)
    window.btn_start_scos.blockSignals(False)
    window._set_state(State.MEASURING)
    window._toggle_scos(False)


def test_stop_passes_the_processing_roi_to_the_check(window, dialogs, monkeypatch):
    """Stop SCOS must hand the check the mask the processor actually used.

    _toggle_scos clears _scos_mask immediately after capturing it for this
    call. Move that clear one line earlier and the check silently starts
    measuring over the unshrunk ROI — or over None, which skips it entirely —
    and nothing else in the app would complain.
    """
    called = []
    monkeypatch.setattr(window, "_laser_off_check", lambda m: called.append(m))
    monkeypatch.setattr(window, "_finish_session", lambda: None)

    _press_stop(window)

    assert called, "the check was not run at all"
    mask = called[0]
    assert mask is not None, "the check was handed no ROI, so it would skip"
    assert mask.shape == FRAME_SHAPE


def test_stop_saves_even_when_the_check_raises(window, dialogs, monkeypatch):
    """The session's data outranks the check: a broken check still saves."""
    def _boom(*a, **k):
        raise RuntimeError("camera exploded")
    monkeypatch.setattr(window, "_laser_off_check", _boom)

    finished = []
    monkeypatch.setattr(window, "_finish_session", lambda: finished.append(True))

    _press_stop(window)

    assert finished == [True], "the session was finalized despite the broken check"
    assert dialogs["probe"] == [safety_dialog.PROBE_SKIPPED], (
        "a check that never finished says so: check the red light yourself")


# --- After the save: when the probe may come off (U2) -----------------------

def _stop_with_outcome(window, monkeypatch, outcome, events=None):
    """Press Stop SCOS with a check that ends in `outcome`."""
    def _check(mask):
        window._laser_off_outcome = outcome
        return None
    monkeypatch.setattr(window, "_laser_off_check", _check)
    if events is not None:
        monkeypatch.setattr(window, "_finish_session", lambda: events.append("finish"))
        monkeypatch.setattr(window, "_stop_recorder", lambda: events.append("recorder"))
    _press_stop(window)


@pytest.mark.parametrize("outcome", [
    safety_dialog.PROBE_PASSED, safety_dialog.PROBE_SKIPPED, safety_dialog.PROBE_FAILED,
])
def test_stop_shows_the_probe_window_for_the_outcome(window, dialogs, monkeypatch,
                                                     outcome):
    """Green, amber or red: the window matches how the check ended."""
    monkeypatch.setattr(window, "_finish_session", lambda: None)
    _stop_with_outcome(window, monkeypatch, outcome)
    assert dialogs["probe"] == [outcome]


def test_probe_window_comes_after_the_data_is_saved(window, dialogs, monkeypatch):
    """The window waits for a click; a recording must never wait for one."""
    events = []
    monkeypatch.setattr(safety_dialog, "show_probe_removal",
                        lambda parent, outcome: events.append("probe"))
    _stop_with_outcome(window, monkeypatch, safety_dialog.PROBE_PASSED, events)
    assert events.index("probe") > events.index("finish")
    assert events.index("probe") > events.index("recorder")
    assert window._state is State.PREVIEW


def test_a_previous_runs_outcome_does_not_carry_over(window, dialogs, monkeypatch):
    """Run 1 passed; run 2's check breaks — run 2 must not get the green window."""
    window._laser_off_outcome = safety_dialog.PROBE_PASSED

    def _boom(*a, **k):
        raise RuntimeError("camera exploded")
    monkeypatch.setattr(window, "_laser_off_check", _boom)
    monkeypatch.setattr(window, "_finish_session", lambda: None)
    _press_stop(window)
    assert dialogs["probe"] == [safety_dialog.PROBE_SKIPPED]


def test_cancelled_calibration_shows_no_probe_window(window, dialogs):
    """Stop during a calibration is not the end of a measurement."""
    window._set_state(State.DARK_CAL)
    window.btn_start_scos.blockSignals(True)
    window.btn_start_scos.setChecked(True)
    window.btn_start_scos.blockSignals(False)
    window._toggle_scos(False)
    assert dialogs["probe"] == []


def test_a_broken_probe_window_does_not_crash_stop(window, dialogs, monkeypatch):
    """The data is already saved; an exception here would abort the app."""
    def _boom(*a, **k):
        raise RuntimeError("no display")
    monkeypatch.setattr(safety_dialog, "show_probe_removal", _boom)
    monkeypatch.setattr(window, "_finish_session", lambda: None)
    _stop_with_outcome(window, monkeypatch, safety_dialog.PROBE_PASSED)
    assert window._state is State.PREVIEW


def test_playback_returns_to_the_main_recording(window, dialogs):
    """--mock-folder must not be left parked on the dark folder.

    The check plays the dark folder because playback has no laser to switch
    off. If it never switches back, Stop SCOS leaves the operator setting up
    the next run against a black live image, and the rehearsal looks broken
    until the following Start SCOS puts it right by accident.
    """
    sources = []
    window.camera.set_playback_source = lambda s: sources.append(s) or True

    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL)

    assert sources, "playback source was never switched"
    assert sources[0] == "dark", "the check samples the dark folder"
    assert sources[-1] == "main", "and puts playback back where it found it"


def test_history_is_cleared_between_runs(window):
    """A second run must not compare against the first run's intensity."""
    window._intensity_history.append((0.0, 999.0))

    # _finish_bright_cal is where a run's measurement state is initialised.
    collector = BrightCalCollector(2, window.spn_window.value())
    for _ in range(2):
        collector.add_frame(np.full(FRAME_SHAPE, DARK_LEVEL + 300, dtype=np.float64))
    window._bright_cal_collector = collector
    window._session_folder = None
    window._finish_bright_cal()

    assert window._intensity_history == [], "a stale reference would misjudge run two"


# --- The laser may be on, but there is no measurement (U2, 2026-10-09) ------
# Stopping during the bright calibration (laser on, subject in place) and
# closing the window mid-measurement used to show nothing at all.

def test_stop_during_bright_calibration_warns(window, dialogs):
    window._set_state(State.BRIGHT_CAL)
    window.btn_start_scos.blockSignals(True)
    window.btn_start_scos.setChecked(True)
    window.btn_start_scos.blockSignals(False)
    window._toggle_scos(False)
    assert dialogs["probe"] == [safety_dialog.PROBE_CANCELLED]
    assert window._state is State.PREVIEW


def test_cancel_at_the_bright_prompt_warns(window, dialogs):
    """The operator may have switched the laser on before clicking Cancel."""
    window._set_state(State.PREVIEW)
    dialogs["question_replies"][:] = [QMessageBox.StandardButton.Cancel]
    window._start_bright_cal()
    assert dialogs["probe"] == [safety_dialog.PROBE_CANCELLED]
    assert window._state is State.PREVIEW


def test_bright_calibration_error_warns_after_the_error(window, dialogs, monkeypatch):
    """The error explains what went wrong; the safety window comes last."""
    class _Broken:
        n_collected = 3
        def result(self, **k):
            raise RuntimeError("bad frames")
    window._set_state(State.BRIGHT_CAL)
    window._bright_cal_collector = _Broken()
    errors_before_probe = []
    monkeypatch.setattr(
        safety_dialog, "show_probe_removal",
        lambda parent, outcome: errors_before_probe.append(
            (outcome, len(dialogs["critical"]))))
    window._finish_bright_cal()
    assert errors_before_probe == [(safety_dialog.PROBE_CANCELLED, 1)]


def test_stop_during_dark_calibration_does_not_warn(window, dialogs):
    """Dark calibration runs with the laser off."""
    window._set_state(State.DARK_CAL)
    window.btn_start_scos.blockSignals(True)
    window.btn_start_scos.setChecked(True)
    window.btn_start_scos.blockSignals(False)
    window._toggle_scos(False)
    assert dialogs["probe"] == []


def test_closing_mid_measurement_stops_it_properly(window, dialogs, monkeypatch):
    """Close = Stop SCOS: laser check, results finalized, probe window."""
    finished = []
    monkeypatch.setattr(window, "_finish_session", lambda: finished.append(True))

    def _check(mask):
        window._laser_off_outcome = safety_dialog.PROBE_PASSED
        return None
    monkeypatch.setattr(window, "_laser_off_check", _check)

    window.close()

    assert finished == [True], "rBFi and the figure are written on close"
    assert dialogs["probe"] == [safety_dialog.PROBE_PASSED]


def test_closing_during_bright_calibration_warns_after_the_camera_stops(
        window, dialogs, monkeypatch):
    """Not earlier: frames arriving while the window is open could finish the
    calibration and start a measurement."""
    events = []
    monkeypatch.setattr(window.camera, "stop", lambda *a, **k: events.append("camera stop"))
    monkeypatch.setattr(safety_dialog, "show_probe_removal",
                        lambda parent, outcome: events.append(outcome))
    window._set_state(State.BRIGHT_CAL)
    window.close()
    assert events == ["camera stop", safety_dialog.PROBE_CANCELLED]
    window._set_state(State.PREVIEW)   # the fixture's teardown closes it again


def test_closing_during_dark_calibration_does_not_warn(window, dialogs):
    window._set_state(State.DARK_CAL)
    window.close()
    assert dialogs["probe"] == []
    window._set_state(State.PREVIEW)
