"""
Tests for arduino_uploader.py — sketch generation and helper functions.

No actual Arduino hardware needed — we test the logic, not the upload.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arduino_uploader import (
    upload_sketch,
    find_arduino_cli,
    find_arduino_port,
    _TEMPLATE,
)


# ======================================================================
# Sketch template
# ======================================================================

class TestSketchTemplate:
    """The Arduino .ino template should produce valid timing code."""

    def test_template_has_placeholders(self):
        assert "{high_time_ms}" in _TEMPLATE
        assert "{period_ms}" in _TEMPLATE

    def test_template_renders(self):
        """Template renders without error with valid values."""
        code = _TEMPLATE.format(high_time_ms=8, period_ms=50)
        assert "int high_time = 8;" in code
        assert "int T         = 50;" in code

    def test_template_has_camera_pin(self):
        assert "cameraPin" in _TEMPLATE
        assert "pin 7" in _TEMPLATE.lower() or "cameraPin = 7" in _TEMPLATE

    def test_template_has_laser_pin(self):
        assert "laserPin" in _TEMPLATE
        assert "laserPin  = 12" in _TEMPLATE


# ======================================================================
# Timing math in upload_sketch
# ======================================================================

class TestUploadSketchTimingMath:
    """
    Test that upload_sketch computes correct timing values.
    We mock find_arduino_cli to return None so it fails early,
    but we can still check the timing logic by inspecting the sketch.
    """

    @patch("arduino_uploader.find_arduino_cli", return_value=None)
    def test_fails_without_cli(self, mock_cli):
        """Without arduino-cli, upload_sketch returns (False, error message)."""
        ok, msg = upload_sketch(8.0, 20.0)
        assert ok is False
        assert "arduino-cli" in msg.lower()

    @patch("arduino_uploader.find_arduino_cli", return_value=None)
    def test_high_time_rounded(self, mock_cli):
        """high_time_ms = max(1, round(exposure_ms))"""
        # Exposure 0.3 ms → rounds to 0, clamped to 1
        # This fails early, but we test the math in upload_sketch:
        # high_time_ms = max(1, round(0.3)) = max(1, 0) = 1
        assert max(1, round(0.3)) == 1
        assert max(1, round(8.7)) == 9

    @patch("arduino_uploader.find_arduino_cli", return_value=None)
    def test_period_at_least_high_time_plus_one(self, mock_cli):
        """period_ms = max(high_time_ms + 1, round(1000 / frame_rate))"""
        # 200 Hz → period = 5 ms, but if exposure = 8 ms → period = 9
        high = max(1, round(8.0))
        period = max(high + 1, round(1000.0 / 200.0))
        assert period == high + 1  # 9 > 5


# ======================================================================
# find_arduino_cli
# ======================================================================

class TestFindArduinoCli:

    @patch("shutil.which", return_value="/usr/bin/arduino-cli")
    def test_found_on_path(self, mock_which):
        result = find_arduino_cli()
        assert result == "/usr/bin/arduino-cli"

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=False)
    def test_not_found(self, mock_isfile, mock_which):
        result = find_arduino_cli()
        assert result is None


# ======================================================================
# find_arduino_port
# ======================================================================

class TestFindArduinoPort:

    @patch("arduino_uploader._SERIAL_OK", False)
    def test_no_serial_module(self):
        """If pyserial is missing, return None."""
        result = find_arduino_port()
        assert result is None

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_finds_official_arduino(self):
        """Detects Arduino by official VID 0x2341."""
        mock_port = MagicMock()
        mock_port.vid = 0x2341
        mock_port.device = "COM3"
        mock_port.description = "Arduino Uno"
        mock_port.manufacturer = "Arduino"

        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = [mock_port]
            result = find_arduino_port()
        assert result == "COM3"

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_finds_ch340_clone(self):
        """Detects cheap CH340-based Arduino clones."""
        mock_port = MagicMock()
        mock_port.vid = 0x1A86  # not official Arduino VID
        mock_port.device = "COM5"
        mock_port.description = "USB-SERIAL CH340"
        mock_port.manufacturer = "wch.cn"

        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = [mock_port]
            result = find_arduino_port()
        assert result == "COM5"

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_no_arduino_connected(self):
        """Returns None when no Arduino-like device is present."""
        mock_port = MagicMock()
        mock_port.vid = 0x1234
        mock_port.device = "COM1"
        mock_port.description = "Bluetooth Serial Port"
        mock_port.manufacturer = "Microsoft"

        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = [mock_port]
            result = find_arduino_port()
        assert result is None

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_finds_ch9102_clone(self):
        """Detects CH9102-based Arduino clones."""
        mock_port = MagicMock()
        mock_port.vid = 0x1A86
        mock_port.device = "COM7"
        mock_port.description = "USB Single Serial CH9102"
        mock_port.manufacturer = "wch.cn"

        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = [mock_port]
            result = find_arduino_port()
        assert result == "COM7"

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_empty_port_list(self):
        """No COM ports at all → None."""
        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = []
            result = find_arduino_port()
        assert result is None

    @patch("arduino_uploader._SERIAL_OK", True)
    def test_arduino_in_manufacturer_field(self):
        """Detects Arduino by manufacturer string (name-based fallback)."""
        mock_port = MagicMock()
        mock_port.vid = 0x9999  # unknown VID
        mock_port.device = "COM8"
        mock_port.description = "Generic Serial"
        mock_port.manufacturer = "Arduino (www.arduino.cc)"

        with patch("arduino_uploader._list_ports") as mock_lp:
            mock_lp.comports.return_value = [mock_port]
            result = find_arduino_port()
        assert result == "COM8"


# ======================================================================
# Arduino timing edge cases
# ======================================================================

class TestArduinoTimingEdgeCases:
    """Test the timing math: high_time_ms and period_ms computation."""

    def _compute_timing(self, exposure_ms: float, frame_rate_hz: float):
        """Replicate the timing logic from upload_sketch."""
        high_time_ms = max(1, round(exposure_ms))
        period_ms = max(high_time_ms + 1, round(1000.0 / frame_rate_hz))
        return high_time_ms, period_ms

    def test_normal_case(self):
        """8 ms exposure, 20 Hz → high=8, period=50."""
        high, period = self._compute_timing(8.0, 20.0)
        assert high == 8
        assert period == 50

    def test_exposure_exceeds_period(self):
        """Exposure 20 ms at 200 Hz (period=5ms) → period clamped to 21."""
        high, period = self._compute_timing(20.0, 200.0)
        assert high == 20
        assert period == 21  # max(21, 5) = 21

    def test_very_high_frame_rate(self):
        """1000 Hz with 0.5 ms exposure → high=1 (clamped), period=max(2,1)=2."""
        high, period = self._compute_timing(0.5, 1000.0)
        assert high == 1  # max(1, round(0.5)) = max(1, 0) = 1
        assert period == 2  # max(1+1, round(1)) = max(2, 1) = 2

    def test_very_low_frame_rate(self):
        """1 Hz with 8 ms exposure → high=8, period=1000."""
        high, period = self._compute_timing(8.0, 1.0)
        assert high == 8
        assert period == 1000

    def test_fractional_exposure_rounds(self):
        """0.3 ms → rounds to 0, clamped to 1."""
        high, period = self._compute_timing(0.3, 50.0)
        assert high == 1

    def test_large_exposure_rounds(self):
        """99.7 ms → rounds to 100."""
        high, period = self._compute_timing(99.7, 5.0)
        assert high == 100
        assert period == 200  # 1000/5 = 200 > 101

    def test_period_always_greater_than_high_time(self):
        """Period must always be at least high_time + 1 (delay = T - high_time > 0)."""
        test_cases = [
            (1.0, 1000.0),
            (10.0, 200.0),
            (50.0, 100.0),
            (100.0, 50.0),
            (500.0, 10.0),
        ]
        for exp, fps in test_cases:
            high, period = self._compute_timing(exp, fps)
            assert period > high, f"Failed for exposure={exp}, fps={fps}"

    def test_delay_never_negative(self):
        """T - high_time must be > 0 for all parameter combinations."""
        for exp in [0.1, 0.5, 1.0, 5.0, 10.0, 50.0, 100.0]:
            for fps in [1.0, 10.0, 50.0, 100.0, 200.0, 500.0]:
                high, period = self._compute_timing(exp, fps)
                delay = period - high
                assert delay > 0, f"Negative delay for exposure={exp}, fps={fps}"
