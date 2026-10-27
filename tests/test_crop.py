"""
Crop arithmetic for the camera-side crop (todo U6, core/crop.py).

What must hold for any circle and any camera step rules:
  * the crop asked for lies inside the sensor and obeys the camera's steps;
  * the ROI circle plus CROP_MARGIN_PX is inside it (where the sensor allows),
    so no κ² filter window — up to the GUI's largest, 51 — reaches past its
    edge (docs/Plan_U6_Camera_Crop.md, section 3);
  * the mask of a cropped frame is the full-frame mask cut out at the same
    place, pixel for pixel.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The QApplication must exist before gui.main_window is imported — see CLAUDE.md.
_app = QApplication.instance() or QApplication([])

from core.crop import CROP_MARGIN_PX, CameraSteps, Crop, circle_mask, crop_for_circle
from gui.image_widget import ImageWidget
from gui.main_window import MainWindow

# The rig camera's sensor (a2A1920-160umBAS): columns × rows.
SENSOR_W, SENSOR_H = 1936, 1216

_STEPS = [
    CameraSteps(),                                      # the pylon emulator: all 1
    CameraSteps(2, 2, 2, 2, 4, 2),
    CameraSteps(4, 2, 4, 2, 4, 1),                      # a plausible ace 2 set
    CameraSteps(16, 16, 16, 16, 64, 64),                # deliberately coarse
    CameraSteps(4, 1, 8, 2, 16, 1),                     # offset and size steps differ
]

# Circles: middle of the sensor, fractional centres, and against every edge
# and corner, where clamping and rounding interact.
_CIRCLES = [
    (968.0, 608.0, 150.0),
    (960.4, 600.7, 150.3),
    (1215.0, 684.0, 200.0),
    (10.0, 600.0, 100.0),          # left edge — circle partly off the sensor
    (1930.0, 600.0, 100.0),        # right edge
    (968.0, 5.0, 80.0),            # top edge
    (968.0, 1210.0, 80.0),         # bottom edge
    (0.0, 0.0, 50.0),              # top-left corner
    (1935.5, 1215.5, 50.0),        # bottom-right corner
    (968.0, 608.0, 0.5),           # tiny
    (968.0, 608.0, 590.0),         # nearly the whole sensor height
]


def _needed(cx, cy, r, margin=CROP_MARGIN_PX):
    """Sensor columns [x0, x1) and rows [y0, y1) the crop must cover: every
    pixel of the circle plus `margin`, cut at the sensor's edges."""
    xs = np.arange(SENSOR_W)
    ys = np.arange(SENSOR_H)
    in_x = xs[np.abs(xs - cx) <= r]
    in_y = ys[np.abs(ys - cy) <= r]
    return (max(0, int(in_x.min()) - margin), min(SENSOR_W, int(in_x.max()) + margin + 1),
            max(0, int(in_y.min()) - margin), min(SENSOR_H, int(in_y.max()) + margin + 1))


class TestCropForCircle:

    @pytest.mark.parametrize("steps", _STEPS)
    @pytest.mark.parametrize("cx, cy, r", _CIRCLES)
    def test_inside_sensor_obeys_steps_and_covers_circle_plus_margin(
            self, cx, cy, r, steps):
        c = crop_for_circle(cx, cy, r, SENSOR_W, SENSOR_H, steps)
        full_w = c.offset_x == 0 and c.width == SENSOR_W
        full_h = c.offset_y == 0 and c.height == SENSOR_H

        # Inside the sensor.
        assert 0 <= c.offset_x and c.offset_x + c.width <= SENSOR_W
        assert 0 <= c.offset_y and c.offset_y + c.height <= SENSOR_H
        # The camera's steps — except the whole axis, which a camera always
        # accepts as its own maximum.
        if not full_w:
            assert c.offset_x % steps.offset_x_inc == 0
            assert c.width % steps.width_inc == 0 and c.width >= steps.width_min
        if not full_h:
            assert c.offset_y % steps.offset_y_inc == 0
            assert c.height % steps.height_inc == 0 and c.height >= steps.height_min
        # Covers the circle and its margin.
        x0, x1, y0, y1 = _needed(cx, cy, r)
        assert c.offset_x <= x0 and c.offset_x + c.width >= x1
        assert c.offset_y <= y0 and c.offset_y + c.height >= y1
        assert (c.sensor_width, c.sensor_height) == (SENSOR_W, SENSOR_H)

    def test_steps_of_one_give_exactly_circle_plus_margin(self):
        # No rounding to do, so nothing extra: the tightest rectangle.
        c = crop_for_circle(968.0, 608.0, 150.0, SENSOR_W, SENSOR_H)
        m = CROP_MARGIN_PX
        assert c == Crop(968 - 150 - m, 608 - 150 - m,
                         2 * (150 + m) + 1, 2 * (150 + m) + 1, SENSOR_W, SENSOR_H)

    def test_a_circle_bigger_than_the_sensor_gives_the_full_sensor(self):
        c = crop_for_circle(968.0, 608.0, 5000.0, SENSOR_W, SENSOR_H,
                            CameraSteps(4, 2, 4, 2, 4, 1))
        assert c.is_full_sensor
        assert c.shape == (SENSOR_H, SENSOR_W)

    def test_far_edge_rounding_never_leaves_a_gap(self):
        # Against the right edge, with an offset step (16) that does not divide
        # the space left by a width step of 12: pushing the start back onto a
        # multiple of 16 can leave the far end short of the circle, and then
        # the width must grow. (With offset step 4 the space always divides
        # evenly and the gap never happens — that version of this test passed
        # with the gap check deleted.) Searched over many positions so the
        # case is actually hit.
        steps = CameraSteps(16, 16, 12, 12, 4, 4)
        for cx in np.arange(1700.0, 1936.0, 0.7):
            c = crop_for_circle(cx, 608.0, 60.0, SENSOR_W, SENSOR_H, steps)
            x0, x1, _, _ = _needed(cx, 608.0, 60.0)
            assert c.offset_x <= x0 and c.offset_x + c.width >= x1, cx
            assert c.offset_x + c.width <= SENSOR_W, cx


class TestMarginCoversEveryWindow:

    def test_margin_is_enough_for_the_largest_window_the_gui_allows(self):
        # The processor pads its crop by window // 2 + 1. If someone raises the
        # Window Size maximum, CROP_MARGIN_PX must follow — this fails until it does.
        w = MainWindow(camera=_StubCamera())
        try:
            largest = w.spn_window.maximum()
        finally:
            w.close()
        assert largest // 2 + 1 <= CROP_MARGIN_PX, (
            f"Window Size goes up to {largest}, which needs a "
            f"{largest // 2 + 1} px margin; CROP_MARGIN_PX is {CROP_MARGIN_PX}")

    def test_worker_maximum_is_the_one_the_frame_checks_allow_for(self):
        # Same pattern, for tools/check_saved_frames.py: a session folder does
        # not record the worker count, so it allows for the most the spinbox
        # gives (core.pipeline.WORST_CASE_FRAMES_HELD).
        from core.pipeline import MAX_WORKERS
        w = MainWindow(camera=_StubCamera())
        try:
            assert w.spn_workers.maximum() == MAX_WORKERS
        finally:
            w.close()


class TestCircleMask:

    @pytest.mark.parametrize("cx, cy, r", _CIRCLES)
    def test_cropped_mask_is_the_full_mask_cut_out(self, cx, cy, r):
        circ = {"cx": cx, "cy": cy, "r": r}
        full = circle_mask((SENSOR_H, SENSOR_W), circ)
        c = crop_for_circle(cx, cy, r, SENSOR_W, SENSOR_H, CameraSteps(4, 2, 4, 2, 4, 1))
        cropped = circle_mask(c.shape, circ, (c.offset_x, c.offset_y))
        np.testing.assert_array_equal(
            cropped,
            full[c.offset_y:c.offset_y + c.height, c.offset_x:c.offset_x + c.width])
        # ...and nothing of the circle is left outside the crop.
        assert cropped.sum() == full.sum()

    @pytest.mark.parametrize("cx, cy, r", _CIRCLES[:3])
    def test_same_mask_as_the_image_widget(self, cx, cy, r):
        circ = {"cx": cx, "cy": cy, "r": r}
        np.testing.assert_array_equal(
            circle_mask((SENSOR_H, SENSOR_W), circ),
            ImageWidget._make_mask((SENSOR_H, SENSOR_W), circ))


class _StubCamera(QObject):
    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)
    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


# ---------------------------------------------------------------------------
# Playback's maximum frame rate for the dark calibration (todo U11)
# ---------------------------------------------------------------------------

from core.crop import PLAYBACK_USB_BYTES_PER_S, playback_max_rate


class TestPlaybackMaxRate:

    def test_full_mono12_frames_are_limited_by_usb(self):
        full = Crop(0, 0, 1936, 1216, 1936, 1216)
        assert playback_max_rate(8000.0, full) == pytest.approx(
            PLAYBACK_USB_BYTES_PER_S / (1936 * 1216 * 2))
        assert playback_max_rate(8000.0, full) == pytest.approx(76.5, abs=0.1)

    def test_a_small_crop_is_limited_by_the_exposure(self):
        cut = Crop(660, 300, 611, 611, 1936, 1216)
        assert playback_max_rate(8000.0, cut) == pytest.approx(125.0)

    def test_mono8_carries_half_the_bytes(self):
        full = Crop(0, 0, 1936, 1216, 1936, 1216)
        assert playback_max_rate(1000.0, full, "Mono8") == pytest.approx(
            2 * playback_max_rate(1000.0, full, "Mono12"))

    def test_unknown_image_area_is_limited_by_the_exposure_only(self):
        assert playback_max_rate(5000.0, None) == pytest.approx(200.0)
