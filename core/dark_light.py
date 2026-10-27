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
"""

import numpy as np

DROP_FRACTION = 0.90      # same rule as MainWindow._LASER_OFF_DROP_FRACTION
FLOOR_DU = 2.0            # the smallest limit — the black level's own uncertainty
FIXED_LIMIT_DU = 5.0      # the limit when the live picture gave no reference
MIN_REFERENCE_DU = FIXED_LIMIT_DU   # a dimmer live picture is no reference


def light_limit(reference_du: float | None) -> tuple[float, str]:
    """(limit in DU above the black level, how it was set).

    reference_du is the live picture's ⟨I⟩ minus the black level, or None.
    """
    if reference_du is not None and reference_du >= MIN_REFERENCE_DU:
        return max(FLOOR_DU, (1.0 - DROP_FRACTION) * reference_du), "live picture"
    return FIXED_LIMIT_DU, "fixed"


def roi_light(du_frame: np.ndarray, mask: np.ndarray, black_level: float) -> float:
    """Mean inside the ROI minus the black level, in DU."""
    return float(np.mean(du_frame, where=mask, dtype=np.float64)) - black_level
