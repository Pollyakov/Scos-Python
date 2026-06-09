"""
Tests for camera.py — CameraThread parameter storage and display throttle logic.

No real camera needed — we test the in-memory state before any hardware calls.
pypylon is imported by camera.py, so we mock it at module level.
"""

import sys
import os
import types
import pytest

# --- Mock pypylon before importing camera.py ---
# camera.py does `from pypylon import pylon, genicam`
# We create fake modules so the import succeeds without Basler SDK.
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

sys.modules["pypylon"] = _mock_pypylon
sys.modules["pypylon.pylon"] = _mock_pylon
sys.modules["pypylon.genicam"] = _mock_genicam

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from camera import CameraThread


# We need QApplication for QThread
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


class TestCameraThreadDefaults:
    """Test initial parameter values after construction."""

    def test_default_pixel_format(self):
        cam = CameraThread()
        assert cam.pixel_format == "Mono12"

    def test_default_exposure(self):
        cam = CameraThread()
        assert cam.exposure_us == 10000.0  # 10 ms in µs

    def test_default_gain(self):
        cam = CameraThread()
        assert cam.gain_db == 0.0

    def test_default_frame_rate(self):
        cam = CameraThread()
        assert cam.frame_rate == 50.0

    def test_default_trigger_off(self):
        cam = CameraThread()
        assert cam.trigger_mode == "Off"

    def test_default_trigger_delay(self):
        cam = CameraThread()
        assert cam.trigger_delay == 0.0

    def test_default_roi_none(self):
        cam = CameraThread()
        assert cam.roi_position is None

    def test_not_running(self):
        cam = CameraThread()
        assert cam._running is False

    def test_camera_none(self):
        cam = CameraThread()
        assert cam.camera is None


class TestCameraThreadSetters:
    """
    When no camera is connected (camera=None), setters should only
    update the stored value without crashing.
    """

    def test_set_exposure_stores_value(self):
        cam = CameraThread()
        cam.set_exposure(5000.0)
        assert cam.exposure_us == 5000.0

    def test_set_gain_stores_value(self):
        cam = CameraThread()
        cam.set_gain(12.0)
        assert cam.gain_db == 12.0

    def test_set_frame_rate_stores_value(self):
        cam = CameraThread()
        cam.set_frame_rate(100.0)
        assert cam.frame_rate == 100.0

    def test_set_trigger_on(self):
        cam = CameraThread()
        cam.set_trigger(True, 500.0)
        assert cam.trigger_mode == "On"
        assert cam.trigger_delay == 500.0

    def test_set_trigger_off(self):
        cam = CameraThread()
        cam.set_trigger(False)
        assert cam.trigger_mode == "Off"

    def test_set_pixel_format(self):
        cam = CameraThread()
        cam.set_pixel_format("Mono8")
        assert cam.pixel_format == "Mono8"

    def test_set_roi(self):
        cam = CameraThread()
        cam.set_roi(100, 200, 640, 480)
        assert cam.roi_position == (100, 200, 640, 480)


class TestDisplayThrottle:
    """Test the display FPS cap logic."""

    def test_display_fps_cap_is_30(self):
        cam = CameraThread()
        assert cam.DISPLAY_FPS_CAP == 30.0

    def test_display_interval_correct(self):
        cam = CameraThread()
        expected = 1.0 / 30.0  # ~33.3 ms
        assert cam._display_interval == pytest.approx(expected)

    def test_last_display_starts_at_zero(self):
        cam = CameraThread()
        assert cam._last_display == 0.0
