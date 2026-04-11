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
        self._roi_circle   = None   # pyqtgraph CircleROI
        self._circ         = None   # dict: cx, cy, r
        self._first_frame  = True
        self._crop_rect    = None   # (y0, y1, x0, x1) when cut is active

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
        self.btn_cut_image = QPushButton("Cut Image")
        self.btn_cut_image.setCheckable(True)
        for btn in (self.btn_auto_roi, self.btn_draw_roi,
                    self.btn_clear_roi, self.btn_auto_clim, self.btn_cut_image):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.btn_auto_roi.clicked.connect(self._auto_roi)
        self.btn_draw_roi.clicked.connect(self._draw_roi)
        self.btn_clear_roi.clicked.connect(self._clear_roi)
        self.btn_auto_clim.clicked.connect(self.auto_contrast)
        self.btn_cut_image.toggled.connect(self._on_cut_toggled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_frame(self, frame: np.ndarray):
        """Display a new camera frame. Called from GUI thread."""
        self._frame = frame
        if self._crop_rect is not None:
            y0, y1, x0, x1 = self._crop_rect
            cropped = frame[y0:y1, x0:x1]
            self.image_item.setImage(cropped, autoLevels=False)
            self.image_item.setPos(x0, y0)
        else:
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setPos(0, 0)
        if self._first_frame:
            self._first_frame = False
            self.auto_contrast()

    def auto_contrast(self):
        if not hasattr(self, '_frame'):
            return
        if self._circ:
            mask = self._make_mask(self._frame.shape, self._circ)
            data = self._frame[mask]
        else:
            data = self._frame.ravel()
        lo, hi = np.percentile(data, [2, 98])
        if lo == hi:
            lo, hi = float(data.min()), float(data.max())
        if lo == hi:                    # truly uniform image
            hi = lo + 1
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
        self._circ = {"cx": cx, "cy": cy, "r": r}
        self._emit_roi()

    def _clear_roi(self):
        if self._roi_circle is not None:
            self.plot.removeItem(self._roi_circle)
            self._roi_circle = None
        self._circ = None
        # Reset cut mode since there is no ROI to cut around
        self._crop_rect = None
        self.btn_cut_image.setChecked(False)
        # Emit full-frame mask so processing uses the whole image
        if hasattr(self, '_frame'):
            mask = np.ones(self._frame.shape, dtype=bool)
            self.roi_changed.emit(mask, {"cx": 0, "cy": 0, "r": 0})
            self.auto_contrast()

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
        self._apply_cut(self.btn_cut_image.isChecked())
        self.auto_contrast()

    def _on_cut_toggled(self, enabled: bool):
        self.btn_cut_image.setText("Full Image" if enabled else "Cut Image")
        self._apply_cut(enabled)

    def _apply_cut(self, enabled: bool):
        if enabled and self._circ is not None and hasattr(self, '_frame'):
            cx, cy, r = self._circ["cx"], self._circ["cy"], self._circ["r"]
            pad = 20  # fixed margin in pixels
            half = r + pad
            h, w = self._frame.shape[:2]

            # Desired square side length, clamped to frame
            span = int(min(2 * half, h, w))

            # Center the square on the ROI, clamped to frame bounds
            x0 = max(0, min(int(cx - span / 2), w - span))
            x1 = x0 + span
            y0 = max(0, min(int(cy - span / 2), h - span))
            y1 = y0 + span

            self._crop_rect = (y0, y1, x0, x1)
            self.update_frame(self._frame)
            self.plot.autoRange()
        else:
            self._crop_rect = None
            if hasattr(self, '_frame'):
                self.update_frame(self._frame)
            self.plot.autoRange()

    @staticmethod
    def _make_mask(shape, circ) -> np.ndarray:
        h, w = shape
        yy, xx = np.ogrid[:h, :w]
        dist2  = (xx - circ["cx"]) ** 2 + (yy - circ["cy"]) ** 2
        return dist2 <= circ["r"] ** 2
