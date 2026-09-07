"""
Tests for MockCameraThread.
Parallel to tests/test_camera.py — no real camera or Basler SDK needed.
"""

import sys
import os
import time
import numpy as np
import pytest
import tifffile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import Qt, QCoreApplication
from mock_camera import MockCameraThread

# One QCoreApplication for the whole test session (QThread requires it)
_app = QCoreApplication.instance() or QCoreApplication(sys.argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tiff(path, n_frames: int = 5, h: int = 32, w: int = 32) -> np.ndarray:
    """Write a small uint16 TIFF stack and return the array."""
    rng   = np.random.default_rng(0)
    stack = rng.integers(100, 4096, size=(n_frames, h, w), dtype=np.uint16)
    tifffile.imwrite(str(path), stack)
    return stack


# ---------------------------------------------------------------------------
# Signal emission
# ---------------------------------------------------------------------------

class TestMockCameraEmission:

    def test_emits_all_frames_when_not_looping(self, tmp_path):
        """With loop=False, frame_ready fires exactly N times then stops."""
        _make_tiff(tmp_path / "stack.tif", n_frames=5)
        mock = MockCameraThread(str(tmp_path / "stack.tif"), loop=False)
        mock.frame_rate = 1000   # no sleeping — run through frames instantly

        received = []
        mock.frame_ready.connect(
            lambda f, t: received.append(f.copy()),
            Qt.ConnectionType.DirectConnection,
        )
        mock.start_capture()
        mock.wait()

        assert len(received) == 5

    def test_emitted_frame_shape_and_dtype(self, tmp_path):
        """Each emitted frame should match the TIFF's shape and dtype."""
        stack = _make_tiff(tmp_path / "stack.tif", n_frames=3, h=16, w=24)
        mock  = MockCameraThread(str(tmp_path / "stack.tif"), loop=False)
        mock.frame_rate = 1000

        received = []
        mock.frame_ready.connect(
            lambda f, t: received.append(f.copy()),
            Qt.ConnectionType.DirectConnection,
        )
        mock.start_capture()
        mock.wait()

        assert len(received) == 3
        for frame in received:
            assert frame.shape == (16, 24)
            assert frame.dtype == np.uint16

    def test_loop_wraps_around(self, tmp_path):
        """With loop=True the thread keeps emitting past the end of the stack."""
        _make_tiff(tmp_path / "stack.tif", n_frames=3)
        mock = MockCameraThread(str(tmp_path / "stack.tif"), loop=True)
        mock.frame_rate = 1000

        received = []
        def _collect(f, t):
            received.append(None)
            if len(received) >= 7:     # more than one full pass
                mock._running = False  # stop from inside the emitting thread
        mock.frame_ready.connect(_collect, Qt.ConnectionType.DirectConnection)

        mock.start_capture()
        mock.wait()

        assert len(received) >= 7


class TestMockCameraCaptureTimestamps:
    """frame_ready carries the monotonic time the frame was captured.

    Timestamping at capture (rather than when the GUI thread gets round to the
    frame) is what keeps timeVec evenly spaced — see merged_worklist task 5.
    """

    def test_timestamp_is_monotonic_and_recent(self, tmp_path):
        _make_tiff(tmp_path / "stack.tif", n_frames=5)
        mock = MockCameraThread(str(tmp_path / "stack.tif"), loop=False)
        mock.frame_rate = 1000

        stamps = []
        mock.frame_ready.connect(
            lambda f, t: stamps.append(t),
            Qt.ConnectionType.DirectConnection,
        )
        t_before = time.monotonic()
        mock.start_capture()
        mock.wait()
        t_after = time.monotonic()

        assert len(stamps) == 5
        # Same clock, taken inside the run: bracketed by the test's own reads.
        assert t_before <= stamps[0]
        assert stamps[-1] <= t_after
        # Strictly non-decreasing — a later frame can never carry an earlier time
        assert stamps == sorted(stamps)

    def test_timestamps_track_the_playback_rate(self, tmp_path):
        """At a fixed FPS the gaps between capture times track 1/FPS."""
        _make_tiff(tmp_path / "stack.tif", n_frames=6)
        mock = MockCameraThread(str(tmp_path / "stack.tif"), loop=False)
        mock.frame_rate = 50            # 20 ms per frame

        stamps = []
        mock.frame_ready.connect(
            lambda f, t: stamps.append(t),
            Qt.ConnectionType.DirectConnection,
        )
        mock.start_capture()
        mock.wait()

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert len(gaps) == 5
        # Generous bound: msleep granularity is coarse on Windows, and this
        # asserts the timestamps follow the capture cadence, not the exact rate.
        for gap in gaps:
            assert 0.005 <= gap <= 0.20, f"gap {gap:.3f}s outside plausible range"


# ---------------------------------------------------------------------------
# Setters
# ---------------------------------------------------------------------------

class TestMockCameraSetters:

    def test_setters_store_values(self, tmp_path):
        _make_tiff(tmp_path / "stack.tif")
        mock = MockCameraThread(str(tmp_path / "stack.tif"))
        mock.set_exposure(5000.0)
        mock.set_gain(8.0)
        mock.set_frame_rate(20.0)
        mock.set_pixel_format("Mono8")
        mock.set_trigger(True, 100.0)
        mock.set_roi(10, 10, 200, 200)

        assert mock.exposure_us  == 5000.0
        assert mock.gain_db      == 8.0
        assert mock.frame_rate   == 20.0
        assert mock.pixel_format == "Mono8"
        assert mock.trigger_mode == "On"
        assert mock.trigger_delay == 100.0
        assert mock.roi_position == (10, 10, 200, 200)

    def test_set_trigger_off(self, tmp_path):
        _make_tiff(tmp_path / "stack.tif")
        mock = MockCameraThread(str(tmp_path / "stack.tif"))
        mock.set_trigger(False)
        assert mock.trigger_mode == "Off"


# ---------------------------------------------------------------------------
# get_info
# ---------------------------------------------------------------------------

class TestMockCameraGetInfo:

    def test_get_info_after_open(self, tmp_path):
        _make_tiff(tmp_path / "stack.tif", h=16, w=24)
        mock = MockCameraThread(str(tmp_path / "stack.tif"))
        mock.open()
        info = mock.get_info()
        assert info["model"]  == "MockTIFF"
        assert info["height"] == 16
        assert info["width"]  == 24

    def test_get_info_before_open(self, tmp_path):
        _make_tiff(tmp_path / "stack.tif")
        mock = MockCameraThread(str(tmp_path / "stack.tif"))
        info = mock.get_info()        # must not crash
        assert info["width"]  == 0
        assert info["height"] == 0

    def test_open_is_idempotent(self, tmp_path):
        """Calling open() twice should not reload the stack."""
        _make_tiff(tmp_path / "stack.tif")
        mock = MockCameraThread(str(tmp_path / "stack.tif"))
        mock.open()
        first_id  = id(mock._stack)
        mock.open()
        second_id = id(mock._stack)
        assert first_id == second_id
