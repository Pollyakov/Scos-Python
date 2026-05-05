# SCOS Real-Time — Implementation Plan

Consolidated, actionable plan that merges:
- [`SCOS_protocol.md`](SCOS_protocol.md) — what the measurement protocol must do
- [`Plan_RealTime.pdf`](Plan_RealTime.pdf) — compact architecture reference
- [`Full_Plan_RealTime.pdf`](Full_Plan_RealTime.pdf) — long-form architecture
- [`Plan_RealTime_Patches.md`](Plan_RealTime_Patches.md) — bug fixes + gaps in the two PDFs

The patches in `Plan_RealTime_Patches.md` corrected real bugs in the original PDFs
(silent frame drop, contradictory plot interval, missing trigger handling, missing
recorder thread, etc.) — those fixes are folded in directly here, not restated.

---

## 1. What we are building

A real-time PyQt6 GUI that:
1. Streams frames from a Basler GigE camera (~20 Hz, 700×700 px, Mono12).
2. Walks the user through a measurement session: **dark calibration → bright
   calibration → measurement** (per [`SCOS_protocol.md`](SCOS_protocol.md)).
3. Computes κ² with full noise correction (`var_dark`, `var_bright`, shot noise
   `G·⟨I⟩`, quantization `1/12`) on every frame.
4. Plots `rBFi = BFi / mean_BFI_firstSeconds`, updated once per second; refreshes
   the live image once every 2.5 s during measurement.
5. Saves to disk progressively (HDF5) so a crash at hour 3 doesn't lose hours 0–3.

**Plain-English why:** the lab shines a laser into tissue and reads the speckle
pattern with the camera. Frame-to-frame brightness fluctuations tell us how fast
blood is moving. The protocol mandates two short calibration runs first so we can
subtract camera noise from the signal. Multi-hour recordings + FFT-based pulse
detection mean **never silently dropping frames** — uneven sampling fakes a wrong
heart rate.

---

## 2. Architecture — 3 worker threads + GUI

```
Camera ──► frame_queue ──► Processor ─┬─► result_queue ──► GUI (QTimer 1000 ms)
                                      └─► record_queue ──► Recorder ──► HDF5
```

| Thread | Job | Output channels |
|---|---|---|
| **Camera** (QThread) | `pylon.RetrieveResult()` in a loop. **Never silently drops.** | `frame_queue.put(frame)` (blocks if full); `display_frame` signal throttled by state |
| **Processor** | `frame_queue.get()` → `compute_kappa2()` → push `ProcessingResult` | `result_queue` (bounded 2000) + `record_queue` (bounded 200) |
| **Recorder** | Drains `record_queue`, batches, appends to one HDF5 file every M minutes | flush on `stop()` / `closeEvent` |
| **GUI (main)** | `QTimer.timeout` every **1000 ms** drains `result_queue`, redraws plot. Cross-thread comms only via `pyqtSignal` | — |

### Pylon settings (settled, do not change)
- `GrabStrategy_OneByOne` (NOT `LatestImageOnly` — that drops oldest silently)
- `MaxNumBuffer = 20`
- Detect drops via `result.GetNumberOfSkippedImages()`

### Overload behavior — never drop silently
- `frame_queue` is bounded (size 20). When fill ≥ 80 % (16 frames, ~800 ms slack
  at 20 Hz), processor emits `overload_detected`.
- Total slack from start of slowdown to first dropped frame is
  `MaxNumBuffer + frame_queue.maxsize = 40` frames ≈ 2 s at 20 Hz. The 80 %
  high-water mark fires after the *Python* queue alone hits 16 frames, leaving
  ~1.2 s of additional Pylon buffer to absorb the user's reaction time.
- GUI pauses capture and shows a non-blocking dialog with **4 options**, ordered
  least-disruptive first:
  1. Reduce ROI radius
  2. Reduce target FPS
  3. Switch to **save-only** (write raw frames, skip κ² — process later)
  4. Switch to **process-only** (skip disk, keep computing)
- 5–10 s timeout → default to **save-only**.
- Always-visible GUI indicators: queue-fill bar (0–100 %) and dropped-frame counter.

### Capture path is dual-channel
The Camera thread emits two signals so SCOS never gets throttled by display:
- `frame_ready` — every frame, into `frame_queue` (the SCOS path)
- `display_ready` — throttled in the **processor** by state:
  - `IDLE` / `PREVIEW` → 30 FPS
  - `DARK_CAL` / `BRIGHT_CAL` → 5 FPS
  - `MEASURING_INIT` / `MEASURING` → every 2.5 s (per protocol)

### A note on the GIL (for understanding why this works)
Python normally runs only one thread at a time because of the Global Interpreter
Lock. But `scipy.ndimage.uniform_filter` (the dominant cost per frame) releases
the GIL while the C code runs. So the Processor thread really does run in
parallel with the GUI thread. If we ever rewrote the math in pure Python, we'd
have to switch to multiprocessing.

---

## 3. State machine

```
IDLE → PREVIEW → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED
                                                                       ↘ ERROR
```

| State | What happens |
|---|---|
| `IDLE` | App started, camera not connected |
| `PREVIEW` | Camera streaming at 30 FPS for ROI/focus/exposure tuning. **No κ², no calibration consumed.** |
| `DARK_CAL` | Pop-up: "Turn off laser, click OK". External trigger OFF. Capture N1 (default 600) frames into a subfolder. Compute `mean_dark` per pixel and `var_dark` per pixel; spatial-filter `var_dark` with the SCOS window. |
| `BRIGHT_CAL` | Pop-up: "Turn on laser, remove subject, click OK". Capture N2 (default 600) frames. Compute `var_bright` per pixel = temporal variance, then spatial-filter with the same window. |
| `MEASURING_INIT` | Shrink ROI by `⌈window/2⌉ + 1` (so the filter window stays inside the ROI). Run the κ² loop for `norm_seconds` (default 5 s) but don't plot yet — accumulate to compute `mean_BFI_firstSeconds`. |
| `MEASURING` | Keep running κ², now plotting `rBFi = BFi / mean_BFI_firstSeconds`. Update plot every 1 s, image every 2.5 s. End on Stop button or recording-length cap. |
| `FINISHED` | Flush recorder, save final `.mat` (rBFi + ⟨I⟩ + metadata) and the matplotlib figure. |
| `ERROR` | Camera disconnect / queue overflow. Save accumulated data; offer reconnect. |

`SCOSSession.start_measurement()` transitions `PREVIEW → DARK_CAL`, not
`IDLE → DARK_CAL`.

**Recording cap:** quote the wording verbatim from
[`SCOS_protocol.md`](SCOS_protocol.md) (max 4 h per the tightened spec) into
`SessionConfig.recording_minutes` default and document. If the protocol cap
changes, update both this doc and the default in `core/session.py`.

---

## 4. Target folder structure

```
scos-python/
├── core/                       # pure Python, no Qt, no GUI
│   ├── scos_math.py            # κ², noise correction, gain conversion
│   ├── frame_source.py         # ABC: FrameSource
│   ├── camera_source.py        # CameraFrameSource (pypylon) — supports trigger_mode
│   ├── folder_source.py        # FolderFrameSource (replays .npz/.tiff for tests)
│   ├── session.py              # SCOSSession + State enum + SessionConfig
│   ├── pipeline.py             # RealtimePipeline (threads + queues)
│   └── recorder.py             # progressive HDF5 save
├── gui/
│   ├── main_window.py
│   ├── image_widget.py
│   ├── plot_widget.py
│   ├── controls_panel.py
│   └── status_bar.py
├── arduino_uploader.py         # kept as-is
├── tests/
│   ├── data/
│   │   ├── offline_reference.npz
│   │   ├── sample_frames.npz   # first 20 frames of reference recording
│   │   └── golden_frames.npz
│   ├── test_scos_math.py
│   ├── test_frame_source.py
│   ├── test_session.py
│   └── test_pipeline.py
├── docs/
│   ├── SCOS_protocol.md
│   ├── Implementation_Plan.md  # this file
│   └── realtime_architecture.md  # written in Phase 1
├── pyproject.toml
├── requirements.lock
└── main.py
```

The existing `processor.py` and `camera.py` are not deleted upfront — they keep
working until the new pipeline replaces them, then they get removed in Phase 3
layer 5.

---

## 5. Key data structures

```python
# core/session.py
class State(Enum):
    IDLE = auto(); PREVIEW = auto(); DARK_CAL = auto(); BRIGHT_CAL = auto()
    MEASURING_INIT = auto(); MEASURING = auto(); FINISHED = auto(); ERROR = auto()

@dataclass
class SessionConfig:
    window_size: int = 7
    n_dark_frames: int = 600       # N1
    n_bright_frames: int = 600     # N2
    recording_minutes: float = 5.0 # max 4 h per protocol
    norm_seconds: float = 5.0      # window for mean_BFI_firstSeconds
    save_frames: bool = False
    output_folder: str = ""
    G_due: float = 0.0             # from camera SN + pixel format table
    plot_update_s: float = 1.0     # Advanced settings; default in config.json
    image_update_s: float = 2.5    # protocol value
```

**Result storage:** keep results as **NumPy arrays per field** (not a list of
dataclass instances). One array each for `times`, `bfi`, `rbfi`,
`mean_intensity`, `frame_number`. For 4 h × 20 Hz × 5 fields × 8 B ≈ ~12 MB.
Pre-allocate; if recording is unbounded, extend by 10 000 each time and trim at
the end.

---

## 6. Core math (`core/scos_math.py`) — pure functions

```python
# κ²_fixed = (var_raw − var_dark − var_bright − G·⟨I⟩ − 1/12) / ⟨I⟩²

convert_gain_db_to_due(gain_db, bit_depth, sat_capacity_e) -> float
local_mean_and_variance(im, window) -> (mean_im, var_im)   # uniform_filter; clamp var ≥ 0
shrink_mask_for_window(mask, window) -> np.ndarray         # binary_erosion by ⌈window/2⌉+1
compute_kappa2(frame, mask, window, mean_dark,
               var_dark_filtered, var_bright_filtered, G_due)
    -> (kappa2_raw, kappa2_fixed, mean_intensity)
```

**`var_bright` formula (spelled out, not `# ...`):**

```python
def _finish_bright_cal(self):
    stack = np.stack(self.state.bright_buffer, axis=2)        # (H, W, N2)
    var_bright_per_pixel = stack.var(axis=2)                  # temporal variance
    self.state.var_bright_filtered = uniform_filter(
        var_bright_per_pixel, size=self.config.window_size)   # spatial smoothing
    self.state.bright_buffer.clear()
    self.state.state = State.MEASURING_INIT
```

**Validation gate:** `test_offline_reference_matches` must reproduce the old
offline output to `rel=1e-6` on the 20 sample frames (using zero calibration
arrays since the reference recording has no calibration).

---

## 7. Phase plan — execute in this order

| Phase | Days | Goal |
|---|---|---|
| **0 — Reference baseline** | 1 | Record 1–5 min on the rig with old code. Save full κ²/BFi output → `tests/data/offline_reference.npz`. Save first 20 raw frames → `sample_frames.npz`. |
| **0.5 — Benchmark on the lab PC** | ½ | Run `bench_processor.py --width 700 --height 700 --window 7 --duration 30 --bits 12` on the **target machine**. Record mean / p95 / p99 per-frame time and CPU%. **If p99 ≥ 35 ms (70 % of 50 ms budget), Phase 1 must include an optimization plan** (smaller ROI, numba, separable filter) before any code is written. |
| **1 — Architecture on paper** | 1 | Write `docs/realtime_architecture.md`. Resolve open questions (§9 below). |
| **2 — Tooling lock-in** | ½ | `pyproject.toml`, `pip-compile` → `requirements.lock`, `ruff`, `mypy --strict` on `core/`, `pre-commit`. |
| **3 — Code, bottom-up** | 2–4 wk | Layer 1 → 5, each fully tested before next (see §8). |
| **4 — Polish** | ongoing | GitHub Actions CI, `pytest-qt`, `pytest-benchmark`, replace `print()` with `logging`, README update. |

---

## 8. Layer-by-layer order inside Phase 3

Each layer must have its tests passing before the next one starts.

### Layer 1 — `core/scos_math.py`
- Port κ² formulas from `processor.py` into pure functions.
- Add `convert_gain_db_to_due` with the table-lookup hook (a stub now; real table later).
- Add `shrink_mask_for_window`.
- Tests: `test_offline_reference_matches`, plus property tests (var ≥ 0,
  shrink-mask is a strict subset of input).

### Layer 2 — `core/frame_source.py`, `camera_source.py`, `folder_source.py`
- Define `FrameSource` ABC (`open()`, `get_frame(timeout_s)`, `close()`).
- `CameraFrameSource(exposure_us, gain_db, frame_rate_hz, pixel_format, trigger_mode='off'|'line2')`.
  - Document: with `trigger_mode='line2'`, `frame_rate_hz` is a **target**;
    the actual rate is whatever Arduino delivers on Line2. Re-use the existing
    "frames stopped arriving" warning from commit `06d4629`.
- `FolderFrameSource` replays a stack of `.npz`/`.tiff` frames at a configurable
  rate — this is what tests use to avoid the camera.
- Tests: `test_frame_source.py` exercises both backends with a known frame stack.

### Layer 3 — `core/session.py`
- `SCOSSession` owns state, config, calibration arrays (`mean_dark`,
  `var_dark_filtered`, `var_bright_filtered`), and the result arrays.
- `process_frame(frame)` is the single entry point: it reads `self.state` and
  dispatches to the right handler (collect calibration / accumulate norm /
  compute κ² / etc.).
- Implement `_finish_dark_cal` and `_finish_bright_cal` per §6.
- Tests: drive a `FolderFrameSource` through the full state machine offline;
  assert final rBFi matches the reference within tolerance.

### Layer 4 — `core/pipeline.py` and `core/recorder.py`
- `RealtimePipeline` owns the three worker threads and the two bounded queues:
  - `result_queue = queue.Queue(maxsize=2000)`  (~100 s of results at 20 Hz)
  - `record_queue = queue.Queue(maxsize=200)`   (~10 s of frames at 20 Hz)
- Capture loop: **block on `frame_queue.put`** (never silent drop). Above 80 %
  fill → emit `overload_detected`.
- Bounded queue overflow → emit `error` and transition to `ERROR` (real bug,
  not transient).
- `Recorder` opens an HDF5 file with resizable datasets; appends every M
  minutes; flushes in `stop()` and on error.
- Tests: `test_pipeline.py` runs the pipeline for 30 s with a
  `FolderFrameSource`, asserts no drops, recorder file plays back identical frames.

### Layer 5 — GUI
- `gui/main_window.py` wires the pipeline up; receives signals; never touches
  the math.
- `controls_panel.py` exposes window_size / N1 / N2 / recording_minutes /
  norm_seconds / save_frames / plot_update_s as inputs (with sane defaults).
- `image_widget.py` and `plot_widget.py` already exist — port them to consume
  `display_frame` and the buffered result drain (`QTimer @ 1000 ms`).
- `status_bar.py` shows: state, queue-fill bar (0–100 %), dropped-frame count,
  elapsed/remaining time, free disk space.
- Pop-ups: "Turn off laser" before `DARK_CAL`, "Turn on laser, remove subject"
  before `BRIGHT_CAL`, overload dialog (4 options).
- Stop during calibration → confirmation dialog → `FINISHED`, save partial data.

---

## 9. Open questions to resolve in Phase 1 (need supervisor input)

1. **rBFI normalization on multi-hour recordings.** Laser drift / detector
   heating / subject motion may invalidate a fixed `mean_BFI_firstSeconds`.
   Options: rolling re-normalization vs. save raw BFi and normalize offline.
2. **Disk-space limit policy.** When does the app stop / refuse to start a
   recording?
3. **Refresh-interval storage.** `plot_update_s` lives in `config.json` (so
   changing it doesn't need a code edit) and is also exposed in the GUI's
   "Advanced settings" panel for one-session override. Confirm with supervisor.
4. **Save-frames policy.** If `save_frames=True`, do we write every frame
   (~280 GB at 4 h), every K-th frame, or only the calibration frames? Default
   off for now.

---

## 10. Coding rules (non-negotiable)

1. **GUI is the LAST layer**, not the first.
2. **Never silently drop frames.**
3. Heavy work never on the main thread.
4. Threads communicate via queues + `pyqtSignal` only — no shared mutable state.
5. Tests for layer N before layer N+1.
6. `np.float64` for all SCOS intermediates; precision matters (small numbers in
   `var_raw − var_dark − var_bright`).
7. `logging` everywhere, never `print()`.
8. Every commit must build and pass fast tests (pre-commit hook is already wired).

---

## 11. Things explicitly NOT to do

- Don't add features in the old `processor.py` — write new code in `core/`.
- Don't put math in GUI files.
- Don't access GUI widgets from camera/processor threads (only via `pyqtSignal`).
- Don't break `test_offline_reference_matches` once it's set up.
- Don't lower the 1000 ms plot QTimer — it's by design for hours-long recordings;
  pyqtgraph downsampling makes individual points invisible at that scale anyway.
- Don't use `LatestImageOnly` — silent drops break the FFT and the user gets a
  wrong heart rate with no error.
