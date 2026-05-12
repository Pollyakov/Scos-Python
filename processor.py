"""
SCOS algorithm: computes speckle contrast (κ²) from a single frame.
Matches MATLAB RecordSCOSLong.m (corrSpeckleContrast formula).
"""

import csv
import warnings
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter


class GainTableError(ValueError):
    """Raised when a camera's SN + nBits combination is absent from the gain CSV."""

# OpenCV is ~5× faster than scipy for box filtering.
# Imported once at module load; falls back to scipy if not installed.
try:
    import cv2 as _cv2
except ImportError:
    _cv2 = None

# Default calibration table bundled with the project.
_DEFAULT_GAIN_TABLE = Path(__file__).parent / "CamerasMeasuredGain.csv"


def load_gain_from_table(
    camera_sn: str,
    n_bits: int,
    gain_db: float,
    gain_data_file: "str | Path | None" = None,
) -> float:
    """
    Return the measured DU/e gain for a specific camera from a CSV table.

    Matches MATLAB LoadG.m: finds the closest gain_dB entry for the given
    cameraSN + nBits combination and interpolates linearly in dB space:
        G = G_closest × 10^((gain_db − closest_gain_db) / 20)

    Parameters
    ----------
    camera_sn      : camera serial number as a numeric string, e.g. "40513592"
    n_bits         : bit depth — must match the nBits column in the CSV
    gain_db        : requested gain in dB
    gain_data_file : path to CamerasMeasuredGain.csv; defaults to the file
                     bundled next to processor.py

    Returns
    -------
    actual_g : DU/e conversion constant (float)

    Raises
    ------
    ValueError       if camera_sn is non-numeric or no row matches cameraSN+nBits
    FileNotFoundError if the CSV file cannot be found
    """
    try:
        sn_numeric = float(camera_sn)
    except ValueError:
        raise ValueError(
            f"load_gain_from_table: camera_sn must be numeric, got {camera_sn!r}"
        )

    csv_path = Path(gain_data_file) if gain_data_file is not None else _DEFAULT_GAIN_TABLE
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Gain table not found: {csv_path}. "
            "Place CamerasMeasuredGain.csv next to processor.py or pass gain_data_file."
        )

    # Parse CSV without pandas — stdlib only.
    # Strip whitespace from header names and all values to handle the lab CSV's
    # irregular spacing.
    matches: list[tuple[float, float]] = []  # (gain_dB, measuredG)
    with open(csv_path, newline="") as fh:
        reader = csv.reader(fh)
        headers = [h.strip() for h in next(reader)]
        for raw_row in reader:
            row = {k: v.strip() for k, v in zip(headers, raw_row)}
            try:
                row_sn   = float(row["cameraSN"])
                row_bits = int(row["nBits"])
                row_db   = float(row["gain_dB"])
                row_g    = float(row["measuredG"])
            except (ValueError, KeyError):
                continue  # skip blank or malformed rows
            if row_sn == sn_numeric and row_bits == n_bits:
                matches.append((row_db, row_g))

    if not matches:
        raise GainTableError(
            f"Can't calculate SCOS: CameraSN {camera_sn} Mono{n_bits} "
            f"was not found in G[DU/e] Calibration file"
        )

    dbs = np.array([m[0] for m in matches])
    gs  = np.array([m[1] for m in matches])
    idx = int(np.argmin(np.abs(dbs - gain_db)))

    actual_g = float(gs[idx] * 10 ** ((gain_db - dbs[idx]) / 20.0))

    if abs(dbs[idx] - gain_db) > 1e-9:
        warnings.warn(
            f"gain_db={gain_db} not in table for cameraSN={camera_sn}, nBits={n_bits}. "
            f"Interpolated G={actual_g:.6f} DU/e from closest entry at {dbs[idx]} dB.",
            UserWarning,
            stacklevel=2,
        )

    return actual_g


def convert_gain(gain_db: float, bit_depth: int = 8, sat_capacity: float = 10500.0) -> float:
    """
    Convert camera gain from dB to DU/e using the camera's sat_capacity.
    Matches MATLAB ConvertGain.m.

    Use load_gain_from_table() instead when a measured CSV entry exists for
    the camera — it is more accurate than this formula-based estimate.
    """
    G0 = (2 ** bit_depth) / sat_capacity
    return 10 ** (gain_db / 20.0) * G0


def local_variance(im: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute local mean and local variance using a square sliding window.
    Equivalent to MATLAB: stdfilt(im, true(w)).^2  and  imboxfilt(im, w)

    Uses the unbiased variance estimator (÷ N-1) to match MATLAB's stdfilt.
    Uses cv2.blur when available (~5× faster than scipy on large frames).

    Intermediate calculations run in float32 for ~40% faster box filtering.
    The variance formula (mean_sq - mean²) is numerically stable in float32
    for dark-subtracted camera data (values typically < 100 DU).

    Returns:
        mean_im : local mean image (float32)
        var_im  : local variance image, unbiased std² (float32)
    """
    im_f = im.astype(np.float32, copy=False)

    if _cv2 is not None:
        ksize   = (window, window)
        mean_im = _cv2.blur(im_f, ksize)
        mean_sq = _cv2.blur(im_f * im_f, ksize)
    else:
        mean_im = uniform_filter(im_f, size=window).astype(np.float32)
        mean_sq = uniform_filter(im_f * im_f, size=window).astype(np.float32)

    var_im = mean_sq - mean_im ** 2
    np.maximum(var_im, 0.0, out=var_im)   # in-place clamp

    # MATLAB stdfilt divides by N-1; the formula above divides by N.
    # Multiply by N/(N-1) to convert.  Skip for window=1 (var is always 0).
    n = window ** 2
    if n > 1:
        var_im *= np.float32(n / (n - 1))

    return mean_im, var_im


def _stream_tiffs_welford(tiff_files: list, scale: float,
                          on_progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel mean and unbiased variance from a list of TIFF paths (no full RAM load)."""
    import tifffile
    n    = len(tiff_files)
    step = max(1, n // 20)
    mean = M2 = None
    for i, f in enumerate(tiff_files):
        fr = tifffile.imread(str(f)).astype(np.float64) / scale
        if mean is None:
            mean = np.zeros_like(fr)
            M2   = np.zeros_like(fr)
        delta  = fr - mean
        mean  += delta / (i + 1)
        M2    += delta * (fr - mean)
        if on_progress is not None and (i % step == 0 or i == n - 1):
            on_progress(i + 1, n)
    assert mean is not None and M2 is not None
    return mean, M2 / (n - 1)


def _stream_tiffs_mean(tiff_files: list, scale: float,
                       on_progress=None) -> np.ndarray:
    """Per-pixel temporal mean from a list of TIFF paths."""
    import tifffile
    n   = len(tiff_files)
    step = max(1, n // 20)
    acc = None
    for i, f in enumerate(tiff_files):
        fr = tifffile.imread(str(f)).astype(np.float64) / scale
        acc = fr if acc is None else acc + fr
        if on_progress is not None and (i % step == 0 or i == n - 1):
            on_progress(i + 1, n)
    assert acc is not None
    return acc / n


def _sort_tiffs(folder) -> list:
    import re
    from pathlib import Path
    p = Path(folder)
    files = list(p.glob("*.tiff")) + list(p.glob("*.tif"))
    return sorted(files,
        key=lambda f: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", f.name)) else 0)


class SCOSProcessor:
    """
    Per-frame SCOS computation.

    Typical usage:
        proc = SCOSProcessor(window_size=9, gain_db=8, bit_depth=12)
        proc.calibrate(dark_frames)           # optional but recommended
        proc.calibrate_bright(bright_frames)  # optional but recommended
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)

    For real lab recordings (Basler a2A1920-160umPRO, 24 dB):
        proc = SCOSProcessor(window_size=7, gain_db=24, bit_depth=10,
                             sat_capacity=11117.0)   # Phase-0 measured
        proc.load_calibration_mat(dark_dir=dark_dir, main_dir=recording_dir,
                                  scale=64.0, sat_capacity=11117.0)
        k2_raw, k2_corr, mean_i = proc.process(raw_uint16_frame, mask)
    """

    def __init__(self, window_size: int = 7, gain_db: float = 0.0,
                 bit_depth: int = 8, sat_capacity: float = 10500.0,
                 camera_sn: str | None = None):
        self.window_size   = window_size
        self.gain_db       = gain_db
        self.bit_depth     = bit_depth
        self.sat_capacity  = sat_capacity
        # When set, process() uses load_gain_from_table() for measured DU/e.
        # When None, falls back to convert_gain() (formula-based).
        self.camera_sn    = camera_sn
        # scale: raw TIFF uint16 values are divided by this before processing.
        # 1.0 for real camera frames; 64.0 for 10-bit left-justified TIFFs (a2A1920).
        self.scale        = 1.0

        self.dark_mean  : np.ndarray | None = None
        self.dark_var   : np.ndarray | None = None  # spatially smoothed
        self.bright_var : np.ndarray | None = None  # spVar

        self._cached_G  : float | None = None       # cached gain so CSV isn't re-read every frame

        # ROI crop: set by set_roi(); None = full frame (no crop).
        self._roi       : tuple[slice, slice] | None = None
        self._mask_crop : np.ndarray | None = None
        # float32 crops of calibration arrays — rebuilt by set_roi().
        # Keeping these as float32 avoids dtype-upcast in process() arithmetic.
        self._dm_f32 : np.ndarray | None = None   # dark_mean crop
        self._dv_f32 : np.ndarray | None = None   # dark_var crop
        self._bv_f32 : np.ndarray | None = None   # bright_var crop

    def set_roi(self, mask: np.ndarray) -> None:
        """
        Precompute the tight bounding-box crop for the given mask.

        After this call, process() operates only on the sub-image that contains
        the mask, padded by window_size//2 + 1 pixels so every edge pixel has a
        full neighbourhood for the box filter.  Matches MATLAB's im_cut crop.

        Also caches float32 crops of dark_mean, dark_var, and bright_var so
        that process() can stay in float32 throughout without repeated dtype
        conversions.

        Call this whenever the ROI mask OR calibration arrays change.
        """
        rows, cols = np.where(mask)
        if rows.size == 0:
            self._roi = None
            self._mask_crop = None
            self._dm_f32 = self._dv_f32 = self._bv_f32 = None
            return
        pad = self.window_size // 2 + 1
        H, W = mask.shape
        r0 = max(0, int(rows.min()) - pad)
        r1 = min(H, int(rows.max()) + pad + 1)
        c0 = max(0, int(cols.min()) - pad)
        c1 = min(W, int(cols.max()) + pad + 1)
        self._roi = (slice(r0, r1), slice(c0, c1))
        self._mask_crop = mask[self._roi]
        # Pre-build float32 crops so process() avoids repeated conversions.
        roi = self._roi
        self._dm_f32 = self.dark_mean[roi].astype(np.float32) if self.dark_mean  is not None else None
        self._dv_f32 = self.dark_var[roi].astype(np.float32)  if self.dark_var   is not None else None
        self._bv_f32 = self.bright_var[roi].astype(np.float32) if self.bright_var is not None else None

    def calibrate(self, dark_frames: np.ndarray) -> None:
        """
        Compute dark mean and variance from a stack of dark frames.
        dark_frames: shape (H, W, N)

        dark_var is spatially smoothed with the same window, matching MATLAB:
            darkVar = imboxfilt(darkVarIm, windowSize)
        """
        self.dark_mean = dark_frames.mean(axis=2)
        raw_var        = dark_frames.var(axis=2, ddof=1)   # N-1, matches MATLAB var()
        self.dark_var  = uniform_filter(raw_var, size=self.window_size)

    def calibrate_bright(self, bright_frames: np.ndarray) -> None:
        """
        Compute the bright calibration term (spVar) from a stack of bright frames.
        bright_frames: shape (H, W, N)

        Matches MATLAB:
            spIm  = mean(bright_frames) − darkIm
            spVar = stdfilt(spIm, true(windowSize)).^2
        """
        mean_bright = bright_frames.mean(axis=2).astype(np.float64)
        if self.dark_mean is not None:
            mean_bright = mean_bright - self.dark_mean
        _, self.bright_var = local_variance(mean_bright, self.window_size)

    def load_calibration_mat(
        self,
        smoothing_mat: "str | Path | None" = None,
        dark_dir: "str | Path | None" = None,
        main_dir: "str | Path | None" = None,
        scale: float = 64.0,
        sat_capacity: float | None = None,
        window_size: int | None = None,
        on_progress: "callable | None" = None,
    ) -> None:
        """
        Load or compute calibration arrays.

        Parameters
        ----------
        smoothing_mat : path to smoothingCoefficients.mat (spVar fallback)
        dark_dir      : folder of dark TIFF files → dark_mean, dark_var
        main_dir      : folder of main recording TIFFs → spVar computed from scratch
                        Preferred over smoothing_mat; requires dark_mean to be loaded first.
        scale         : divide raw TIFF uint16 by this (64 for 10-bit left-justified a2A1920)
        sat_capacity  : override self.sat_capacity (11117.0 for a2A1920 at 24 dB)
        window_size   : spatial smoothing window; defaults to self.window_size

        Order when both dark_dir and main_dir are given:
          1. Stream dark TIFFs → dark_mean, dark_var
          2. Stream main TIFFs → mean_bright → spIm = mean_bright - dark_mean → spVar
        """
        self.scale = scale
        w = window_size if window_size is not None else self.window_size
        if sat_capacity is not None:
            self.sat_capacity = sat_capacity

        # --- Dark calibration ---
        if dark_dir is not None:
            tiffs = _sort_tiffs(dark_dir)
            if tiffs:
                def _dark_prog(i, n):
                    if on_progress:
                        on_progress(f"Dark frames: {i}/{n}")
                mean, M2_over_nm1 = _stream_tiffs_welford(tiffs, scale, _dark_prog)
                self.dark_mean = mean
                self.dark_var  = uniform_filter(M2_over_nm1, size=w)
                if on_progress:
                    on_progress("Dark calibration done")

        # --- Bright calibration (spVar) ---
        if main_dir is not None:
            # Compute from raw frames: spIm = mean(main) - dark_mean, then local variance
            tiffs = _sort_tiffs(main_dir)
            if tiffs:
                def _bright_prog(i, n):
                    if on_progress:
                        on_progress(f"Bright frames: {i}/{n}")
                mean_bright = _stream_tiffs_mean(tiffs, scale, _bright_prog)
                sp_im = mean_bright - (self.dark_mean if self.dark_mean is not None
                                       else np.zeros_like(mean_bright))
                if on_progress:
                    on_progress("Computing spVar…")
                _, self.bright_var = local_variance(sp_im, w)
        elif smoothing_mat is not None:
            # Fallback: load pre-computed spVar from MATLAB file
            import scipy.io
            mat = scipy.io.loadmat(str(smoothing_mat))
            self.bright_var = mat["spVar"].astype(np.float64)

    def process(self, frame: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
        """
        Compute speckle contrast for one frame.

        Formula (matches MATLAB corrected):
            K²_corr = mean_ROI( (var_raw − G·⟨I⟩ − spVar − dark_var − 1/12) / ⟨I⟩² )

        If self.scale != 1.0, raw frame values are divided by scale before processing.

        Returns
        -------
        kappa2_raw    : raw κ² = mean(var / mean²) over ROI
        kappa2_corr   : noise-corrected κ²
        mean_intensity: mean pixel value inside ROI after dark subtraction
        """
        if self._cached_G is None:
            if self.camera_sn is not None:
                self._cached_G = load_gain_from_table(self.camera_sn, self.bit_depth, self.gain_db)
            else:
                self._cached_G = convert_gain(self.gain_db, self.bit_depth, self.sat_capacity)
        G = self._cached_G

        # Crop to ROI bounding box (set by set_roi) — same as MATLAB's im_cut.
        # Slicing numpy arrays is a zero-copy view; astype() below does the copy.
        if self._roi is not None:
            roi        = self._roi
            mask_work  = self._mask_crop
            dark_mean  = self._dm_f32   # pre-cropped float32 — may be None
            dark_var   = self._dv_f32
            bright_var = self._bv_f32
        else:
            roi        = (slice(None), slice(None))
            mask_work  = mask
            dark_mean  = self.dark_mean.astype(np.float32)  if self.dark_mean  is not None else None
            dark_var   = self.dark_var.astype(np.float32)   if self.dark_var   is not None else None
            bright_var = self.bright_var.astype(np.float32) if self.bright_var is not None else None

        # Stay in float32 throughout: avoids a redundant float64 allocation
        # and keeps all subsequent arithmetic in float32 (cv2.blur is already f32).
        im = frame[roi].astype(np.float32) / np.float32(self.scale)

        if dark_mean is not None:
            im -= dark_mean

        mean_im, var_im = local_variance(im, self.window_size)

        mean_sq   = mean_im ** 2
        mask_safe = mask_work & (mean_sq > 0)

        # Divide only on safe pixels to avoid NumPy divide-by-zero warnings.
        safe_mean_sq = mean_sq[mask_safe]

        # Raw κ² — no corrections
        kappa2_raw = float(np.mean(var_im[mask_safe] / safe_mean_sq))

        # Corrected κ² — subtract all noise terms in-place to avoid temporaries
        # corr_num = var_im − G·mean − spVar − dark_var − 1/12
        # Cast G to float32 so the multiplication stays in float32.
        corr_num  = var_im - (np.float32(G) * mean_im)
        if bright_var is not None:
            corr_num -= bright_var
        if dark_var is not None:
            corr_num -= dark_var
        corr_num -= np.float32(1.0 / 12.0)

        kappa2_corr = float(np.mean(corr_num[mask_safe] / safe_mean_sq))

        mean_intensity = float(np.mean(im[mask_work]))
        return kappa2_raw, kappa2_corr, mean_intensity
