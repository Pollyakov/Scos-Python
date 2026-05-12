"""
Real-time 1/κ² time-series plot using pyqtgraph.
Designed for incremental updates (no full redraw each frame).
"""

import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import QTimer, pyqtSignal


class PlotWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._time   = []
        self._bfi    = []      # 1 / kappa2_corr
        self._dirty  = False   # new data waiting to be rendered
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
        self.graph.setLabel('bottom', 'Time', units='min')
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
        self._time.append(time_sec / 60.0)   # convert to minutes
        self._bfi.append(bfi)
        self._dirty = True

    def _refresh(self):
        """Called by QTimer every second — push buffered data to the curve."""
        if self._dirty:
            self.curve.setData(self._time, self._bfi)
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
        self.curve.setData([], [])

    def get_data(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array(self._time), np.array(self._bfi)
