"""
HDF5 replay: feed a previously saved scos_*.h5 file back into the GUI
as if it were live camera output.  Bypasses camera and processor — results
flow directly into the plot and info panel.

Usage:
    python main.py --mock-h5 path/to/scos_20260512_123456.h5
"""

import time
from pathlib import Path

import h5py
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


class _NullCamera:
    """Drop-in for CameraThread / FolderMockCamera with no hardware.

    All attribute writes are accepted silently so MainWindow._toggle_video
    can set pixel_format, exposure_us, etc. without crashing.
    Signals expected by _connect_signals() are provided as no-op stubs.
    """

    class _Sig:
        """Minimal signal stub — connect() and emit() are no-ops."""
        def connect(self, *_):   pass
        def emit(self, *_):      pass
        def disconnect(self, *_): pass

    frame_ready   = _Sig()
    display_ready = _Sig()
    error         = _Sig()
    warning       = _Sig()

    pixel_format  = "Mono10"
    exposure_us   = 5000.0
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0.0
    roi_position  = None

    def open(self):           pass
    def start_capture(self):  pass
    def stop(self):           pass
    def close(self):          pass
    def set_frame_rate(self, *_):    pass
    def set_exposure(self, *_):      pass
    def set_gain(self, *_):          pass
    def set_pixel_format(self, *_):  pass
    def set_trigger_mode(self, *_):  pass
    def set_trigger_delay(self, *_): pass

    def get_info(self) -> dict:
        return {"frame_rate": self.frame_rate, "exposure_us": self.exposure_us,
                "gain_db": self.gain_db, "pixel_format": self.pixel_format}


class H5ReplayThread(QThread):
    """Replays a scos_*.h5 recording into the GUI at the original frame rate.

    Emits the same result_ready signal as _SCOSWorkerThread so MainWindow
    needs no rewiring — just swap the thread.
    """

    result_ready = pyqtSignal(float, float, float, float, float)
                             # t, k2_raw, k2_corr, mean_i, proc_ms=0
    finished_replay = pyqtSignal()

    def __init__(self, h5_path: str | Path, loop: bool = False, parent=None):
        super().__init__(parent)
        self._path  = Path(h5_path)
        self._loop  = loop
        self._running = False

    # ------------------------------------------------------------------
    # Public API (mirrors _SCOSWorkerThread's lifecycle used by MainWindow)
    # ------------------------------------------------------------------

    def start_replay(self) -> None:
        self._running = True
        self.start()

    def stop(self) -> None:
        self._running = False
        self.wait()

    # _SCOSWorkerThread.stop() is also called from closeEvent
    # (same name, same semantics — no extra wiring needed)

    def get_metadata(self) -> dict:
        """Return the metadata dict stored in the HDF5 file."""
        with h5py.File(self._path, "r") as f:
            return dict(f["metadata"].attrs)

    # ------------------------------------------------------------------
    # Playback loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        with h5py.File(self._path, "r") as f:
            times   = f["time"][:]
            k2_raws = f["k2_raw"][:]
            k2_cors = f["k2_corr"][:]
            mean_is = f["mean_intensity"][:]
            fps     = float(f["metadata"].attrs.get("frame_rate_hz", 20.0))

        dt_ms = int(1000.0 / max(fps, 0.1))

        while True:
            for t, r, c, m in zip(times, k2_raws, k2_cors, mean_is):
                if not self._running:
                    return
                self.result_ready.emit(float(t), float(r), float(c), float(m), 0.0)
                self.msleep(dt_ms)
            if not self._loop:
                break

        self.finished_replay.emit()
