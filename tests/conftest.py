"""
Shared pytest setup.

**Modal dialogs are blocked during tests.** A QMessageBox or QFileDialog opened
from test code waits for a human to click it: the suite stops dead, and a window
appears on whoever's screen is attached — including during the pre-commit hook,
where nobody expects the app to talk to them. That happened while writing
tests/test_gain_table.py: the calibration prompt "Please turn on the laser"
popped up over and over, once per test that pressed Start SCOS.

So every modal entry point raises instead of opening. A test that legitimately
drives one of these paths must monkeypatch the specific call it expects, which
also documents what the operator would have seen.
"""

import shutil
from pathlib import Path

import pytest

from PyQt6.QtWidgets import QFileDialog, QMessageBox

_REPO = Path(__file__).resolve().parent.parent


_BLOCKED = (
    "{cls}.{name}() tried to open a real modal dialog during tests.\n"
    "Nothing can click it, so the suite would hang here.\n"
    "monkeypatch this call in the test (see the `dialogs` fixture in "
    "tests/test_gain_table.py for the pattern)."
)


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for cls, names in (
        (QMessageBox,  ("information", "warning", "critical", "question", "about")),
        (QFileDialog,  ("getExistingDirectory", "getOpenFileName", "getSaveFileName")),
    ):
        for name in names:
            if not hasattr(cls, name):
                continue

            def _blocked(*_a, _cls=cls, _name=name, **_k):
                raise AssertionError(_BLOCKED.format(cls=_cls.__name__, name=_name))

            monkeypatch.setattr(cls, name, staticmethod(_blocked))


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path_factory):
    """Point MainWindow at a private copy of the config for every test.

    MainWindow saves settings on close (todo.md B3), and the suite closes
    windows hundreds of times — none of that may reach the real files. The
    committed defaults are copied in, so loading behaves exactly as it does
    for the app; the operator's local override file is NOT copied, so a
    developer's own settings cannot change test results.
    """
    # Its own directory, not inside tmp_path: tests use tmp_path as a results
    # folder and assert on exactly what lands in it.
    cfg_dir = tmp_path_factory.mktemp("config")
    src = _REPO / "scos_config.json"
    if src.exists():
        shutil.copy(src, cfg_dir / "scos_config.json")
    monkeypatch.setenv("SCOS_CONFIG_DIR", str(cfg_dir))
    return cfg_dir
