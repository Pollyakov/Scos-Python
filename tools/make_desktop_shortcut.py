"""
Put an "SCOS" icon on this PC's desktop that starts the app with the real camera.

Run once on each PC (the developer's and the rig's), from the repo, after setup.bat:
    venv\\Scripts\\python.exe tools\\make_desktop_shortcut.py

Running it again replaces the shortcut — do that if the repo folder moves.

The shortcut runs main.py with the venv's pythonw.exe: the project's own
packages, and no console window. A console window would be a way to kill the
app mid-measurement — closing it ends the app without saving the results file
or putting the camera's frame-rate limit back.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHONW = REPO / "venv" / "Scripts" / "pythonw.exe"
MAIN = REPO / "main.py"
ICON = REPO / "assets" / "scos.ico"
SHORTCUT_NAME = "SCOS.lnk"


def _ps_quote(value: object) -> str:
    """A PowerShell single-quoted string: a ' inside is written twice."""
    return "'" + str(value).replace("'", "''") + "'"


def powershell_script() -> str:
    # GetFolderPath('Desktop') finds the desktop also when OneDrive has moved it.
    return "; ".join([
        "$desktop = [Environment]::GetFolderPath('Desktop')",
        f"$path = Join-Path $desktop {_ps_quote(SHORTCUT_NAME)}",
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)",
        f"$s.TargetPath = {_ps_quote(PYTHONW)}",
        f"$s.Arguments = {_ps_quote(chr(34) + str(MAIN) + chr(34))}",
        f"$s.WorkingDirectory = {_ps_quote(REPO)}",
        f"$s.IconLocation = {_ps_quote(str(ICON) + ',0')}",
        "$s.Description = 'SCOS - cerebral blood flow measurement (real camera)'",
        "$s.Save()",
        "Write-Output $path",
    ])


def main() -> int:
    if sys.platform != "win32":
        print("Desktop shortcuts are made for Windows only.")
        return 1
    for needed, hint in ((PYTHONW, "run setup.bat first"),
                         (ICON, "run tools/make_icon.py first")):
        if not needed.is_file():
            print(f"Not found: {needed} — {hint}.")
            return 1
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", powershell_script()],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("Could not make the shortcut:\n" + (r.stderr or r.stdout).strip())
        return 1
    print(f"Made {r.stdout.strip()}")
    print(f"  runs   {PYTHONW} \"{MAIN}\"")
    print(f"  in     {REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
