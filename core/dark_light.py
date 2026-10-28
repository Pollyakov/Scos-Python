"""
Is the camera really dark for the dark calibration?

Found on the rig, 2026-10-22: the operator clicked OK at "Please turn off the
laser" with the laser still on, and nothing noticed. A dark calibration taken
like that subtracts laser light as "dark" from every frame of the session.
The user's decision (2026-10-26): check it, and stop the run if it fails.

The light is measured as MainWindow's ⟨I⟩ label measures it: the mean inside
the ROI, in DU, minus the camera's black level (the offset the camera adds to
every pixel — 100 DU on the rig cameras). Without a dark calibration that
offset is the only known zero; the camera's real offset is within about
±1 DU of it (−1.07 DU at 2 ms, +0.5 DU at 8 ms on the rig, 2026-10-19;
−0.7 DU on the lab recording's dark folder).

The limit, against what reaches the sensor with the laser on:
  - with a reference — the ⟨I⟩ of the live picture just before the laser
    prompt — the light must have fallen by 90 %, the rule of the laser-off
    check at the end of a measurement, but never by less than FLOOR_DU above
    the black level, since the offset itself is only known to about 1 DU;
  - without one (the picture was already dark before Start SCOS) a fixed
    FIXED_LIMIT_DU.
For scale (rig): laser on 33 DU (2026-10-22) and 114 DU (2026-10-08); room
light through a leaky test probe 12.7 DU (2026-10-19), which made κ²_corr
1–3.5 % too low — so catching that too is intended.

Light above the limit is not always a stop (Vika, 2026-10-28, open question
21): a *steady* leak is in the dark frames and the measurement alike, so the
dark mean takes it out again, and she wants only a warning — the operator
decides. A *flickering* leak still stops the run, and so does the laser:
  - the laser — light at LASER_FRACTION of the live picture or more. A laser
    left on gives about all of it; the leak of 2026-10-19 was a little over a
    quarter of the live picture it would have been part of;
  - flicker — judged by what it would do to the result. Flicker spread over
    the ROI with a frame-to-frame standard deviation σ_f adds about σ_f² to
    every pixel's var_dark, which the measurement frames do not share, so
    κ²_corr comes out low by about σ_f² / ⟨I⟩². Above FLICKER_LIMIT_K2 the
    leak counts as flickering. Room light flickers with the mains: scaled from
    the rig (2026-10-19) the 12.7 DU leak had σ_f² ≈ 0.5 DU² — 3.8e-5 at
    114 DU of signal, 4.6e-4 at 33 DU, so it stops either way. A leak that
    lights one part of the ROI more than the rest is underestimated by σ_f;
    the strict limit leaves room for that.
The camera's own frame-to-frame jitter in the dark (rig 2026-10-22, good
probe, 600 frames at the maximum rate) was σ_f² ≈ 4e-4 DU², 1/25 of the limit
at 33 DU — and it is shared with the measurement, so it is not judged at all
while the light stays under the limit.
"""

import numpy as np

DROP_FRACTION = 0.90      # same rule as MainWindow._LASER_OFF_DROP_FRACTION
FLOOR_DU = 2.0            # the smallest limit — the black level's own uncertainty
FIXED_LIMIT_DU = 5.0      # the limit when the live picture gave no reference
MIN_REFERENCE_DU = FIXED_LIMIT_DU   # a dimmer live picture is no reference
LASER_FRACTION = 0.50     # light at this share of the live picture = the laser
FLICKER_LIMIT_K2 = 1e-5   # flicker moving κ²_corr by more = flickering (user, 2026-10-28)

# What the finished dark calibration was judged to be.
DARK = "dark"                 # under the limit — passed
STEADY_LEAK = "steady leak"   # a warning; the operator decides
LASER = "laser"               # stop
FLICKER = "flicker"           # stop
NO_REFERENCE = "no reference" # light, but nothing to judge it by — stop


def light_limit(reference_du: float | None) -> tuple[float, str]:
    """(limit in DU above the black level, how it was set).

    reference_du is the live picture's ⟨I⟩ minus the black level, or None.
    """
    if reference_du is not None and reference_du >= MIN_REFERENCE_DU:
        return max(FLOOR_DU, (1.0 - DROP_FRACTION) * reference_du), "live picture"
    return FIXED_LIMIT_DU, "fixed"


def laser_ceiling(reference_du: float | None) -> float | None:
    """Light at or above this (DU above the black level) is the laser.
    None without a live-picture reference — there is nothing to compare to."""
    if reference_du is None or reference_du < MIN_REFERENCE_DU:
        return None
    return LASER_FRACTION * reference_du


def roi_light(du_frame: np.ndarray, mask: np.ndarray | None,
              black_level: float) -> float:
    """Mean inside the ROI (the whole frame without one) minus the black
    level, in DU."""
    where = True if mask is None else mask
    return float(np.mean(du_frame, where=where, dtype=np.float64)) - black_level


class FrameLight:
    """Running mean and variance of the per-frame ROI light (Welford) — one
    number per dark frame, so nothing is stored per frame."""

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def add(self, light: float) -> None:
        self.n += 1
        delta = light - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (light - self.mean)

    @property
    def var(self) -> float:
        """Frame-to-frame variance (ddof=1), DU²; 0 with fewer than 2 frames."""
        return self._m2 / (self.n - 1) if self.n > 1 else 0.0


def flicker_effect(frame_var: float, pixel_var: float, n_pixels: int,
                   signal_du: float) -> tuple[float, float]:
    """(σ_f² in DU², how far it would move κ²_corr).

    frame_var — variance of the per-frame ROI mean; pixel_var — the mean
    per-pixel temporal variance in the ROI. Pixel noise alone scatters the
    mean of n_pixels pixels by pixel_var / n_pixels; that part is in the
    measurement frames too, so it is taken off. signal_du — the measurement's
    ⟨I⟩, dark-subtracted.
    """
    sf2 = max(0.0, frame_var - pixel_var / max(n_pixels, 1))
    return sf2, sf2 / signal_du ** 2


def judge(light: float, limit: float, ceiling: float | None,
          flicker_k2: float | None) -> str:
    """What the finished dark calibration's light is: one of DARK,
    STEADY_LEAK, LASER, FLICKER, NO_REFERENCE. Every one but DARK and
    STEADY_LEAK stops the run."""
    if light < limit:
        return DARK
    if ceiling is None or flicker_k2 is None:
        return NO_REFERENCE
    if light >= ceiling:
        return LASER
    if flicker_k2 > FLICKER_LIMIT_K2:
        return FLICKER
    return STEADY_LEAK
