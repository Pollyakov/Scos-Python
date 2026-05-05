"""
Mock camera thread that replays a TIFF stack in place of a live Basler camera.
Drop-in replacement for CameraThread — identical signals and public API.
Usage: python main.py --mock-tiff path/to/stack.tif
"""

import time
import numpy as np
import tifffile
from PyQt6.QtCore import QThread, pyqtSignal


class MockCameraThread(QThread):
    frame_ready   = pyqtSignal(np.ndarray)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    DISPLAY_FPS_CAP = 30.0

    def __init__(self, tiff_path: str, loop: bool = True, parent=None):
        super().__init__(parent)
        self.tiff_path  = tiff_path
        self._loop      = loop
        self._running   = False
        self._stack: np.ndarray | None = None
        self._last_display    = 0.0
        self._display_interval = 1.0 / self.DISPLAY_FPS_CAP

        # Mirror CameraThread attribute names so MainWindow direct-writes work
        self.pixel_format  = "Mono12"
        self.exposure_us   = 10000.0
        self.gain_db       = 0.0
        self.frame_rate    = 50.0
        self.trigger_mode  = "Off"
        self.trigger_delay = 0.0
        self.roi_position  = None

    # ------------------------------------------------------------------
    # Public API — mirrors CameraThread exactly
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Load TIFF stack into RAM. Idempotent."""
        if self._stack is None:
            stack = tifffile.imread(self.tiff_path)
            if stack.ndim == 2:              # single-page TIFF → add frame axis
                stack = stack[np.newaxis, ...]
            self._stack = stack              # shape (N, H, W)

    def close(self) -> None:
        self.stop()
        self._stack = None

    def start_capture(self) -> None:
        if self._stack is None:
            self.open()
        self._running = True
        self.start()

    def stop(self) -> None:
        self._running = False
        self.wait()

    # Functional: changing FPS actually changes playback speed (re-read each iter)
    def set_frame_rate(self, hz: float) -> None:
        self.frame_rate = hz

    # Store-only setters — TIFF content is fixed, ROI handled by GUI mask
    def set_exposure(self, us: float) -> None:
        self.exposure_us = us

    def set_gain(self, db: float) -> None:
        self.gain_db = db

    def set_pixel_format(self, fmt: str) -> None:
        self.pixel_format = fmt

    def set_trigger(self, enabled: bool, delay_us: float = 0.0) -> None:
        self.trigger_mode  = "On" if enabled else "Off"
        self.trigger_delay = delay_us

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        self.roi_position = (x, y, w, h)

    def get_info(self) -> dict:
        h = self._stack.shape[1] if self._stack is not None else 0
        w = self._stack.shape[2] if self._stack is not None else 0
        return {
            "model":        "MockTIFF",
            "serial":       self.tiff_path,
            "exposure_us":  self.exposure_us,
            "gain_db":      self.gain_db,
            "frame_rate":   self.frame_rate,
            "pixel_format": self.pixel_format,
            "width":        w,
            "height":       h,
        }

    # ------------------------------------------------------------------
    # Playback loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        idx      = 0
        n_frames = len(self._stack)
        try:
            while self._running:
                target_dt = 1.0 / max(self.frame_rate, 1e-3)
                t0 = time.perf_counter()

                frame = self._stack[idx]
                self.frame_ready.emit(frame)

                now = time.monotonic()
                if now - self._last_display >= self._display_interval:
                    self.display_ready.emit(frame)
                    self._last_display = now

                idx += 1
                if idx >= n_frames:
                    if self._loop:
                        idx = 0
                    else:
                        break

                sleep_s = target_dt - (time.perf_counter() - t0)
                if sleep_s > 0:
                    self.msleep(int(sleep_s * 1000))
        except Exception as e:
            self.error.emit(str(e))
