"""
Benchmark SCOSProcessor.process() without a camera.
Simulates realistic frame sizes and reports per-frame timing.

Usage:
    python bench_processor.py
    python bench_processor.py --width 2448 --height 2048 --window 7 --frames 50
"""

import argparse
import time
import numpy as np
from processor import SCOSProcessor


def run(width, height, window, duration_s, bit_depth, camera_fps):
    rng   = np.random.default_rng(42)
    dtype = np.uint16 if bit_depth > 8 else np.uint8
    maxval = (1 << bit_depth) - 1

    proc = SCOSProcessor(window_size=window, gain_db=0.0, bit_depth=bit_depth)

    # Circular mask covering the centre 60 % of the frame
    cy, cx = height // 2, width // 2
    r      = int(min(height, width) * 0.30)
    ys, xs = np.ogrid[:height, :width]
    mask   = (ys - cy) ** 2 + (xs - cx) ** 2 <= r ** 2

    print(f"Frame: {width}×{height}  window: {window}  bit_depth: {bit_depth}  camera: {camera_fps} Hz")
    print(f"Mask pixels: {mask.sum():,}  /  {width*height:,} total")
    print(f"Running for {duration_s} s ...\n")

    durations = []
    deadline  = time.perf_counter() + duration_s
    i = 0
    while time.perf_counter() < deadline:
        frame = rng.integers(0, maxval, size=(height, width), dtype=dtype)

        t0 = time.perf_counter()
        proc.process(frame, mask)
        dt = (time.perf_counter() - t0) * 1000   # ms
        durations.append(dt)
        i += 1

        if i % 10 == 0:
            elapsed = duration_s - (deadline - time.perf_counter())
            print(f"  {elapsed:5.1f}s  frame {i:4d}: {dt:6.1f} ms  (running avg {sum(durations)/len(durations):.1f} ms)")

    durations = np.array(durations)
    budget_ms = 1000.0 / camera_fps
    headroom  = budget_ms - durations.mean()

    print(f"\n--- Results ({len(durations)} frames over {duration_s} s) ---")
    print(f"  mean : {durations.mean():.1f} ms")
    print(f"  std  : {durations.std():.1f} ms")
    print(f"  min  : {durations.min():.1f} ms")
    print(f"  max  : {durations.max():.1f} ms")
    print(f"  p50  : {np.percentile(durations, 50):.1f} ms")
    print(f"  p95  : {np.percentile(durations, 95):.1f} ms")
    print(f"  p99  : {np.percentile(durations, 99):.1f} ms")
    print(f"\n  Budget at {camera_fps} Hz : {budget_ms:.1f} ms/frame")
    if headroom >= 0:
        print(f"  Headroom        : {headroom:.1f} ms  (OK)")
    else:
        print(f"  Overrun         : {-headroom:.1f} ms  (GUI will lag!)")
    print(f"  Max sustainable : {1000/durations.mean():.1f} Hz  (vs {camera_fps} Hz camera)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--width",    type=int,   default=1920)
    p.add_argument("--height",   type=int,   default=1080)
    p.add_argument("--window",   type=int,   default=7)
    p.add_argument("--duration", type=float, default=30.0, help="wall-clock seconds to run")
    p.add_argument("--bits",     type=int,   default=12, choices=[8, 10, 12])
    p.add_argument("--fps",      type=float, default=50.0, help="camera frame rate for budget calc")
    args = p.parse_args()
    run(args.width, args.height, args.window, args.duration, args.bits, args.fps)
