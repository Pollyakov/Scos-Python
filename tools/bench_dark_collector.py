"""
Time the dark-calibration collector, DarkCalCollector.add_frame(), per frame.

Written for todo D8 (2026-10-11), kept for U11: the dark calibration at the
camera's maximum frame rate is only as fast as this collector, so run it on the
PC that will measure (the rig PC) to know how many frames per second it can
take. The camera may be faster; then frames queue up and the calibration does
not finish sooner.

It times the current collector against a verbatim copy of the pre-D8
add_frame (a new array for every intermediate), alternating them in rounds so
a change in PC load hits both alike, and checks that the two give bit-for-bit
the same dark_mean and dark_var.

Usage (with the app closed — it competes for the same CPU and memory):
    venv\\Scripts\\python.exe tools/bench_dark_collector.py
    venv\\Scripts\\python.exe tools/bench_dark_collector.py --width 611 --height 611

Measured on the dev PC, 1216 x 1936 (2026-10-11): old 30.7, current 15.0
ms/frame, bit-identical.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.session import DarkCalCollector  # noqa: E402


class _PlainWelfordDark(DarkCalCollector):
    """add_frame exactly as it was before D8 (commit 6c700a4)."""

    def add_frame(self, frame):
        f = frame.astype(np.float64)
        self._n += 1
        if self._mean is None:
            self._mean = np.zeros_like(f)
            self._M2   = np.zeros_like(f)
        delta       = f - self._mean
        self._mean += delta / self._n
        self._M2   += delta * (f - self._mean)


def _time(cls, frames: list[np.ndarray], n: int) -> tuple[float, DarkCalCollector]:
    col = cls(n, 7)
    col.add_frame(frames[0])               # the first frame allocates; not timed
    t0 = time.perf_counter()
    for i in range(1, n):
        col.add_frame(frames[i % len(frames)])
    return (time.perf_counter() - t0) / (n - 1) * 1000, col


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--width",  type=int, default=1936, help="frame columns (default 1936)")
    ap.add_argument("--height", type=int, default=1216, help="frame rows (default 1216)")
    ap.add_argument("--frames", type=int, default=100, help="frames per round (default 100)")
    ap.add_argument("--rounds", type=int, default=5, help="rounds (default 5)")
    args = ap.parse_args()

    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 4096, (args.height, args.width), dtype=np.uint16)
              for _ in range(20)]
    print(f"{args.height} x {args.width} uint16 frames, {args.frames} per round, "
          f"{args.rounds} rounds")

    old_ms, new_ms = [], []
    for r in range(args.rounds):
        o, col_old = _time(_PlainWelfordDark, frames, args.frames)
        c, col_new = _time(DarkCalCollector, frames, args.frames)
        old_ms.append(o)
        new_ms.append(c)
        print(f"round {r + 1}: pre-D8 {o:6.2f} ms/frame   current {c:6.2f} ms/frame")

    same = all(np.array_equal(a, b) for a, b in zip(col_old.result(), col_new.result()))
    cur = float(np.median(new_ms))
    print(f"\nmedian: pre-D8 {np.median(old_ms):.2f} ms/frame, current {cur:.2f} ms/frame "
          f"({np.median(old_ms) / cur:.2f}x)")
    print(f"current collector alone keeps up with up to about {1000 / cur:.0f} frames/s "
          f"(the app's other per-frame work on the GUI thread comes on top)")
    print(f"results bit-identical: {same}")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
