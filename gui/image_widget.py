"""
Live camera image display with ROI overlay (circle).
Uses pyqtgraph ImageItem for fast GPU-accelerated rendering.

Coordinates are sensor pixels throughout (todo U6, plan decision D3): when the
camera is cropped ("Cut Image"), each frame is drawn at its offset on the
sensor, so the ROI circle stays where it was and keeps the same cx, cy, r.
The crop itself is decided by MainWindow, which talks to the camera — this
widget only asks for it (`cut_requested`) and is told the result
(`set_camera_crop`).
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout

from core.crop import Crop, circle_mask


class ImageWidget(QWidget):
    roi_changed   = pyqtSignal(np.ndarray, dict)   # (mask, circ={cx,cy,r})
    cut_requested = pyqtSignal(bool)               # True = Cut Image, False = Full Image

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._roi_circle   = None   # pyqtgraph CircleROI
        self._circ         = None   # dict: cx, cy, r — sensor coordinates
        self._first_frame  = True   # next accepted frame: auto contrast + zoom to it
        self._frame_offset = (0, 0) # (x, y) of the shown frame's top-left pixel on the sensor
        # The camera's current image area, as MainWindow read it back from the
        # camera. None = not known (no camera that crops) — then every frame is
        # shown at (0, 0), as before U6.
        self._crop: Crop | None = None
        self._roi_locked    = False  # a run is on — see set_roi_locked()
        self._cut_available = False  # video running and a camera that crops
        self._refresh_controls()

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
        for btn in (self.btn_auto_roi, self.btn_draw_roi,
                    self.btn_clear_roi, self.btn_auto_clim, self.btn_cut_image):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.btn_auto_roi.clicked.connect(self._auto_roi)
        self.btn_draw_roi.clicked.connect(self._draw_roi)
        self.btn_clear_roi.clicked.connect(self._clear_roi)
        self.btn_auto_clim.clicked.connect(self.auto_contrast)
        # Not a checkable button: whether the image is cut is decided by the
        # camera (it may refuse), so the button only asks, and its text follows
        # set_camera_crop().
        self.btn_cut_image.clicked.connect(
            lambda: self.cut_requested.emit(not self.is_cut))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_cut(self) -> bool:
        """True while the camera sends only part of the sensor."""
        return self._crop is not None and not self._crop.is_full_sensor

    @property
    def roi_circle(self) -> dict | None:
        """The ROI circle {cx, cy, r} in sensor coordinates, or None."""
        return self._circ

    def update_frame(self, frame: np.ndarray):
        """Display a new camera frame. Called from GUI thread."""
        if self._crop is not None and frame.shape != self._crop.shape:
            # Captured before the camera's image area changed and still queued
            # (todo U6, plan 4.5). Drawn at the new offset it would be in the
            # wrong place, and keeping it as _frame would put the old size back.
            return
        self._frame = frame
        self._frame_offset = ((self._crop.offset_x, self._crop.offset_y)
                              if self._crop is not None else (0, 0))
        self.image_item.setImage(frame, autoLevels=False)
        self.image_item.setPos(*self._frame_offset)
        if self._first_frame:
            self._first_frame = False
            self.auto_contrast()
            self.plot.autoRange()

    def set_camera_crop(self, crop: Crop | None) -> None:
        """The camera's image area changed (Cut Image, Full Image, or what the
        camera reported at Start Video). Rebuilds the ROI mask for frames of
        that area and re-emits it, so MainWindow's mask and the processor's
        always match the frames the camera now sends (plan pitfall P1)."""
        changed = crop != self._crop
        self._crop = crop
        if changed:
            # Contrast and zoom are set from the first frame of the new size;
            # the frame shown right now is still the old one.
            self._first_frame = True
        self._refresh_controls()
        self._emit_roi()

    def set_cut_available(self, available: bool) -> None:
        """Whether Cut/Full Image may be clicked at all — video running, a
        camera that can crop, no run in progress (MainWindow decides)."""
        self._cut_available = available
        self._refresh_controls()

    def auto_contrast(self):
        if not hasattr(self, '_frame'):
            return
        data = None
        if self._circ:
            # The mask is built for the frame actually shown, at its own
            # offset — right after a Cut that is still the old, full frame.
            mask = self._make_mask(self._frame.shape, self._circ, self._frame_offset)
            data = self._frame[mask]
        if data is None or data.size == 0:
            data = self._frame.ravel()
        lo, hi = np.percentile(data, [2, 98])
        if lo == hi:
            lo, hi = float(data.min()), float(data.max())
        if lo == hi:                    # truly uniform image
            hi = lo + 1
        self.image_item.setLevels([lo, hi])

    def get_mask(self) -> np.ndarray | None:
        geometry = self._mask_geometry()
        if self._circ is None or geometry is None:
            return None
        shape, offset = geometry
        return self._make_mask(shape, self._circ, offset)

    # ------------------------------------------------------------------
    # ROI helpers
    # ------------------------------------------------------------------

    def _mask_geometry(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """(shape, offset) of the frames the camera sends now — the frames
        the mask is for. From the camera's crop when it is known, never from
        the frame on screen, which can be one of the old size."""
        if self._crop is not None:
            return self._crop.shape, (self._crop.offset_x, self._crop.offset_y)
        if hasattr(self, '_frame'):
            return self._frame.shape, self._frame_offset
        return None

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
        ox, oy = self._frame_offset          # frame pixels → sensor pixels
        self._set_roi(cx + ox, cy + oy, r)

    def _draw_roi(self):
        """Add an interactive circle ROI the user can drag/resize."""
        self._clear_roi()
        geometry = self._mask_geometry()
        (h, w), (ox, oy) = geometry if geometry is not None else ((100, 100), (0, 0))
        r = min(h, w) // 6
        cx, cy = ox + w // 2, oy + h // 2
        roi = pg.CircleROI(
            [cx - r, cy - r], [2 * r, 2 * r],
            pen=pg.mkPen('r', width=2)
        )
        self.plot.addItem(roi)
        self._roi_circle = roi
        roi.sigRegionChangeFinished.connect(self._on_roi_changed)
        self._circ = {"cx": cx, "cy": cy, "r": r}
        self._refresh_controls()
        self._emit_roi()

    def _clear_roi(self):
        if self._roi_circle is not None:
            self.plot.removeItem(self._roi_circle)
            self._roi_circle = None
        self._circ = None
        self._refresh_controls()
        # Emit full-frame mask so processing uses the whole image
        geometry = self._mask_geometry()
        if geometry is not None:
            mask = np.ones(geometry[0], dtype=bool)
            self.roi_changed.emit(mask, {"cx": 0, "cy": 0, "r": 0})
            self.auto_contrast()

    def set_roi_circle(self, cx: float, cy: float, r: float) -> None:
        """Programmatically set the ROI circle (e.g. from a saved Mask.mat)."""
        self._set_roi(cx, cy, r)

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
        self._refresh_controls()
        self._emit_roi()

    def set_roi_locked(self, locked: bool) -> None:
        """Prevent (or restore) any ROI edit while a run is on.

        Dragging/resizing the circle, or clicking Auto/Draw/Clear ROI, all
        end up calling processor.set_roi() on the GUI thread while worker
        threads may be concurrently reading that state in process() — see
        the _RoiCrop race this closes. QGraphicsItem.setEnabled(False) also
        disables the circle's child resize handles, not just body dragging.
        """
        self._roi_locked = locked
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        """Enable or disable the ROI and Cut controls from all the reasons at
        once, so one reason can never undo another:

          * a run is on (set_roi_locked) — nothing may change;
          * the image is cut (plan decision D1) — the circle and Auto/Draw/
            Clear ROI stay locked until Full Image, because the camera sends
            only the area around the circle as it was when Cut was clicked;
          * Cut needs a circle to cut around and a camera that can crop.
        """
        circle_locked = self._roi_locked or self.is_cut
        if self._roi_circle is not None:
            self._roi_circle.setEnabled(not circle_locked)
        for btn in (self.btn_auto_roi, self.btn_draw_roi, self.btn_clear_roi):
            btn.setEnabled(not circle_locked)
        self.btn_cut_image.setText("Full Image" if self.is_cut else "Cut Image")
        self.btn_cut_image.setEnabled(
            self._cut_available and not self._roi_locked
            and (self.is_cut or self._circ is not None))

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
        geometry = self._mask_geometry()
        if geometry is None or self._circ is None:
            return
        shape, offset = geometry
        mask = self._make_mask(shape, self._circ, offset)
        self.roi_changed.emit(mask, self._circ)
        self.auto_contrast()

    @staticmethod
    def _make_mask(shape, circ, offset=(0, 0)) -> np.ndarray:
        """The ROI mask for frames of `shape` starting at sensor pixel
        `offset` — core.crop.circle_mask, so the GUI holds no mask math."""
        return circle_mask(tuple(shape), circ, offset)
