"""
Camera-side crop around the ROI circle (todo U6, docs/Plan_U6_Camera_Crop.md).

When the operator clicks "Cut Image", the camera is told to send only a
rectangle around the ROI circle instead of the whole sensor. This module holds
the arithmetic, with no camera or GUI code:

  * `crop_for_circle()` — which rectangle to ask the camera for.
  * `circle_mask()`     — the ROI mask for a frame that starts somewhere on the
                          sensor, i.e. a cropped frame.

Coordinates are **sensor pixels** throughout: x = column, y = row, (0, 0) the
top-left pixel of the full sensor. The ROI circle keeps those coordinates when
the image is cropped (plan decision D3), so a cropped frame is simply a window
that starts at (offset_x, offset_y).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# Pixels kept around the ROI circle on every side. Every κ² filter reads at most
# window // 2 pixels around each ROI pixel, and the processor pads its own crop
# by window // 2 + 1 (processor.set_roi), so with this margin the camera crop
# never cuts into a neighbourhood the measurement uses — for any window the GUI
# allows. 26 = 51 // 2 + 1, where 51 is the largest "Window Size"
# (gui/main_window.py, spn_window); tests/test_crop.py fails if that maximum is
# raised without raising this. The display-only cut used 20 px, too few for
# windows above 41 (plan pitfall P2).
CROP_MARGIN_PX = 26


@dataclass(frozen=True)
class CameraSteps:
    """The camera's rules for an image ROI, read from the camera at run time
    (`OffsetX.Inc`, `Width.Inc`, `Width.Min`, ...). Never hard-coded: the pylon
    emulator allows steps of 1, the real camera may require more."""
    offset_x_inc: int = 1
    offset_y_inc: int = 1
    width_inc:    int = 1
    height_inc:   int = 1
    width_min:    int = 1
    height_min:   int = 1


@dataclass(frozen=True)
class Crop:
    """A rectangle of the sensor. With offsets 0 and the sensor's own size it
    is the full sensor. This is the record U10 will save with the results."""
    offset_x:      int
    offset_y:      int
    width:         int
    height:        int
    sensor_width:  int
    sensor_height: int

    @property
    def is_full_sensor(self) -> bool:
        return (self.offset_x == 0 and self.offset_y == 0
                and self.width == self.sensor_width
                and self.height == self.sensor_height)

    @property
    def shape(self) -> tuple[int, int]:
        """(rows, columns) — the shape of a frame the camera sends with it."""
        return (self.height, self.width)


def _ceil_to(value: int, step: int) -> int:
    return -(-value // step) * step


def _fit_axis(lo: int, hi: int, size: int, off_inc: int, len_inc: int,
              len_min: int) -> tuple[int, int]:
    """Start and length along one axis that cover pixels lo .. hi-1.

    The start is a multiple of off_inc and the length a multiple of len_inc
    (at least len_min), and the window stays inside 0 .. size. Rounding only
    ever makes the window bigger, never smaller. If nothing smaller fits, the
    whole axis (start 0, length = size) is returned — the camera always
    accepts its own maximum.
    """
    lo = max(0, lo)
    hi = min(size, hi)
    start = (lo // off_inc) * off_inc
    length = _ceil_to(max(hi - start, len_min), len_inc)
    while length < size:
        # Pushed back inside the sensor if it runs past the far edge.
        s = min(start, ((size - length) // off_inc) * off_inc)
        if s + length >= hi:
            return s, length
        length += len_inc   # rounding left a gap at the far end; grow and retry
    return 0, size


def crop_for_circle(cx: float, cy: float, r: float, sensor_width: int,
                    sensor_height: int, steps: CameraSteps = CameraSteps(),
                    margin: int = CROP_MARGIN_PX) -> Crop:
    """The rectangle to ask the camera for: the ROI circle plus `margin`
    pixels on every side, clamped to the sensor and rounded to the camera's
    steps. Rounding and clamping can only enlarge it, so the circle and its
    margin are always inside (except where the sensor itself ends — then the
    crop ends there too, exactly like the full frame).

    This is the *request*. The crop actually in use is the one read back from
    the camera after applying it (plan 4.1), in case the camera rounds again.
    """
    # Pixels that can be in the mask: circle_mask keeps (x - cx)^2 + (y - cy)^2
    # <= r^2, so integer x from ceil(cx - r) to floor(cx + r). floor/ceil the
    # other way round is one pixel more generous, never less.
    x_lo = math.floor(cx - r) - margin
    x_hi = math.ceil(cx + r) + margin + 1          # exclusive
    y_lo = math.floor(cy - r) - margin
    y_hi = math.ceil(cy + r) + margin + 1
    x, w = _fit_axis(x_lo, x_hi, sensor_width, steps.offset_x_inc,
                     steps.width_inc, steps.width_min)
    y, h = _fit_axis(y_lo, y_hi, sensor_height, steps.offset_y_inc,
                     steps.height_inc, steps.height_min)
    return Crop(x, y, w, h, sensor_width, sensor_height)


def circle_mask(frame_shape: tuple[int, int], circ: dict,
                offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    """ROI mask for a frame of `frame_shape` (rows, columns) whose top-left
    pixel is sensor pixel `offset` = (offset_x, offset_y). `circ` holds cx, cy,
    r in sensor coordinates.

    Same rule as ImageWidget._make_mask — (x - cx)^2 + (y - cy)^2 <= r^2 — and
    with offset (0, 0) the same mask. For a crop it equals the full-frame mask
    cut out at the same place, pixel for pixel (the subtraction is done on the
    same integers either way).
    """
    h, w = frame_shape
    ox, oy = offset
    yy, xx = np.ogrid[oy:oy + h, ox:ox + w]
    dist2 = (xx - circ["cx"]) ** 2 + (yy - circ["cy"]) ** 2
    return dist2 <= circ["r"] ** 2


class PlaybackCrop:
    """The camera-side crop for playback sources (`--mock-folder`,
    `--mock-tiff`), so a rehearsal exercises the same path as the rig: after
    "Cut Image" every frame — recording *and* dark folder — arrives already
    cut, and the dark/bright calibrations are built from cut frames (plan
    decision D4).

    The playback thread calls `cut()` on every frame while the GUI thread may
    call `apply()`. The rectangle is held as one tuple and replaced in a
    single assignment, so a frame is never cut with half of an old and half of
    a new crop (same idea as processor._RoiCrop).
    """

    # A playback source has no camera rules to obey; steps of 1, like the
    # pylon emulator. Rounding to coarser steps is tested in tests/test_crop.py
    # and against a fake camera in tests/test_camera.py.
    STEPS = CameraSteps()

    def __init__(self) -> None:
        self._cut: tuple[slice, slice] | None = None

    def apply(self, roi_position: tuple[int, int, int, int] | None,
              sensor_width: int, sensor_height: int) -> tuple[Crop, str | None]:
        """Set the crop (None = full sensor). Returns the crop now in use and,
        if the request was refused, the reason — like a camera, an area
        outside the sensor is refused and the full sensor is used."""
        refused = None
        if roi_position is not None:
            x, y, w, h = roi_position
            if not (0 <= x and 0 <= y and w >= self.STEPS.width_min
                    and h >= self.STEPS.height_min
                    and x + w <= sensor_width and y + h <= sensor_height):
                refused = (f"Image crop {w} × {h} at ({x}, {y}) does not fit the "
                           f"{sensor_width} × {sensor_height} frames — using the full frame")
                roi_position = None
        if roi_position is None:
            self._cut = None
            return Crop(0, 0, sensor_width, sensor_height,
                        sensor_width, sensor_height), refused
        x, y, w, h = roi_position
        self._cut = (slice(y, y + h), slice(x, x + w))
        return Crop(x, y, w, h, sensor_width, sensor_height), refused

    def cut(self, frame: np.ndarray) -> np.ndarray:
        """The frame as the camera would send it: a contiguous copy of the
        crop, or the frame itself when there is no crop."""
        c = self._cut                     # one read — see the class docstring
        return frame if c is None else frame[c].copy()
