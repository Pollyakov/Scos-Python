"""
Main application window.
Combines: live image, SCOS time-series plot, camera controls panel.
"""

import datetime
import json
import logging
import os
import time
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)

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
from processor import SCOSProcessor, GainTableError, shrink_mask_for_window
from core.session   import (BrightCalCollector, DarkCalCollector, State,
                            NORM_METHOD_MEAN, NORM_METHOD_PERCENTILE,
                            choose_norm_method, normalization_constant)
from core.recorder  import CALIBRATION_FILENAME, HDF5Recorder, write_calibration
from core.pipeline  import RealtimePipeline
from gui.image_widget import ImageWidget
from gui.plot_widget  import PlotWidget


class _CalibrationLoaderThread(QThread):
    """Background thread: streams dark TIFFs + computes spVar from main TIFFs."""
    done     = pyqtSignal(bool, str)   # (success, status message)
    progress = pyqtSignal(str)         # human-readable step description

    def __init__(self, processor, dark_dir, main_dir, smoothing_mat,
                 scale, window_size, parent=None):
        super().__init__(parent)
        self._proc    = processor
        self._dark    = dark_dir
        self._main    = main_dir        # NEW: compute spVar from raw frames
        self._smooth  = smoothing_mat   # fallback if main_dir is None
        self._scale   = scale
        self._window  = window_size

    def run(self):
        try:
            self._proc.load_calibration_mat(
                smoothing_mat=self._smooth,
                dark_dir=self._dark,
                main_dir=self._main,
                scale=self._scale,
                window_size=self._window,
                on_progress=self.progress.emit,
            )
            n_dark = len(list(self._dark.glob("*.tiff"))) if self._dark else 0
            src    = "computed from frames" if self._main else "loaded from mat"
            self.done.emit(True, f"dark:{n_dark}  spVar:{src}")
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



class MainWindow(QMainWindow):
    def __init__(self, camera=None, h5_replay=None):
        super().__init__()
        self.setWindowTitle("SCOS — Speckle Contrast Optical Spectroscopy")

        # State
        self._mask        = None   # full ROI circle — used for display and calibration
        self._scos_mask   = None   # shrunk by window//2+1 — used for κ² processing only
        self._start_time  = None
        self._frame_count = 0
        self._proc_times  = []   # rolling window of process() durations (ms)
        self._last_proc_label_time = 0.0
        self._last_logged_dropped_count = 0   # for change-triggered app.log warnings
        self._last_stats_time = 0.0
        self._last_display_time = 0.0
        self._calib_thread:   _CalibrationLoaderThread | None = None
        self._arduino_thread: _ArduinoUploadThread | None = None
        self._recorder:       HDF5Recorder | None = None
        self._roi_circ:       dict = {}

        # Session state machine
        self._state:           State       = State.IDLE
        self._bfi_norm:        float | None = None              # mean BFI over baseline window
        # How that constant was actually produced, for Params.normalizationMethod
        # — NORM_METHOD_MEAN or NORM_METHOD_PERCENTILE. Not the same as
        # _norm_type, which is the mode the operator picked in the GUI: in the
        # default "seconds" mode the recording's length decides, so the method
        # is not known until the run ends (see _finalize_normalization).
        self._bfi_norm_method: str          = NORM_METHOD_MEAN
        # Last timestamp seen in this run. This is `timeVec(end)` in the
        # reference, and it decides mean vs percentile at close.
        self._last_result_t:   float        = 0.0
        self._bfi_norm_buffer: list[tuple[float, float]] = []  # (t, bfi_raw) during MEASURING_INIT
        # Guard against a measurement that can never start because every
        # corrected kappa^2 is <= 0 (see _abort_on_invalid_k2).
        self._n_invalid_k2:        int  = 0
        # Frames this window has received, and the arrival index below which
        # they are stale — see _flush_stale_frames().
        self._frames_seen:         int  = 0
        self._skip_frames_until:   int  = 0
        self._invalid_k2_reported: bool = False
        self._norm_seconds:           float       = 5.0          # baseline window length (seconds)
        self._norm_type:              str         = "seconds"    # "seconds" | "pulsation"
        self._measurement_duration_s: float       = float('inf') # ∞ = run until Stop SCOS
        self._measuring_start_time:   float | None = None         # set when normalization ends
        self._arduino_debounce = QTimer(self)
        self._arduino_debounce.setSingleShot(True)
        self._arduino_debounce.setInterval(1000)  # 1 second
        self._arduino_debounce.timeout.connect(self._upload_arduino)

        # Calibration state (dark and bright are mutually exclusive)
        self._dark_cal_collector:      DarkCalCollector   | None = None
        self._bright_cal_collector:    BrightCalCollector | None = None
        self._dark_cal_trigger_was_on: bool = False
        # Output layout: the operator picks a parent folder once (remembered for
        # the lifetime of the window), and every run creates its own timestamped
        # session folder underneath it. Calibration, results and figure all land
        # in that one folder — see merged_worklist.md #8 and docs/session_tab.
        self._output_root:    Path | None = None
        self._session_folder: Path | None = None

        # Camera & processor
        self.camera    = camera if camera is not None else CameraThread()
        self.processor = SCOSProcessor()
        self._h5_replay = h5_replay          # not None → HDF5 replay mode
        self._scos_worker = RealtimePipeline(self.processor, n_workers=3, parent=self)
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

        self._last_fps_time = time.time()
        self._fps_count = 0


    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Camera Controls ---
        self.cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(self.cam_group)
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
        layout.addWidget(self.cam_group)

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
        self.scos_group = QGroupBox("SCOS")
        scos_layout = QVBoxLayout(self.scos_group)
        scos_layout.setSpacing(2)
        scos_layout.setContentsMargins(4, 8, 4, 4)

        self.spn_window = self._labeled_int_spin(
            scos_layout, "Window Size:", 3, 51, 7, step=2
        )
        self.spn_n1 = self._labeled_int_spin(
            scos_layout, "Dark Frames:", 10, 3000, 600, step=50
        )
        self.spn_n2 = self._labeled_int_spin(
            scos_layout, "Bright Frames:", 10, 3000, 600, step=50
        )
        self.spn_duration = self._labeled_spin(
            scos_layout, "Measuring duration (in minutes):", 0.0, 240.0, 0.0, 1, step=1.0
        )
        self.spn_duration.setSpecialValueText("∞")
        self.spn_duration.setToolTip(
            "0 = run until Stop SCOS is clicked  |  max 240 min (4 h)"
        )

        # Normalization type row
        norm_row = QHBoxLayout()
        norm_row.setContentsMargins(0, 0, 0, 0)
        norm_row.addWidget(QLabel("Norm. type:"))
        self.cmb_norm_type = QComboBox()
        self.cmb_norm_type.addItems(["Number of seconds", "Pulsation lower level"])
        norm_row.addWidget(self.cmb_norm_type)
        self.spn_norm_seconds = QSpinBox()
        self.spn_norm_seconds.setRange(1, 60)
        self.spn_norm_seconds.setValue(5)
        self.spn_norm_seconds.setSuffix(" s")
        norm_row.addWidget(self.spn_norm_seconds)
        scos_layout.addLayout(norm_row)

        self.spn_workers = self._labeled_int_spin(
            scos_layout, "Processing workers:", 1, 8, 3, step=1
        )
        _cores = os.cpu_count() or 4
        self.spn_workers.setToolTip(
            f"Parallel threads for κ² computation.\n"
            f"This machine has {_cores} logical cores.\n"
            f"Recommended: 1–{max(1, _cores - 2)}  (leave cores for GUI + camera)"
        )

        layout.addWidget(self.scos_group)

        # --- Action buttons (outside scos_group so they are never locked) ---
        self.btn_start_scos = QPushButton("Start SCOS")
        self.btn_start_scos.setCheckable(True)
        self.btn_start_scos.setEnabled(False)
        self.btn_start_scos.setStyleSheet("""
            QPushButton          { background:#2a7a2a; color:white; font-weight:bold;
                                   padding:4px; border-radius:3px; }
            QPushButton:checked  { background:#7a2a2a; }
            QPushButton:disabled { background:#444; color:#888; font-weight:normal; }
        """)
        layout.addWidget(self.btn_start_scos)

        self.chk_save_frames = QCheckBox("Save Frames")
        self.chk_save_frames.setChecked(False)
        layout.addWidget(self.chk_save_frames)

        self.btn_save = QPushButton("Save Data...")
        self.btn_save.setEnabled(False)
        layout.addWidget(self.btn_save)

        # --- Status indicator ---
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(2)
        status_layout.setContentsMargins(4, 8, 4, 4)

        # Top row: state name (left) + time remaining (right)
        state_row = QHBoxLayout()
        self._state_label = QLabel("IDLE")
        self._state_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #888888;"
        )
        self._time_left_label = QLabel("")
        self._time_left_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._time_left_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #00aaff;"
        )
        self._time_left_label.hide()
        state_row.addWidget(self._state_label)
        state_row.addWidget(self._time_left_label)
        status_layout.addLayout(state_row)

        # Second row: calibration / normalization progress
        self._calib_label = QLabel("")
        self._calib_label.setWordWrap(True)
        self._calib_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._calib_label.setStyleSheet("font-size: 11px; color: #aaaaaa;")
        status_layout.addWidget(self._calib_label)
        layout.addWidget(status_group)

        # --- Info labels ---
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(1)
        info_layout.setContentsMargins(4, 8, 4, 4)

        self._fps_label  = QLabel("FPS  : --")
        self._proc_label = QLabel("Proc : -- ms")
        for lbl in (self._fps_label, self._proc_label):
            lbl.setStyleSheet("font-weight: bold; color: #00cc44;")
        self._proc_label.setStyleSheet("font-weight: bold; color: #ffaa00;")

        self.lbl_dropped = QLabel("Dropped: 0")
        self.lbl_dropped.setStyleSheet("font-weight: bold; color: #ff5555;")

        self.lbl_size   = QLabel("Size : --")
        self.lbl_mean_i = QLabel("⟨I⟩  : --")
        self.lbl_p5     = QLabel("p5   : --")
        self.lbl_p95    = QLabel("p95  : --")
        self.lbl_kappa  = QLabel("κ²   : --")
        self.lbl_bfi    = QLabel("1/κ² : --")
        self.lbl_roi    = QLabel("ROI  : full frame")
        for lbl in (self._fps_label, self._proc_label, self.lbl_dropped,
                    self.lbl_size, self.lbl_mean_i, self.lbl_p5, self.lbl_p95,
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
            "norm_seconds":    lambda v: self.spn_norm_seconds.setValue(int(float(v))),
            "norm_type":       lambda v: self.cmb_norm_type.setCurrentIndex(
                                   0 if str(v) == "seconds" else 1),
            "measurement_duration_min": lambda v: self.spn_duration.setValue(float(v)),
            "n_workers":               lambda v: self.spn_workers.setValue(int(v)),
        }.items():
            if key in cfg:
                try:
                    apply_fn(cfg[key])
                except (ValueError, TypeError):
                    pass

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    _STATE_COLORS = {
        State.IDLE:           "#888888",
        State.PREVIEW:        "#aabbcc",
        State.DARK_CAL:       "#ffaa00",
        State.BRIGHT_CAL:     "#ffaa00",
        State.MEASURING_INIT: "#ffdd00",
        State.MEASURING:      "#00cc44",
        State.FINISHED:       "#00aaff",
        State.ERROR:          "#ff4444",
    }

    def _set_state(self, new_state: State) -> None:
        logger.info("State: %s → %s", self._state.name, new_state.name)
        self._state = new_state
        color = self._STATE_COLORS.get(new_state, "#888888")
        self._state_label.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {color}; padding: 2px;"
        )
        self._state_label.setText(new_state.name.replace("_", " "))
        # Lock ROI edits while worker threads are reading processor ROI
        # state concurrently in process() — dragging the circle mid-run is
        # a data race, not just a UX nuisance (see merged_worklist.md #3).
        self.image_widget.set_roi_locked(
            new_state in (State.MEASURING_INIT, State.MEASURING)
        )

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self):
        # Camera thread signals
        self.camera.display_ready.connect(self._on_display_frame)   # 30 FPS cap → GUI
        # Queued (GUI thread): FPS/intensity labels, calibration collectors,
        # raw-frame saving. SCOS intake is NOT here any more — see
        # _connect_pipeline_intake().
        self.camera.frame_ready.connect(self._on_scos_frame)
        self.camera.error.connect(self._on_camera_error)
        self.camera.warning.connect(self._on_camera_warning)

        # SCOS worker thread signals (results arrive on GUI thread via queued connection)
        self._scos_worker.result_ready.connect(self._on_scos_result)
        self._scos_worker.error_occurred.connect(self._on_scos_error)
        self._connect_pipeline_intake()

        if self._h5_replay is not None:
            self._h5_replay.result_ready.connect(self._on_scos_result)
            self._h5_replay.finished_replay.connect(self._on_h5_replay_finished)

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

        # Measurement duration
        self.spn_duration.valueChanged.connect(self._on_duration_changed)

        # Normalization type + seconds
        self.cmb_norm_type.currentIndexChanged.connect(self._on_norm_type_changed)
        self.spn_norm_seconds.valueChanged.connect(
            lambda v: setattr(self, "_norm_seconds", float(v))
        )

        # Save
        self.btn_save.clicked.connect(self._save_data)

    # ------------------------------------------------------------------
    # Pipeline intake wiring
    # ------------------------------------------------------------------

    def _connect_pipeline_intake(self) -> None:
        """Feed the camera's frames straight into the pipeline, off the GUI thread.

        DirectConnection means RealtimePipeline.on_frame() executes on the
        *camera* thread, so a full input queue blocks the grab loop — real
        backpressure, absorbed by Pylon's own 20-frame buffer — instead of
        piling frames up in Qt's unbounded queued-connection event queue while
        the GUI thread is busy (Implementation_Plan §2).
        """
        self.camera.frame_ready.connect(
            self._scos_worker.on_frame, Qt.ConnectionType.DirectConnection
        )
        self._scos_worker.overload_detected.connect(self._on_overload)

    def _disconnect_pipeline_intake(self) -> None:
        """Unhook the current pipeline before it is replaced or stopped.

        A stopped pipeline no longer drains its input queue, so a camera thread
        still connected to it would block for the full put timeout on every
        single frame. Must run before the old pipeline is torn down.
        """
        try:
            self.camera.frame_ready.disconnect(self._scos_worker.on_frame)
        except TypeError:
            pass   # not connected (e.g. _NullCamera in HDF5 replay mode)
        try:
            self._scos_worker.overload_detected.disconnect(self._on_overload)
        except TypeError:
            pass

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _toggle_video(self, checked: bool):
        if self._h5_replay is not None:
            self._toggle_video_h5(checked)
            return

        if checked:
            try:
                self.camera.pixel_format  = self.cmb_format.currentText()
                self.camera.exposure_us   = self.spn_exposure.value() * 1000
                self.camera.gain_db       = self.spn_gain.value()
                self.camera.frame_rate    = self.spn_fps.value()
                self.camera.trigger_mode  = "On" if self.chk_trigger.isChecked() else "Off"
                self.camera.trigger_delay = self.spn_trigger_delay.value()
                logger.info(
                    "Start Video — format=%s  exposure=%.1f ms  gain=%.1f dB  "
                    "fps=%.1f Hz  trigger=%s",
                    self.camera.pixel_format,
                    self.spn_exposure.value(),
                    self.camera.gain_db,
                    self.camera.frame_rate,
                    self.camera.trigger_mode,
                )
                self.camera.start_capture()
                self.btn_start_video.setText("Stop Video")
                self.btn_start_scos.setEnabled(True)
                self.status.showMessage("Video running")
                self._set_state(State.PREVIEW)
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
                logger.exception("Failed to start video: %s", e)
                self.btn_start_video.setChecked(False)
                QMessageBox.critical(self, "Camera Error", str(e))
        else:
            logger.info("Stop Video")
            # Close the intake gate before camera.stop(), which wait()s on the
            # camera thread with no timeout. Every route to stopping capture
            # passes through here — including _on_camera_error, which unchecks
            # Start Video directly without going through _toggle_scos.
            self._scos_worker.disable_intake()
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setEnabled(False)
            self.chk_trigger.setEnabled(True)   # re-enable in case it was disabled for mock-folder
            # Cancel any in-progress calibration gracefully
            if self._state == State.DARK_CAL:
                self._dark_cal_collector = None
                self._calib_label.setText("Dark cal cancelled")
            elif self._state == State.BRIGHT_CAL:
                self._bright_cal_collector = None
                self._calib_label.setText("Bright cal cancelled")
            self.camera.stop()
            self.btn_start_video.setText("Start Video")
            self.status.showMessage("Video stopped")
            self._set_state(State.IDLE)

    def _toggle_video_h5(self, checked: bool):
        """Start/stop HDF5 replay — no camera, no processor."""
        if checked:
            meta = self._h5_replay.get_metadata()
            self.spn_fps.setValue(meta.get("frame_rate_hz", 20.0))
            self.spn_exposure.setValue(meta.get("exposure_ms", 8.0))
            self.spn_gain.setValue(meta.get("gain_db", 8.0))
            self._start_time  = time.time()
            self._bfi_norm        = None
            self._bfi_norm_buffer = []
            self.plot_widget.reset()
            # Show a placeholder so the image panel isn't empty (no frames in HDF5)
            placeholder = np.full((700, 700), 512, dtype=np.uint16)
            self.image_widget.update_frame(placeholder)
            self._h5_replay.start_replay()
            self._set_state(State.MEASURING_INIT)
            self.btn_start_video.setText("Stop Replay")
            self.btn_start_scos.setEnabled(False)   # replay drives results directly
            self.chk_trigger.setEnabled(False)
            self.status.showMessage(f"Replaying: {self._h5_replay._path.name}")
        else:
            self._h5_replay.stop()
            self.btn_start_video.setText("Start Replay")
            self.status.showMessage("Replay stopped")
            self._set_state(State.IDLE)

    def _on_h5_replay_finished(self):
        """Called when H5ReplayThread exhausts the file (non-loop mode)."""
        self.btn_start_video.setChecked(False)
        self.btn_start_video.setText("Start Replay")
        self.status.showMessage("Replay finished")
        self._set_state(State.FINISHED)

    # Stylesheet applied to the Camera / SCOS group boxes while SCOS is running.
    # Qt's built-in disabled appearance is too subtle in dark themes, so we set
    # explicit colors that are unmistakably different from the active state.
    _LOCKED_GROUP_STYLE = """
        QGroupBox {
            color: #555555;
            border: 1px solid #333333;
        }
        QGroupBox::title { color: #555555; }
        QDoubleSpinBox, QDoubleSpinBox:disabled,
        QSpinBox,       QSpinBox:disabled {
            color: #484848;
            background-color: #1c1c1c;
            border: 1px solid #2e2e2e;
        }
        QComboBox, QComboBox:disabled {
            color: #484848;
            background-color: #1c1c1c;
            border: 1px solid #2e2e2e;
        }
        QCheckBox, QCheckBox:disabled { color: #484848; }
        QLabel                        { color: #505050; }
    """

    def _set_params_enabled(self, enabled: bool) -> None:
        """Lock / unlock all Camera and SCOS parameter inputs during a session."""
        for widget in (
            self.cmb_format,
            self.spn_exposure,
            self.spn_gain,
            self.spn_fps,
            self.spn_trigger_delay,
            self.chk_trigger,
            self.spn_window,
            self.spn_n1,
            self.spn_n2,
            self.spn_duration,
            self.cmb_norm_type,
            self.spn_norm_seconds,
            self.spn_workers,
        ):
            widget.setEnabled(enabled)

        # Apply / remove the explicit locked stylesheet so the visual change is
        # obvious even in dark themes where Qt's default disabled look is subtle.
        locked_ss = "" if enabled else self._LOCKED_GROUP_STYLE
        self.cam_group.setStyleSheet(locked_ss)
        self.scos_group.setStyleSheet(locked_ss)

    def _reset_start_button(self) -> None:
        """Put Start SCOS back to its un-pressed state after a refused run.

        blockSignals keeps setChecked() from re-entering _toggle_scos with
        checked=False, which would run the whole Stop branch against a session
        that never started.
        """
        self.btn_start_scos.blockSignals(True)
        self.btn_start_scos.setChecked(False)
        self.btn_start_scos.setText("Start SCOS")
        self.btn_start_scos.blockSignals(False)
        self.btn_save.setEnabled(True)
        self._set_params_enabled(True)

    def _toggle_scos(self, checked: bool):
        if checked:
            logger.info(
                "Start SCOS — window=%d  gain=%.1f dB  format=%s  fps=%.1f Hz",
                self.spn_window.value(), self.spn_gain.value(),
                self.cmb_format.currentText(), self.spn_fps.value(),
            )
            # Set processor params now so they're ready when measurement begins
            self.processor.window_size = self.spn_window.value()
            self.processor.gain_db     = self.spn_gain.value()
            fmt = self.cmb_format.currentText()
            self.processor.bit_depth   = int(fmt.replace("Mono", ""))
            # G[DU/e] is resolved here, before anything else changes state: a
            # camera that is not in the gain table must stop the run now, while
            # nothing has been started and there is nothing to unwind.
            if not self._prepare_gain():
                self._reset_start_button()
                return
            self.btn_start_scos.setText("Stop SCOS")
            self.btn_save.setEnabled(False)
            self._set_params_enabled(False)   # lock all parameter inputs
            # Recreate pipeline with the currently selected worker count.
            # Unhook the camera from the outgoing pipeline first — once it is
            # stopped nothing drains its queue, and a camera thread still
            # feeding it would stall on every frame.
            self._disconnect_pipeline_intake()
            self._scos_worker.stop()
            self._scos_worker.wait(2000)
            n_workers = self.spn_workers.value()
            self._scos_worker = RealtimePipeline(self.processor, n_workers=n_workers, parent=self)
            self._scos_worker.result_ready.connect(self._on_scos_result)
            self._scos_worker.error_occurred.connect(self._on_scos_error)
            self._connect_pipeline_intake()
            self._scos_worker.start()
            self._last_logged_dropped_count = 0
            self.lbl_dropped.setText("Dropped: 0")
            logger.info("Pipeline started with %d workers", n_workers)
            # Begin calibration sequence: dark cal → bright cal → measure
            self._start_dark_cal()
        else:
            logger.info("Stop SCOS")
            # Was this a real measurement, or an abandoned calibration? Only a
            # measurement gets finalized — cancelling during DARK_CAL/BRIGHT_CAL
            # has no results to write, so it must not pass through FINISHED.
            was_measuring = self._state in (State.MEASURING_INIT, State.MEASURING)
            self._scos_worker.disable_intake()   # camera stops feeding immediately
            self._scos_mask            = None
            self._measuring_start_time = None
            self._time_left_label.hide()
            # Cancel any in-progress calibration
            if self._state == State.DARK_CAL:
                self._dark_cal_collector = None
                self._calib_label.setText("Dark cal cancelled")
                # Restore trigger if it was disabled for dark cal
                if self._dark_cal_trigger_was_on:
                    self.camera.set_trigger(True, self.spn_trigger_delay.value())
            elif self._state == State.BRIGHT_CAL:
                self._bright_cal_collector = None
                self._calib_label.setText("Bright cal cancelled")
            self.btn_start_scos.setText("Start SCOS")
            self.btn_save.setEnabled(True)
            self._set_params_enabled(True)    # restore all parameter inputs
            # Bound unconditionally: the closing message below is guarded by the
            # same `was_measuring` flag, but leaving this to the guard alone
            # makes an unbound-local NameError one edit away.
            session_folder = None
            if was_measuring:
                self._set_state(State.FINISHED)
                session_folder = self._session_folder   # kept for the closing message
                self._finish_session()
            self._stop_recorder()
            if was_measuring:
                self.status.showMessage(
                    f"Session finished → {session_folder}"
                    if session_folder is not None
                    else "Session finished (not saved — no output folder)"
                )
            self._set_state(State.PREVIEW)

    def _finish_session(self) -> None:
        """Single finalization point for a completed measurement.

        Reached from exactly one place — the Stop branch of _toggle_scos — which
        both the Stop SCOS button and the duration auto-stop funnel through, so
        a manual stop and an automatic one finalize identically.

        The recorder is deliberately still OPEN here: the caller runs
        _stop_recorder() only after this returns. Work that must write into the
        results file belongs in this method, not after it — task 9's design
        buffers raw BFi during the session and writes the final rBFi once at
        close, and task 10 supplies the normalization constant that write uses.
        Tasks 11 (laser-off popup + intensity check) and 12 (save the figure)
        hook in here too. See merged_worklist.md #8-#12.
        """
        logger.info("Session finished — state FINISHED")
        if self._session_folder is not None:
            logger.info("Session output folder: %s", self._session_folder)
        self._finalize_normalization()
        self._write_rbfi()
        # Released so the next run creates its own folder rather than writing
        # into this one. The parent (_output_root) is kept, so the operator is
        # asked for a location only once per window. The status message is left
        # to the caller: _stop_recorder() runs after this and would overwrite
        # anything set here.
        self._session_folder = None

    def _start_recorder(self) -> None:
        """Open the results recorder in this session's folder.

        Deliberately shows no dialog. It used to prompt for a second folder
        here — mid-measurement, after calibration had already finished — which
        stalled the GUI at the worst possible moment and let a session's files
        be scattered across two directories. The folder is now chosen once, up
        front, in _start_dark_cal (merged_worklist.md #8).
        """
        if self._session_folder is None:
            # No folder was chosen at Start SCOS — measure without auto-save,
            # matching the previous behaviour when the operator cancelled the
            # dialog. Data still plots live; it is just not written to disk.
            logger.warning("No session folder — SCOS runs without auto-save")
            return
        # Name and contents come from `docs/session_tab`; the spelling is hers.
        path = self._session_folder / "rBfi_results.h5"
        # Engineering provenance — kept out of Params on purpose, see recorder.
        meta = {
            "camera_sn":      str(self.processor.camera_sn or ""),
            "camera_model":   str(self.camera.get_info().get("model", "")),
            "gain_du_per_e":  float(self.processor.gain_du_per_e or 0.0),
            "gain_source":    str(self.processor.gain_source or ""),
        }
        # Params: exactly the fields the supervisor listed (2026-09-23), named
        # as she named them. satCapacity is deliberately absent. The three
        # normalization fields are filled in by write_rbfi() at close, because
        # the constant is not known until the recording's length is final.
        params = {
            "frameRate":    float(self.spn_fps.value()),
            "exposureTime": float(self.spn_exposure.value()),       # ms
            "gain":         float(self.spn_gain.value()),           # dB
            "windowSize":   int(self.spn_window.value()),
            "ROI":          [float(self._roi_circ.get("cx", -1)),
                             float(self._roi_circ.get("cy", -1)),
                             float(self._roi_circ.get("r",  -1))],
            "bitDepth":     int(self.cmb_format.currentText().replace("Mono", "")),
        }
        self._recorder = HDF5Recorder(path, meta, params)
        self.status.showMessage(f"Recording → {path}")

    def _finalize_normalization(self) -> None:
        """Re-pick the baseline statistic now that the recording's length is known.

        Worklist task 10. The reference script decides between the mean and the
        5th percentile of the baseline window on `timeVec(end) > 120`, which it
        can do because it normalizes once, offline, after everything is
        recorded. Here the curve has to be drawn live, so a provisional
        constant is computed the moment the window closes and corrected here.

        Only the divisor changes: the window is the same first `norm_seconds`
        of the recording either way, and `_bfi_norm_buffer` still holds it.
        Nothing is recomputed from the frames.

        The plot is rescaled rather than redrawn. What was plotted is
        `bfi / provisional`; multiplying by `provisional / final` turns it into
        `bfi / final` with one pass over a list that is already in memory,
        which matters for a multi-hour recording.
        """
        # Whatever else happens below, the session ends with the curve fully
        # drawn: up to a second of points can still be sitting in the plot's
        # buffer, and task 12 saves this figure to a file.
        self.plot_widget.render_now()

        if not self._bfi_norm or not self._bfi_norm_buffer:
            return                      # never normalized; _write_rbfi says so

        method = choose_norm_method(
            duration_s       = self._last_result_t,
            force_percentile = self._norm_type == "pulsation",
        )
        if method == self._bfi_norm_method:
            logger.info(
                "Recording is %.1f s — normalization stays %s, constant %.6g",
                self._last_result_t, method, self._bfi_norm,
            )
            return

        previous        = self._bfi_norm
        previous_method = self._bfi_norm_method
        try:
            final = normalization_constant(
                [b for _, b in self._bfi_norm_buffer], method)
        except ValueError:
            # Cannot happen if `previous` was computed from the same values,
            # but a bad constant here would divide the whole results file.
            logger.exception("Could not re-compute the normalization constant; "
                             "keeping %s = %.6g", self._bfi_norm_method, previous)
            return
        if not final or not np.isfinite(final):
            logger.error("Re-computed constant is %r — keeping %s = %.6g",
                         final, self._bfi_norm_method, previous)
            return

        self._bfi_norm        = final
        self._bfi_norm_method = method
        self.plot_widget.rescale(previous / final)
        logger.info(
            "Recording is %.1f s — normalization switched to %s: "
            "constant %.6g (was %.6g by %s), plot rescaled by %.6g",
            self._last_result_t, method, final, previous, previous_method,
            previous / final,
        )

    def _write_rbfi(self) -> None:
        """Finalize the results file: rBFi plus the normalization fields.

        Runs inside _finish_session(), while the recorder is still open. A
        session that never reached MEASURING has no normalization constant, so
        there is nothing to normalize by and the file keeps raw `bfi` only —
        the same state a crash leaves behind, which the supervisor accepted.
        """
        if self._recorder is None:
            return
        if not self._bfi_norm:
            logger.warning(
                "No normalization constant (session never left MEASURING_INIT) "
                "— results file keeps raw BFi with no rBFi"
            )
            return
        try:
            self._recorder.write_rbfi(
                norm_constant  = self._bfi_norm,
                method         = self._bfi_norm_method,
                window_seconds = self._norm_seconds,
            )
            logger.info("rBFi written — constant=%.6g method=%s window=%.0f s",
                        self._bfi_norm, self._bfi_norm_method, self._norm_seconds)
        except Exception:
            # Never let the finalization step lose the data already on disk.
            logger.exception("Could not write rBFi; raw BFi is still in the file")

    def _stop_recorder(self) -> None:
        if self._recorder is None:
            return
        self._recorder.close()
        n = self._recorder.n_points
        path = self._recorder.path
        self._recorder = None
        self.status.showMessage(f"Saved {n} points → {path}")

    # ------------------------------------------------------------------
    # Dark calibration
    # ------------------------------------------------------------------

    def _start_dark_cal(self):
        """
        Step 1 of the automatic calibration sequence (triggered by Start SCOS):
          1. Prompt to turn off the laser.
          2. Prompt for an output folder (remembered for the session).
          3. Switch camera to internal trigger (no Arduino upload side-effect).
          4. Collect N1 frames via frame_ready → _on_scos_frame.
          5. _finish_dark_cal() fires automatically when N1 frames are in.
        """
        # Step 1: ask user to turn off the laser
        reply = QMessageBox.question(
            self,
            "Calibration — Step 1 of 2: Dark Frames",
            "Please turn off the laser.\n\n"
            "Click OK when the laser is off and the measurement area is dark.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            # User cancelled — revert Start SCOS button
            self.btn_start_scos.blockSignals(True)
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setText("Start SCOS")
            self.btn_start_scos.blockSignals(False)
            self.btn_save.setEnabled(True)
            return

        # Step 2: choose (or reuse) the parent folder, then create this run's
        # session folder inside it. Everything this session produces —
        # calibration, results, figure — goes in there and nowhere else, so a
        # second run can never mix its files into the first run's output.
        # Clear any folder left over from a previous run *before* creating the
        # new one: if this run is cancelled below, a stale path must not remain
        # and silently collect the next run's calibration files.
        self._session_folder = None
        if self._output_root is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose folder to save this session's results"
            )
            if not folder:
                self.btn_start_scos.blockSignals(True)
                self.btn_start_scos.setChecked(False)
                self.btn_start_scos.setText("Start SCOS")
                self.btn_start_scos.blockSignals(False)
                self.btn_save.setEnabled(True)
                return
            self._output_root = Path(folder)

        session_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_folder = self._output_root / f"scos_{session_ts}"
        try:
            session_folder.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            # An unwritable or full drive must stop the run here, before any
            # frames are collected — not silently produce an unsaved session.
            logger.exception("Could not create session folder %s", session_folder)
            QMessageBox.critical(
                self, "Cannot Create Session Folder",
                f"Could not create\n{session_folder}\n\n{exc}\n\n"
                "Choose a different location and start again.",
            )
            self._output_root = None   # force a re-pick on the next attempt
            self._reset_start_button()
            return
        self._session_folder = session_folder
        logger.info("Session folder created: %s", session_folder)
        self.status.showMessage(f"Session folder: {session_folder}")

        # Step 3: disable external trigger without triggering Arduino upload
        self._dark_cal_trigger_was_on = self.chk_trigger.isChecked()
        self.camera.set_trigger(False, self.spn_trigger_delay.value())

        # In playback there is no laser to switch off, so the mock camera
        # switches folders instead (todo D6). Nothing to do for a real camera.
        self._set_playback_source("dark")
        self._flush_stale_frames("the laser was switched off")

        # Step 4: start collecting
        n1 = self.spn_n1.value()
        self._dark_cal_collector = DarkCalCollector(n1, self.spn_window.value())
        self._calib_label.setText(f"Dark cal: 0 / {n1}")
        self._set_state(State.DARK_CAL)

    def _set_playback_source(self, source: str) -> None:
        """Ask a mock-folder camera to play its dark folder, or the recording.

        A no-op for every other camera: a real Basler has one stream and a
        real laser, and `--mock-tiff` has no dark frames to offer.
        """
        setter = getattr(self.camera, "set_playback_source", None)
        if setter is None:
            return
        if setter(source):
            logger.info("Playback source \u2192 %s", source)

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
            self.btn_start_scos.blockSignals(True)
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setText("Start SCOS")
            self.btn_start_scos.blockSignals(False)
            self.btn_save.setEnabled(True)
            self._set_state(State.PREVIEW)
            return

        n_collected = self._dark_cal_collector.n_collected
        logger.info("Dark calibration complete — %d frames collected", n_collected)
        # Store in processor and rebuild float32 crops used in process()
        self.processor.dark_mean = dark_mean
        self.processor.dark_var  = dark_var
        if self._mask is not None:
            self.processor.set_roi(self._mask)

        # One calibration file per session, holding both kinds (supervisor,
        # 2026-09-23). Key names are the ones used throughout processor.py and
        # the MATLAB reference, so the arrays are recognisable on her side.
        if self._session_folder is not None:
            cal_path = self._session_folder / CALIBRATION_FILENAME
            write_calibration(
                cal_path, "dark",
                {
                    "mean_dark": dark_mean,   # per-pixel temporal mean [DU]
                    "var_dark":  dark_var,    # per-pixel variance, spatially filtered [DU²]
                    # The ROI mask has no other home now that the results file
                    # carries no calibration group; it belongs with the arrays
                    # it was applied to.
                    "mask":      self._mask,
                },
                {"n_frames":    n_collected,
                 "window_size": self._dark_cal_collector.window_size},
            )
            self._calib_label.setText(
                f"Dark cal OK — {n_collected} frames, saved {cal_path.name}"
            )
        else:
            self._calib_label.setText(f"Dark cal OK — {n_collected} frames (not saved)")

        # Back to the recording before _start_bright_cal() opens its modal
        # prompt: frames keep arriving while a dialog is up, and the first
        # bright ones would otherwise still be dark.
        self._set_playback_source("main")

        # Restore trigger to whatever the user had before
        if self._dark_cal_trigger_was_on:
            self.camera.set_trigger(True, self.spn_trigger_delay.value())

        # Change state to PREVIEW *before* nulling the collector and before
        # _start_bright_cal() shows its dialog — otherwise frames arriving
        # during the dialog hit the DARK_CAL branch with a None collector.
        self._set_state(State.PREVIEW)
        self._dark_cal_collector = None
        # Automatically move to bright calibration
        self._start_bright_cal()

    def _start_bright_cal(self):
        """
        Step 2 of the automatic calibration sequence (called automatically by
        _finish_dark_cal):
          1. Prompt to turn on the laser and remove the subject.
          2. Collect N2 frames via frame_ready → _on_scos_frame.
          3. _finish_bright_cal() fires automatically when N2 frames are in,
             then immediately starts SCOS measurement.

        External trigger is NOT changed — the laser is on and the camera should
        stay synchronised with whatever trigger mode the user has set.
        """
        reply = QMessageBox.question(
            self,
            "Calibration — Step 2 of 2: Bright Frames",
            "Please turn on the laser and remove the subject from the measurement area.\n\n"
            "Click OK when the laser is on and the area is clear.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            # User cancelled — revert Start SCOS button
            self.btn_start_scos.blockSignals(True)
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setText("Start SCOS")
            self.btn_start_scos.blockSignals(False)
            self.btn_save.setEnabled(True)
            self._set_state(State.PREVIEW)
            return

        self._flush_stale_frames("the laser was switched back on")

        n2 = self.spn_n2.value()
        self._bright_cal_collector = BrightCalCollector(n2, self.spn_window.value())
        self._calib_label.setText(f"Bright cal: 0 / {n2}")
        self._set_state(State.BRIGHT_CAL)

    def _finish_bright_cal(self):
        """
        Called (on GUI thread) when N2 frames have been collected.
        Computes sp_im and bright_var, saves a .mat file, then immediately
        starts SCOS measurement — no user action required.
        """
        n_collected = self._bright_cal_collector.n_collected
        try:
            sp_im, bright_var = self._bright_cal_collector.result(
                dark_mean=self.processor.dark_mean
            )
        except RuntimeError as exc:
            QMessageBox.critical(self, "Bright Calibration Error", str(exc))
            self._bright_cal_collector = None
            self.btn_start_scos.blockSignals(True)
            self.btn_start_scos.setChecked(False)
            self.btn_start_scos.setText("Start SCOS")
            self.btn_start_scos.blockSignals(False)
            self.btn_save.setEnabled(True)
            self._set_state(State.PREVIEW)
            return

        logger.info("Bright calibration complete — %d frames collected", n_collected)
        self.processor.bright_var = bright_var
        if self._mask is not None:
            self.processor.set_roi(self._mask)

        if self._session_folder is not None:
            cal_path = self._session_folder / CALIBRATION_FILENAME
            write_calibration(
                cal_path, "bright",
                {
                    "spIm":  sp_im,       # mean bright image minus dark [DU]
                    "spVar": bright_var,  # local spatial variance of spIm [DU²]
                },
                {"n_frames":    n_collected,
                 "window_size": self._bright_cal_collector.window_size},
            )
            self._calib_label.setText(
                f"Cal OK — dark+bright done, saved {cal_path.name}"
            )
        else:
            self._calib_label.setText(f"Cal OK — dark+bright done ({n_collected} bright frames)")

        self._bright_cal_collector = None

        # Calibration complete — initialise measurement state *before*
        # _start_recorder() shows its file dialog, so frames arriving during
        # the dialog go to the SCOS branch instead of the BRIGHT_CAL branch
        # with a None collector.
        logger.info("Starting SCOS measurement after calibration")
        self._start_time          = time.time()
        self._bfi_norm            = None
        self._bfi_norm_method     = NORM_METHOD_MEAN
        self._bfi_norm_buffer     = []
        self._last_result_t       = 0.0
        self._n_invalid_k2        = 0
        self._invalid_k2_reported = False
        self._measuring_start_time = None  # set later when normalization ends

        # Shrink the ROI mask so no κ² pixel has its filter window straddle the
        # ROI boundary.  Keep self._mask intact for display and intensity stats.
        w = self.spn_window.value()
        if self._mask is not None:
            self._scos_mask = shrink_mask_for_window(self._mask, w)
            removed = int(self._mask.sum()) - int(self._scos_mask.sum())
            logger.info("ROI mask shrunk by %d px (window=%d) — %d px removed from edge",
                        w // 2 + 1, w, removed)
            self.processor.set_roi(self._scos_mask)
        else:
            self._scos_mask = None

        self.plot_widget.reset()
        self._set_state(State.MEASURING_INIT)
        # Open the intake gate *before* _start_recorder()'s modal folder dialog.
        # Frames captured while that dialog is up are now processed on the
        # camera and worker threads and timestamped at capture, instead of
        # being mis-timed (or lost) by a stalled GUI thread.
        scos_mask = self._scos_mask if self._scos_mask is not None else self._mask
        if scos_mask is None:
            logger.warning("No ROI mask available — SCOS intake not started")
        else:
            self._scos_worker.enable_intake(scos_mask)
        self._start_recorder()

    def keyPressEvent(self, event):
        """Press 'v' to toggle External Trigger (and trigger Arduino upload)."""
        if event.key() == Qt.Key.Key_V:
            self.chk_trigger.setChecked(not self.chk_trigger.isChecked())
        else:
            super().keyPressEvent(event)

    def _on_duration_changed(self, minutes: float) -> None:
        """Convert spinbox value (minutes) to internal seconds; 0 means infinite."""
        self._measurement_duration_s = float('inf') if minutes == 0.0 else minutes * 60.0
        logger.debug("Measurement duration → %s",
                     "∞" if minutes == 0.0 else f"{minutes:.1f} min")

        # Protocol rule: short recordings (≤ 1 min) should use pulsation lower level.
        # Only prompt if currently on "Number of seconds" and duration is finite and short.
        if 0.0 < minutes <= 1.0 and self._norm_type == "seconds":
            reply = QMessageBox.question(
                self,
                "Short Recording Detected",
                "The recording duration is 1 minute or less.\n\n"
                "The protocol recommends using \"Pulsation lower level\" "
                "normalization for short recordings.\n\n"
                "Switch to \"Pulsation lower level\"?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.cmb_norm_type.setCurrentIndex(1)   # triggers _on_norm_type_changed

    def _on_norm_type_changed(self, index: int) -> None:
        """Show/hide the seconds spinbox based on normalization type selection."""
        is_seconds = (index == 0)
        self.spn_norm_seconds.setVisible(is_seconds)
        self._norm_type = "seconds" if is_seconds else "pulsation"
        logger.debug("Normalization type → %s", self._norm_type)

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
        logger.info(
            "Scheduling Arduino upload — exposure=%.1f ms  fps=%.1f Hz",
            exposure_ms, frame_rate_hz,
        )
        self._arduino_thread = _ArduinoUploadThread(exposure_ms, frame_rate_hz, self)
        self._arduino_thread.progress.connect(self.status.showMessage)
        self._arduino_thread.done.connect(self._on_arduino_done)
        self._arduino_thread.start()
        self.chk_trigger.setEnabled(False)
        self.status.showMessage("Arduino: connecting…")

    def _on_arduino_done(self, ok: bool, msg: str):
        if ok:
            logger.info("Arduino upload succeeded: %s", msg)
        else:
            logger.error("Arduino upload failed: %s", msg)
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
        if self._state in (State.MEASURING_INIT, State.MEASURING):
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

    # A frame may already be in flight when the counter is read, so the cutoff
    # is nudged past it. Waiting two frames too long costs 50 ms at 40 Hz;
    # accepting one frame from before the laser changed corrupts a calibration.
    _FLUSH_MARGIN_FRAMES = 2

    def _flush_stale_frames(self, reason: str) -> None:
        """Ignore frames captured before the lighting changed.

        The calibration collectors run on the GUI thread behind a queued
        connection. When the camera outruns this handler — and at 40 Hz with
        per-frame percentiles over 2.4 Mpx it does — a backlog builds up in
        Qt's event queue. Measured on the lab recording: 60 to 130 frames,
        two to three seconds' worth.

        Every one of those was captured before the operator clicked OK. Without
        this, the dark collector's first frames are laser-on and the bright
        collector's are laser-off, which is not a playback quirk: on the rig the
        same backlog puts pre-laser frames into the bright calibration. It was
        found in playback only because there the two sets differ so plainly —
        60 of 60 "bright" frames came out dark.

        The camera counts what it has handed to Qt and this window counts what
        it has received; the difference is the backlog, so everything up to the
        camera's current count is dropped.
        """
        emitted = getattr(self.camera, "frames_emitted", None)
        if emitted is None:
            return                      # replay stub: no live stream to flush
        self._skip_frames_until = emitted + self._FLUSH_MARGIN_FRAMES
        backlog = max(0, self._skip_frames_until - self._frames_seen)
        if backlog:
            logger.info("Flushing %d frame(s) captured before %s", backlog, reason)
            self._calib_label.setText(f"Discarding {backlog} buffered frames…")

    def _to_du(self, frame: np.ndarray) -> np.ndarray:
        """Convert a raw frame to the digital units `process()` computes in.

        `process()` divides by `processor.scale` before doing anything, so a
        calibration array built from undivided frames is wrong by that factor.
        With a real camera `scale` is 1 and this returns the frame untouched;
        it is only ever other than 1 for the Pylon-Viewer TIFFs replayed by
        `--mock-folder`, which store 10-bit data left-justified in uint16.

        Getting this wrong is not subtle but it is silent: a `dark_mean` 64x
        too large made the mean ROI intensity come out at -7872 DU.
        """
        scale = getattr(self.processor, "scale", 1.0)
        if not scale or scale == 1.0:
            return frame
        return frame.astype(np.float64) / float(scale)

    def _on_scos_frame(self, frame: np.ndarray, t_capture: float):
        """Runs on GUI thread (queued signal from camera thread) — every frame.

        GUI-owned per-frame work only: labels, calibration collectors and the
        raw-frame save. SCOS intake happens on the camera thread instead (see
        _connect_pipeline_intake), so this handler falling behind can no longer
        delay or mis-timestamp a measurement frame. `t_capture` is the
        monotonic capture time; the measurement path uses it, and it is
        accepted here so both connections share one signal signature.
        """
        self._frames_seen += 1
        if self._frames_seen < self._skip_frames_until:
            return      # captured before the lighting changed — see _flush_stale_frames

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
            # Same units as everything else on screen and in the file: these
            # labels say "DU", and in playback the raw frame is 64x that.
            pixels = self._to_du(frame)[self._mask].astype(np.float64)
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
        if self._state == State.DARK_CAL:
            if self._dark_cal_collector is None:
                return   # guard: frame arrived during state transition
            self._dark_cal_collector.add_frame(self._to_du(frame))
            n       = self._dark_cal_collector.n_collected
            n_total = self._dark_cal_collector.n_target
            self._calib_label.setText(f"Dark cal: {n} / {n_total}")
            if self._dark_cal_collector.done:
                self._finish_dark_cal()
            return

        if self._state == State.BRIGHT_CAL:
            if self._bright_cal_collector is None:
                return   # guard: frame arrived during state transition
            self._bright_cal_collector.add_frame(self._to_du(frame))
            n       = self._bright_cal_collector.n_collected
            n_total = self._bright_cal_collector.n_target
            self._calib_label.setText(f"Bright cal: {n} / {n_total}")
            if self._bright_cal_collector.done:
                self._finish_bright_cal()
            return

        if self._state not in (State.MEASURING_INIT, State.MEASURING) or self._mask is None:
            return

        # NOTE: the frame is NOT submitted for processing here — the camera
        # thread already did that via RealtimePipeline.on_frame().

        # Save raw frame to HDF5 if requested. Still synchronous on the GUI
        # thread (gzip and all) — moving it to its own thread is task 14.
        if self._recorder is not None and self.chk_save_frames.isChecked():
            self._recorder.append_frame(frame)

    # Grace period added to the normalization window before the all-negative
    # kappa^2 guard fires. Long enough that a slow first second cannot trip it,
    # short enough that the operator is not left staring at an empty plot.
    _INVALID_K2_GRACE_S = 2.0
    # ...and a floor on how many results must have arrived, so one stray sample
    # with a late timestamp cannot abort a run on its own.
    _INVALID_K2_MIN_SAMPLES = 10

    def _abort_on_invalid_k2(self, t: float) -> bool:
        """Stop a measurement whose corrected κ² is never positive.

        BFi is 1/κ², so a non-positive corrected κ² yields no value at
        all. If *every* frame is like that, `_bfi_norm_buffer` stays empty, the
        normalization constant is never computed, and MEASURING_INIT never
        advances to MEASURING. Before this guard the app just sat there: an
        empty plot, "Normalizing - 22.3 / 5 s" frozen on screen, and a results
        file that is schema-valid but all NaN with no rBFi. The only trace was
        one WARNING in app.log, which nobody reads during a session.

        The cause is nearly always a dark calibration taken with light on the
        sensor: dark_var then carries the live signal's variance, and
        `var_im - G*mean - spVar - dark_var - 1/12` is negative everywhere.

        Returns True when it has aborted the run, so the caller stops
        processing this result.
        """
        self._n_invalid_k2 += 1
        if (self._invalid_k2_reported
                or self._bfi_norm_buffer                       # some frames were usable
                or t < self._norm_seconds + self._INVALID_K2_GRACE_S
                or self._n_invalid_k2 < self._INVALID_K2_MIN_SAMPLES):
            return False

        self._invalid_k2_reported = True
        logger.error(
            "Corrected kappa^2 was <= 0 for all %d results in the first %.1f s "
            "— no BFi can be computed; aborting the measurement",
            self._n_invalid_k2, t,
        )
        folder = self._session_folder
        # Stop first, show the dialog second. Stopping moves the state out of
        # MEASURING_INIT, so results already queued behind this one are dropped
        # by the guard at the top of _on_scos_result instead of stacking more
        # dialogs up behind this modal one.
        self.btn_start_scos.setChecked(False)   # triggers _toggle_scos(False)
        self._calib_label.setText("Aborted — corrected κ² ≤ 0")
        where = (f"saved in\n{folder}\n\n" if folder is not None
                 else "not saved.\n\n")
        QMessageBox.critical(
            self, "Corrected κ² Is Negative",
            f"Every frame in the first {t:.0f} seconds has a corrected κ² "
            f"of zero or less, so no blood-flow value can be computed and the "
            f"measurement cannot start.\n\n"
            "This almost always means light reached the sensor during the dark "
            "calibration — the laser still on, room lights on, or OK clicked "
            f"before the laser went out.\n\n"
            "The run has been stopped and the raw data collected so far is "
            + where
            + "Make sure the measurement area is dark, then press Start SCOS "
            "again to recalibrate.",
        )
        return True

    def _on_scos_result(self, t: float, k2_raw: float, k2_corr: float,
                        mean_i: float, proc_ms: float):
        """Receives SCOS result from the worker thread — runs on the GUI thread."""
        if self._state not in (State.MEASURING_INIT, State.MEASURING):
            return

        # Elapsed time since normalization ended (None while still in MEASURING_INIT)
        elapsed_measuring = (
            time.time() - self._measuring_start_time
            if self._measuring_start_time is not None else None
        )

        # Auto-stop when user-set duration is reached (only after normalization)
        if elapsed_measuring is not None and elapsed_measuring >= self._measurement_duration_s:
            logger.info("Measurement duration reached (%.1f min) — auto-stopping",
                        self._measurement_duration_s / 60.0)
            self.btn_start_scos.setChecked(False)   # triggers _toggle_scos(False)
            return

        self._proc_times.append(proc_ms)
        if len(self._proc_times) > 100:
            self._proc_times.pop(0)

        now = time.time()
        if (now - self._last_proc_label_time) >= 1.0:
            avg_ms = sum(self._proc_times) / len(self._proc_times)
            self._proc_label.setText(f"Proc: {avg_ms:.0f} ms (last: {proc_ms:.0f} ms)")
            self._last_proc_label_time = now
            # Dropped-frame counter — visible in the GUI at all times; a new
            # log warning is only emitted when the count actually changes, so
            # a stalled pipeline doesn't spam app.log once per second.
            dropped = self._scos_worker.dropped_count
            self.lbl_dropped.setText(f"Dropped: {dropped}")
            if dropped != self._last_logged_dropped_count:
                logger.warning(
                    "SCOS pipeline overloaded — %d frame(s) dropped from the "
                    "input queue so far", dropped,
                )
                self._last_logged_dropped_count = dropped
            # Time-remaining indicator — only after normalization ends and duration is finite
            if self._measurement_duration_s < float('inf') and elapsed_measuring is not None:
                remaining_s = max(0.0, self._measurement_duration_s - elapsed_measuring)
                mins = int(remaining_s // 60)
                secs = int(remaining_s % 60)
                self._time_left_label.setText(f"⏱ {mins}:{secs:02d} remaining")
                self._time_left_label.show()

        self._last_result_t = t

        bfi_raw = 1.0 / k2_corr if k2_corr > 0 else None
        if bfi_raw is None and self._state == State.MEASURING_INIT:
            if self._abort_on_invalid_k2(t):
                return
        if bfi_raw is not None:
            if self._state == State.MEASURING_INIT:
                self._bfi_norm_buffer.append((t, bfi_raw))   # collect; don't plot yet
                # Live countdown in the status-bar permanent label
                remaining = max(0.0, self._norm_seconds - t)
                self._calib_label.setText(
                    f"Normalizing — {t:.1f} / {self._norm_seconds:.0f} s  ({remaining:.1f} s left)"
                )
                if t >= self._norm_seconds and self._bfi_norm_buffer:
                    # Provisional constant. Which statistic is correct depends
                    # on how long the recording turns out to be, and it has
                    # barely started — so unless the operator forced the
                    # percentile, assume the long-recording rule for now and
                    # let _finalize_normalization() correct it at the end.
                    self._bfi_norm_method = (
                        NORM_METHOD_PERCENTILE if self._norm_type == "pulsation"
                        else NORM_METHOD_MEAN)
                    bfi_values = [b for _, b in self._bfi_norm_buffer]
                    self._bfi_norm = normalization_constant(
                        bfi_values, self._bfi_norm_method)
                    logger.info(
                        "Normalization window closed at %.1f s — provisional "
                        "constant %.6g by %s over %d points",
                        t, self._bfi_norm, self._bfi_norm_method, len(bfi_values),
                    )
                    self._measuring_start_time = time.time()   # timer starts here
                    # Add normalization window to plot retroactively, already normalized
                    for t_buf, bfi_buf in self._bfi_norm_buffer:
                        self.plot_widget.append(t_buf, bfi_buf / self._bfi_norm)
                    self._calib_label.setText("Normalized ✓")
                    self._set_state(State.MEASURING)
            elif self._state == State.MEASURING:
                self.plot_widget.append(t, bfi_raw / self._bfi_norm)
            else:
                self.plot_widget.append(t, bfi_raw)   # fallback (e.g. h5 replay edge case)

        self.lbl_kappa.setText(f"κ²   : {k2_corr:.5f}")
        self.lbl_bfi.setText(f"1/κ² : {bfi_raw:.2f}" if bfi_raw else "1/κ²: --")
        if self._recorder is not None:
            self._recorder.append(t, k2_raw, k2_corr, mean_i)

    def _on_overload(self, depth: int):
        """Input queue crossed 80 % full — emitted once per overload episode.

        At this point capture is already being throttled by the blocking
        intake, which is the safe behaviour; this is the operator's warning
        that the machine is not keeping up. The queue-fill bar and the
        four-option "what should I give up?" dialog are task 18.
        """
        msg = (
            f"SCOS overload — input queue {depth}/{self._scos_worker.queue_maxsize} "
            f"full; camera capture is being throttled to keep up"
        )
        logger.warning(msg)
        self.status.showMessage(msg)

    # ------------------------------------------------------------------
    # G[DU/e] resolution — supervisor's rule, 2026-09-22
    # ------------------------------------------------------------------

    _GAIN_TABLE_HINT = (
        "\n\nMeasure G[DU/e] for this camera at this bit depth and add the row "
        "to CamerasMeasuredGain.csv, then start again."
    )

    def _camera_serial(self) -> str:
        """Serial number reported by the frame source; '' when unknown.

        get_info() returns {} on a camera that is not open yet, and the
        synthetic TIFF mock puts a file path in this field — neither is a
        serial number, so both come back as ''.
        """
        try:
            sn = str(self.camera.get_info().get("serial", "") or "").strip()
        except Exception:
            logger.exception("Could not read camera info for the gain lookup")
            return ""
        return sn if sn.isdigit() else ""

    def _prepare_gain(self) -> bool:
        """Resolve G[DU/e] before a run starts. Returns False to refuse the run.

        Vika's rule (2026-09-22): G always comes from the measured table, never
        from the convert_gain() formula. A camera missing from the table is a
        hard stop — a formula estimate would silently bias the shot-noise term
        and therefore every κ² the session produces. An inexact *gain* is not a
        hard stop: the table entry is rescaled in dB and the operator is told.

        The one exception is the synthetic --mock-tiff source, which has no
        camera at all and so can never be in the table; it keeps the formula and
        says so, because its numbers are for exercising the GUI, not for science.
        """
        synthetic = bool(getattr(self.camera, "is_synthetic", False))
        sn        = self._camera_serial()
        self.processor.camera_sn = sn or None
        self.processor.invalidate_gain()

        if not sn and not synthetic:
            QMessageBox.critical(
                self, "Can't calculate SCOS",
                f"Can't calculate SCOS: CameraSN <unknown> Mono{self.processor.bit_depth} "
                f"was not found in G[DU/e] Calibration file"
                f"\n\nThe camera did not report a serial number, so its measured "
                f"gain cannot be looked up." + self._GAIN_TABLE_HINT,
            )
            return False

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                g = self.processor.resolve_gain()
        except GainTableError as exc:
            logger.error("Gain table lookup failed: %s", exc)
            QMessageBox.critical(
                self, "Can't calculate SCOS", f"{exc}{self._GAIN_TABLE_HINT}")
            return False

        logger.info("G = %.6f DU/e (source: %s, SN %s, Mono%d, %.1f dB)",
                    g, self.processor.gain_source, sn or "-",
                    self.processor.bit_depth, self.processor.gain_db)

        if synthetic:
            QMessageBox.warning(
                self, "Synthetic Source — Estimated G",
                f"This is the synthetic --mock-tiff source, which has no camera "
                f"and cannot appear in the gain table.\n\n"
                f"G was calculated from the formula instead "
                f"(test-mode saturation capacity "
                f"{self.processor.test_mode_sat_capacity:.0f} e-, G={g:.4f} DU/e).\n\n"
                f"These results are for exercising the GUI — do not use them as "
                f"measurements.",
            )
        elif caught:
            QMessageBox.warning(
                self, "Estimated G[DU/e]",
                f"CameraSN {sn} Mono{self.processor.bit_depth} is in the "
                f"G[DU/e] table, but not at {self.processor.gain_db:g} dB.\n\n"
                f"G was rescaled from the closest measured gain "
                f"(G={g:.4f} DU/e). SCOS will continue.",
            )
        return True

    def _on_scos_error(self, msg: str):
        """GainTableError raised in worker thread — stop SCOS and show dialog."""
        self.btn_start_scos.setChecked(False)
        QMessageBox.critical(self, "SCOS Error", msg)

    def _on_roi_changed(self, mask: np.ndarray, circ: dict):
        self._mask = mask
        self._roi_circ = circ
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
        """Camera-side warning — most often Pylon reporting skipped frames.

        Now that intake blocks the grab loop under overload, buffer-overflow
        warnings are the *expected* symptom of a slow patch rather than a
        rarity, and a modal dialog per event would freeze the GUI thread —
        exactly what this backpressure work exists to prevent. During a session
        they go to the status bar and app.log only; outside one (parameter
        problems at start-up, where there is no data to lose) the blocking
        dialog is still the right call.
        """
        logger.warning("%s", msg)   # camera messages already name their source
        self.status.showMessage(msg)
        if self._state in (State.DARK_CAL, State.BRIGHT_CAL,
                           State.MEASURING_INIT, State.MEASURING):
            return
        QMessageBox.warning(self, "Camera Warning", msg)

    def _on_camera_error(self, msg: str):
        logger.error("Camera error: %s", msg)
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
        self.processor.gain_db   = info.get("gain_db",   self.processor.gain_db)
        self.processor.bit_depth = info.get("bit_depth", 10)
        self._pending_mask_mat   = self.camera.get_mask_mat()

        self.btn_start_scos.setEnabled(False)
        self.btn_start_scos.setText("Waiting for calibration…")
        self._calib_label.setText("Calibrating…")

        self._calib_thread = _CalibrationLoaderThread(
            self.processor, dark_dir, main_dir, smoothing,
            64.0, self.spn_window.value(), self,
        )
        self._calib_thread.progress.connect(self._calib_label.setText)
        self._calib_thread.done.connect(self._on_calibration_done)
        self._calib_thread.start()

    def _on_calibration_done(self, success: bool, msg: str):
        """Runs on the GUI thread when _CalibrationLoaderThread finishes."""
        self.btn_start_scos.setText("Start SCOS")
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
        # The plot holds rBFi — BFi already divided by the normalization
        # constant — so 1/plotted is not κ². Undo the division to get the
        # corrected κ² this key is documented to carry (CLAUDE.md, "Save
        # format"). Before normalization completes there is no constant and
        # the plotted values are raw BFi, so 1/plotted is κ² as it stands.
        t, bfi = self.plot_widget.get_data()
        k2_corr = 1.0 / (bfi * self._bfi_norm) if self._bfi_norm else 1.0 / bfi
        if path.endswith(".mat"):
            scipy.io.savemat(path, {
                "scosTime": t,           # seconds, matching MATLAB's timeVec
                "scosData": k2_corr,     # corrected κ², per MATLAB convention
                "rBFi": bfi,             # what was plotted
                "normalizationConstant": float(self._bfi_norm or 0.0),
                "frameRate": self.spn_fps.value(),
                "exposureTime": self.spn_exposure.value(),
                "Gain": self.spn_gain.value(),
            })
        else:
            np.savez(path, scosTime=t, BFI=bfi, scosData=k2_corr,
                     normalizationConstant=float(self._bfi_norm or 0.0),
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
        if self._h5_replay is not None:
            self._h5_replay.stop()
        self.camera.stop()
        self.camera.close()
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
        event.accept()
