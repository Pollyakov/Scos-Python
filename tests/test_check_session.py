"""
tools/check_session.py — the rig-trip helper that prints a session folder's
numbers with a verdict each (docs/rig_trip_U6_U10_U11.md). The session files
are built with the app's own writers, so a change to the file layout that
breaks the helper fails here, not at the rig.

Verdicts: OK / NOT OK only for hard rules, CHECK for everything that has no
hard rule (an estimate, the subject, a case that is fine in some sessions).
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.crop import Crop
from core.recorder import (CALIBRATION_FILENAME, RESULTS_FILENAME, HDF5Recorder,
                           write_calibration)
from tools import check_session

CUT = Crop(offset_x=16, offset_y=10, width=24, height=18,
           sensor_width=64, sensor_height=48)
PARAMS = {"exposureTime": 8.0, "gain": 8.0, "frameRate": 20.0, "windowSize": 7,
          "ROI": np.array([28.0, 19.0, 6.0]), "bitDepth": 12}
RATE_OFF = ("10:00:01 INFO camera — Frame-rate limit off — camera at its maximum "
            "(camera reports 118.0 fps; exposure {exp} µs, gain {gain} dB, Mono12, "
            "24 × 18 at (16, 10); USB limit on)\n")
GOOD_LOG = (
    "10:00:00 INFO core.frame_clock — Camera clock accepted — 1000 MHz ticks agree\n"
    + RATE_OFF.format(exp="8000.0", gain="8.00")
    + "10:00:02 INFO gui.main_window — Dark calibration took 6.0 s; the camera delivered 100.0 fps\n"
    "10:00:03 INFO gui.main_window — something unrelated\n"
    "10:05:00 INFO gui.main_window — Laser-off check passed — 0.3 DU < 11.4 DU\n"
    "10:05:01 INFO gui.main_window — Frame accounting — time source camera; "
    "0 frame(s) lost at the camera, 0 dropped from the processing queue\n")


def _session(folder, *, times=None, source="camera", lost=0, dark_crop=CUT,
             dark_duration=6.0, camera_rate=118.0, n_frames=600, log=GOOD_LOG,
             old=False):
    """A session folder. old=True: as written before U10/U11 (2026-10-08)."""
    times = [0.05 * i for i in range(6)] if times is None else times
    meta = {"camera_sn": "40075248", "time_source": source,
            "frames_lost_camera": lost, "frames_dropped_queue": 0}
    rec = HDF5Recorder(folder / RESULTS_FILENAME, meta, dict(PARAMS),
                       crop=None if old else CUT)
    for t in times:
        rec.append(t, 0.1, 0.008, 110.0)
    rec.close()
    if old:
        os.rename(folder / RESULTS_FILENAME, folder / check_session.OLD_RESULTS_FILENAME)
    shape = (CUT.height, CUT.width)
    dark_attrs = {"n_frames": n_frames, "window_size": 7}
    if not old:
        dark_attrs.update(duration_s=dark_duration, camera_frame_rate_hz=camera_rate)
    write_calibration(folder / CALIBRATION_FILENAME, "dark",
                      {"mean_dark": np.full(shape, 0.5), "var_dark": np.ones(shape)},
                      dark_attrs, crop=None if old else dark_crop)
    write_calibration(folder / CALIBRATION_FILENAME, "bright",
                      {"spIm": np.ones(shape), "spVar": np.ones(shape)},
                      {"n_frames": n_frames, "window_size": 7}, crop=None if old else CUT)
    if log is not None:
        (folder / "app.log").write_text(log, encoding="utf-8")


def _run(folder, capsys):
    code = check_session.main([str(folder)])
    return code, capsys.readouterr().out


def _verdict(out, label):
    """The verdict printed on the row whose label starts with `label`."""
    for line in out.splitlines():
        if line[11:].startswith(label):
            return line[3:9].strip()
    raise AssertionError(f"no row {label!r} in:\n{out}")


def test_a_good_session_has_no_not_ok(tmp_path, capsys):
    _session(tmp_path)
    code, out = _run(tmp_path, capsys)
    assert code == 0
    for label in ("time_source", "frames_lost_camera", "time step median",
                  "time step min / max", "first time step", "k2_corr <= 0",
                  "dark frames", "dark duration", "dark camera rate", "dark arrays",
                  "bright arrays", "same image area", "dark exposure / gain"):
        assert _verdict(out, label) == "OK", label
    assert _verdict(out, "k2_corr mean") == "CHECK"        # depends on the subject
    assert _verdict(out, "mean_dark") == "CHECK"           # depends on the black level
    assert "Summary:" in out and "0 NOT OK" in out
    assert "Laser-off check passed" in out
    assert "something unrelated" not in out


def test_another_crop_in_the_calibration_is_not_ok(tmp_path, capsys):
    _session(tmp_path, dark_crop=Crop(0, 0, 24, 18, 64, 48))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "same image area") == "NOT OK"


def test_a_dark_calibration_no_faster_than_the_set_rate_is_not_ok(tmp_path, capsys):
    """599 / 20 Hz = 30 s: U11 gave no speed-up (rig_checklist R5g)."""
    _session(tmp_path, dark_duration=30.0, camera_rate=20.0)
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "dark duration") == "NOT OK"
    assert _verdict(out, "dark camera rate") == "NOT OK"


def test_a_gap_in_timevec_is_not_ok(tmp_path, capsys):
    _session(tmp_path, times=[0.0, 0.05, 0.10, 0.20, 0.25])     # one frame missing
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "time step min / max") == "NOT OK"


def test_a_first_step_from_the_other_clock_is_not_ok(tmp_path, capsys):
    """D10: a first point stamped by the PC, the rest by the camera."""
    _session(tmp_path, times=[0.0, 0.043] + [0.043 + 0.05 * i for i in range(1, 6)])
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "first time step") == "NOT OK"


def test_exposure_changed_in_the_dark_calibration_is_not_ok(tmp_path, capsys):
    _session(tmp_path, log=RATE_OFF.format(exp="4000.0", gain="8.00"))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "dark exposure / gain") == "NOT OK"


def test_gain_changed_in_the_dark_calibration_is_not_ok(tmp_path, capsys):
    _session(tmp_path, log=RATE_OFF.format(exp="8000.0", gain="12.00"))
    code, out = _run(tmp_path, capsys)
    assert _verdict(out, "dark exposure / gain") == "NOT OK"


def test_lost_frames_are_not_ok_and_say_when_that_is_expected(tmp_path, capsys):
    _session(tmp_path, lost=3)
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "frames_lost_camera") == "NOT OK"
    assert "R7 overload test" in out


def test_pc_clock_is_check_not_not_ok_even_with_jitter(tmp_path, capsys):
    _session(tmp_path, source="pc", times=[0.0, 0.03, 0.11, 0.15, 0.19])
    code, out = _run(tmp_path, capsys)
    assert code == 0
    assert _verdict(out, "time_source") == "CHECK"
    assert _verdict(out, "time step, ms") == "CHECK"


def test_a_short_test_session_is_check(tmp_path, capsys):
    _session(tmp_path, n_frames=60, dark_duration=0.6)
    code, out = _run(tmp_path, capsys)
    assert code == 0
    assert _verdict(out, "dark frames") == "CHECK"


def test_a_session_from_before_u10_and_u11_has_no_not_ok(tmp_path, capsys):
    """Like the first rig session (2026-10-08): old file name, no crop, no
    dark duration, no rate line in app.log."""
    _session(tmp_path, old=True, log="10:00:00 INFO Camera clock accepted\n")
    code, out = _run(tmp_path, capsys)
    assert code == 0
    assert "rBfi_results.h5" in out
    for label in ("image area (crop)", "dark duration", "dark arrays", "same image area",
                  "dark exposure / gain"):
        assert _verdict(out, label) == "CHECK", label


def test_log_lines_carry_their_verdict(tmp_path, capsys):
    _session(tmp_path, log=GOOD_LOG
             + "10:06:00 WARNING camera — Camera: 3 frame(s) lost — Pylon buffers full\n"
             + "10:07:00 WARNING gui.main_window — Laser-off check skipped — no frame\n")
    code, out = _run(tmp_path, capsys)
    lines = {l.split("—", 1)[-1].strip()[:20]: l[3:9].strip()
             for l in out.splitlines() if "—" in l}
    assert lines["Camera: 3 frame(s) l"] == "NOT OK"
    assert lines["Laser-off check skip"] == "CHECK"
    # "0 frame(s) lost at the camera" in the accounting line is not a loss.
    assert lines["Frame accounting — t"] == "-"


def test_missing_app_log_is_pointed_out(tmp_path, capsys):
    _session(tmp_path, log=None)
    code, out = _run(tmp_path, capsys)
    assert "no app.log in this folder" in out


def test_not_a_folder(tmp_path, capsys):
    assert check_session.main([str(tmp_path / "nope")]) == 2
