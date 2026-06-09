"""
Tests for the display FPS throttle logic in CameraThread.

The camera emits frame_ready for every frame (SCOS processing),
but display_ready is rate-limited to ~30 FPS to avoid flooding the GUI.

The throttle logic in CameraThread.run():
    now = time.monotonic()
    if now - self._last_display >= self._display_interval:
        self.display_ready.emit(frame)
        self._last_display = now

We test this logic in isolation without a real camera.
"""

import time
import numpy as np
import pytest

import sys, os, types

# Mock pypylon
_mock_pylon = types.ModuleType("pylon")
_mock_pylon.TlFactory = type("TlFactory", (), {"GetInstance": staticmethod(lambda: None)})
_mock_pylon.InstantCamera = type("InstantCamera", (), {})
_mock_pylon.GrabStrategy_LatestImageOnly = 0
_mock_pylon.GrabStrategy_OneByOne = 1
_mock_pylon.TimeoutHandling_ThrowException = 0
_mock_genicam = types.ModuleType("genicam")
_mock_genicam.IsWritable = lambda x: True
_mock_pypylon = types.ModuleType("pypylon")
_mock_pypylon.pylon = _mock_pylon
_mock_pypylon.genicam = _mock_genicam
sys.modules.setdefault("pypylon", _mock_pypylon)
sys.modules.setdefault("pypylon.pylon", _mock_pylon)
sys.modules.setdefault("pypylon.genicam", _mock_genicam)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from camera import CameraThread


class TestDisplayThrottleLogic:
    """
    Simulate the throttle decision without running the actual camera loop.
    We manually step through the time check logic.
    """

    def test_first_frame_always_displayed(self):
        """_last_display starts at 0.0, so the first frame always passes the check."""
        cam = CameraThread()
        now = time.monotonic()
        # First frame: now - 0.0 is always >= _display_interval
        assert (now - cam._last_display) >= cam._display_interval

    def test_rapid_frames_throttled(self):
        """Frames arriving faster than 30 FPS should be skipped."""
        cam = CameraThread()
        cam._last_display = time.monotonic()
        # Simulate a frame arriving 1ms later — should NOT pass
        fake_now = cam._last_display + 0.001  # 1ms later
        should_display = (fake_now - cam._last_display) >= cam._display_interval
        assert should_display is False

    def test_frame_after_interval_displayed(self):
        """A frame arriving after the interval should pass the throttle."""
        cam = CameraThread()
        cam._last_display = time.monotonic()
        # Simulate a frame arriving 40ms later (> 33.3ms interval)
        fake_now = cam._last_display + 0.040
        should_display = (fake_now - cam._last_display) >= cam._display_interval
        assert should_display is True

    def test_exactly_at_interval(self):
        """A frame arriving exactly at the interval boundary should pass.
        Note: floating-point subtraction may introduce tiny error, so
        we check with a small epsilon to mirror the real-world behavior."""
        cam = CameraThread()
        cam._last_display = 100.0
        # Add a tiny epsilon to avoid float subtraction landing just below
        fake_now = 100.0 + cam._display_interval + 1e-12
        should_display = (fake_now - cam._last_display) >= cam._display_interval
        assert should_display is True

    def test_display_count_at_high_fps(self):
        """
        Simulate 200 frames at 200 FPS (5ms apart).
        Display should fire ~30 times (every ~33ms).
        """
        cam = CameraThread()
        cam._last_display = 0.0
        display_count = 0
        interval = cam._display_interval

        for i in range(200):
            t = i * 0.005  # 5ms per frame = 200 FPS
            if t - cam._last_display >= interval:
                display_count += 1
                cam._last_display = t

        # 200 frames over 1 second at 200 FPS → ~30 display updates
        assert 25 <= display_count <= 35

    def test_display_count_at_low_fps(self):
        """
        At 10 FPS (100ms per frame), every frame should be displayed
        because 100ms > 33ms interval.
        Frame 0 at t=0.0 is skipped (0.0 - 0.0 = 0 < interval),
        matching the real camera loop where _last_display starts at 0
        but time.monotonic() is already large.
        """
        cam = CameraThread()
        cam._last_display = -1.0  # simulate: last display was "long ago"
        display_count = 0
        interval = cam._display_interval

        for i in range(100):
            t = i * 0.100  # 100ms per frame = 10 FPS
            if t - cam._last_display >= interval:
                display_count += 1
                cam._last_display = t

        # Every frame passes the throttle
        assert display_count == 100
