"""
Live camera image display with ROI overlay (circle).
Uses pyqtgraph ImageItem for fast GPU-accelerated rendering.
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal, Qt, QRectF
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout
from PyQt6.QtGui import QPen, QColor
import pyqtgraph as pg


class ImageWidget(QWidget):
    roi_changed = pyqtSignal(np.ndarray, dict)   # (mask, circ={cx,cy,r})

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._roi_circle = None   # pyqtgraph CircleROI
        self._circ       = None   # dict: cx, cy, r

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # pyqtgraph plot area (no axes)
        self.view = pg.GraphicsLayoutWidget()
        self.plot = self.view.addPlot()
        self.plot.setAspectLocked(True)
        self.plot.hideAxis('left')
        self.plot.hideAxis('bottom')
        self.plot.invertY(True)

        self.image_item = pg.ImageItem()
        self.image_item.setOpts(axisOrder='row-major')   # avoids .T transpose
        self.plot.addItem(self.image_item)

        layout.addWidget(self.view)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_auto_roi  = QPushButton("Auto ROI")
        self.btn_draw_roi  = QPushButton("Draw ROI")
        self.btn_clear_roi = QPushButton("Clear ROI")
        self.btn_auto_clim = QPushButton("Auto Contrast")
        for btn in (self.btn_auto_roi, self.btn_draw_roi,
                    self.btn_clear_roi, self.btn_auto_clim):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.btn_auto_roi.clicked.connect(self._auto_roi)
        self.btn_draw_roi.clicked.connect(self._draw_roi)
        self.btn_clear_roi.clicked.connect(self._clear_roi)
        self.btn_auto_clim.clicked.connect(self.auto_contrast)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_frame(self, frame: np.ndarray):
        """Display a new camera frame. Called from GUI thread."""
        self.image_item.setImage(frame, autoLevels=False)
        self._frame = frame

    def auto_contrast(self):
        if not hasattr(self, '_frame'):
            return
        if self._circ:
            mask = self._make_mask(self._frame.shape, self._circ)
            data = self._frame[mask]
        else:
            data = self._frame.ravel()
        lo, hi = np.percentile(data, [2, 98])
        self.image_item.setLevels([lo, hi])

    def get_mask(self) -> np.ndarray | None:
        if self._circ is None:
            return None
        return self._make_mask(self._frame.shape, self._circ)

    # ------------------------------------------------------------------
    # ROI helpers
    # ------------------------------------------------------------------

    def _auto_roi(self):
        if not hasattr(self, '_frame'):
            return
        im = self._frame.astype(np.float64)
        # center of mass
        y_idx, x_idx = np.indices(im.shape)
        total = im.sum()
        cx = float((im * x_idx).sum() / total)
        cy = float((im * y_idx).sum() / total)
        max_i = im[int(cy), int(cx)]
        threshold = 0.3 * max_i
        n_pixels = np.count_nonzero(im > threshold)
        r = float(np.sqrt(n_pixels / np.pi))
        self._set_roi(cx, cy, r)

    def _draw_roi(self):
        """Add an interactive circle ROI the user can drag/resize."""
        self._clear_roi()
        h, w = self._frame.shape if hasattr(self, '_frame') else (100, 100)
        r = min(h, w) // 6
        cx, cy = w // 2, h // 2
        roi = pg.CircleROI(
            [cx - r, cy - r], [2 * r, 2 * r],
            pen=pg.mkPen('r', width=2)
        )
        self.plot.addItem(roi)
        self._roi_circle = roi
        roi.sigRegionChangeFinished.connect(self._on_roi_changed)

    def _clear_roi(self):
        if self._roi_circle is not None:
            self.plot.removeItem(self._roi_circle)
            self._roi_circle = None
        self._circ = None

    def _set_roi(self, cx: float, cy: float, r: float):
        self._clear_roi()
        roi = pg.CircleROI(
            [cx - r, cy - r], [2 * r, 2 * r],
            pen=pg.mkPen('r', width=2)
        )
        self.plot.addItem(roi)
        self._roi_circle = roi
        roi.sigRegionChangeFinished.connect(self._on_roi_changed)
        self._circ = {"cx": cx, "cy": cy, "r": r}
        self._emit_roi()

    def _on_roi_changed(self):
        if self._roi_circle is None:
            return
        pos  = self._roi_circle.pos()
        size = self._roi_circle.size()
        r  = size[0] / 2
        cx = pos[0] + r
        cy = pos[1] + r
        self._circ = {"cx": cx, "cy": cy, "r": r}
        self._emit_roi()

    def _emit_roi(self):
        if not hasattr(self, '_frame') or self._circ is None:
            return
        mask = self._make_mask(self._frame.shape, self._circ)
        self.roi_changed.emit(mask, self._circ)

    @staticmethod
    def _make_mask(shape, circ) -> np.ndarray:
        h, w = shape
        yy, xx = np.ogrid[:h, :w]
        dist2  = (xx - circ["cx"]) ** 2 + (yy - circ["cy"]) ** 2
        return dist2 <= circ["r"] ** 2
