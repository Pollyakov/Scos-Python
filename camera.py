"""
Camera acquisition thread.
Grabs frames from Basler camera continuously and emits them via a Qt signal.
"""

import logging
import time
import threading
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from pypylon import pylon, genicam

from core.crop import CameraSteps, Crop
from core.frame_clock import FrameClock

logger = logging.getLogger(__name__)


def _result_int(result, name: str) -> int:
    """A grab result's TimeStamp / BlockID, or 0 if this camera has none.
    Never raises: the grab loop must not die over a missing chunk of data."""
    try:
        return int(getattr(result, name))
    except Exception:
        return 0


class CameraThread(QThread):
    frame_ready   = pyqtSignal(np.ndarray, float)
   # (frame, t_capture)
    # t_capture is on the time.monotonic() scale and marks when the frame was
    # *exposed*: the camera's own timestamp once FrameClock has checked it
    # against the PC clock, else the PC time right after RetrieveResult().
    # The wall clock can jump mid-recording if the OS syncs time; and a
    # timestamp taken later, on the GUI thread, records GUI scheduling
    # jitter as if it were physiology.
    display_ready = pyqtSignal(np.ndarray)   # emitted for display (capped at 30 FPS)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    DISPLAY_FPS_CAP = 30.0

    # Only a real camera's settings are worth remembering between launches.
    # Playback sources read their recording's exposure/gain/fps back into the
    # GUI, and saving those would start the next rig session with a
    # recording's settings. MainWindow._save_config() checks for this marker
    # positively, so a new frame source never starts writing by accident.
    persists_settings = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running      = False
        self.camera        = None
        # Frames handed to Qt so far. MainWindow compares it with its own
        # arrival count to tell how far behind it is — see
        # _flush_stale_frames(), which uses that to discard frames captured
        # before the operator changed the lighting.
        self.frames_emitted = 0
        # Capture times and lost-frame counting from the grab results' own
        # TimeStamp and BlockID — see core/frame_clock.py. A fresh one per
        # Start Video (run()).
        self._clock: FrameClock | None = None
        self._lost_unreported = 0         # lost frames not yet in a warning
        self._last_lost_warning = 0.0
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
        # The image area the camera is actually using, read back from it after
        # every _apply_params() — the camera may adjust what it was asked for,
        # so the ROI mask must be built from this, never from roi_position
        # (todo U6, plan pitfall P4). None until the camera is configured.
        self.applied_crop: Crop | None = None
        # True during the dark calibration: the camera runs as fast as it can
        # instead of at frame_rate (todo U11, set_max_frame_rate()).
        self.max_frame_rate = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self):
        """Open the first available Basler camera."""
        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            logger.error("No Basler camera found")
            raise RuntimeError("No Basler camera found. Check cable / Pylon SDK.")
        self.camera = pylon.InstantCamera(factory.CreateFirstDevice())
        self.camera.Open()
        info = self.camera.GetDeviceInfo()
        logger.info("Camera opened — model=%s  SN=%s",
                    info.GetModelName(), info.GetSerialNumber())

    def close(self):
        self.stop()
        if self.camera and self.camera.IsOpen():
            self.camera.Close()

    def start_capture(self):
        if not self.camera or not self.camera.IsOpen():
            self.open()
        self._apply_params()
        logger.info(
            "Capture starting — format=%s  exposure=%.1f µs  gain=%.1f dB  "
            "fps=%.1f Hz  trigger=%s",
            self.pixel_format, self.exposure_us, self.gain_db,
            self.frame_rate, self.trigger_mode,
        )
        self._running = True
        self.start()   # starts QThread.run()

    def stop(self):
        logger.info("Camera capture stopping")
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
        if self.max_frame_rate:
            return      # stored; set_max_frame_rate(False) writes it
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self.camera.AcquisitionFrameRateEnable.Value = True
            self.camera.AcquisitionFrameRate.Value = hz

    def set_max_frame_rate(self, enabled: bool) -> float | None:
        """Let the camera run as fast as it can, or put frame_rate back.

        For the dark calibration (todo U11, Vika 2026-10-08): the spacing
        between dark frames does not matter, so the camera's frame-rate limit
        is switched off and it runs as fast as exposure, readout and USB
        bandwidth allow. Exposure, gain, pixel format and image area are not
        touched — the dark noise depends on them.

        `False` writes the limit back on *and* the rate, explicitly, whatever
        the trigger mode: "back to normal" must be a written value, never an
        assumption, because _apply_params() writes neither node with the
        trigger On (plan pitfall P1). Writes whenever the camera is open,
        grabbing or not — a camera keeps the setting until it is powered off
        (P3). No restart: both nodes can be written while grabbing (verified
        on the pylon emulator, plan step 1).

        Returns the rate the camera reports, or None if it is not open or
        reports none (the pylon emulator has no such node).
        """
        self.max_frame_rate = enabled
        if not (self.camera and self.camera.IsOpen()):
            return None
        self._write_frame_rate()
        rate = self.resulting_frame_rate()
        rate_txt = (f"camera reports {rate:.1f} fps" if rate is not None
                    else "camera does not report its rate")
        if enabled:
            cam, c = self.camera, self.applied_crop
            area = (f"{c.width} × {c.height} at ({c.offset_x}, {c.offset_y})"
                    if c is not None else "image area unknown")
            logger.info(
                "Frame-rate limit off — camera at its maximum (%s; exposure "
                "%.1f µs, gain %.2f dB, %s, %s; %s)",
                rate_txt, cam.ExposureTime.Value, cam.Gain.Value,
                cam.PixelFormat.Value, area, self._throughput_limit())
        else:
            logger.info("Frame-rate limit back on — %.1f Hz (%s)",
                        self.frame_rate, rate_txt)
        return rate

    def resulting_frame_rate(self) -> float | None:
        """The frame rate the camera says it will deliver, or None. Newer
        Basler cameras call it BslResultingAcquisitionFrameRate, older ones
        ResultingFrameRate (the rig's a2A1920 answers to the latter); the
        pylon emulator has neither."""
        for name in ("BslResultingAcquisitionFrameRate", "ResultingFrameRate"):
            try:
                node = getattr(self.camera, name)
                if genicam.IsReadable(node):
                    return float(node.Value)
            except Exception:
                pass
        return None

    def _throughput_limit(self) -> str:
        """The USB bandwidth cap, for the log: when On it also caps the
        maximum frame rate (Basler, DeviceLinkThroughputLimitMode). Read
        only — U11 does not change it."""
        try:
            mode = self.camera.DeviceLinkThroughputLimitMode.Value
            if mode == "On":
                limit = self.camera.DeviceLinkThroughputLimit.Value
                return f"USB throughput limit On, {limit / 1e6:.0f} MB/s"
            return f"USB throughput limit {mode}"
        except Exception:
            return "USB throughput limit not reported"

    def _write_frame_rate(self) -> None:
        cam = self.camera
        if self.max_frame_rate:
            cam.AcquisitionFrameRateEnable.Value = False
        else:
            cam.AcquisitionFrameRateEnable.Value = True
            cam.AcquisitionFrameRate.Value       = self.frame_rate

    def set_trigger(self, enabled: bool, delay_us: float = 0.0):
        self.trigger_mode  = "On" if enabled else "Off"
        self.trigger_delay = delay_us
        logger.info("Trigger mode → %s  delay=%.0f µs", self.trigger_mode, delay_us)
        # Full restart needed to change trigger mode
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self._restart_clock()
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.MaxNumBuffer.Value = 20
            self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            self._restart_clock()

    def set_pixel_format(self, fmt: str):
        """fmt: 'Mono8', 'Mono10', or 'Mono12'"""
        logger.info("Pixel format → %s", fmt)
        self.pixel_format = fmt
        if self.camera and self.camera.IsOpen() and self.camera.IsGrabbing():
            self._restart_clock()
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.MaxNumBuffer.Value = 20
            self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            self._restart_clock()

    def set_roi(self, x: int, y: int, w: int, h: int) -> Crop | None:
        """Make the camera send only this rectangle of the sensor ("Cut
        Image", todo U6). x, y, w, h must already obey camera_steps() — see
        core.crop.crop_for_circle(). Returns the crop the camera actually uses
        (applied_crop), or None if the camera is not open yet (then it is
        applied at the next Start Video). If the camera rejects it, the full
        sensor is used instead and `warning` says so."""
        self.roi_position = (x, y, w, h)
        return self._reapply_roi()

    def clear_roi(self) -> Crop | None:
        """Back to the full sensor ("Full Image"). Returns applied_crop."""
        self.roi_position = None
        return self._reapply_roi()

    def camera_steps(self) -> CameraSteps:
        """The camera's rules for the image area, read from the camera itself
        — never assumed: the pylon emulator allows steps of 1, a real camera
        may need e.g. multiples of 4."""
        cam = self.camera
        return CameraSteps(
            offset_x_inc=int(cam.OffsetX.Inc), offset_y_inc=int(cam.OffsetY.Inc),
            width_inc=int(cam.Width.Inc),      height_inc=int(cam.Height.Inc),
            width_min=int(cam.Width.Min),      height_min=int(cam.Height.Min),
        )

    def sensor_size(self) -> tuple[int, int]:
        """(width, height) of the whole usable sensor, whatever crop is set."""
        return int(self.camera.WidthMax.Value), int(self.camera.HeightMax.Value)

    def _reapply_roi(self) -> Crop | None:
        if not (self.camera and self.camera.IsOpen()):
            return None
        if self.camera.IsGrabbing():
            # The image size can only change while the camera is not
            # grabbing — same restart as set_trigger() / set_pixel_format().
            self._restart_clock()
            self.camera.StopGrabbing()
            self._apply_params()
            self.camera.MaxNumBuffer.Value = 20
            self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            self._restart_clock()
        else:
            self._apply_params()
        return self.applied_crop

    @property
    def frames_lost(self) -> int:
        """Frames the camera exposed that never reached the app, since Start
        Video: gaps in the BlockID sequence plus failed grabs. Under
        GrabStrategy_OneByOne Pylon itself counts none of these."""
        return self._clock.frames_lost if self._clock else 0

    @property
    def time_source(self) -> str:
        """"camera" once the camera's timestamps are in use, else "pc"."""
        return self._clock.time_source if self._clock else "pc"

    @property
    def clock_settled(self) -> bool:
        """True once the camera-clock check since the last restart has ended
        (core/frame_clock.py `settled`). The measurement keeps no frame before
        that (todo D10). Meaningful on the camera thread, in a frame_ready
        slot connected directly — that is where RealtimePipeline reads it."""
        return self._clock.settled if self._clock else True

    def _restart_clock(self) -> None:
        # Grabbing restarts: frame numbers may start again at 1 and the
        # camera clock is re-checked. Thread-safe (one flag write). Called
        # before StopGrabbing and again after StartGrabbing, so whichever frame
        # the camera thread retrieves first — a late one from the old run or
        # the first of the new — the gap across the restart is never counted
        # as lost frames.
        if self._clock is not None:
            self._clock.request_reset()

    def _tick_frequency(self) -> float | None:
        """The camera's timestamp tick rate if it reports one (GigE ace
        classic: GevTimestampTickFrequency); None lets FrameClock try the
        known Basler rates and accept only one the PC clock confirms."""
        try:
            node = self.camera.GevTimestampTickFrequency
            if genicam.IsReadable(node):
                return float(node.Value)
        except Exception:
            pass
        return None

    def _note_lost(self, n: int) -> None:
        """Count n lost frames; warn at most once a second so a sustained
        overload does not flood the status bar and app.log."""
        self._lost_unreported += n
        now = time.monotonic()
        if now - self._last_lost_warning >= 1.0:
            msg = (f"Camera: {self._lost_unreported} frame(s) lost — Pylon "
                   f"buffers full or transfer failed ({self.frames_lost} since "
                   f"Start Video)")
            logger.warning(msg)
            self.warning.emit(msg)
            self._lost_unreported = 0
            self._last_lost_warning = now

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
        self._apply_roi()

        # Trigger
        cam.TriggerMode.Value = self.trigger_mode
        if self.trigger_mode == "On":
            cam.TriggerSource.Value = "Line2"
            cam.TriggerDelay.Value  = self.trigger_delay
        else:
            # Honours max_frame_rate, so a restart during the dark calibration
            # keeps the maximum rate (todo U11).
            self._write_frame_rate()

        # Exposure & gain
        cam.ExposureTime.Value = self.exposure_us
        cam.Gain.Value         = self.gain_db

    def _apply_roi(self) -> None:
        """Write roi_position (None = full sensor) to the camera, then read
        back what it actually uses into applied_crop. Never raises: a crop the
        camera refuses falls back to the full sensor with a warning, so a bad
        value can never leave the camera stopped half-configured."""
        cam = self.camera
        self._disable_auto_centering()
        if self.roi_position:
            x, y, w, h = self.roi_position
            try:
                # Offsets to 0 first: Width + OffsetX may not exceed the
                # sensor at any moment, whatever the previous crop was.
                cam.OffsetX.Value = 0
                cam.OffsetY.Value = 0
                cam.Width.Value   = w
                cam.Height.Value  = h
                cam.OffsetX.Value = x
                cam.OffsetY.Value = y
            except Exception as exc:
                msg = (f"Camera refused the image crop {w} × {h} at ({x}, {y}) — "
                       f"using the full sensor ({exc})")
                logger.exception(msg)
                self.warning.emit(msg)
                self.roi_position = None
        if not self.roi_position:
            cam.OffsetX.Value = 0
            cam.OffsetY.Value = 0
            cam.Width.Value   = cam.Width.Max
            cam.Height.Value  = cam.Height.Max
        sensor_w, sensor_h = self.sensor_size()
        self.applied_crop = Crop(
            int(cam.OffsetX.Value), int(cam.OffsetY.Value),
            int(cam.Width.Value),   int(cam.Height.Value), sensor_w, sensor_h)
        c = self.applied_crop
        logger.info("Camera image area: %d × %d at (%d, %d) of the %d × %d sensor%s",
                    c.width, c.height, c.offset_x, c.offset_y, sensor_w, sensor_h,
                    " (full sensor)" if c.is_full_sensor else "")

    def _disable_auto_centering(self) -> None:
        """Older Basler cameras (ace Classic/U/L) have CenterX/CenterY
        switches; while on, the offsets cannot be written. Basler: "set the
        CenterX and CenterY parameters to false". Newer cameras (ace 2, like
        the rig's a2A1920) have BslCenterX/Y instead — a one-time *command*
        that centres the area, not a mode — which is left alone."""
        for name in ("CenterX", "CenterY"):
            try:
                node = getattr(self.camera, name)
                if genicam.IsWritable(node) and node.Value is True:
                    node.Value = False
                    logger.info("Camera %s switched off so the crop offsets apply", name)
            except Exception:
                pass   # no such switch on this camera (or not a boolean)

    # How many consecutive 2-second timeouts before we declare a real error.
    # 5 × 2 s = 10 s grace period — survives a brief Arduino reset/reboot.
    _MAX_CONSECUTIVE_TIMEOUTS = 5

    def run(self):
        """Main acquisition loop — runs in a separate thread."""
        consecutive_timeouts = 0
        self._clock = FrameClock(self._tick_frequency())
        self._lost_unreported = 0
        try:
            self.camera.MaxNumBuffer.Value = 20
            self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            while self._running:
                if self.camera.IsGrabbing():
                    result = self.camera.RetrieveResult(
                        2000, pylon.TimeoutHandling_Return
                    )
                    if not result.IsValid():
                        # Timeout — no frame arrived within 2 s
                        consecutive_timeouts += 1
                        if consecutive_timeouts >= self._MAX_CONSECUTIVE_TIMEOUTS:
                            msg = (
                                f"No frames received for "
                                f"{consecutive_timeouts * 2} s — "
                                "camera stopped or trigger lost."
                            )
                            logger.error(msg)
                            self.error.emit(msg)
                            break
                        continue
                    consecutive_timeouts = 0
                    # PC time first, before the copy and any signal delivery —
                    # it is the capture time until the camera clock is accepted.
                    t_retrieved = time.monotonic()
                    if result.GrabSucceeded():
                        stamp = self._clock.stamp(
                            t_retrieved,
                            _result_int(result, "TimeStamp"),
                            _result_int(result, "BlockID"),
                        )
                        if stamp.lost_before:
                            self._note_lost(stamp.lost_before)
                        frame = result.Array.copy()
                        self.frames_emitted += 1
                        self.frame_ready.emit(frame, stamp.t_capture)   # always — for SCOS
                        if t_retrieved - self._last_display >= self._display_interval:
                            self.display_ready.emit(frame)   # capped — for GUI
                            self._last_display = t_retrieved
                    else:
                        # Exposed but incomplete (e.g. GigE packets lost): a
                        # frame that never reaches timeVec. Counted here only
                        # if the next frame's BlockID gap will not count it.
                        if self._clock.count_failed_grab():
                            self._note_lost(1)
                    result.Release()
        except Exception as e:
            logger.exception("Camera acquisition error: %s", e)
            self.error.emit(str(e))
        finally:
            if self.camera.IsGrabbing():
                self.camera.StopGrabbing()
            logger.info("Camera acquisition loop ended")
