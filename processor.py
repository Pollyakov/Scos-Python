"""
SCOS algorithm: computes speckle contrast (κ²) from a single frame.
Matches MATLAB RecordSCOSLong.m (corrSpeckleContrast formula).
"""

import csv
import warnings
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter

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
        raise ValueError(
            f"load_gain_from_table: no calibration entry for cameraSN={camera_sn}, "
            f"nBits={n_bits} in {csv_path}. "
            "Add a measured row or use convert_gain() for a formula-based estimate."
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

    Returns:
        mean_im : local mean image
        var_im  : local variance image (unbiased std²)
    """
    im_f = im if im.dtype == np.float64 else im.astype(np.float64)

    if _cv2 is not None:
        ksize   = (window, window)
        mean_im = _cv2.blur(im_f, ksize)
        mean_sq = _cv2.blur(im_f * im_f, ksize)
    else:
        mean_im = uniform_filter(im_f, size=window)
        mean_sq = uniform_filter(im_f * im_f, size=window)

    var_im = mean_sq - mean_im ** 2
    np.maximum(var_im, 0.0, out=var_im)   # in-place clamp

    # MATLAB stdfilt divides by N-1; the formula above divides by N.
    # Multiply by N²/(N²-1) to convert.  Skip for window=1 (var is always 0).
    n = window ** 2
    if n > 1:
        var_im *= n / (n - 1)

    return mean_im, var_im


def _stream_tiffs_welford(tiff_files: list, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel mean and unbiased variance from a list of TIFF paths (no full RAM load)."""
    import tifffile
    n    = len(tiff_files)
    mean = M2 = None
    for i, f in enumerate(tiff_files):
        fr = tifffile.imread(str(f)).astype(np.float64) / scale
        if mean is None:
            mean = np.zeros_like(fr)
            M2   = np.zeros_like(fr)
        delta  = fr - mean
        mean  += delta / (i + 1)
        M2    += delta * (fr - mean)
    assert mean is not None and M2 is not None
    return mean, M2 / (n - 1)


def _stream_tiffs_mean(tiff_files: list, scale: float) -> np.ndarray:
    """Per-pixel temporal mean from a list of TIFF paths."""
    import tifffile
    acc = None
    for f in tiff_files:
        fr = tifffile.imread(str(f)).astype(np.float64) / scale
        acc = fr if acc is None else acc + fr
    assert acc is not None
    return acc / len(tiff_files)


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
        self.window_size  = window_size
        self.gain_db      = gain_db
        self.bit_depth    = bit_depth
        self.sat_capacity = sat_capacity
        # When set, process() uses load_gain_from_table() for measured DU/e.
        # When None, falls back to convert_gain() (formula-based).
        self.camera_sn    = camera_sn
        # scale: raw TIFF uint16 values are divided by this before processing.
        # 1.0 for real camera frames; 64.0 for 10-bit left-justified TIFFs (a2A1920).
        self.scale        = 1.0

        self.dark_mean  : np.ndarray | None = None
        self.dark_var   : np.ndarray | None = None  # spatially smoothed
        self.bright_var : np.ndarray | None = None  # spVar

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
                mean, M2_over_nm1 = _stream_tiffs_welford(tiffs, scale)
                self.dark_mean = mean
                self.dark_var  = uniform_filter(M2_over_nm1, size=w)

        # --- Bright calibration (spVar) ---
        if main_dir is not None:
            # Compute from raw frames: spIm = mean(main) - dark_mean, then local variance
            tiffs = _sort_tiffs(main_dir)
            if tiffs:
                mean_bright = _stream_tiffs_mean(tiffs, scale)
                sp_im = mean_bright - (self.dark_mean if self.dark_mean is not None
                                       else np.zeros_like(mean_bright))
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
        if self.camera_sn is not None:
            G = load_gain_from_table(self.camera_sn, self.bit_depth, self.gain_db)
        else:
            G = convert_gain(self.gain_db, self.bit_depth, self.sat_capacity)
        im = frame.astype(np.float64) / self.scale  # no-op when scale=1.0

        if self.dark_mean is not None:
            im -= self.dark_mean     # in-place after astype (new array already allocated)

        mean_im, var_im = local_variance(im, self.window_size)

        mean_sq   = mean_im ** 2
        mask_safe = mask & (mean_sq > 0)          # combined ROI + numerical-safety mask

        # Raw κ² — no corrections
        kappa2_raw = float(np.mean((var_im / mean_sq)[mask_safe]))

        # Corrected κ² — subtract all noise terms in-place to avoid temporaries
        # corr_num = var_im − G·mean − spVar − dark_var − 1/12
        corr_num  = var_im - (G * mean_im)         # first allocation
        if self.bright_var is not None:
            corr_num -= self.bright_var             # in-place
        if self.dark_var is not None:
            corr_num -= self.dark_var               # in-place
        corr_num -= (1.0 / 12.0)                   # scalar, in-place

        kappa2_corr = float(np.mean((corr_num / mean_sq)[mask_safe]))

        mean_intensity = float(np.mean(im[mask]))
        return kappa2_raw, kappa2_corr, mean_intensity
