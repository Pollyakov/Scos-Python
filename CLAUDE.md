# CLAUDE.md

## Scientific Priority — Non-Negotiable

**`docs/SCOS_protocol.md` is the absolute source of truth for this project.**
The purpose of SCOS is to measure cerebral blood flow as accurately as possible.
No code change — refactoring, performance optimization, UI improvement, or architectural cleanup — may reduce measurement accuracy or deviate from the protocol.
When in doubt between correctness and convenience, always choose correctness.

## Purpose

SCOS (Speckle Contrast Optical Spectroscopy) — real-time GUI app that acquires frames from a Basler camera, computes speckle contrast (κ²) with noise correction, and plots blood flow (1/κ²) over time. Translated from MATLAB (SCOSvsTime_WithNoiseSubtraction_Ver2.m). Used in the Optical Neuroimaging Lab at Bar-Ilan University.

SCOS measures cerebral blood flow velocity by illuminating tissue with a laser and capturing speckle patterns with a Basler camera. Frame-to-frame intensity fluctuations reveal how fast blood cells are moving.

## Current Phase: Demo preparation

We simplified the long-term real-time plan into a 2-phase demo plan.
See [`docs/Plan_RealTime_Demo.md`](docs/Plan_RealTime_Demo.md) for the
full demo plan and [`docs/Plan_RealTime_Demo_Short.md`](docs/Plan_RealTime_Demo_Short.md)
for the ideas-only summary. The long-term plan (`docs/Plan_RealTime.pdf`,
`docs/Full_Plan_RealTime.pdf`, `docs/Plan_RealTime_Patches.md`) is still
the post-demo direction.

**Phase 1 — COMPLETE.** Both mock cameras implemented and tested.
- Synthetic TIFF (no real data needed):
  ```
  python tools/synth_tiff.py --out scratch/mock.tif --frames 1200
  python main.py --mock-tiff scratch/mock.tif
  ```
- Real lab recording folder (auto-loads calibration + mask):
  ```
  python main.py --mock-folder "path/to/expT5ms_Gain24dB_BL100DU_FR40Hz_005"
  ```

**Phase 2 — TODO.** Connect the real Basler camera + laser:
  ```
  python main.py          # no flag → uses real CameraThread
  python check_camera.py  # smoke-test first
  ```

**Math bugs fixed:**
1. Missing `bright_var` (spVar) term in corrected formula — added `calibrate_bright()`
2. Biased variance estimator → fixed to unbiased (×N²/(N²−1))
3. Dark variance not spatially smoothed → now applies `uniform_filter`
4. Wrong `sat_capacity` (was 10400, correct value for a2A1920-160umPRO is **11117 e-**)
   — diagnosed via Phase-0 PTC analysis. Superseded on 2026-09-22: G now comes from the
   measured table, so no saturation capacity enters a measurement at all.

**Math validation result (against MATLAB reference, 600 real frames):**
- Raw κ²: **0.45% error** ✓
- Corrected κ²: **1.2% error** ✓ (G from the measured table for SN 40513592, spVar from smoothingCoefficients.mat,
  dark calibration from 600 dark frames)

**Architecture target:** 3 threads + 2 queues:
- Thread 1: Camera capture (pypylon RetrieveResult)
- Thread 2: Processor (κ² with noise correction)
- Main thread: GUI (QTimer reads result_queue every 1000 ms)

**Code organization target:**
- `core/` — pure logic (math, frame source, session state machine, pipeline)
- `gui/` — PyQt6 widgets only, no math
- Existing `camera.py` and `processor.py` will be refactored into
  `core/camera_source.py` and `core/scos_math.py` respectively.

**Key parameters for THIS lab:**
- Frame size: 700 × 700 pixels
- Frame rate: ~20 Hz (target)
- Recording duration: up to several hours
- Camera: Basler GigE via pypylon

**State machine for session:**
IDLE → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED

## What NOT to do
- Don't add features in the old `processor.py` — write new code in `core/`
- Don't put math in GUI files
- Don't access GUI widgets from camera/processor threads (only via pyqtSignal)
- Don't break the offline reference test once it's set up

## Setup & Run

```bash
# Windows setup
setup.bat
# Or manually:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run app
python main.py

# Verify camera
python check_camera.py

# Benchmark (no camera needed)
python bench_processor.py
python bench_processor.py --width 2448 --height 2048 --window 7 --duration 30 --bits 12 --fps 50
```

## Testing

```bash
python -m pytest tests/
```

A pre-commit hook runs all tests before each commit; failures block the commit.

IMPORTANT: a test module that builds a `MainWindow` must create the `QApplication` **before** `gui.main_window` is imported, at module level:
```python
_app = QApplication.instance() or QApplication([])
from gui.main_window import MainWindow
```
Importing that module pulls in pyqtgraph, and constructing the application afterwards kills the interpreter outright — no traceback, no pytest output, exit code 127, which looks like a broken command rather than a crash. See `tests/test_gain_table.py` and `tests/test_invalid_k2_guard.py`.

Modal dialogs are blocked suite-wide by `tests/conftest.py`: any `QMessageBox` or `QFileDialog` a test reaches raises instead of opening. A test that legitimately drives one must monkeypatch that specific call (the `dialogs` fixture is the pattern).

## Architecture

```
Thread 1 (CameraThread/QThread):  pypylon grabs → stamps t_capture (time.monotonic())
                                  → emits frame_ready(frame, t_capture)   (every frame)
                                  → emits display_ready(frame)             (≤30 FPS)

  frame_ready ─direct─→ RealtimePipeline.on_frame()   [runs on the CAMERA thread]
                            ↓ bounded blocking queue (20)
              Thread 2+ (worker pool): SCOSProcessor.process()
                            ↓ result_ready (queued)
  frame_ready ─queued─→ Main thread (GUI): _on_scos_frame()  → labels, calibration
                                                                   collectors, raw-frame save
                       _on_scos_result() → PlotWidget.append() / HDF5Recorder.append()
                       display_ready     → _on_display_frame()  → ImageWidget.update_frame()
```

IMPORTANT: κ² processing runs on a worker pool, and frame **intake** runs on the camera
thread — not the GUI thread (merged_worklist task 5). A slow `process()` therefore fills a
bounded queue and back-pressures the grab loop (visible: `overload_detected`, `Dropped: N`,
Pylon skipped-frame warnings) instead of lagging the GUI. Each frame's timestamp is taken at
capture on the **monotonic** clock, so GUI scheduling jitter can never enter `timeVec`.

Still on the GUI thread, by design for now: the dark/bright calibration collectors and the
raw-frame HDF5 write (tasks 14/16). Intake backpressure bounds Qt's queued-connection event
queue only while a measurement is running; during DARK_CAL / BRIGHT_CAL it is unbounded as
before.

## Key Modules

- `camera.py` — CameraThread wraps pypylon. Supports Mono8/10/12, hardware trigger (Line2), live parameter changes
- `processor.py` — SCOSProcessor: local variance via `scipy.ndimage.uniform_filter`, noise corrections (shot, dark, quantization)
- `gui/main_window.py` — wires camera, processor, GUI controls, .mat/.npz export
- `gui/image_widget.py` — pyqtgraph ImageItem + circle ROI (auto-detect or manual drag)
- `gui/plot_widget.py` — real-time 1/κ² time-series plot (incremental append, no full redraw)

**Dependencies:** PyQt6 for GUI, pyqtgraph for fast image/plot rendering, pypylon for Basler cameras, numpy/scipy for computation, pyserial for Arduino communication, tifffile/h5py for file I/O.

## Critical Gotchas

IMPORTANT: Exposure in GUI = **milliseconds**. Camera API (pypylon) = **microseconds**. Conversion: `exposure_us = gui_value * 1000`. Getting this wrong silently produces bad data.

IMPORTANT: **G[DU/e] always comes from the measured table**, never from a formula (supervisor's ruling, 2026-09-22). `load_gain_from_table(camera_sn, n_bits, gain_db)` reads `CamerasMeasuredGain.csv` — the same file MATLAB's `LoadG.m` uses — keyed on the camera's **serial number** plus bit depth. A camera that is not in that table cannot be measured with: `MainWindow._prepare_gain()` refuses to start the run and shows "Can't calculate SCOS: CameraSN <> Mono<> was not found in G[DU/e] Calibration file". A camera that *is* in the table but not at the requested gain is fine — G is rescaled in dB from the closest row and the operator is warned.

IMPORTANT: `sat_capacity` must NOT be used for measurements and must NOT be saved with results. The formula `convert_gain(gain_db, bit_depth, sat_capacity)` survives only for the synthetic `--mock-tiff` source (which has no camera and therefore no serial number) and for the offline scripts in `tools/`. On `SCOSProcessor` it is deliberately named `test_mode_sat_capacity` so nothing suggests it belongs in the measurement path.

Known camera parameters:
| Camera | bit_depth | Notes |
|--------|-----------|-------|
| Basler a2A1920-160umPRO (SN 40513592) | 10 | TIFF ×64 (10-bit left-justified in uint16); 1216×1936; in the gain table at Mono10 (16/18/20 dB) and Mono12 (8 dB) |
| Lab demo camera (700×700) | 12 | Must be added to `CamerasMeasuredGain.csv` before it can be used |

- ROI mask: boolean ndarray, same shape as frame, generated from circle (cx, cy, r)
- Save format matches MATLAB convention: .mat with keys `scosTime`, `scosData` (κ²), `frameRate`, `exposureTime`, `Gain`
- Trigger mode "On" = hardware trigger on Line2; "Off" = internal frame rate
- When changing pixel format or trigger mode, camera must stop and restart grabbing
- Default camera params: Mono12, 8ms exposure, 20 Hz frame rate, gain 8 dB

IMPORTANT: a corrected κ² that is ≤ 0 yields no BFi at all, and if *every* frame is like that the measurement can never leave `MEASURING_INIT`. `MainWindow._abort_on_invalid_k2()` catches this and stops the run with an error naming the likely cause — a dark calibration taken with light on the sensor. Before it existed the app sat silently on an empty plot and wrote an all-NaN results file. Fixed for `--mock-folder` playback on 2026-09-28; see the next note.

IMPORTANT: **calibration frames are discarded until the camera's backlog is gone.**
The dark/bright collectors run on the GUI thread behind a queued connection, so when the
camera outruns that handler a backlog builds in Qt's event queue — 60 to 130 frames at
40 Hz, measured. Every one of them was captured *before* the operator clicked OK on the
laser prompt, so without this the bright calibration is built from laser-off frames.
`MainWindow._flush_stale_frames()` is called after each prompt and drops the difference
between what the camera has emitted (`camera.frames_emitted`, on all three emitters) and
what this window has received. Any new frame source must keep that counter.

Scope of that measurement: it was taken in a **headless** run where the laser prompt
returns instantly. A real modal dialog runs a nested event loop that keeps delivering
queued frames while nobody is collecting them, so on the rig the backlog at the moment OK
is clicked is probably much smaller — and the lab camera is 700x700 at 20 Hz, roughly a
tenth of the per-second GUI work of the 2.4 Mpx 40 Hz playback. The flush is cheap
insurance, not a measured rig problem. What *is* measured on both is that a backlog builds
during the collection itself.

IMPORTANT: in `--mock-folder` playback there is no laser to switch off, so
`FolderMockCamera.set_playback_source("dark")` plays the `_dark` folder for the duration of
`DARK_CAL` instead. The bright calibration still comes from the main recording, which was
made with a subject in place, so `spVar` is about 2.3× too large and κ²_corr lands well
below MATLAB. That is the dataset, not the code — `--mock-folder` is for rehearsing the
sequence, and `tests/test_dark_cal_offline.py` / `test_bright_cal_offline.py` remain the
accuracy check. Also note `MainWindow._to_du()`: the Pylon-Viewer TIFFs store 10-bit data
left-justified in uint16, so a frame must be divided by `processor.scale` before any
calibration array is built from it.

IMPORTANT: `Mask.mat`'s `channels.Centers` is **[x y]**, as MATLAB's `imfindcircles`
returns it. Reading it as [y x] — which the code did until 2026-09-28 — placed the ROI
circle at (684, 1215) on a 1216-row frame, centred on the bottom edge. The mask that
circle generates then replaces `totMask` through the `roi_changed` signal, so every κ² in
a replayed session was computed over roughly the wrong half of the sensor. Measured on the
lab recording: the swapped reading agrees with `totMask` on 49.4 % of pixels, the correct
one on 99.3 %. Real-camera sessions are unaffected — there is no `Mask.mat` and the
operator sets the ROI in the GUI.

## Future Protocol Design

The full target measurement protocol (multi-phase calibration with dark + bright frames, ROI shrink, `var_bright` noise term, rBFi normalization, recording-length limits, etc.) is documented in [docs/SCOS_protocol.md](docs/SCOS_protocol.md). The current code implements only a subset — assume features described there are NOT yet present unless this CLAUDE.md says otherwise.

## Code Style

- Python 3.11+, type hints on public functions
- PyQt6 signals/slots for thread communication — never access GUI from camera thread
- NumPy vectorized operations preferred over Python loops for image processing
- Use `np.float64` for all intermediate SCOS calculations to avoid precision loss

## Delegation Policy

Each subagent spawn is its own conversation and costs tokens — be deliberate.

- **Single-file reads, single-symbol greps, "where is X" lookups** → use Read/Grep/Glob directly. Do **not** spawn the Task tool for these.
- **Simple multi-step searches** (e.g. "find all callers of convert_gain across the repo") → prefer the project-local **quick-search** subagent (`.claude/agents/quick-search.md`, pinned to Haiku, read-only). It is cheaper than `general-purpose`.
- **Slightly broader read-only exploration** → use the built-in **Explore** agent.
- **Reserve `general-purpose` and `Plan`** for genuinely multi-file, open-ended, or design-level work — e.g. wiring a new calibration phase from `docs/SCOS_protocol.md`, refactoring the camera/processor threading model, or auditing for race conditions.

If you catch yourself reaching for `Task` to answer a question that could be one Grep call, stop and just do the Grep call.

## Compaction Instructions

When compacting, preserve: list of modified files, current task, any test results or error messages, and the threading model (which thread does what).
