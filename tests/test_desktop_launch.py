"""
Starting the app from a desktop icon (todo D11, 2026-10-29).

The icon runs main.py with venv\\Scripts\\pythonw.exe, from whatever folder
Windows chooses and with no console. What has to hold for that:
- app.log goes next to main.py, not into the start folder;
- importing main.py opens no log (so a second copy can be turned away first);
- a second copy is turned away by a lock, before it touches app.log;
- a crash is logged and shown in a message box, and the app still stops;
- console programs started by the app (arduino-cli, git) open no black window.
"""

import ast
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

import main  # noqa: E402
import arduino_uploader  # noqa: E402
from core import recorder  # noqa: E402
from tools import make_desktop_shortcut  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_ON_WINDOWS = sys.platform == "win32"


class TestFilesNextToMain:
    def test_paths_are_in_the_repo_folder(self):
        assert main.APP_DIR == REPO
        assert main.LOG_PATH == REPO / "app.log"
        assert main.LOCK_PATH.parent == REPO

    def test_import_opens_no_log(self):
        """Before 2026-10-29 importing main.py opened app.log with mode="w"."""
        for h in logging.getLogger().handlers:
            assert Path(getattr(h, "baseFilename", "")).name != "app.log"

    def test_logging_writes_the_given_file_and_skips_a_missing_console(
            self, monkeypatch, tmp_path):
        captured, dumps = {}, []
        monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))
        # Not the real one: pytest has its own faulthandler on.
        monkeypatch.setattr(main.faulthandler, "enable",
                            lambda file, all_threads: dumps.append((file, all_threads)))
        monkeypatch.setattr(sys, "stderr", None)        # as under pythonw.exe
        main.setup_logging(tmp_path / "app.log")
        handlers = captured["handlers"]
        try:
            assert len(handlers) == 1
            assert Path(handlers[0].baseFilename) == tmp_path / "app.log"
            # A crash in C code (pypylon, Qt) dumps every thread into app.log.
            assert dumps == [(handlers[0].stream, True)]
        finally:
            for h in handlers:
                h.close()

    def test_icon_has_every_size(self):
        sizes = {s.width() for s in QIcon(str(main.ICON_PATH)).availableSizes()}
        assert {16, 32, 48, 256} <= sizes


class TestSecondCopy:
    def test_lock_turns_a_second_copy_away(self, tmp_path):
        path = tmp_path / "scos.lock"
        first = main.acquire_instance_lock(path)
        assert first is not None
        assert main.acquire_instance_lock(path) is None
        first.unlock()
        again = main.acquire_instance_lock(path)
        assert again is not None
        again.unlock()

    def test_lock_comes_before_the_log_and_the_laser_warning(self):
        tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "main")
        src = ast.unparse(fn)
        lock = src.index("acquire_instance_lock()")
        assert lock < src.index("setup_logging()")
        assert lock < src.index("confirm_laser_safety()")
        assert lock < src.index("CameraThread()")


class TestCrash:
    def test_crash_is_logged_shown_and_stops_the_app(self, monkeypatch, caplog):
        shown = []
        monkeypatch.setattr(main.subprocess, "Popen",
                            lambda args, **kw: shown.append((args, kw)))
        monkeypatch.setattr(main.logging, "shutdown", lambda: None)

        class _Exited(Exception):
            pass

        def _exit(code):
            raise _Exited(code)
        monkeypatch.setattr(main.os, "_exit", _exit)

        try:
            raise ValueError("test failure")
        except ValueError:
            info = sys.exc_info()
        with caplog.at_level(logging.CRITICAL), pytest.raises(_Exited):
            main._on_uncaught_exception(*info)

        assert "ValueError: test failure" in caplog.text
        if _ON_WINDOWS:
            (args, kw), = shown
            text, title = args[-2], args[-1]
            assert title == main.CRASH_TITLE
            assert "ValueError: test failure" in text
            assert str(main.LOG_PATH) in text
            assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(not _ON_WINDOWS, reason="CREATE_NO_WINDOW is Windows-only")
class TestNoConsoleWindows:
    def _record_runs(self, monkeypatch, module):
        calls = []

        def _run(args, **kw):
            calls.append(kw)
            return subprocess.CompletedProcess(args, 0, stdout="abc123\n", stderr="")
        monkeypatch.setattr(module.subprocess, "run", _run)
        return calls

    def test_git_hash(self, monkeypatch):
        calls = self._record_runs(monkeypatch, recorder)
        recorder._git_commit()
        assert calls
        assert all(kw["creationflags"] == subprocess.CREATE_NO_WINDOW for kw in calls)

    def test_arduino_compile_and_upload(self, monkeypatch):
        calls = self._record_runs(monkeypatch, arduino_uploader)
        monkeypatch.setattr(arduino_uploader, "find_arduino_cli", lambda: "arduino-cli")
        ok, _ = arduino_uploader.upload_sketch(8.0, 20.0, port="COM99")
        assert ok
        assert len(calls) == 2
        assert all(kw["creationflags"] == subprocess.CREATE_NO_WINDOW for kw in calls)


class TestShortcutScript:
    def test_runs_main_with_the_venv_pythonw_in_the_repo(self):
        ps = make_desktop_shortcut.powershell_script()
        assert str(REPO / "venv" / "Scripts" / "pythonw.exe") in ps
        assert f'"{REPO / "main.py"}"' in ps
        assert f"WorkingDirectory = '{REPO}'" in ps
        assert str(REPO / "assets" / "scos.ico") in ps
        assert "GetFolderPath('Desktop')" in ps

    def test_quote_survives_an_apostrophe(self):
        assert make_desktop_shortcut._ps_quote("C:\\O'Brien") == "'C:\\O''Brien'"
