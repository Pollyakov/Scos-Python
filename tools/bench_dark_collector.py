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

With --handler it times the app's whole per-frame work during DARK_CAL
instead (U11 step 1): MainWindow._on_scos_frame() — the collector plus the FPS
label — on an offscreen window, the intensity statistics it adds twice a second,
and the display update (_on_display_frame + one paint) the camera triggers up
to 30 times a second. From those three it works out how many dark frames per
second the GUI thread can take; a camera running faster than that only fills
Qt's queue.

Usage (with the app closed — it competes for the same CPU and memory):
    venv\\Scripts\\python.exe tools/bench_dark_collector.py
    venv\\Scripts\\python.exe tools/bench_dark_collector.py --width 611 --height 611
    venv\\Scripts\\python.exe tools/bench_dark_collector.py --handler [--width W --height H]

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
    ap.add_argument("--handler", action="store_true",
                    help="time the app's whole per-frame work during DARK_CAL (U11)")
    args = ap.parse_args()

    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 4096, (args.height, args.width), dtype=np.uint16)
              for _ in range(20)]
    if args.handler:
        return _bench_handler(frames, args.frames, args.rounds)
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


def _bench_handler(frames: list[np.ndarray], n: int, rounds: int) -> int:
    """Time MainWindow's per-frame work during DARK_CAL; see the module text."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # before pyqtgraph loads
    from core.crop import circle_mask
    from core.session import State
    from gui.main_window import MainWindow

    class _Camera(QObject):
        """Just enough of a frame source for MainWindow; never emits."""
        frame_ready   = pyqtSignal(np.ndarray, float)
        display_ready = pyqtSignal(np.ndarray)
        error         = pyqtSignal(str)
        warning       = pyqtSignal(str)
        pixel_format, exposure_us, gain_db, frame_rate = "Mono12", 8000.0, 8.0, 20.0
        trigger_mode, trigger_delay, frames_emitted = "Off", 0.0, 0

        def __getattr__(self, name):            # set_exposure(), start_capture(), …
            return lambda *a, **k: None

        def get_info(self):
            return {}

    h, w = frames[0].shape
    win = MainWindow(camera=_Camera())
    win.show()
    app.processEvents()
    # An ROI circle as the operator would draw it: the largest that fits, less
    # the crop margin — the stats and the collector see a realistic mask.
    circ = {"cx": w / 2, "cy": h / 2, "r": max(10, min(h, w) // 2 - 30)}
    win._mask = circle_mask((h, w), circ)
    win.processor.set_roi(win._mask)
    print(f"{h} x {w} frames, ROI circle r={circ['r']} "
          f"({int(win._mask.sum())} pixels), {n} frames per round, {rounds} rounds")

    def one_round() -> tuple[float, float, float]:
        win._dark_cal_collector = DarkCalCollector(n * 2 + 10, 7)
        win._set_state(State.DARK_CAL)
        win._on_scos_frame(frames[0], 0.0)      # first frame allocates; not timed
        # 1. per frame, statistics held off
        win._last_stats_time = float("inf")
        t0 = time.perf_counter()
        for i in range(1, n):
            win._on_scos_frame(frames[i % len(frames)], float(i))
        core_ms = (time.perf_counter() - t0) / (n - 1) * 1000
        # 2. the statistics alone: one frame with them, minus one without
        k = 10
        t0 = time.perf_counter()
        for i in range(k):
            win._last_stats_time = 0.0
            win._on_scos_frame(frames[i % len(frames)], float(i))
        stats_ms = (time.perf_counter() - t0) / k * 1000 - core_ms
        # 3. one display update, painted
        t0 = time.perf_counter()
        for i in range(k):
            win._on_display_frame(frames[i % len(frames)])
            win.repaint()
            app.processEvents()
        disp_ms = (time.perf_counter() - t0) / k * 1000
        win._set_state(State.PREVIEW)
        win._dark_cal_collector = None
        return core_ms, stats_ms, disp_ms

    rows = []
    for r in range(rounds):
        core_ms, stats_ms, disp_ms = one_round()
        rows.append((core_ms, stats_ms, disp_ms))
        print(f"round {r + 1}: per frame {core_ms:6.2f} ms   stats {stats_ms:6.2f} ms "
              f"(2/s)   display {disp_ms:6.2f} ms (up to 30/s)")
    core_ms, stats_ms, disp_ms = (float(np.median(c)) for c in zip(*rows))
    # Per second: 2 stats + up to 30 displays + fps frames must fit in 1000 ms.
    spare = 1000 - 2 * stats_ms - 30 * disp_ms
    fps = spare / core_ms if spare > 0 else 0.0
    print(f"\nmedian: per frame {core_ms:.2f} ms, stats {stats_ms:.2f} ms, "
          f"display {disp_ms:.2f} ms")
    print(f"GUI thread can take about {fps:.0f} dark frames/s "
          f"(after {2 * stats_ms + 30 * disp_ms:.0f} ms/s of stats + display)")
    win._set_state(State.PREVIEW)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
