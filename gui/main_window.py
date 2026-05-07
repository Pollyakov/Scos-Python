"""
Main application window.
Combines: live image, SCOS time-series plot, camera controls panel.
"""

import datetime
import json
import queue
import time
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSpinBox,
    QPushButton, QCheckBox, QComboBox, QSplitter,
    QStatusBar, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
import scipy.io

from camera    import CameraThread
from processor import SCOSProcessor, GainTableError
from core.session import BrightCalCollector, DarkCalCollector
from gui.image_widget import ImageWidget
from gui.plot_widget  import PlotWidget


class _CalibrationLoaderThread(QThread):
    """Background thread: streams dark TIFFs + computes spVar from main TIFFs."""
    done = pyqtSignal(bool, str)   # (success, status message)

    def __init__(self, processor, dark_dir, main_dir, smoothing_mat,
                 scale, sat_capacity, window_size, parent=None):
        super().__init__(parent)
        self._proc    = processor
        self._dark    = dark_dir
        self._main    = main_dir        # NEW: compute spVar from raw frames
        self._smooth  = smoothing_mat   # fallback if main_dir is None
        self._scale   = scale
        self._sat     = sat_capacity
        self._window  = window_size

    def run(self):
        try:
            self._proc.load_calibration_mat(
                smoothing_mat=self._smooth,
                dark_dir=self._dark,
                main_dir=self._main,
                scale=self._scale,
                sat_capacity=self._sat,
                window_size=self._window,
            )
            n_dark = len(list(self._dark.glob("*.tiff"))) if self._dark else 0
            src    = "computed from frames" if self._main else "loaded from mat"
            self.done.emit(True,
                f"dark:{n_dark}  spVar:{src}  sat_cap={self._sat:.0f}e-")
        except Exception as e:
            self.done.emit(False, str(e))


class _ArduinoUploadThread(QThread):
    """Background thread that compiles and uploads the Arduino sketch."""
    progress = pyqtSignal(str)          # intermediate status messages
    done     = pyqtSignal(bool, str)    # (success, final message)

    def __init__(self, exposure_ms: float, frame_rate_hz: float, parent=None):
        super().__init__(parent)
        self._exposure_ms   = exposure_ms
        self._frame_rate_hz = frame_rate_hz

    def run(self):
        try:
            from arduino_uploader import upload_sketch
            ok, msg = upload_sketch(
                self._exposure_ms,
                self._frame_rate_hz,
                on_progress=self.progress.emit,
            )
        except Exception as exc:
            ok, msg = False, f"Arduino upload error: {exc}"
        self.done.emit(ok, msg)


class _SCOSWorkerThread(QThread):
    """
    Runs SCOSProcessor.process() off the GUI thread.

    Holds a 1-frame queue.  If a new frame arrives before the previous one has
    been processed, the pending frame is replaced — this thread always processes
    the most recent frame and never falls behind.
    """
    result_ready   = pyqtSignal(float, float, float, float)  # t, k2_raw, k2_corr, proc_ms
    error_occurred = pyqtSignal(str)                          # GainTableError message

    def __init__(self, processor, parent=None):
        super().__init__(parent)
        self._processor = processor
        self._q: queue.Queue = queue.Queue(maxsize=1)

    def submit(self, frame: np.ndarray, mask: np.ndarray, t: float) -> None:
        """Enqueue a frame, dropping any unprocessed pending frame."""
        try:
            self._q.get_nowait()        # discard stale frame
        except queue.Empty:
            pass
        try:
            self._q.put_nowait((frame, mask, t))
        except queue.Full:
            pass                        # defensive — should not happen

    def stop(self) -> None:
        """Drain the queue and send a sentinel so run() exits cleanly."""
        try:
            self._q.get_nowait()
        except queue.Empty:
            pass
        self._q.put_nowait(None)

    def run(self) -> None:
        while True:
            item = self._q.get()        # blocks until frame or sentinel
            if item is None:
                break
            frame, mask, t = item
            t0 = time.perf_counter()
            try:
                k2_raw, k2_corr, _ = self._processor.process(frame, mask)
            except GainTableError as e:
                self.error_occurred.emit(str(e))
                continue
            except Exception:
                continue
            proc_ms = (time.perf_counter() - t0) * 1000
            self.result_ready.emit(t, k2_raw, k2_corr, proc_ms)


class MainWindow(QMainWindow):
    def __init__(self, camera=None):
        super().__init__()
        self.setWindowTitle("SCOS — Speckle Contrast Optical Spectroscopy")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, screen.width() - 20), screen.height() - 10)

        # State
        self._mask        = None
        self._scos_active = False
        self._start_time  = None
        self._frame_count = 0
        self._proc_times  = []   # rolling window of process() durations (ms)
        self._last_proc_label_time = 0.0
        self._last_stats_time = 0.0
        self._last_display_time = 0.0
        self._calib_thread:   _CalibrationLoaderThread | None = None
        self._arduino_thread: _ArduinoUploadThread | None = None
        self._arduino_debounce = QTimer(self)
        self._arduino_debounce.setSingleShot(True)
        self._arduino_debounce.setInterval(1000)  # 1 second
        self._arduino_debounce.timeout.connect(self._upload_arduino)

        # Calibration state (shared output folder; dark and bright are mutually exclusive)
        self._dark_cal_collector:      DarkCalCollector   | None = None
        self._bright_cal_collector:    BrightCalCollector | None = None
        self._dark_cal_trigger_was_on: bool = False
        self._cal_output_folder:       Path | None = None

        # Camera & processor
        self.camera    = camera if camera is not None else CameraThread()
        self.processor = SCOSProcessor()
        self._scos_worker = _SCOSWorkerThread(self.processor, parent=self)
        self._scos_worker.start()

        self._build_ui()
        self._load_config()
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
        splitter.setStretchFactor(0, 1)   # image : plot = 1 : 4 — graph is primary
        splitter.setStretchFactor(1, 4)
        self.image_widget.setMinimumHeight(80)
        self.plot_widget.setMinimumHeight(200)
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

        # Processing time label
        self._proc_label = QLabel("Proc: -- ms")
        self.status.addPermanentWidget(self._proc_label)

        # Calibration status — permanent so per-frame messages don't overwrite it
        self._calib_label = QLabel("")
        self.status.addPermanentWidget(self._calib_label)

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Camera Controls ---
        cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(cam_group)
        cam_layout.setSpacing(2)
        cam_layout.setContentsMargins(4, 8, 4, 4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Format:"))
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["Mono8", "Mono10", "Mono12"])
        self.cmb_format.setCurrentText("Mono12")
        row.addWidget(self.cmb_format)
        cam_layout.addLayout(row)

        self.spn_exposure = self._labeled_spin(
            cam_layout, "Exposure (ms):", 0.021, 10000.0, 8.0, 3, step=0.1
        )
        self.spn_gain = self._labeled_spin(
            cam_layout, "Gain (dB):", 0.0, 24.0, 8.0, 1, step=0.5
        )
        self.spn_fps = self._labeled_spin(
            cam_layout, "Frame Rate (Hz):", 1.0, 220.0, 20.0, 1, step=1.0
        )
        self.spn_trigger_delay = self._labeled_spin(
            cam_layout, "Trigger Delay (µs):", 0.0, 1e6, 0.0, 0, step=100.0
        )
        self.chk_trigger = QCheckBox("External Trigger")
        cam_layout.addWidget(self.chk_trigger)
        layout.addWidget(cam_group)

        # --- Video Controls ---
        vid_group = QGroupBox("Acquisition")
        vid_layout = QVBoxLayout(vid_group)
        vid_layout.setSpacing(2)
        vid_layout.setContentsMargins(4, 8, 4, 4)

        self.btn_start_video = QPushButton("Start Video")
        self.btn_start_video.setCheckable(True)
        vid_layout.addWidget(self.btn_start_video)
        layout.addWidget(vid_group)

        # --- SCOS Controls ---
        scos_group = QGroupBox("SCOS")
        scos_layout = QVBoxLayout(scos_group)
        scos_layout.setSpacing(2)
        scos_layout.setContentsMargins(4, 8, 4, 4)

        self.spn_window = self._labeled_int_spin(
            scos_layout, "Window Size:", 3, 51, 7, step=2
        )
        self.spn_n1 = self._labeled_int_spin(
            scos_layout, "Dark Frames (N1):", 10, 3000, 600, step=50
        )
        self.btn_dark_cal = QPushButton("Dark Calibration")
        self.btn_dark_cal.setEnabled(False)
        scos_layout.addWidget(self.btn_dark_cal)

        self.spn_n2 = self._labeled_int_spin(
            scos_layout, "Bright Frames (N2):", 10, 3000, 600, step=50
        )
        self.btn_bright_cal = QPushButton("Bright Calibration")
        self.btn_bright_cal.setEnabled(False)
        scos_layout.addWidget(self.btn_bright_cal)

        self.btn_start_scos = QPushButton("Start SCOS")
        self.btn_start_scos.setCheckable(True)
        self.btn_start_scos.setEnabled(False)
        scos_layout.addWidget(self.btn_start_scos)

        self.btn_save = QPushButton("Save Data...")
        self.btn_save.setEnabled(False)
        scos_layout.addWidget(self.btn_save)
        layout.addWidget(scos_group)

        # --- Info labels ---
        # FPS and Proc are already in the permanent status-bar widgets;
        # omitting them here keeps the panel short enough to avoid scrolling.
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(1)
        info_layout.setContentsMargins(4, 8, 4, 4)

        self.lbl_size   = QLabel("Size : --")
        self.lbl_mean_i = QLabel("⟨I⟩  : --")
        self.lbl_p5     = QLabel("p5   : --")
        self.lbl_p95    = QLabel("p95  : --")
        self.lbl_kappa  = QLabel("κ²   : --")
        self.lbl_bfi    = QLabel("1/κ² : --")
        self.lbl_roi    = QLabel("ROI  : full frame")
        for lbl in (self.lbl_size, self.lbl_mean_i, self.lbl_p5, self.lbl_p95,
                    self.lbl_kappa, self.lbl_bfi, self.lbl_roi):
            info_layout.addWidget(lbl)
        layout.addWidget(info_group)

        layout.addStretch()
        return panel

    @staticmethod
    def _labeled_spin(parent_layout, label, min_, max_, default, decimals, step=1.0):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
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
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        spn = QSpinBox()
        spn.setRange(min_, max_)
        spn.setValue(default)
        spn.setSingleStep(step)
        row.addWidget(spn)
        parent_layout.addLayout(row)
        return spn

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self):
        """Load default values from scos_config.json into GUI widgets."""
        config_path = Path(__file__).resolve().parent.parent / "scos_config.json"
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return

        for key, apply_fn in {
            "pixel_format":    lambda v: self.cmb_format.setCurrentText(str(v)),
            "exposure_ms":     lambda v: self.spn_exposure.setValue(float(v)),
            "gain_db":         lambda v: self.spn_gain.setValue(float(v)),
            "frame_rate_hz":   lambda v: self.spn_fps.setValue(float(v)),
            "trigger_delay_us": lambda v: self.spn_trigger_delay.setValue(float(v)),
            "external_trigger": lambda v: self.chk_trigger.setChecked(bool(v)),
            "window_size":     lambda v: self.spn_window.setValue(int(v)),
            "n_dark_frames":   lambda v: self.spn_n1.setValue(int(v)),
            "n_bright_frames": lambda v: self.spn_n2.setValue(int(v)),
        }.items():
            if key in cfg:
                try:
                    apply_fn(cfg[key])
                except (ValueError, TypeError):
                    pass

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self):
        # Camera thread signals
        self.camera.display_ready.connect(self._on_display_frame)   # 30 FPS cap → GUI
        self.camera.frame_ready.connect(self._on_scos_frame)         # every frame → SCOS
        self.camera.error.connect(self._on_camera_error)
        self.camera.warning.connect(self._on_camera_warning)

        # SCOS worker thread signals (results arrive on GUI thread via queued connection)
        self._scos_worker.result_ready.connect(self._on_scos_result)
        self._scos_worker.error_occurred.connect(self._on_scos_error)

        # Video start/stop
        self.btn_start_video.toggled.connect(self._toggle_video)

        # SCOS start/stop
        self.btn_start_scos.toggled.connect(self._toggle_scos)

        # Camera parameter changes (live)
        self.spn_exposure.valueChanged.connect(
            lambda v: self.camera.set_exposure(v * 1000))   # ms → µs
        self.spn_exposure.valueChanged.connect(self._schedule_arduino_reupload)
        self.spn_gain.valueChanged.connect(self.camera.set_gain)
        self.spn_fps.valueChanged.connect(self.camera.set_frame_rate)
        self.spn_fps.valueChanged.connect(self._schedule_arduino_reupload)
        self.spn_trigger_delay.valueChanged.connect(
            lambda v: self.camera.set_trigger(self.chk_trigger.isChecked(), v))
        self.chk_trigger.toggled.connect(self._on_trigger_toggled)
        self.cmb_format.currentTextChanged.connect(self.camera.set_pixel_format)

        # ROI
        self.image_widget.roi_changed.connect(self._on_roi_changed)

        # Window size → processor
        self.spn_window.valueChanged.connect(
            lambda v: setattr(self.processor, 'window_size', v))

        # Calibration buttons
        self.btn_dark_cal.clicked.connect(self._start_dark_cal)
        self.btn_bright_cal.clicked.connect(self._start_bright_cal)

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
                self.btn_dark_cal.setEnabled(True)
                self.btn_bright_cal.setEnabled(True)
                self.status.showMessage("Video running")
                # Read back actual camera params and update spinboxes
                self._sync_params_from_camera()
                # Auto-load calibration when replaying a real recording folder
                is_mock_folder = hasattr(self.camera, "get_calibration_mat")
                if is_mock_folder:
                    # External trigger and Arduino make no sense in playback mode
                    self.chk_trigger.blockSignals(True)
                    self.chk_trigger.setChecked(False)
                    self.chk_trigger.setEnabled(False)
                    self.chk_trigger.blockSignals(False)
                    self._auto_load_folder_calibration()
            except Exception as e:
                self.btn_start_video.setChecked(False)
                QMessageBox.critical(self, "Camera Error", str(e))
        else:
            self._scos_active = False
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setEnabled(False)
            self.btn_dark_cal.setEnabled(False)
            self.btn_bright_cal.setEnabled(False)
            self.chk_trigger.setEnabled(True)   # re-enable in case it was disabled for mock-folder
            # Cancel any in-progress calibration gracefully
            if self._dark_cal_collector is not None:
                self._dark_cal_collector = None
                self.btn_dark_cal.setText("Dark Calibration")
                self._calib_label.setText("Dark cal cancelled")
            if self._bright_cal_collector is not None:
                self._bright_cal_collector = None
                self.btn_bright_cal.setText("Bright Calibration")
                self._calib_label.setText("Bright cal cancelled")
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
            self.btn_dark_cal.setEnabled(False)
            self.btn_bright_cal.setEnabled(False)   # can't calibrate mid-measurement
            self.processor.window_size = self.spn_window.value()
            self.processor.gain_db     = self.spn_gain.value()
            fmt = self.cmb_format.currentText()
            self.processor.bit_depth   = int(fmt.replace("Mono", ""))
        else:
            self._scos_active = False
            self.btn_start_scos.setText("Start SCOS")
            self.btn_save.setEnabled(True)
            self.btn_dark_cal.setEnabled(True)
            self.btn_bright_cal.setEnabled(True)

    # ------------------------------------------------------------------
    # Dark calibration
    # ------------------------------------------------------------------

    def _start_dark_cal(self):
        """
        Walk the user through the dark calibration sequence:
          1. Prompt to turn off the laser.
          2. Prompt for an output folder (remembered for the session).
          3. Switch camera to internal trigger (no Arduino upload side-effect).
          4. Collect N1 frames via frame_ready → _on_scos_frame.
          5. _finish_dark_cal() fires when N1 frames are in.
        """
        if not self.camera.isRunning():
            QMessageBox.warning(self, "Dark Calibration",
                                "Please press Start Video first.")
            return

        # Step 1: ask user to turn off the laser
        reply = QMessageBox.question(
            self,
            "Dark Calibration — Step 1 of 1",
            "Please turn off the laser.\n\n"
            "Click OK when the laser is off and the measurement area is dark.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        # Step 2: choose (or reuse) output folder
        if self._cal_output_folder is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose folder to save dark calibration .mat"
            )
            if not folder:
                return
            self._cal_output_folder = Path(folder)

        # Step 3: disable external trigger without triggering Arduino upload
        self._dark_cal_trigger_was_on = self.chk_trigger.isChecked()
        self.camera.set_trigger(False, self.spn_trigger_delay.value())

        # Step 4: start collecting
        n1 = self.spn_n1.value()
        self._dark_cal_collector = DarkCalCollector(n1, self.spn_window.value())
        self.btn_dark_cal.setText(f"Dark Cal: 0 / {n1}")
        self.btn_dark_cal.setEnabled(False)
        self.btn_start_scos.setEnabled(False)

    def _finish_dark_cal(self):
        """
        Called (on GUI thread) when N1 frames have been collected.
        Computes dark_mean + dark_var, stores them in the processor,
        saves a .mat file, and restores the camera trigger.
        """
        try:
            dark_mean, dark_var = self._dark_cal_collector.result()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Dark Calibration Error", str(exc))
            self._dark_cal_collector = None
            self.btn_dark_cal.setEnabled(True)
            self.btn_start_scos.setEnabled(True)
            return

        # Store in processor and rebuild float32 crops used in process()
        self.processor.dark_mean = dark_mean
        self.processor.dark_var  = dark_var
        if self._mask is not None:
            self.processor.set_roi(self._mask)

        # Save .mat — keys match the names used throughout processor.py and MATLAB
        n_collected = self._dark_cal_collector.n_collected
        if self._cal_output_folder is not None:
            ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            mat_path = self._cal_output_folder / f"dark_cal_{ts}.mat"
            scipy.io.savemat(str(mat_path), {
                "mean_dark":  dark_mean,   # per-pixel temporal mean  [DU]
                "var_dark":   dark_var,    # per-pixel variance, spatially filtered [DU²]
                "n_frames":   n_collected,
                "window_size": self._dark_cal_collector.window_size,
            })
            self._calib_label.setText(
                f"Dark cal OK — {n_collected} frames, saved {mat_path.name}"
            )
        else:
            self._calib_label.setText(f"Dark cal OK — {n_collected} frames (not saved)")

        # Restore trigger to whatever the user had before
        if self._dark_cal_trigger_was_on:
            self.camera.set_trigger(True, self.spn_trigger_delay.value())

        self._dark_cal_collector = None
        self._restore_cal_buttons()
        QMessageBox.information(
            self, "Dark Calibration Complete",
            f"Dark calibration complete — {n_collected} frames collected.\n\n"
            "You can now run Bright Calibration, or press Start SCOS."
        )

    def _start_bright_cal(self):
        """
        Walk the user through the bright calibration sequence:
          1. Warn if dark calibration hasn't been done yet.
          2. Prompt to turn on the laser and remove the subject.
          3. Prompt for output folder (shared with dark cal; remembered for session).
          4. Collect N2 frames via frame_ready → _on_scos_frame.
          5. _finish_bright_cal() fires when N2 frames are in.

        External trigger is NOT changed — the laser is on, so the camera should
        stay synchronized with whatever trigger mode the user has set.
        """
        if not self.camera.isRunning():
            QMessageBox.warning(self, "Bright Calibration",
                                "Please press Start Video first.")
            return

        if self.processor.dark_mean is None:
            reply = QMessageBox.question(
                self,
                "Bright Calibration — No Dark Calibration",
                "Dark calibration has not been run yet.\n\n"
                "Bright calibration will proceed without dark subtraction, "
                "which may reduce accuracy.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        reply = QMessageBox.question(
            self,
            "Bright Calibration — Step 1 of 1",
            "Please turn on the laser and remove the subject from the measurement area.\n\n"
            "Click OK when the laser is on and the area is clear.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        if self._cal_output_folder is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose folder to save bright calibration .mat"
            )
            if not folder:
                return
            self._cal_output_folder = Path(folder)

        n2 = self.spn_n2.value()
        self._bright_cal_collector = BrightCalCollector(n2, self.spn_window.value())
        self.btn_bright_cal.setText(f"Bright Cal: 0 / {n2}")
        self.btn_bright_cal.setEnabled(False)
        self.btn_dark_cal.setEnabled(False)
        self.btn_start_scos.setEnabled(False)

    def _finish_bright_cal(self):
        """
        Called (on GUI thread) when N2 frames have been collected.
        Computes sp_im and bright_var, stores bright_var in the processor,
        saves a .mat file.
        """
        try:
            sp_im, bright_var = self._bright_cal_collector.result(
                dark_mean=self.processor.dark_mean
            )
        except RuntimeError as exc:
            QMessageBox.critical(self, "Bright Calibration Error", str(exc))
            self._bright_cal_collector = None
            self._restore_cal_buttons()
            return

        self.processor.bright_var = bright_var
        if self._mask is not None:
            self.processor.set_roi(self._mask)

        n_collected = self._bright_cal_collector.n_collected
        if self._cal_output_folder is not None:
            ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            mat_path = self._cal_output_folder / f"bright_cal_{ts}.mat"
            scipy.io.savemat(str(mat_path), {
                "spIm":      sp_im,       # mean bright image minus dark [DU]
                "spVar":     bright_var,  # local spatial variance of spIm [DU²]
                "n_frames":  n_collected,
                "window_size": self._bright_cal_collector.window_size,
            })
            self._calib_label.setText(
                f"Bright cal OK — {n_collected} frames, saved {mat_path.name}"
            )
        else:
            self._calib_label.setText(f"Bright cal OK — {n_collected} frames (not saved)")

        self._bright_cal_collector = None
        self._restore_cal_buttons()
        QMessageBox.information(
            self, "Bright Calibration Complete",
            f"Bright calibration complete — {n_collected} frames collected.\n\n"
            "Press Start SCOS to begin measurement."
        )

    def _restore_cal_buttons(self):
        """Re-enable calibration and SCOS buttons after either cal finishes or is cancelled."""
        self.btn_dark_cal.setText("Dark Calibration")
        self.btn_bright_cal.setText("Bright Calibration")
        self.btn_dark_cal.setEnabled(True)
        self.btn_bright_cal.setEnabled(True)
        self.btn_start_scos.setEnabled(True)

    def keyPressEvent(self, event):
        """Press 'v' to toggle External Trigger (and trigger Arduino upload)."""
        if event.key() == Qt.Key.Key_V:
            self.chk_trigger.setChecked(not self.chk_trigger.isChecked())
        else:
            super().keyPressEvent(event)

    def _on_trigger_toggled(self, on: bool):
        """Handle External Trigger checkbox toggle."""
        if on:
            # Don't switch camera yet — wait until Arduino is ready and sending pulses
            self._upload_arduino()
        else:
            self._arduino_debounce.stop()
            self.setWindowTitle("SCOS — Speckle Contrast Optical Spectroscopy")
            self.camera.set_trigger(False, self.spn_trigger_delay.value())

    def _schedule_arduino_reupload(self):
        """Re-upload Arduino sketch if external trigger is active (debounced)."""
        if self.chk_trigger.isChecked():
            self._arduino_debounce.start()  # restart the 1s timer

    def _upload_arduino(self):
        """Start background thread to compile + upload Arduino sketch."""
        # Previous upload still running — schedule retry so new values get sent
        if self._arduino_thread and self._arduino_thread.isRunning():
            self._arduino_debounce.start()
            return

        exposure_ms   = self.spn_exposure.value()          # already in ms
        frame_rate_hz = self.spn_fps.value()

        self._arduino_thread = _ArduinoUploadThread(exposure_ms, frame_rate_hz, self)
        self._arduino_thread.progress.connect(self.status.showMessage)
        self._arduino_thread.done.connect(self._on_arduino_done)
        self._arduino_thread.start()
        self.chk_trigger.setEnabled(False)
        self.status.showMessage("Arduino: connecting…")

    def _on_arduino_done(self, ok: bool, msg: str):
        self.chk_trigger.setEnabled(True)
        if ok:
            exp = self.spn_exposure.value()
            fps = self.spn_fps.value()
            self.status.showMessage(
                f"Arduino: trigger pulses active  |  exposure={exp} ms, FPS={fps} Hz"
            )
            self.setWindowTitle(
                f"SCOS — Trigger ACTIVE ({fps:.0f} Hz, {exp:.0f} ms)"
            )
            # Arduino is now sending trigger pulses — safe to switch camera
            self.camera.set_trigger(True, self.spn_trigger_delay.value())
        else:
            self.status.showMessage(msg)
            # Upload failed — revert checkbox without re-triggering the signal
            self.chk_trigger.blockSignals(True)
            self.chk_trigger.setChecked(False)
            self.chk_trigger.blockSignals(False)
            QMessageBox.warning(self, "Arduino Upload", msg)

    def _on_display_frame(self, frame: np.ndarray):
        """Runs at ≤30 FPS — only updates the image widget."""
        if self._scos_active:
            now = time.time()
            if now - self._last_display_time < 2.5:
                return
            self._last_display_time = now
        self._frame_count += 1
        self.status.showMessage(
            f"Frame #{self._frame_count}  |  shape={frame.shape}  "
            f"min={frame.min()}  max={frame.max()}  dtype={frame.dtype}"
        )
        self.image_widget.update_frame(frame)

    def _on_scos_frame(self, frame: np.ndarray):
        """Runs on GUI thread (queued signal from camera thread) — every frame."""
        # Default mask = whole frame when no ROI is set
        if self._mask is None or self._mask.shape != frame.shape:
            self._mask = np.ones(frame.shape, dtype=bool)
            self.processor.set_roi(self._mask)
            h, w = frame.shape
            self.lbl_size.setText(f"Size : {w}×{h}")
            self.lbl_roi.setText(f"ROI  : full frame ({w}x{h})")

        # FPS counter — counts all camera frames, not the display-capped ones
        self._fps_count += 1
        now = time.time()
        elapsed = now - self._last_fps_time
        if elapsed >= 1.0:
            fps = self._fps_count / elapsed
            self._fps_label.setText(f"FPS: {fps:.1f}")
            self._fps_count = 0
            self._last_fps_time = now

        # Intensity stats — update every 0.5s regardless of SCOS state
        if self._mask is not None and (now - self._last_stats_time) >= 0.5:
            pixels = frame[self._mask].astype(np.float64)
            mean_i = float(pixels.mean())
            p5     = float(np.percentile(pixels, 5))
            p95    = float(np.percentile(pixels, 95))
            self.lbl_mean_i.setText(f"⟨I⟩  : {mean_i:.1f} DU")
            self.lbl_p5.setText(    f"p5   : {p5:.1f} DU")
            self.lbl_p95.setText(   f"p95  : {p95:.1f} DU")
            self._last_stats_time = now

        # Calibration intercepts — after FPS/stats (so labels stay live) and
        # before the rate-limiter (every frame must be counted).
        # Progress is shown in the button text, not the status bar, because
        # _on_display_frame overwrites the status bar at 30 FPS.
        if self._dark_cal_collector is not None:
            self._dark_cal_collector.add_frame(frame)
            n       = self._dark_cal_collector.n_collected
            n_total = self._dark_cal_collector.n_target
            self.btn_dark_cal.setText(f"Dark Cal: {n} / {n_total}")
            if self._dark_cal_collector.done:
                self._finish_dark_cal()
            return

        if self._bright_cal_collector is not None:
            self._bright_cal_collector.add_frame(frame)
            n       = self._bright_cal_collector.n_collected
            n_total = self._bright_cal_collector.n_target
            self.btn_bright_cal.setText(f"Bright Cal: {n} / {n_total}")
            if self._bright_cal_collector.done:
                self._finish_bright_cal()
            return

        if not self._scos_active or self._mask is None:
            return

        # Hand the frame off to the worker thread.  The worker keeps a 1-frame
        # queue so it always processes the latest frame and never falls behind.
        t = time.time() - self._start_time
        self._scos_worker.submit(frame, self._mask, t)

    def _on_scos_result(self, t: float, k2_raw: float, k2_corr: float, proc_ms: float):
        """Receives SCOS result from the worker thread — runs on the GUI thread."""
        if not self._scos_active:
            return

        self._proc_times.append(proc_ms)
        if len(self._proc_times) > 100:
            self._proc_times.pop(0)

        now = time.time()
        if (now - self._last_proc_label_time) >= 1.0:
            avg_ms = sum(self._proc_times) / len(self._proc_times)
            self._proc_label.setText(f"Proc: {avg_ms:.0f} ms (last: {proc_ms:.0f} ms)")
            self._last_proc_label_time = now

        self.plot_widget.append(t, k2_corr)
        self.lbl_kappa.setText(f"κ²   : {k2_corr:.5f}")
        self.lbl_bfi.setText(f"1/κ² : {1/k2_corr:.2f}" if k2_corr > 0 else "1/κ²: --")

    def _on_scos_error(self, msg: str):
        """GainTableError raised in worker thread — stop SCOS and show dialog."""
        self.btn_start_scos.setChecked(False)
        QMessageBox.critical(self, "SCOS Error", msg)

    def _on_roi_changed(self, mask: np.ndarray, circ: dict):
        self._mask = mask
        self.processor.window_size = self.spn_window.value()
        self.processor.set_roi(mask)
        if circ.get("r", 0) > 0:
            self.lbl_roi.setText(
                f"ROI  : cx={circ['cx']:.0f} cy={circ['cy']:.0f} r={circ['r']:.0f}"
            )
        else:
            h, w = mask.shape
            self.lbl_roi.setText(f"ROI  : full frame ({w}x{h})")

    def _on_camera_warning(self, msg: str):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Camera Warning", msg)

    def _on_camera_error(self, msg: str):
        # pypylon reports "device removed" on GigE timeout even when the camera
        # is physically present — rewrite to avoid confusing the user.
        if "removed" in msg.lower() or "disconnect" in msg.lower():
            display = "Camera connection lost — press Start Video to reconnect."
        else:
            display = f"Camera error: {msg}"
        self.status.showMessage(display)
        self.btn_start_video.setChecked(False)

        # Diagnose trigger-mode failures: the camera times out when no triggers arrive
        if self.chk_trigger.isChecked():
            from arduino_uploader import find_arduino_port
            port = find_arduino_port()
            if port is None:
                QMessageBox.warning(
                    self,
                    "No Frames — Arduino Disconnected",
                    "Frames stopped arriving in external trigger mode, and the "
                    "Arduino is no longer detected on any COM port.\n\n"
                    "→ Check the USB cable to the Arduino.\n"
                    "→ Reconnect the Arduino, then re-enable External Trigger."
                )
            else:
                QMessageBox.warning(
                    self,
                    "No Frames — No Triggers Received",
                    f"Frames stopped arriving in external trigger mode.\n\n"
                    f"Arduino is detected on {port}, but the camera receives "
                    f"no trigger pulses.\n\n"
                    "→ Check the wire from Arduino Pin 7 to Basler Line2.\n"
                    "→ Verify the Arduino is powered and running the sketch."
                )

    def _sync_params_from_camera(self):
        """Read current camera params and populate all GUI controls."""
        try:
            info = self.camera.get_info()
            if info:
                self.spn_exposure.blockSignals(True)
                self.spn_gain.blockSignals(True)
                self.spn_fps.blockSignals(True)
                self.cmb_format.blockSignals(True)
                self.spn_exposure.setValue(info["exposure_us"] / 1000.0)
                self.spn_gain.setValue(info["gain_db"])
                if not self.chk_trigger.isChecked():
                    self.spn_fps.setValue(info["frame_rate"])
                if info.get("pixel_format"):
                    self.cmb_format.setCurrentText(info["pixel_format"])
                self.spn_exposure.blockSignals(False)
                self.spn_gain.blockSignals(False)
                self.spn_fps.blockSignals(False)
                self.cmb_format.blockSignals(False)
                self.status.showMessage(
                    f"{info['model']}  SN:{info['serial']}  "
                    f"{info['width']}×{info['height']}  {info['pixel_format']}"
                )
        except Exception:
            pass

    def _auto_load_folder_calibration(self):
        """Start background calibration load for FolderMockCamera recordings.

        Streams dark TIFFs + main TIFFs in a background thread so the GUI stays
        responsive.  Disables 'Start SCOS' until calibration finishes.

        Calibration order:
          1. Stream dark_dir TIFFs  → dark_mean, dark_var
          2. Stream recording TIFFs → mean_bright → spIm → spVar (computed from scratch)
          Uses smoothingCoefficients.mat as spVar fallback if main_dir fails.
        """
        dark_dir  = self.camera.get_dark_dir()
        smoothing = self.camera.get_calibration_mat()
        # If smoothingCoefficients.mat exists, use it — avoids reading all main
        # TIFFs concurrently with playback, which saturates the disk.
        main_dir  = None if smoothing is not None else self.camera._recording_dir

        info    = self.camera.get_info()
        sat_cap = info.get("sat_capacity", None)
        self.processor.gain_db   = info.get("gain_db",   self.processor.gain_db)
        self.processor.bit_depth = info.get("bit_depth", 10)
        self._pending_mask_mat   = self.camera.get_mask_mat()

        self.btn_start_scos.setEnabled(False)
        self._calib_label.setText("Calibrating…")

        self._calib_thread = _CalibrationLoaderThread(
            self.processor, dark_dir, main_dir, smoothing,
            64.0, sat_cap, self.spn_window.value(), self,
        )
        self._calib_thread.done.connect(self._on_calibration_done)
        self._calib_thread.start()

    def _on_calibration_done(self, success: bool, msg: str):
        """Runs on the GUI thread when _CalibrationLoaderThread finishes."""
        self.btn_start_scos.setEnabled(True)

        if not success:
            self._calib_label.setText(f"Cal FAILED: {msg}")
            return

        # Load ROI mask from Mask.mat (fast — just reads one small file)
        mask_mat = getattr(self, "_pending_mask_mat", None)
        if mask_mat is not None:
            try:
                mat = scipy.io.loadmat(str(mask_mat))
                if "totMask" in mat:
                    self._mask = mat["totMask"].astype(bool)
                    self.processor.set_roi(self._mask)
                if "channels" in mat:
                    ch = mat["channels"][0, 0]
                    cy = float(ch["Centers"][0, 0])
                    cx = float(ch["Centers"][0, 1])
                    r  = float(ch["Radii"][0, 0])
                    self.image_widget.set_roi_circle(cx, cy, r)
            except Exception:
                pass

        self._calib_label.setText(f"Cal OK — {msg}")

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
        # Stop calibration thread first so it isn't writing to the processor
        # while the camera thread is also stopped.
        if self._calib_thread and self._calib_thread.isRunning():
            self._calib_thread.wait(5000)
            if self._calib_thread.isRunning():
                self._calib_thread.terminate()
        self._scos_worker.stop()
        self._scos_worker.wait(2000)
        self.camera.stop()
        self.camera.close()
        event.accept()
