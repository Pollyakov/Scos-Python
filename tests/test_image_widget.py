"""
Tests for gui/image_widget.py — mask generation and ROI logic.

Only tests the static/pure-math parts; GUI widget tests are skipped
if PyQt6 display is unavailable.
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

# _make_mask is a @staticmethod — import the class
from gui.image_widget import ImageWidget


class TestMakeMask:
    """ImageWidget._make_mask(shape, circ) → boolean ndarray"""

    def test_output_shape(self):
        mask = ImageWidget._make_mask((100, 200), {"cx": 100, "cy": 50, "r": 20})
        assert mask.shape == (100, 200)
        assert mask.dtype == bool

    def test_center_pixel_inside(self):
        """The center of the circle must be inside the mask."""
        mask = ImageWidget._make_mask((100, 100), {"cx": 50, "cy": 50, "r": 10})
        assert mask[50, 50] is np.True_

    def test_far_corner_outside(self):
        """A pixel far from the circle should be outside."""
        mask = ImageWidget._make_mask((100, 100), {"cx": 50, "cy": 50, "r": 10})
        assert mask[0, 0] is np.False_

    def test_pixel_count_approximates_area(self):
        """Number of True pixels ≈ π·r² for a large-enough image."""
        r = 30
        mask = ImageWidget._make_mask((200, 200), {"cx": 100, "cy": 100, "r": r})
        expected_area = np.pi * r ** 2
        actual = np.count_nonzero(mask)
        # Allow 5% error (discretization)
        assert actual == pytest.approx(expected_area, rel=0.05)

    def test_radius_zero_single_pixel(self):
        """r=0 should select only the center pixel (distance=0 ≤ 0)."""
        mask = ImageWidget._make_mask((50, 50), {"cx": 25, "cy": 25, "r": 0})
        assert np.count_nonzero(mask) == 1
        assert mask[25, 25] is np.True_

    def test_large_radius_selects_all(self):
        """A circle bigger than the image selects every pixel."""
        mask = ImageWidget._make_mask((10, 10), {"cx": 5, "cy": 5, "r": 100})
        assert np.all(mask)

    def test_off_center_circle(self):
        """Circle near edge: center inside, opposite corner outside."""
        mask = ImageWidget._make_mask((100, 100), {"cx": 10, "cy": 10, "r": 5})
        assert mask[10, 10] is np.True_
        assert mask[90, 90] is np.False_

    def test_mask_is_symmetric(self):
        """A centered circle should produce a symmetric mask."""
        mask = ImageWidget._make_mask((101, 101), {"cx": 50, "cy": 50, "r": 20})
        # Flip horizontally and vertically — should be identical
        np.testing.assert_array_equal(mask, np.flip(mask, axis=0))
        np.testing.assert_array_equal(mask, np.flip(mask, axis=1))


class TestRoiLock:
    """set_roi_locked() — disables ROI edits during MEASURING (task 3: ROI race)."""

    @pytest.fixture
    def widget(self):
        w = ImageWidget()
        w._frame = np.zeros((100, 100), dtype=np.uint16)
        w._draw_roi()   # creates a real CircleROI so locking has something to act on
        yield w
        w.close()

    def test_lock_disables_circle_and_buttons(self, widget):
        widget.set_roi_locked(True)
        assert widget._roi_circle.isEnabled() is False
        assert widget.btn_auto_roi.isEnabled() is False
        assert widget.btn_draw_roi.isEnabled() is False
        assert widget.btn_clear_roi.isEnabled() is False

    def test_unlock_restores_circle_and_buttons(self, widget):
        widget.set_roi_locked(True)
        widget.set_roi_locked(False)
        assert widget._roi_circle.isEnabled() is True
        assert widget.btn_auto_roi.isEnabled() is True
        assert widget.btn_draw_roi.isEnabled() is True
        assert widget.btn_clear_roi.isEnabled() is True

    def test_new_circle_created_while_locked_is_also_disabled(self, widget):
        """Defense in depth: even if a new ROI is created while locked
        (e.g. via set_roi_circle(), not gated by the disabled buttons),
        the new circle must come up disabled too."""
        widget.set_roi_locked(True)
        widget.set_roi_circle(30, 30, 10)
        assert widget._roi_circle.isEnabled() is False
