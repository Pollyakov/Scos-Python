# Writing tests in this project

Loaded when working under `tests/`. The root `CLAUDE.md` still applies.

IMPORTANT: a test module that builds a `MainWindow` must create the `QApplication` **before** `gui.main_window` is imported, at module level:
```python
_app = QApplication.instance() or QApplication([])
from gui.main_window import MainWindow
```
Importing that module pulls in pyqtgraph, and constructing the application afterwards kills the interpreter outright — no traceback, no pytest output, exit code 127, which looks like a broken command rather than a crash. See `tests/test_gain_table.py` and `tests/test_invalid_k2_guard.py`.

Modal dialogs are blocked suite-wide by `tests/conftest.py`: any `QMessageBox` or `QFileDialog` a test reaches raises instead of opening. A test that legitimately drives one must monkeypatch that specific call (the `dialogs` fixture is the pattern).

Settings persistence (todo.md B3): `MainWindow` saves settings on close, so the autouse
`isolated_config` fixture in `tests/conftest.py` points every test at a private copy of
the defaults via `SCOS_CONFIG_DIR`, in its own temp directory — never inside `tmp_path`,
which tests use as a results folder and assert on. A test that exercises saving must use
a camera stub with `persists_settings = True` (see `tests/test_settings_persistence.py`).
