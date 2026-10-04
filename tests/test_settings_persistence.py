"""
Tests for remembering the operator's settings between launches (todo.md B3).

Found on 2026-09-26: the operator set Dark Frames to 60, relaunched, and the run
used 600 again — `_load_config()` read a config file that nothing ever wrote.

Settings now go to `scos_config.local.json`, a gitignored override next to the
committed defaults in `scos_config.json`, which the app never writes. Three rules
matter more than the round trip itself, and each has a test:

* only a real camera's settings are saved — playback reads its recording's
  exposure/gain/fps back into the GUI, and saving those would start the next rig
  session with a recording's settings;
* the remembered results folder is only the folder dialog's starting directory —
  the dialog still opens, so a session never lands silently in the previous
  subject's folder;
* the recording name is never carried over.

`tests/conftest.py` points every test at a private config directory, so nothing
here (or anywhere in the suite) touches the real files.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from gui.main_window import MainWindow

LOCAL = MainWindow.LOCAL_CONFIG_FILENAME
DEFAULTS = MainWindow.CONFIG_FILENAME


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


class _RealLikeCamera(_StubCamera):
    """Carries the marker only CameraThread has."""
    persists_settings = True


class _FolderLikeCamera(_RealLikeCamera):
    """Even with the marker set, a playback source must not count as real."""
    def get_calibration_mat(self):
        return None


def _open(camera_cls=_RealLikeCamera):
    return MainWindow(camera=camera_cls())


def _local(cfg_dir) -> dict:
    return json.loads((cfg_dir / LOCAL).read_text(encoding="utf-8"))


def _set_everything(w):
    w.cmb_format.setCurrentText("Mono10")
    w.spn_exposure.setValue(5.0)
    w.spn_gain.setValue(18.0)
    w.spn_fps.setValue(25.0)
    w.spn_trigger_delay.setValue(150.0)
    w.spn_window.setValue(9)
    w.spn_n1.setValue(123)
    w.spn_n2.setValue(77)
    w.spn_duration.setValue(12.0)
    w.cmb_norm_type.setCurrentIndex(1)
    w.spn_norm_seconds.setValue(8)
    w.spn_workers.setValue(2)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_every_setting_survives_a_relaunch(self, isolated_config):
        w = _open()
        _set_everything(w)
        w.close()

        w2 = _open()
        try:
            assert w2.cmb_format.currentText() == "Mono10"
            assert w2.spn_exposure.value()      == pytest.approx(5.0)
            assert w2.spn_gain.value()          == pytest.approx(18.0)
            assert w2.spn_fps.value()           == pytest.approx(25.0)
            assert w2.spn_trigger_delay.value() == pytest.approx(150.0)
            assert w2.spn_window.value()        == 9
            assert w2.spn_n1.value()            == 123
            assert w2.spn_n2.value()            == 77
            assert w2.spn_duration.value()      == pytest.approx(12.0)
            assert w2.cmb_norm_type.currentIndex() == 1
            assert w2.spn_norm_seconds.value()  == 8
            assert w2.spn_workers.value()       == 2
        finally:
            w2.close()

    def test_the_dark_frames_bug_of_2026_09_26(self, isolated_config):
        w = _open()
        w.spn_n1.setValue(60)
        w.close()
        w2 = _open()
        try:
            assert w2.spn_n1.value() == 60
        finally:
            w2.close()

    def test_saved_keys_are_exactly_the_keys_the_loader_reads(self, isolated_config):
        # A key written but never read (or read but never written) is a
        # setting that silently stops persisting.
        w = _open()
        w.close()
        saved = set(_local(isolated_config))
        defaults = set(json.loads((isolated_config / DEFAULTS).read_text(encoding="utf-8")))
        assert defaults <= saved, f"defaults not saved back: {defaults - saved}"


# ---------------------------------------------------------------------------
# Which file, and how it is written
# ---------------------------------------------------------------------------

class TestFiles:
    def test_committed_defaults_are_never_written(self, isolated_config):
        before = (isolated_config / DEFAULTS).read_bytes()
        w = _open()
        _set_everything(w)
        w.close()
        assert (isolated_config / DEFAULTS).read_bytes() == before

    def test_no_temp_file_left_behind(self, isolated_config):
        w = _open()
        w.close()
        assert sorted(p.name for p in isolated_config.iterdir()) == sorted([DEFAULTS, LOCAL])

    def test_file_is_human_editable(self, isolated_config):
        w = _open()
        w.close()
        raw = (isolated_config / LOCAL).read_bytes()
        assert b"\r\n" not in raw                 # no CRLF churn on Windows
        assert b'\n    "' in raw                  # indent=4, one key per line

    def test_unknown_keys_in_the_local_file_are_preserved(self, isolated_config):
        (isolated_config / LOCAL).write_text('{"some_future_key": 42}', encoding="utf-8")
        w = _open()
        w.close()
        assert _local(isolated_config)["some_future_key"] == 42

    def test_corrupt_local_file_still_launches_with_defaults(self, isolated_config):
        (isolated_config / LOCAL).write_text("{ not json", encoding="utf-8")
        w = _open()
        try:
            defaults = json.loads((isolated_config / DEFAULTS).read_text(encoding="utf-8"))
            assert w.spn_n1.value() == defaults["n_dark_frames"]
        finally:
            w.close()
        _local(isolated_config)                   # and the close repaired it

    def test_local_file_overrides_defaults_key_by_key(self, isolated_config):
        (isolated_config / LOCAL).write_text('{"window_size": 11}', encoding="utf-8")
        w = _open()
        try:
            defaults = json.loads((isolated_config / DEFAULTS).read_text(encoding="utf-8"))
            assert w.spn_window.value() == 11
            assert w.spn_n1.value() == defaults["n_dark_frames"]   # untouched key
        finally:
            w.close()

    def test_failed_write_does_not_stop_the_window_closing(self, isolated_config, monkeypatch):
        import gui.main_window as mw
        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(mw.os, "replace", _boom)
        w = _open()
        w.close()                                  # must not raise
        assert not (isolated_config / LOCAL).exists()
        assert not any(p.suffix == ".tmp" for p in isolated_config.iterdir())


# ---------------------------------------------------------------------------
# Only a real camera's settings
# ---------------------------------------------------------------------------

class TestOnlyRealCamera:
    def test_camera_without_the_marker_saves_nothing(self, isolated_config):
        w = _open(_StubCamera)
        _set_everything(w)
        w.close()
        assert not (isolated_config / LOCAL).exists()

    def test_folder_playback_saves_nothing(self, isolated_config):
        w = _open(_FolderLikeCamera)
        _set_everything(w)
        w.close()
        assert not (isolated_config / LOCAL).exists()

    def test_real_camera_class_carries_the_marker(self):
        from camera import CameraThread
        assert CameraThread.persists_settings is True

    def test_mock_cameras_do_not(self):
        from folder_camera import FolderMockCamera
        from mock_camera import MockCameraThread
        assert not getattr(FolderMockCamera, "persists_settings", False)
        assert not getattr(MockCameraThread, "persists_settings", False)


# ---------------------------------------------------------------------------
# Results folder and recording name
# ---------------------------------------------------------------------------

class TestOutputFolder:
    def test_chosen_folder_is_saved(self, isolated_config, tmp_path, monkeypatch):
        chosen = tmp_path / "results"
        chosen.mkdir()
        w = _open()
        w._output_root = chosen
        w.close()
        assert _local(isolated_config)["output_root"] == str(chosen)

    def test_remembered_folder_does_not_skip_the_dialog(self, isolated_config, tmp_path):
        (isolated_config / LOCAL).write_text(
            json.dumps({"output_root": str(tmp_path)}), encoding="utf-8")
        w = _open()
        try:
            assert w._output_root is None          # so the dialog will still open
            assert w._last_output_root == tmp_path
        finally:
            w.close()

    def test_dialog_opens_in_the_remembered_folder(self, isolated_config, tmp_path, monkeypatch):
        (isolated_config / LOCAL).write_text(
            json.dumps({"output_root": str(tmp_path)}), encoding="utf-8")
        seen = []

        def _dialog(parent, caption, directory="", *a, **k):
            seen.append(directory)
            return ""                              # operator cancels

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(_dialog))
        w = _open()
        try:
            w._start_dark_cal()
        finally:
            w.close()
        assert seen == [str(tmp_path)]

    def test_recording_name_is_not_carried_over(self, isolated_config):
        w = _open()
        w.txt_recording_name.setText("subject03_rest")
        w.close()
        assert "subject03_rest" not in (isolated_config / LOCAL).read_text(encoding="utf-8")
        w2 = _open()
        try:
            assert w2.txt_recording_name.text() == ""
        finally:
            w2.close()
