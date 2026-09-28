"""
SCOS session state machine types and calibration helpers.

State flow:
    IDLE → PREVIEW → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED
                                                                          ↘ ERROR
"""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from scipy.ndimage import uniform_filter

# local_variance lives in processor.py for now; imported here so core/ code
# doesn't duplicate the cv2 / scipy fallback logic.
from processor import local_variance


class State(Enum):
    IDLE          = auto()
    PREVIEW       = auto()
    DARK_CAL      = auto()
    BRIGHT_CAL    = auto()
    MEASURING_INIT = auto()
    MEASURING     = auto()
    FINISHED      = auto()
    ERROR         = auto()


@dataclass
class SessionConfig:
    window_size:        int   = 7
    n_dark_frames:      int   = 600    # N1 — frames captured with laser off
    n_bright_frames:    int   = 600    # N2 — frames captured with laser on, no subject
    recording_minutes:  float = 5.0   # max 4 h per protocol; stored here as default
    norm_seconds:       float = 5.0   # length of normalization window for rBFi
    save_frames:        bool  = False  # write individual TIFFs during calibration
    output_folder:      str   = ""
    plot_update_s:      float = 1.0   # QTimer interval for plot refresh (config.json)
    image_update_s:     float = 2.5   # live-image refresh during MEASURING (protocol)


# ---------------------------------------------------------------------------
# rBFi normalization (worklist task 10 / todo E1)
# ---------------------------------------------------------------------------

# A recording longer than this is normalized by the mean of its baseline
# window; a shorter one by the 5th percentile. Straight from the MATLAB
# reference, SCOSvsTime_WithNoiseSubtraction_Ver2.m:498-514:
#
#     if timeVec(end) > 120
#         rBFi = BFi/mean(  BFi(1:round(10*frameRate)));
#     else
#         rBFi = BFi/prctile(BFi(1:round(10*frameRate)), 5);
#     end
#
# The reason is physiological. Over a short recording the baseline window is
# only a few heartbeats long, so its mean sits somewhere in the middle of the
# pulsation and depends on which part of the cardiac cycle happened to be
# captured. The 5th percentile tracks the diastolic floor instead, which is
# stable. Over a long recording the window averages many cycles, the mean is
# steady, and it is the less noisy of the two.
NORM_LONG_RECORDING_S = 120.0
NORM_PERCENTILE       = 5.0

NORM_METHOD_MEAN       = "mean"
NORM_METHOD_PERCENTILE = "percentile5"


def choose_norm_method(duration_s: float, force_percentile: bool = False) -> str:
    """Pick the baseline statistic for a recording of this total length.

    `duration_s` is the whole recording — the last timestamp in `timeVec`,
    which is what `timeVec(end)` means in the reference. The baseline window
    is part of it, not subtracted from it.

    `force_percentile` is the GUI's "Pulsation lower level" mode: the operator
    has said explicitly that they want the diastolic floor, and that overrides
    the automatic choice however long the recording turns out to be.
    """
    if force_percentile:
        return NORM_METHOD_PERCENTILE
    return (NORM_METHOD_MEAN if duration_s > NORM_LONG_RECORDING_S
            else NORM_METHOD_PERCENTILE)


def normalization_constant(bfi_values, method: str) -> float:
    """Reduce the baseline window's BFi values to the single rBFi divisor.

    The percentile uses numpy's "hazen" method, not its default. MATLAB's
    `prctile` interpolates linearly between the sorted values placed at
    (i - 0.5) / n, which is exactly Hazen; numpy defaults to (i - 1) / (n - 1)
    and gives a different answer on the same data. On [1, 2, 3, 4] the 5th
    percentile is 1.0 by MATLAB and Hazen but 1.15 by numpy's default — and
    this number divides every point in the results file, so the discrepancy
    would show up in the very comparison against MATLAB this exists for.
    """
    arr = np.asarray(list(bfi_values), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        raise ValueError("no finite BFi values in the normalization window")
    if method == NORM_METHOD_PERCENTILE:
        return float(np.percentile(arr, NORM_PERCENTILE, method="hazen"))
    if method == NORM_METHOD_MEAN:
        return float(np.mean(arr))
    raise ValueError(f"unknown normalization method {method!r}")


class DarkCalCollector:
    """
    Online per-pixel mean and variance using Welford's algorithm.

    Accumulates camera frames one at a time without storing the full stack,
    so memory usage is constant regardless of how many frames (N1) are collected.

    Usage:
        col = DarkCalCollector(n_frames=600, window_size=7)
        for frame in camera:
            col.add_frame(frame)
            if col.done:
                dark_mean, dark_var = col.result()
                break
    """

    def __init__(self, n_frames: int, window_size: int) -> None:
        self.n_target    = n_frames
        self.window_size = window_size
        self._n:    int             = 0
        self._mean: np.ndarray | None = None
        self._M2:   np.ndarray | None = None

    # ------------------------------------------------------------------
    # Properties

    @property
    def n_collected(self) -> int:
        return self._n

    @property
    def done(self) -> bool:
        return self._n >= self.n_target

    # ------------------------------------------------------------------
    # Frame accumulation

    def add_frame(self, frame: np.ndarray) -> None:
        """Incorporate one frame into the running statistics."""
        f = frame.astype(np.float64)
        self._n += 1
        if self._mean is None:
            self._mean = np.zeros_like(f)
            self._M2   = np.zeros_like(f)
        delta       = f - self._mean
        self._mean += delta / self._n
        self._M2   += delta * (f - self._mean)   # Welford update

    # ------------------------------------------------------------------
    # Final computation

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (dark_mean, dark_var_filtered).

        dark_mean          — per-pixel temporal mean of the dark frames
        dark_var_filtered  — per-pixel unbiased temporal variance (ddof=1),
                             spatially smoothed with a uniform_filter of
                             size=window_size to match MATLAB:
                                 darkVar = imboxfilt(darkVarIm, windowSize)

        Raises RuntimeError if fewer than 2 frames have been collected.
        """
        if self._n < 2 or self._mean is None or self._M2 is None:
            raise RuntimeError(
                f"DarkCalCollector needs at least 2 frames; only {self._n} collected."
            )
        raw_var  = self._M2 / (self._n - 1)          # unbiased, matches MATLAB var()
        dark_var = uniform_filter(raw_var, size=self.window_size)
        return self._mean.copy(), dark_var


class BrightCalCollector:
    """
    Online per-pixel mean using Welford's algorithm.

    Accumulates N2 bright frames one at a time, then computes:
        sp_im      = mean_bright − dark_mean   (spIm in MATLAB)
        bright_var = local spatial variance of sp_im   (spVar in MATLAB)

    bright_var is set as processor.bright_var in process() to correct for
    camera fixed-pattern noise from the illuminated sensor.

    Usage:
        col = BrightCalCollector(n_frames=600, window_size=7)
        for frame in camera:
            col.add_frame(frame)
            if col.done:
                sp_im, bright_var = col.result(dark_mean=proc.dark_mean)
                break
    """

    def __init__(self, n_frames: int, window_size: int) -> None:
        self.n_target    = n_frames
        self.window_size = window_size
        self._n:    int               = 0
        self._mean: np.ndarray | None = None

    # ------------------------------------------------------------------
    # Properties

    @property
    def n_collected(self) -> int:
        return self._n

    @property
    def done(self) -> bool:
        return self._n >= self.n_target

    # ------------------------------------------------------------------
    # Frame accumulation

    def add_frame(self, frame: np.ndarray) -> None:
        """Incorporate one frame into the running mean (Welford)."""
        f = frame.astype(np.float64)
        self._n += 1
        if self._mean is None:
            self._mean = np.zeros_like(f)
        self._mean += (f - self._mean) / self._n

    # ------------------------------------------------------------------
    # Final computation

    def result(
        self, dark_mean: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (sp_im, bright_var).

        sp_im      — mean bright image minus dark_mean [DU].
                     If dark_mean is None, sp_im = mean_bright (no subtraction).
        bright_var — local spatial variance of sp_im, computed with the same
                     window_size box filter used for κ².  Matches MATLAB:
                         spIm  = mean(bright_frames) − darkIm
                         spVar = stdfilt(spIm, true(windowSize)).^2

        Raises RuntimeError if no frames have been collected.
        """
        if self._n < 1 or self._mean is None:
            raise RuntimeError(
                f"BrightCalCollector needs at least 1 frame; only {self._n} collected."
            )
        sp_im = self._mean.copy()
        if dark_mean is not None:
            sp_im -= dark_mean
        _, bright_var = local_variance(sp_im, self.window_size)
        return sp_im, bright_var
