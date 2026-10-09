"""
Laser-safety windows: the warning at startup (todo U1), the "laser may still
be on" warning after a failed laser-off check (U2), and the probe-removal
instructions shown once a measurement is saved (U2, the user's idea) — or
once a run is stopped during the bright calibration, with the laser on.

The safety wording comes from the user's list after the first rig session
(2026-10-08) — it is a safety instruction, so keep it as written:
  * the subject wears laser-safety goggles for the whole measurement;
  * the probe comes off only after checking that the laser is off — the red
    indicator light on the laser is not lit;
  * the probe comes off only by pulling the rubber strap backwards.

The startup warning is shown in real-camera mode only (user's decision,
2026-10-09): the playback modes have no laser and no subject.

Each window is a QDialog of its own rather than a QMessageBox: a message box
caps its own width (about 800 px on the rig PC's 1280-px screen) and left the
text crowded against its edges; the user asked for larger, roomier windows.

In every window the button that is *safe* to press by accident is the
default, so Enter or Escape never confirms anything risky.
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QStyle, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

_WIDTH = 960          # px; the rig PC's screen is 1280 × 752
_ICON_SIZE = 72       # px

# Window colours, by how serious the message is. "border" draws a frame round
# the whole window; "heading" colours the first line; "background"/"text"
# replace the dark theme's colours (danger only — the window turns red).
_THEMES = {
    "neutral": {},
    "ok":      {"border": "#2e9e4f", "heading": "#6fdc8c"},
    "caution": {"border": "#e0a020", "heading": "#ffc94d"},
    "danger":  {"border": "#ff4d4d", "heading": "#ffffff",
                "background": "#7a1010", "text": "#ffffff"},
}

_REMOVE_BY_STRAP = ("Remove the probe <b>only by pulling the rubber strap "
                    "backwards</b>.")


def _html(heading: str, items: list[str], intro: str = "",
          heading_color: str | None = None) -> str:
    """Rich text: a large bold heading, an optional paragraph, a numbered list."""
    color = f" color:{heading_color};" if heading_color else ""
    html = (f"<p style='font-size:22pt; font-weight:bold;{color}'>{heading}</p>")
    if intro:
        html += f"<p style='font-size:16pt;'>{intro}</p>"
    html += "<ol style='font-size:17pt;'>"
    for i, item in enumerate(items):
        margin = " style='margin-bottom:14px;'" if i < len(items) - 1 else ""
        html += f"<li{margin}>{item}</li>"
    return html + "</ol>"


class _SafetyWindow(QDialog):
    """A large window: icon, rich text, a row of buttons on the right.

    accept() and reject() carry the answer; subclasses add the buttons with
    _add_button() and say which one is the default.
    """

    def __init__(self, title: str, text_html: str, icon: QStyle.StandardPixmap,
                 theme: str = "neutral", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("safetyWindow")
        self.setWindowTitle(title)
        self.setModal(True)

        icon_label = QLabel()
        icon_label.setPixmap(self.style().standardIcon(icon).pixmap(_ICON_SIZE, _ICON_SIZE))

        self.text_label = QLabel(text_html)
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setWordWrap(True)

        top = QHBoxLayout()
        top.setSpacing(32)
        top.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.text_label, 1)

        self._buttons = QHBoxLayout()
        self._buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 36)
        layout.setSpacing(32)
        layout.addLayout(top)
        layout.addLayout(self._buttons)

        # Large on purpose: a safety instruction must not look like a routine pop-up.
        c = _THEMES[theme]
        style = "QPushButton { font-size: 16pt; padding: 12px 36px; }"
        if "border" in c:
            bg = f" background-color: {c['background']};" if "background" in c else ""
            style += f" QDialog#safetyWindow {{ border: 6px solid {c['border']};{bg} }}"
        if "text" in c:
            style += f" QLabel {{ color: {c['text']}; }}"
        self.setStyleSheet(style)
        self.setFixedWidth(_WIDTH)

    def _add_button(self, text: str, accepts: bool) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(self.accept if accepts else self.reject)
        # Only the button made default below may answer Enter — a button
        # does not become the default just because it has the focus.
        button.setAutoDefault(False)
        if self._buttons.count() > 1:
            self._buttons.addSpacing(20)
        self._buttons.addWidget(button)
        return button

    def _make_default(self, button: QPushButton) -> None:
        button.setDefault(True)
        button.setFocus()


# ---------------------------------------------------------------------------
# U1 — at startup, real camera only
# ---------------------------------------------------------------------------

LASER_SAFETY_TITLE = "Laser safety"
LASER_SAFETY_TEXT = _html(
    "Laser safety — read before you continue",
    ["The subject must wear <b>laser-safety goggles</b> for the "
     "<b>whole measurement</b>.",
     "Remove the probe <b>only after checking that the laser is off</b> — the "
     "<b>red indicator light</b> on the laser is <b>not lit</b>.",
     _REMOVE_BY_STRAP],
)
CONFIRM_TEXT = "I confirm"
EXIT_TEXT = "Exit"


class LaserSafetyDialog(_SafetyWindow):
    """accept() = "I confirm"; Exit, Escape, Enter and the X all reject."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(LASER_SAFETY_TITLE, LASER_SAFETY_TEXT,
                         QStyle.StandardPixmap.SP_MessageBoxWarning, "neutral", parent)
        self.confirm_button = self._add_button(CONFIRM_TEXT, accepts=True)
        self.exit_button = self._add_button(EXIT_TEXT, accepts=False)
        # Enter means "Exit": confirming takes a deliberate click on
        # "I confirm" (user's decision, 2026-10-09).
        self._make_default(self.exit_button)


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


# ---------------------------------------------------------------------------
# U2 — the laser-off check failed
# ---------------------------------------------------------------------------

LASER_STILL_ON_TITLE = "Laser May Still Be On"
CHECK_AGAIN_TEXT = "Check again"
CONTINUE_ANYWAY_TEXT = "Continue anyway"


class LaserStillOnDialog(_SafetyWindow):
    """Red, unmissable. accept() = "Continue anyway"; everything else —
    "Check again", Enter, Escape, the X — means check again."""

    def __init__(self, measured: float, expected: float,
                 parent: QWidget | None = None):
        text = _html(
            "LASER MAY STILL BE ON",
            ["Switch the laser off, then click <b>Check again</b>.",
             "<b>Continue anyway</b> saves the data without a passed check."],
            intro=(f"The image did not get dark — the mean intensity did not "
                   f"drop by 90 % (measured: <b>{measured:.1f} DU</b>, "
                   f"expected: <b>&lt; {expected:.1f} DU</b>)."),
            heading_color=_THEMES["danger"]["heading"],
        )
        super().__init__(LASER_STILL_ON_TITLE, text,
                         QStyle.StandardPixmap.SP_MessageBoxWarning, "danger", parent)
        self.continue_button = self._add_button(CONTINUE_ANYWAY_TEXT, accepts=True)
        self.check_again_button = self._add_button(CHECK_AGAIN_TEXT, accepts=False)
        self._make_default(self.check_again_button)


def ask_continue_with_laser_on(parent: QWidget | None, measured: float,
                               expected: float) -> bool:
    """True = "Continue anyway"; False = check again."""
    dialog = LaserStillOnDialog(measured, expected, parent)
    dialog.exec()
    return dialog.result() == QDialog.DialogCode.Accepted


# ---------------------------------------------------------------------------
# After the session is saved — when the probe may come off
# ---------------------------------------------------------------------------

# How the laser-off check ended; MainWindow passes one of these.
PROBE_PASSED = "passed"      # the camera saw the laser go off
PROBE_SKIPPED = "skipped"    # the check could not run
PROBE_FAILED = "failed"      # it failed and the operator chose "Continue anyway"
PROBE_CANCELLED = "cancelled"  # stopped during the bright calibration — laser on,
                               # no measurement, no check (user's request, 2026-10-09)

PROBE_TITLES = {
    PROBE_PASSED:  "Laser Is Off — Remove the Probe",
    PROBE_SKIPPED: "Laser-Off Check Could Not Run",
    PROBE_FAILED:  "Do Not Remove the Probe Yet",
    PROBE_CANCELLED: "Calibration Stopped — Laser Still On",
}

_PROBE_WINDOWS = {
    # outcome: (theme, icon, heading, intro, items)
    PROBE_PASSED: (
        "ok", QStyle.StandardPixmap.SP_MessageBoxInformation,
        "Laser is off — you may remove the probe",
        "The camera no longer sees the laser light. The data is saved.",
        ["First check that the <b>red indicator light</b> on the laser is "
         "<b>not lit</b>.",
         _REMOVE_BY_STRAP],
    ),
    PROBE_SKIPPED: (
        "caution", QStyle.StandardPixmap.SP_MessageBoxWarning,
        "The laser-off check could not run",
        "The app could not confirm that the laser is off. The data is saved.",
        ["Check yourself that the <b>red indicator light</b> on the laser is "
         "<b>not lit</b>.",
         "Only then remove the probe — <b>only by pulling the rubber strap "
         "backwards</b>."],
    ),
    PROBE_FAILED: (
        "danger", QStyle.StandardPixmap.SP_MessageBoxWarning,
        "The laser may still be on — do NOT remove the probe yet",
        "The data is saved.",
        ["Switch the laser off. Do <b>not</b> remove the probe until the "
         "<b>red indicator light</b> on the laser is <b>off</b>.",
         "Then remove it <b>only by pulling the rubber strap backwards</b>."],
    ),
    PROBE_CANCELLED: (
        "danger", QStyle.StandardPixmap.SP_MessageBoxWarning,
        "Calibration stopped — the laser is probably still on",
        "The measurement did not start. Before the probe comes off:",
        ["Switch the laser off and check that the <b>red indicator light</b> "
         "on the laser is <b>not lit</b>.",
         "Only then remove the probe — <b>only by pulling the rubber strap "
         "backwards</b>."],
    ),
}


class ProbeRemovalDialog(_SafetyWindow):
    """When the probe may come off, and how. One button: OK."""

    def __init__(self, outcome: str, parent: QWidget | None = None):
        theme, icon, heading, intro, items = _PROBE_WINDOWS[outcome]
        text = _html(heading, items, intro=intro,
                     heading_color=_THEMES[theme]["heading"])
        super().__init__(PROBE_TITLES[outcome], text, icon, theme, parent)
        self.outcome = outcome
        self.ok_button = self._add_button("OK", accepts=True)
        self._make_default(self.ok_button)


def show_probe_removal(parent: QWidget | None, outcome: str) -> None:
    """Show the probe-removal window for this laser-off outcome and wait."""
    logger.info("Probe-removal instructions shown — laser-off check %s", outcome)
    ProbeRemovalDialog(outcome, parent).exec()
