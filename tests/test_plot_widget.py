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
        assert t[0] == pytest.approx(60.0)
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

    def test_time_stored_in_seconds(self, widget):
        """Time is stored in seconds.

        It used to be stored in minutes, because minutes were the only unit
        the widget could draw. Task 10 made the axis unit depend on the
        recording's length, so the conversion moved to render time and the
        stored data stayed in the unit it arrives in.
        """
        widget.append(120.0, 20.0)
        t, _ = widget.get_data()
        assert t[0] == pytest.approx(120.0)

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


# ----------------------------------------------------------------------
# Stretching the graph along time only (todo D12, picture 2, 2026-11-09)
# ----------------------------------------------------------------------

from PyQt6.QtCore import Qt, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest


def _pulse_session(w, seconds=60.0, rate=20.0):
    """Fill the widget with a made-up pulsing rBFi (72 bpm) and draw it."""
    t = np.arange(0.0, seconds, 1.0 / rate)
    bfi = 1.0 + 0.1 * np.sin(2 * np.pi * 1.2 * t) + 0.002 * t
    for ti, b in zip(t, bfi):
        w.append(float(ti), float(b))
    w.render_now()
    return t, bfi


@pytest.fixture
def shown_widget():
    w = PlotWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(900, 330)
    w.show()
    _app.processEvents()
    yield w
    w.close()


class TestStretchAlongTime:

    def test_mouse_moves_time_axis_only(self, widget):
        """The mouse may stretch and slide the time axis, never the height."""
        assert widget.graph.getViewBox().state["mouseEnabled"] == [True, False]

    def test_not_stretched_at_start(self, widget):
        _pulse_session(widget)
        assert not widget.is_stretched()

    def test_stretch_fits_height_to_shown_part(self, widget):
        t, bfi = _pulse_session(widget)
        widget.graph.setXRange(20.0, 30.0, padding=0)
        assert widget.is_stretched()
        shown = bfi[(t >= 20.0) & (t <= 30.0)]
        y0, y1 = widget.graph.getViewBox().viewRange()[1]
        assert y0 <= shown.min() < shown.max() <= y1
        # Fitted to the stretch, not to the whole recording: the drift over the
        # whole minute (0.002/s) would widen the range by ~0.12 - here it must not.
        assert (y1 - y0) < (shown.max() - shown.min()) * 1.5

    def test_view_holds_still_while_new_points_arrive(self, widget):
        _pulse_session(widget, seconds=60.0)
        widget.graph.setXRange(20.0, 30.0, padding=0)
        for i in range(40):
            widget.append(60.0 + i * 0.05, 1.0)
        widget.render_now()
        x0, x1 = widget.graph.getViewBox().viewRange()[0]
        assert (x0, x1) == pytest.approx((20.0, 30.0))
        assert widget.is_stretched()

    def test_show_all_goes_back_to_whole_recording(self, widget):
        _pulse_session(widget, seconds=60.0)
        widget.graph.setXRange(20.0, 30.0, padding=0)
        widget.show_all()
        assert not widget.is_stretched()
        x0, x1 = widget.graph.getViewBox().viewRange()[0]
        assert x0 <= 0.0 and x1 >= 59.9

    def test_double_click_shows_all(self, shown_widget):
        w = shown_widget
        _pulse_session(w)
        w.graph.setXRange(20.0, 30.0, padding=0)
        assert w.is_stretched()
        vp = w.graph.viewport()
        c, left = vp.rect().center(), Qt.MouseButton.LeftButton
        # A real mouse sends press, release, double-click, release; pyqtgraph
        # reports the click on the last release. QTest.mouseDClick alone is not enough.
        QTest.mousePress(vp, left, pos=c)
        QTest.mouseRelease(vp, left, pos=c)
        QTest.mouseDClick(vp, left, pos=c)
        QTest.mouseRelease(vp, left, pos=c)
        _app.processEvents()
        assert not w.is_stretched()

    def test_mouse_wheel_stretches_time_not_height(self, shown_widget):
        w = shown_widget
        t, bfi = _pulse_session(w)
        vb = w.graph.getViewBox()
        (x0, x1), _ = vb.viewRange()
        vp = w.graph.viewport()
        pos = QPointF(vp.rect().center())
        ev = QWheelEvent(pos, QPointF(vp.mapToGlobal(vp.rect().center())), QPoint(0, 0),
                         QPoint(0, 120 * 5), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(vp, ev)
        _app.processEvents()
        (nx0, nx1), (ny0, ny1) = vb.viewRange()
        assert (nx1 - nx0) < (x1 - x0) * 0.9       # time stretched
        assert w.is_stretched()
        # The height was refitted at once to the part now shown (not by the wheel itself).
        shown = bfi[(t >= nx0) & (t <= nx1)]
        assert ny0 <= shown.min() < shown.max() <= ny1
        assert (ny1 - ny0) < (shown.max() - shown.min()) * 1.5

    def test_stretched_view_survives_switch_to_minutes(self, widget):
        """Crossing 120 s switches the axis to minutes: keep showing the same stretch."""
        _pulse_session(widget, seconds=100.0)
        widget.graph.setXRange(50.0, 60.0, padding=0)
        for i in range(500):
            widget.append(100.0 + i * 0.05, 1.0)    # past 120 s
        widget.render_now()
        x0, x1 = widget.graph.getViewBox().viewRange()[0]
        assert (x0, x1) == pytest.approx((50.0 / 60.0, 60.0 / 60.0))
        assert widget.is_stretched()

    def test_stretching_does_not_change_the_data(self, widget):
        _pulse_session(widget)
        before = widget.get_data()
        widget.graph.setXRange(20.0, 30.0, padding=0)
        widget.show_all()
        after = widget.get_data()
        np.testing.assert_array_equal(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])

    def test_reset_ends_stretch(self, widget):
        _pulse_session(widget)
        widget.graph.setXRange(20.0, 30.0, padding=0)
        widget.reset()
        assert not widget.is_stretched()
