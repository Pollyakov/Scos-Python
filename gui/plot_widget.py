"""
Real-time 1/κ² time-series plot using pyqtgraph.
Designed for incremental updates (no full redraw each frame).
"""

import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import QTimer, pyqtSignal

from core.session import NORM_LONG_RECORDING_S


class PlotWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Seconds, as they arrive. The axis unit is a rendering choice made in
        # _refresh(), not something baked into the stored data — a recording
        # crosses from one unit to the other while it is being plotted.
        self._time   = []
        self._bfi    = []      # 1 / kappa2_corr, normalized once a constant exists
        self._dirty  = False   # new data waiting to be rendered
        self._use_minutes = False
        self._setup_ui()

        # Refresh the curve once per second — batches GPU redraws and keeps
        # the GUI thread free between frames (design: CLAUDE.md §architecture).
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.graph = pg.PlotWidget()
        self.graph.setLabel('left',   '1/κ²  (rBFI)')
        self.graph.setLabel('bottom', 'Time', units='s')
        self.graph.setBackground('#1e1e1e')
        self.graph.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.graph.plot(pen=pg.mkPen('#00d4ff', width=2))
        layout.addWidget(self.graph)

        # Stretching the graph along time only (todo D12, picture 2 chosen 2026-11-09).
        # Wheel stretches/shrinks the time axis, dragging slides it; the height is
        # always set by this widget, never by the mouse. Turning the wheel switches
        # off pyqtgraph's automatic X range — that state *is* "stretched"
        # (is_stretched()); a double-click switches it back on (show_all()).
        # Display only: nothing here touches the data that is recorded or saved.
        self.graph.setMouseEnabled(x=True, y=False)
        self.graph.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)
        self.graph.scene().sigMouseClicked.connect(self._on_mouse_clicked)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset")
        btn_row.addStretch()
        btn_row.addWidget(self.btn_reset)
        layout.addLayout(btn_row)

        self.btn_reset.clicked.connect(self.reset)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, time_sec: float, bfi: float):
        """Add one data point (bfi = 1/κ², already computed). time_sec is elapsed seconds."""
        if not (math.isfinite(bfi) and bfi > 0):
            return
        self._time.append(time_sec)
        self._bfi.append(bfi)
        self._dirty = True

    def rescale(self, factor: float) -> None:
        """Multiply every plotted value by `factor` and redraw.

        Used once, at the end of a session: the curve was drawn against a
        provisional normalization constant, and the final one is only known
        when the recording's total length is (worklist task 10). Rescaling
        beats re-plotting from the file — the curve is already in memory, and
        a multi-hour session has hundreds of thousands of points.
        """
        if not math.isfinite(factor) or factor <= 0 or factor == 1.0:
            return
        self._bfi = [v * factor for v in self._bfi]
        self.render_now()

    def render_now(self) -> None:
        """Draw whatever is buffered, without waiting for the next timer tick.

        Points arrive continuously but the curve is only redrawn once a second,
        so at any moment up to a second of data may be buffered and unrendered.
        That is fine while a session runs and wrong at the end of one: the last
        thing that happens to the plot should be a complete draw, so the curve
        left on screen shows the whole session. (The saved figure is drawn
        from the results file, not from this widget — core/results_figure.py.)
        """
        self._dirty = True
        self._refresh()

    def _refresh(self):
        """Called by QTimer every second — push buffered data to the curve."""
        if not self._dirty:
            return
        # The reference script plots a recording over 120 s in minutes and a
        # shorter one in seconds (SCOSvsTime_WithNoiseSubtraction_Ver2.m:498).
        # Here the recording is still growing, so the switch happens live, the
        # moment it crosses that length.
        if not self._use_minutes and self._time and self._time[-1] > NORM_LONG_RECORDING_S:
            self._use_minutes = True
            self.graph.setLabel('bottom', 'Time', units='min')
            if self.is_stretched():
                # Keep showing the same stretch of time in the new unit.
                x0, x1 = self.graph.getViewBox().viewRange()[0]
                self.graph.setXRange(x0 / 60.0, x1 / 60.0, padding=0)

        div = 60.0 if self._use_minutes else 1.0
        self.curve.setData([t / div for t in self._time], self._bfi)
        self._dirty = False
        if self.is_stretched():
            self._fit_height_to_shown()
        elif len(self._bfi) >= 10:
            arr = np.array(self._bfi)
            lo = float(np.percentile(arr, 5))
            hi = float(np.percentile(arr, 95))
            pad = max((hi - lo) * 0.15, hi * 0.05)
            self.graph.setYRange(max(0.0, lo - pad), hi + pad, padding=0)

    # ------------------------------------------------------------------
    # Stretching along time (display only)
    # ------------------------------------------------------------------

    def is_stretched(self) -> bool:
        """True while the operator has stretched or slid the time axis.

        The view then holds still while new points arrive; show_all() (or a
        double-click) goes back to the whole recording, following live data.
        """
        return not self.graph.getViewBox().autoRangeEnabled()[0]

    def show_all(self) -> None:
        """Back to the whole recording, following the live data again."""
        vb = self.graph.getViewBox()
        vb.enableAutoRange(x=True)
        vb.updateAutoRange()     # now, not at the next paint
        self.render_now()

    def _on_mouse_clicked(self, ev) -> None:
        if ev.double():
            self.show_all()

    def _on_x_range_changed(self, *_args) -> None:
        # Wheel or drag: refit the height at once, not at the next 1 s tick.
        if self.is_stretched():
            self._fit_height_to_shown()

    def _fit_height_to_shown(self) -> None:
        """Fit the height to the points inside the stretch being looked at."""
        if not self._time:
            return
        div = 60.0 if self._use_minutes else 1.0
        x0, x1 = self.graph.getViewBox().viewRange()[0]
        t = np.asarray(self._time) / div
        shown = np.asarray(self._bfi)[(t >= x0) & (t <= x1)]
        if shown.size < 2:
            return
        lo, hi = float(shown.min()), float(shown.max())
        pad = max((hi - lo) * 0.1, hi * 0.01)
        self.graph.setYRange(max(0.0, lo - pad), hi + pad, padding=0)

    def reset(self):
        self._time.clear()
        self._bfi.clear()
        self._dirty = False
        self._use_minutes = False
        self.graph.setLabel('bottom', 'Time', units='s')
        self.curve.setData([], [])
        self.graph.getViewBox().enableAutoRange(x=True)

    def get_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Plotted data, time in **seconds**.

        Before task 10 this returned minutes, because minutes were all the
        widget stored.
        """
        return np.array(self._time), np.array(self._bfi)
