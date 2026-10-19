"""
Print what the rig checklist asks to check in one session folder — the results
file, Calibration.h5 and the session's app.log — each line with a verdict, so
nothing has to be typed into a Python one-liner at the rig
(docs/rig_trip_U6_U10_U11.md).

Verdicts — given only where the rule is a hard fact:
    OK      the rule holds
    NOT OK  a hard rule is broken: copy the output and tell Claude / Vika
    CHECK   no hard rule exists (an estimate, a subject-dependent value, or
            a case that is fine in some sessions): compare by eye with the sheet
    -       information only

It only reads; nothing in the folder is changed. Exit code: 0 = no NOT OK,
1 = at least one NOT OK, 2 = not a folder.

Usage:
    venv\\Scripts\\python.exe tools/check_session.py "<session folder>"
"""

import argparse
import re
import sys
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.recorder import CALIBRATION_FILENAME, RESULTS_FILENAME

OLD_RESULTS_FILENAME = "rBfi_results.h5"     # sessions before 2026-10-11
PROTOCOL_FRAMES = 600                        # Dark / Bright Frames, the protocol's value

OK, NOT_OK, CHECK, INFO = "OK", "NOT OK", "CHECK", "-"

# app.log lines worth seeing at the rig, and the verdict each one carries.
# Matched case-sensitively; the first matching key wins, so the more specific
# keys come first.
LOG_KEYS = (
    ("Frame accounting",                 INFO),    # "… 0 frame(s) lost at the camera"
    ("Camera clock accepted",            OK),
    ("Camera clock not used",            CHECK),   # valid session, PC clock (R2d)
    ("Camera reports no frame numbers",  CHECK),   # a false alarm until Done item 41
    ("Camera opened",                    INFO),
    ("Cut Image",                        INFO),    # U6
    ("Camera image area",                INFO),    # U6: what the camera really uses
    ("Camera refused the image crop",    CHECK),   # the session goes on uncut
    ("Skipped",                          INFO),    # "Skipped N frame(s) of the previous image size"
    ("frame rate not restored",          NOT_OK),  # U11: the run was stopped
    ("Could not put the camera's frame rate back", NOT_OK),
    ("Frame-rate limit",                 INFO),    # U11: off / back on
    ("Dark calibration took",            INFO),    # U11
    ("State: PREVIEW → BRIGHT_CAL",      INFO),
    ("Bright calibration complete",      INFO),
    ("Measurement starts after the camera-clock check", INFO),   # D10
    ("frame(s) lost",                    NOT_OK),  # expected only in the R7 overload test
    ("overload",                         CHECK),
    ("Laser-off check passed",           OK),
    ("Laser-off check FAILED",           NOT_OK),
    ("Laser-off check",                  CHECK),   # skipped / could not run
)

# "Frame-rate limit off — camera at its maximum (…; exposure 8000.0 µs, gain 8.00 dB, …)"
# (camera.py). The "µ" is left out of the pattern on purpose.
_RATE_OFF = re.compile(r"Frame-rate limit off.*?exposure ([\d.]+) \S*s, gain ([\d.]+) dB")


class Report:
    """Rows of (verdict, label, value, hint), printed with the verdict first."""

    def __init__(self) -> None:
        self.counts = {OK: 0, NOT_OK: 0, CHECK: 0, INFO: 0}

    def row(self, verdict: str, label: str, value, hint: str = "") -> None:
        self.counts[verdict] += 1
        text = f"  [{verdict:6s}] {label:22s} {value}"
        print(text + (f"   <- {hint}" if hint else ""))

    def line(self, verdict: str, text: str) -> None:
        self.counts[verdict] += 1
        print(f"  [{verdict:6s}] {text}")


def _crop(group) -> dict | None:
    return {k: int(v) for k, v in group["crop"].attrs.items()} if "crop" in group else None


def _fmt_crop(c: dict | None) -> str:
    if c is None:
        return "none recorded"
    return (f"{c['Width']} x {c['Height']} at ({c['OffsetX']}, {c['OffsetY']}) "
            f"of the {c['SensorWidth']} x {c['SensorHeight']} sensor")


def _time_steps(rep: Report, t: np.ndarray, rate_hz: float | None, source: str) -> None:
    d = np.diff(t) * 1000.0
    median, lo, hi, first = float(np.median(d)), float(d.min()), float(d.max()), float(d[0])
    if not rate_hz:
        rep.row(CHECK, "time step, ms", f"median {median:.2f}  min {lo:.2f}  max {hi:.2f}  "
                f"first {first:.2f}", "no frame rate in Params to compare with")
        return
    period = 1000.0 / rate_hz
    if source != "camera":
        rep.row(CHECK, "time step, ms", f"median {median:.2f}  min {lo:.2f}  max {hi:.2f}  "
                f"first {first:.2f}", f"PC clock: steps jitter; expect about {period:.1f}")
        return
    # Median: 3 % — the Arduino's period is a whole number of ms (30 Hz -> 33 ms).
    rep.row(OK if abs(median - period) <= 0.03 * period else NOT_OK,
            "time step median, ms", f"{median:.2f}", f"expected {period:.1f}")
    if hi >= 1.5 * period:
        rep.row(NOT_OK, "time step min / max", f"{lo:.2f} / {hi:.2f}",
                "a gap: frames are missing from timeVec")
    elif lo <= 0.5 * period:
        rep.row(NOT_OK, "time step min / max", f"{lo:.2f} / {hi:.2f}",
                "steps bunched together")
    elif lo >= 0.9 * period and hi <= 1.1 * period:
        rep.row(OK, "time step min / max", f"{lo:.2f} / {hi:.2f}")
    else:
        rep.row(CHECK, "time step min / max", f"{lo:.2f} / {hi:.2f}",
                f"more than 10 % from {period:.1f}")
    # D10: the first step must not join a PC stamp to a camera stamp.
    rep.row(OK if 0.9 * period <= first <= 1.1 * period else NOT_OK,
            "first time step, ms", f"{first:.2f}", "D10")


def results(rep: Report, path: Path) -> dict:
    print(f"== {path.name}")
    out: dict = {}
    with h5py.File(path, "r") as f:
        m = f["metadata"].attrs
        rep.row(INFO, "camera_sn", m.get("camera_sn", "(missing)"))
        source = str(m.get("time_source", ""))
        out["source"] = source
        if source == "camera":
            rep.row(OK, "time_source", source)
        else:
            rep.row(CHECK, "time_source", source or "(missing)",
                    "PC clock: valid, the reason is in app.log ('Camera clock not used')")
        for k in ("frames_lost_camera", "frames_dropped_queue"):
            if k not in m:
                rep.row(CHECK, k, "(missing)")
            else:
                n = int(m[k])
                rep.row(OK if n == 0 else NOT_OK, k, n,
                        "" if n == 0 else "frames lost (expected only in the R7 overload test)")
        out["crop"] = _crop(f["metadata"])
        rep.row(INFO if out["crop"] else CHECK, "image area (crop)", _fmt_crop(out["crop"]),
                "" if out["crop"] else "no crop recorded (session before U10)")
        start = f["startTime"][()]
        rep.row(INFO, "startTime", start.decode() if isinstance(start, bytes) else start)

        p = f["Params"].attrs
        out["rate"] = float(p["frameRate"]) if "frameRate" in p else None
        out["exposure_ms"] = float(p["exposureTime"]) if "exposureTime" in p else None
        out["gain_db"] = float(p["gain"]) if "gain" in p else None
        rep.row(INFO, "Params", f"exposure {p.get('exposureTime')} ms, gain {p.get('gain')} dB, "
                f"frame rate {p.get('frameRate')} Hz, window {p.get('windowSize')}, "
                f"ROI {np.round(p['ROI'], 1).tolist() if 'ROI' in p else '?'}, "
                f"code {p.get('gitCommit', '?')}")

        t = f["timeVec"][()].ravel()
        rep.row(OK if t.size else NOT_OK, "frames", t.size, "" if t.size else "no results")
        if t.size > 1:
            _time_steps(rep, t, out["rate"], source)
            rep.row(INFO, "duration", f"{t[-1] - t[0]:.1f} s")
        k2 = f["k2_corr"][()].ravel()
        if k2.size:
            bad = int(np.sum(~(k2 > 0)))
            rep.row(OK if bad == 0 else CHECK, "k2_corr <= 0", f"{bad} of {k2.size}",
                    "" if bad == 0 else "those points have no BFi")
            rep.row(CHECK, "k2_corr mean", f"{np.nanmean(k2):.5f}",
                    "depends on the subject; 0.0081 on 2026-10-08 - not 2x or 1/2x")
    return out


def calibration(rep: Report, path: Path, res: dict) -> dict:
    print(f"== {path.name}")
    crops = {}
    rate = res.get("rate")
    with h5py.File(path, "r") as f:
        for name in ("dark", "bright"):
            if name not in f:
                rep.row(NOT_OK, name, "(missing)")
                continue
            g, a = f[name], f[name].attrs
            n = int(a["n_frames"]) if "n_frames" in a else None
            rep.row(OK if n == PROTOCOL_FRAMES else CHECK, f"{name} frames", n,
                    "" if n == PROTOCOL_FRAMES else f"not the protocol's {PROTOCOL_FRAMES} (a short test?)")
            if name == "dark":
                _dark_rate(rep, a, n, rate)
            crops[name] = _crop(g)
            shapes = {k: g[k].shape for k in g if isinstance(g[k], h5py.Dataset)}
            c = crops[name]
            if c is None:
                rep.row(CHECK, f"{name} arrays", shapes, "no crop recorded to compare with")
            else:
                same = all(s == (c["Height"], c["Width"]) for s in shapes.values())
                rep.row(OK if same else NOT_OK, f"{name} arrays", shapes,
                        f"crop is {c['Height']} x {c['Width']}")
            if name == "dark" and "mean_dark" in g:
                mean_dark = float(np.mean(g["mean_dark"][()]))
                if "camera_black_level" in a:
                    # The offset the camera adds to every pixel (recorded since
                    # 2026-10-19); what is left above it is light or the sensor.
                    level = float(a["camera_black_level"])
                    rep.row(INFO, "camera black level", f"{level:g}")
                    rep.row(CHECK, "mean_dark - black level", f"{mean_dark - level:.3f} DU",
                            f"mean_dark {mean_dark:.3f} DU; light during the dark calibration "
                            "raises it - no measured value with the lens capped yet")
                else:
                    rep.row(CHECK, "mean_dark", f"{mean_dark:.3f} DU",
                            "about 0.5 on the rig (2026-10-08); depends on the camera's black level")
    return crops


def _dark_rate(rep: Report, a, n: int | None, rate: float | None) -> None:
    """U11: was the dark calibration faster than at the set rate?"""
    if "duration_s" not in a:
        rep.row(CHECK, "dark duration", "(not recorded)", "session before U11")
        return
    dur = float(a["duration_s"])
    if rate and n and n > 1:
        at_set_rate = (n - 1) / rate
        rep.row(OK if dur < 0.9 * at_set_rate else NOT_OK, "dark duration", f"{dur:.1f} s",
                f"{at_set_rate:.1f} s at {rate:g} Hz" if dur < 0.9 * at_set_rate else
                f"not faster than at {rate:g} Hz ({at_set_rate:.1f} s): the maximum rate "
                f"did not help - tell Claude")
    else:
        rep.row(CHECK, "dark duration", f"{dur:.1f} s", "no frame rate to compare with")
    if "camera_frame_rate_hz" not in a:
        rep.row(CHECK, "dark camera rate", "(not reported)", "the camera has no rate node")
    else:
        cam = float(a["camera_frame_rate_hz"])
        fast = rate is not None and cam > 1.1 * rate
        rep.row(OK if fast else NOT_OK if rate else CHECK, "dark camera rate", f"{cam:.1f} fps",
                "" if fast else "not above the set rate: was the limit really off?")


def log_lines(rep: Report, path: Path, res: dict) -> None:
    print(f"== {path.name}")
    rate_off = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            for key, verdict in LOG_KEYS:
                if key in line:
                    rep.line(verdict, line.rstrip()[:200])
                    break
            m = _RATE_OFF.search(line)
            if m:
                rate_off = (float(m.group(1)), float(m.group(2)))
    print()
    # The one accuracy-critical log check (R5g): the dark noise depends on the
    # exposure and gain, so the maximum rate must not have changed them.
    if rate_off is None:
        rep.row(CHECK, "dark exposure / gain", "no 'Frame-rate limit off' line",
                "playback, or this app.log is not from this session")
        return
    exp_ms, gain = rate_off[0] / 1000.0, rate_off[1]
    want_exp, want_gain = res.get("exposure_ms"), res.get("gain_db")
    if want_exp is None or want_gain is None:
        rep.row(CHECK, "dark exposure / gain", f"{exp_ms:g} ms / {gain:g} dB",
                "no Params to compare with")
        return
    same = abs(exp_ms - want_exp) <= 0.005 * want_exp + 1e-3 and abs(gain - want_gain) <= 0.01
    rep.row(OK if same else NOT_OK, "dark exposure / gain", f"{exp_ms:g} ms / {gain:g} dB",
            f"session: {want_exp:g} ms / {want_gain:g} dB"
            + ("" if same else " - CHANGED during the dark calibration: tell Claude"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("folder", type=Path, help="the session folder")
    args = ap.parse_args(argv)
    # The Windows console cannot show every character app.log contains (an
    # arrow, say): print a "?" for those instead of stopping with an error.
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:          # not a real console stream (tests)
        pass
    folder = args.folder
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return 2

    rep = Report()
    res_path = folder / RESULTS_FILENAME
    if not res_path.exists() and (folder / OLD_RESULTS_FILENAME).exists():
        res_path = folder / OLD_RESULTS_FILENAME
    res: dict = {}
    if res_path.exists():
        res = results(rep, res_path)
    else:
        rep.line(NOT_OK, f"no {RESULTS_FILENAME}")
    print()

    cal_path = folder / CALIBRATION_FILENAME
    crops = calibration(rep, cal_path, res) if cal_path.exists() else {}
    if not cal_path.exists():
        rep.line(NOT_OK, f"no {CALIBRATION_FILENAME}")
    print()

    if res and crops:
        all_crops = [res.get("crop"), *crops.values()]
        if all(c is None for c in all_crops):
            rep.row(CHECK, "same image area", "no crop recorded", "session before U10")
        else:
            same = all(c == res.get("crop") for c in crops.values())
            rep.row(OK if same else NOT_OK, "same image area",
                    "results, dark and bright" if same else "DIFFERENT",
                    "" if same else "the calibration does not belong to this measurement")
        print()

    log = folder / "app.log"
    if log.exists():
        log_lines(rep, log, res)
    else:
        rep.line(CHECK, "no app.log in this folder - copy it here: the app overwrites it "
                        "at every launch")

    c = rep.counts
    print()
    print(f"Summary: {c[OK]} OK, {c[CHECK]} CHECK, {c[NOT_OK]} NOT OK"
          + ("  - copy this output and tell Claude" if c[NOT_OK] else ""))
    return 1 if c[NOT_OK] else 0


if __name__ == "__main__":
    sys.exit(main())
