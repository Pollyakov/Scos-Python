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
        src = _main_source()
        lock = src.index("acquire_instance_lock()")
        assert lock < src.index("setup_logging()")
        assert lock < src.index("confirm_laser_safety()")
        assert lock < src.index("CameraThread()")


def _main_source() -> str:
    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    return ast.unparse(next(n for n in tree.body
                            if isinstance(n, ast.FunctionDef) and n.name == "main"))


class TestStartingSign:
    """≈ 10 s pass on a cold start before anything else appears (2026-10-29)."""

    def test_sign_is_drawn(self):
        pix = main.splash_pixmap()
        assert not pix.isNull()
        assert (pix.width(), pix.height()) == (420, 150)

    def test_nothing_heavy_loads_before_the_sign(self):
        """Only the standard library and PyQt6 at the top of main.py."""
        tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top == "PyQt6" or top in sys.stdlib_module_names, name

    def test_shown_after_the_lock_and_closed_before_the_laser_warning(self):
        src = _main_source()
        shown = src.index("QSplashScreen(")
        assert src.index("acquire_instance_lock()") < shown
        assert shown < src.index("from gui.main_window import MainWindow")
        # It stays on top: it must be gone before the warning or any window opens.
        closed = src.index("splash.close()")
        assert closed < src.index("confirm_laser_safety()")
        assert closed < src.index("MainWindow(")


class TestLogCopiedIntoSession:
    """app.log is rewritten at every launch; a copy stays with each session."""

    @pytest.fixture
    def file_log(self, tmp_path):
        handler = logging.FileHandler(tmp_path / "running.log", encoding="utf-8")
        root = logging.getLogger()
        root.addHandler(handler)
        yield handler
        root.removeHandler(handler)
        handler.close()

    def _copy(self, folder):
        from gui.main_window import MainWindow

        class _Win:
            SESSION_LOG_FILENAME = MainWindow.SESSION_LOG_FILENAME
        MainWindow._copy_app_log(_Win(), folder)

    def test_copy_holds_everything_logged_so_far(self, file_log, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        logging.getLogger("test").warning("a line from the session")
        self._copy(session)
        text = (session / "app.log").read_text(encoding="utf-8")
        assert "a line from the session" in text

    def test_no_folder_no_copy(self, file_log, tmp_path):
        self._copy(None)
        self._copy(tmp_path / "missing")
        assert not (tmp_path / "missing").exists()

    def _discard(self, folder):
        """MainWindow._discard_session_folder on a stand-in with only what it uses."""
        from gui.main_window import MainWindow

        class _Status:
            def showMessage(self, *_):
                pass

        class _Win:
            SESSION_LOG_FILENAME = MainWindow.SESSION_LOG_FILENAME
            _copy_app_log = MainWindow._copy_app_log
            status = _Status()

            def _close_frame_writer(self):
                pass
        win = _Win()
        win._session_folder = folder
        MainWindow._discard_session_folder(win)

    def test_cancelled_run_with_a_calibration_keeps_a_copy(self, file_log, tmp_path):
        folder = tmp_path / "scos_x"
        folder.mkdir()
        (folder / "Calibration.h5").write_bytes(b"dark only")
        logging.getLogger("test").warning("why the run stopped")
        self._discard(folder)
        kept = tmp_path / "scos_x_cancelled"
        assert "why the run stopped" in (kept / "app.log").read_text(encoding="utf-8")

    def test_cancelled_empty_folder_is_still_removed(self, file_log, tmp_path):
        folder = tmp_path / "scos_y"
        folder.mkdir()
        self._discard(folder)
        assert not folder.exists()
        assert not (tmp_path / "scos_y_cancelled").exists()

    def test_copied_after_the_last_window_of_a_session(self):
        import inspect
        from gui.main_window import MainWindow
        src = inspect.getsource(MainWindow._toggle_scos)
        assert src.index("self._show_probe_removal()") < src.index(
            "self._copy_app_log(session_folder)")
        # A cancelled run's kept folder gets one too — never an empty one,
        # which must still be removed.
        src = inspect.getsource(MainWindow._discard_session_folder)
        assert src.index("folder.rmdir()") < src.index("self._copy_app_log(target)")


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
