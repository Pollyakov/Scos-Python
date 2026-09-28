# SCOS — Implementation Backlog

Last updated: 2026-09-03 (items 17, 18, and the A3 rewrite added; see docs/reviews/merged_worklist.md for the full task list these come from)

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
| 7 | `core/pipeline.py` | `RealtimePipeline`: drop-oldest 20-frame `_input_q` + `ThreadPoolExecutor` (configurable workers) for parallel processing, with an `_inflight_sem` semaphore capping in-flight work so a slow patch can't grow memory without bound. Results emitted in submission order. `dropped_count` now shown in the GUI Info panel and logged on change. **See item A3 below — moving intake off the GUI thread is still open.** |
| 8 | `core/session.py` | `State` enum, `SessionConfig` dataclass, `DarkCalCollector` (Welford online stats), `BrightCalCollector` |
| 9 | Phase 1 — mock cameras | `mock_camera.py` (TIFF stack), `folder_camera.py` (real lab folder, auto-loads calibration), `h5_replay.py` (replays saved HDF5). CLI flags: `--mock-tiff`, `--mock-folder`, `--mock-h5` |
| 10 | `tools/synth_tiff.py` | Generates synthetic Rayleigh-distributed TIFF stacks for offline development |
| 11 | `logging` module | All debug/info output goes to `logging`; session log written to `app.log` |
| 12 | Math bugs fixed | `bright_var` (spVar) term added; unbiased variance estimator; `dark_var` spatially smoothed; `sat_capacity` corrected to 11117 e⁻ for a2A1920-160umPRO |
| 13 | Phase 2 — real camera partial validation | App ran on real system at 40 Hz; processing time ~13 ms per frame (~12 ms headroom). Camera + laser streaming confirmed working. |
| 14 | Processing workers GUI control | `spn_workers` spinbox in SCOS group (range 1–8, default 3). Pipeline recreated on Start SCOS with the selected count. Tooltip shows machine core count. |
| 15 | `shrink_mask_for_window` — ROI edge fix | `processor.shrink_mask_for_window(mask, window)` erodes the ROI by `window//2+1` px. Applied at MEASURING_INIT start; shrunk mask used for κ² only, full mask kept for display. Erosion size logged. 4 new tests. |
| 16 | `GrabStrategy_OneByOne` + skipped-frame warning | `camera.py` now uses `OneByOne` + `MaxNumBuffer=20`. After each `RetrieveResult`, `GetNumberOfSkippedImages()` is checked and a `warning` signal emitted if > 0. Both test mocks updated. |
| 17 | Stop silently dropping data | `core/pipeline.py`'s emitter no longer swallows a worker exception with a bare `continue` — it's now logged (with traceback) and counted via `error_count`. `core/recorder.py.append()` no longer drops a row when κ² ≤ 0 — it keeps the row and stores `bfi=NaN` (via `n_invalid`) instead, so `time` stays evenly spaced for downstream FFT-based analysis. |
| 18 | Fix the ROI data race | `processor.py`: `set_roi()` and `process()` now publish/read one immutable `_RoiCrop` bundle via a single attribute assignment/read (atomic under the GIL) instead of 5 separate fields, so a worker thread in `process()` can no longer observe a torn mix of old/new ROI state. `gui/image_widget.py`: new `set_roi_locked()` disables the draggable circle (via `setEnabled`, which also disables its resize handles) and the Auto/Draw/Clear ROI buttons; wired into `gui/main_window.py`'s `_set_state()` so the ROI is locked for the duration of `MEASURING_INIT`/`MEASURING`. Verified the race empirically: the new concurrency test fails with ~28% of calls raising shape-mismatch errors against the old code, 0 against the fixed code. |
| 19 | Real sustained-overload test | `tests/test_pipeline.py` gains `TestSustainedOverload`, closing merged-worklist task 4 (the old `test_inflight_capped_under_sustained_overload` only covered item count, not memory). Memory is measured exactly, via a `weakref` to every submitted frame — the alive count *is* the number of frames the pipeline still holds — rather than by noisy RSS sampling; frames are the lab's real 700×700 uint16 size. Flooding a stalled 2-worker pipeline with 60 frames: **capped (shipped)** retains 25 frames / 24.5 MB with 35 drops counted; **uncapped (pre-fix)** retains all 60 / 58.8 MB with **0** drops counted — Review B's silent leak, reproduced. The uncapped case is kept as a permanent negative control so the cap can't be removed, nor the bound widened, without a red test. Verified it fails on broken code: neutering the semaphore made it fail with "retained 60 frames (58.8 MB)… expected <= 32". Tests only — no production code touched. Suite 194/194. |
| 20 | Frame intake off the GUI thread + capture-time timestamps | Closes merged-worklist task 5. `frame_ready` now carries `(frame, t_capture)` — `t_capture` is `time.monotonic()` taken at the grab, in all three real emitters (`camera.py`, `mock_camera.py`, `folder_camera.py`; `h5_replay._NullCamera` only stubs the signal and never emits). `RealtimePipeline.on_frame()` is direct-connected to that signal so intake executes on the **camera** thread: a full queue now blocks the grab loop (real backpressure, absorbed by Pylon's `MaxNumBuffer=20`) instead of freezing the GUI or piling frames into Qt's unbounded event queue. The wait is capped (`put_timeout_s`, 1.5 s) and a frame lost to that cap is counted — an unbounded wait would hang `CameraThread.stop()`, which calls `wait()` with no timeout, and with it the GUI. `overload_detected(depth)` fires once per episode at 80 % fill (re-arms below 50 %). `timeVec` is built from capture time relative to the first captured frame; `t0_wall` is latched alongside it for task 9's absolute `startTime`. Camera warnings (now the *expected* overload symptom) were demoted from a modal dialog to status bar + `app.log` during a session. **Verified end-to-end:** with the GUI handler stalled at 150 ms/frame against a 20 Hz source, `timeVec` keeps the camera's ~59 ms cadence; the pre-change design produced 151 ms gaps — a 20 Hz recording described as 6.6 Hz, which is a wrong heart rate out of the FFT. Suite 209/209; offline MATLAB dark/bright 4/4 at <2 %. |
| 21 | One session folder + real FINISHED transition | Closes merged-worklist task 8 (`gui/main_window.py` only). `_cal_output_folder` split into `_output_root` (parent, asked once per window) and `_session_folder` (a fresh `scos_<timestamp>/` per run) — dark cal, bright cal and results now land together, so two runs can't interleave files. The second `QFileDialog` inside `_start_recorder`, which used to pop up *mid-measurement*, is gone. Stop SCOS and the duration auto-stop both now go FINISHED → `_finish_session()` → PREVIEW; cancelling an unfinished calibration still goes straight to PREVIEW, since there are no results to finalize. `_finish_session()` deliberately runs while the recorder is still open — that is where E1/E2's close-time `rBFi` write, E3's laser popup and E4's figure save attach. New `tests/test_session_lifecycle.py` (7 tests); the two FINISHED tests were confirmed to fail against the old path. Suite 216/216. |
| 22 | Abort a run whose corrected κ² is never positive | Found in a mock-folder rehearsal on 2026-09-26: all 704 results came back at κ² ≈ −0.0029, so `bfi_raw = 1/κ²` was never computed, `_bfi_norm_buffer` stayed empty, and the session never advanced out of `MEASURING_INIT`. The app showed an empty plot and a frozen "Normalizing — 22.3 / 5 s" for 22 s and said nothing; stopping it produced a schema-valid `rBfi_results.h5` with an all-NaN `bfi` and no `rBFi`, the only trace being one WARNING in `app.log`. **That run's cause was specific to playback** (`--mock-folder` cannot switch the laser off, so the "dark" frames were the laser-on recording — `spIm` came out as symmetric noise around zero, proving both collectors drew from the same stream; see D6, the mode is not broken, Start SCOS just discards a good calibration). **The silence was not:** room light, a laser still settling, or OK clicked a beat early give the same picture on real hardware. New `MainWindow._abort_on_invalid_k2()` stops the run and shows an error naming the likely cause and the folder the raw data went to. It fires only once all three hold: no usable frame so far, `t` past `norm_seconds + 2 s`, and at least 10 results in — so a stalled pipeline or one stray late sample cannot abort a good run. The stop goes through `btn_start_scos.setChecked(False)`, the same funnel as the duration auto-stop, so an aborted session is finalized exactly like a manual one. New `tests/test_invalid_k2_guard.py` (8 tests), 4 of which were confirmed to fail with the guard removed. Suite 245/245. |

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

#### A3 · Cap in-flight work; don't just block `put()`

**Why it matters:** `_DropOldestQueue` (in `core/pipeline.py`) is bounded and its drops are
counted — but that was never the actual leak. The `ThreadPoolExecutor`'s internal queue and
the `_inflight` deque behind it (`core/pipeline.py`, dispatcher loop) had **no size limit at
all**. A sustained processing slowdown grew those two without bound while `dropped_count`
kept reading zero — over an hour that is an out-of-memory crash that loses the whole
session, with no warning. Reproduced experimentally in `Review_Findings_B.md`.

**This doc previously said** "replace `_DropOldestQueue` with a blocking `queue.Queue(20)`."
**That fix is insufficient on its own** — a fast dispatcher empties a blocking `queue.Queue`
into the still-unbounded executor queue just as fast as a drop-oldest one. It also can't run
on the GUI thread (a full blocking queue would freeze the UI, since intake currently happens
in `_on_scos_frame`); moving intake off the GUI thread is tracked separately (see
`Implementation_Plan.md §2` and the pipeline docstring in `core/pipeline.py`).

**Fix implemented:** a `threading.Semaphore` (`_inflight_sem`, default `2 * n_workers`)
caps how many frames may be submitted-but-not-yet-collected at once. The dispatcher thread
(not the GUI thread) blocks on this semaphore before calling `pool.submit()`; the emitter
releases it after each future resolves. While the dispatcher is blocked, new frames still
land in `_input_q`, whose bounded drop-oldest behavior (and `dropped_count`) absorbs the
backlog visibly instead of letting it grow silently downstream. `dropped_count` is now
surfaced in the GUI Info panel (`Dropped: N`) and logged to `app.log` when it changes.

~~**Still open:** moving frame intake off the GUI thread onto a real bounded
`frame_queue`~~ — ✅ **DONE**, see item 20 in the Done table. Intake now runs on the camera
thread against a bounded *blocking* queue, with `overload_detected` at 80 % full.

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

**DONE 2026-09-23** — see merged_worklist task 9 for the full note. The schema now matches
the supervisor's spec and her answers of 2026-09-23:

- `rBfi_results.h5` (her spelling) with `startTime` (fixed-length ASCII, `23-Sep-2026
  15:41:47`, so MATLAB's `datetime()` reads a char row and not a cell array), `timeVec`,
  `rBFi`, `Intensity`, and a `Params` group of exactly her ten fields — including
  `normalizationConstant`, `normalizationMethod`, `normalizationWindowSec` and `gitCommit`.
  `satCapacity` is excluded by instruction. `k2_raw`, `k2_corr` and raw `bfi` are kept
  alongside; camera/G provenance sits in a separate `metadata` group.
- `rBFi` is computed and written **once at close**, in `_finish_session()`, from raw BFi
  buffered on disk during the session.
- **One calibration file, not two:** `Calibration.h5` with `dark` and `bright` groups. This
  overrides the two-file table above — the supervisor changed it on 2026-09-23. The old
  `dark_cal_*.mat` / `bright_cal_*.mat` writes and the embedded `calibration` group are gone.
- `h5_replay.py` reads both the old and new dataset names, so existing recordings still play.

Still open for Tier E: the normalization method itself (E1 — the constant is currently always
the mean, and `normalizationMethod` records that honestly), the figure file (E4), the laser
popup (E3) and the v0 tag (E5).

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

#### B3 · Persist GUI settings between launches

Nothing the operator sets in the GUI survives closing the window. `_load_config()`
(`gui/main_window.py`) reads `scos_config.json` into the widgets at startup, but no counterpart
ever writes it back — grep for `json.dump` finds nothing. So every launch resets exposure, gain,
frame rate, window size, dark/bright frame counts, normalization seconds and measurement
duration to whatever is hardcoded in the widget constructors or listed in the config file.

Found on 2026-09-26: the operator set `Dark Frames` to 60, relaunched the app, and the run used
600 again. Workaround applied that day — `n_dark_frames` / `n_bright_frames` were added to
`scos_config.json` by hand (the loader already knew those keys; only the file lacked them).
That is a patch for two fields, not a fix.

**Implement:** a `_save_config()` that writes the same key set `_load_config()` reads, called
from `closeEvent()` before the threads are stopped. Keep the file human-editable (indent=4,
stable key order) — it is currently edited by hand and should stay that way. Write to a temp
file and rename, so a crash mid-write cannot leave an unparsable config and brick the next
launch. Do not save a setting that was forced by the mode rather than chosen by the operator
(e.g. `external_trigger` is switched off and disabled in `--mock-folder` playback).

**Also remember the last results folder.** `_output_root` is neither a widget nor a config
key today: it is asked for once per window launch via `QFileDialog` in `_start_dark_cal()` and
lost on close, so the operator re-navigates to the same place at every launch. Save the chosen
path as `output_root` in the config and pass it as the dialog's starting directory next time.
**Keep showing the dialog** — do not silently reuse the path. Writing a session into the
previous subject's folder is far worse than one extra click, and the operator may be working
with a different subject or a different drive. This one differs from the settings above in that
`_load_config()` does not read it either, so both halves need adding.

**Note — not the same bug as the "600 frames" auto-load.** In `--mock-folder` mode,
`_auto_load_folder_calibration()` streams *every* dark TIFF in the recording folder and ignores
the `Dark Frames` spinbox entirely. That path does not exist for a real camera (it is gated on
`hasattr(camera, "get_calibration_mat")`, which only `FolderMockCamera` has), so it needs no
fix — but the two are easy to confuse when reading a bug report.

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

#### D5 · Verify `GrabStrategy_OneByOne` on real hardware

The `GrabStrategy_OneByOne` + `GetNumberOfSkippedImages()` change in `camera.py` cannot
be tested with mock cameras — they bypass `camera.py`'s `run()` loop entirely.

**How to verify when real camera is available:**
1. Run `python main.py` (no mock flag).
2. Start Video at a normal FPS (40 Hz).
3. Confirm normal streaming works — no regressions.
4. Deliberately overload: raise FPS to a value the system cannot sustain (e.g. 150 Hz).
5. Check `app.log` for lines like:
   ```
   WARNING  camera — Camera: N frame(s) dropped (buffer overflow)
   ```
6. Confirm the warning also appears in the GUI status bar.

If no drops occur even at high FPS, the buffer is absorbing them — that is also a valid
result (it means `MaxNumBuffer=20` is giving enough slack).

---

#### D6 · Start SCOS destroys the auto-loaded calibration in `--mock-folder`

`_auto_load_folder_calibration()` loads a correct calibration when a recording folder is
replayed: `dark_mean`/`dark_var` streamed from the `_dark` folder, `spVar` from
`smoothingCoefficients.mat`, the ROI mask from `Mask.mat`, and `processor.scale = 64`. Start
SCOS then runs the live dark and bright calibrations on top and overwrites all of it — and in
playback there is no laser to switch off, so those 60 "dark" frames are the laser-on recording.
Two independent faults follow: `dark_var` carries the live signal's variance, and `dark_mean`
is in raw units while `process()` divides the frame by 64.

Result: corrected κ² is negative in every frame and the run aborts (see Done item 22). A
mock-folder rehearsal therefore cannot currently reach MEASURING, which is exactly what that
mode exists to rehearse.

**Measured 2026-09-27** — this is not a limit of the mode. Feeding the same main TIFFs through
a processor holding only the auto-loaded calibration gives `dark_mean` = 99.3 DU (the BL100DU
baseline, i.e. genuinely dark), `dark_var` = 5.6, and κ²_corr ≈ 0.0104 against MATLAB's
0.0105 — a 1.3 % match. The calibration is good; Start SCOS simply throws it away.

**Two ways to fix, pick one:**

1. *Skip the live calibration in playback* and keep the auto-loaded arrays. Cheapest, but
   `Calibration.h5` is written inside `_finish_dark_cal` / `_finish_bright_cal`, so skipping
   them leaves the session folder without the very file whose layout the rehearsal is meant to
   prove. The auto-loaded arrays would have to be routed through `write_calibration()` too.
2. *Serve dark frames from the dark folder.* Have `FolderMockCamera` play the `_dark` TIFFs
   while the state is `DARK_CAL`. Every prompt, collector, folder dialog and file write then
   runs exactly as on real hardware and gets frames that really are dark — a faithful
   rehearsal instead of a bypassed one. More work, and bright calibration still has no clean
   source, since the main recording was made with a subject in place.

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
  → B1 (overload dialog) → B2 (disk space) → B3 (persist GUI settings)
  → C1 (scos_math) → C2 (frame_source ABC) → C3 (camera_source)
  → D1/D2/D3/D4/D6 (any order)
  → F1 (raw frames) → F2 (long sessions) → F3 (laser control)
```

Tier-A items can be done independently of each other (different files) but all should be
done before v0. Tier-E items E1–E4 can also be done in parallel.
