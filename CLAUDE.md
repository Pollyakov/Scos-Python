# CLAUDE.md

## Scientific Priority — Non-Negotiable

**`docs/SCOS_protocol.md` is the absolute source of truth for this project.**
The purpose of SCOS is to measure cerebral blood flow as accurately as possible.
No code change — refactoring, performance optimization, UI improvement, or architectural cleanup — may reduce measurement accuracy or deviate from the protocol.
When in doubt between correctness and convenience, always choose correctness.

## Purpose

SCOS (Speckle Contrast Optical Spectroscopy) — real-time GUI app that acquires frames from a Basler camera, computes speckle contrast (κ²) with noise correction, and plots blood flow (1/κ²) over time. Translated from MATLAB (SCOSvsTime_WithNoiseSubtraction_Ver2.m). Used in the Optical Neuroimaging Lab at Bar-Ilan University.

SCOS measures cerebral blood flow velocity by illuminating tissue with a laser and capturing speckle patterns with a Basler camera. Frame-to-frame intensity fluctuations reveal how fast blood cells are moving.

## Current status

Work is tracked in [`docs/todo.md`](docs/todo.md) — the only task list (fixes from
the first rig session, U1–U11, at the top, then rig-session prep, the backlog, Done). Run modes: `python main.py` (real camera; run
`python check_camera.py` first; it opens with a laser-safety warning that must be confirmed —
`gui/safety_dialog.py`, todo U1), `--mock-folder <recording dir>`, `--mock-tiff <stack>`,
`--mock-h5 <results file>`. Headless whole-session rehearsal with checks:
`python tools/rehearsal.py [--cal-frames 60] [--scenario normal]` — exit code 0 = all passed.

**Math validation result (against MATLAB reference, 600 real frames):**
- Raw κ²: **0.45% error** ✓
- Corrected κ²: **1.2% error** ✓ (G from the measured table for SN 40513592, spVar from smoothingCoefficients.mat,
  dark calibration from 600 dark frames)

**Key parameters for THIS lab:**
- Frame size: not fixed — whatever the camera sends (the rig camera, SN 40075248, sends its full 1216 × 1936 sensor; cropping on the camera is todo U6)
- Frame rate: ~20 Hz (target)
- Recording duration: up to several hours
- Camera: Basler camera (USB or GigE) via pypylon — the rig camera is USB

## What NOT to do
- Don't add features in the old `processor.py` — write new code in `core/`
- Don't put math in GUI files
- Don't access GUI widgets from camera/processor threads (only via pyqtSignal)
- Don't break the offline reference test once it's set up

## Setup & Run

```bash
# Windows setup
setup.bat

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

A pre-commit hook runs the **fast** tests (`-m "not slow"`) before each commit; failures block
the commit. The 4 slow offline MATLAB tests run only with the plain command above.

Test-writing gotchas (QApplication import order, blocked modal dialogs, config
isolation) are in [`tests/CLAUDE.md`](tests/CLAUDE.md), which loads when working in `tests/`.

## Architecture

```
Thread 1 (CameraThread/QThread):  pypylon grabs → t_capture from the camera clock (FrameClock)
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
"Camera: N frame(s) lost" warnings) instead of lagging the GUI. Each frame's timestamp is its
capture time on the **monotonic** scale, so GUI scheduling jitter can never enter `timeVec`.

IMPORTANT: on the real camera `t_capture` is the grab result's **hardware `TimeStamp`**
(start of exposure), converted by `core/frame_clock.py` — but only after that clock has
agreed with the PC clock within 1 % over the first 5 s after Start Video; until then, or
for good if it never agrees, it is the PC time at `RetrieveResult()`. Never assume a tick
rate: a wrong one stretches the whole `timeVec`. The same class counts **lost frames from
`BlockID` gaps** — under `GrabStrategy_OneByOne`, `GetNumberOfSkippedImages()` does not
count frames lost to full buffers. Which clock was used and how many frames were lost go
into the results file's `metadata` (`time_source`, `frames_lost_camera`,
`frames_dropped_queue`). Rig verification: todo D5.

Still on the GUI thread, by design for now: the dark/bright calibration collectors and the
raw-frame HDF5 write (tasks 14/16). Intake backpressure bounds Qt's queued-connection event
queue only while a measurement is running; during DARK_CAL / BRIGHT_CAL it is unbounded as
before.

## Critical Gotchas

IMPORTANT: Exposure in GUI = **milliseconds**. Camera API (pypylon) = **microseconds**. Conversion: `exposure_us = gui_value * 1000`. Getting this wrong silently produces bad data.

IMPORTANT: **G[DU/e] always comes from the measured table**, never from a formula (supervisor's ruling, 2026-09-22). `load_gain_from_table(camera_sn, n_bits, gain_db)` reads `CamerasMeasuredGain.csv` — the same file MATLAB's `LoadG.m` uses — keyed on the camera's **serial number** plus bit depth. A camera that is not in that table cannot be measured with: `MainWindow._prepare_gain()` refuses to start the run and shows "Can't calculate SCOS: CameraSN <> Mono<> was not found in G[DU/e] Calibration file". A camera that *is* in the table but not at the requested gain is fine — G is rescaled in dB from the closest row and the operator is warned.

IMPORTANT: `sat_capacity` must NOT be used for measurements and must NOT be saved with results. The formula `convert_gain(gain_db, bit_depth, sat_capacity)` survives only for the synthetic `--mock-tiff` source (which has no camera and therefore no serial number) and for the offline scripts in `tools/`. On `SCOSProcessor` it is deliberately named `test_mode_sat_capacity` so nothing suggests it belongs in the measurement path.

Known camera parameters:
| Camera | bit_depth | Notes |
|--------|-----------|-------|
| Basler a2A1920-160umPRO (SN 40513592) | 10 | TIFF ×64 (10-bit left-justified in uint16); 1216×1936; in the gain table at Mono10 (16/18/20 dB) and Mono12 (8 dB) |
| Basler a2A1920-160umBAS (SN 40075248, "DAN01") — **the rig camera**, USB | 12 | 1216×1936; in the gain table at Mono12, 8 dB only (G = 0.9564). USB block IDs start at 0 (Done item 41). First rig session 2026-10-08 |

- ROI mask: boolean ndarray, same shape as frame, generated from circle (cx, cy, r)
- Session output (automatic, per Start SCOS) goes to `<Recording name>_<YYYYMMDD_HHMMSS>/` (`scos_<timestamp>/` if the name is left empty): `rBfi_results.h5` (`startTime`, `timeVec`, `rBFi`, `Intensity`, `Params`, plus `k2_raw`/`k2_corr`/`bfi` and a `metadata` group — camera SN, G source, `time_source`, lost/dropped frame counts), `Calibration.h5` (`dark` + `bright` groups) and `rBfi_fig.png`
- The manual "Save SCOS Data" button is a separate, older export: .mat with keys `scosTime`, `scosData` (κ²), `frameRate`, `exposureTime`, `Gain` (or the same as .npz)
- Trigger mode "On" = hardware trigger on Line2; "Off" = internal frame rate
- When changing pixel format or trigger mode, camera must stop and restart grabbing
- Default camera params: Mono12, 8ms exposure, 20 Hz frame rate, gain 8 dB
- Settings: `scos_config.json` is the **committed defaults and is never written by the app**; the operator's last-used settings go to the gitignored `scos_config.local.json` on close (`MainWindow._save_config()`), real camera only (`CameraThread.persists_settings`). Delete it to reset. Tests are redirected to a private copy by `tests/conftest.py` (`SCOS_CONFIG_DIR`).

IMPORTANT: a corrected κ² that is ≤ 0 yields no BFi at all, and if *every* frame is like that the measurement can never leave `MEASURING_INIT`. `MainWindow._abort_on_invalid_k2()` catches this and stops the run with an error naming the likely cause — a dark calibration taken with light on the sensor. Before it existed the app sat silently on an empty plot and wrote an all-NaN results file. Fixed for `--mock-folder` playback on 2026-09-28; see the next note.

IMPORTANT: **calibration frames are discarded until the camera's backlog is gone.**
The dark/bright collectors run on the GUI thread behind a queued connection, so when the
camera outruns that handler a backlog builds in Qt's event queue — from tens to over a
thousand frames, depending on the calibration length and the PC's speed (measurements in
todo D8). Every one of them was captured *before* the operator clicked OK on the
laser prompt, so without this the bright calibration is built from laser-off frames.
`MainWindow._flush_stale_frames()` is called after each prompt and drops the difference
between what the camera has emitted (`camera.frames_emitted` — kept by all three camera
classes, bumped once per frame just before `frame_ready`) and
what this window has received. Any new frame source must keep that counter.

Scope of those measurements: they were taken in **headless** runs where the laser prompt
returns instantly. A real modal dialog runs a nested event loop that keeps delivering
queued frames while nobody is collecting them, so on the rig the backlog at the moment OK
is clicked is much smaller — measured on the rig on 2026-10-08 (full 1216×1936 frames at
20 Hz, about half the per-second GUI work of the 2.4 Mpx 40 Hz playback): "Flushing 2"
and "Flushing 3" frames after the laser prompts. The flush is cheap insurance, not a
measured rig problem. What *is* measured on both is that a backlog builds
during the collection itself.

IMPORTANT: in `--mock-folder` playback there is no laser to switch off, so
`FolderMockCamera.set_playback_source("dark")` plays the `_dark` folder for the duration of
`DARK_CAL` instead. The bright calibration comes from the main recording, subject in
place — which is correct: **the bright calibration is taken with the subject in the
measurement area** (user's instruction, 2026-10-08), and MATLAB's `smoothingCoefficients.mat`
was made from the same frames. With 600 bright frames a playback session matches MATLAB
(κ²_corr 0.0083 vs `LocalStd7x7_corr.mat`'s mean 0.00838; `spVar` 0.513 vs 0.528 in
`totMask`; measured 2026-10-08). The older claim that playback lands well below MATLAB
because "`spVar` is 2.3× too large from the subject" was wrong: its reference figure, 0.0105,
is not the recording's mean — its origin is not traced: no variable in `LocalStd7x7_corr.mat` or `smoothingCoefficients.mat` averages 0.0105, though single frames reach it (MATLAB's κ²_corr pulses between 0.004 and 0.013). With 60 bright frames κ²_corr *is* lower (≈ 0.0070–0.0076): the leftover noise in
an average of N frames is counted as `spVar` (open question 17). `tests/test_dark_cal_offline.py`
/ `test_bright_cal_offline.py` remain the accuracy check. Also note `MainWindow._to_du()`: the Pylon-Viewer TIFFs store 10-bit data
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

IMPORTANT: the opening dialogs follow `docs/SCOS_protocol.md:11-17` and their order is
part of the protocol, not a UI preference: G[DU/e] from the table, then the output folder
(created there and then, named from the **Recording name** field plus a timestamp), and
only then "Please turn off the laser". Anything that needs the keyboard happens before the
room goes dark. `tests/test_recording_name.py` asserts the order by recording which dialog
opens first — don't reorder them to make a code path tidier.

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
