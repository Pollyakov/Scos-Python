# SCOS — Implementation Backlog

Last updated: 2026-10-04 — **this is now the only working task list.** It absorbed the open
items of `docs/reviews/merged_worklist.md`, which is frozen as an archive of the review
evidence and the reasoning behind each task (map at the bottom of this file).

Order: what to do next (rig-session prep) → open backlog by priority → done.

---

## ▶ Next: rig-session prep

The first full session on the real rig is a couple of days away and the lab is not
reachable before then. Everything here either prevents a failure on the day or tests, in
simulation, the machinery the day depends on. Nothing in Tier C, B1, D1–D4, D7 or F goes
in before the session — every change adds risk. **E5 (tag v0) comes after a successful
session, not before.**

| # | Task | What it is | Est. | Status |
|---|---|---|---|---|
| 0 | Message to Vika | Open questions 7–16 from `docs/open_questions.md`, implemented defaults stated so she can just confirm; flag #11 (stop before the normalization window closes → no `rBFi`). Confirm the session's gain (dB) and bit depth match a row of `CamerasMeasuredGain.csv`. | 20 min | Drafted |
| 1a | Docs ↔ code audit | todo, worklist, CLAUDE.md, protocol typo (bright cal said "turn off") | — | ✅ `7c89dbc`, `794d384` |
| 1b | **B3** — persist GUI settings | see B3 below; also remember `output_root` (dialog still shown) | 1 h 45 m | |
| 1c | **B2-lite** — disk pre-flight | refuse to start below a free-space threshold, show free space; skip the mid-session monitor | 40 min | |
| 2 | Simulation checklist | what the operator does and should see in a `--mock-folder` session, dialog by dialog; the expected (low) mock κ²_corr — the bright cal there comes from a subject-in-place recording, spVar ≈ 2.3× too large, which is the dataset, not a bug | 45 min | |
| 3a | Rehearsal harness in `tools/` | promote the headless `e2e_rehearsal.py` (2026-09-28 scratchpad) to a committed, parameterised script with assertions and a scenario flag | 1 h 15 m | |
| 3b | Scenario: slowdown / backpressure | slowed `process()`: `overload_detected` once per episode, drops counted and visible, memory flat, **`timeVec` keeps capture cadence** | 1 h 30 m | |
| 3c | Scenario: overload recovery | restore speed: flag re-arms below 50 %, drops stop | 45 min | |
| 3d | Scenario: compressed long run | looped playback for minutes: recorder flushes, memory flat, plot responsive | 1 h + run | |
| 3e | Output verification | after every scenario: `rBFi` present, length = `timeVec`, all ten `Params`, figure, both `Calibration.h5` groups | 45 min | |
| 3f | **Hands-on GUI pass** (user) | one full `--mock-folder` session with real windows — the only test of the real modal-dialog path (nested event loop) | 45–60 min | |
| 4 | Fix what turns up | budget | ~2 h | |
| 5a | Real-rig checklist | first five minutes at the rig: **D5** (Pylon skipped frames, intake under overload), trigger-mode restart, Arduino, and `bench_processor.py --width 700 --height 700 --window 7 --bits 12` on the lab PC | 45 min | |
| 5b | Vika's expectations sheet | "what you'll see and why it's normal": calibration looks frozen except the counter, `Discarding N buffered frames…`, the G warning, the laser-off window + 90 % check, what stopping early does | 45 min | |

---

## ❌ Open backlog — ordered by priority

---

### Tier A — Measurement Correctness (do before anything else)

These items affect the scientific validity of results. Per the CLAUDE.md scientific priority
policy, correctness beats everything.

---

#### ~~A1 · `shrink_mask_for_window`~~ — ✅ DONE (see item 15 in Done table)

---

#### ~~A2 · `GrabStrategy_OneByOne`~~ — ✅ DONE (see item 16 in Done table)

---

#### ~~A3 · Cap in-flight work; don't just block `put()`~~ — ✅ DONE (see items 7 and 20 in Done table)

> Both halves have landed: `_inflight_sem` caps submitted-but-uncollected work
> (`core/pipeline.py:208`), and intake moved to the camera thread against a bounded
> blocking queue with `overload_detected` at 80 % (item 20). **Tier A is therefore
> complete.** The history below is kept because it records why the obvious fix was the
> wrong one.

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

#### A4 · float32 vs float64 in the corrected numerator *(was worklist task 22)*

CLAUDE.md requires `np.float64` for all SCOS intermediates; `processor.py` computes the local
variance and the corrected numerator `var_im − G·mean − spVar − dark_var − 1/12` in float32.
Review A is explicit that this is **not** a demonstrated accuracy bug — the offline MATLAB
tests pass at 0.6–1.2 % — but it is a written rule the code breaks, and the subtraction of
nearly equal numbers is exactly where float32 would hurt, at small κ² (high flow).

**Done when:** a small-κ² test exists, the precision the MATLAB data supports is kept with the
reasoning written down, and CLAUDE.md §Code Style either holds or records the measured
exception. If that test fails, this jumps the queue.

---

### Tier E — v0 Release Requirements

These are the concrete items needed before tagging version 0. They come from the
supervisor's requirements (2026-06-04 PDF). Do after Tier A, before architecture cleanup.

---

#### ~~E1 · "Pulsation lower level" normalization — short vs long recording~~ — ✅ DONE (see item 23 in Done table)

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
| `rBfi_results.h5` | `startTime`, `timeVec`, `rBFi`, `Intensity`, `Params` (group), plus `k2_raw`, `k2_corr`, `bfi` and a `metadata` group |
| `rBfi_fig.png` | The rBFi curve at session end (`PlotWidget.save_png()`) |
| `Calibration.h5` | One file, two groups: `dark` and `bright` |

*Filenames above are what the code actually writes (`rBfi_`, lower-case f — her spelling), verified 2026-10-04. The supervisor replaced the two separate calibration files with one `Calibration.h5` on 2026-09-23; the original PDF's `DarkCalibration.h5` / `BrightCalibration.h5` no longer apply.*

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

Still open for Tier E: **only the v0 tag (E5)**. The normalization method (E1, item 23), the
laser popup (E3, item 28) and the figure file (E4, item 27) have all landed since this
paragraph was written.

---

#### ~~E3 · End-of-session popup + laser-off intensity check~~ — ✅ DONE (see item 28 in Done table)

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

#### ~~E4 · Save plot figure at end of session~~ — ✅ DONE (see item 27 in Done table)

At `FINISHED`, export the BFI time-series plot to a PNG file in the same output folder:
```python
# As implemented: PlotWidget.save_png(), rendered from the plot item at a fixed
# 1600 px, called after _finalize_normalization() so the curve matches the saved
# constant. Writes <session folder>/rBfi_fig.png.
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

#### B4 · Automatic camera reconnect *(was worklist task 19)*

A GigE dropout during an unattended multi-hour run currently ends the acquisition:
`_on_camera_error` un-checks Start Video and waits for the operator. **Done when:** a simulated
disconnect recovers on its own, into the camera-thread intake path, and the gap is logged
and marked in the data (so `timeVec` shows it rather than hiding it).

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
load_gain_from_table(camera_sn, n_bits, gain_db) -> float   # G for measurements: table only
convert_gain(gain_db, bit_depth, sat_capacity) -> float     # synthetic/test sources ONLY
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
`requirements.lock` for reproducible installs. *(Also covers worklist task 25: `ruff check` and `mypy core/` clean in the pre-commit hook and in CI — explicitly last, accuracy work comes first.)*

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

**Also covers worklist task 6** — the camera-thread intake and capture timestamps (item 20)
need the same real-hardware check, for the same reason: mocks bypass `camera.py`'s grab
loop. During the overload step also confirm: the grab loop *blocks* rather than freezing the
GUI, `overload_detected` appears in the status bar, any drop is counted (`Dropped: N`) and
logged, and the saved `timeVec` is evenly spaced at the camera's rate.

---

#### ~~D6 · Start SCOS destroys the auto-loaded calibration in `--mock-folder`~~ — ✅ DONE (see item 24 in Done table)

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

#### D7 · Write down the worker-pool decision *(was worklist task 7)*

Record whether the multi-worker pool stays, with the timing numbers behind it — belongs in
`docs/realtime_architecture.md` (D4). Recommendation already made in the reviews: keep the
pool **with** the in-flight cap (`_inflight_sem`); going single-thread would remove the
`spn_workers` operator control. Needs the lab-PC benchmark from the rig session.

---

### Future Phases (post-v0)

These are the next development phases after v0 is tagged.

---

#### F1 · Save raw frames option

Add an option to save every raw camera frame to HDF5 during a session. One frame at 700×700
uint16 ≈ 1 MB. At 40 Hz for 30 min ≈ 72 GB — so this requires disk-space check (B2) first.
**Already wired, 2026-10-04 audit:** `MainWindow._on_scos_frame()` calls
`self._recorder.append_frame(frame)` whenever `chk_save_frames` is ticked
(`gui/main_window.py:1818`). What is actually left here is not the wiring:

- the write is **synchronous on the GUI thread**, gzip included — that is worklist task 14;
- there is no disk-space guard in front of it (B2), and the box is reachable today;
- no decision yet on every frame vs every K-th (open question 13).

The checkbox defaults to off, so none of this bites unless an operator ticks it.

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

---

#### F4 · HDF5 recording on its own thread *(was worklist task 14)*

`recorder.append()` and the gzip-compressing `recorder.append_frame()` run synchronously on
the GUI thread (`MainWindow._on_scos_result` / `_on_scos_frame`). Harmless at the result rate;
with Save Frames ticked, a slow disk becomes a GUI freeze. Move the writes behind a bounded
record queue so a slow disk becomes counted backpressure instead. **Done when:** Save Frames
does not stall the GUI, the queue is bounded, and the offline MATLAB tests still pass.

---

## Execution order summary

```
▶ rig-session prep (above): 0 → 1a ✅ → 1b (B3) → 1c (B2-lite) → 2 → 3a → 3f → 3b–3e → 4 → 5a → 5b
  then, after a successful session:
~~A1~~ → ~~A2~~ → ~~A3~~ → A4 (float64 test)
  → ~~E1~~ → ~~E2~~ → ~~E3~~ → ~~E4~~ → E5 (tag v0)
  → B1 (overload dialog) → B2 (full disk monitor) → B4 (camera reconnect)
  → C1 (scos_math) → C2 (frame_source ABC) → C3 (camera_source)
  → D1/D2/D3/D4/D7 (any order); D5 at the rig; ~~D6~~
  → F1 (raw frames) → F2 (long sessions) → F3 (laser control) → F4 (recorder thread)
```

---

## ✅ Done

| # | Task | Notes |
|---|------|-------|
| 1 | Auto-save to HDF5 wired into GUI | Folder picker on Start SCOS, status bar shows path |
| 2 | `HDF5Recorder` class (`core/recorder.py`) | Buffered writes, flushes every 300 points; `save_calibration()` and `append_frame()` also implemented |
| 3 | `closeEvent` flush | `recorder.close()` called before window exit |
| 4 | Metadata saved with session | Two groups, by instruction (2026-09-23). `Params` holds the supervisor's ten fields (`frameRate`, `exposureTime`, `gain`, `windowSize`, `ROI`, `bitDepth`, the three normalization fields, `gitCommit`); `metadata` holds engineering provenance only (`camera_sn`, `camera_model`, `gain_du_per_e`, `gain_source`). **`satCapacity` is excluded from both** — see the Critical Gotchas in CLAUDE.md. Built in `MainWindow._start_recorder()`, written by `core/recorder.py`. |
| 5 | rBFI normalization — "seconds" mode | `MEASURING_INIT` collects `norm_seconds` of BFI, computes mean, divides all subsequent values. **"Pulsation lower level" mode logs a warning and falls back to mean — see E1 below.** |
| 6 | Full state machine | `State` enum (IDLE → PREVIEW → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED/ERROR) wired in `gui/main_window.py` via `_set_state()`. Colored status indicator in GUI. |
| 7 | `core/pipeline.py` | `RealtimePipeline`: drop-oldest 20-frame `_input_q` + `ThreadPoolExecutor` (configurable workers) for parallel processing, with an `_inflight_sem` semaphore capping in-flight work so a slow patch can't grow memory without bound. Results emitted in submission order. `dropped_count` now shown in the GUI Info panel and logged on change. Intake moved off the GUI thread in item 20, which closed the last open half of A3. |
| 8 | `core/session.py` | `State` enum, `SessionConfig` dataclass, `DarkCalCollector` (Welford online stats), `BrightCalCollector` |
| 9 | Phase 1 — mock cameras | `mock_camera.py` (TIFF stack), `folder_camera.py` (real lab folder, auto-loads calibration), `h5_replay.py` (replays saved HDF5). CLI flags: `--mock-tiff`, `--mock-folder`, `--mock-h5` |
| 10 | `tools/synth_tiff.py` | Generates synthetic Rayleigh-distributed TIFF stacks for offline development |
| 11 | `logging` module | All debug/info output goes to `logging`; session log written to `app.log` |
| 12 | Math bugs fixed | `bright_var` (spVar) term added; unbiased variance estimator; `dark_var` spatially smoothed; `sat_capacity` corrected to 11117 e⁻ for a2A1920-160umPRO. **That last one was superseded on 2026-09-22:** G[DU/e] now always comes from `CamerasMeasuredGain.csv`, so no saturation capacity enters a measurement at all (`test_mode_sat_capacity` is synthetic-source only). |
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
| 23 | Short-vs-long rBFi normalization (E1) | Closes worklist task 10. The reference (`SCOSvsTime_WithNoiseSubtraction_Ver2.m:498-514`) divides BFi by the **mean** of the baseline window when `timeVec(end) > 120`, and by its **5th percentile** otherwise; Python always used the mean, so every recording under two minutes — which is what a test run is — disagreed with MATLAB by the ratio of those two statistics, not by a rounding error. Three choices the reference leaves open were settled 2026-09-28: total duration decides (baseline window included, not subtracted); the window is the first `norm_seconds` from the GUI spinbox (MATLAB hardcodes 10 s, `session_tab` says the operator sets it); and picking "Pulsation lower level" forces the percentile at any length. **MATLAB's `prctile` is not numpy's default** — it interpolates between sorted values placed at (i−0.5)/n, which is numpy's `method="hazen"`; on [1,2,3,4] the 5th percentile is 1.0 by MATLAB and 1.15 by numpy's default, and that number divides every point in the file. The rules are pure functions in `core/session.py` (`choose_norm_method`, `normalization_constant`), so no math moved into the GUI. Because the curve is drawn live, the constant computed when the window closes is provisional and `MainWindow._finalize_normalization()` re-picks it at FINISHED, rescaling the plot by `provisional / final` instead of redrawing. `gui/plot_widget.py` now stores seconds and chooses its axis unit at render time, switching to minutes the moment a recording passes 120 s — it previously always divided by 60 and always said "min". Consequence worth knowing: `get_data()`, and therefore `scosTime` in the manual .mat/.npz export, is now in **seconds**, which is what MATLAB's `timeVec` holds. Two adjacent bugs fell out and are fixed here: the Save button exported `1/rBFi` under `scosData`, a key documented as κ², so it was off by the normalization constant; and the plot could end a session with up to a second of points still buffered, which matters because task 12 saves that figure. New `tests/test_normalization.py` (35 tests); six mutations checked — numpy's percentile instead of MATLAB's, `>=` instead of `>` at the threshold, the pulsation override removed, the finalizer not called at close, `scosData` back to `1/rBFi`, and the final render removed — each caught. Suite 280/280 fast, 4/4 slow. |
| 24 | A mock-folder rehearsal can reach MEASURING (D6) | Closes D6. Playback has no laser to switch off, so Start SCOS's dark calibration collected 60 frames of the laser-on recording and overwrote the calibration the auto-load had already put in the processor; corrected κ² was then negative in every frame and the run aborted. Two halves: `FolderMockCamera.set_playback_source("dark")` plays the `_dark` folder while the app is in `DARK_CAL` — every prompt, collector, dialog and file write still runs, they just get frames that really are dark; and `MainWindow._to_du()` converts a frame into the units `process()` computes in before any calibration array is built from it, because the Pylon-Viewer TIFFs store 10-bit data left-justified in uint16 and `dark_mean` was coming out 64× too large (that is where the recorded mean ROI intensity of −7872 DU came from). The intensity labels get the same treatment — they say "DU" and were showing raw counts. **Not a limit of the mode:** measured 2026-09-27, the auto-loaded arrays alone give κ²_corr ≈ 0.0104 against MATLAB's 0.0105. New `tests/test_playback_calibration.py` (12 tests); five mutations checked — never switches to dark, never switches back, collectors fed raw frames again, run loop ignores the source, a missing `_dark` folder accepted silently — each caught. Suite 292/292 fast, 4/4 slow. |
| 25 | Discard frames captured before the lighting changed | The calibration collectors run on the GUI thread behind a queued connection, so when the camera outruns that handler a backlog builds in Qt's event queue — and every frame in it was captured *before* the operator clicked OK on the laser prompt. Measured on the lab recording at 40 Hz with the backlog left in place: 11 of 60 collected "dark" frames were laser-on and **60 of 60 "bright" frames were dark**; `dark_var` came out at 72 instead of 5.6 and corrected κ² was negative in 353 of 355 points. All three emitters now count what they hand to Qt (`camera.frames_emitted`), `MainWindow` counts what it receives, and `_flush_stale_frames()` drops the difference after each prompt. The same mechanism exists on the rig, so this applies to every camera — but the 60–130 figure was measured headless, with the prompt returning instantly; a real modal dialog runs a nested event loop that keeps draining the queue, and the lab camera is 700×700 at 20 Hz against 2.4 Mpx at 40 Hz here. Treat it as cheap insurance rather than a measured rig problem; what is measured on both is that the backlog builds during the collection itself. **A whole session now completes:** headless run on the lab recording gives `dark_mean` = 99.307 DU and `dark_var` = 5.555, equal to streaming the dark folder offline, with κ²_corr positive in 315 of 315 points and `rBFi` written. New `tests/test_frame_flush.py` (7 tests); four mutations checked — skip removed, no flush before dark cal, none before bright cal, margin zeroed — each caught. Suite 299/299 fast, 4/4 slow. |
| 26 | `Mask.mat` ROI centre was read as [y x] | Found by checking the output of the first complete mock-folder rehearsal: `Params.ROI` did not describe the mask in the same file. `channels.Centers` is **[x y]**, as MATLAB's `imfindcircles` returns it, and the code read it the other way round — putting the ROI circle at (684, 1215) on a 1216-row frame, centred on the bottom edge. The mask that circle generates then replaces `totMask` through the `roi_changed` signal, so every κ² in a replayed session was computed over roughly the wrong half of the sensor. Pixel agreement with `totMask`: **49.4 % swapped, 99.3 % correct**. Real-camera sessions are unaffected — there is no `Mask.mat` and the operator sets the ROI in the GUI. The bare `except: pass` around this block, which would have hidden any failure in it, now logs. One test, and the swap put back fails it. Suite 300/300 fast, 4/4 slow. |
| 27 | Save the session figure (E4) | Closes E4 / worklist task 12. At FINISHED, `MainWindow._save_plot_figure()` writes the curve to `rBfi_fig.png` in the session folder, beside `rBfi_results.h5`. `session_tab` names the file `rBfi_fig.fig` — MATLAB's own figure format, which Python cannot write; PNG is the equivalent now that the tool is Python, and nothing is lost, because `timeVec` and `rBFi` sit in the results file in the same folder, so a real `.fig` can still be rebuilt in MATLAB from the same session. Confirmation is question 7 for the supervisor and the extension is one constant. Two things matter beyond "a file appears": it runs **after** `_finalize_normalization()`, so the figure shows the curve against the constant that was actually saved rather than the provisional one it was drawn with (that ordering is asserted, not assumed); and every failure is logged and swallowed — a missing PNG never justifies interfering with the close of a session whose HDF5 is already on disk. The export lives in `PlotWidget.save_png()`, rendered from the plot item so the Reset button stays out of the picture, at a fixed 1600 px so the file looks the same whether the window was maximised or tucked into a corner, and preceded by `render_now()` because the curve redraws on a 1 s timer and up to a second of points can be pending when a session ends. An empty plot writes nothing: a blank figure looks like a failed measurement. New `tests/test_figure_export.py` (9 tests, reading the PNG's IHDR chunk directly rather than pulling in an image library); five mutations checked — figure never saved at close, export failure no longer swallowed, empty plot writes a blank file, width taken from the window, buffered points not drawn first — each caught. Suite 319/319 fast, 4/4 slow. |
| 28 | Confirm the laser is off before a session is saved (E3) | Closes E3 / worklist task 11 (commit `cd4dbf3`). At the end of a measurement the operator is prompted — *"Measurement has ended. Please turn off the laser."* — and the app then checks that it actually went off: one fresh frame is captured and its mean ROI intensity must have fallen by ≥ 90 % against what the measurement was seeing. A session ruined by a laser left running is caught at the rig rather than during analysis weeks later. `_laser_off_check()` runs from the Stop branch of `_toggle_scos`, which both Stop SCOS and the duration auto-stop funnel through; it sits **before** `_set_state(FINISHED)`, so results from frames still in flight — all captured before the prompt — are not discarded, and before `_finish_session()` writes `rBFi`. **Both sides of the comparison are dark-subtracted**, mirroring `process()` exactly, and that is not a preference: the camera's black level (100 DU on this rig) does not go away when the laser does, so a comparison of raw DU could never fall by 90 % however completely the laser was switched off — the check would have warned on every run ever made. **The reference is the trailing mean over the last 5 s of results, not the final value.** `todo.md` E3 states that the last `mean_i` is "already available"; it is not — it was only ever a parameter of `_on_scos_result` and was never stored, hence `_intensity_history`, trimmed to the window and reset per run in `_finish_bright_cal` so a second Start SCOS cannot compare against the previous run's intensity. One frame's ROI mean is noisy, and a momentary shadow on the last frame would drag the reference low enough to let a laser that is still on pass. **Answering No to "Continue anyway?" re-runs the check and never discards the session** — the frames are already recorded, and Escape returns No too, which under this reading merely re-prompts where under a discard reading it would destroy a session. ROI-vs-whole-frame and trailing-mean-vs-last-value are questions 8 and 9 for the supervisor; both are answered here by a default and logged as such in `docs/open_questions.md`, each one constant away from changing (`_LASER_OFF_DROP_FRACTION`, `_LASER_OFF_REF_SECONDS`). Two things that would otherwise have looked like bugs at the rig: playback has no laser, so the check plays the `_dark` folder while it samples and restores `"main"` in the `finally` the way `_finish_dark_cal` does — without the restore, Stop SCOS leaves a mock-folder session parked on the dark folder and the next run's preview is black until Start SCOS is pressed again; and the whole step is wrapped in `try/except`, because a dialog or event-loop failure in the stop path would otherwise skip `_finish_session()` and cost the session its `rBFi`. For the same reason the outcome is **returned** and appended to the session-finished message rather than posted to the status bar, which `_stop_recorder()` and the closing message both overwrite — a failed check that reached only `app.log` is one the operator never saw. The dark-subtraction and ROI-mask shape guards `return None` into the "check skipped" path instead of falling through, since falling through would silently reintroduce the exact error the check exists to catch. New `tests/test_laser_off_check.py` (10 tests): the pass, the failure and its warning text, No-means-check-again, an empty reference, a camera that never delivers a frame, the playback restore, the ROI actually handed to the check by `_toggle_scos`, and a check that raises mid-stop still saving the session. Seven mutations checked 2026-09-28, each caught: dark subtraction removed so raw DU is compared (2 tests fail — a laser genuinely off no longer passes, and the warning quotes the wrong numbers); reference taken as the last value instead of the trailing mean; playback never restored to `"main"`; the `try/except` around the check removed, so a raising check costs the session its `rBFi`; No on "Continue anyway?" returning instead of re-checking; the per-run reset of `_intensity_history` removed, so run two compares against run one; and `laser_check_mask` captured after `_scos_mask` is cleared, which hands the check `None` and silently skips it. Suite 310/310 fast, 4/4 slow at the time of the commit. **Not yet exercised on real hardware:** the rig session of 2026-09-28 was stopped during `DARK_CAL` and never reached `MEASURING`, so the prompt has so far only ever been seen by the tests. |
| 29 | Recording name, and the opening dialogs in the protocol's order | Spotted by the user against `docs/SCOS_protocol.md`, and never in this backlog. The protocol reads G[DU/e] from the table, then *"Ask for recording name and location. Create appropriate folder"* (line 13), and only then the *"Please turn off the Laser"* pop-up (line 17); `docs/session_tab` says the same in other words — saving is arranged "at the very beginning of the session". The app asked for a location **after** the laser prompt and never asked for a name at all: the folder was always `scos_<timestamp>`, and no `QLineEdit` existed anywhere in `gui/`. Both are fixed. A **Recording name** field now sits at the top of the SCOS box and prefixes the session folder, `subject03_rest_20260928_164512`; the timestamp is never dropped, so repeating a name is harmless, and an empty field keeps the old `scos_<timestamp>` form. What is typed is repaired rather than rejected — spaces, slashes, colons and the rest of Windows' forbidden set become underscores, trailing dots and spaces go (they produce folders Windows cannot delete), and the prefix is capped at 64 characters — because an operator halfway through setting up a subject should not be stopped by a colon. The field locks with the other parameters at Start SCOS, since the folder is named there and a later edit would change nothing. **The order matters in the room, not only on paper:** everything done at the keyboard now happens before the lights go out, instead of leaving the operator standing in the dark hunting for a folder — and a Cancel there used to abort a run whose laser was already off. Because the folder is created before the prompt, cancelling it removes the folder again, via `rmdir`, which refuses to touch one that is not empty. The name is deliberately **not** persisted between launches (cf. B3): exposure carries over sensibly, a subject's name does not. New `tests/test_recording_name.py` (18 tests, including the dialog order asserted by recording which one opens first); six mutations checked — name ignored, timestamp dropped, name not sanitised, length cap removed, cancelled folder not cleaned up, field not locked — each caught. Suite 337/337 fast, 4/4 slow. |

---

## Map: frozen `merged_worklist.md` tasks → this file

| Worklist | Here | | Worklist | Here |
|---|---|---|---|---|
| 1 in-flight cap | A3 / Done 7 ✅ | | 14 recorder thread | **F4** |
| 2 silent swallows | Done 17 ✅ | | 15 disk space | B2 |
| 3 ROI race | Done 18 ✅ | | 16 raw frames | F1 |
| 4 overload test | Done 19 ✅ | | 17 plot downsampling | D1 |
| 5 intake off GUI thread | Done 20 ✅ | | 18 fill bar + dialog | B1 |
| 6 verify on real camera | **D5** | | 19 camera reconnect | **B4** |
| 7 pool-vs-thread write-up | **D7** | | 20 long sessions | F2 |
| 8 session folder + FINISHED | Done 21 ✅ | | 21 Arduino laser | F3 |
| 9 results schema | E2 ✅ | | 22 float64 check | **A4** |
| 10 short/long normalization | E1 / Done 23 ✅ | | 23 protocol typo | ✅ `7c89dbc` |
| 11 laser popup + 90 % | E3 / Done 28 ✅ | | 24 scos_math | C1 |
| 12 figure | E4 / Done 27 ✅ | | 25 tooling | D2 |
| 13 tag v0 | E5 | | | |
