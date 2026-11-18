"""
Check that SCOS is installed correctly on this PC. setup.bat runs it as its
last step; run it again any time something looks wrong:

    venv\\Scripts\\python.exe tools\\check_install.py

Three kinds of lines:
    OK       fine
    NOTE     not needed to start the app, but read it (no camera connected, ...)
    PROBLEM  the app will not work properly until this is fixed

Exit code 0 = no PROBLEM line. Nothing is changed on the PC and the camera's
settings are not touched: a connected camera is only listed, never opened.
The text is plain ASCII on purpose - it must print in any Windows console.
"""

from __future__ import annotations

import csv
import re
import sys
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MIN_PYTHON = (3, 11)
TESTED_PYTHON = "3.13"


class Report:
    def __init__(self) -> None:
        self.problems = 0

    def ok(self, text: str) -> None:
        print(f"  OK       {text}")

    def note(self, text: str) -> None:
        print(f"  NOTE     {text}")

    def problem(self, text: str) -> None:
        self.problems += 1
        print(f"  PROBLEM  {text}")


def pinned_requirements(path: Path) -> dict[str, str]:
    """{package: version} for every "name==version" line of a requirements file."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==(\S+)", line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def _same_name(a: str, b: str) -> bool:
    """Package names compare without case, and - _ . are the same character."""
    norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()
    return norm(a) == norm(b)


def gain_table_serials(path: Path) -> dict[str, set[int]]:
    """{camera serial number: bit depths it has a measured G for}."""
    serials: dict[str, set[int]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in list(csv.reader(f))[1:]:
            if len(row) < 6 or not row[0].strip():
                continue
            try:
                serials.setdefault(row[0].strip(), set()).add(int(row[3]))
            except ValueError:
                continue
    return serials


def check_python(r: Report) -> None:
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro}"
    if v < MIN_PYTHON:
        r.problem(f"{text} is too old - SCOS needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")
    elif f"{v.major}.{v.minor}" != TESTED_PYTHON:
        r.note(f"{text} - SCOS was tested with Python {TESTED_PYTHON}")
    else:
        r.ok(text)


def check_libraries(r: Report) -> None:
    pins = pinned_requirements(REPO / "requirements.txt")
    if not pins:
        r.problem("requirements.txt lists no versions")
        return
    wrong = 0
    for name, wanted in pins.items():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            r.problem(f"library {name} is not installed (run setup.bat)")
            wrong += 1
            continue
        if have != wanted:
            r.problem(f"library {name} is version {have}, SCOS was tested with "
                      f"{wanted} (run setup.bat)")
            wrong += 1
    if not wrong:
        r.ok(f"all {len(pins)} libraries installed in the tested versions")


def check_app_code(r: Report) -> None:
    """The measurement code loads, and G is read from the measured table."""
    try:
        from processor import load_gain_from_table
        import core.recorder  # noqa: F401  (h5py, the results file)
        import core.results_figure  # noqa: F401  (matplotlib, the figure)
        from PyQt6.QtWidgets import QApplication  # noqa: F401
        import pyqtgraph  # noqa: F401
    except Exception as e:
        r.problem(f"the app's code does not load: {type(e).__name__}: {e}")
        return
    try:
        g = load_gain_from_table("40075248", 12, 8.0)
    except Exception as e:
        r.problem(f"the gain table CamerasMeasuredGain.csv cannot be read: {e}")
        return
    r.ok(f"the app's code loads; gain table readable (rig camera G = {g:.6f} DU/e)")


def check_code_version(r: Report) -> None:
    from core.recorder import _git_commit
    commit = _git_commit()
    if commit == "unknown":
        r.note("code version unknown - this folder is not a git clone (or Git is "
               "not installed), so results files will say gitCommit = 'unknown'. "
               "See README.md, 'Install'")
    elif commit.endswith("-dirty"):
        r.note(f"code version {commit} - files in this folder were changed since "
               f"that version")
    else:
        r.ok(f"code version {commit} (written into every results file)")


def check_camera(r: Report) -> None:
    try:
        from pypylon import pylon
        devices = pylon.TlFactory.GetInstance().EnumerateDevices()
    except Exception as e:
        r.problem(f"the camera library (pypylon) does not work: {e}")
        return
    if not devices:
        r.note("no Basler camera found - fine for playback; for measurements "
               "connect the camera and install Basler's pylon software")
        return
    table = gain_table_serials(REPO / "CamerasMeasuredGain.csv")
    for dev in devices:
        sn, model = dev.GetSerialNumber(), dev.GetModelName()
        if sn in table:
            depths = ", ".join(f"Mono{b}" for b in sorted(table[sn]))
            r.ok(f"camera {model} SN {sn} - in the gain table at {depths}")
        else:
            r.note(f"camera {model} SN {sn} is NOT in the gain table - the app "
                   f"will show the picture but refuse to measure with it")


def check_arduino(r: Report) -> None:
    try:
        from arduino_uploader import find_arduino_cli
        cli = find_arduino_cli()
    except Exception as e:
        r.note(f"could not look for the Arduino software: {e}")
        return
    if cli:
        r.ok("Arduino software found (needed for External Trigger)")
    else:
        r.note("Arduino software (Arduino IDE 2 or arduino-cli) not found - "
               "needed only for External Trigger")


def main() -> int:
    r = Report()
    print(f"SCOS installation check - {REPO}")
    check_python(r)
    check_libraries(r)
    if r.problems:
        # Loading the app's code with missing libraries only repeats the same
        # problem in a less readable form.
        print(f"\n{r.problems} problem(s) found.")
        return 1
    check_app_code(r)
    if not r.problems:
        check_code_version(r)
    check_camera(r)
    check_arduino(r)
    print(f"\n{r.problems} problem(s) found." if r.problems
          else "\nNo problems found.")
    return 1 if r.problems else 0


if __name__ == "__main__":
    sys.exit(main())
