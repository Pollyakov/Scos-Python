"""
Generates and uploads an Arduino sketch to synchronise camera trigger + laser.

Each Arduino loop() = one camera frame:
  high_time (ms) = exposure time
  T         (ms) = 1000 / frame_rate   (period)

Pin 7  → camera trigger (Line2)
Pin 12 → laser enable
"""

import os
import shutil
import subprocess
import tempfile
from typing import Callable, Optional, Tuple

try:
    import serial.tools.list_ports as _list_ports
    _SERIAL_OK = True
except ImportError:
    _SERIAL_OK = False

# ---------------------------------------------------------------------------
# Arduino sketch template
# ---------------------------------------------------------------------------
_TEMPLATE = """\
const int cameraPin = 7;
const int laserPin  = 12;

int high_time = {high_time_ms};  // ms  (exposure time)
int T         = {period_ms};     // ms  (1000 / frame_rate)

void setup() {{
  Serial.begin(9600);
  pinMode(cameraPin, OUTPUT);
  digitalWrite(cameraPin, LOW);
  pinMode(laserPin, OUTPUT);
  digitalWrite(laserPin, LOW);
}}

void loop() {{
    digitalWrite(cameraPin, HIGH);
    digitalWrite(laserPin, HIGH);
    delay(high_time);
    digitalWrite(cameraPin, LOW);
    digitalWrite(laserPin, LOW);
    delay(T - high_time);
}}
"""

# ---------------------------------------------------------------------------
# arduino-cli discovery
# ---------------------------------------------------------------------------
_CLI_CANDIDATES = [
    r"C:\Users\{user}\AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
    r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
    r"C:\Program Files (x86)\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
]


def find_arduino_cli() -> Optional[str]:
    """Return path to arduino-cli, or None if not found."""
    found = shutil.which("arduino-cli")
    if found:
        return found
    user = os.environ.get("USERNAME", "USER")
    for tmpl in _CLI_CANDIDATES:
        path = tmpl.replace("{user}", user)
        if os.path.isfile(path):
            return path
    return None


def find_arduino_port() -> Optional[str]:
    """Return the first COM port that looks like an Arduino."""
    if not _SERIAL_OK:
        return None
    for port in _list_ports.comports():
        desc = (port.description or "").lower()
        mfr  = (port.manufacturer or "").lower()
        # Official Arduino boards (VID 0x2341)
        if port.vid == 0x2341:
            return port.device
        # Name-based fallback
        if "arduino" in desc or "arduino" in mfr:
            return port.device
        # CH340/CH341/CH9102 — common clones
        if any(x in desc for x in ("ch340", "ch341", "ch9102")):
            return port.device
    return None


# ---------------------------------------------------------------------------
# Main upload function
# ---------------------------------------------------------------------------

def upload_sketch(
    exposure_ms: float,
    frame_rate_hz: float,
    port: Optional[str] = None,
    fqbn: str = "arduino:avr:uno",
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """
    Build and upload Arduino sketch with timing parameters.

    Parameters
    ----------
    exposure_ms   : camera exposure time in milliseconds
    frame_rate_hz : camera frame rate in Hz
    port          : COM port string (auto-detected when None)
    fqbn          : Arduino board identifier (default: arduino:avr:uno)
    on_progress   : optional callback(str) for status messages

    Returns
    -------
    (success: bool, message: str)
    """
    high_time_ms = max(1, round(exposure_ms))
    period_ms    = max(high_time_ms + 1, round(1000.0 / frame_rate_hz))

    def log(msg: str):
        if on_progress:
            on_progress(msg)

    # --- locate arduino-cli ---
    cli = find_arduino_cli()
    if not cli:
        return False, (
            "arduino-cli not found.\n"
            "Install Arduino IDE 2 or download arduino-cli from "
            "https://arduino.github.io/arduino-cli"
        )

    # --- locate board port ---
    if port is None:
        port = find_arduino_port()
    if port is None:
        return False, "No Arduino detected on any COM port. Check USB connection."

    # --- write sketch to temp directory ---
    sketch_src  = _TEMPLATE.format(high_time_ms=high_time_ms, period_ms=period_ms)
    tmpdir      = tempfile.mkdtemp(prefix="scos_ard_")
    sketch_name = "scos_trigger"
    sketch_dir  = os.path.join(tmpdir, sketch_name)
    os.makedirs(sketch_dir)
    with open(os.path.join(sketch_dir, sketch_name + ".ino"), "w") as fh:
        fh.write(sketch_src)

    try:
        # Compile
        log(f"Compiling sketch (T={period_ms} ms, exposure={high_time_ms} ms)…")
        r = subprocess.run(
            [cli, "compile", "--fqbn", fqbn, sketch_dir],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return False, f"Compile error:\n{(r.stderr or r.stdout).strip()}"

        # Upload
        log(f"Uploading to Arduino on {port}…")
        r = subprocess.run(
            [cli, "upload", "-p", port, "--fqbn", fqbn, sketch_dir],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return False, f"Upload error:\n{(r.stderr or r.stdout).strip()}"

        return True, (
            f"Arduino ready — T={period_ms} ms, exposure={high_time_ms} ms on {port}"
        )

    except subprocess.TimeoutExpired:
        return False, "Arduino operation timed out."
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
