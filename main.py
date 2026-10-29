"""
SCOS Application entry point.

Run with a TIFF mock camera (no hardware needed):
    python main.py --mock-tiff path/to/stack.tif

Generate a synthetic stack first if you don't have real data:
    python tools/synth_tiff.py --out scratch/mock.tif

Run with the real Basler camera (default):
    python main.py

Or double-click the SCOS icon on the desktop (made once per PC by
tools/make_desktop_shortcut.py). It runs this file with venv\\Scripts\\pythonw.exe:
no console window, so app.log is the only place messages go.
"""

import faulthandler
import os
import subprocess
import sys
import argparse
import logging
import threading
import traceback
from pathlib import Path

import pyqtgraph as pg
from PyQt6.QtCore import QLockFile
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

# Every file main.py writes or reads lives next to it, whatever folder the app
# was started from — a desktop icon starts it from wherever Windows likes.
APP_DIR   = Path(__file__).resolve().parent
LOG_PATH  = APP_DIR / "app.log"
LOCK_PATH = APP_DIR / "scos.lock"
ICON_PATH = APP_DIR / "assets" / "scos.ico"

# Windows groups taskbar buttons by this ID; without one the button shows
# Python's icon instead of the app's.
APP_USER_MODEL_ID = "BarIlan.OpticalNeuroimaging.SCOS"

ALREADY_RUNNING_TITLE = "SCOS Is Already Running"
CRASH_TITLE = "SCOS Stopped — Error"

logger = logging.getLogger(__name__)

# Dark theme for pyqtgraph
pg.setConfigOption('background', '#1e1e1e')
pg.setConfigOption('foreground', '#cccccc')


def setup_logging(log_path: Path = LOG_PATH) -> None:
    """Write to app.log, and to the console when there is one.

    Not done at import: a second copy of the app must be turned away before it
    opens app.log with mode="w", or it wipes the running session's log.
    """
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:          # None under pythonw.exe (the desktop icon)
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=handlers,
    )
    # A crash inside C code (pypylon, Qt) never reaches sys.excepthook; this
    # writes every thread's Python stack into app.log instead — without a
    # console nothing else would remain.
    faulthandler.enable(file=file_handler.stream, all_threads=True)


def acquire_instance_lock(path: Path = LOCK_PATH) -> QLockFile | None:
    """The lock that keeps a second copy of the app from starting, or None if
    another copy holds it.

    A second copy would fight the first for the camera and overwrite its
    app.log. Stale time 0: the lock is never given up because of its age, only
    when the process holding it is gone — so a crashed run does not block the
    next start (checked 2026-10-29: held past 30 s, freed when the holder was
    killed).
    """
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    return lock if lock.tryLock(0) else None


def _show_error_in_own_process(title: str, text: str) -> None:
    """A Windows message box shown by a separate, tiny process.

    The crashing app exits at once, as it did before it had a crash hook; a box
    of its own would keep it running (and delivering queued frames) until OK.
    """
    if sys.platform != "win32":
        return
    code = ("import ctypes, sys; "
            "ctypes.windll.user32.MessageBoxW(None, sys.argv[1], sys.argv[2], 0x10 | 0x40000)")
    try:
        subprocess.Popen([sys.executable, "-c", code, text, title],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        logger.exception("Could not show the crash message")


def _on_uncaught_exception(etype, value, tb) -> None:
    """sys.excepthook: log the error, tell the operator, stop the app.

    Without a hook PyQt6 aborts on an exception in a slot, and under pythonw.exe
    the traceback goes nowhere — the window would just vanish. Stopping is kept:
    carrying on in an unknown state could record wrong data.
    """
    text = "".join(traceback.format_exception(etype, value, tb))
    logger.critical("Uncaught exception — SCOS stops:\n%s", text)
    logging.shutdown()
    _show_error_in_own_process(
        CRASH_TITLE,
        f"SCOS stopped because of an unexpected error:\n\n{etype.__name__}: {value}\n\n"
        f"The details are in app.log:\n{LOG_PATH}\n\n"
        "Copy app.log somewhere safe before starting SCOS again — "
        "the next start overwrites it.")
    os._exit(1)


def _on_thread_exception(args: threading.ExceptHookArgs) -> None:
    """threading.excepthook: a Python thread (not a QThread) that dies only
    ends itself, as before — but now it is in app.log, not on a missing console."""
    logger.error("Uncaught exception in thread %s:\n%s",
                 args.thread.name if args.thread else "?",
                 "".join(traceback.format_exception(
                     args.exc_type, args.exc_value, args.exc_traceback)))


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(ICON_PATH)))

    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        QMessageBox.warning(
            None, ALREADY_RUNNING_TITLE,
            "SCOS is already open on this computer — look for its window on the "
            "taskbar.\n\nA second copy cannot run: only one program can use the "
            "camera, and it would overwrite the running session's app.log.")
        sys.exit(0)

    sys.excepthook = _on_uncaught_exception     # before the log: a log that
    threading.excepthook = _on_thread_exception  # cannot open still shows the box
    setup_logging()
    logger.info("SCOS app starting")
    from gui.main_window import MainWindow

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mock-tiff", type=str, default=None,
                        help="Replay a multipage TIFF stack (synthetic data).")
    parser.add_argument("--mock-folder", type=str, default=None,
                        help="Replay a per-frame TIFF folder (real lab recording).")
    parser.add_argument("--mock-h5", type=str, default=None,
                        help="Replay a saved scos_*.h5 result file (no camera/processing).")
    args, _ = parser.parse_known_args()

    # Dark palette
    from PyQt6.QtGui import QPalette, QColor
    from PyQt6.QtCore import Qt
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base,            QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button,          QColor(55, 55, 55))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(0, 120, 215))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    if args.mock_h5:
        logger.info("Mode: HDF5 replay — %s", args.mock_h5)
        from h5_replay import H5ReplayThread, _NullCamera
        camera = _NullCamera()
        window = MainWindow(camera=camera, h5_replay=H5ReplayThread(args.mock_h5, loop=True))
    elif args.mock_folder:
        logger.info("Mode: folder mock camera — %s", args.mock_folder)
        from folder_camera import FolderMockCamera
        camera = FolderMockCamera(args.mock_folder)
        window = MainWindow(camera=camera)
    elif args.mock_tiff:
        logger.info("Mode: TIFF mock camera — %s", args.mock_tiff)
        from mock_camera import MockCameraThread
        camera = MockCameraThread(args.mock_tiff)
        window = MainWindow(camera=camera)
    else:
        logger.info("Mode: real Basler camera")
        # Laser safety first (todo U1): nothing opens until the operator
        # confirms. Real camera only — the playback modes have no laser.
        from gui.safety_dialog import confirm_laser_safety
        if not confirm_laser_safety():
            sys.exit(0)
        from camera import CameraThread
        camera = CameraThread()
        window = MainWindow(camera=camera)
    window.showMaximized()
    exit_code = app.exec()
    instance_lock.unlock()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
