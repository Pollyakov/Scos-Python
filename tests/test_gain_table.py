"""
Tests for the supervisor's G[DU/e] rule (2026-09-22).

Vika's instruction: G always comes from the measured table
(CamerasMeasuredGain.csv), never from the convert_gain() formula. The two
outcomes she specified are different in kind:

  * **CameraSN + nBits missing from the table → error, refuse to run.**
    A formula estimate would silently bias the shot-noise term (G·mean) in
    every corrected κ² the session produces, and nothing downstream would
    reveal it. The operator sees the exact message from her spec.

  * **CameraSN + nBits present but that gain_dB is not → warning, continue.**
    The table entry is rescaled in dB, which is a reasonable approximation.

The check runs once at Start SCOS rather than inside process(): a failure there
would fire error_occurred on every frame, and the run would already have created
a session folder and started calibration before the operator saw anything.

The synthetic --mock-tiff source is the one exception (agreed with the user,
2026-09-22): it has no camera, so it can never be in the table. It keeps the
formula and says so in a dialog.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from gui.main_window import MainWindow
from processor import GainTableError, SCOSProcessor, convert_gain, load_gain_from_table


# Rows that exist in CamerasMeasuredGain.csv for the lab's a2A1920-160umPRO.
LAB_SN = "40513592"


class _FakeCamera(QObject):
    """Frame source stand-in whose reported serial the test controls."""

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

    def __init__(self, serial="", is_synthetic=False):
        super().__init__()
        self._serial      = serial
        self.is_synthetic = is_synthetic

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass

    def get_info(self):
        return {"serial": self._serial, "model": "a2A1920-160umPRO"}


@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    """Capture QMessageBox calls instead of opening modal windows.

    A real dialog would hang the suite, since nothing is there to click it.
    The output-folder QFileDialog that Start SCOS opens is answered with a
    temp directory for the same reason — these tests are the first ones that
    press Start SCOS for real rather than faking the state.
    """
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
    # The two calibration prompts ("turn the laser off / on") are modal too —
    # answer them with OK so a started run walks into DARK_CAL and stops there,
    # waiting for frames the fake camera never sends.
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )
    return seen


def _window(serial="", synthetic=False, fmt="Mono12", gain_db=8.0):
    w = MainWindow(camera=_FakeCamera(serial, synthetic))
    w.cmb_format.setCurrentText(fmt)
    w.spn_gain.setValue(gain_db)
    return w


# ----------------------------------------------------------------------
# The processor half: resolve_gain() picks the table and caches correctly
# ----------------------------------------------------------------------

class TestResolveGain:

    def test_table_is_used_when_serial_is_known(self):
        proc = SCOSProcessor(gain_db=8.0, bit_depth=12, camera_sn=LAB_SN)
        assert proc.resolve_gain() == pytest.approx(
            load_gain_from_table(LAB_SN, 12, 8.0))
        assert proc.gain_source == "table"

    def test_formula_is_used_only_without_a_serial(self):
        proc = SCOSProcessor(gain_db=8.0, bit_depth=12,
                             test_mode_sat_capacity=10500.0)
        assert proc.resolve_gain() == pytest.approx(convert_gain(8.0, 12, 10500.0))
        assert proc.gain_source == "formula"

    def test_missing_row_raises_with_vikas_message(self):
        proc = SCOSProcessor(gain_db=8.0, bit_depth=8, camera_sn=LAB_SN)
        with pytest.raises(GainTableError) as exc:
            proc.resolve_gain()
        assert str(exc.value) == (
            f"Can't calculate SCOS: CameraSN {LAB_SN} Mono8 "
            f"was not found in G[DU/e] Calibration file"
        )

    def test_inexact_gain_warns_but_returns_a_value(self):
        # Mono10 rows exist at 16/18/20 dB only — the lab records at 24 dB.
        proc = SCOSProcessor(gain_db=24.0, bit_depth=10, camera_sn=LAB_SN)
        with pytest.warns(UserWarning, match="not in table"):
            g = proc.resolve_gain()
        assert g == pytest.approx(1.4596658, rel=1e-6)

    def test_invalidate_gain_picks_up_a_changed_gain_db(self):
        """The staleness bug: G was cached once and survived a parameter change.

        Without invalidate_gain() a second run at a different gain would keep
        using the first run's G for every frame.
        """
        proc = SCOSProcessor(gain_db=16.0, bit_depth=10, camera_sn=LAB_SN)
        first = proc.resolve_gain()

        proc.gain_db = 20.0
        assert proc.resolve_gain() == first        # still stale, by design

        proc.invalidate_gain()
        assert proc.resolve_gain() != first
        assert proc.resolve_gain() == pytest.approx(
            load_gain_from_table(LAB_SN, 10, 20.0))


# ----------------------------------------------------------------------
# The GUI half: what the operator sees, and whether the run starts
# ----------------------------------------------------------------------

class TestStartScosGainGate:

    def test_camera_not_in_table_refuses_to_start(self, dialogs):
        """Mono8 is absent for this camera → hard stop before any state changes."""
        w = _window(serial=LAB_SN, fmt="Mono8")
        try:
            w.btn_start_scos.setChecked(True)

            assert len(dialogs["critical"]) == 1
            title, text = dialogs["critical"][0]
            assert "Can't calculate SCOS" in title
            assert (f"Can't calculate SCOS: CameraSN {LAB_SN} Mono8 "
                    f"was not found in G[DU/e] Calibration file") in text

            # Refused: the button is back up and the parameters are editable.
            assert not w.btn_start_scos.isChecked()
            assert w.btn_start_scos.text() == "Start SCOS"
            assert w.spn_gain.isEnabled()
            assert w._session_folder is None
        finally:
            w.close()

    def test_unknown_serial_refuses_to_start(self, dialogs):
        """A camera that reports no serial cannot be looked up → same refusal."""
        w = _window(serial="", fmt="Mono12")
        try:
            w.btn_start_scos.setChecked(True)
            assert len(dialogs["critical"]) == 1
            assert "CameraSN <unknown> Mono12" in dialogs["critical"][0][1]
            assert not w.btn_start_scos.isChecked()
        finally:
            w.close()

    def test_exact_table_row_starts_silently(self, dialogs):
        """Mono12 @ 8 dB is an exact row — no dialog, and the run proceeds."""
        w = _window(serial=LAB_SN, fmt="Mono12", gain_db=8.0)
        try:
            w.btn_start_scos.setChecked(True)
            assert dialogs["critical"] == []
            assert dialogs["warning"] == []
            assert w.btn_start_scos.isChecked()
            assert w.processor.gain_source == "table"
            assert w.processor.gain_du_per_e == pytest.approx(
                load_gain_from_table(LAB_SN, 12, 8.0))
        finally:
            w.btn_start_scos.setChecked(False)
            w.close()

    def test_inexact_gain_warns_and_continues(self, dialogs):
        """Mono10 @ 24 dB: rescaled from the 20 dB row, operator told, run starts."""
        w = _window(serial=LAB_SN, fmt="Mono10", gain_db=24.0)
        try:
            w.btn_start_scos.setChecked(True)
            assert dialogs["critical"] == []
            assert len(dialogs["warning"]) == 1
            assert "not at 24 dB" in dialogs["warning"][0][1]
            assert w.btn_start_scos.isChecked()          # continues
            assert w.processor.gain_source == "table"
        finally:
            w.btn_start_scos.setChecked(False)
            w.close()

    def test_synthetic_source_falls_back_to_the_formula(self, dialogs):
        """--mock-tiff has no camera: formula, a dialog saying so, run continues."""
        w = _window(serial="", synthetic=True, fmt="Mono12", gain_db=8.0)
        try:
            w.btn_start_scos.setChecked(True)
            assert dialogs["critical"] == []
            assert len(dialogs["warning"]) == 1
            assert "do not use them as measurements" in dialogs["warning"][0][1]
            assert w.btn_start_scos.isChecked()
            assert w.processor.gain_source == "formula"
        finally:
            w.btn_start_scos.setChecked(False)
            w.close()

    def test_gain_is_re_resolved_on_every_start(self, dialogs):
        """Second run at a different gain must not reuse the first run's G."""
        w = _window(serial=LAB_SN, fmt="Mono10", gain_db=16.0)
        try:
            w.btn_start_scos.setChecked(True)
            first = w.processor.gain_du_per_e
            w.btn_start_scos.setChecked(False)

            w.spn_gain.setValue(20.0)
            w.btn_start_scos.setChecked(True)
            assert w.processor.gain_du_per_e == pytest.approx(
                load_gain_from_table(LAB_SN, 10, 20.0))
            assert w.processor.gain_du_per_e != first
        finally:
            w.btn_start_scos.setChecked(False)
            w.close()
