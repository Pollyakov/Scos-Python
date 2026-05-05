"""
SCOS algorithm: computes speckle contrast (κ²) from a single frame.
Matches MATLAB RecordSCOSLong.m (corrSpeckleContrast formula).
"""

import numpy as np
from scipy.ndimage import uniform_filter


def convert_gain(gain_db: float, bit_depth: int = 8, sat_capacity: float = 10500.0) -> float:
    """
    Convert camera gain from dB to DU/e (digital units per electron).
    Matches MATLAB ConvertGain.m
    """
    G0 = (2 ** bit_depth) / sat_capacity
    return 10 ** (gain_db / 20.0) * G0


def local_variance(im: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute local mean and local variance using a square sliding window.
    Equivalent to MATLAB: stdfilt(im, true(w)).^2  and  imboxfilt(im, w)

    Uses the unbiased variance estimator (÷ N-1) to match MATLAB's stdfilt.

    Returns:
        mean_im : local mean image
        var_im  : local variance image (unbiased std²)
    """
    im_f    = im.astype(np.float64)
    mean_im = uniform_filter(im_f, size=window)
    mean_sq = uniform_filter(im_f ** 2, size=window)
    var_im  = mean_sq - mean_im ** 2
    var_im  = np.maximum(var_im, 0.0)   # numerical safety before scaling

    # MATLAB stdfilt divides by N-1; the formula above divides by N.
    # Multiply by N²/(N²-1) to convert.  Skip for window=1 (var is always 0).
    n = window ** 2
    if n > 1:
        var_im = var_im * (n / (n - 1))

    return mean_im, var_im


class SCOSProcessor:
    """
    Per-frame SCOS computation.

    Typical usage:
        proc = SCOSProcessor(window_size=9, gain_db=8, bit_depth=12)
        proc.calibrate(dark_frames)           # optional but recommended
        proc.calibrate_bright(bright_frames)  # optional but recommended
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
    """

    def __init__(self, window_size: int = 7, gain_db: float = 0.0,
                 bit_depth: int = 8, sat_capacity: float = 10500.0):
        self.window_size  = window_size
        self.gain_db      = gain_db
        self.bit_depth    = bit_depth
        self.sat_capacity = sat_capacity

        self.dark_mean  : np.ndarray | None = None
        self.dark_var   : np.ndarray | None = None  # spatially smoothed
        self.bright_var : np.ndarray | None = None  # spVar from MATLAB

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

    def process(self, frame: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
        """
        Compute speckle contrast for one frame.

        Matches MATLAB corrected formula:
            K²_corr = mean((var_raw − G·<I> − spVar − 1/12 − darkVar) / <I>²)

        Returns:
            kappa2_raw   : raw κ² = mean(var / mean²) over ROI
            kappa2_corr  : noise-corrected κ² (requires calibration for full accuracy)
            mean_intensity: mean pixel value inside ROI after dark subtraction
        """
        G  = convert_gain(self.gain_db, self.bit_depth, self.sat_capacity)
        im = frame.astype(np.float64)

        if self.dark_mean is not None:
            im = im - self.dark_mean

        mean_im, var_im = local_variance(im, self.window_size)

        mean_sq = mean_im ** 2
        safe    = mean_sq > 0

        kappa2_map = np.where(safe, var_im / mean_sq, np.nan)
        kappa2_raw = float(np.nanmean(kappa2_map[mask]))

        shot_noise    = G * mean_im
        quant_noise   = np.full_like(var_im, 1.0 / 12.0)
        dark_var_im   = self.dark_var   if self.dark_var   is not None else np.zeros_like(var_im)
        bright_var_im = self.bright_var if self.bright_var is not None else np.zeros_like(var_im)

        corr_num        = var_im - shot_noise - bright_var_im - quant_noise - dark_var_im
        kappa2_corr_map = np.where(safe, corr_num / mean_sq, np.nan)
        kappa2_corr     = float(np.nanmean(kappa2_corr_map[mask]))

        mean_intensity = float(np.mean(im[mask]))
        return kappa2_raw, kappa2_corr, mean_intensity
