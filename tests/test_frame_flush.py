"""
Tests for discarding frames captured before the lighting changed.

The calibration collectors run on the GUI thread behind a queued connection.
When the camera outruns that handler — at 40 Hz, with a percentile over 2.4
megapixels per frame, it does — a backlog builds up in Qt's event queue. Every
frame in it was captured before the operator clicked OK on the laser prompt.

Measured on the lab recording, 2026-09-28, with the backlog left in place:

    DARK_CAL   — 49 of 60 collected frames were dark, 11 were laser-on
    BRIGHT_CAL — 60 of 60 collected frames were dark

`dark_var` then came out at 72 instead of 5.6, because a mixture of two
brightness levels 18 DU apart has a temporal variance of roughly (18/2)². The
corrected κ² was negative in 353 of 355 points.

This was found in `--mock-folder` playback, where the two sets are obviously
different, but it is not a playback bug. On the rig the same backlog puts
frames captured before the laser came back on into the bright calibration.
The fix therefore lives in MainWindow and applies to every camera: all three
emitters count what they hand to Qt, this window counts what it receives, and
the difference is dropped after each prompt.

With the flush, the same rehearsal gives `dark_mean` = 99.307 DU and
`dark_var` = 5.555 — equal to what streaming the dark folder offline produces,
and κ²_corr positive in 315 of 315 points.
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


class _CountingCamera(QObject):
    """Camera stub that reports how many frames it has handed to Qt."""

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


class _NoCounterCamera(_CountingCamera):
    """A source with no live stream to flush, like the HDF5 replay stub."""

    def __init__(self):
        super().__init__()
        del self.frames_emitted


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = MainWindow(camera=_CountingCamera())
    yield w
    w.close()


DARK, BRIGHT = 100, 118


def _frame(value):
    return np.full((8, 8), value, dtype=np.uint16)


class TestFlush:

    def test_backlog_is_measured_from_the_two_counters(self, window):
        # The camera is 50 frames ahead of what this window has processed.
        window.camera.frames_emitted = 50
        window._frames_seen = 0

        window._flush_stale_frames("the laser was switched off")

        assert window._skip_frames_until == 50 + window._FLUSH_MARGIN_FRAMES

    def test_stale_frames_reach_no_collector(self, window):
        from core.session import DarkCalCollector

        window._mask = np.ones((8, 8), dtype=bool)
        window.camera.frames_emitted = 5      # five frames still queued
        window._flush_stale_frames("the laser was switched off")
        window._dark_cal_collector = DarkCalCollector(3, 3)
        window._set_state(State.DARK_CAL)

        for _ in range(5 + window._FLUSH_MARGIN_FRAMES - 1):
            window._on_scos_frame(_frame(BRIGHT), 0.0)   # pre-prompt frames
        assert window._dark_cal_collector.n_collected == 0, (
            "not one frame from before the prompt may enter the calibration"
        )

        for _ in range(3):
            window._on_scos_frame(_frame(DARK), 0.0)
        assert window.processor.dark_mean is not None
        assert float(np.mean(window.processor.dark_mean)) == pytest.approx(DARK), (
            "and the calibration must be built from the frames after it"
        )

    def test_a_margin_covers_the_frame_already_in_flight(self, window):
        # The counter is read on the GUI thread while the camera thread may be
        # mid-iteration, so the cutoff has to sit past the frame in flight.
        assert window._FLUSH_MARGIN_FRAMES >= 1

    def test_no_backlog_means_no_frames_lost(self, window):
        from core.session import DarkCalCollector

        window._mask = np.ones((8, 8), dtype=bool)
        window.camera.frames_emitted = 0
        window._frames_seen = 0
        window._flush_stale_frames("the laser was switched off")
        window._dark_cal_collector = DarkCalCollector(2, 3)
        window._set_state(State.DARK_CAL)

        # Only the margin is skipped, and it is small enough not to matter.
        for _ in range(2 + window._FLUSH_MARGIN_FRAMES):
            window._on_scos_frame(_frame(DARK), 0.0)
        assert window._dark_cal_collector is None, "the collector finished"

    def test_a_source_without_a_counter_is_left_alone(self, monkeypatch, tmp_path):
        w = MainWindow(camera=_NoCounterCamera())
        try:
            w._skip_frames_until = 0
            w._flush_stale_frames("the laser was switched off")
            assert w._skip_frames_until == 0, (
                "an HDF5 replay has no live stream and nothing to discard"
            )
        finally:
            w.close()


class TestFlushIsWiredIntoBothPrompts:
    """Where it is called matters as much as what it does."""

    def test_dark_calibration_flushes_first(self, window):
        window.camera.frames_emitted = 40
        window._frames_seen = 0

        window._start_dark_cal()

        assert window._state is State.DARK_CAL
        assert window._skip_frames_until >= 40

    def test_bright_calibration_flushes_too(self, window):
        # The bright prompt is the one that mattered in the measured run:
        # 60 of 60 collected "bright" frames were actually dark.
        window._start_dark_cal()
        window.camera.frames_emitted = 200
        window._frames_seen = 10

        window._start_bright_cal()

        assert window._state is State.BRIGHT_CAL
        assert window._skip_frames_until >= 200
