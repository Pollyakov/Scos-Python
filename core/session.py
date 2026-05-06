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
