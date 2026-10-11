"""
Tests for the results-file schema — merged_worklist task 9.

The file this produces is the one Vika opens in MATLAB, so these tests check
what MATLAB will actually do with it, not merely that keys exist:

  * `startTime` must come back as a char row that `datetime()` parses. Stored
    as h5py's default variable-length UTF-8 it arrives as a cell array instead,
    and her `datetime(startTime)` line fails — so the dtype is asserted.
  * `Params` must hold exactly the ten fields she listed on 2026-09-23, and in
    particular must NOT contain `satCapacity`.
  * `rBFi` must be the same length as `timeVec`, with NaN kept wherever κ² ≤ 0
    made BFi undefined (answer 6: plain NaN, no separate mask).

Calibration lives in its own file holding both kinds (answer 4), which
supersedes the separate DarkCalibration.h5 / BrightCalibration.h5 in
`docs/session_tab`.
"""

import datetime
import math
import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QObject, pyqtSignal

from core.crop import Crop, circle_mask
from core.recorder import (CALIBRATION_FILENAME, RESULTS_FILENAME, HDF5Recorder,
                           write_calibration)


PARAMS_AT_START = {
    "frameRate":    20.0,
    "exposureTime": 5.0,
    "gain":         24.0,
    "windowSize":   7,
    "ROI":          [608.0, 400.0, 150.0],
    "bitDepth":     10,
}

# What Params must contain once the session has been finalized: the six fields
# known up front, the three the normalization supplies at close, and the hash.
EXPECTED_PARAM_FIELDS = set(PARAMS_AT_START) | {
    "normalizationConstant", "normalizationMethod", "normalizationWindowSec",
    "gitCommit",
}


def _recorder(tmp_path, **meta):
    return HDF5Recorder(tmp_path / RESULTS_FILENAME,
                        {"camera_sn": "40513592", **meta},
                        dict(PARAMS_AT_START))


def _finished_session(tmp_path, n=10, bad_index=None):
    """Write a small session and finalize it, as _finish_session() does."""
    rec = _recorder(tmp_path)
    k2_cors = []
    for i in range(n):
        k2 = -0.01 if i == bad_index else 0.08 + 0.001 * i
        k2_cors.append(k2)
        rec.append(0.05 * i, 0.1, k2, 500.0 + i)
    rec.write_rbfi(norm_constant=12.5, method="mean", window_seconds=5.0)
    rec.close()
    return tmp_path / RESULTS_FILENAME, k2_cors


class _FakeCamera(QObject):
    """Just enough of a camera for MainWindow; no applied_crop, like a
    source that cannot crop."""
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
    frames_lost   = 0          # camera.py's BlockID count, since Start Video
    time_source   = "camera"   # camera clock accepted by FrameClock

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



def _window():
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from gui.main_window import MainWindow
    return MainWindow(camera=_FakeCamera())


# ----------------------------------------------------------------------
# File name
# ----------------------------------------------------------------------

class TestFileName:

    def test_is_the_protocol_name(self):
        # Protocol step 6: "Save into .h5 file 'rBFi_resultsAndParameters'"
        # (todo U9). Spelled out here because every other test reaches the
        # file through the constant, so they would pass with any name.
        assert RESULTS_FILENAME == "rBFi_resultsAndParameters.h5"


# ----------------------------------------------------------------------
# startTime
# ----------------------------------------------------------------------

class TestStartTime:

    def test_parses_as_a_matlab_datetime(self, tmp_path):
        rec = _recorder(tmp_path)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            raw = f["startTime"][()]
        text = raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
        # The format MATLAB's datetime() reads back: 23-Sep-2026 15:41:47
        parsed = datetime.datetime.strptime(text, "%d-%b-%Y %H:%M:%S")
        assert abs((datetime.datetime.now() - parsed).total_seconds()) < 300

    def test_is_fixed_length_ascii_not_a_cell_array(self, tmp_path):
        """Variable-length UTF-8 reaches MATLAB as a cell array, not a char row."""
        rec = _recorder(tmp_path)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            dt = f["startTime"].dtype
            assert h5py.check_string_dtype(dt) is not None
            assert h5py.check_string_dtype(dt).length is not None, (
                "startTime is variable-length; MATLAB will return a cell array"
            )


# ----------------------------------------------------------------------
# Params
# ----------------------------------------------------------------------

class TestParams:

    def test_holds_exactly_the_agreed_fields(self, tmp_path):
        path, _ = _finished_session(tmp_path)
        with h5py.File(path, "r") as f:
            assert set(f["Params"].attrs) == EXPECTED_PARAM_FIELDS

    def test_has_no_sat_capacity(self, tmp_path):
        """Explicit instruction, 2026-09-23: it must not appear in the results."""
        path, _ = _finished_session(tmp_path)
        with h5py.File(path, "r") as f:
            keys = {k.lower() for k in f["Params"].attrs}
            keys |= {k.lower() for k in f["metadata"].attrs}
            assert not any("sat" in k for k in keys), sorted(keys)

    def test_values_round_trip(self, tmp_path):
        path, _ = _finished_session(tmp_path)
        with h5py.File(path, "r") as f:
            p = f["Params"].attrs
            assert p["frameRate"]    == pytest.approx(20.0)
            assert p["exposureTime"] == pytest.approx(5.0)
            assert p["gain"]         == pytest.approx(24.0)
            assert p["windowSize"]   == 7
            assert p["bitDepth"]     == 10
            np.testing.assert_allclose(p["ROI"], [608.0, 400.0, 150.0])

    def test_normalization_provenance_written_at_close(self, tmp_path):
        path, _ = _finished_session(tmp_path)
        with h5py.File(path, "r") as f:
            p = f["Params"].attrs
            assert p["normalizationConstant"]  == pytest.approx(12.5)
            assert p["normalizationMethod"]    == "mean"
            assert p["normalizationWindowSec"] == pytest.approx(5.0)

    def test_git_commit_is_recorded(self, tmp_path):
        rec = _recorder(tmp_path)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            commit = f["Params"].attrs["gitCommit"]
        assert commit
        # Either a real short hash (optionally marked dirty) or the honest
        # fallback — never a silent empty string.
        assert commit == "unknown" or commit.rstrip("-dirty").isalnum()


# ----------------------------------------------------------------------
# rBFi
# ----------------------------------------------------------------------

class TestRbfi:

    def test_same_length_as_timevec(self, tmp_path):
        path, _ = _finished_session(tmp_path, n=25)
        with h5py.File(path, "r") as f:
            assert f["rBFi"].shape == f["timeVec"].shape

    def test_is_raw_bfi_divided_by_the_constant(self, tmp_path):
        path, _ = _finished_session(tmp_path)
        with h5py.File(path, "r") as f:
            np.testing.assert_allclose(f["rBFi"][:], f["bfi"][:] / 12.5)

    def test_nan_survives_where_kappa_was_invalid(self, tmp_path):
        """κ² ≤ 0 → NaN in bfi → NaN in rBFi, with timeVec still evenly spaced."""
        path, _ = _finished_session(tmp_path, n=10, bad_index=4)
        with h5py.File(path, "r") as f:
            rbfi  = f["rBFi"][:]
            times = f["timeVec"][:]
        assert math.isnan(rbfi[4])
        assert np.isfinite(np.delete(rbfi, 4)).all()
        np.testing.assert_allclose(np.diff(times), 0.05, atol=1e-9)

    def test_absent_when_the_session_was_never_normalized(self, tmp_path):
        """A crash, or closing the window mid-run, leaves raw BFi and no rBFi.

        The supervisor accepted this trade-off (answer 5). What matters is that
        the file is still valid HDF5 with the raw data intact.
        """
        rec = _recorder(tmp_path)
        for i in range(5):
            rec.append(0.05 * i, 0.1, 0.08, 500.0)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            assert "rBFi" not in f
            assert f["bfi"].shape == (5,)
            assert "startTime" in f

    def test_refuses_a_useless_constant(self, tmp_path):
        """Zero or NaN would turn every point into inf/NaN — fail loudly instead."""
        rec = _recorder(tmp_path)
        rec.append(0.0, 0.1, 0.08, 500.0)
        for bad in (0.0, float("nan")):
            with pytest.raises(ValueError, match="normalization constant"):
                rec.write_rbfi(bad, "mean", 5.0)
        rec.close()


# ----------------------------------------------------------------------
# Calibration file
# ----------------------------------------------------------------------

class TestCalibrationFile:

    def test_both_kinds_land_in_one_file(self, tmp_path):
        """Answer 4: one separate file with dark and bright, written minutes apart."""
        path = tmp_path / CALIBRATION_FILENAME
        write_calibration(path, "dark",
                          {"mean_dark": np.full((4, 4), 42.0),
                           "var_dark":  np.full((4, 4), 0.5),
                           "mask":      np.ones((4, 4), dtype=bool)},
                          {"n_frames": 600, "window_size": 7})
        write_calibration(path, "bright",
                          {"spIm":  np.full((4, 4), 100.0),
                           "spVar": np.full((4, 4), 1.2)},
                          {"n_frames": 300, "window_size": 7})

        with h5py.File(path, "r") as f:
            assert set(f) == {"dark", "bright"}
            np.testing.assert_allclose(f["dark/mean_dark"][:], 42.0)
            np.testing.assert_allclose(f["dark/var_dark"][:],  0.5)
            np.testing.assert_allclose(f["dark/mask"][:],      1.0)
            np.testing.assert_allclose(f["bright/spIm"][:],  100.0)
            np.testing.assert_allclose(f["bright/spVar"][:],   1.2)
            assert f["dark"].attrs["n_frames"]   == 600
            assert f["bright"].attrs["n_frames"] == 300

    def test_none_arrays_are_skipped(self, tmp_path):
        path = tmp_path / CALIBRATION_FILENAME
        write_calibration(path, "dark", {"mean_dark": None, "var_dark": None})
        with h5py.File(path, "r") as f:
            assert list(f["dark"]) == []

    def test_recalibrating_replaces_rather_than_merges(self, tmp_path):
        """A second dark cal must not leave arrays from the first behind."""
        path = tmp_path / CALIBRATION_FILENAME
        write_calibration(path, "dark", {"mean_dark": np.zeros((4, 4)),
                                         "mask":      np.ones((4, 4))})
        write_calibration(path, "dark", {"mean_dark": np.ones((4, 4))})
        with h5py.File(path, "r") as f:
            assert set(f["dark"]) == {"mean_dark"}
            np.testing.assert_allclose(f["dark/mean_dark"][:], 1.0)


# ----------------------------------------------------------------------
# The wiring: MainWindow actually produces such a file
# ----------------------------------------------------------------------

class TestGuiWiring:
    """Unit tests above prove the recorder can write the schema; this proves
    the GUI asks it to, with the values the operator set in the widgets."""

    def test_finish_session_writes_a_complete_results_file(self, tmp_path, monkeypatch):
        w = _window()
        try:
            w.spn_fps.setValue(20.0)
            w.spn_exposure.setValue(5.0)
            w.spn_gain.setValue(8.0)
            w.spn_window.setValue(7)
            w.cmb_format.setCurrentText("Mono12")
            w._roi_circ = {"cx": 608.0, "cy": 400.0, "r": 150.0}
            w._session_folder = tmp_path
            # What Start SCOS does first: resolve G and remember which camera
            # it was resolved for. Mono12 @ 8 dB for this SN is an exact row in
            # the gain table, so no dialog is raised (conftest would fail the
            # test if one were).
            w.processor.bit_depth = 12
            w.processor.gain_db   = 8.0
            assert w._prepare_gain()
            # 2 frames lost during preview/calibration, then 5 more while
            # measuring: only those 5 belong to this measurement.
            w.camera.frames_lost = 2
            w._camera_lost_at_start = w._camera_frames_lost()
            w._start_recorder()
            w.camera.frames_lost = 7

            for i in range(6):
                w._recorder.append(0.05 * i, 0.1, 0.08, 500.0 + i)
            # As _on_scos_result would have left it once normalization ended.
            w._bfi_norm        = 12.5
            w._bfi_norm_method = "mean"
            w._norm_seconds    = 5.0

            w._finish_session()
            w._stop_recorder()

            path = tmp_path / RESULTS_FILENAME
            assert path.exists(), "the file must carry the name session_tab asks for"
            with h5py.File(path, "r") as f:
                assert set(f["Params"].attrs) == EXPECTED_PARAM_FIELDS
                p = f["Params"].attrs
                assert p["gain"]       == pytest.approx(8.0)
                assert p["bitDepth"]   == 12
                assert p["windowSize"] == 7
                np.testing.assert_allclose(p["ROI"], [608.0, 400.0, 150.0])
                assert p["normalizationConstant"] == pytest.approx(12.5)
                # G provenance stays in metadata, out of Params.
                assert f["metadata"].attrs["camera_sn"] == "40513592"
                # Which clock timeVec came from, and what never reached it
                # (todo D5) — provenance, so metadata, not Params.
                assert f["metadata"].attrs["time_source"] == "camera"
                assert f["metadata"].attrs["frames_lost_camera"] == 5
                assert f["metadata"].attrs["frames_dropped_queue"] == 0
                np.testing.assert_allclose(f["rBFi"][:], f["bfi"][:] / 12.5)
                assert f["rBFi"].shape == f["timeVec"].shape
        finally:
            w.close()


# ----------------------------------------------------------------------
# The camera's image area (todo U10)
# ----------------------------------------------------------------------

# Offsets, width and height all different, so a swapped x/y or width/height
# cannot pass by coincidence.
CUT = Crop(offset_x=16, offset_y=10, width=24, height=18,
           sensor_width=64, sensor_height=48)
CUT_ATTRS = {"OffsetX": 16, "OffsetY": 10, "Width": 24, "Height": 18,
             "SensorWidth": 64, "SensorHeight": 48}


def _crop_of(group) -> dict:
    return {k: int(v) for k, v in group["crop"].attrs.items()}


class TestCropRecord:
    """Vika, 2026-10-08: record the crop that was used — width, height,
    OffsetX, OffsetY and the full sensor size — as a struct in `metadata`,
    and the same in Calibration.h5."""

    def test_results_file_has_it_in_metadata(self, tmp_path):
        rec = HDF5Recorder(tmp_path / RESULTS_FILENAME, {"camera_sn": "1"},
                           dict(PARAMS_AT_START), crop=CUT)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            assert _crop_of(f["metadata"]) == CUT_ATTRS
            # Engineering provenance — Params stays exactly her fields.
            assert "crop" not in f["Params"]
            assert set(f["Params"].attrs) == set(PARAMS_AT_START) | {"gitCommit"}

    def test_both_calibration_groups_have_it(self, tmp_path):
        path = tmp_path / CALIBRATION_FILENAME
        write_calibration(path, "dark", {"mean_dark": np.zeros((18, 24))},
                          {"n_frames": 600}, crop=CUT)
        write_calibration(path, "bright", {"spIm": np.zeros((18, 24))},
                          {"n_frames": 600}, crop=CUT)
        with h5py.File(path, "r") as f:
            assert set(f) == {"dark", "bright"}, "no new top-level group"
            assert _crop_of(f["dark"]) == CUT_ATTRS
            assert _crop_of(f["bright"]) == CUT_ATTRS
            assert f["dark"].attrs["n_frames"] == 600, "attrs are kept too"

    def test_recalibrating_replaces_the_crop(self, tmp_path):
        path = tmp_path / CALIBRATION_FILENAME
        write_calibration(path, "dark", {}, crop=CUT)
        full = Crop(0, 0, 64, 48, 64, 48)
        write_calibration(path, "dark", {}, crop=full)
        with h5py.File(path, "r") as f:
            assert _crop_of(f["dark"])["Width"] == 64

    def test_no_crop_given_writes_no_group(self, tmp_path):
        rec = HDF5Recorder(tmp_path / RESULTS_FILENAME, {}, {})
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            assert "crop" not in f["metadata"]

    def test_the_session_writes_the_cameras_crop_into_all_three_places(
            self, tmp_path, monkeypatch):
        """The real path: dark calibration → bright calibration → results
        file, on frames the camera cut. What is recorded is the crop the
        camera reported (_camera_crop), which is what the frames really are."""
        from core.session import BrightCalCollector, DarkCalCollector, State
        w = _window()
        try:
            w._camera_crop = CUT
            w._mask = circle_mask(CUT.shape, {"cx": 28.0, "cy": 19.0, "r": 6.0},
                                  (CUT.offset_x, CUT.offset_y))
            w._session_folder = tmp_path
            w.processor.bit_depth = 12
            w.processor.gain_db   = 8.0
            assert w._prepare_gain()
            rng = np.random.default_rng(0)
            frame = lambda: rng.integers(90, 110, CUT.shape).astype(np.uint16)

            w._dark_cal_collector = DarkCalCollector(2, 7)
            for _ in range(2):
                w._dark_cal_collector.add_frame(frame())
            w._set_state(State.DARK_CAL)
            monkeypatch.setattr(w, "_start_bright_cal", lambda: None)
            w._finish_dark_cal()

            w._bright_cal_collector = BrightCalCollector(1, 7)
            w._bright_cal_collector.add_frame(frame() + 100)
            w._set_state(State.BRIGHT_CAL)
            w._finish_bright_cal()          # → measurement → _start_recorder
            assert w._recorder is not None
            w._stop_recorder()

            with h5py.File(tmp_path / CALIBRATION_FILENAME, "r") as f:
                assert _crop_of(f["dark"]) == CUT_ATTRS
                assert _crop_of(f["bright"]) == CUT_ATTRS
                assert f["dark/mean_dark"].shape == CUT.shape
            with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
                assert _crop_of(f["metadata"]) == CUT_ATTRS
        finally:
            w._set_state(State.PREVIEW)
            w.close()

    def test_a_source_that_cannot_crop_records_its_full_frame(self):
        w = _window()
        try:
            assert w._camera_crop is None          # the fake has no applied_crop
            assert w._session_crop() is None       # no frame, no mask: nothing known
            w._mask = np.ones((18, 24), dtype=bool)
            assert w._session_crop() == Crop(0, 0, 24, 18, 24, 18)
        finally:
            w.close()

    def test_the_figure_shows_the_image_area(self, tmp_path):
        from core.results_figure import parameter_lines
        rec = HDF5Recorder(tmp_path / RESULTS_FILENAME, {}, {}, crop=CUT)
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            rows = dict(parameter_lines(f))
        assert rows["Image area"] == "24 × 18 at (16, 10)"
        assert rows["Sensor"] == "64 × 48 px"

    def test_the_figure_says_full_sensor_when_uncut(self, tmp_path):
        from core.results_figure import parameter_lines
        rec = HDF5Recorder(tmp_path / RESULTS_FILENAME, {}, {},
                           crop=Crop(0, 0, 64, 48, 64, 48))
        rec.close()
        with h5py.File(tmp_path / RESULTS_FILENAME, "r") as f:
            rows = dict(parameter_lines(f))
        assert rows["Image area"] == "full sensor"
