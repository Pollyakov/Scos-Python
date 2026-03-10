"""
SCOS algorithm: computes speckle contrast (κ²) from a single frame.
Translated from MATLAB SCOSvsTime_WithNoiseSubtraction_Ver2.m
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

    Returns:
        mean_im  : local mean image
        var_im   : local variance image (std²)
    """
    mean_im  = uniform_filter(im.astype(np.float64), size=window)
    mean_sq  = uniform_filter(im.astype(np.float64) ** 2, size=window)
    var_im   = mean_sq - mean_im ** 2
    var_im   = np.maximum(var_im, 0)   # numerical safety
    return mean_im, var_im


class SCOSProcessor:
    """
    Per-frame SCOS computation.

    Usage:
        proc = SCOSProcessor(window_size=7, gain_db=0, bit_depth=12)
        kappa2, kappa2_corr = proc.process(frame, mask)
    """

    def __init__(self, window_size: int = 7, gain_db: float = 0.0,
                 bit_depth: int = 8, sat_capacity: float = 10500.0):
        self.window_size  = window_size
        self.gain_db      = gain_db
        self.bit_depth    = bit_depth
        self.sat_capacity = sat_capacity

        # Dark / noise calibration (set via calibrate())
        self.dark_mean : np.ndarray | None = None
        self.dark_var  : np.ndarray | None = None

    def calibrate(self, dark_frames: np.ndarray):
        """
        Compute dark mean and variance from a stack of dark frames.
        dark_frames: shape (H, W, N)
        """
        self.dark_mean = dark_frames.mean(axis=2)
        self.dark_var  = dark_frames.var(axis=2)

    def process(self, frame: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
        """
        Compute speckle contrast for one frame.

        Returns:
            kappa2_raw  : raw κ² = mean(var / mean²) over ROI
            kappa2_corr : noise-corrected κ²
                          = mean((var - shot_noise - dark_var - quant_noise) / mean²)
        """
        w   = self.window_size
        G   = convert_gain(self.gain_db, self.bit_depth, self.sat_capacity)
        im  = frame.astype(np.float64)

        # Subtract dark background if available
        if self.dark_mean is not None:
            im = im - self.dark_mean

        mean_im, var_im = local_variance(im, w)

        # Avoid division by zero
        mean_sq = mean_im ** 2
        safe    = mean_sq > 0

        kappa2_map = np.where(safe, var_im / mean_sq, np.nan)
        kappa2_raw = float(np.nanmean(kappa2_map[mask]))

        # Noise corrections  (matches MATLAB line 412)
        #   shot noise  : G / <I>
        #   dark noise  : darkVar / <I>²
        #   quant noise : 1/12 / <I>²
        shot_noise  = G * mean_im
        quant_noise = np.full_like(var_im, 1.0 / 12.0)

        dark_var_im = self.dark_var if self.dark_var is not None else np.zeros_like(var_im)

        corr_num    = var_im - shot_noise - dark_var_im - quant_noise
        kappa2_corr_map = np.where(safe, corr_num / mean_sq, np.nan)
        kappa2_corr = float(np.nanmean(kappa2_corr_map[mask]))

        mean_intensity = float(np.mean(im[mask]))
        return kappa2_raw, kappa2_corr, mean_intensity
