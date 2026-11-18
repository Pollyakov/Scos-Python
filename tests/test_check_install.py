"""
The installation files a new user depends on (setup.bat, requirements.txt,
tools/check_install.py, README.md) - added 2026-11-18, after the v0 tag.

requirements.txt pins every library to the version the app was tested with:
an unpinned line would let a later install bring a newer numpy or PyQt6 than
the one the accuracy tests ran on.
"""

import os
import re
import sys
from importlib import metadata
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import check_install

REPO = Path(__file__).resolve().parent.parent


def _requirement_lines(name: str) -> list[str]:
    lines = (REPO / name).read_text(encoding="utf-8").splitlines()
    return [l.split("#", 1)[0].strip() for l in lines if l.split("#", 1)[0].strip()]


def test_every_library_is_pinned_to_one_version():
    lines = _requirement_lines("requirements.txt")
    assert lines
    for line in lines:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==\S+", line), f"not pinned: {line!r}"
    assert len(check_install.pinned_requirements(REPO / "requirements.txt")) == len(lines)


def test_this_environment_has_the_pinned_versions():
    """The tests then run on the versions a new user gets from setup.bat."""
    for name, wanted in check_install.pinned_requirements(REPO / "requirements.txt").items():
        assert metadata.version(name) == wanted, name


def test_the_libraries_the_app_imports_are_pinned():
    pins = check_install.pinned_requirements(REPO / "requirements.txt")
    for needed in ("pypylon", "PyQt6", "pyqtgraph", "numpy", "scipy",
                   "opencv-python", "pyserial", "tifffile", "h5py", "matplotlib"):
        assert any(check_install._same_name(needed, p) for p in pins), needed


def test_gain_table_serials_lists_the_rig_camera():
    table = check_install.gain_table_serials(REPO / "CamerasMeasuredGain.csv")
    assert table["40075248"] == {12}            # DAN01, the rig camera
    assert table["40513592"] == {10, 12}        # Nadav06
    assert "" not in table                      # the empty second line


def test_a_problem_gives_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr(check_install, "pinned_requirements",
                        lambda path: {"no-such-library-xyz": "1.0"})
    assert check_install.main() == 1
    out = capsys.readouterr().out
    assert "PROBLEM  library no-such-library-xyz is not installed" in out


def test_a_good_installation_gives_exit_code_0(monkeypatch, capsys):
    # The camera and the Arduino software depend on the PC: left out.
    monkeypatch.setattr(check_install, "check_camera", lambda r: None)
    monkeypatch.setattr(check_install, "check_arduino", lambda r: None)
    assert check_install.main() == 0
    out = capsys.readouterr().out
    assert "PROBLEM" not in out and "No problems found." in out
    assert out.isascii()                        # prints in any Windows console


def test_setup_bat_is_plain_ascii_with_windows_line_ends():
    """cmd.exe misreads a batch file with Unix line ends or non-ASCII text."""
    raw = (REPO / "setup.bat").read_bytes()
    assert raw.isascii()
    assert b"\n" in raw and raw.count(b"\r\n") == raw.count(b"\n")
    for needed in (b"requirements.txt", b"tools\\make_desktop_shortcut.py",
                   b"tools\\check_install.py"):
        assert needed in raw


def test_readme_names_files_that_exist():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for name in ("setup.bat", "tools\\synth_tiff.py", "tools\\check_session.py",
                 "tools\\check_install.py", "requirements-dev.txt",
                 "CamerasMeasuredGain.csv", "docs/SCOS_protocol.md"):
        assert name in text, name
        assert (REPO / name.replace("\\", "/")).is_file(), name
