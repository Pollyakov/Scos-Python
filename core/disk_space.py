"""
Disk space for saving raw frames — the estimate and the two limits.

Plan: docs/Plan_Frame_Saving.md, decisions 3 and 4 (taken 2026-10-20). The
limits were proposed to Vika on 2026-10-07 (open question 14) and never
confirmed, so they are named constants here, easy to change:

- before Start SCOS: free space ≥ estimate × START_MARGIN_FACTOR + START_MARGIN_BYTES;
- while recording: stop the session cleanly below STOP_BELOW_BYTES.

The estimate is of uncompressed frames — what FrameWriter writes; speckle
barely compresses, and a check must never underestimate. Without saved frames
a session writes well under 50 MB at any length, so the check applies only
when frames are saved.
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

GB = 1_000_000_000

START_MARGIN_FACTOR = 1.2
START_MARGIN_BYTES = 5 * GB
STOP_BELOW_BYTES = 2 * GB
CHECK_INTERVAL_S = 30.0

# "Unlimited" duration (0 in the GUI): estimate for the protocol's maximum.
UNLIMITED_DURATION_S = 4 * 3600.0

# Per file on top of the pixels: the TIFF header and strip table, plus the
# file system's rounding up to whole 4 KB clusters. Generous on purpose.
_PER_FILE_OVERHEAD_BYTES = 8 * 1024


@dataclass(frozen=True)
class SpaceEstimate:
    n_frames: int
    bytes_needed: int
    bytes_required_free: int     # bytes_needed with the start margin
    duration_s: float            # the measurement length the estimate assumed


def frame_bytes(width: int, height: int, bytes_per_pixel: int) -> int:
    """Bytes on disk for one saved frame."""
    return width * height * bytes_per_pixel + _PER_FILE_OVERHEAD_BYTES


def estimate(
    width: int,
    height: int,
    bytes_per_pixel: int,
    frame_rate_hz: float,
    duration_s: float,
    n_dark: int,
    n_bright: int,
) -> SpaceEstimate:
    """Space for a session that saves every frame of every phase.

    ``duration_s`` is the measurement's set length; ``inf`` or 0 (unlimited)
    is taken as UNLIMITED_DURATION_S.
    """
    if not math.isfinite(duration_s) or duration_s <= 0:
        duration_s = UNLIMITED_DURATION_S
    n_measure = math.ceil(frame_rate_hz * duration_s)
    n = n_dark + n_bright + n_measure
    need = n * frame_bytes(width, height, bytes_per_pixel)
    required = math.ceil(need * START_MARGIN_FACTOR) + START_MARGIN_BYTES
    return SpaceEstimate(n_frames=n, bytes_needed=need,
                         bytes_required_free=required, duration_s=duration_s)


def free_bytes(path: Path) -> int:
    """Free space on the drive that holds ``path`` (which must exist)."""
    return shutil.disk_usage(path).free


def minutes_that_fit(free: int, width: int, height: int,
                     bytes_per_pixel: int, frame_rate_hz: float) -> float:
    """How many minutes of measurement frames ``free`` bytes hold, after the
    start margin — for the operator's message when the check refuses."""
    usable = (free - START_MARGIN_BYTES) / START_MARGIN_FACTOR
    per_minute = frame_bytes(width, height, bytes_per_pixel) * frame_rate_hz * 60
    return max(0.0, usable / per_minute) if per_minute > 0 else 0.0
