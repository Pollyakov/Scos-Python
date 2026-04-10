"""
Tests for auto-ROI detection algorithm in ImageWidget.

The auto-ROI finds the center-of-mass of the image and estimates
a circle radius from thresholded pixel count. We feed synthetic
images with known bright spots to verify it works.
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gui.image_widget import ImageWidget


class TestAutoROI:
    """Test the _auto_roi() algorithm via a real widget instance."""

    @pytest.fixture
    def widget(self):
        w = ImageWidget()
        yield w
        w.close()

    def _feed_frame_and_auto(self, widget, frame: np.ndarray):
        """Helper: set a frame on the widget and run auto-ROI."""
        widget._frame = frame
        widget._first_frame = False
        widget._auto_roi()
        return widget._circ

    def test_centered_bright_spot(self, widget):
        """A bright Gaussian spot in the center → ROI near center."""
        h, w = 200, 200
        yy, xx = np.mgrid[:h, :w]
        cx, cy = 100, 100
        # Gaussian-like bright spot
        frame = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 20 ** 2))
        frame = (frame * 4000).astype(np.uint16)

        circ = self._feed_frame_and_auto(widget, frame)
        assert circ is not None
        assert circ["cx"] == pytest.approx(cx, abs=5)
        assert circ["cy"] == pytest.approx(cy, abs=5)
        assert circ["r"] > 5  # should find a reasonable radius

    def test_off_center_spot(self, widget):
        """Bright spot at (150, 50) → ROI center near that point."""
        h, w = 200, 300
        yy, xx = np.mgrid[:h, :w]
        spot_x, spot_y = 150, 50
        frame = np.exp(-((xx - spot_x) ** 2 + (yy - spot_y) ** 2) / (2 * 15 ** 2))
        frame = (frame * 3000).astype(np.uint16)

        circ = self._feed_frame_and_auto(widget, frame)
        assert circ is not None
        # Center-of-mass should be pulled toward the bright spot
        assert circ["cx"] == pytest.approx(spot_x, abs=10)
        assert circ["cy"] == pytest.approx(spot_y, abs=10)

    def test_uniform_image_roi_centered(self, widget):
        """Uniform image → center of mass = image center."""
        frame = np.full((100, 100), 1000, dtype=np.uint16)
        circ = self._feed_frame_and_auto(widget, frame)
        assert circ is not None
        # Center of mass of a uniform image = geometric center
        assert circ["cx"] == pytest.approx(49.5, abs=1)
        assert circ["cy"] == pytest.approx(49.5, abs=1)

    def test_auto_roi_radius_scales_with_spot_size(self, widget):
        """Larger bright spot → larger detected radius."""
        h, w = 300, 300
        yy, xx = np.mgrid[:h, :w]
        cx, cy = 150, 150

        # Small spot (sigma=10)
        frame_small = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 10 ** 2))
        frame_small = (frame_small * 4000).astype(np.uint16)
        circ_small = self._feed_frame_and_auto(widget, frame_small)

        # Large spot (sigma=40)
        frame_large = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 40 ** 2))
        frame_large = (frame_large * 4000).astype(np.uint16)
        circ_large = self._feed_frame_and_auto(widget, frame_large)

        assert circ_small is not None
        assert circ_large is not None
        assert circ_large["r"] > circ_small["r"]

    def test_no_frame_does_nothing(self, widget):
        """_auto_roi() without a frame should not crash or set _circ."""
        # Don't set _frame at all
        if hasattr(widget, '_frame'):
            delattr(widget, '_frame')
        widget._auto_roi()
        assert widget._circ is None
