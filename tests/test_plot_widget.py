"""
Tests for gui/plot_widget.py — PlotWidget data logic.

Requires a QApplication instance for PyQt6 widgets.
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

# Need a QApplication before creating any widget
_app = QApplication.instance() or QApplication([])

from gui.plot_widget import PlotWidget


@pytest.fixture
def widget():
    w = PlotWidget()
    yield w
    w.close()


class TestPlotWidget:

    def test_initial_state_empty(self, widget):
        """No data points after creation."""
        t, bfi = widget.get_data()
        assert len(t) == 0
        assert len(bfi) == 0

    def test_append_positive_kappa(self, widget):
        """Appending a positive κ² value adds one data point."""
        widget.append(60.0, 0.05)  # 60 seconds, κ²=0.05
        t, bfi = widget.get_data()
        assert len(t) == 1
        assert t[0] == pytest.approx(1.0)  # 60s → 1 minute
        assert bfi[0] == pytest.approx(1.0 / 0.05)  # 1/κ² = 20

    def test_append_skips_zero_kappa(self, widget):
        """κ² = 0 would cause division by zero → should be skipped."""
        widget.append(10.0, 0.0)
        t, _ = widget.get_data()
        assert len(t) == 0

    def test_append_skips_negative_kappa(self, widget):
        """Negative κ² is physically meaningless → should be skipped."""
        widget.append(10.0, -0.1)
        t, _ = widget.get_data()
        assert len(t) == 0

    def test_append_multiple_points(self, widget):
        """Multiple appends accumulate correctly."""
        for i in range(5):
            widget.append(float(i * 10), 0.1)
        t, bfi = widget.get_data()
        assert len(t) == 5
        assert len(bfi) == 5

    def test_time_converted_to_minutes(self, widget):
        """Time is stored in minutes, not seconds."""
        widget.append(120.0, 0.05)
        t, _ = widget.get_data()
        assert t[0] == pytest.approx(2.0)  # 120s = 2 min

    def test_reset_clears_data(self, widget):
        """reset() removes all accumulated data."""
        widget.append(10.0, 0.05)
        widget.append(20.0, 0.06)
        widget.reset()
        t, bfi = widget.get_data()
        assert len(t) == 0
        assert len(bfi) == 0

    def test_get_data_returns_numpy(self, widget):
        """get_data() returns numpy arrays."""
        widget.append(10.0, 0.1)
        t, bfi = widget.get_data()
        assert isinstance(t, np.ndarray)
        assert isinstance(bfi, np.ndarray)

    def test_bfi_is_inverse_kappa(self, widget):
        """BFI = 1/κ² for each appended point."""
        kappas = [0.01, 0.05, 0.1, 0.5]
        for i, k in enumerate(kappas):
            widget.append(float(i), k)
        _, bfi = widget.get_data()
        for i, k in enumerate(kappas):
            assert bfi[i] == pytest.approx(1.0 / k)
