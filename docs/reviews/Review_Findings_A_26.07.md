# SCOS Review — Scientific Correctness & Measurement Session

Review date: 2026-07-20. Read-only review; no project files were modified.
Backing evidence: full test suite run (`186 passed in 457.7 s`), processor
benchmark, and line-by-line reading of the math, session, recorder, pipeline,
and GUI code against `docs/SCOS_protocol.md`, the Session-tab requirements
captured in `docs/todo.md`, and `docs/Implementation_Plan.md`.

Jargon is defined the first time it appears.

---

## Assumptions (please correct any that are wrong)

1. **The binding "Session tab" spec is the one captured in `docs/todo.md`** (items
   E1–E5 and the file-schema table). There is no separate "SCOS GUI Project"
   file in the repo; `todo.md` quotes the supervisor's 2026-06-04 requirements
   and the MATLAB reference, so I treated it as the Session-tab source of truth.
2. **The MATLAB reference is authoritative for the math** (`corrSpeckleContrast`
   in `LocalStd7x7_corr.mat`). The offline tests compare against it.
3. **"κ²" (kappa-squared) = speckle contrast squared** = variance / mean² inside a
   small sliding window. **"BFi" = blood-flow index = 1/κ²**. **"rBFi" = relative
   BFi = BFi ÷ a baseline value** measured at the start. These are the quantities
   the lab actually wants.
4. The reference lab-data folder (`C:/Users/USER/Scos_Frames_and_Results/`) is
   present on this machine — the MATLAB-match tests ran (not skipped), confirmed by
   the gain warning they emitted.
5. "Version tagged 0" (E5) means a **git tag** milestone, not a data field — but
   Q5's reproducibility ask (a code version saved *inside every results file*) is a
   separate thing and is currently absent.
6. The target camera runs **Mono10, 24 dB gain**; the lab demo camera is 700×700
   Mono12. I did not assume the demo-plan cuts are acceptable — I treated them as
   gaps.

---

## A) Summary — most critical first

1. **The math is correct and matches MATLAB.** The full corrected κ² path
   (`processor.process()`) is validated end-to-end against the MATLAB reference at
   **< 2 % per-frame error (observed 0.6–1.2 %)** in `test_dark_cal_offline.py` and
   `test_bright_cal_offline.py`, both of which ran in my suite. The formula, the
   five noise terms (dark var, bright var, shot `G·⟨I⟩`, quantization `1/12`), the
   unbiased-variance correction, and the ROI mask-shrink all match the protocol.
   **This is the good news: the science core is sound.**

2. **The results file does not contain the scientific result.** The recorder saves
   **raw `bfi = 1/κ²`, never the normalized `rBFi`**, and uses field names
   (`time`, `k2_raw`, `k2_corr`, `bfi`, `mean_intensity`, `metadata`) that do **not**
   match the required Session-tab schema (`startTime`, `timeVec`, `rBFi`,
   `Intensity`, `Params`). The normalized signal — the thing the analysis needs —
   exists only in the live plot and is lost on exit. **(E2 gap, confirmed.)**

3. **There is no real end-of-session step, and that one gap blocks four others.**
   For a real (non-replay) measurement, both "Stop SCOS" and the auto-stop timer go
   to `PREVIEW`, never `FINISHED` (`main_window.py:685`). `FINISHED` is only ever
   reached by HDF5 replay. Because there is no end-of-session hook, **E1
   (short/long re-normalization), E3 (laser-off popup + intensity check), and E4
   (save figure) have nowhere to attach** — they are all absent. This is the single
   most important structural fix.

4. **Frames (and their timestamps) can be silently dropped — which corrupts the
   physiological/FFT analysis.** Two paths drop data with no error: the
   `_DropOldestQueue` evicts the oldest frame when full (`pipeline.py:50`), and the
   emitter swallows any processing exception (`except Exception: continue`,
   `pipeline.py:176`). Separately, the recorder **silently discards every point
   where κ²_corr ≤ 0** (`recorder.py:52`). Each hole makes the time axis uneven, and
   uneven sampling produces a **wrong heart rate** from an FFT ("FFT" = the math
   that finds the pulse frequency; it assumes evenly spaced samples). **(A3 gap +
   a new data-integrity bug.)**

5. **Timestamps are taken at the wrong place and from the wrong clock.** The time
   stored with each sample is `time.time() − start_time`, computed **on the GUI
   thread when the frame's signal is delivered** (`main_window.py:1108`), not when
   the camera captured it. That adds GUI-scheduling jitter; `time.time()` (wall
   clock) can also jump during a multi-hour recording if the OS syncs its clock.
   The camera thread already has the hardware capture instant and already uses the
   monotonic clock for display throttling — the fix pattern exists.

6. **Short-vs-long normalization (E1) is not implemented.** The MATLAB rule is:
   long recording (> 120 s) → divide by the **mean** of the first N seconds; short
   recording (≤ 120 s) → divide by the **5th percentile** of the first N seconds.
   The code always uses the mean; the "Pulsation lower level" option just logs a
   warning and falls back to mean (`main_window.py:1162`). N is correctly a GUI
   spinbox.

7. **Reproducibility and calibration-accuracy loose ends.** (a) No **git
   commit/version** is written into results (Q5). (b) The **normalization constant
   `_bfi_norm` is never saved**, so rBFi can't be reconstructed after a crash.
   (c) The production **24 dB gain is extrapolated ~4 dB beyond the calibration
   table** (CSV has 16/18/20 dB only) — the `UserWarning` fires in the test output;
   this scales the shot-noise term. It matches MATLAB (same extrapolation law) but
   is a real accuracy risk if the camera's gain is non-linear there.

8. **Long-session risk is real but not "memory growth."** The recorder streams to
   disk and the in-RAM plot lists are only ~5 MB over 4 h. The actual risks are
   (a) the plot re-drawing ~288 k points every second with **no downsampling**
   (D1), which will lag, and (b) the timestamp evenness from #4/#5. Performance is
   otherwise fine: the benchmark shows **7.8 ms mean / 11.5 ms p99 per frame** at
   700×700, well inside a 20–50 Hz budget.

---

## B) Concrete suggested changes

Format: **What → Why (plain language) → Risk if skipped.**

### B1. Add a genuine end-of-session transition (FINISHED)
- **What:** On Stop SCOS and on auto-stop, go `MEASURING → FINISHED`, run the
  end-of-session work (re-normalize, laser check, write results, save figure),
  then return to `PREVIEW`. Files: `gui/main_window.py` (`_toggle_scos`,
  `_on_scos_result` auto-stop branch, a new `_on_finished`).
- **Why:** Right now the app just flips back to preview and closes the recorder.
  There is no single place to finalize a measurement, which is why three required
  features are missing.
- **Risk if skipped:** E1/E3/E4 cannot be implemented cleanly; every "end of
  measurement" behavior stays bolted on ad hoc.

### B2. Save the real result with the required schema (E2)
- **What:** Write `rBFi_results.h5` containing `startTime`, `timeVec`, `rBFi`,
  `Intensity`, and a `Params` group. Store the **normalized** rBFi (after E1), plus
  the normalization constant and the git commit hash inside `Params`. Also emit the
  separate `DarkCalibration.h5` / `BrightCalibration.h5` and the figure. Files:
  `core/recorder.py`, `gui/main_window.py`.
- **Why:** The file is the deliverable of a session. Today it lacks the normalized
  signal, the start time, and the parameter struct the downstream analysis expects.
- **Risk if skipped:** Recorded sessions are not analyzable in the lab's pipeline
  without manual reconstruction; some sessions are unrecoverable.

### B3. Stop silently dropping data; store invalid points as NaN
- **What:** In `recorder.append`, keep the row when κ²_corr ≤ 0 but store the value
  as `NaN` ("not a number", a marker for "missing") so `timeVec` stays evenly
  spaced. In the pipeline emitter, log-and-count exceptions instead of silently
  `continue`. Files: `core/recorder.py`, `core/pipeline.py`.
- **Why:** The heart-rate/FFT analysis needs one sample per frame at a steady rate.
  A dropped row is an invisible gap that shifts every later timestamp.
- **Risk if skipped:** Wrong pulse frequency with no warning — a scientifically
  invalid result that looks fine.

### B4. Timestamp at capture, on the monotonic clock
- **What:** Capture the timestamp in the **camera thread** at grab time (Pylon's
  hardware `GetTimeStamp()` if available, else `time.monotonic()`), and pass it
  through with the frame instead of computing `time.time()` on the GUI thread.
  Files: `camera.py`, `core/pipeline.py`, `gui/main_window.py`.
- **Why:** The current time reflects when Qt got around to delivering the frame,
  plus a wall clock that can jump. Both distort the sample spacing the FFT relies
  on.
- **Risk if skipped:** Jittery / jumpy time axis; degraded or wrong physiological
  readouts, especially under load or over long recordings.

### B5. Implement E1 short/long normalization at FINISHED
- **What:** Keep the live plot using the mean (can't know duration until the end).
  At FINISHED, if measuring time ≤ 120 s, recompute the baseline as the **5th
  percentile** of the first-N-seconds BFi, rescale the plotted data, and save the
  corrected rBFi. Define clearly whether "duration" is total or post-normalization.
  Files: `gui/main_window.py`.
- **Why:** For short clinical clips the 5th-percentile baseline is the lab's chosen
  method (it picks the "lower level" of the pulse instead of its average).
- **Risk if skipped:** Short recordings are normalized by the wrong constant, so
  rBFi is systematically off for exactly the short-clip use case.

### B6. End-of-session laser popup + intensity-drop check (E3)
- **What:** At FINISHED, show *"Measurement has ended. Please turn off the laser,"*
  then capture one frame and confirm mean ROI intensity dropped ≥ 90 % vs the last
  measurement intensity; warn if not. Files: `gui/main_window.py`.
- **Why:** A safety/data-quality gate: it catches "laser left on" and confirms the
  session really ended in the dark.
- **Risk if skipped:** Operator-safety gap and no automatic check that the session
  closed correctly.

### B7. Ask for the session folder once, at the start (protocol §0)
- **What:** Choose one output folder at Start SCOS and put calibration + results +
  figure under it. Remove the second `QFileDialog` inside `_start_recorder`. Files:
  `gui/main_window.py`.
- **Why:** Two dialogs today (`_start_dark_cal` and `_start_recorder`) can point at
  different folders, splitting a session's data. The second dialog also pops up
  **mid-measurement** right after `_start_time` is set, which risks eating into the
  normalization window because the modal dialog interleaves with frame processing.
- **Risk if skipped:** Split/lost session data; fragile normalization timing.

### B8. Replace `_DropOldestQueue` — but move intake off the GUI thread (A3)
- **What:** Replace the drop-oldest queue with a bounded blocking queue **and** feed
  it from the camera thread, not from `_on_scos_frame` on the GUI thread. Add the
  80 %-full `overload_detected` signal. Files: `core/pipeline.py`, `camera.py`,
  `gui/main_window.py`.
- **Why (important nuance):** A simple `queue.Queue` swap is **not** safe here:
  `submit()` runs on the GUI thread, so a full blocking queue would freeze the UI
  (which also stops draining results — a deadlock-like stall). "Never drop
  silently" therefore requires the small architectural change of moving frame
  intake into the camera/worker thread.
- **Risk if skipped:** Under load you either drop silently (wrong data) or freeze
  the GUI — there is no safe middle state today.

### B9. Save a code version with every result (Q5 reproducibility)
- **What:** Read the current git commit (`git rev-parse HEAD`) at session start and
  store it in `Params`. Files: `gui/main_window.py`, `core/recorder.py`.
- **Why:** Six months later you must be able to say exactly which code produced a
  result. The v0 **tag** (E5) is a release milestone; this per-file stamp is
  different and independent.
- **Risk if skipped:** Results can't be tied to the code that made them.

### B10. Enable plot downsampling for long sessions (D1)
- **What:** `self.curve.setDownsampling(auto=True, mode='peak')` and
  `setClipToView(True)`. Files: `gui/plot_widget.py`.
- **Why:** Redrawing hundreds of thousands of points every second is what actually
  makes long recordings lag.
- **Risk if skipped:** GUI slows to a crawl in hour 2–3 of a recording.

### B11 (docs). Fix the protocol typo — bright cal says "turn off"
- **What:** `docs/SCOS_protocol.md:27` says *"Please turn off the Laser"* for the
  **bright** calibration; it should say **on**. The code and plan are already
  correct. Fix the doc.
- **Why:** The protocol is the source of truth; a copy-paste error there could
  mislead a future reader. (This is the one place "protocol wins" should **not**
  flip the code — the code is right.)
- **Risk if skipped:** Confusion; someone could "fix" correct code to match a wrong
  doc.

### B12 (note). float32 vs the float64 mandate — measure, then decide
- **What:** `local_variance` and `process()` compute in **float32**
  (`processor.py:155, 434`), which contradicts the explicit "use float64 for all
  SCOS intermediates" rule (CLAUDE.md §Code Style; Plan §10.6). The risky spot is
  the corrected numerator `var_raw − var_dark − var_bright − G·⟨I⟩ − 1/12` when
  κ²_corr is small (high blood flow), where near-equal numbers subtract.
- **Why:** Right now the offline tests pass at 0.6–1.2 % against MATLAB, so it is
  **not** currently a demonstrated accuracy bug — but it violates a written
  non-negotiable rule and the reference data may not cover the worst (low-κ²) case.
- **Risk if skipped:** Possible hidden precision loss in high-flow regimes; and a
  documented rule is silently broken. Recommendation: add a float64 variant of the
  corrected-numerator step (or a test at small κ²) and keep whichever the data
  supports — don't "fix" blindly.

---

## C) Ordered task plan (measurement / session side)

Ordering respects the Session-tab sequence (results + end-of-session before
raw-frame saving, before long sessions, before automatic laser control) and puts
data-integrity correctness first. "Done" always includes **updating the relevant
docs** (`docs/todo.md`, `docs/Implementation_Plan.md`, and `SCOS_protocol.md` where
noted).

### Phase 0 — Data integrity (do first; protects every recording)

**T1. Timestamp at capture, monotonic clock.**
- Goal: each sample carries the camera capture time, not the GUI delivery time.
- Files: `camera.py`, `core/pipeline.py`, `gui/main_window.py`.
- Done when: timestamps come from the camera thread; a mock-folder replay shows
  evenly spaced `timeVec`; docs updated.

**T2. Stop silent drops; mark invalid κ² as NaN.**
- Goal: no hidden gaps in the time axis.
- Files: `core/recorder.py` (keep κ²≤0 rows as NaN), `core/pipeline.py` (log+count
  exceptions instead of `continue`).
- Done when: a test feeds a κ²≤0 point and the saved `timeVec` still has one row per
  frame; dropped/errored counts are logged; docs updated.

**T3. Replace drop-oldest queue, intake off the GUI thread (A3).**
- Goal: never drop silently and never freeze the GUI.
- Files: `core/pipeline.py`, `camera.py`, `gui/main_window.py`.
- Done when: `_DropOldestQueue` is gone; a sustained-overload test shows blocking +
  an `overload_detected` signal, GUI stays responsive; docs updated.

### Phase 1 — End-of-session spine (unblocks E1/E2/E3/E4)

**T4. Real FINISHED transition + single session folder (B1, B7).**
- Goal: one end-of-session hook; one folder chosen at Start SCOS.
- Files: `gui/main_window.py`.
- Done when: Stop and auto-stop both reach `FINISHED` then `PREVIEW`; calibration,
  results, and figure land in the same folder; docs updated.

**T5. Results schema per Session tab (E2, B2, B9).**
- Goal: `rBFi_results.h5` = `startTime`, `timeVec`, `rBFi`, `Intensity`, `Params`
  (incl. normalization constant + git hash); separate calibration `.h5` files.
- Files: `core/recorder.py`, `gui/main_window.py`.
- Done when: a completed mock session produces a file with exactly those keys and a
  reloadable `Params` group; a round-trip test asserts them; docs updated.

**T6. Short/long normalization (E1, B5).**
- Goal: ≤ 120 s → 5th-percentile baseline; > 120 s → mean; rescale + save corrected
  rBFi; switch x-axis to minutes when long.
- Files: `gui/main_window.py`, `gui/plot_widget.py`.
- Done when: two mock sessions (one short, one long) save the correct baseline type;
  "duration" basis is documented; docs updated.

**T7. End-of-session laser popup + 90 % intensity check (E3, B6).**
- Goal: prompt to turn the laser off, then verify intensity dropped ≥ 90 %.
- Files: `gui/main_window.py`.
- Done when: the popup appears at FINISHED, the check passes on a real drop and
  warns otherwise; docs updated.

**T8. Save the figure at end (E4).**
- Goal: write `rBFi_fig.png` to the session folder and show its path.
- Files: `gui/main_window.py`.
- Done when: a completed session leaves a PNG next to the results file; docs
  updated.

**T9. Tag v0 (E5).**
- Goal: annotated git tag after T4–T8 and all tests pass.
- Done when: `git tag -a v0 …` exists; docs updated.

### Phase 2 — Robustness for long/real sessions

**T10. Plot downsampling (D1, B10).** — `gui/plot_widget.py`; done when a 200k-point
replay stays smooth.

**T11. Queue-fill indicator + overload dialog (B1/tier-B).** — status bar bar +
dropped-frame counter + 4-option dialog; done when overload is visible and
actionable.

**T12. Disk-space check (B2/tier-B).** — refuse to start below threshold, stop
gracefully when low; done when both paths are exercised.

**T13. float64 numerator check (B12).** — add a small-κ² test / float64 variant of
the corrected-numerator step; keep whatever the MATLAB data supports; docs updated.

### Phase 3 — Later phases (Session-tab order)

**T14. Save-raw-frames option (F1)** — wire `chk_save_frames` to
`recorder.append_frame`; requires T12 first.
**T15. Long-session hardening (F2)** — pre-allocated result arrays, verified
multi-hour run.
**T16. Automatic laser control (F3)** — drive the laser enable pin (pin 13) from the
Arduino, replacing the manual popups.

---

## What I verified vs. assumed

- **Verified by running:** 186/186 tests pass; the MATLAB end-to-end match runs at
  < 2 % (both dark and bright offline tests); processor benchmark 7.8 ms mean /
  11.5 ms p99 at 700×700.
- **Verified by reading:** every code claim above cites a file/line.
- **Assumed:** the `todo.md` E-items equal the binding Session tab (Assumption 1);
  the reference-data folder is the correct MATLAB ground truth (Assumption 2/4).
