# CLAUDE.md

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
   — diagnosed via Phase-0 PTC analysis; `load_calibration_mat` now accepts this as a parameter.

**Math validation result (against MATLAB reference, 600 real frames):**
- Raw κ²: **0.45% error** ✓
- Corrected κ²: **1.2% error** ✓ (using sat_capacity=11117, spVar from smoothingCoefficients.mat,
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

## Architecture

```
Thread 1 (CameraThread/QThread):  pypylon grabs → emits frame_ready (every frame)
                                                 → emits display_ready (≤30 FPS)
Main thread (GUI):                frame_ready  → _on_scos_frame() → SCOSProcessor.process() → PlotWidget.append()
                                  display_ready → _on_display_frame() → ImageWidget.update_frame()
```

IMPORTANT: Processing runs on the GUI thread. If `process()` exceeds the frame budget (1000/FPS ms), the GUI lags and frames drop.

## Key Modules

- `camera.py` — CameraThread wraps pypylon. Supports Mono8/10/12, hardware trigger (Line2), live parameter changes
- `processor.py` — SCOSProcessor: local variance via `scipy.ndimage.uniform_filter`, noise corrections (shot, dark, quantization)
- `gui/main_window.py` — wires camera, processor, GUI controls, .mat/.npz export
- `gui/image_widget.py` — pyqtgraph ImageItem + circle ROI (auto-detect or manual drag)
- `gui/plot_widget.py` — real-time 1/κ² time-series plot (incremental append, no full redraw)

**Dependencies:** PyQt6 for GUI, pyqtgraph for fast image/plot rendering, pypylon for Basler cameras, numpy/scipy for computation, pyserial for Arduino communication, tifffile/h5py for file I/O.

## Critical Gotchas

IMPORTANT: Exposure in GUI = **milliseconds**. Camera API (pypylon) = **microseconds**. Conversion: `exposure_us = gui_value * 1000`. Getting this wrong silently produces bad data.

IMPORTANT: `convert_gain(gain_db, bit_depth, sat_capacity)` returns DU/e (digital units per electron). Both `bit_depth` and `sat_capacity` are camera-model-specific — they are fixed hardware properties, unrelated to calibration.

Known camera parameters:
| Camera | bit_depth | sat_capacity | Notes |
|--------|-----------|-------------|-------|
| Basler a2A1920-160umPRO (SN 40513592) | 10 | **11117** | TIFF ×64 (10-bit left-justified in uint16); 1216×1936; sat_capacity measured via Phase-0 diagnostic |
| Lab demo camera (700×700) | 12 | 10500 | Default — verify on first use |

- ROI mask: boolean ndarray, same shape as frame, generated from circle (cx, cy, r)
- Save format matches MATLAB convention: .mat with keys `scosTime`, `scosData` (κ²), `frameRate`, `exposureTime`, `Gain`
- Trigger mode "On" = hardware trigger on Line2; "Off" = internal frame rate
- When changing pixel format or trigger mode, camera must stop and restart grabbing
- Default camera params: Mono12, 8ms exposure, 20 Hz frame rate, gain 8 dB

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
