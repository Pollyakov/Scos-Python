# SCOS Real-Time Demo Plan (Simplified)

## Status

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1 — TIFF mock camera | **COMPLETE** | `python main.py --mock-tiff scratch/mock.tif` |
| Math bug fixes (processor.py) | **COMPLETE** | Commits 9627398, dc3e7c1 |
| Math validation — raw κ² | **COMPLETE** | 0.40% error vs MATLAB ✓ |
| Math validation — corrected κ² | **TODO** | Need full 600 dark frames; currently have 322 |
| Phase 2 — real camera | **TODO** | `python main.py` (no flag) |

**Next session starting point:**
1. Find the full dark calibration dataset (600 frames) for
   `expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark` and recheck corrected κ²
   with `bit_depth=10, sat_capacity=10400` for the `a2A1920-160umPRO`.
2. Then move to Phase 2: connect real lab camera + laser, run
   `python check_camera.py`, then `python main.py`.

---

## Context

We have **1 day to prepare + half a day of show**, and limited access to the
real Basler camera + laser. The big real-time plan
(`docs/Plan_RealTime.pdf` / `docs/Full_Plan_RealTime.pdf` /
`docs/Plan_RealTime_Patches.md`) targets a multi-week refactor with 3 worker
threads, 2 bounded queues, an HDF5 recorder, an overload dialog, calibration
phases, a state machine, etc. That is **out of scope** for the demo.

This plan supersedes the big plan **for demo purposes only**. Once the demo
is over, the big plan is still the long-term direction.

**Cuts vs. the big plan:**
- No long-record handling, no 4-hour cap, no disk-space check.
- No data storage / HDF5 / recorder thread / record_queue.
- No overload dialog, no queue backpressure logic.
- No DARK_CAL / BRIGHT_CAL state machine — calibration is skipped.
- No `core/` package refactor yet — single new file at repo root.

**Kept:** the existing GUI, the existing `SCOSProcessor` math, the existing
`CameraThread` for Phase 2.

**Goals:**
- Phase 1 — verify the SCOS math runs end-to-end, **measure real per-frame
  processing time**, and be able to develop / demo without lab equipment.
- Phase 2 — plug the real camera + laser back in and hope it works.

---

## Phase 1: TIFF mock camera (target: 1 day)

### Files to add

#### 1. `mock_camera.py` (new, repo root, ~120 lines)

Sibling of `camera.py`. Mirrors `CameraThread`'s public surface so
`MainWindow` needs **zero** rewiring:

```python
class MockCameraThread(QThread):
    frame_ready   = pyqtSignal(np.ndarray)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    DISPLAY_FPS_CAP = 30.0

    def __init__(self, tiff_path: str, loop: bool = True, parent=None): ...
    def open(self): ...                # tifffile.imread → self._stack; idempotent
    def close(self): self.stop(); self._stack = None
    def start_capture(self):           # if stack not loaded → open(); _running=True; self.start()
    def stop(self): self._running=False; self.wait()
    def set_exposure(self, us: float): self.exposure_us = us       # store-only
    def set_gain(self, db: float): self.gain_db = db               # store-only
    def set_frame_rate(self, hz: float): self.frame_rate = hz      # FUNCTIONAL — affects pacing
    def set_pixel_format(self, fmt: str): self.pixel_format = fmt  # store-only
    def set_trigger(self, enabled: bool, delay_us: float = 0.0): ...   # store-only
    def set_roi(self, x, y, w, h): ...                                  # store-only
    def get_info(self) -> dict: ...    # model="MockTIFF", real W/H from stack
    def run(self): ...                 # the playback loop
```

**Init defaults must match `CameraThread`** so `MainWindow._toggle_video()`
direct-attribute writes (lines 301–306) keep working: `exposure_us`,
`gain_db`, `frame_rate=50.0`, `pixel_format="Mono12"`, `trigger_mode="Off"`,
`roi_position=None`, `_last_display=0.0`, `_display_interval=1/30`.

**`open()` idempotency** — mirror `camera.py:55–57`:
```python
def open(self):
    if self._stack is None:
        self._stack = tifffile.imread(self.tiff_path)
        if self._stack.ndim == 2:
            self._stack = self._stack[None, ...]
```

**`run()` loop** (the only real logic):
```python
self._idx = 0
while self._running:
    target_dt = 1.0 / max(self.frame_rate, 1e-3)   # re-read every iter
    t0 = time.perf_counter()
    frame = self._stack[self._idx]
    self.frame_ready.emit(frame)
    now = time.monotonic()
    if now - self._last_display >= self._display_interval:
        self.display_ready.emit(frame)
        self._last_display = now
    self._idx += 1
    if self._idx >= len(self._stack):
        if self._loop: self._idx = 0
        else: break
    sleep_s = target_dt - (time.perf_counter() - t0)
    if sleep_s > 0:
        self.msleep(int(sleep_s * 1000))
```

`loop=True` by default (so the demo doesn't run out of frames mid-show).
The display throttle uses `time.monotonic()` to match `camera.py:180–183`.

#### 2. `tools/synth_tiff.py` (new, ~40 lines)

Synthetic 16-bit multipage TIFF generator using a Rayleigh distribution
(speckle-ish, easy to reason about). CLI:

```
python tools/synth_tiff.py --out scratch/mock.tif \
    --frames 1200 --width 700 --height 700 --mean 30000 --seed 0
```

Core:
```python
def generate(width, height, frames, mean=30000, seed=0):
    rng = np.random.default_rng(seed)
    sigma = mean / np.sqrt(np.pi / 2)        # so E[X] ≈ mean
    stack = rng.rayleigh(scale=sigma, size=(frames, height, width))
    return np.clip(stack, 0, 65535).astype(np.uint16)

tifffile.imwrite(args.out, stack, photometric="minisblack")
```

The stack does not need to be physically realistic — this is for pipeline
timing and visual sanity, not for measuring biology.

#### 3. `tests/test_mock_camera.py` (new, ~60 lines)

Three tests, parallel to `tests/test_camera.py`:

- `test_emits_all_frames_when_loop_false`: write a 5-page TIFF to
  `tmp_path`, run `MockCameraThread(path, loop=False)`, collect emissions
  via a list, assert `len == 5` with correct shape/dtype.
- `test_setters_store_values`: `set_exposure / set_gain / set_frame_rate /
  set_pixel_format / set_trigger / set_roi` don't crash and store values.
- `test_get_info_after_open`: `open()` then `get_info()` returns sensible
  width/height matching the TIFF.

Reuse the `QApplication.instance() or QApplication([])` pattern from
`tests/test_camera.py:13–31`. Existing tests stay untouched.

### Files to edit

#### 4. `main.py` (~10 added lines)

Add an argparse flag and pick the camera implementation:

```python
import argparse
from camera import CameraThread
from mock_camera import MockCameraThread

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-tiff", type=str, default=None,
                        help="Replay a TIFF stack instead of opening Basler.")
    args, qt_argv = parser.parse_known_args()

    app = QApplication(qt_argv)            # was sys.argv
    # ... unchanged palette setup ...
    camera = MockCameraThread(args.mock_tiff) if args.mock_tiff else CameraThread()
    window = MainWindow(camera=camera)
    window.show()
    sys.exit(app.exec())
```

#### 5. `gui/main_window.py` (~3 changed lines)

- Constructor: accept an optional camera, default to today's behavior.
  ```python
  def __init__(self, camera=None):
      super().__init__()
      ...
      self.camera = camera if camera is not None else CameraThread()
  ```
  Replaces the inline `self.camera = CameraThread()` at line 67.
  All signal wiring (`:260–264`) and setter wiring (`:273–282`) keeps
  working as-is — that's the whole reason the mock mirrors the real
  camera's API exactly.

- **Throttle the proc-time label to ~1 Hz.** The `_proc_times` list and
  `_proc_label` already exist (lines 107–108, 442–452); they update on
  every frame, which is fine for the rolling average but spams `setText`
  at 50 Hz. Wrap the two `setText` calls (`:451–452`) with the same
  pattern used for `_last_stats_time` higher up in the function:
  ```python
  if (now - self._last_proc_label_time) >= 1.0:
      self._proc_label.setText(...)
      self.lbl_proc.setText(...)
      self._last_proc_label_time = now
  ```
  Add `self._last_proc_label_time = 0.0` to `__init__`. The `_proc_times`
  rolling buffer keeps appending every frame regardless. **Optionally**
  bump the buffer cap from 30 → 100 frames at line 448 for a smoother
  average — easy one-line change.

### Why each setter is functional or no-op (for the mock)

| Setter | Behavior | Why |
|---|---|---|
| `set_frame_rate(hz)` | **Functional** — changes pacing | Re-read in the `run()` loop each iteration. |
| `set_exposure(us)` | Store only | TIFF brightness is fixed; can't change post-hoc. |
| `set_gain(db)` | Store only | Same reason. |
| `set_pixel_format(fmt)` | Store only | TIFF dtype is fixed. |
| `set_trigger(...)` | Store only | No real trigger source. |
| `set_roi(x,y,w,h)` | Store only | Mock always emits the full TIFF frame; ROI masking happens in the GUI side via `_on_roi_changed` → `_mask`, which already works. |

This matches the user's "use current GUI, possibly small changes" note —
the controls stay on screen and live-update; they just don't change
playback brightness. (Document this in the demo intro if asked.)

### Pitfalls to watch for

- **Memory.** 700 × 700 × uint16 × 1200 frames ≈ 1.18 GB resident. Fine on
  the demo laptop. A 1024 × 1024 × 5000 stack would be ~10 GB — caller's
  responsibility, but documented in the synth script's `--help`.
- **Thread safety of `set_frame_rate`.** Plain attribute write under the
  GIL is atomic at bytecode level; `run()` re-reads once per iteration.
  Worst case is one frame paced with the old rate. **Do not introduce a
  Lock** — the real `CameraThread` accepts the same trade-off.
- **`pyqtSignal` from a `QThread.run()` with `msleep`.** Standard PyQt6
  pattern — `msleep` is a static blocking sleep on the worker thread that
  doesn't run an event loop, and signals are delivered to the GUI thread
  via queued connection (default cross-thread). This is the same pattern
  `camera.py` already uses, so we know it works here.
- **`time.perf_counter` vs `QElapsedTimer`.** Both wrap
  `QueryPerformanceCounter` on Windows. No measurable drift at 50 Hz.
  Stick with `perf_counter` for pacing math and `monotonic` for the
  display throttle (matches `camera.py`).
- **Bit depth.** Real Basler in Mono12 returns values 0–4095 in a uint16
  container. The synth defaults to the full 0–65535 range. The processor
  uses the format-combo `bit_depth` for the quantization-noise term, but
  κ² is a normalized contrast and the demo plot will still look right.
  If the absolute κ² value matters, drop `--mean` to ~2000 in the synth
  script and set the format combo to Mono12.

### Calibration in mock mode

**Skipped** (per user choice). `SCOSProcessor.dark_mean` and
`SCOSProcessor.dark_var` default to zero, and `process()` returns a valid
`kappa2_corr` without calibration — just without the dark-noise subtraction
term. Shot-noise and quantization corrections still apply. Acceptable for
demo.

### Phase 1 verification

Run order on the dev machine (no camera needed):
1. `python tools/synth_tiff.py --out scratch/mock.tif --frames 1200`
2. `python -m pytest tests/` — all existing tests + the 3 new ones pass.
3. `python main.py --mock-tiff scratch/mock.tif`
   - GUI opens, "Start video" streams frames, "Start SCOS" appends to plot.
   - Status bar shows `FPS: ~50` and `Proc: NN ms`.
   - `Proc` average should be **well under** `1000 / fps` ms per frame
     (target: < 20 ms at 50 Hz). If not, that's a real finding to feed back
     into the big plan's threading decision.
4. Sanity-check live setter wiring: change FPS spinbox → playback rate
   visibly changes; change exposure/gain → no effect (expected).
5. Smoke-check `closeEvent`: close the window mid-stream → no hang, no
   exception.

---

## Phase 2: Plug in the real camera (target: ½ day, on lab PC)

Drop the `--mock-tiff` flag — `main.py` falls back to `CameraThread()` and
nothing else changes. The mock and real paths are entirely independent code
branches, so a Phase-2 regression cannot have been caused by Phase-1 work.

**Verify on real hardware:**
- `python check_camera.py` first, as a Pylon SDK smoke test.
- `python main.py` — camera enumerates and opens; "Start video" → frames
  arrive; FPS label > 0; `Proc` label stays under the inter-frame budget.
- "Start SCOS" with the laser on → plot moves; ROI auto-detect or manual
  drag still works.

**Most likely failure modes** (and where they surface):
- Pixel-format mismatch → `camera.py:135` calls `warning.emit`, surfaced
  via `_on_camera_warning` in `main_window.py`.
- Trigger-mode hang → already handled by `_on_camera_error` in
  `main_window.py:480–502`.
- Pylon SDK install / firmware regression → `check_camera.py` will catch
  it before the GUI does.

---

## Critical files (paths)

- New: `mock_camera.py`
- New: `tools/synth_tiff.py`
- New: `tests/test_mock_camera.py`
- Edit: `main.py` (argparse, camera choice)
- Edit: `gui/main_window.py` (~3 lines: constructor signature + proc-label
  throttle)

## Reused, not rewritten

- `camera.py:55–60, 62–64, 168–189` — public-API shape and run-loop
  pattern that `MockCameraThread` mirrors.
- `processor.py` `SCOSProcessor.process(frame, mask)` — unchanged; works
  fine with zero `dark_mean` / `dark_var`.
- `gui/main_window.py:107–108, 442–452` — the proc-time tracking
  (`_proc_label`, `_proc_times`) is already 90% built; we just throttle
  the label setText to 1 Hz.
- `gui/image_widget.py` and `gui/plot_widget.py` — dumb sinks, no changes.
- `tests/test_camera.py:13–31` — pypylon mock pattern, copied for the new
  test module.

## Out of scope (intentionally)

- The full big-plan refactor (`core/` package, 3-thread architecture,
  state machine, queues, recorder, overload dialog, HDF5).
- Dark / bright calibration phases.
- Long-recording length cap.
- Hardware-trigger Arduino integration in mock mode.
- Disk-space monitoring.

These all stay in the long-term plan documents — this file does not
modify or supersede them outside of the demo window.
