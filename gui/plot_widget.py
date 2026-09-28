"""
Real-time 1/κ² time-series plot using pyqtgraph.
Designed for incremental updates (no full redraw each frame).
"""

import math

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters          # noqa: F401  — registers pg.exporters
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
        thing that happens to the plot should be a complete draw, because the
        next step (task 12) saves it to a file.
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

        div = 60.0 if self._use_minutes else 1.0
        self.curve.setData([t / div for t in self._time], self._bfi)
        self._dirty = False
        if len(self._bfi) >= 10:
            arr = np.array(self._bfi)
            lo = float(np.percentile(arr, 5))
            hi = float(np.percentile(arr, 95))
            pad = max((hi - lo) * 0.15, hi * 0.05)
            self.graph.setYRange(max(0.0, lo - pad), hi + pad, padding=0)

    def reset(self):
        self._time.clear()
        self._bfi.clear()
        self._dirty = False
        self._use_minutes = False
        self.graph.setLabel('bottom', 'Time', units='s')
        self.curve.setData([], [])

    def save_png(self, path: "str | Path", width: int = 1600) -> int:
        """Write the curve to `path` as a PNG. Returns the number of points drawn.

        Rendered from the plot item rather than the whole widget, so the Reset
        button does not appear in the saved figure. The width is fixed instead
        of taken from the window: the file should look the same whether the
        operator had the window maximised or tucked into a corner.

        Returns 0 and writes nothing when there is no curve to save. Anything
        else that goes wrong raises — the caller decides whether a failed
        figure should be allowed to affect the session.
        """
        if not self._bfi:
            return 0
        self.render_now()                     # draw whatever is still buffered
        exporter = pg.exporters.ImageExporter(self.graph.plotItem)
        exporter.parameters()["width"] = int(width)
        exporter.export(str(path))
        return len(self._bfi)

    def get_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Plotted data, time in **seconds**.

        Before task 10 this returned minutes, because minutes were all the
        widget stored. `_save_data()` writes it as `scosTime`, so the unit is
        visible in exported .mat/.npz files.
        """
        return np.array(self._time), np.array(self._bfi)
