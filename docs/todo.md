# SCOS — Implementation Backlog

Last updated: 2026-06-04

> **Reconciliation note (vs. 2026-05-12 version):**
> Items 5, 6, 7, and 16 from the previous version have landed and are reclassified below.
> Several new items were also added. The remaining items are reordered by priority —
> **measurement correctness first**, then v0 release requirements, then operator safety,
> then architecture cleanup, then tooling.

---

## ✅ Done

| # | Task | Notes |
|---|------|-------|
| 1 | Auto-save to HDF5 wired into GUI | Folder picker on Start SCOS, status bar shows path |
| 2 | `HDF5Recorder` class (`core/recorder.py`) | Buffered writes, flushes every 300 points; `save_calibration()` and `append_frame()` also implemented |
| 3 | `closeEvent` flush | `recorder.close()` called before window exit |
| 4 | Metadata saved with session | fps, gain, exposure, window, ROI, sat_capacity → HDF5 `metadata` group |
| 5 | rBFI normalization — "seconds" mode | `MEASURING_INIT` collects `norm_seconds` of BFI, computes mean, divides all subsequent values. **"Pulsation lower level" mode logs a warning and falls back to mean — see E1 below.** |
| 6 | Full state machine | `State` enum (IDLE → PREVIEW → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED/ERROR) wired in `gui/main_window.py` via `_set_state()`. Colored status indicator in GUI. |
| 7 | `core/pipeline.py` | `RealtimePipeline`: drop-oldest 20-frame queue + `ThreadPoolExecutor` (configurable workers) for parallel processing. Results emitted in submission order. **See item A3 below — silent drop must still be fixed.** |
| 8 | `core/session.py` | `State` enum, `SessionConfig` dataclass, `DarkCalCollector` (Welford online stats), `BrightCalCollector` |
| 9 | Phase 1 — mock cameras | `mock_camera.py` (TIFF stack), `folder_camera.py` (real lab folder, auto-loads calibration), `h5_replay.py` (replays saved HDF5). CLI flags: `--mock-tiff`, `--mock-folder`, `--mock-h5` |
| 10 | `tools/synth_tiff.py` | Generates synthetic Rayleigh-distributed TIFF stacks for offline development |
| 11 | `logging` module | All debug/info output goes to `logging`; session log written to `app.log` |
| 12 | Math bugs fixed | `bright_var` (spVar) term added; unbiased variance estimator; `dark_var` spatially smoothed; `sat_capacity` corrected to 11117 e⁻ for a2A1920-160umPRO |
| 13 | Phase 2 — real camera partial validation | App ran on real system at 40 Hz; processing time ~13 ms per frame (~12 ms headroom). Camera + laser streaming confirmed working. |
| 14 | Processing workers GUI control | `spn_workers` spinbox in SCOS group (range 1–8, default 3). Pipeline recreated on Start SCOS with the selected count. Tooltip shows machine core count. |
| 15 | `shrink_mask_for_window` — ROI edge fix | `processor.shrink_mask_for_window(mask, window)` erodes the ROI by `window//2+1` px. Applied at MEASURING_INIT start; shrunk mask used for κ² only, full mask kept for display. Erosion size logged. 4 new tests. |
| 16 | `GrabStrategy_OneByOne` + skipped-frame warning | `camera.py` now uses `OneByOne` + `MaxNumBuffer=20`. After each `RetrieveResult`, `GetNumberOfSkippedImages()` is checked and a `warning` signal emitted if > 0. Both test mocks updated. |

---

## ❌ Not done — ordered by priority

---

### Tier A — Measurement Correctness (do before anything else)

These items affect the scientific validity of results. Per the CLAUDE.md scientific priority
policy, correctness beats everything.

---

#### ~~A1 · `shrink_mask_for_window`~~ — ✅ DONE (see item 15 in Done table)

---

#### ~~A2 · `GrabStrategy_OneByOne`~~ — ✅ DONE (see item 16 in Done table)

---

#### A3 · Replace `_DropOldestQueue` with blocking bounded queue

**Why it matters:** `_DropOldestQueue` (in `core/pipeline.py:50-80`) silently evicts the
oldest frame when full. `Implementation_Plan.md §2` explicitly requires "block until there
is space — never drop silently."

**Fix:** Replace `_DropOldestQueue` with `queue.Queue(maxsize=20)`. The capture side calls
`put(frame)` (blocking). If it blocks for more than ~1 s without progress, emit
`overload_detected` (see B1).

---

### Tier E — v0 Release Requirements

These are the concrete items needed before tagging version 0. They come from the
supervisor's requirements (2026-06-04 PDF). Do after Tier A, before architecture cleanup.

---

#### E1 · "Pulsation lower level" normalization — short vs long recording

**What the MATLAB reference does** (from `SCOSvsTime_WithNoiseSubtraction_Ver2.m` line 505):
```matlab
if timeVec(end) > 120   % long recording
    rBFi = BFi / mean(BFi(1 : round(norm_seconds * frameRate)));
else                    % short recording
    rBFi = BFi / prctile(BFi(1 : round(norm_seconds * frameRate)), 5);
end
```

**What is currently done:** both modes compute `mean` (the "pulsation lower level" option
logs a warning and falls back to mean at `gui/main_window.py:1124`).

**What to implement:**
- The normalization constant cannot be chosen until the recording ends (because "short vs
  long" depends on the final duration). Keep the live plot using `mean` during the session.
- At `FINISHED` (Stop SCOS or auto-stop): check actual `elapsed_measuring`.
  - If > 120 s → normalization constant stays as-is (mean of first `norm_seconds`)
  - If ≤ 120 s → recompute constant as `np.percentile(bfi_norm_buffer_values, 5)`, re-scale
    the already-plotted data, and save the corrected `rBFi` to HDF5.
- The `norm_seconds` spinbox already exists in the GUI and controls the window length.
- Also: if total time > 120 s, **convert the plot x-axis to minutes** (not seconds).

---

#### E2 · Correct HDF5 file structure per protocol

**Required output files** (from supervisor's PDF):

| File | Contents |
|---|---|
| `rBFi_results.h5` | `startTime`, `timeVec`, `rBFi`, `Intensity`, `Params` (struct/group) |
| `rBFi_fig.png` | Saved screenshot of the BFI plot at session end |
| `DarkCalibration.h5` | `mean_dark`, `var_dark`, `n_frames`, `window_size` |
| `BrightCalibration.h5` | `sp_im`, `bright_var`, `n_frames`, `window_size` |

**Current state:** the recorder saves one file with fields `time`, `k2_raw`, `k2_corr`,
`bfi`, `mean_intensity` plus a `metadata` group. Missing: `startTime` as an explicit
dataset, `rBFi` (currently saves raw `bfi`, not normalized), `Params` group, separate
calibration HDF5 files, the figure file.

**Changes needed:**
- `HDF5Recorder`: add `startTime` scalar dataset; add `rBFi` dataset (or rewrite `bfi`
  as `rBFi` after normalization is finalised); rename `mean_intensity` → `Intensity`;
  add a `Params` group containing fps, gain, exposure, window, ROI, sat_capacity.
- `_finish_dark_cal()`: also write `DarkCalibration.h5` alongside the existing `.mat`.
- `_finish_bright_cal()`: also write `BrightCalibration.h5`.
- `_on_finished()`: write final `rBFi` (after E1 re-normalization) to the results file.

---

#### E3 · End-of-session popup + laser-off intensity check

**What the supervisor requires:**
1. When measurement ends (Stop SCOS button or auto-stop by duration), show a pop-up:
   *"Measurement has ended. Please turn off the laser."* with an OK button.
2. After the user clicks OK, capture one frame and check that the mean intensity over the
   ROI has dropped by ≥ 90 % compared to the last known measurement intensity.
   - If yes: proceed to flush and save normally.
   - If no: show a warning: *"Laser may still be on — mean intensity did not drop by 90 %
     (measured: X DU, expected: < Y DU). Continue anyway?"*

**Where to add it:** in `_toggle_scos(False)` / `_set_state(FINISHED)` in
`gui/main_window.py`. The last `mean_i` value is already available from `_on_scos_result`.

---

#### E4 · Save plot figure at end of session

At `FINISHED`, export the BFI time-series plot to a PNG file in the same output folder:
```python
exporter = pg.exporters.ImageExporter(self.plot_widget.scene())
exporter.export(str(output_folder / "rBFi_fig.png"))
```
Show the saved path in the status bar.

---

#### E5 · Git tag v0

After E1–E4 are done and all tests pass, create an annotated git tag:
```
git tag -a v0 -m "Version 0: complete real-time SCOS with calibration, normalization, HDF5 save"
```

---

### Tier B — Operator Safety

These protect against data loss and let the operator react before a session is ruined.
Implement after Tier A and E are done.

---

#### B1 · Queue fill indicator + overload dialog

**Queue fill bar:** Add a small progress bar (0–100 %) to the status bar showing
`frame_queue.qsize() / frame_queue.maxsize`. Update it every second via the existing
`QTimer`. Add a dropped-frame counter label next to it.

**Overload dialog:** When queue fill ≥ 80 %, pause capture and show a non-blocking dialog
with 4 options (least-disruptive first):
1. Reduce ROI radius
2. Reduce target FPS
3. Switch to save-only (write raw frames, skip κ² — process later)
4. Switch to process-only (skip disk, keep computing)

5–10 s timeout → default to save-only. On timeout, log the decision and resume.

---

#### B2 · Disk-space check

Before starting a recording, call `shutil.disk_usage(output_folder)` and refuse to start
if free space < some threshold (e.g. 5 GB). During a session, check every few minutes and
stop gracefully if space drops below 1 GB. Show remaining disk space in the status bar.

---

### Tier C — Architecture Cleanup

Refactoring the math and camera layers into clean, testable modules. Do after Tier A and E
are solid — don't refactor code that still has correctness bugs or missing protocol features.

---

#### C1 · `core/scos_math.py` — extract pure math from `processor.py`

Move the κ² formula, gain conversion, `local_mean_and_variance`, and `shrink_mask_for_window`
into a pure-functions module with no camera/GUI imports. This makes unit testing trivial
and isolates the scientific core from infrastructure.

Key functions to extract:
```python
convert_gain_db_to_due(gain_db, bit_depth, sat_capacity_e) -> float
local_mean_and_variance(im, window) -> (mean_im, var_im)
shrink_mask_for_window(mask, window) -> np.ndarray
compute_kappa2(frame, mask, window, mean_dark, var_dark_filtered,
               var_bright_filtered, G_due) -> (kappa2_raw, kappa2_fixed, mean_intensity)
```

Add `test_scos_math.py` with offline-reference test against the MATLAB output.

---

#### C2 · `core/frame_source.py` ABC

Define a `FrameSource` abstract base class with `open()`, `get_frame(timeout_s)`, `close()`.
Make `CameraThread`, `MockCameraThread`, and `FolderMockCamera` implement it so they are
interchangeable without duck-typing. Prerequisite for C3.

---

#### C3 · `core/camera_source.py` with `trigger_mode` parameter

A clean `CameraFrameSource` wrapping pypylon, replacing `camera.py` for the refactored
architecture. Must support `trigger_mode='off'|'line2'`. Document: with
`trigger_mode='line2'`, `frame_rate_hz` is a target — actual rate is whatever the Arduino
delivers on Line2.

---

### Tier D — Tooling & Docs

These can land in any order and don't block correctness or release work.

---

#### D1 · `setDownsampling` + `setClipToView` in `gui/plot_widget.py`

For recordings longer than ~100 k points, pyqtgraph will lag without downsampling.
One-line fix when the plot curve is created:
```python
self._curve.setDownsampling(auto=True, mode='peak')
self._curve.setClipToView(True)
```

---

#### D2 · `pyproject.toml` + `requirements.lock`

Consolidate pytest/ruff/mypy config in `pyproject.toml`. Run `pip-compile` to produce
`requirements.lock` for reproducible installs.

---

#### D3 · GitHub Actions CI

Add `.github/workflows/ci.yml` to run `ruff check` + `pytest` on every push and pull
request.

---

#### D4 · `docs/realtime_architecture.md`

A document that answers the open architecture questions from `Implementation_Plan.md §9`
(rBFI normalization policy, disk-space limit policy, save-frames policy). Needs supervisor
input before writing.

---

### Future Phases (post-v0)

These are the next development phases after v0 is tagged.

---

#### F1 · Save raw frames option

Add an option to save every raw camera frame to HDF5 during a session. One frame at 700×700
uint16 ≈ 1 MB. At 40 Hz for 30 min ≈ 72 GB — so this requires disk-space check (B2) first.
The `HDF5Recorder.append_frame()` method already exists; just needs to be wired into the
GUI via the existing `chk_save_frames` checkbox.

---

#### F2 · Long sessions (> 2 hours)

Handle recordings longer than 2 hours without memory growth or GUI lag. Requires:
- Pre-allocated NumPy arrays for results (extend in chunks of 10 000) instead of Python lists
- Periodic HDF5 flush every M minutes (already in `HDF5Recorder`)
- Queue fill indicator (B1) to detect and handle sustained overload
- `setDownsampling` in plot widget (D1) to keep rendering fast at 200 k+ points

---

#### F3 · Automatic laser control via Arduino

Instead of pop-up dialogs asking the user to turn the laser on/off, control the laser
directly from the app via the existing Arduino serial connection. The Arduino already
controls the camera trigger; extend it to also toggle the laser enable pin (currently
pin 13 per recent hardware change). Sequence: dark cal → signal LOW → wait → bright cal →
signal HIGH → wait → measure.

---

## Execution order summary

```
A1 (shrink_mask) → A2 (GrabStrategy) → A3 (blocking queue)
  → E1 (normalization) → E2 (HDF5 format) → E3 (laser popup) → E4 (plot save) → E5 (tag v0)
  → B1 (overload dialog) → B2 (disk space)
  → C1 (scos_math) → C2 (frame_source ABC) → C3 (camera_source)
  → D1/D2/D3/D4 (any order)
  → F1 (raw frames) → F2 (long sessions) → F3 (laser control)
```

Tier-A items can be done independently of each other (different files) but all should be
done before v0. Tier-E items E1–E4 can also be done in parallel.
