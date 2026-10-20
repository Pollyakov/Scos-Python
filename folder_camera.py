"""
Mock camera that replays a folder of per-frame TIFF files in place of a live Basler.

Mirrors CameraThread's public signals and methods so MainWindow needs no rewiring.
Reads one TIFF file per playback iteration (lazy load) to avoid loading 2+ GB into RAM.

Usage:
    python main.py --mock-folder "path/to/expT5ms_Gain24dB_BL100DU_FR40Hz_005"

The dark folder and calibration .mat files are auto-detected from the recording folder.
"""

import re
import time
from pathlib import Path

import numpy as np
import tifffile
from PyQt6.QtCore import QThread, pyqtSignal

from core.crop import CameraSteps, Crop, PlaybackCrop, playback_max_rate

def _sort_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(
        files,
        key=lambda p: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", p.name)) else 0,
    )


def find_dark_dir(recording_dir: Path) -> Path | None:
    """Auto-detect the dark calibration folder next to the recording folder.

    Handles Pylon's double-nesting: the outer folder ends in '_dark' and
    contains one same-named subfolder where the TIFFs actually live.
    """
    candidate = recording_dir.parent / (recording_dir.name + "_dark")
    if not candidate.exists():
        return None
    nested = candidate / candidate.name
    return nested if (nested.exists() and nested.is_dir()) else candidate


class FolderMockCamera(QThread):
    """Replays a per-frame TIFF recording folder as a live camera stream."""

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

    def __init__(self, recording_dir: str | Path, loop: bool = True, parent=None):
        super().__init__(parent)
        self._recording_dir = Path(recording_dir)
        self._loop          = loop
        self._running       = False
        self._tiff_files: list[Path] = []
        # Dark calibration cannot be rehearsed by switching off a laser that
        # does not exist, so the dark folder is played instead while the app
        # is in DARK_CAL. See set_playback_source() and todo D6.
        self._dark_files: list[Path] = []
        self._source          = "main"
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
        self._recording_params: dict = {}

        # Mirror CameraThread attribute names (MainWindow writes these directly)
        self.pixel_format  = "Mono10"
        self.exposure_us   = 5000.0
        self.gain_db       = 24.0
        self.frame_rate    = 40.0
        self.trigger_mode  = "Off"
        self.trigger_delay = 0.0
        self.roi_position  = None
        # The dark calibration's maximum rate (todo U11), set_max_frame_rate().
        self.max_frame_rate = False
        # "Cut Image" crops the frames here, as the camera would (todo U6).
        self._crop = PlaybackCrop()
        self._sensor: tuple[int, int] | None = None   # (width, height) of the TIFFs
        self.applied_crop: Crop | None = None
        # No camera: the black level is the one in the recording's folder name,
        # if any (open(); CameraThread.black_level reads the camera's).
        self.black_level: float | None = None

    # ------------------------------------------------------------------
    # Public API — mirrors CameraThread exactly
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Discover TIFFs and parse recording parameters. Idempotent."""
        if self._tiff_files:
            return
        self._tiff_files = _sort_tiffs(self._recording_dir)
        if not self._tiff_files:
            raise FileNotFoundError(
                f"No TIFF files found in {self._recording_dir}"
            )
        first = tifffile.imread(str(self._tiff_files[0]))
        self._recording_params = self._parse_recording_params(first)
        h, w = first.shape[:2]
        self._sensor = (w, h)
        self._reapply_roi()

        dark_dir = find_dark_dir(self._recording_dir)
        self._dark_files = _sort_tiffs(dark_dir) if dark_dir is not None else []

        p = self._recording_params
        self.exposure_us  = p.get("exposure_us",  self.exposure_us)
        self.gain_db      = p.get("gain_db",       self.gain_db)
        self.frame_rate   = p.get("frame_rate",    self.frame_rate)
        self.pixel_format = p.get("pixel_format",  self.pixel_format)
        # The black level the recording's name declares ("BL100DU"), reported
        # like the camera's own. It matters for frames saved from playback:
        # the MATLAB script subtracts the folder name's BL from the frames
        # before fitting the mean image, so a wrong one ruins its κ² — a
        # playback session saved with BL0DU gave κ²_raw ≈ 2800 (2026-10-21).
        self.black_level  = p.get("black_level",   self.black_level)

    def close(self) -> None:
        self.stop()
        self._tiff_files = []

    def start_capture(self) -> None:
        if not self._tiff_files:
            self.open()
        self._running = True
        self.start()

    def stop(self) -> None:
        self._running = False
        self.wait()

    def set_frame_rate(self, hz: float) -> None:
        self.frame_rate = hz            # re-read every iteration → changes playback speed

    def set_exposure(self, us: float) -> None:
        self.exposure_us = us           # store-only; TIFF brightness is fixed

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
        """Cut every frame to this rectangle from now on, recording and dark
        folder alike — see CameraThread.set_roi(). None if not open yet."""
        self.roi_position = (x, y, w, h)
        return self._reapply_roi()

    def clear_roi(self) -> Crop | None:
        self.roi_position = None
        return self._reapply_roi()

    def camera_steps(self) -> CameraSteps:
        return PlaybackCrop.STEPS

    def sensor_size(self) -> tuple[int, int]:
        """(width, height) of the recorded frames — the "sensor" here."""
        return self._sensor

    def _reapply_roi(self) -> Crop | None:
        if self._sensor is None:
            return None
        self.applied_crop, refused = self._crop.apply(self.roi_position, *self._sensor)
        if refused:
            self.roi_position = None
            self.warning.emit(refused)
        return self.applied_crop

    def get_info(self) -> dict:
        h = w = 0
        if self._tiff_files:
            sample = tifffile.imread(str(self._tiff_files[0]))
            h, w = sample.shape[:2]
        p = self._recording_params
        return {
            "model":        p.get("model",    "FolderMock"),
            "serial":       p.get("serial",   ""),
            "exposure_us":  self.exposure_us,
            "gain_db":      self.gain_db,
            "frame_rate":   self.frame_rate,
            "pixel_format": self.pixel_format,
            "width":        w,
            "height":       h,
            "bit_depth":    p.get("bit_depth", 10),
            # What a raw frame is divided by to get DU (see _pixel_layout).
            "scale":        p.get("scale", 64.0),
        }

    # ------------------------------------------------------------------
    # Calibration helpers (no equivalent on CameraThread)
    # ------------------------------------------------------------------

    def set_playback_source(self, source: str) -> bool:
        """Play the dark folder ("dark") or the recording ("main").

        A real session calibrates by asking the operator to switch the laser
        off, collecting N frames, and switching it back on. Playback has no
        laser, so before this existed those "dark" frames were the laser-on
        recording: `dark_var` came out carrying the live signal's variance and
        every corrected kappa^2 went negative (todo D6, Done item 22).

        Switching the file list instead makes the rehearsal cover the real
        code — the prompts, the collectors, the folder dialog and the
        calibration file all run exactly as they will on the rig — with frames
        that are genuinely dark.

        Returns False, and warns, when there is no dark folder to switch to;
        playback stays on the recording so the run is not left frameless.
        """
        if source not in ("main", "dark"):
            raise ValueError(f"unknown playback source {source!r}")
        if source == "dark" and not self._dark_files:
            self.warning.emit(
                "No dark folder next to this recording — dark calibration will "
                "collect laser-on frames and the corrected \u03ba\u00b2 will be "
                "negative. Expected a sibling folder named "
                f"'{self._recording_dir.name}_dark'."
            )
            return False
        self._source = source
        return True

    @property
    def playback_source(self) -> str:
        return self._source

    def get_dark_dir(self) -> Path | None:
        """Auto-detect dark folder next to the recording directory."""
        return find_dark_dir(self._recording_dir)

    def get_calibration_mat(self) -> Path | None:
        """Return path to smoothingCoefficients.mat if it exists."""
        p = self._recording_dir / "smoothingCoefficients.mat"
        return p if p.exists() else None

    def get_mask_mat(self) -> Path | None:
        """Return path to Mask.mat if it exists."""
        p = self._recording_dir / "Mask.mat"
        return p if p.exists() else None

    # ------------------------------------------------------------------
    # Lazy playback loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        idx     = 0
        current = None
        try:
            while self._running:
                target_dt = 1.0 / max(self._playback_rate(), 1e-3)
                t0 = time.perf_counter()

                # Re-read the source every iteration: the GUI thread flips it
                # when calibration starts and ends. One attribute read is
                # atomic under the GIL, and restarting the index on a switch
                # keeps it in range whatever the two folders' lengths are.
                source = self._source
                if source != current:
                    current = source
                    idx     = 0
                files = self._dark_files if source == "dark" else self._tiff_files
                n     = len(files)
                if n == 0:
                    break

                frame = self._crop.cut(tifffile.imread(str(files[idx])))
                t_capture = time.monotonic()
                self.frames_emitted += 1
                self.frame_ready.emit(frame, t_capture)

                now = t_capture
                if now - self._last_display >= self._display_interval:
                    self.display_ready.emit(frame)
                    self._last_display = now

                idx += 1
                if idx >= n:
                    if self._loop:
                        idx = 0
                    else:
                        break

                sleep_s = target_dt - (time.perf_counter() - t0)
                if sleep_s > 0:
                    self.msleep(int(sleep_s * 1000))
        except Exception as e:
            self.error.emit(str(e))

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    # The settings in a recording folder's name, the convention of the MATLAB
    # script (GetParamsFromFileName.m) and of the app's own saved frames
    # (core/frame_writer.py): "expT5ms_Gain24dB_BL100DU_FR40Hz_005".
    _NAME_TOKENS = {
        "exposure_us": (r"(?:^|_)expT(\d+(?:\.\d+)?)ms(?:_|$)", 1000.0),   # ms → µs
        "gain_db":     (r"(?:^|_)Gain(\d+(?:\.\d+)?)dB(?:_|$)", 1.0),
        "frame_rate":  (r"(?:^|_)FR(\d+(?:\.\d+)?)Hz(?:_|$)", 1.0),
        "black_level": (r"(?:^|_)BL(\d+(?:\.\d+)?)DU(?:_|$)", 1.0),
    }

    @staticmethod
    def _pixel_layout(frame: np.ndarray) -> tuple[int, float]:
        """(bit depth, scale) of a recording, from its first frame — the test
        the MATLAB script makes (SCOSvsTime_WithNoiseSubtraction_Ver2.m:386).

        Pylon Viewer stores 10-bit data left-justified in uint16 (every value
        a multiple of 64), the app saves frames as the camera sent them
        (scale 1). A frame that is a multiple of neither 64 nor 16 is taken as
        Mono12 — the rig's format, and the only 16-bit one the MATLAB script
        reads right; an 8-bit TIFF is Mono8.
        """
        if frame.dtype == np.uint8:
            return 8, 1.0
        if not np.any(frame % 64):
            return 10, 64.0
        if not np.any(frame % 16):
            return 12, 16.0
        return 12, 1.0

    def _parse_recording_params(self, first_frame: np.ndarray | None = None) -> dict:
        """Read camera parameters from LocalStd7x7_corr.mat, the folder name
        and the TIFF file name; the bit depth and scale from the first frame."""
        params: dict = {}
        for key, (pattern, factor) in self._NAME_TOKENS.items():
            m = re.search(pattern, self._recording_dir.name)
            if m:
                params[key] = float(m.group(1)) * factor
        if first_frame is not None:
            params["bit_depth"], params["scale"] = self._pixel_layout(first_frame)
            params["pixel_format"] = f"Mono{params['bit_depth']}"

        # Parse camera model and serial from the first TIFF filename.
        # Example: Basler_a2A1920-160umPRO__40513592__20241101_163604036_0000.tiff
        m = re.match(r"Basler_([^_]+(?:-\w+)*)__(\d+)__", self._tiff_files[0].name)
        if m:
            model_str = m.group(1)    # e.g. "a2A1920-160umPRO"
            params["model"]        = f"Basler_{model_str}"
            # The serial is what matters: G is looked up by SN + bit depth in
            # CamerasMeasuredGain.csv.
            params["serial"]       = m.group(2)

        # Try to read recording params from LocalStd7x7_corr.mat
        mat_path = self._recording_dir / "LocalStd7x7_corr.mat"
        if mat_path.exists():
            try:
                import scipy.io
                mat  = scipy.io.loadmat(str(mat_path))
                info = mat["info"][0, 0]
                params["bit_depth"] = int(info["nBits"][0, 0])
                params["serial"]    = str(info["cameraSN"][0])
                h, w = info["imageSize"][0].tolist()
                params["height"] = int(h)
                params["width"]  = int(w)
                # The "name" sub-struct carries expT, Gain, FR
                name = info["name"][0, 0]
                params["exposure_us"]   = float(name["expT"][0, 0]) * 1000   # ms → µs
                params["gain_db"]       = float(name["Gain"][0, 0])
                params["frame_rate"]    = float(name["FR"][0, 0])
                params["pixel_format"]  = f"Mono{params['bit_depth']}"
            except Exception:
                pass   # fall back to filename-parsed values and defaults

        return params
