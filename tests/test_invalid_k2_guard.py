"""
Tests for the all-negative κ² guard — docs/todo.md B-tier operator safety.

Found on 2026-09-26 during a mock-folder rehearsal. Every one of 704 results
came back with a corrected κ² of about −0.0029, so `bfi_raw = 1/κ²` was never
computed, `_bfi_norm_buffer` stayed empty, and the session never advanced out
of MEASURING_INIT. The app showed an empty plot and a frozen
"Normalizing — 22.3 / 5 s" label for 22 seconds and said nothing. Stopping it
produced a schema-valid `rBfi_results.h5` with an all-NaN `bfi` and no `rBFi`
at all, and the only trace of the failure was one WARNING line in `app.log`.

That run's cause was specific to `--mock-folder` playback, where the "dark"
calibration frames are really the laser-on recording. The *silence* is not:
a dark calibration contaminated by room light, a laser that had not finished
switching off, or an operator who clicked OK a beat early all produce the same
picture on real hardware. These tests pin the behaviour that replaces it —
stop the run, say why, and say where the raw data went.

The tests drive `_on_scos_result` directly rather than going through the
camera: the guard is a property of that handler, and a fake camera that
actually emits frames would make the timing non-deterministic.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The QApplication must exist before gui.main_window is imported: importing it
# pulls in pyqtgraph, and building it afterwards kills the interpreter outright
# (no traceback, no pytest output, exit code 127). tests/test_gain_table.py does
# the same for the same reason.
_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


# Enough results to clear _INVALID_K2_MIN_SAMPLES several times over.
N_RESULTS = 40
# Timestamps spanning well past _norm_seconds + _INVALID_K2_GRACE_S (5 + 2 s).
T_END = 10.0


class _FakeCamera(QObject):
    """Camera stub: accepts every call MainWindow makes, emits nothing."""

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
    """Record QMessageBox calls instead of opening windows that nothing can click."""
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)),
    )
    seen = {"critical": [], "warning": []}

    def _grab(kind):
        def _fn(parent, title, text, *a, **k):
            seen[kind].append((title, text))
            return QMessageBox.StandardButton.Ok
        return _fn

    monkeypatch.setattr(QMessageBox, "critical", _grab("critical"))
    monkeypatch.setattr(QMessageBox, "warning",  _grab("warning"))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )
    return seen


@pytest.fixture
def window(tmp_path):
    """A MainWindow parked in MEASURING_INIT with a recorder open.

    This is the state _finish_bright_cal() leaves behind: calibration done,
    normalization buffer empty, waiting for the first usable results.
    """
    w = MainWindow(camera=_FakeCamera())
    w._session_folder = tmp_path
    w._roi_circ = {"cx": 608.0, "cy": 400.0, "r": 150.0}
    w.processor.bit_depth = 12
    w.processor.gain_db   = 8.0
    assert w._prepare_gain(), "Mono12 @ 8 dB for this SN is an exact table row"
    w._start_recorder()
    w._bfi_norm        = None
    w._bfi_norm_buffer = []
    w._norm_seconds    = 5.0
    # Start SCOS leaves the button checked, and the abort stops the run by
    # unchecking it. Without this the toggle signal never fires and the test
    # would be exercising a state the app is never actually in.
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.blockSignals(False)
    w._set_state(State.MEASURING_INIT)
    yield w
    w.close()


def _feed(w, k2_corr_values):
    """Push results through the real handler, timestamped evenly to T_END."""
    n = len(k2_corr_values)
    for i, k2_corr in enumerate(k2_corr_values):
        t = T_END * i / max(n - 1, 1)
        w._on_scos_result(t, 0.0001, k2_corr, 500.0, 1.0)


class TestAbortsOnAllNegative:
    """The failure this guard exists for."""

    def test_run_is_stopped_and_the_operator_is_told(self, window, dialogs):
        _feed(window, [-0.0029] * N_RESULTS)

        assert len(dialogs["critical"]) == 1, (
            "exactly one error dialog — the operator must be told, and told once"
        )
        title, text = dialogs["critical"][0]
        assert "κ²" in title
        # The message has to name the likely cause, or it only says that
        # something is wrong and leaves the operator with no next move.
        assert "dark" in text.lower() and "laser" in text.lower()
        assert "Start SCOS" in text, "the message must say how to retry"

        assert not window.btn_start_scos.isChecked(), "the run must be stopped"
        assert window._state is State.PREVIEW, (
            "a stopped run ends in PREVIEW, the same as pressing Stop SCOS"
        )

    def test_the_raw_data_is_kept_and_its_location_named(self, window, dialogs,
                                                         tmp_path):
        _feed(window, [-0.0029] * N_RESULTS)

        _, text = dialogs["critical"][0]
        assert str(tmp_path) in text, (
            "an aborted run still wrote raw data; the message must say where"
        )
        assert (tmp_path / "rBfi_results.h5").exists()

    def test_further_results_raise_no_second_dialog(self, window, dialogs):
        _feed(window, [-0.0029] * N_RESULTS)
        # Results already queued behind the aborting one keep arriving; each
        # opening its own modal box is exactly the pile-up that made the laser
        # prompt unusable in tests/test_gain_table.py.
        _feed(window, [-0.0029] * N_RESULTS)
        assert len(dialogs["critical"]) == 1


class TestDoesNotFireWhenItShouldNot:
    """A guard that aborts a good run is worse than no guard."""

    def test_silent_before_the_window_has_elapsed(self, window, dialogs):
        # Same bad data, but all inside the first 5 s + 2 s grace.
        for i in range(N_RESULTS):
            window._on_scos_result(6.9 * i / (N_RESULTS - 1), 0.0001,
                                   -0.0029, 500.0, 1.0)
        assert dialogs["critical"] == []
        assert window._state is State.MEASURING_INIT

    def test_silent_when_too_few_results_have_arrived(self, window, dialogs):
        # Past the time threshold, but only a handful of samples: a stalled
        # pipeline must not be mistaken for a bad calibration.
        n = window._INVALID_K2_MIN_SAMPLES - 1
        for i in range(n):
            window._on_scos_result(T_END * i / max(n - 1, 1), 0.0001,
                                   -0.0029, 500.0, 1.0)
        assert dialogs["critical"] == []
        assert window._state is State.MEASURING_INIT

    def test_silent_when_some_frames_are_usable(self, window, dialogs):
        # One good frame early is enough to normalize on, so the run is
        # viable and must be left alone however noisy the rest is.
        values = [-0.0029] * N_RESULTS
        values[2] = 0.08
        _feed(window, values)
        assert dialogs["critical"] == []

    def test_a_healthy_run_still_normalizes(self, window, dialogs):
        _feed(window, [0.08] * N_RESULTS)
        assert dialogs["critical"] == []
        assert window._state is State.MEASURING, "normalization must have finished"
        assert window._bfi_norm == pytest.approx(1.0 / 0.08)


class _StubBrightCollector:
    """Just enough of BrightCalCollector for _finish_bright_cal to run."""

    n_collected = 60
    window_size = 7

    def result(self, dark_mean=None):
        sp_im = np.zeros((4, 4), dtype=np.float64)
        return sp_im, np.ones((4, 4), dtype=np.float64)


class TestCounterIsPerRun:
    """A second run in the same window must be able to abort too.

    `_invalid_k2_reported` exists to stop one bad run raising a dialog per
    result. If it were not cleared when the next measurement starts, the
    operator would be warned about the first contaminated calibration and then
    never again for the rest of the session.

    This drives the real reset in `_finish_bright_cal()` rather than
    reassigning the fields by hand, so deleting those two lines from the
    production code fails the test.
    """

    def test_a_second_run_aborts_as_well(self, window, dialogs, tmp_path):
        _feed(window, [-0.0029] * N_RESULTS)
        assert len(dialogs["critical"]) == 1
        assert window._invalid_k2_reported

        # Start a second measurement the way the app does: dark and bright
        # calibration finish, and _finish_bright_cal() enters MEASURING_INIT.
        window._session_folder        = tmp_path          # _start_dark_cal makes a fresh one
        window._bright_cal_collector  = _StubBrightCollector()
        window.btn_start_scos.blockSignals(True)
        window.btn_start_scos.setChecked(True)
        window.btn_start_scos.blockSignals(False)
        window._finish_bright_cal()

        assert window._state is State.MEASURING_INIT
        assert window._n_invalid_k2 == 0
        assert not window._invalid_k2_reported

        # The second run's results start from t = 0 again, so the grace period
        # is measured from the start of *this* run, not the session.
        _feed(window, [-0.0029] * N_RESULTS)
        assert len(dialogs["critical"]) == 2, (
            "a second contaminated run must warn the operator again"
        )
