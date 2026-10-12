"""
Does the dark calibration give the same result at the camera's maximum frame
rate as at the set rate? Same-session A/B test on the rig (todo U11, plan
docs/Plan_U11_Dark_Cal_Max_Rate.md section 3, steps 6 and 8).

Takes three dark sets, one after the other, with the lens capped and the laser
off, through the app's own camera code (camera.py) and the app's own
collector (DarkCalCollector — the same math as the GUI's dark calibration):

    A  at the set rate (default 20 Hz)
    B  at the camera's maximum rate        <- what U11 changes
    C  at the set rate again

All three with the same exposure, gain, pixel format and image area. Then it
compares, inside the ROI circle, the mean of mean_dark and the mean of
var_dark:

  - A and C were taken at the same rate, so their difference is what changes
    on its own in a few minutes (sensor temperature, drift) — the noise floor.
    It is never taken as smaller than 3 x the statistical error of the
    comparison (N frames over P pixels), so a lucky A = C cannot fail B.
  - B was taken between them, so it is compared with their mean.
  - PASS: |B - (A + C) / 2| <= that floor, for both quantities.
  - FAIL: stop — do not use U11; tell Vika. Exit code 1.
  - INCONCLUSIVE (exit 2): the maximum rate was not faster than the set rate,
    so nothing was compared.

Usage, on the rig PC, app closed (the camera can only be open in one program),
Pylon Viewer closed:
    venv\\Scripts\\python.exe tools/dark_rate_ab.py
    venv\\Scripts\\python.exe tools/dark_rate_ab.py --roi 968 608 280 --cut
    venv\\Scripts\\python.exe tools/dark_rate_ab.py --frames 600 --exposure-ms 8 --gain-db 8

Use the session's settings (exposure, gain, pixel format, ROI circle as the
app's "ROI" label shows it, Cut Image or not). Defaults are the rig's: Mono12,
8 ms, 8 dB, 20 Hz, 600 frames, window 7, a centred circle.

Dry run without a camera (checks that the tool runs; the emulator's frames are
a test pattern, so its verdict means nothing):
    venv\\Scripts\\python.exe tools/dark_rate_ab.py --emulator --frames 20 --cut --roi 2048 2048 150
"""

import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

SETTLE_S = 0.5          # frames still in Pylon's buffers after a rate switch are dropped


def roi_stats(mean_dark: np.ndarray, var_dark: np.ndarray, mask: np.ndarray,
              n_frames: int) -> dict:
    """ROI means of the two calibration arrays, and the statistical error of
    each mean for one set of n_frames frames over the ROI's pixels (pixels
    taken as independent, which makes it a lower bound)."""
    m = float(mean_dark[mask].mean())
    v = float(var_dark[mask].mean())
    p = int(mask.sum())
    return {
        "mean_dark": m,
        "var_dark": v,
        "pixels": p,
        # mean of N samples per pixel: sqrt(var / N); averaged over P pixels
        "se_mean_dark": math.sqrt(max(v, 0.0) / (n_frames * p)),
        # sample variance of N samples: var * sqrt(2 / (N - 1)); over P pixels
        "se_var_dark": v * math.sqrt(2.0 / (n_frames - 1)) / math.sqrt(p),
    }


def judge(a: dict, b: dict, c: dict, key: str) -> dict:
    """B against the mean of A and C, allowed the larger of |A - C| and 3 x
    the statistical error of B - (A + C) / 2."""
    mid = (a[key] + c[key]) / 2
    dev = abs(b[key] - mid)
    se = b[f"se_{key}"] * math.sqrt(1.5)          # var(B) + var(mid) = SE^2 (1 + 1/2)
    allowed = max(abs(a[key] - c[key]), 3 * se)
    return {"key": key, "A": a[key], "B": b[key], "C": c[key], "mid": mid,
            "dev": dev, "a_minus_c": abs(a[key] - c[key]), "three_se": 3 * se,
            "allowed": allowed, "pass": dev <= allowed}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--frames", type=int, default=600, help="frames per set (default 600)")
    ap.add_argument("--fps", type=float, default=20.0, help="the set rate (default 20 Hz)")
    ap.add_argument("--exposure-ms", type=float, default=8.0, help="exposure, ms (default 8)")
    ap.add_argument("--gain-db", type=float, default=8.0, help="gain, dB (default 8)")
    ap.add_argument("--pixel-format", default="Mono12", help="default Mono12")
    ap.add_argument("--window", type=int, default=7, help="spatial window (default 7)")
    ap.add_argument("--roi", type=float, nargs=3, metavar=("CX", "CY", "R"),
                    help="ROI circle in sensor pixels (default: centred, largest that fits)")
    ap.add_argument("--cut", action="store_true",
                    help="crop on the camera around the ROI, as Cut Image does")
    ap.add_argument("--emulator", action="store_true",
                    help="use the pylon camera emulator (dry run; verdict meaningless)")
    ap.add_argument("--yes", action="store_true", help="do not ask to cap the lens")
    ap.add_argument("--save", type=Path, help="write the numbers to this JSON file")
    args = ap.parse_args()

    if args.emulator:
        os.environ["PYLON_CAMEMU"] = "1"
    from PyQt6.QtCore import QCoreApplication, Qt
    app = QCoreApplication.instance() or QCoreApplication([])   # noqa: F841 — QThread needs it
    from camera import CameraThread
    from core.crop import circle_mask, crop_for_circle
    from core.session import DarkCalCollector

    if not args.yes:
        input("Laser OFF and the lens capped (no light on the sensor)? Press Enter to start, "
              "Ctrl+C to stop. ")

    cam = CameraThread()
    cam.pixel_format = args.pixel_format
    cam.exposure_us = args.exposure_ms * 1000.0          # GUI ms -> camera µs
    cam.gain_db = args.gain_db
    cam.frame_rate = args.fps
    cam.trigger_mode = "Off"
    cam.open()
    info = cam.camera.GetDeviceInfo()
    sensor_w, sensor_h = cam.sensor_size()
    if args.roi:
        circ = {"cx": args.roi[0], "cy": args.roi[1], "r": args.roi[2]}
    else:
        circ = {"cx": sensor_w / 2, "cy": sensor_h / 2,
                "r": float(min(sensor_w, sensor_h) // 2 - 30)}
    if args.cut:
        want = crop_for_circle(circ["cx"], circ["cy"], circ["r"], sensor_w, sensor_h,
                               cam.camera_steps())
        cam.roi_position = (want.offset_x, want.offset_y, want.width, want.height)

    # Collected on the camera thread, inside the grab loop (direct connection):
    # no event loop needed, and nothing is queued. A slow collector only makes
    # the camera thread fetch later; frames the camera exposed meanwhile wait
    # in Pylon's buffers or are lost — harmless for dark frames.
    lock = threading.Lock()
    target: dict = {"collector": None, "from": float("inf"), "first": None, "last": None}

    def _on_frame(frame, t_capture):
        with lock:
            col = target["collector"]
            if col is None or col.done or time.monotonic() < target["from"]:
                return
            col.add_frame(frame)
            if target["first"] is None:
                target["first"] = t_capture
            target["last"] = t_capture

    cam.frame_ready.connect(_on_frame, Qt.ConnectionType.DirectConnection)
    cam.start_capture()
    crop = cam.applied_crop
    mask = circle_mask(crop.shape, circ, (crop.offset_x, crop.offset_y))
    c = cam.camera
    print(f"camera   : {info.GetModelName()}  SN {info.GetSerialNumber()}")
    print(f"settings : {c.PixelFormat.Value}, exposure {c.ExposureTime.Value:.1f} µs, "
          f"gain {c.Gain.Value:.2f} dB, image {crop.width} × {crop.height} at "
          f"({crop.offset_x}, {crop.offset_y}) of {sensor_w} × {sensor_h}")
    print(f"ROI      : centre ({circ['cx']:.0f}, {circ['cy']:.0f}), r {circ['r']:.0f}, "
          f"{int(mask.sum())} pixels; {args.frames} frames per set, window {args.window}")

    results = {}
    try:
        for name, at_max in (("A", False), ("B", True), ("C", False)):
            cam.set_max_frame_rate(at_max)
            emitted0, lost0 = cam.frames_emitted, cam.frames_lost
            col = DarkCalCollector(args.frames, args.window)
            with lock:
                target.update(collector=col, first=None, last=None,
                              **{"from": time.monotonic() + SETTLE_S})
            t0 = time.monotonic()
            while not col.done:
                if time.monotonic() - t0 > args.frames / 2 + 60:   # ≥ 2 fps, else stuck
                    raise TimeoutError(f"set {name}: {col.n_collected} of {args.frames} "
                                       f"frames after {time.monotonic() - t0:.0f} s")
                time.sleep(0.05)
            with lock:
                target["collector"] = None
                first, last = target["first"], target["last"]
            total = time.monotonic() - t0                 # frames_emitted counts the settle too
            elapsed = total - SETTLE_S
            mean_dark, var_dark = col.result()
            stats = roi_stats(mean_dark, var_dark, mask, args.frames)
            stats.update(
                rate="maximum" if at_max else f"{args.fps:g} Hz",
                reported_fps=cam.resulting_frame_rate(),
                delivered_fps=(cam.frames_emitted - emitted0) / max(total, 1e-9),
                collected_fps=(args.frames - 1) / (last - first) if last and first and last > first else None,
                frames_lost=cam.frames_lost - lost0,
                exposure_us=float(c.ExposureTime.Value), gain_db=float(c.Gain.Value),
                pixel_format=str(c.PixelFormat.Value),
            )
            results[name] = stats
            print(f"set {name} ({stats['rate']:>8s}): {elapsed:5.1f} s, camera delivered "
                  f"{stats['delivered_fps']:6.1f} fps, {stats['frames_lost']} lost; "
                  f"mean_dark {stats['mean_dark']:.4f} DU, var_dark {stats['var_dark']:.5f} DU²")
    finally:
        cam.set_max_frame_rate(False)          # never leave the camera at its maximum
        cam.close()

    a, b, cc = results["A"], results["B"], results["C"]
    same = all(r["exposure_us"] == a["exposure_us"] and r["gain_db"] == a["gain_db"]
               and r["pixel_format"] == a["pixel_format"] for r in (b, cc))
    verdicts = [judge(a, b, cc, "mean_dark"), judge(a, b, cc, "var_dark")]
    print()
    for v in verdicts:
        rel = 100 * v["dev"] / abs(v["mid"]) if v["mid"] else float("nan")
        print(f"{v['key']:9s}: B − (A+C)/2 = {v['dev']:.5g} ({rel:.2f} %), allowed "
              f"{v['allowed']:.5g} (|A − C| {v['a_minus_c']:.5g}, 3 SE {v['three_se']:.5g})"
              f"  → {'pass' if v['pass'] else 'FAIL'}")
    faster = b["delivered_fps"] > 1.2 * max(a["delivered_fps"], cc["delivered_fps"])
    if not same:
        verdict, code = "FAIL — exposure, gain or pixel format changed between the sets", 1
    elif not faster:
        verdict, code = (f"INCONCLUSIVE — the maximum rate ({b['delivered_fps']:.1f} fps) was "
                         f"not faster than the set rate; nothing was compared"), 2
    elif all(v["pass"] for v in verdicts):
        verdict, code = "PASS — the maximum rate gives the same dark calibration", 0
    else:
        verdict, code = "FAIL — the maximum rate changes the dark calibration; do not use U11, ask Vika", 1
    print(f"\n{verdict}")
    if args.save:
        args.save.write_text(json.dumps(
            {"camera": info.GetModelName(), "serial": info.GetSerialNumber(),
             "roi": circ, "crop": [crop.offset_x, crop.offset_y, crop.width, crop.height],
             "frames": args.frames, "window": args.window, "sets": results,
             "verdicts": verdicts, "verdict": verdict}, indent=2), encoding="utf-8")
        print(f"saved {args.save}")
    return code


if __name__ == "__main__":
    sys.exit(main())
