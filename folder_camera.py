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

# Per-camera saturation capacity (e-), measured via Phase-0 diagnostic.
# Used by MainWindow._auto_load_folder_calibration to set the correct G.
_SAT_CAPACITY = {
    "a2A1920-160umPRO": 11117.0,
}
_DEFAULT_SAT_CAPACITY = 10500.0


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

    frame_ready   = pyqtSignal(np.ndarray)
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
        self._recording_params = self._parse_recording_params()

        p = self._recording_params
        self.exposure_us  = p.get("exposure_us",  self.exposure_us)
        self.gain_db      = p.get("gain_db",       self.gain_db)
        self.frame_rate   = p.get("frame_rate",    self.frame_rate)
        self.pixel_format = p.get("pixel_format",  self.pixel_format)

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

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        self.roi_position = (x, y, w, h)

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
            "sat_capacity": p.get("sat_capacity", _DEFAULT_SAT_CAPACITY),
        }

    # ------------------------------------------------------------------
    # Calibration helpers (no equivalent on CameraThread)
    # ------------------------------------------------------------------

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
        idx  = 0
        n    = len(self._tiff_files)
        try:
            while self._running:
                target_dt = 1.0 / max(self.frame_rate, 1e-3)
                t0 = time.perf_counter()

                frame = tifffile.imread(str(self._tiff_files[idx]))
                self.frame_ready.emit(frame)

                now = time.monotonic()
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

    def _parse_recording_params(self) -> dict:
        """Read camera parameters from LocalStd7x7_corr.mat and the TIFF filename."""
        params: dict = {}

        # Parse camera model and serial from the first TIFF filename.
        # Example: Basler_a2A1920-160umPRO__40513592__20241101_163604036_0000.tiff
        m = re.match(r"Basler_([^_]+(?:-\w+)*)__(\d+)__", self._tiff_files[0].name)
        if m:
            model_str = m.group(1)    # e.g. "a2A1920-160umPRO"
            params["model"]        = f"Basler_{model_str}"
            params["serial"]       = m.group(2)
            params["sat_capacity"] = _SAT_CAPACITY.get(model_str, _DEFAULT_SAT_CAPACITY)

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
