# SCOS — Merged Worklist (Review A + Review B)

Merged: 2026-07-26. This document **only reorganizes** the two existing reviews
(`Review_Findings_A.md` — scientific correctness & measurement session;
`Review_Findings_B.md` — pipeline, timing & reliability) into one execution order.
No project files were changed to produce it.

**Note on filenames:** the request named `review_A_science_session.md` and
`review_B_pipeline.md`. The repo actually contains `Review_Findings_A.md` and
`Review_Findings_B.md`. Their contents match the described scopes, so those are the
two documents merged here.

**How to read it:** the list is ordered so that nothing appears before the thing it
needs. Within that, measurement accuracy comes before convenience. Every task that
changes behavior includes "docs updated" in its *Done when*.

### Words used more than once (defined here, once)

| Term | Plain meaning |
|---|---|
| **κ² (kappa-squared)** | Speckle contrast squared = variance ÷ mean², measured in a small sliding window over the image. The core measured quantity. |
| **BFi / rBFi** | BFi = blood-flow index = 1/κ². rBFi = "relative" BFi = BFi divided by a baseline value from the start of the recording. rBFi is what the lab analyses. |
| **Backpressure** | When a slow stage makes the stage feeding it *wait*, instead of letting unprocessed work pile up in memory. Waiting is safe; piling up is not. |
| **In-flight cap** | A counter (a "semaphore") that limits how many frames may be inside the processing stage at once. Without it, a queue can be "bounded" on paper while work piles up invisibly behind it. |
| **NaN** | "Not a Number" — a numeric marker meaning *missing value*. Keeps a row in place instead of deleting it. |
| **FFT** | The math that finds the pulse frequency from the signal. It assumes samples are **evenly spaced in time** — every deleted or late sample corrupts it. |
| **Monotonic clock** | A clock that only ever counts forward (`time.monotonic()`). The wall clock (`time.time()`) can jump if the OS syncs time mid-recording. |
| **Race condition** | Two threads touching the same data at the same time, where the result depends on who wins. Produces rare, silent, wrong answers. |
| **gzip** | Compression. Cheap on disk, expensive in CPU time — bad on the GUI thread. |
| **HDF5 (`.h5`)** | The file format the session results are saved in. |

---

## Phase 0 — Data integrity (nothing else is trustworthy until these land)

Ordering rule applied here: your constraint #3 — a wrong result from a dropped frame
is worse than a slow but correct one, so the buffering fix precedes all performance work.

---

### 1. Cap in-flight work and make dropped frames visible

- **Goal:** A processing slowdown can no longer grow memory without limit, and any
  frame that *is* dropped is visible to the operator instead of silent.
- **Source:** Review B (B1, T1). Review A raises the same symptom from the data side (A4).
- **Depends on:** none — start here.
- **Files:** `core/pipeline.py`, `gui/main_window.py`.
- **Done when:** an overload run keeps memory flat; the dropped-frame count shows in the
  status bar **and** `app.log`; the offline MATLAB tests (`test_dark_cal_offline.py`,
  `test_bright_cal_offline.py`) still pass under 2 %; docs updated — including rewriting
  `todo.md` A3, whose current prescription is wrong (see Conflict C2).

*Why this is first:* today `_input_q` is capped at 20 and reports zero drops, while the
ThreadPoolExecutor's internal queue and the `_inflight` deque behind it have **no limit at
all** (`core/pipeline.py:114-151`). Review B reproduced ~23 MB parked and growing after 5
seconds of 33 % overload, with the drop counter reading zero. Over hours that is an
out-of-memory crash that loses the whole session.

---

### 2. Stop the silent swallows (errors and κ² ≤ 0 rows)

- **Goal:** No hidden holes in the time axis and no hidden crashes.
- **Source:** both (A: B3/T2; B: B2/T2 — same fix, described identically).
- **Depends on:** none (independent of #1; different lines of the same file).
- **Files:** `core/pipeline.py` (the emitter's `except Exception: continue`, line 176 —
  log and count instead), `core/recorder.py` (line 52 — keep the row and store `NaN`
  instead of `return`).
- **Done when:** a test feeds one κ² ≤ 0 point and one frame that raises, and both are
  preserved/counted rather than vanishing; `timeVec` still has one row per frame;
  offline MATLAB tests still pass; docs updated.

*Confirmed against the code:* `recorder.append()` really does `if k2_corr <= 0: return`,
silently deleting the row — which shifts every later timestamp and corrupts the FFT.
Note the live plot already skips non-finite values (`gui/plot_widget.py:56`), so writing
NaN will not break plotting. See Question Q3 about whether the downstream analysis
tolerates NaN.

---

### 3. Fix the ROI data race (locked or atomic)

- **Goal:** Dragging the ROI circle during a measurement can no longer corrupt or vanish
  a data point.
- **Source:** Review B only (B5, T6). Review A did not find this.
- **Depends on:** none — but do it *after* #2, so if any crash remains it appears in the
  log instead of disappearing.
- **Files:** `gui/image_widget.py` (disable `CircleROI` dragging while measuring),
  `gui/main_window.py`, `processor.py` (`set_roi` should build one new bundle and assign
  one reference).
- **Done when:** a test drags the ROI during `MEASURING` and no worker crashes and no
  point is lost; offline MATLAB tests still pass; docs updated.

*Confirmed against the code:* `processor.set_roi()` reassigns `_roi`, `_mask_crop`,
`_dm_f32`, `_dv_f32`, `_bv_f32` one at a time (`processor.py:270+`) while up to 3 worker
threads read them, and nothing in `gui/image_widget.py` ever locks the circle. Placed in
Phase 0 because it can silently produce a **wrong κ²**, which the accuracy rule outranks
everything else.

---

### 4. Add a real overload test

- **Goal:** Remove the false confidence in the current tests, so #1 can't silently regress.
- **Source:** Review B (B7, T3).
- **Depends on:** 1, 2.
- **Files:** `tests/test_pipeline.py`.
- **Done when:** a test submits frames faster than the workers drain and asserts that
  in-flight work **and** memory stay bounded while drops are counted; docs updated.

*Why:* today `test_pipeline.py` only checks `dropped_count` on the isolated queue — the one
buffer that never fills in practice — so the actual failure mode is untested.

---

### 5. Move frame intake off the GUI thread — and timestamp at capture, in the same change

- **Goal:** Restore real backpressure to the camera (the documented
  `Implementation_Plan.md §2` design), and at the same time attach each frame's timestamp
  where the frame is *captured*, on the monotonic clock, instead of when the GUI thread
  happens to handle it.
- **Source:** both, merged — B's T4 (intake off GUI) + A's T1 / B's T10 (capture-time
  timestamps). See Conflict C1 for why these are bundled.
- **Depends on:** 1, 2, 4.
- **Files:** `camera.py`, `mock_camera.py`, `folder_camera.py`, `h5_replay.py`,
  `core/pipeline.py`, `gui/main_window.py`, and the tests that assert the signal shape
  (`tests/test_camera.py`, `test_mock_camera.py`, `test_folder_camera.py`,
  `test_display_throttle.py`).
- **Done when:** a sustained-overload run blocks rather than dropping silently, raises
  `overload_detected` at ~80 % full, keeps the GUI responsive and RAM flat; a mock-folder
  replay produces evenly spaced `timeVec` taken from capture time; offline MATLAB tests
  still pass; docs updated (`Implementation_Plan.md` diagram, `todo.md` A3, and the stale
  "processing runs on the GUI thread" line in `CLAUDE.md`).

*Inferred dependency, stated so you can check it:* both changes rewrite the same handoff —
`frame_ready` is emitted by **four** frame sources (`camera.py:223`, `mock_camera.py:113`,
`folder_camera.py:183`, `h5_replay.py`) and consumed by `_on_scos_frame`
(`gui/main_window.py:1108`). Doing them separately means changing that signature and its
four tests twice.

---

### 6. Verify #1 and #5 on the real camera

- **Goal:** Confirm the backpressure and timestamp changes behave on real hardware, not
  just mocks.
- **Source:** neither review — **gap I added** (see Gap G3).
- **Depends on:** 5.
- **Files:** none (procedure + a note in `docs/todo.md`, alongside the existing D5 entry).
- **Done when:** the app runs on the real rig at normal FPS with no regression; a
  deliberate overload shows blocking + `overload_detected` + a counted, logged drop rather
  than a freeze; capture timestamps look evenly spaced in the saved file; docs updated.

*Why this must exist:* the mock cameras bypass `camera.py`'s grab loop entirely — this is
exactly why `todo.md` D5 exists for the `GrabStrategy_OneByOne` change. The intake refactor
needs the same treatment.

---

### 7. Write down the pool-vs-single-thread decision

- **Goal:** Record whether the 3-worker pool stays, with the timing numbers behind the call.
- **Source:** Review B (B4, T5).
- **Depends on:** 5, 6.
- **Files:** `docs/realtime_architecture.md` (new; this is `todo.md` D4).
- **Done when:** the choice and its measurements are written down; docs updated.
- **Recommendation:** keep the pool **with** the in-flight cap from #1 — see Conflict C3;
  going single-thread would remove the shipped `spn_workers` operator control.

---

## Phase 1 — End-of-session spine (this is what unblocks the whole Session tab)

---

### 8. One session folder at the start, and a real FINISHED transition

- **Goal:** One place where a measurement is finalized, and one folder holding everything
  from that session.
- **Source:** Review A (B1 + B7, T4). Review B independently flags the mid-measurement
  folder dialog as a GUI stall that grows an unbounded buffer.
- **Depends on:** none technically — but sequenced after Phase 0, and it edits
  `gui/main_window.py`, which #5 also edits, so land #5 first to avoid conflicts.
- **Files:** `gui/main_window.py`.
- **Done when:** both Stop SCOS and the auto-stop timer reach `FINISHED` and then return
  to `PREVIEW`; the second `QFileDialog` inside `_start_recorder` is gone; calibration,
  results and figure all land in the folder chosen at Start SCOS; docs updated.

*Confirmed against the code:* Stop SCOS goes straight to `State.PREVIEW`
(`gui/main_window.py:685`) and `FINISHED` is only ever reached by HDF5 replay
(`:589`). There are two separate folder dialogs (`:688` and `:756`), and the second one
pops up *mid-measurement*. This single gap is why tasks 9–12 have nowhere to attach.

---

### 9. Save the real result, in the required schema, with provenance

- **Goal:** `rBFi_results.h5` contains `startTime`, `timeVec`, `rBFi`, `Intensity` and a
  `Params` group — including the normalization constant and the git commit hash — plus
  the separate `DarkCalibration.h5` / `BrightCalibration.h5`.
- **Source:** Review A (B2 + B9, T5 = `todo.md` E2 + Q5).
- **Depends on:** 8 (needs the FINISHED hook), 2 (NaN rows), 5 (capture timestamps feed
  `timeVec`).
- **Files:** `core/recorder.py`, `gui/main_window.py`.
- **Done when:** a completed mock session produces a file with exactly those keys and a
  reloadable `Params` group; **raw BFi is buffered during the session and `rBFi` is written
  once at close**, so no already-flushed data ever needs rewriting; a round-trip test
  asserts the keys; the normalization constant and git hash are present; offline MATLAB
  tests still pass; docs updated.

*The "write `rBFi` at close" part is a design decision, not something either review states —
see Gap G4 and Question Q4. It is what keeps #9 and #10 from depending on each other: #9
builds the close-time write path, #10 supplies the number that path writes.*

*Confirmed against the code:* the recorder writes `time`, `k2_raw`, `k2_corr`, `bfi`,
`mean_intensity` (`core/recorder.py:44-48`) — raw BFi, never the normalized rBFi. The
normalized signal exists only in the live plot and is lost on exit. See Question Q2 about
whether the in-file calibration copy stays.

---

### 10. Short-vs-long normalization (E1)

- **Goal:** At FINISHED, recordings ≤ 120 s use the **5th percentile** of the first N
  seconds as baseline; longer ones keep the **mean**. Rescale what was plotted and save
  the corrected rBFi.
- **Source:** Review A (B5, T6 = `todo.md` E1).
- **Depends on:** 8 (the FINISHED hook), 9 — #9 builds the close-time write path, #10
  supplies the constant that path writes. Nothing is written twice.
- **Files:** `gui/main_window.py`, `core/recorder.py`, `gui/plot_widget.py`.
- **Done when:** two mock sessions — one short, one long — save the correct baseline type;
  the meaning of "duration" (total vs. post-normalization) is written down; docs updated.

*Confirmed against the code:* the "Pulsation lower level" option currently just logs a
warning and falls back to the mean (`gui/main_window.py:1162-1168`). Note the plot x-axis
is **already always in minutes** (`gui/plot_widget.py:58`, axis label `min`) — see Question
Q1 and Gap G4.

---

### 11. End-of-session laser popup + 90 % intensity-drop check (E3)

- **Goal:** Prompt *"Measurement has ended. Please turn off the laser,"* then capture one
  frame and confirm mean ROI intensity dropped by ≥ 90 %; warn if it did not.
- **Source:** Review A (B6, T7 = `todo.md` E3). Review B agrees the manual popup is the
  right home for now.
- **Depends on:** 8.
- **Files:** `gui/main_window.py`.
- **Done when:** the popup appears at FINISHED; the check passes on a real drop and warns
  otherwise; docs updated.

*Your Session-tab constraint puts this and #9 before any raw-frame work — honoured here.*

---

### 12. Save the plot figure at end of session (E4)

- **Goal:** Write `rBFi_fig.png` into the session folder and show the path.
- **Source:** Review A (T8 = `todo.md` E4).
- **Depends on:** 8, 10 (the figure should show the *final*, re-normalized curve).
- **Files:** `gui/main_window.py`.
- **Done when:** a completed session leaves a PNG next to the results file, showing the
  corrected rBFi; docs updated.

---

### 13. Tag version 0 (E5)

- **Goal:** An annotated git tag marking the release milestone.
- **Source:** Review A (T9 = `todo.md` E5).
- **Depends on:** 8, 9, 10, 11, 12, and a full passing test suite.
- **Files:** none (git tag).
- **Done when:** `git tag -a v0 …` exists and docs updated. *(This is a milestone tag, not
  a data field — separate from the per-file git hash in #9. Nothing here creates commits
  or tags on its own; that stays your call.)*

---

## Phase 2 — Recording, long sessions, unattended runs

---

### 14. Move HDF5 recording into its own thread with a bounded queue

- **Goal:** A slow disk becomes backpressure instead of a GUI freeze; gzip compression
  stops running on the GUI thread.
- **Source:** Review B (B6, T7).
- **Depends on:** 5 (the pipeline shape it plugs into), 9 (settle the file schema first —
  otherwise `recorder.py` gets rewritten twice).
- **Files:** `core/recorder.py`, `core/pipeline.py`, `gui/main_window.py`.
- **Done when:** Save Frames enabled does not stall the GUI; the record queue is bounded;
  test passes; offline MATLAB tests still pass; docs updated.

*Confirmed against the code:* `recorder.append()` and the gzip-compressing
`recorder.append_frame()` both run synchronously on the GUI thread
(`gui/main_window.py:1113-1114, 1182-1183`).

---

### 15. Disk-space check (B2)

- **Goal:** Refuse to start a recording below a free-space threshold; stop gracefully if
  space runs low mid-session.
- **Source:** Review A (T12 = `todo.md` B2).
- **Depends on:** 8 (the folder must be chosen at the start to check the right drive).
- **Files:** `gui/main_window.py`, possibly `core/recorder.py`.
- **Done when:** both paths are exercised — refusal at start and graceful stop mid-session;
  remaining space shows in the status bar; docs updated.

*Verified: no disk-space check exists anywhere in the code today.*

---

### 16. Finish the raw-frame saving feature (F1)

- **Goal:** Saving every raw frame is safe to switch on — guarded by free space and no
  longer blocking the GUI.
- **Source:** Review A (T14) — **but corrected**, see Stale Claim S1.
- **Depends on:** 14, 15.
- **Files:** `gui/main_window.py`, `core/recorder.py`.
- **Done when:** a long Save-Frames session runs without GUI stalls, refuses to start
  without space, and stops cleanly when space runs out; docs updated (including
  `todo.md` F1, whose text is out of date).

*Correction:* both `todo.md:308` and Review A's T14 say this "just needs to be wired into
the GUI via the existing `chk_save_frames` checkbox." **It is already wired** —
`gui/main_window.py:1113-1114`. The genuine remaining work is the disk guard (#15) and
getting gzip off the GUI thread (#14).

---

### 17. Plot downsampling for long recordings (D1)

- **Goal:** Keep the plot fast when a session reaches 200 k+ points.
- **Source:** both (A: B10/T10; B: B8/T8 — identical one-line fix).
- **Depends on:** none technically; sequenced here because it is a long-session concern,
  and because your constraint #3 puts the backpressure fix (#1, #5) ahead of any
  performance work.
- **Files:** `gui/plot_widget.py` (`setDownsampling(auto=True, mode='peak')` +
  `setClipToView(True)`).
- **Done when:** a 200 k-point replay stays smooth; docs updated.

---

### 18. Queue-fill indicator + overload dialog (B1)

- **Goal:** The operator can see the pipeline filling up and choose what to give up
  before the session is ruined.
- **Source:** Review A (T11 = `todo.md` B1). Builds on the counter surfaced in #1.
- **Depends on:** 1, 5.
- **Files:** `gui/main_window.py`.
- **Done when:** a fill bar and dropped-frame counter appear; at ≥ 80 % the 4-option
  dialog appears with its timeout default, and the decision is logged; docs updated.

---

### 19. Automatic camera reconnect

- **Goal:** A camera dropout during an unattended multi-hour run recovers without the
  operator clicking Start Video again.
- **Source:** Review B (B9, T9).
- **Depends on:** 5 (it must reconnect into the new intake path).
- **Files:** `camera.py`, `gui/main_window.py`.
- **Done when:** a simulated disconnect auto-recovers and the gap is logged and marked in
  the data; docs updated.

*Confirmed against the code:* `_on_camera_error` currently just un-checks Start Video and
asks the user to click again (`gui/main_window.py:1207-1216`).

---

### 20. Long-session hardening (F2)

- **Goal:** Multi-hour recordings run without memory growth or GUI lag.
- **Source:** Review A (T15 = `todo.md` F2).
- **Depends on:** 14, 15, 17, 18, 19 — `todo.md` F2 itself lists B1 and D1 as prerequisites.
- **Files:** `gui/main_window.py`, `gui/plot_widget.py`, `core/recorder.py`.
- **Done when:** a verified multi-hour run completes with flat memory and a responsive GUI;
  docs updated.

---

### 21. Automatic laser control via Arduino (F3)

- **Goal:** The app drives the laser enable pin instead of asking the operator via popups.
- **Source:** both (A: T16; B: §4 agrees `todo.md` F3 is the right home).
- **Depends on:** 8, 11 (it replaces the manual popups, so those must exist and be correct
  first), 20.
- **Files:** `arduino_uploader.py`, `camera.py`, `gui/main_window.py`.
- **Done when:** the dark → bright → measure sequence runs with no manual laser prompts,
  with a manual fallback retained; docs updated.

*Last, per your Session-tab constraint: results + intensity check → raw frames → long
sessions → automatic laser control.*

---

## Phase 3 — Written-rule cleanup and tooling (after correctness)

---

### 22. float64 check on the corrected numerator

- **Goal:** Decide, with data, whether the float32 computation loses precision when κ² is
  small (high blood flow).
- **Source:** Review A (B12, T13).
- **Depends on:** none.
- **Files:** `processor.py`, a new small-κ² test.
- **Done when:** a small-κ² test exists and whichever precision the MATLAB data supports is
  kept, with the reasoning recorded; docs updated (`CLAUDE.md` §Code Style either holds or
  is amended with a measured exception).

*Review A is explicit that this is **not** a demonstrated accuracy bug — the offline tests
pass at 0.6–1.2 %. It is a written non-negotiable rule (`CLAUDE.md`: float64 for all SCOS
intermediates) that the code breaks. I checked A's line cites directly and they hold:
`processor.py:155` and `:434` cast to float32, and the corrected numerator itself stays
float32 (`:453-458`). Measure, then decide. If the small-κ² test fails, promote this to
Phase 0 immediately.*

---

### 23. Fix the protocol typo (bright calibration says "turn off")

- **Goal:** `docs/SCOS_protocol.md:27` should say turn the laser **on** for bright
  calibration.
- **Source:** both (A: B11; B: §4 cross-references it).
- **Depends on:** none — a 2-minute docs edit, do it whenever.
- **Files:** `docs/SCOS_protocol.md`.
- **Done when:** the line reads "on"; the code is **not** changed — the code is already
  correct here, and this is the one place where "the protocol wins" must not flip the code.

---

### 24. Extract pure math into `core/scos_math.py` (C1)

- **Goal:** The scientific core lives in `core/` with no camera/GUI imports.
- **Source:** neither review proposes it as a task; both note the boundary is unclean
  (B §5). It is `todo.md` C1.
- **Depends on:** 22 (don't refactor the math while its precision is still under review),
  and all of Phase 0/1 — `todo.md` is explicit: don't refactor code that still has
  correctness bugs.
- **Files:** new `core/scos_math.py`, `processor.py`, `core/session.py`,
  new `tests/test_scos_math.py`.
- **Done when:** the offline MATLAB reference test passes against the extracted functions
  at < 2 %; docs updated.
- **Included because** #22 and the Phase 0 fixes would otherwise be refactored twice.
  **Drop this task if you want a strict two-review merge** — it comes from `todo.md`, not
  from either review's plan.

---

### 25. Project tooling lock-in

- **Goal:** Reproducible installs and automatic static checks.
- **Source:** Review B (T11 = `todo.md` D2/D3).
- **Depends on:** none — but explicitly last, per your accuracy-over-tidiness constraint.
- **Files:** `pyproject.toml`, a lockfile, `ruff` + `mypy` config, `.github/workflows/ci.yml`.
- **Done when:** `ruff check` and `mypy core/` run clean in the pre-commit hook and in CI;
  docs updated.

---

# Conflicts between the two reviews

### C1. *When do capture-time timestamps happen?* — A says first, B says nearly last

- **Review A:** Phase 0, task T1 — the very first thing to do.
- **Review B:** Phase 4, task T10 — after reconnect, near the end.
- **Neither argues against the other**; both describe the identical fix.
- **Recommendation — bundle it into task #5 (intake off the GUI thread), not before it:**
  both changes rewrite the same `frame_ready` handoff across four frame sources and four
  test files, so doing them separately means paying that cost twice. This still lands well
  before any session or convenience work, so accuracy-first is preserved.

### C2. *How to fix the queue* — the repo's own plan is insufficient

- **`todo.md` A3 / `Plan_RealTime_Patches.md` 1.1 (and Review A's T3 wording):** replace
  `_DropOldestQueue` with a blocking `queue.Queue(20)`.
- **Review B:** that alone does **not** work — the dispatcher empties `_input_q` faster
  than the camera fills it, straight into the executor's *unbounded* internal queue. Cap
  in-flight work instead.
- **Recommendation — Review B is right, and I confirmed the structure it describes**
  (`core/pipeline.py:141-151`: the dispatcher submits into the pool with no cap and appends
  to an unbounded `_inflight` deque). Note Review A reaches a compatible conclusion from a
  different angle (a blocking `put()` on the GUI thread would freeze the UI). Both agree
  intake must move off the GUI thread. **The rewrite of `todo.md` A3 is itself a task**
  (inside #1) — it is not something to do silently.

### C3. *Three workers or one?*

- **Review B:** at ~4 ms/frame a single processor thread covers 700×700 at 20 Hz; the pool
  is only justified for the larger a2A1920 sensor, and only with an in-flight cap.
- **Not stated by B:** `spn_workers` is a **shipped GUI control** (`todo.md` Done item 14;
  `gui/main_window.py:630, 658`). Going single-thread removes an operator control that
  already exists.
- **Recommendation — keep the pool plus the in-flight cap from #1.** It costs nothing once
  capped, and avoids a visible UI regression. Record the decision in #7.

### C4. *Two different benchmark numbers*

- **Review A:** 7.8 ms mean / 11.5 ms p99 at 700×700.
- **Review B:** 4.0 ms mean / 9.3 ms p99, same size.
- **The lab's own note (`todo.md` item 13):** ~13 ms/frame at 40 Hz on the real rig.
- **Not a contradiction** — three different machines. **The lab rig number governs**, and
  all three sit comfortably inside budget. Treat "timing is fine" as established and
  "which number to quote" as machine-dependent.

### C5. *Where calibration data lives*

- **Current code:** `HDF5Recorder.save_calibration()` writes calibration arrays into a
  `calibration` group **inside the session file** (`core/recorder.py:102-121`).
- **Review A B2 / `todo.md` E2:** emit **separate** `DarkCalibration.h5` and
  `BrightCalibration.h5`.
- **Neither review says whether the in-file copy stays.** → Question Q2 below. My
  recommendation if you don't want to decide now: keep both (the in-file copy makes a
  session file self-contained; the separate files satisfy the supervisor's schema), and
  note the duplication in `Params`.

---

# Gaps — things that must happen but neither review lists

### G1. Nobody re-runs the MATLAB reference tests after the refactors *(most important)*

`CLAUDE.md` makes measurement accuracy non-negotiable, yet tasks 1, 2, 3, 5, 9, 14 and 24
all touch the exact code path that `test_dark_cal_offline.py` and
`test_bright_cal_offline.py` validate against MATLAB. Neither review makes "the offline
match still holds at < 2 %" a completion criterion. **I added it to the *Done when* of
every task that touches `core/pipeline.py`, `core/recorder.py`, `processor.py`, or the
timestamp path.**

### G2. Changing the timestamp means changing four frame sources, not one

Both reviews list `camera.py` for the timestamp fix. In fact `frame_ready(np.ndarray)` is
emitted by `camera.py`, `mock_camera.py`, `folder_camera.py` and `h5_replay.py`, and
asserted in four test files. Folded into task #5's file list.

### G3. No real-hardware verification step for the backpressure/timestamp work

`todo.md` D5 exists precisely because mock cameras bypass `camera.py`'s grab loop. The
intake refactor needs the same treatment and neither review adds it. → task #6.

### G4. Nobody specifies how already-saved points get re-scaled at FINISHED

Task #10 (short/long normalization) recomputes the baseline **after** the session ends —
but the recorder streams to disk *during* the session and flushes every 300 points
(`core/recorder.py:15, 59`). So the file already holds values normalized with the old
constant. Neither review states the mechanism. Two options: (a) store raw BFi during the
session and write `rBFi` once at close, or (b) re-read and rescale the dataset at FINISHED.
**Recommendation: (a)** — one write path, no rewrite of a possibly-huge dataset. Worth
deciding before starting #9, since it shapes the schema.

### G5. The x-axis is already in minutes

`todo.md` E1 asks to "convert the plot x-axis to minutes if total time > 120 s," and Review
A's T6 repeats it. But `gui/plot_widget.py:58` already divides by 60 unconditionally and the
axis label is always `min` — so a 60-second clip currently renders as 0–1 min. The real
requirement is probably the inverse (show **seconds** for short recordings). → Question Q1.

### G6. Nothing states what a NaN row means downstream

Task #2 writes NaN into the results file. Safe for the live plot (it already filters
non-finite values), but nobody has confirmed the lab's MATLAB analysis tolerates NaN in
`rBFi`/`timeVec`. → Question Q3.

---

# Claims labelled as findings that don't hold up

### S1. "Save-raw-frames just needs wiring" — **false, it's already wired**

Both `todo.md:308` and Review A's T14 say `chk_save_frames` still needs connecting to
`recorder.append_frame()`. It is connected — `gui/main_window.py:1113-1114`. The real
remaining work is the disk guard and moving gzip off the GUI thread. Corrected in task #16.

### S2. "Convert the x-axis to minutes" — already true

See Gap G5. The stated task is already satisfied, and probably means the opposite.

### S3. Review B's memory-growth number is from a rebuilt harness, not the live app

Review B's "23 MB and growing, 0 drops reported" comes from a **reconstruction** of the
dispatcher/pool/inflight structure, not from a running session. I verified the
reconstruction faithfully matches `core/pipeline.py`, so the *mechanism* is real and the
finding stands — but treat the specific megabyte figure as an illustration, not a
measurement of your app.

### S4. "186/186 tests pass" is inherited, not re-verified

Review A ran the suite; Review B explicitly did not (its Assumption 3) and neither did this
merge. Worth one `pytest` run before starting, so you know your baseline is green.

### S5. Both reviews substitute a document they could not find

Both state they could not locate a "SCOS GUI Project" document with a Session tab, and both
treat `docs/SCOS_protocol.md` + the `todo.md` E-items as the binding spec instead. Every
Session-tab task here (#9–#13, #21) inherits that substitution. If the real document exists
outside the repo, those tasks should be re-checked against it.

---

# Questions before you start

1. **Plot x-axis units (blocks #10):** the axis is already always in minutes. Should short
   recordings (≤ 120 s) show **seconds** instead, or is "always minutes" fine?
2. **Calibration duplication (blocks #9):** when `DarkCalibration.h5` / `BrightCalibration.h5`
   become separate files, does the copy inside the session `.h5` stay, or go?
3. **NaN tolerance (affects #2):** does your MATLAB analysis handle `NaN` inside `rBFi` /
   `Intensity`, or should missing points be flagged another way (e.g. a separate `valid`
   mask dataset)?
4. **When is `rBFi` written (blocks #9, shapes the schema)?** I assumed option (a) from Gap
   G4 — buffer raw BFi during the session, write the final `rBFi` once at close. The
   alternative is to write rBFi live and rescale the dataset at FINISHED. (a) avoids
   rewriting a possibly-huge dataset, but means a crash mid-session leaves a file with no
   `rBFi` in it — only raw BFi plus the constant. Confirm (a) is acceptable.
