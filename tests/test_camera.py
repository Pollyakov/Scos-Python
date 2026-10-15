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
        assert cam.clock_settled


class TestMeasurementWaitsForTheClockCheck:
    """todo D10, end to end: the real grab loop feeds the real pipeline intake
    the way MainWindow wires them, with the measurement opened right after a
    camera restart (short calibrations). Retrieval delays jitter, so PC stamps
    are uneven and camera stamps are not: one PC stamp in timeVec shows."""

    @staticmethod
    def _jittery(n, *, missing=()):
        out = []
        for r in _results_20hz(n, missing=missing):
            r.t_retrieved += 0.003 * (r.BlockID % 4)      # 4–13 ms, uneven
            out.append(r)
        return out

    def _run(self, monkeypatch, results, *, gate=True):
        from core.pipeline import RealtimePipeline
        pc_now = [0.0]
        monkeypatch.setattr(camera_module.time, "monotonic", lambda: pc_now[0])
        cam = CameraThread()
        cam.camera = _FakePylonCamera(results, cam, pc_now)
        pipeline = RealtimePipeline(object(), n_workers=1)
        kept = []
        # Stand-in for the worker queue: record what the intake keeps.
        pipeline._input_q.put = lambda item, **_kw: kept.append(item[2])
        pipeline.enable_intake(
            np.ones((4, 4), dtype=bool),
            clock_settled=(lambda: cam.clock_settled) if gate else None,
            frames_lost=lambda: cam.frames_lost)
        cam.frame_ready.connect(pipeline.on_frame)        # same thread: direct
        cam._running = True
        cam.run()
        return cam, pipeline, kept

    def test_timevec_is_camera_time_from_its_first_point(self, monkeypatch):
        cam, pipeline, kept = self._run(monkeypatch, self._jittery(160))
        assert cam.time_source == "camera"
        assert kept[0] == 0.0
        assert np.diff(kept) == pytest.approx([0.05] * (len(kept) - 1), abs=1e-9)
        # ≈ 5 s at 20 Hz went to the check; the rest is the measurement.
        assert 95 <= pipeline.skipped_before_t0 <= 105
        assert len(kept) + pipeline.skipped_before_t0 == 160

    def test_without_the_wait_timevec_mixes_the_two_clocks(self, monkeypatch):
        """The fault D10 describes — proves the test above can see it."""
        _, _, kept = self._run(monkeypatch, self._jittery(160), gate=False)
        gaps = np.diff(kept)
        assert max(abs(gaps - 0.05)) > 0.002

    def test_frames_lost_during_the_check_are_not_the_measurements(self, monkeypatch):
        cam, pipeline, _ = self._run(monkeypatch,
                                     self._jittery(160, missing={40, 140}))
        assert cam.frames_lost == 2
        assert cam.frames_lost - pipeline.frames_lost_at_t0 == 1   # only #140


# ---------------------------------------------------------------------------
# Camera-side crop (todo U6): set_roi / clear_roi / camera_steps, read-back
# ---------------------------------------------------------------------------

from core.crop import CameraSteps, Crop


class _IntNode:
    """A GenICam integer node that enforces its step, like a real camera:
    a value off the step or outside min..max raises. `max_fn` lets Width's
    maximum depend on the current OffsetX, as it does on a camera."""

    def __init__(self, value, vmin, vmax, inc, max_fn=None, adjust=None):
        self._v, self.Min, self._max, self.Inc = value, vmin, vmax, inc
        self._max_fn, self._adjust = max_fn, adjust

    @property
    def Max(self):
        return self._max_fn() if self._max_fn else self._max

    @property
    def Value(self):
        return self._v

    @Value.setter
    def Value(self, v):
        if self._adjust:                      # a camera that silently adjusts
            v = self._adjust(v)
        if v < self.Min or v > self.Max or (v - self.Min) % self.Inc:
            raise ValueError(f"value {v} invalid (min {self.Min}, max {self.Max}, inc {self.Inc})")
        self._v = v


class _CropCamera:
    """Just enough of a pylon InstantCamera for _apply_params()."""

    SENSOR_W, SENSOR_H = 1936, 1216

    def __init__(self, grabbing=False, inc=4, width_adjust=None, center=None):
        W, H = self.SENSOR_W, self.SENSOR_H
        self.OffsetX = _IntNode(0, 0, 0, inc, max_fn=lambda: W - self.Width.Value)
        self.OffsetY = _IntNode(0, 0, 0, 2,   max_fn=lambda: H - self.Height.Value)
        self.Width   = _IntNode(W, 4, 0, inc, max_fn=lambda: W - self.OffsetX.Value,
                                adjust=width_adjust)
        self.Height  = _IntNode(H, 2, 0, 2,   max_fn=lambda: H - self.OffsetY.Value)
        self.WidthMax  = _types.SimpleNamespace(Value=W)
        self.HeightMax = _types.SimpleNamespace(Value=H)
        for name in ("PixelFormat", "TriggerMode", "TriggerSource", "TriggerDelay",
                     "AcquisitionFrameRateEnable", "AcquisitionFrameRate",
                     "ExposureTime", "Gain", "MaxNumBuffer"):
            setattr(self, name, _types.SimpleNamespace(Value=None))
        self.PixelFormat.Value = "Mono12"
        if center is not None:
            self.CenterX = _types.SimpleNamespace(Value=center)
            self.CenterY = _types.SimpleNamespace(Value=center)
        self._grabbing = grabbing
        self.calls = []

    def IsOpen(self):         return True
    def IsGrabbing(self):     return self._grabbing
    def StopGrabbing(self):   self.calls.append("stop");  self._grabbing = False
    def StartGrabbing(self, strategy): self.calls.append("start"); self._grabbing = True


def _crop_thread(**kw):
    cam = CameraThread()
    cam.pixel_format = "Mono12"
    cam.camera = _CropCamera(**kw)
    warnings = []
    cam.warning.connect(warnings.append)
    return cam, warnings


class TestCameraCrop:

    @pytest.fixture(autouse=True)
    def _fake_genicam(self, monkeypatch):
        # In the full suite another test imports camera.py first, with the real
        # pypylon, so this file's sys.modules fake never reaches it — and the
        # real genicam.IsWritable() rejects these fake nodes. Patch the module
        # camera.py actually uses, for each test.
        monkeypatch.setattr(camera_module, "genicam",
                            _types.SimpleNamespace(IsWritable=lambda node: True))

    def test_steps_and_sensor_are_read_from_the_camera(self):
        cam, _ = _crop_thread(inc=4)
        assert cam.camera_steps() == CameraSteps(4, 2, 4, 2, 4, 2)
        assert cam.sensor_size() == (1936, 1216)

    def test_crop_is_applied_and_read_back(self):
        cam, warnings = _crop_thread()
        applied = cam.set_roi(760, 402, 412, 412)
        assert applied == Crop(760, 402, 412, 412, 1936, 1216)
        assert cam.applied_crop == applied and not applied.is_full_sensor
        assert not warnings

    def test_changing_crop_while_grabbing_restarts_the_camera(self):
        cam, _ = _crop_thread(grabbing=True)
        cam.set_roi(760, 402, 412, 412)
        assert cam.camera.calls == ["stop", "start"]
        assert cam.camera.IsGrabbing()

    def test_a_crop_far_right_works_from_any_previous_crop(self):
        # Offsets go to 0 before the size changes, so OffsetX + Width never
        # exceeds the sensor in between — the fake camera raises if it does.
        cam, warnings = _crop_thread()
        cam.set_roi(1500, 800, 436, 416)            # against the right edge
        assert cam.set_roi(0, 0, 1000, 1000) == Crop(0, 0, 1000, 1000, 1936, 1216)
        assert not warnings

    def test_full_image_goes_back_to_the_whole_sensor(self):
        cam, _ = _crop_thread(grabbing=True)
        cam.set_roi(760, 402, 412, 412)
        applied = cam.clear_roi()
        assert applied.is_full_sensor and cam.roi_position is None
        assert cam.camera.IsGrabbing()

    def test_a_refused_crop_falls_back_to_the_full_sensor_and_warns(self):
        # 413 is off the camera's step of 4. The camera must not be left
        # stopped or half-configured: full sensor, grabbing again, a warning.
        cam, warnings = _crop_thread(grabbing=True)
        applied = cam.set_roi(760, 402, 413, 412)
        assert applied.is_full_sensor and cam.roi_position is None
        assert cam.camera.IsGrabbing()
        assert len(warnings) == 1 and "refused" in warnings[0]

    def test_mask_must_follow_what_the_camera_did_not_what_was_asked(self):
        # A camera that silently rounds Width down to a multiple of 8: the
        # read-back, not the request, is what applied_crop reports (pitfall P4).
        cam, _ = _crop_thread(width_adjust=lambda v: v - v % 8)
        applied = cam.set_roi(760, 402, 412, 412)
        assert applied.width == 408

    @pytest.mark.parametrize("restart", [
        lambda cam: cam.set_trigger(False),        # the dark calibration's switch
        lambda cam: cam.set_trigger(True),         # and back after it
        lambda cam: cam.set_pixel_format("Mono12"),
    ])
    def test_the_crop_survives_every_other_restart(self, restart):
        # The dark calibration turns the trigger off and on again, and each
        # change restarts grabbing. If a restart dropped the crop, the camera
        # would send full frames that the window skips as the wrong size, and
        # the calibration would never finish.
        cam, _ = _crop_thread(grabbing=True)
        cut = cam.set_roi(760, 402, 412, 412)
        restart(cam)
        assert cam.camera.calls == ["stop", "start", "stop", "start"]
        assert cam.applied_crop == cut
        assert (cam.camera.Width.Value, cam.camera.Height.Value) == (412, 412)

    def test_old_camera_centering_switch_is_turned_off(self):
        cam, _ = _crop_thread(center=True)
        cam.set_roi(760, 402, 412, 412)
        assert cam.camera.CenterX.Value is False and cam.camera.CenterY.Value is False

    def test_not_open_yet_only_remembers_the_crop(self):
        cam = CameraThread()
        assert cam.set_roi(760, 402, 412, 412) is None
        assert cam.roi_position == (760, 402, 412, 412)
        assert cam.applied_crop is None


# ---------------------------------------------------------------------------
# Dark calibration at the camera's maximum frame rate (todo U11):
# set_max_frame_rate() — docs/Plan_U11_Dark_Cal_Max_Rate.md, step 2
# ---------------------------------------------------------------------------

def _rate_thread(grabbing=True, trigger="Off", **nodes):
    """A CameraThread on the fake camera, configured as Start Video leaves it:
    20 Hz, trigger as given. `nodes` adds e.g. ResultingFrameRate=118.0."""
    cam, warnings = _crop_thread(grabbing=grabbing)
    cam.frame_rate = 20.0
    cam.exposure_us, cam.gain_db = 8000.0, 8.0
    cam.trigger_mode = trigger
    cam._apply_params()
    for name, value in nodes.items():
        setattr(cam.camera, name, _types.SimpleNamespace(Value=value))
    cam.camera.calls.clear()
    return cam, warnings


def _settings(fake):
    """Everything the dark noise depends on, as the camera holds it."""
    return (fake.ExposureTime.Value, fake.Gain.Value, fake.PixelFormat.Value,
            fake.TriggerMode.Value, fake.OffsetX.Value, fake.OffsetY.Value,
            fake.Width.Value, fake.Height.Value)


class TestMaxFrameRate:

    @pytest.fixture(autouse=True)
    def _fake_genicam(self, monkeypatch):
        # As in TestCameraCrop: patch the genicam camera.py actually uses.
        monkeypatch.setattr(camera_module, "genicam", _types.SimpleNamespace(
            IsWritable=lambda node: True, IsReadable=lambda node: True))

    def test_on_switches_the_limit_off(self):
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        assert cam.camera.AcquisitionFrameRateEnable.Value is False
        assert cam.max_frame_rate is True

    def test_off_writes_the_limit_and_the_rate_back(self):
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        # Whatever the rate node holds now, "back to normal" is written, not
        # assumed (a fresh camera's value is its own default, not ours).
        cam.camera.AcquisitionFrameRate.Value = 160.0
        cam.set_max_frame_rate(False)
        assert cam.camera.AcquisitionFrameRateEnable.Value is True
        assert cam.camera.AcquisitionFrameRate.Value == 20.0

    def test_off_is_written_even_when_not_grabbing(self):
        # A camera keeps the setting until it is powered off: the restore
        # after Stop Video or at close must reach it (plan pitfall P3).
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        cam.camera._grabbing = False
        cam.set_max_frame_rate(False)
        assert cam.camera.AcquisitionFrameRateEnable.Value is True
        assert cam.camera.AcquisitionFrameRate.Value == 20.0

    def test_off_is_written_with_the_trigger_on_too(self):
        # _apply_params() writes neither node with the trigger On, so the
        # restore must do it itself (plan pitfall P1).
        cam, _ = _rate_thread(trigger="On")
        cam.set_max_frame_rate(True)
        cam.set_max_frame_rate(False)
        assert cam.camera.AcquisitionFrameRateEnable.Value is True
        assert cam.camera.AcquisitionFrameRate.Value == 20.0

    def test_nothing_else_changes_and_nothing_restarts(self):
        # The dark noise depends on exposure, gain, pixel format and image
        # area — only the spacing between frames may change.
        cam, _ = _rate_thread()
        cam.set_roi(760, 402, 412, 412)
        cam.camera.calls.clear()
        before = _settings(cam.camera)
        cam.set_max_frame_rate(True)
        assert _settings(cam.camera) == before
        cam.set_max_frame_rate(False)
        assert _settings(cam.camera) == before
        assert cam.camera.calls == [], "switching the rate must not restart grabbing"
        assert cam.camera.IsGrabbing()

    @pytest.mark.parametrize("restart", [
        lambda cam: cam.set_trigger(False),
        lambda cam: cam.set_pixel_format("Mono12"),
        lambda cam: cam.set_roi(760, 402, 412, 412),
    ])
    def test_a_restart_during_the_dark_calibration_keeps_the_maximum(self, restart):
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        restart(cam)
        assert cam.camera.calls == ["stop", "start"]
        assert cam.camera.AcquisitionFrameRateEnable.Value is False

    def test_a_restart_after_it_keeps_the_set_rate(self):
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        cam.set_max_frame_rate(False)
        cam.set_trigger(False)
        assert cam.camera.AcquisitionFrameRateEnable.Value is True
        assert cam.camera.AcquisitionFrameRate.Value == 20.0

    def test_a_new_rate_meanwhile_is_kept_for_later(self):
        # The parameter boxes are locked during the dark calibration, but if a
        # rate arrived anyway it must not switch the limit back on mid-way.
        cam, _ = _rate_thread()
        cam.set_max_frame_rate(True)
        cam.set_frame_rate(25.0)
        assert cam.camera.AcquisitionFrameRateEnable.Value is False
        cam.set_max_frame_rate(False)
        assert cam.camera.AcquisitionFrameRate.Value == 25.0

    def test_not_open_yet_only_remembers(self):
        cam = CameraThread()
        assert cam.set_max_frame_rate(True) is None
        assert cam.max_frame_rate is True

    @pytest.mark.parametrize("nodes, expected", [
        ({"BslResultingAcquisitionFrameRate": 118.3}, 118.3),     # newer cameras
        ({"ResultingFrameRate": 76.0}, 76.0),                     # the rig's a2A1920
        ({}, None),                                               # pylon emulator
    ])
    def test_the_resulting_rate_is_reported_when_the_camera_has_it(self, nodes, expected):
        cam, _ = _rate_thread(**nodes)
        assert cam.set_max_frame_rate(True) == expected
