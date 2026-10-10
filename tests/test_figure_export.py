"""
Tests for the session figure — protocol step 6g, todo U5 (was E4 / task 12).

`docs/session_tab` asks for `rBfi_fig.fig`. That is MATLAB's own figure format
and Python cannot write it, so this writes `rBfi_fig.png` instead — accepted by
the supervisor as a first version (open question 7, 2026-10-07); a reopenable
figure is todo F5.

Since U5 the figure is drawn from the results file (core/results_figure.py):
rBFi on top, <I> below, each with its own axes (the user's choice,
2026-10-10), and a box with the parameters the files record. What matters:

  * The figure shows exactly the data saved beside it — the final rBFi, NaN
    rows included, and the Intensity — not the live plot, which has neither
    the NaNs nor <I>.
  * A figure is a convenience and the data is not. Nothing that goes wrong
    while writing a PNG may interfere with closing a session whose HDF5 is
    already on disk.
"""

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

import core.results_figure as results_figure
from core.recorder import CALIBRATION_FILENAME, HDF5Recorder, write_calibration
from core.results_figure import (MISSING, build_results_figure,
                                 parameter_lines, read_calibration_info,
                                 save_results_figure)
from core.session import State
from gui.main_window import MainWindow

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk, without pulling in an image library."""
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, "not a PNG"
    assert data[12:16] == b"IHDR", "malformed PNG header"
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


PARAMS = {
    "frameRate": 20.0, "exposureTime": 8.0, "gain": 8.0, "windowSize": 7,
    "ROI": [1221.0, 865.0, 278.0], "bitDepth": 12,
}
META = {
    "camera_sn": "40075248", "camera_model": "a2A1920-160umBAS",
    "gain_du_per_e": 0.9564, "gain_source": "table",
}


def _results_file(tmp_path, seconds=10.0, fps=20.0, bad=(), rbfi=True,
                  calibration=True):
    """A finished results file (and Calibration.h5) like a session leaves."""
    rec = HDF5Recorder(tmp_path / "rBfi_results.h5", dict(META), dict(PARAMS))
    n = int(seconds * fps)
    for i in range(n):
        k2 = -0.01 if i in bad else 0.01 * (1.0 + 0.2 * np.sin(i / 3.0))
        rec.append(i / fps, 0.02, k2, 114.0 + np.cos(i / 5.0))
    rec.set_metadata(time_source="camera", frames_lost_camera=0,
                     frames_dropped_queue=3,
                     normalization_type="Pulsation lower level")
    if rbfi:
        rec.write_rbfi(80.0, "percentile5", 5.0)
    rec.close()
    if calibration:
        cal = tmp_path / CALIBRATION_FILENAME
        write_calibration(cal, "dark", {"mean_dark": np.zeros((4, 4))},
                          {"n_frames": 600, "window_size": 7})
        write_calibration(cal, "bright", {"spIm": np.ones((4, 4))},
                          {"n_frames": 60, "window_size": 7})
    return tmp_path / "rBfi_results.h5"


class TestResultsFigure:

    def test_the_top_plot_is_the_files_rbfi_nan_gaps_included(self, tmp_path):
        path = _results_file(tmp_path, bad={5, 6})
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
            t, rbfi = f["timeVec"][:], f["rBFi"][:]
        line = fig.axes[0].lines[0]
        np.testing.assert_array_equal(line.get_xdata(), t)
        # NaN where κ² ≤ 0, kept as a gap — not dropped, not joined across.
        np.testing.assert_array_equal(line.get_ydata(), rbfi)
        assert np.isnan(line.get_ydata()[5])

    def test_the_bottom_plot_is_the_files_intensity(self, tmp_path):
        path = _results_file(tmp_path)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
            inten = f["Intensity"][:]
        assert fig.axes[1].get_ylabel() == "<I> [DU]"
        np.testing.assert_array_equal(fig.axes[1].lines[0].get_ydata(), inten)

    def test_the_two_plots_are_stacked_with_their_own_axes(self, tmp_path):
        # The user's choice, 2026-10-10: one above the other, nothing shared.
        path = _results_file(tmp_path)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
        top, bottom = fig.axes[0], fig.axes[1]
        assert top.get_position().y0 > bottom.get_position().y1
        assert not top.get_shared_x_axes().joined(top, bottom)
        assert not top.get_shared_y_axes().joined(top, bottom)

    def test_long_recordings_are_plotted_in_minutes(self, tmp_path):
        # Same switch as the reference script (Ver2.m:498): > 120 s → minutes.
        path = _results_file(tmp_path, seconds=150.0, fps=2.0)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
            t = f["timeVec"][:]
        assert fig.axes[0].get_xlabel() == "time [min]"
        np.testing.assert_allclose(fig.axes[0].lines[0].get_xdata(), t / 60.0)

    def test_short_recordings_are_plotted_in_seconds(self, tmp_path):
        path = _results_file(tmp_path, seconds=10.0)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
        assert fig.axes[0].get_xlabel() == "time [sec]"
        assert fig.axes[1].get_xlabel() == "time [sec]"

    def test_rbfi_axis_follows_the_reference_script(self, tmp_path):
        # ylim([0 min(10,max(rBFi))]) — Ver2.m:527.
        path = _results_file(tmp_path)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
            top = float(np.nanmax(f["rBFi"][:]))
        assert fig.axes[0].get_ylim() == pytest.approx((0.0, min(10.0, top)))

    def test_the_parameters_box_shows_what_the_files_record(self, tmp_path):
        path = _results_file(tmp_path, bad={1})
        with h5py.File(path, "r") as f:
            rows = dict(parameter_lines(f, read_calibration_info(
                tmp_path / CALIBRATION_FILENAME)))
        assert rows["Camera SN"]       == "40075248"
        assert rows["Pixel format"]    == "Mono12"
        assert rows["Exposure"]        == "8 ms"
        assert rows["Gain"]            == "8 dB"
        assert rows["G"]               == "0.9564 DU/e (table)"
        assert rows["Frame rate"]      == "20 Hz"
        assert rows["ROI"]             == "x 1221, y 865, r 278 px"
        assert rows["Dark cal."]       == "600 frames"
        assert rows["Bright cal."]     == "60 frames"
        assert rows["Norm. type"]      == "Pulsation lower level"
        assert rows["Normalization"]   == "percentile5"
        assert rows["Norm. window"]    == "5.0 s"
        assert rows["Norm. constant"]  == "80"
        assert rows["Dropped (queue)"] == "3 frames"
        assert rows["Frames"]          == "200 (1 with κ² ≤ 0)"

    def test_the_box_text_is_drawn_into_the_figure(self, tmp_path):
        path = _results_file(tmp_path)
        with h5py.File(path, "r") as f:
            fig = build_results_figure(f)
        drawn = "\n".join(t.get_text() for t in fig.axes[2].texts)
        assert "40075248" in drawn and "0.9564 DU/e" in drawn

    def test_what_is_not_recorded_is_shown_as_missing(self, tmp_path):
        # No Calibration.h5: the box must not invent frame counts.
        path = _results_file(tmp_path, calibration=False)
        with h5py.File(path, "r") as f:
            rows = dict(parameter_lines(
                f, read_calibration_info(tmp_path / CALIBRATION_FILENAME)))
        assert rows["Dark cal."] == MISSING
        assert rows["Bright cal."] == MISSING

    def test_a_png_of_fixed_size_is_written(self, tmp_path):
        path = _results_file(tmp_path)
        out = tmp_path / "fig.png"
        with h5py.File(path, "r") as f:
            assert save_results_figure(f, out, tmp_path / CALIBRATION_FILENAME,
                                       "title") == 200
        assert png_size(out) == (1600, 900)

    def test_no_rbfi_writes_nothing(self, tmp_path):
        path = _results_file(tmp_path, rbfi=False)
        out = tmp_path / "fig.png"
        with h5py.File(path, "r") as f:
            assert save_results_figure(f, out) == 0
        assert not out.exists(), (
            "a figure without its main curve looks like a failed measurement"
        )

    def test_hours_of_data_can_be_drawn(self, tmp_path):
        # 3 h at 20 Hz is one line of 216 000 points — the figure is drawn
        # from the whole file, not a downsampled copy.
        n = 216_000
        with h5py.File(tmp_path / "big.h5", "w") as f:
            f["timeVec"]   = np.arange(n) / 20.0
            f["rBFi"]      = 1.0 + 0.3 * np.random.default_rng(0).standard_normal(n)
            f["Intensity"] = np.full(n, 114.0)
            out = tmp_path / "big.png"
            assert save_results_figure(f, out) == n
        assert out.exists()


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

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0

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
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = MainWindow(camera=_StubCamera())
    w._session_folder = tmp_path
    w._roi_circ = {"cx": 608.0, "cy": 400.0, "r": 150.0}
    w.processor.bit_depth = 12
    w.processor.gain_db   = 8.0
    assert w._prepare_gain()
    w._start_recorder()
    w._norm_seconds = 2.0
    w._set_state(State.MEASURING_INIT)
    yield w
    w.close()


def _measure(window, seconds=8.0, fps=40.0):
    """Feed a whole recording through the real handler."""
    k2_cycle = [0.10, 0.08, 0.125, 0.09, 0.11]
    for i in range(int(seconds * fps) + 1):
        window._on_scos_result(i / fps, 0.09, k2_cycle[i % 5], 500.0, 1.0)


class TestSessionWritesTheFigure:

    def test_finishing_a_session_leaves_a_png_beside_the_results(
            self, window, tmp_path):
        _measure(window)
        window._finish_session()

        fig = tmp_path / "rBfi_fig.png"
        assert fig.exists(), "session_tab asks for a figure in the session folder"
        assert png_size(fig) == (1600, 900)

    def test_the_figure_shows_the_saved_rbfi_and_intensity(
            self, window, tmp_path, monkeypatch):
        # Drawn through the recorder's still-open handle at FINISHED: what it
        # shows must be the final rBFi written to the file, not the curve the
        # live plot drew against a provisional constant.
        _measure(window)
        built = {}
        real_build = results_figure.build_results_figure

        def _spy(*a, **k):
            built["fig"] = real_build(*a, **k)
            return built["fig"]

        monkeypatch.setattr(results_figure, "build_results_figure", _spy)
        window._finish_session()
        window._stop_recorder()

        assert "fig" in built and built["fig"] is not None
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            rbfi, inten = f["rBFi"][:], f["Intensity"][:]
        np.testing.assert_array_equal(built["fig"].axes[0].lines[0].get_ydata(), rbfi)
        np.testing.assert_array_equal(built["fig"].axes[1].lines[0].get_ydata(), inten)
        assert built["fig"].get_suptitle() == tmp_path.name

    def test_a_failing_export_does_not_disturb_the_session(
            self, window, tmp_path, caplog, monkeypatch):
        _measure(window)
        monkeypatch.setattr(
            "gui.main_window.save_results_figure",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

        window._finish_session()      # must not raise

        assert not (tmp_path / "rBfi_fig.png").exists()
        assert "Could not save the plot figure" in caplog.text
        # And the thing that actually matters is still on disk.
        window._stop_recorder()
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert "rBFi" in f

    def test_no_session_folder_means_no_attempt(self, window):
        _measure(window)
        window._session_folder = None
        window._save_plot_figure()     # must not raise

    def test_a_session_with_no_curve_writes_no_figure(self, window, tmp_path):
        # Stopped before any usable result arrived.
        window._finish_session()
        assert not (tmp_path / "rBfi_fig.png").exists()
