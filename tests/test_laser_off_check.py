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
    path opens three: the "turn off the laser" prompt (information), the
    failure warning (question), and the output-folder picker.

    `question_replies` is a queue of answers — one is popped per call while
    more than one remains, which is what lets a test drive the
    No-means-check-again loop.
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
        window._laser_off_check(window._scos_mask)
    finally:
        feeder.stop()


def test_laser_off_passes_when_intensity_drops(window, dialogs):
    """Laser genuinely off: prompt shown, check passes, no warning."""
    # The run saw 500 DU above dark; the laser-off frame reads the black level
    # and nothing more, so the drop is total.
    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL)

    assert len(dialogs["information"]) == 1, "the operator was told to switch the laser off"
    assert "turn off the laser" in dialogs["information"][0][1].lower()
    assert dialogs["question"] == [], "a passing check must not warn"


def test_laser_still_on_warns_with_the_measured_numbers(window, dialogs):
    """Laser left on: the warning fires and quotes measured and expected."""
    # 500 DU above dark during the run, 400 above dark afterwards — a 20 %
    # fall, nowhere near the 90 % required.
    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL + 400)

    assert len(dialogs["question"]) == 1, "the operator was warned"
    title, text = dialogs["question"][0]
    assert "Laser May Still Be On" in title
    assert "did not drop by 90" in text
    assert "400.0 DU" in text, "the measured value is quoted"
    assert "50.0 DU"  in text, "the expected ceiling (10 % of 500) is quoted"


def test_no_answer_re_runs_the_check(window, dialogs):
    """No on 'Continue anyway?' means check again, not discard the data."""
    dialogs["question_replies"][:] = [
        QMessageBox.StandardButton.No,     # first warning: let me try again
        QMessageBox.StandardButton.Yes,    # second warning: give up and save
    ]
    _run_check(window, ref_intensity=500.0, off_level=DARK_LEVEL + 400)

    assert len(dialogs["question"]) == 2, "No ran the check a second time"
    assert len(dialogs["information"]) == 2, "and re-prompted for the laser"


def test_no_reference_intensity_skips_the_check(window, dialogs):
    """A run stopped before any result: prompt still shown, nothing crashes."""
    window._intensity_history = []
    window._laser_off_check(window._scos_mask)

    assert len(dialogs["information"]) == 1, "the operator is still told to kill the laser"
    assert dialogs["question"] == [], "nothing to compare against, so nothing to warn about"


def test_no_frame_arriving_does_not_hang(window, dialogs, monkeypatch):
    """A camera that has already stopped must time out, not strand the operator."""
    monkeypatch.setattr(type(window), "_LASER_OFF_TIMEOUT_MS", 50)
    for i in range(10):
        window._intensity_history.append((float(i), 500.0))

    window._laser_off_check(window._scos_mask)   # no frame is ever emitted

    assert len(dialogs["information"]) == 1
    assert dialogs["question"] == [], "a timeout is not a failed check"


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
