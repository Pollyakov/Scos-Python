"""
Generates and uploads an Arduino sketch to synchronise camera trigger + laser.

Each Arduino loop() = one camera frame:
  high_time (ms) = exposure time
  T         (ms) = 1000 / frame_rate   (period)

Pin 7  → camera trigger (Line2)
Pin 12 → laser enable
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Pin assignments (hard-coded in the sketch template below)
CAMERA_TRIGGER_PIN = 7   # Arduino pin → Basler Line2
LASER_ENABLE_PIN   = 12  # Arduino pin → laser enable

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
        logger.warning("pyserial not available — cannot auto-detect Arduino port")
        return None
    all_ports = list(_list_ports.comports())
    logger.debug("Scanning %d COM port(s) for Arduino", len(all_ports))
    for port in all_ports:
        desc = (port.description or "").lower()
        mfr  = (port.manufacturer or "").lower()
        logger.debug("  %s — desc=%r  mfr=%r  vid=0x%04X",
                     port.device, port.description, port.manufacturer,
                     port.vid or 0)
        # Official Arduino boards (VID 0x2341)
        if port.vid == 0x2341:
            logger.info("Arduino found (official VID) on %s", port.device)
            return port.device
        # Name-based fallback
        if "arduino" in desc or "arduino" in mfr:
            logger.info("Arduino found (name match) on %s", port.device)
            return port.device
        # CH340/CH341/CH9102 — common clones
        if any(x in desc for x in ("ch340", "ch341", "ch9102")):
            logger.info("Arduino clone (%s) found on %s", port.description, port.device)
            return port.device
    logger.warning("No Arduino detected on any COM port")
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

    logger.info(
        "Arduino upload — exposure=%.1f ms  frame_rate=%.1f Hz  "
        "high_time=%d ms  period=%d ms  "
        "camera→pin%d  laser→pin%d",
        exposure_ms, frame_rate_hz, high_time_ms, period_ms,
        CAMERA_TRIGGER_PIN, LASER_ENABLE_PIN,
    )

    def log(msg: str):
        logger.info("Arduino: %s", msg)
        if on_progress:
            on_progress(msg)

    # --- locate arduino-cli ---
    cli = find_arduino_cli()
    if not cli:
        logger.error("arduino-cli not found")
        return False, (
            "arduino-cli not found.\n"
            "Install Arduino IDE 2 or download arduino-cli from "
            "https://arduino.github.io/arduino-cli"
        )
    logger.debug("arduino-cli path: %s", cli)

    # --- locate board port ---
    if port is None:
        port = find_arduino_port()
    if port is None:
        logger.error("Arduino not detected on any COM port")
        return False, "No Arduino detected on any COM port. Check USB connection."
    logger.info("Using Arduino port: %s", port)

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
            err = (r.stderr or r.stdout).strip()
            logger.error("Compile failed: %s", err)
            return False, f"Compile error:\n{err}"
        logger.info("Sketch compiled OK")

        # Upload
        log(f"Uploading to Arduino on {port}…")
        r = subprocess.run(
            [cli, "upload", "-p", port, "--fqbn", fqbn, sketch_dir],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip()
            logger.error("Upload failed: %s", err)
            return False, f"Upload error:\n{err}"

        msg = f"Arduino ready — T={period_ms} ms, exposure={high_time_ms} ms on {port}"
        logger.info(msg)
        return True, msg

    except subprocess.TimeoutExpired:
        logger.error("Arduino operation timed out")
        return False, "Arduino operation timed out."
    except Exception as exc:
        logger.exception("Unexpected error during Arduino upload: %s", exc)
        return False, str(exc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
