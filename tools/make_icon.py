"""
Draw the app's icon and save it as assets/scos.ico.

The icon is a cyan pulse curve (the colour of the live rBFi plot) on the app's
dark background — chosen 2026-10-29. Each size in the .ico is drawn on its own,
so the line stays at least 2 px wide on the taskbar instead of being a blurred
copy of the large picture.

Run once after changing the drawing; the .ico is committed:
    python tools/make_icon.py
"""

import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

REPO = Path(__file__).resolve().parent.parent
ICO_PATH = REPO / "assets" / "scos.ico"

SIZES = (16, 24, 32, 48, 64, 128, 256)
_DESIGN = 256.0     # the drawing below is in a 256 × 256 square


def _pulse_path(x0: float, x1: float, y: float, amp: float) -> QPainterPath:
    """Two heartbeats: flat, a sharp rise, the dicrotic notch, the decay."""
    beat = [(0.00, 0), (0.10, 0), (0.18, -1.0), (0.28, -0.35), (0.33, -0.5), (0.48, 0)]
    path = QPainterPath(QPointF(x0, y))
    half = (x1 - x0) / 2
    for k in range(2):
        for fx, fy in beat:
            path.lineTo(QPointF(x0 + k * half + fx * half * 2, y + fy * amp))
    path.lineTo(QPointF(x1, y))
    return path


def draw_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / _DESIGN, size / _DESIGN)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1e1e1e"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 44, 44)
    line_px = max(16 * size / _DESIGN, 2.0)
    p.setPen(QPen(QColor("#00d4ff"), line_px * _DESIGN / size, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_pulse_path(36, 220, 168, 100))
    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, sizes=SIZES) -> None:
    """An .ico with one PNG image per size (the format Windows has read since Vista)."""
    images = [_png_bytes(draw_icon(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for s, png in zip(sizes, images):
        dim = 0 if s >= 256 else s          # 0 means 256 in an .ico entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + data)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    write_ico(ICO_PATH)
    print(f"Saved {ICO_PATH}")
