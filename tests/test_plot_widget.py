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

    def test_append_positive_bfi(self, widget):
        """Appending a positive BFI value adds one data point."""
        widget.append(60.0, 20.0)  # 60 seconds, BFI=20 (= 1/κ² where κ²=0.05)
        t, bfi = widget.get_data()
        assert len(t) == 1
        assert t[0] == pytest.approx(1.0)  # 60s → 1 minute
        assert bfi[0] == pytest.approx(20.0)

    def test_append_skips_zero_bfi(self, widget):
        """BFI = 0 is non-physical → should be skipped."""
        widget.append(10.0, 0.0)
        t, _ = widget.get_data()
        assert len(t) == 0

    def test_append_skips_negative_bfi(self, widget):
        """Negative BFI is physically meaningless → should be skipped."""
        widget.append(10.0, -0.1)
        t, _ = widget.get_data()
        assert len(t) == 0

    def test_append_multiple_points(self, widget):
        """Multiple appends accumulate correctly."""
        for i in range(5):
            widget.append(float(i * 10), 10.0)
        t, bfi = widget.get_data()
        assert len(t) == 5
        assert len(bfi) == 5

    def test_time_converted_to_minutes(self, widget):
        """Time is stored in minutes, not seconds."""
        widget.append(120.0, 20.0)
        t, _ = widget.get_data()
        assert t[0] == pytest.approx(2.0)  # 120s = 2 min

    def test_reset_clears_data(self, widget):
        """reset() removes all accumulated data."""
        widget.append(10.0, 20.0)
        widget.append(20.0, 16.7)
        widget.reset()
        t, bfi = widget.get_data()
        assert len(t) == 0
        assert len(bfi) == 0

    def test_get_data_returns_numpy(self, widget):
        """get_data() returns numpy arrays."""
        widget.append(10.0, 10.0)
        t, bfi = widget.get_data()
        assert isinstance(t, np.ndarray)
        assert isinstance(bfi, np.ndarray)

    def test_bfi_stored_as_passed(self, widget):
        """BFI values are stored exactly as passed (caller does 1/κ² before appending)."""
        bfis = [100.0, 20.0, 10.0, 2.0]
        for i, b in enumerate(bfis):
            widget.append(float(i), b)
        _, stored = widget.get_data()
        for i, b in enumerate(bfis):
            assert stored[i] == pytest.approx(b)
