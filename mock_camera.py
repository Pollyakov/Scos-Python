"""
Mock camera thread that replays a TIFF stack in place of a live Basler camera.
Drop-in replacement for CameraThread — identical signals and public API.
Usage: python main.py --mock-tiff path/to/stack.tif
"""

import time
import numpy as np
import tifffile
from PyQt6.QtCore import QThread, pyqtSignal

from core.crop import CameraSteps, Crop, PlaybackCrop, playback_max_rate


class MockCameraThread(QThread):
    frame_ready   = pyqtSignal(np.ndarray, float)
   # (frame, t_capture)
    # t_capture is time.monotonic() taken where the frame is *captured*.
    # The wall clock can jump mid-recording if the OS syncs time; and a
    # timestamp taken later, on the GUI thread, records GUI scheduling
    # jitter as if it were physiology.
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    DISPLAY_FPS_CAP = 30.0

    # No physical camera behind these frames, so there is no serial number to
    # look up in CamerasMeasuredGain.csv. MainWindow checks this flag before
    # refusing a run: the synthetic source is allowed to fall back to the
    # convert_gain() formula, with a dialog saying so.
    is_synthetic = True

    def __init__(self, tiff_path: str, loop: bool = True, parent=None):
        super().__init__(parent)
        self.tiff_path  = tiff_path
        self._loop      = loop
        self._running   = False
        self._stack: np.ndarray | None = None
        # Frames handed to Qt so far. MainWindow compares it with its own
        # arrival count to tell how far behind it is — see
        # _flush_stale_frames(), which uses that to discard frames captured
        # before the operator changed the lighting.
        self.frames_emitted = 0
        # Playback has no camera clock to check, so a measurement never
        # waits for one (CameraThread.clock_settled, todo D10).
        self.clock_settled = True
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
        # The dark calibration's maximum rate (todo U11), set_max_frame_rate().
        self.max_frame_rate = False
        # "Cut Image" crops the frames here, as the camera would (todo U6).
        self._crop = PlaybackCrop()
        self.applied_crop: Crop | None = None

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
            self._reapply_roi()

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

    # Store-only setters — TIFF content is fixed
    def set_exposure(self, us: float) -> None:
        self.exposure_us = us

    def set_gain(self, db: float) -> None:
        self.gain_db = db

    def set_pixel_format(self, fmt: str) -> None:
        self.pixel_format = fmt

    def set_trigger(self, enabled: bool, delay_us: float = 0.0) -> None:
        self.trigger_mode  = "On" if enabled else "Off"
        self.trigger_delay = delay_us

    def set_max_frame_rate(self, enabled: bool) -> float:
        """Play as fast as a camera without its frame-rate limit could
        deliver these frames — the dark calibration's maximum rate (todo U11;
        see CameraThread.set_max_frame_rate()): one frame per exposure, capped
        by USB bandwidth, core.crop.playback_max_rate(), as the rig camera is.
        At 1 / exposure alone the lab's dark folder played at ~146 fps, faster
        than any camera sends full frames. Even capped, playback cannot show the
        speed-up: here the GUI thread is the limit, and a faster playback thread
        (a TIFF decode per frame) takes processor time from it — 600 dark frames
        took 38.9 s at the capped maximum against 32.4 s at the set rate, back
        to back on 2026-10-12. Returns the rate playback now aims for."""
        self.max_frame_rate = enabled
        return self._playback_rate()

    def _playback_rate(self) -> float:
        if self.max_frame_rate:
            return playback_max_rate(self.exposure_us, self.applied_crop,
                                     self.pixel_format)
        return self.frame_rate

    def set_roi(self, x: int, y: int, w: int, h: int) -> Crop | None:
        """Cut every frame to this rectangle from now on — see
        CameraThread.set_roi(). None if not open yet."""
        self.roi_position = (x, y, w, h)
        return self._reapply_roi()

    def clear_roi(self) -> Crop | None:
        self.roi_position = None
        return self._reapply_roi()

    def camera_steps(self) -> CameraSteps:
        return PlaybackCrop.STEPS

    def sensor_size(self) -> tuple[int, int] | None:
        """(width, height) of the stack's frames — the "sensor" here."""
        if self._stack is None:
            return None
        return int(self._stack.shape[2]), int(self._stack.shape[1])

    def _reapply_roi(self) -> Crop | None:
        if self._stack is None:
            return None
        self.applied_crop, refused = self._crop.apply(self.roi_position, *self.sensor_size())
        if refused:
            self.roi_position = None
            self.warning.emit(refused)
        return self.applied_crop

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
                target_dt = 1.0 / max(self._playback_rate(), 1e-3)
                t0 = time.perf_counter()

                frame = self._crop.cut(self._stack[idx])
                t_capture = time.monotonic()
                self.frames_emitted += 1
                self.frame_ready.emit(frame, t_capture)

                now = t_capture
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
