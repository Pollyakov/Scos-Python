"""
SCOS Application entry point.

Run with a TIFF mock camera (no hardware needed):
    python main.py --mock-tiff path/to/stack.tif

Generate a synthetic stack first if you don't have real data:
    python tools/synth_tiff.py --out scratch/mock.tif

Run with the real Basler camera (default):
    python main.py
"""

import sys
import argparse
import pyqtgraph as pg
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow

# Dark theme for pyqtgraph
pg.setConfigOption('background', '#1e1e1e')
pg.setConfigOption('foreground', '#cccccc')


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mock-tiff", type=str, default=None,
                        help="Replay a multipage TIFF stack (synthetic data).")
    parser.add_argument("--mock-folder", type=str, default=None,
                        help="Replay a per-frame TIFF folder (real lab recording).")
    args, _ = parser.parse_known_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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

    if args.mock_folder:
        from folder_camera import FolderMockCamera
        camera = FolderMockCamera(args.mock_folder)
    elif args.mock_tiff:
        from mock_camera import MockCameraThread
        camera = MockCameraThread(args.mock_tiff)
    else:
        from camera import CameraThread
        camera = CameraThread()

    window = MainWindow(camera=camera)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
