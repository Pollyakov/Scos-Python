"""
Camera acquisition thread.
Grabs frames from Basler camera continuously and emits them via a Qt signal.
"""

import time
import threading
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from pypylon import pylon, genicam


class CameraThread(QThread):
    frame_ready   = pyqtSignal(np.ndarray)   # emitted for SCOS (every frame)
    display_ready = pyqtSignal(np.ndarray)   # emitted for display (capped at 30 FPS)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    DISPLAY_FPS_CAP = 30.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running      = False
        self.camera        = None
        self._last_display = 0.0          # timestamp of last display emit
        self._display_interval = 1.0 / self.DISPLAY_FPS_CAP

        # camera parameters (applied before next start)
        self.pixel_format  = "Mono12"
        self.exposure_us   = 10000.0   # microseconds
        self.gain_db       = 0.0
        self.frame_rate    = 50.0
        self.trigger_mode  = "Off"     # "Off" = internal, "On" = hardware
        self.trigger_delay = 0.0       # microseconds
        self.roi_position  = None      # (x, y, w, h) or None for full frame

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self):
        """Open the first available Basler camera."""
        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler camera found. Check cable / Pylon SDK.")
        self.camera = pylon.InstantCamera(factory.CreateFirstDevice())
        self.camera.Open()

    def close(self):
        self.stop()
        if self.camera and self.camera.IsOpen():
            self.camera.Close()

    def start_capture(self):
        if not self.camera or not self.camera.IsOpen():
            self.open()
        self._apply_params()
        self._running = True
        self.start()   # starts QThread.run()

    def stop(self):
        self._running = False
        self.wait()

    def set_exposure(self, us: float):
        self.exposure_us = us
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.ExposureTime.Value = us

    def set_gain(self, db: float):
        self.gain_db = db
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.Gain.Value = db

    def set_frame_rate(self, hz: float):
        self.frame_rate = hz
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.AcquisitionFrameRateEnable.Value = True
            self.camera.AcquisitionFrameRate.Value = hz

    def set_trigger(self, enabled: bool, delay_us: float = 0.0):
        self.trigger_mode  = "On" if enabled else "Off"
        self.trigger_delay = delay_us
        # Full restart needed to change trigger mode
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def set_pixel_format(self, fmt: str):
        """fmt: 'Mono8', 'Mono10', or 'Mono12'"""
        self.pixel_format = fmt
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def set_roi(self, x: int, y: int, w: int, h: int):
        self.roi_position = (x, y, w, h)
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def get_info(self) -> dict:
        if not self.camera or not self.camera.IsOpen():
            return {}
        return {
            "model":        self.camera.GetDeviceInfo().GetModelName(),
            "serial":       self.camera.GetDeviceInfo().GetSerialNumber(),
            "exposure_us":  self.camera.ExposureTime.Value,
            "gain_db":      self.camera.Gain.Value,
            "frame_rate":   self.camera.ResultingFrameRate.Value,
            "pixel_format": self.camera.PixelFormat.Value,
            "width":        self.camera.Width.Value,
            "height":       self.camera.Height.Value,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_params(self):
        cam = self.camera

        # Pixel format
        if genicam.IsWritable(cam.PixelFormat):
            try:
                cam.PixelFormat.Value = self.pixel_format
            except Exception:
                pass  # unsupported format on this camera, keep current
        actual_fmt = cam.PixelFormat.Value
        if actual_fmt != self.pixel_format:
            self.warning.emit(
                f"Requested pixel format '{self.pixel_format}' but camera is using '{actual_fmt}'"
            )

        # ROI  (must be set before exposure / frame-rate)
        if self.roi_position:
            x, y, w, h = self.roi_position
            # align to camera increment requirements
            cam.OffsetX.Value = 0
            cam.OffsetY.Value = 0
            cam.Width.Value   = w
            cam.Height.Value  = h
            cam.OffsetX.Value = x
            cam.OffsetY.Value = y
        else:
            cam.OffsetX.Value = 0
            cam.OffsetY.Value = 0
            cam.Width.Value   = cam.Width.Max
            cam.Height.Value  = cam.Height.Max

        # Trigger
        cam.TriggerMode.Value = self.trigger_mode
        if self.trigger_mode == "On":
            cam.TriggerSource.Value = "Line2"
            cam.TriggerDelay.Value  = self.trigger_delay
        else:
            cam.AcquisitionFrameRateEnable.Value = True
            cam.AcquisitionFrameRate.Value       = self.frame_rate

        # Exposure & gain
        cam.ExposureTime.Value = self.exposure_us
        cam.Gain.Value         = self.gain_db

    def run(self):
        """Main acquisition loop — runs in a separate thread."""
        try:
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            while self._running:
                if self.camera.IsGrabbing():
                    result = self.camera.RetrieveResult(
                        2000, pylon.TimeoutHandling_ThrowException
                    )
                    if result.GrabSucceeded():
                        frame = result.Array.copy()
                        self.frame_ready.emit(frame)   # always — for SCOS
                        now = time.monotonic()
                        if now - self._last_display >= self._display_interval:
                            self.display_ready.emit(frame)   # capped — for GUI
                            self._last_display = now
                    result.Release()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if self.camera.IsGrabbing():
                self.camera.StopGrabbing()
