"""
Laser-safety warning shown when the app starts with the real camera (todo U1).

The operator must confirm it before the main window opens; "Exit" (or closing
the window, Escape, Enter) quits the app. The wording comes from the user's
list after the first rig session (2026-10-08) — it is a safety instruction, so
keep it as written.

Only the real-camera mode shows it (user's decision, 2026-10-09): the playback
modes have no laser and no subject.

A QDialog of its own rather than a QMessageBox: a message box caps its own
width (about 800 px on the rig PC's 1280-px screen) and leaves the text
crowded against its edges; the user asked for a larger, roomier window.
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QStyle, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

LASER_SAFETY_TITLE = "Laser safety"

LASER_SAFETY_TEXT = (
    "<p style='font-size:22pt; font-weight:bold;'>"
    "Laser safety — read before you continue</p>"
    "<ol style='font-size:17pt;'>"
    "<li style='margin-bottom:14px;'>The subject must wear <b>laser-safety goggles</b> "
    "for the <b>whole measurement</b>.</li>"
    "<li style='margin-bottom:14px;'>Remove the probe <b>only after checking that the "
    "laser is off</b> — the <b>red indicator light</b> on the laser is "
    "<b>not lit</b>.</li>"
    "<li>Remove the probe <b>only by pulling the rubber strap backwards</b>.</li>"
    "</ol>"
)

CONFIRM_TEXT = "I confirm"
EXIT_TEXT = "Exit"

_WIDTH = 960          # px; the rig PC's screen is 1280 × 752
_ICON_SIZE = 72       # px


class LaserSafetyDialog(QDialog):
    """The warning window. accept() = "I confirm"; anything else rejects."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(LASER_SAFETY_TITLE)
        self.setModal(True)

        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(_ICON_SIZE, _ICON_SIZE))

        self.text_label = QLabel(LASER_SAFETY_TEXT)
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setWordWrap(True)

        self.confirm_button = QPushButton(CONFIRM_TEXT)
        self.exit_button = QPushButton(EXIT_TEXT)
        self.confirm_button.clicked.connect(self.accept)
        self.exit_button.clicked.connect(self.reject)
        # Enter means "Exit": confirming takes a deliberate click on
        # "I confirm" (user's decision, 2026-10-09). Escape and the title-bar
        # X reject by themselves (QDialog).
        self.confirm_button.setAutoDefault(False)
        self.exit_button.setDefault(True)

        top = QHBoxLayout()
        top.setSpacing(32)
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.text_label, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.confirm_button)
        buttons.addSpacing(20)
        buttons.addWidget(self.exit_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 36)
        layout.setSpacing(32)
        layout.addLayout(top)
        layout.addLayout(buttons)

        # Large on purpose: a safety instruction must not look like a routine pop-up.
        self.setStyleSheet("QPushButton { font-size: 16pt; padding: 12px 36px; }")
        self.setFixedWidth(_WIDTH)
        self.exit_button.setFocus()


def confirm_laser_safety(parent: QWidget | None = None) -> bool:
    """Show the warning and wait. True only if the operator clicked "I confirm".

    Both answers go into app.log, which is copied into the session folder —
    a record that the warning was confirmed, and when.
    """
    dialog = LaserSafetyDialog(parent)
    dialog.exec()
    confirmed = dialog.result() == QDialog.DialogCode.Accepted
    if confirmed:
        logger.info("Laser-safety warning confirmed by the operator")
    else:
        logger.warning("Laser-safety warning not confirmed — the app closes")
    return confirmed
