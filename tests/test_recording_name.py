"""
Tests for the recording name and the order of the two opening dialogs.

`docs/SCOS_protocol.md:11-17` sets the sequence at the start of a session:

    Get G[DU/e] conversion constant from table
    Ask for recording name and location. Create appropriate folder.
    Calibration 1: Dark Frames — "Please turn off the Laser"

The app asked only for a location, gave the folder an automatic name, and
asked *after* the laser prompt. `docs/session_tab` says the same as the
protocol in other words: saving is arranged "at the very beginning of the
session".

The order is not only a formality. Everything done at the keyboard should
happen before the room goes dark — an operator who has just switched the laser
off should not then be standing in the dark hunting for a folder, and a Cancel
at that point aborted a run whose laser was already off.
"""

import datetime
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow

TIMESTAMP = re.compile(r"^(.*)_(\d{8}_\d{6})$")


class _StubCamera(QObject):
    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)
    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A window with the output root already chosen, so only the order varies."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    w = MainWindow(camera=_StubCamera())
    yield w
    w.close()


class TestFolderName:

    def test_the_typed_name_becomes_the_folder_prefix(self, window):
        window.txt_recording_name.setText("subject03_rest")
        name = window._session_folder_name()

        m = TIMESTAMP.match(name)
        assert m, f"{name!r} should end in a timestamp"
        assert m.group(1) == "subject03_rest"

    def test_an_empty_name_keeps_the_old_automatic_form(self, window):
        window.txt_recording_name.setText("")
        assert TIMESTAMP.match(window._session_folder_name()).group(1) == "scos"

    def test_the_timestamp_is_never_optional(self, window):
        # Two runs for the same subject must not collide, and the protocol
        # asks for a folder per recording rather than one per name.
        window.txt_recording_name.setText("subject03")
        a = window._session_folder_name()
        ts = TIMESTAMP.match(a).group(2)
        datetime.datetime.strptime(ts, "%Y%m%d_%H%M%S")   # parses, or raises

    @pytest.mark.parametrize("typed, expected", [
        ("sub 03 rest",        "sub_03_rest"),     # spaces would quote badly
        ("sub/03",             "sub_03"),          # would make a nested folder
        ("sub:03",             "sub_03"),          # illegal on Windows
        ('a<b>c"d|e?f*g',      "a_b_c_d_e_f_g"),
        ("  padded  ",         "padded"),
        ("trailing.",          "trailing"),        # Windows cannot delete these
        (".", "scos"),                             # nothing usable left
    ])
    def test_the_name_is_repaired_not_rejected(self, window, typed, expected):
        # An operator halfway through setting up a subject should not be
        # stopped by a colon.
        window.txt_recording_name.setText(typed)
        assert TIMESTAMP.match(window._session_folder_name()).group(1) == expected

    def test_a_very_long_name_is_truncated(self, window):
        window.txt_recording_name.setText("x" * 300)
        prefix = TIMESTAMP.match(window._session_folder_name()).group(1)
        assert len(prefix) == window._NAME_MAX_CHARS

    def test_the_folder_actually_created_uses_the_name(self, window, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        window.txt_recording_name.setText("subject07")
        window._start_dark_cal()

        assert window._session_folder is not None
        assert window._session_folder.name.startswith("subject07_")
        assert window._session_folder.is_dir()


class TestDialogOrder:
    """The protocol's order, asserted by watching which dialog opens first."""

    @pytest.fixture
    def order(self, window, monkeypatch, tmp_path):
        seen = []

        def _folder(*a, **k):
            seen.append("folder")
            return str(tmp_path)

        def _question(*a, **k):
            seen.append("laser")
            return QMessageBox.StandardButton.Ok

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(_folder))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
        window._output_root = None          # force the folder dialog to open
        return seen

    def test_the_folder_is_asked_for_before_the_laser_prompt(self, window, order):
        window._start_dark_cal()
        assert order == ["folder", "laser"], (
            "SCOS_protocol.md:13 puts 'Ask for recording name and location' "
            "before the 'Please turn off the Laser' pop-up"
        )

    def test_the_folder_exists_before_the_laser_prompt(self, window, monkeypatch,
                                                       tmp_path):
        # "Create appropriate folder" comes before the pop-up too, not just
        # the question about where to put it.
        existed = {}

        def _question(*a, **k):
            existed["at_prompt"] = (window._session_folder is not None
                                    and window._session_folder.is_dir())
            return QMessageBox.StandardButton.Ok

        monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
        window._start_dark_cal()

        assert existed.get("at_prompt") is True


class TestCancelling:

    def test_cancelling_the_folder_dialog_starts_nothing(self, window, monkeypatch):
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: ""))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: pytest.fail(
                                "the laser prompt must not appear after a cancelled "
                                "folder dialog")))
        window._output_root = None

        window._start_dark_cal()

        assert window._session_folder is None
        assert not window.btn_start_scos.isChecked()
        assert window._state is not State.DARK_CAL

    def test_cancelling_the_laser_prompt_leaves_no_empty_folder(
            self, window, tmp_path, monkeypatch):
        # The folder is created before the prompt now, so cancelling there
        # would otherwise litter the output root with empty directories.
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
        window.txt_recording_name.setText("abandoned")

        window._start_dark_cal()

        assert window._session_folder is None
        assert list(tmp_path.iterdir()) == [], "no empty session folder left behind"
        assert not window.btn_start_scos.isChecked()

    def test_a_folder_with_files_in_it_is_never_deleted(self, window, tmp_path,
                                                        monkeypatch):
        # rmdir only removes an empty directory. If anything did land in there,
        # losing it would be far worse than leaving a stray folder.
        def _question(*a, **k):
            (window._session_folder / "something.h5").write_bytes(b"data")
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
        window._start_dark_cal()

        leftover = list(tmp_path.iterdir())
        assert len(leftover) == 1 and (leftover[0] / "something.h5").exists()


class TestTheNameIsLockedDuringARun:

    def test_it_locks_with_the_other_parameters(self, window):
        window._set_params_enabled(False)
        assert not window.txt_recording_name.isEnabled(), (
            "the folder is named at Start SCOS, so editing the name later "
            "would change nothing and only mislead"
        )
        window._set_params_enabled(True)
        assert window.txt_recording_name.isEnabled()
