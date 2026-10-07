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
_mock_pylon.TimeoutHandling_Return = 1

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


# ---------------------------------------------------------------------------
# The grab loop: capture times and lost frames (core/frame_clock.py, todo D5)
# ---------------------------------------------------------------------------

import types as _types

import numpy as np

import camera as camera_module


class _FakeResult:
    def __init__(self, t_retrieved, ticks, block_id, ok=True, valid=True):
        self.t_retrieved = t_retrieved
        self.TimeStamp   = ticks
        self.BlockID     = block_id
        self._ok, self._valid = ok, valid
        self.Array = np.zeros((4, 4), dtype=np.uint16)

    def IsValid(self):       return self._valid
    def GrabSucceeded(self): return self._ok
    def Release(self):       pass


class _FakePylonCamera:
    """Hands out prepared grab results, moving the fake PC clock to each one's
    retrieval time; stops the loop when they run out."""

    def __init__(self, results, thread, clock):
        self._results, self._thread, self._clock = list(results), thread, clock
        self.MaxNumBuffer = _types.SimpleNamespace(Value=0)
        self._grabbing = False

    def StartGrabbing(self, _strategy): self._grabbing = True
    def StopGrabbing(self):             self._grabbing = False
    def IsGrabbing(self):               return self._grabbing

    def RetrieveResult(self, _timeout, _mode):
        if not self._results:
            self._thread._running = False
            return _FakeResult(0, 0, 0, valid=False)
        r = self._results.pop(0)
        self._clock[0] = r.t_retrieved
        return r


def _run_grab_loop(monkeypatch, results):
    """Run CameraThread.run() synchronously on fake results; return what it emitted."""
    pc_now = [0.0]
    monkeypatch.setattr(camera_module.time, "monotonic", lambda: pc_now[0])
    cam = CameraThread()
    cam.camera = _FakePylonCamera(results, cam, pc_now)
    emitted, warnings = [], []
    cam.frame_ready.connect(lambda frame, t: emitted.append(t))
    cam.warning.connect(warnings.append)
    cam._running = True
    cam.run()
    return cam, emitted, warnings


def _results_20hz(n, *, stall=None, missing=(), failed=()):
    """n frames at 20 Hz, 1 GHz camera ticks, retrieved 4 ms after exposure.
    stall=(first, last): those frames are retrieved together, 2 ms apart,
    right after `last` was exposed — a backlog in Pylon's buffers."""
    out = []
    for i in range(n):
        if i in missing:
            continue
        t_exp = 1000.0 + i * 0.05
        t_ret = t_exp + 0.004
        if stall and stall[0] <= i <= stall[1]:
            t_ret = 1000.0 + stall[1] * 0.05 + 0.004 + 0.002 * (i - stall[0])
        out.append(_FakeResult(t_ret, 10**9 + round(i * 0.05 * 1e9), i + 1,
                               ok=i not in failed))
    return out


class TestGrabLoopTiming:

    def test_backlog_keeps_exposure_spacing(self, monkeypatch):
        cam, t, _ = _run_grab_loop(monkeypatch, _results_20hz(240, stall=(160, 179)))
        assert cam.time_source == "camera"
        gaps = np.diff(t[150:200])
        assert gaps == pytest.approx([0.05] * len(gaps), abs=1e-9)

    def test_lost_frames_counted_and_reported(self, monkeypatch):
        cam, t, warnings = _run_grab_loop(
            monkeypatch, _results_20hz(240, missing={200, 201, 202}, failed={220}))
        assert cam.frames_lost == 4                     # 3 never arrived + 1 broken
        assert len(t) == 240 - 3 - 1
        assert any("lost" in w for w in warnings)
        # The gap in the capture times is the true one: 4 frame periods.
        assert max(np.diff(t[150:])) == pytest.approx(0.20, abs=1e-9)

    def test_camera_without_clock_or_ids_behaves_as_before(self, monkeypatch):
        results = _results_20hz(100)
        for r in results:
            r.TimeStamp, r.BlockID = 0, 0
        cam, t, warnings = _run_grab_loop(monkeypatch, results)
        assert cam.time_source == "pc"
        assert t == [r.t_retrieved for r in results]
        assert cam.frames_lost == 0 and not warnings

    def test_no_clock_before_start_video(self):
        cam = CameraThread()
        assert cam.frames_lost == 0
        assert cam.time_source == "pc"
