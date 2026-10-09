"""
Show every laser-safety window, one after another, as the operator sees them.

The windows live in gui/safety_dialog.py (todo U1, U2). Tests check their
wording and behaviour, but not how they look — run this after changing any of
their text or layout and click through them:

    venv\\Scripts\\python.exe tools/preview_safety_windows.py

(From Claude Code's `!` prompt: `! venv/Scripts/python.exe tools/preview_safety_windows.py`.)

Order: the startup warning, the red "laser may still be on" window (with the
numbers from the first rig session, 114.5 vs 11.4 DU), then the four
probe-removal windows — passed, skipped, failed, cancelled. Each answer is
printed. Nothing is measured or saved.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def _dark_palette() -> QPalette:
    """The app's dark theme (main.py), so the windows look as they do there."""
    p = QPalette()
    for role, rgb in [
        ("Window", (30, 30, 30)), ("WindowText", (220, 220, 220)),
        ("Base", (45, 45, 45)), ("AlternateBase", (35, 35, 35)),
        ("Text", (220, 220, 220)), ("Button", (55, 55, 55)),
        ("ButtonText", (220, 220, 220)), ("Highlight", (0, 120, 215)),
        ("HighlightedText", (255, 255, 255)),
    ]:
        p.setColor(getattr(QPalette.ColorRole, role), QColor(*rgb))
    return p


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    from gui import safety_dialog as s

    print("startup warning:", "I confirm" if s.confirm_laser_safety() else "Exit")
    print("laser still on:",
          "Continue anyway" if s.ask_continue_with_laser_on(None, 114.5, 11.4)
          else "Check again")
    for outcome in (s.PROBE_PASSED, s.PROBE_SKIPPED, s.PROBE_FAILED, s.PROBE_CANCELLED):
        print("probe window:", outcome)
        s.show_probe_removal(None, outcome)


if __name__ == "__main__":
    main()
