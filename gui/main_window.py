"""
Main application window.
Combines: live image, SCOS time-series plot, camera controls panel.
"""

import time
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSpinBox,
    QPushButton, QCheckBox, QComboBox, QSplitter,
    QStatusBar, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
import scipy.io

from camera    import CameraThread
from processor import SCOSProcessor
from gui.image_widget import ImageWidget
from gui.plot_widget  import PlotWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SCOS — Speckle Contrast Optical Spectroscopy")
        self.resize(1400, 800)

        # State
        self._mask        = None
        self._scos_active = False
        self._start_time  = None
        self._frame_count = 0

        # Camera & processor
        self.camera    = CameraThread()
        self.processor = SCOSProcessor()

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Left: image + plot stacked
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.image_widget = ImageWidget()
        self.plot_widget  = PlotWidget()
        splitter.addWidget(self.image_widget)
        splitter.addWidget(self.plot_widget)
        splitter.setSizes([500, 300])
        root.addWidget(splitter, stretch=3)

        # Right: controls panel
        root.addWidget(self._build_controls(), stretch=1)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — camera not started")

        # FPS timer label
        self._fps_label = QLabel("FPS: --")
        self.status.addPermanentWidget(self._fps_label)
        self._last_fps_time = time.time()
        self._fps_count = 0

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # --- Camera Controls ---
        cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(cam_group)

        # Pixel format
        row = QHBoxLayout()
        row.addWidget(QLabel("Format:"))
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["Mono8", "Mono10", "Mono12"])
        self.cmb_format.setCurrentText("Mono12")
        row.addWidget(self.cmb_format)
        cam_layout.addLayout(row)

        # Exposure
        self.spn_exposure = self._labeled_spin(
            cam_layout, "Exposure (ms):", 0.021, 10000.0, 10.0, 3, step=0.1
        )

        # Gain
        self.spn_gain = self._labeled_spin(
            cam_layout, "Gain (dB):", 0.0, 24.0, 0.0, 1, step=0.5
        )

        # Frame rate
        self.spn_fps = self._labeled_spin(
            cam_layout, "Frame Rate (Hz):", 1.0, 220.0, 50.0, 1, step=1.0
        )

        # Trigger delay
        self.spn_trigger_delay = self._labeled_spin(
            cam_layout, "Trigger Delay (µs):", 0.0, 1e6, 0.0, 0, step=100.0
        )

        # External trigger
        self.chk_trigger = QCheckBox("External Trigger")
        cam_layout.addWidget(self.chk_trigger)

        layout.addWidget(cam_group)

        # --- Video Controls ---
        vid_group = QGroupBox("Acquisition")
        vid_layout = QVBoxLayout(vid_group)

        self.btn_start_video = QPushButton("Start Video")
        self.btn_start_video.setCheckable(True)
        vid_layout.addWidget(self.btn_start_video)

        layout.addWidget(vid_group)

        # --- SCOS Controls ---
        scos_group = QGroupBox("SCOS")
        scos_layout = QVBoxLayout(scos_group)

        self.spn_window = self._labeled_int_spin(
            scos_layout, "Window Size:", 3, 51, 7, step=2
        )

        self.btn_start_scos = QPushButton("Start SCOS")
        self.btn_start_scos.setCheckable(True)
        self.btn_start_scos.setEnabled(False)
        scos_layout.addWidget(self.btn_start_scos)

        self.btn_save = QPushButton("Save Data...")
        self.btn_save.setEnabled(False)
        scos_layout.addWidget(self.btn_save)

        layout.addWidget(scos_group)

        # --- Info labels ---
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        self.lbl_mean_i = QLabel("<I> : --")
        self.lbl_kappa  = QLabel("κ²  : --")
        self.lbl_bfi    = QLabel("1/κ²: --")
        for lbl in (self.lbl_mean_i, self.lbl_kappa, self.lbl_bfi):
            info_layout.addWidget(lbl)
        layout.addWidget(info_group)

        layout.addStretch()
        return panel

    @staticmethod
    def _labeled_spin(parent_layout, label, min_, max_, default, decimals, step=1.0):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spn = QDoubleSpinBox()
        spn.setRange(min_, max_)
        spn.setValue(default)
        spn.setDecimals(decimals)
        spn.setSingleStep(step)
        row.addWidget(spn)
        parent_layout.addLayout(row)
        return spn

    @staticmethod
    def _labeled_int_spin(parent_layout, label, min_, max_, default, step=1):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spn = QSpinBox()
        spn.setRange(min_, max_)
        spn.setValue(default)
        spn.setSingleStep(step)
        row.addWidget(spn)
        parent_layout.addLayout(row)
        return spn

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self):
        # Camera thread signals
        self.camera.display_ready.connect(self._on_display_frame)  # 30 FPS cap → GUI
        self.camera.frame_ready.connect(self._on_scos_frame)        # every frame → SCOS
        self.camera.error.connect(self._on_camera_error)

        # Video start/stop
        self.btn_start_video.toggled.connect(self._toggle_video)

        # SCOS start/stop
        self.btn_start_scos.toggled.connect(self._toggle_scos)

        # Camera parameter changes (live)
        self.spn_exposure.valueChanged.connect(
            lambda v: self.camera.set_exposure(v * 1000))   # ms → µs
        self.spn_gain.valueChanged.connect(self.camera.set_gain)
        self.spn_fps.valueChanged.connect(self.camera.set_frame_rate)
        self.spn_trigger_delay.valueChanged.connect(
            lambda v: self.camera.set_trigger(self.chk_trigger.isChecked(), v))
        self.chk_trigger.toggled.connect(
            lambda on: self.camera.set_trigger(on, self.spn_trigger_delay.value()))
        self.cmb_format.currentTextChanged.connect(self.camera.set_pixel_format)

        # ROI
        self.image_widget.roi_changed.connect(self._on_roi_changed)

        # Window size → processor
        self.spn_window.valueChanged.connect(
            lambda v: setattr(self.processor, 'window_size', v))

        # Save
        self.btn_save.clicked.connect(self._save_data)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _toggle_video(self, checked: bool):
        if checked:
            try:
                self.camera.pixel_format  = self.cmb_format.currentText()
                self.camera.exposure_us   = self.spn_exposure.value() * 1000
                self.camera.gain_db       = self.spn_gain.value()
                self.camera.frame_rate    = self.spn_fps.value()
                self.camera.trigger_mode  = "On" if self.chk_trigger.isChecked() else "Off"
                self.camera.trigger_delay = self.spn_trigger_delay.value()
                self.camera.start_capture()
                self.btn_start_video.setText("Stop Video")
                self.btn_start_scos.setEnabled(True)
                self.status.showMessage("Video running")
                # Read back actual camera params and update spinboxes
                self._sync_params_from_camera()
            except Exception as e:
                self.btn_start_video.setChecked(False)
                QMessageBox.critical(self, "Camera Error", str(e))
        else:
            self._scos_active = False
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setEnabled(False)
            self.camera.stop()
            self.btn_start_video.setText("Start Video")
            self.status.showMessage("Video stopped")

    def _toggle_scos(self, checked: bool):
        if checked:
            self._scos_active = True
            self._start_time  = time.time()
            self.plot_widget.reset()
            self.btn_start_scos.setText("Stop SCOS")
            self.btn_save.setEnabled(False)
            self.processor.window_size = self.spn_window.value()
            self.processor.gain_db     = self.spn_gain.value()
            fmt = self.cmb_format.currentText()
            self.processor.bit_depth   = int(fmt.replace("Mono", ""))
        else:
            self._scos_active = False
            self.btn_start_scos.setText("Start SCOS")
            self.btn_save.setEnabled(True)

    def _on_display_frame(self, frame: np.ndarray):
        """Runs at ≤30 FPS — only updates the image widget."""
        self.image_widget.update_frame(frame)

        # FPS counter (based on display frames, good enough)
        self._fps_count += 1
        now = time.time()
        elapsed = now - self._last_fps_time
        if elapsed >= 1.0:
            fps = self._fps_count / elapsed
            self._fps_label.setText(f"FPS: {fps:.1f}")
            self._fps_count = 0
            self._last_fps_time = now

    def _on_scos_frame(self, frame: np.ndarray):
        """Runs on every camera frame — SCOS computation only, no GUI work."""
        if not self._scos_active or self._mask is None:
            return
        try:
            k2_raw, k2_corr, mean_i = self.processor.process(frame, self._mask)
            t = time.time() - self._start_time
            self.plot_widget.append(t, k2_corr)
            self.lbl_mean_i.setText(f"<I>  : {mean_i:.1f} DU")
            self.lbl_kappa.setText( f"κ²   : {k2_corr:.5f}")
            self.lbl_bfi.setText(   f"1/κ² : {1/k2_corr:.2f}" if k2_corr > 0 else "1/κ²: --")
        except Exception:
            pass

    def _on_roi_changed(self, mask: np.ndarray, circ: dict):
        self._mask = mask
        self.processor.window_size = self.spn_window.value()

    def _on_camera_error(self, msg: str):
        self.status.showMessage(f"Camera error: {msg}")
        self.btn_start_video.setChecked(False)

    def _sync_params_from_camera(self):
        """Read current camera params and populate spinboxes."""
        try:
            info = self.camera.get_info()
            if info:
                self.spn_exposure.blockSignals(True)
                self.spn_gain.blockSignals(True)
                self.spn_fps.blockSignals(True)
                self.spn_exposure.setValue(info["exposure_us"] / 1000.0)
                self.spn_gain.setValue(info["gain_db"])
                self.spn_fps.setValue(info["frame_rate"])
                self.spn_exposure.blockSignals(False)
                self.spn_gain.blockSignals(False)
                self.spn_fps.blockSignals(False)
                self.status.showMessage(
                    f"{info['model']}  SN:{info['serial']}  "
                    f"{info['width']}×{info['height']}  {info['pixel_format']}"
                )
        except Exception:
            pass

    def _save_data(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save SCOS Data", "", "MAT files (*.mat);;NumPy (*.npz)"
        )
        if not path:
            return
        t, bfi = self.plot_widget.get_data()
        if path.endswith(".mat"):
            scipy.io.savemat(path, {
                "scosTime": t,
                "scosData": 1.0 / bfi,   # save κ² to match MATLAB convention
                "frameRate": self.spn_fps.value(),
                "exposureTime": self.spn_exposure.value(),
                "Gain": self.spn_gain.value(),
            })
        else:
            np.savez(path, scosTime=t, BFI=bfi,
                     frameRate=self.spn_fps.value(),
                     exposureTime=self.spn_exposure.value(),
                     gain=self.spn_gain.value())
        self.status.showMessage(f"Saved: {path}")

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self.camera.stop()
        self.camera.close()
        event.accept()
