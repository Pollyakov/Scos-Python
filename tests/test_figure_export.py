"""
Tests for saving the session figure — todo E4 / worklist task 12.

`docs/session_tab` asks for `rBfi_fig.fig`. That is MATLAB's own figure format
and Python cannot write it, so this writes `rBfi_fig.png` instead. Nothing is
lost by the substitution: `timeVec` and `rBFi` sit in `rBfi_results.h5` in the
same folder, so a real `.fig` can still be rebuilt in MATLAB from the same
session. Confirmation is still pending — question 7 in
docs/open_questions.md — and the extension is one constant.

Two properties matter beyond "a file appears":

  * The figure must show the **final** curve. The normalization constant is
    provisional while a session runs and is re-picked at FINISHED (task 10),
    so a figure saved before that would disagree with the `rBFi` in the file
    next to it.
  * A figure is a convenience and the data is not. Nothing that goes wrong
    while writing a PNG may interfere with closing a session whose HDF5 is
    already on disk.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow
from gui.plot_widget import PlotWidget

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk, without pulling in an image library."""
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, "not a PNG"
    assert data[12:16] == b"IHDR", "malformed PNG header"
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


class TestSavePng:

    @pytest.fixture
    def plot(self):
        w = PlotWidget()
        yield w
        w.close()

    def test_an_empty_plot_writes_nothing(self, plot, tmp_path):
        out = tmp_path / "fig.png"
        assert plot.save_png(out) == 0
        assert not out.exists(), (
            "a blank figure is worse than none — it looks like a failed measurement"
        )

    def test_a_curve_is_written_as_a_real_png(self, plot, tmp_path):
        for i in range(50):
            plot.append(i * 0.1, 1.0 + 0.01 * i)
        out = tmp_path / "fig.png"

        assert plot.save_png(out) == 50
        assert out.exists() and out.stat().st_size > 0
        assert png_size(out)[0] == 1600

    def test_the_width_is_fixed_not_taken_from_the_window(self, plot, tmp_path):
        # The file should look the same whether the operator had the window
        # maximised or tucked into a corner.
        for i in range(20):
            plot.append(float(i), 1.0)
        plot.resize(200, 150)
        a = tmp_path / "small.png"
        plot.save_png(a)
        plot.resize(1200, 800)
        b = tmp_path / "large.png"
        plot.save_png(b)

        assert png_size(a) == png_size(b)

    def test_buffered_points_are_drawn_before_export(self, plot, tmp_path):
        # Points arrive continuously but the curve redraws on a 1 s timer, so
        # up to a second of data can be pending when the session ends.
        for i in range(30):
            plot.append(float(i), 1.0)
        assert plot._dirty, "precondition: the timer has not fired yet"

        plot.save_png(tmp_path / "fig.png")

        assert not plot._dirty, "the exported figure must include every point"


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
        assert png_size(fig)[0] == 1600

    def test_the_figure_shows_the_final_normalization(self, window, tmp_path):
        # _finalize_normalization rescales the curve at FINISHED; a figure
        # saved before that would not match the rBFi in the file next to it.
        _measure(window)
        before = window.plot_widget.get_data()[1].copy()

        saved = {}
        real_save = window.plot_widget.save_png

        def _spy(path, *a, **k):
            saved["data"] = window.plot_widget.get_data()[1].copy()
            return real_save(path, *a, **k)

        window.plot_widget.save_png = _spy
        window._finish_session()

        assert "data" in saved, "the figure must actually be written"
        assert not np.allclose(saved["data"], before), (
            "this session was short, so the constant changed to the percentile "
            "and the curve must have been rescaled before it was saved"
        )
        np.testing.assert_allclose(saved["data"],
                                   window.plot_widget.get_data()[1])

    def test_a_failing_export_does_not_disturb_the_session(
            self, window, tmp_path, caplog):
        _measure(window)
        window.plot_widget.save_png = lambda *a, **k: (_ for _ in ()).throw(
            OSError("disk full"))

        window._finish_session()      # must not raise

        assert not (tmp_path / "rBfi_fig.png").exists()
        assert "Could not save the plot figure" in caplog.text
        # And the thing that actually matters is still on disk.
        window._stop_recorder()
        import h5py
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
