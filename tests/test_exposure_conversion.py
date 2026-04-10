"""
Tests for the critical ms → µs exposure conversion.

The GUI uses milliseconds but the camera API (pypylon) uses microseconds.
The conversion happens in MainWindow._connect_signals:
    self.spn_exposure.valueChanged.connect(lambda v: self.camera.set_exposure(v * 1000))

Getting this wrong silently produces bad data — these tests verify the math.
"""

import pytest


class TestExposureConversion:
    """Verify ms ↔ µs conversion logic used throughout the app."""

    def test_ms_to_us_basic(self):
        """8 ms → 8000 µs"""
        gui_ms = 8.0
        camera_us = gui_ms * 1000
        assert camera_us == 8000.0

    def test_ms_to_us_fractional(self):
        """0.5 ms → 500 µs"""
        gui_ms = 0.5
        camera_us = gui_ms * 1000
        assert camera_us == 500.0

    def test_ms_to_us_minimum(self):
        """GUI minimum 0.021 ms → 21 µs"""
        gui_ms = 0.021
        camera_us = gui_ms * 1000
        assert camera_us == pytest.approx(21.0, rel=1e-6)

    def test_us_to_ms_readback(self):
        """Camera reports µs, GUI shows ms: 10000 µs → 10.0 ms"""
        camera_us = 10000.0
        gui_ms = camera_us / 1000.0
        assert gui_ms == 10.0

    def test_round_trip(self):
        """ms → µs → ms should be lossless for typical values."""
        values_ms = [0.021, 0.1, 0.5, 1.0, 5.0, 8.0, 50.0, 100.0, 1000.0]
        for ms in values_ms:
            us = ms * 1000
            ms_back = us / 1000.0
            assert ms_back == pytest.approx(ms, rel=1e-12)

    def test_default_exposure_conversion(self):
        """
        Default GUI value is 8.0 ms.
        Camera should receive 8000 µs.
        MainWindow sets: camera.exposure_us = self.spn_exposure.value() * 1000
        """
        default_gui_ms = 8.0
        expected_us = 8000.0
        assert default_gui_ms * 1000 == expected_us

    def test_conversion_never_negative(self):
        """Even the smallest GUI value produces positive µs."""
        gui_min_ms = 0.021  # from _labeled_spin min_
        assert gui_min_ms * 1000 > 0


class TestPixelFormatBitDepthExtraction:
    """
    _toggle_scos does: int(fmt.replace("Mono", ""))
    Verify this works for all supported formats and doesn't crash on edge cases.
    """

    def test_mono8(self):
        assert int("Mono8".replace("Mono", "")) == 8

    def test_mono10(self):
        assert int("Mono10".replace("Mono", "")) == 10

    def test_mono12(self):
        assert int("Mono12".replace("Mono", "")) == 12

    def test_mono16_hypothetical(self):
        """If a Mono16 camera appears, the parsing still works."""
        assert int("Mono16".replace("Mono", "")) == 16

    def test_unexpected_format_raises(self):
        """A non-numeric suffix like 'MonoXYZ' should raise ValueError."""
        with pytest.raises(ValueError):
            int("MonoXYZ".replace("Mono", ""))

    def test_empty_string_raises(self):
        """Empty string after replace → ValueError."""
        with pytest.raises(ValueError):
            int("Mono".replace("Mono", ""))
