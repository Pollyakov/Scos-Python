# SCOS Review B — Pipeline, Timing & Reliability

Review date: 2026-07-26. Read-only review of the code; the only files written are
this findings document (and its task plan). Prompt: `Review_Prompt_B.md`.

Backing evidence:
- **Line-by-line reading** of `core/pipeline.py`, `camera.py`, `processor.py`,
  `core/session.py`, `core/recorder.py`, `gui/main_window.py`,
  `gui/image_widget.py`, `gui/plot_widget.py`, `main.py`, and `tests/test_pipeline.py`,
  against `docs/SCOS_protocol.md`, `docs/Implementation_Plan.md`, and
  `docs/Plan_RealTime_Patches.md`.
- **Processor benchmark** (`bench_processor.py`, 700x700, window 7, 12-bit): mean
  **4.0 ms**, p95 5.7 ms, p99 9.3 ms per frame.
- **A custom read-only experiment** that imports the real `_DropOldestQueue` and
  rebuilds the exact dispatcher -> `ThreadPoolExecutor` -> `_inflight` structure to
  reproduce the memory-growth trap under overload.

Companion to `Review_Findings_A.md` (Review A, scientific correctness). Where an
item overlaps Review A (silent drops, timestamps) it is **cross-referenced**, not
re-derived, so the two reviews compose into one backlog.

Jargon is defined the first time it appears.

---

## Assumptions (please correct any that are wrong)

1. **I could not find a "SCOS GUI Project" document with a "Session" tab in the
   repo.** `docs/SCOS_protocol.md` and the E-items in `docs/todo.md` read like that
   tab's content, so I treated them as the binding Session-tab spec — the same
   substitution Review A made. You called this document *binding*; please point me
   at it if it lives outside the repo.
2. **The 4 ms/frame benchmark is on this machine, not necessarily the lab PC.** The
   lab's own note (`todo.md` item 13) says ~13 ms/frame at 40 Hz on the real rig.
   Both are well inside budget; the real-PC number is the one that matters.
3. **I did not run the full `pytest` suite in this review** (Review A already
   reported 186/186 passing). I ran only the benchmark and the backpressure
   experiment.
4. Terms: "kappa^2" = speckle contrast squared (variance / mean^2 in a small sliding
   window); "BFi" = blood-flow index = 1/kappa^2; "backpressure" = when a slow
   downstream stage makes an upstream stage wait instead of letting work pile up;
   "OOM" = out of memory (the program is killed).

---

## The observation under review — accurate, and reproduced

> "The two buffers that are bounded and visible (Pylon 20 + `_input_q` 20) rarely
> fill. The two that actually fill up (`_inflight` deque + the ThreadPoolExecutor's
> internal queue) have no size limit and no backpressure ... The proper fix is to cap
> how many work items are in flight at once."

**Confirmed.** A read-only experiment imported the real `_DropOldestQueue` and the
exact dispatcher/pool/inflight structure, then fed frames 33 % faster than three
100 ms/frame workers could drain. After 5 seconds:

| | Drops *reported* | Executor's hidden queue | `_inflight` deque | **Frames still in RAM** |
|---|---|---|---|---|
| **Current design** | **0** | 20 items | 166 | **23 (~23 MB) and growing** |
| **Fixed (semaphore cap)** | 0 (counts once input fills) | 2 items | 146 | **5 (~5 MB), bounded** |

The bounded, visible queue reported **zero** drops while ~23 MB of unprocessed
frames accumulated in two invisible, unbounded places. Extrapolated to an hour that
is tens of GB -> OOM -> total loss of a multi-hour session, drop counter still at
zero. Making `put()` blocking (as `todo.md` A3 / `Plan_RealTime_Patches.md` 1.1
prescribe) does **not** help: the dispatcher empties `_input_q` faster than the
camera fills it.

**Root cause (answer to prompt 1c):** there is **no backpressure to the camera at
all.** The camera copies each frame and calls `result.Release()` *before* processing
runs, so the Pylon driver buffer is freed every loop; intake is a non-blocking
drop-oldest `put()` on the GUI thread; the dispatcher never blocks. A slowdown has
nowhere to push back to and can only become unbounded RAM.

---

## Actual structure vs. documented target

Documented target (`Implementation_Plan.md §2`): Camera -> bounded blocking
`frame_queue` -> Processor thread -> bounded `result_queue` + bounded `record_queue`
-> GUI / Recorder thread. What actually runs:

```
Camera QThread
  (copies frame, releases Pylon buffer)
    frame_ready  -- Qt queued signal -->  GUI thread: _on_scos_frame()   <- intake ON the GUI thread
      submit() -> _DropOldestQueue(20)
        Dispatcher QThread: get() -> ThreadPoolExecutor(3) -> _inflight deque
          Emitter thread: future.result() -> result_ready -- Qt queued signal --> GUI: _on_scos_result()
            HDF5Recorder.append()   <- recording SYNCHRONOUS on the GUI thread
```

Two reliability-relevant mismatches: **(a)** no separate Processor thread draining a
bounded queue — intake runs on the GUI thread; **(b)** no Recorder thread —
`recorder.append()` and the gzip-compressing `recorder.append_frame()` run
synchronously on the GUI thread. `CLAUDE.md`'s "processing runs on the GUI thread"
line is also stale (processing moved into the pool).

### 1a/1b — Every place work can pile up

| # | Buffer | Bounded? | Limit | When it fills | Warned? |
|---|---|---|---|---|---|
| 1 | Pylon driver buffer | yes | `MaxNumBuffer=20` | drops frames; `GetNumberOfSkippedImages()` counts | yes (log + status) — **but only catches camera-thread lag; blind to downstream overload** because the frame is copied & released before processing |
| 2 | Camera->GUI Qt event queue (`frame_ready`) | **no** | none | grows ~1 MB/frame whenever the **GUI thread stalls** (modal dialog in `_start_recorder`; gzip in `append_frame`) | no |
| 3 | `_input_q` (`_DropOldestQueue`) | yes | 20 | silently evicts **oldest**, `dropped_count++` | **no — counter is never displayed or logged** (confirmed: no reference in `gui/`) |
| 4 | ThreadPoolExecutor internal queue | **no** | none | grows in RAM under sustained overload | no |
| 5 | `_inflight` deque | **no** | none | grows in lockstep with #4 | no |
| 6 | Emitter->GUI Qt queue (`result_ready`) | **no** | none | grows if GUI stalls (small: ~5 floats each) | no |
| 7 | Recorder in-RAM buffers (`_buf_*`) | yes | 300 | flush to disk | fine |
| 8 | Recorder `append_frame` (Save Frames) | — | — | **synchronous gzip on the GUI thread** -> stalls #2 | no |
| 9 | Plot lists `_time`/`_bfi` | **no** | none | ~5 MB over 4 h (small), but **full redraw every 1 s, no downsampling** | no |

### 1c — Does the slowdown reach the camera? No.
It slips past the one visibly-bounded queue (#3, which drains instantly) into the
unbounded #4/#5 (processing overload) or #2 (GUI stall).

### 1d — Frames silently discarded in four places:
1. `_input_q` drop-oldest (#3) — counted, but the counter is invisible.
2. #4/#5 grow until OOM — which discards the whole session.
3. The emitter's `except Exception: continue` (`pipeline.py:176`) swallows any
   processing error with no log/count — including the ROI-race crash below (this is
   how a thread-safety bug becomes an invisible data gap).
4. `recorder.append()` does `if k2_corr <= 0: return` — drops the row, so `time`
   stops being evenly spaced (cross-ref Review A B3; matters for pulse/FFT).

---

## 2. Timing budget — comfortably inside budget

Benchmark: mean **4.0 ms**, p95 5.7 ms, p99 9.3 ms per frame vs a **50 ms** budget at
20 Hz (~46 ms headroom); consistent with Review A (~8/11.5 ms) and the lab's ~13 ms
at 40 Hz. **One frame fits with large margin; a single Processor thread would suffice
at 700x700 / 20 Hz.** No timing optimization is needed — so per the prompt's ordering,
the buffering/flow-control fix is the priority. The 3-worker pool is only justified if
the larger a2A1920 sensor at speed needs it, and only *with* an in-flight cap.

## 3. Thread-safety — one real race

- **ROI dragged during measurement = data race (real).** The ROI circle stays
  draggable during `MEASURING`; dragging fires `_on_roi_changed` ->
  `processor.set_roi()`, which reassigns `_roi`, `_mask_crop`, `_dm_f32`, `_dv_f32`,
  `_bv_f32` **non-atomically** while up to 3 worker threads read them in `process()`.
  A worker can read a new `_roi` with an old `_mask_crop` -> shape mismatch ->
  `process()` raises -> the emitter swallows it -> the point silently vanishes (or,
  worse, a wrong kappa^2 if shapes happen to match). Violates the "no accuracy loss"
  rule.
- `_cached_G` read-check-write race is genuinely benign (deterministic value).
- Otherwise cross-thread comms use Qt signals correctly and workers touch no GUI
  objects. Good.

## 4. Hardware handling

- **Line2 trigger + Arduino:** solid — background upload thread, debounce, and a
  helpful diagnostic when frames stop (distinguishes "Arduino gone" from "no pulses").
- **Missed frames:** detected only via `GetNumberOfSkippedImages()` (camera-thread
  lag) — blind to the processing/GUI overload paths above.
- **Reconnect is NOT automatic.** On a camera drop, `_on_camera_error` just un-checks
  Start Video and asks the user to click again — a gap for unattended multi-hour runs.
- **Laser control** is manual pop-ups today; the hardware path exists (enable pin 13);
  `todo.md` F3 is the right home for automating it. (`SCOS_protocol.md:27` has a typo —
  "turn off the Laser" for *bright* cal should be *on*; the code is already correct —
  cross-ref Review A B11.)

## 5. Code organization & tooling

- **core/gui split is mostly good** — but `processor.py` and `camera.py` still live at
  repo root, and `core/session.py` imports `local_variance` from `processor.py`, so the
  "pure logic in core/" boundary isn't clean yet (`todo.md` C1).
- **Offline reference test exists and passes** (`test_dark_cal_offline.py` /
  `test_bright_cal_offline.py` vs MATLAB, <2 %).
- **`logging` used throughout** (good), not `print`.
- **Tooling is thin:** only `requirements.txt`. No `pyproject.toml`, no lockfile, no
  `ruff`/`mypy` config, no `.pre-commit-config.yaml` (there is a git `pre-commit` hook
  running the tests).
- **Test blind spot:** `test_pipeline.py` checks `dropped_count` on the *isolated*
  queue — the one buffer that never fills in practice — and never exercises the
  overload/memory-growth path. False confidence about exactly this review's failure
  mode.

## 6. Other long-run reliability flags

- Synchronous recording (esp. gzip `append_frame`) on the GUI thread (#8) -> stalls
  the pipeline when Save Frames is on.
- Plot redraws all points every second with no downsampling (#9) -> lag in hour 2-3
  (cross-ref Review A D1 / B10).
- Timestamp taken on the GUI thread from wall-clock `time.time()` -> jitter + possible
  clock jumps over hours (cross-ref Review A B4).

---

## A) Summary — most important first

1. **Two unbounded, invisible buffers (executor queue + `_inflight` deque) turn any
   sustained processing slowdown into unbounded memory growth -> OOM crash -> total
   loss of a multi-hour session, with the drop counter reading zero.** Reproduced
   experimentally. Top reliability risk.
2. **The repo's own prescribed fix is insufficient.** `todo.md` A3 / `Patch 1.1` say
   "replace with a blocking `queue.Queue(20)`." A fast dispatcher empties it into the
   unbounded executor queue anyway. **Revise A3 — cap in-flight work; don't just block
   `put()`.**
3. **A second unbounded path: the camera->GUI Qt event queue.** Because intake runs on
   the GUI thread and the frame is copied+released before processing, any GUI stall
   (modal dialog, or synchronous gzip when Save Frames is on) grows ~1 MB/frame with no
   limit and no warning.
4. **Frames/points are silently discarded in four places**, including the emitter
   swallowing all exceptions — which is how the ROI race becomes an invisible data gap.
5. **Real thread-safety bug:** the ROI circle stays draggable during measurement;
   `set_roi()` mutates processor state non-atomically while worker threads read it.
6. **Timing is a non-issue (4 ms/frame vs 50 ms budget)** — a single Processor thread
   would suffice at 700x700 / 20 Hz; the pool is justified only for the larger sensor at
   speed, and only with an in-flight cap.
7. **The running code diverges from the documented architecture:** no Processor thread
   (intake on GUI), no Recorder thread (recording synchronous on GUI). Docs and
   `CLAUDE.md` are stale.
8. **Tooling/tests don't cover this:** no pyproject/lockfile/ruff/mypy; `test_pipeline.py`
   tests only the one buffer that never fills — the overload path is untested.

## B) Concrete suggested changes (what -> why -> risk of skipping)

- **B1. Cap in-flight work items (semaphore) + surface the drop counter.** Bounds
  memory so a slow patch can't OOM the session, and makes drops visible in the status
  bar + `app.log`. *Risk:* the silent-OOM failure above.
- **B2. Stop the silent swallows.** Emitter: log + count exceptions instead of
  `except Exception: continue`. Recorder: keep `k2_corr <= 0` rows as `NaN` so the time
  axis stays even. *Risk:* invisible gaps corrupt pulse/FFT; hidden crashes.
- **B3. Move frame intake off the GUI thread into a bounded, blocking `frame_queue`
  drained by a Processor thread, with `overload_detected` at 80 % full** — the
  documented `Implementation_Plan §2` design. *Risk:* the camera->GUI queue keeps
  growing on any GUI stall.
- **B4. Decide pool vs. single thread.** At 4 ms/frame a single thread covers
  700x700/20 Hz; keep the pool + in-flight cap only if the a2A1920 sensor at speed needs
  it. *Risk:* carrying complexity (and bugs) you don't need.
- **B5. Lock the ROI (and processor-mutating controls) during measurement, or make
  `set_roi()` atomic** (build one new bundle, assign one reference). *Risk:* silent
  crashes / wrong kappa^2 if a user nudges the ROI mid-run.
- **B6. Move HDF5 recording (incl. gzip `append_frame`) into a Recorder thread fed by a
  bounded `record_queue`.** *Risk:* GUI stalls (-> buffer #2 growth) whenever Save Frames
  is on or the disk hiccups.
- **B7. Add a real overload test** (submit faster than workers drain; assert in-flight
  and RAM stay bounded and drops are counted). *Risk:* regressions reopen the bug.
- **B8. Plot downsampling** (`setDownsampling(auto=True, mode='peak')` +
  `setClipToView(True)`) — cross-ref Review A D1.
- **B9. Automatic camera reconnect** for unattended long runs.

## C) Ordered task plan (by dependency)

> **"Done" always includes updating the relevant docs** — `docs/todo.md`,
> `docs/Implementation_Plan.md`, and the stale architecture line in `CLAUDE.md`.

### Phase 0 — Stop the bleeding (bounded memory + visible drops) — *do first*

**T1 — Cap in-flight work + surface the drop counter.**
- Goal: overload can no longer grow memory without bound, and any dropped frame is
  visible to the operator.
- Files: `core/pipeline.py`, `gui/main_window.py`.
- Done when: the overload experiment shows RAM stays bounded; the dropped-frame count
  appears in the status bar **and** `app.log`; `todo.md` A3 is rewritten to say
  "cap in-flight work," not "just block `put()`."

**T2 — Stop the silent swallows.**
- Goal: no hidden gaps in the time axis and no hidden crashes.
- Files: `core/pipeline.py` (emitter logs + counts exceptions), `core/recorder.py`
  (keep `k2_corr <= 0` rows as `NaN`). Cross-ref Review A B3.
- Done when: a test feeds one `k2_corr <= 0` point and one raising frame, and both are
  preserved / counted rather than vanishing.

**T3 — Add a real overload test.**
- Goal: kill the false confidence in `test_pipeline.py`.
- Files: `tests/test_pipeline.py`.
- Done when: a test submits faster than the workers drain and asserts in-flight work
  **and** memory stay bounded while drops are counted.

### Phase 1 — Root-cause redesign (real backpressure) — *depends on Phase 0*

**T4 — Move frame intake off the GUI thread.**
- Goal: restore backpressure to the camera — the documented `Implementation_Plan §2`
  design (Camera -> bounded blocking `frame_queue` -> Processor thread), with an
  `overload_detected` signal at 80 % full.
- Files: `camera.py`, `core/pipeline.py`, `gui/main_window.py`.
- Done when: a sustained-overload run blocks (never silently drops), raises
  `overload_detected`, keeps the GUI responsive, and holds RAM flat; the
  `Implementation_Plan.md` diagram and `todo.md` A3 are updated to match what shipped.

**T5 — Confirm pool-vs-single-thread decision.**
- Goal: a single Processor thread covers 700x700/20 Hz; keep the pool (with the T1
  in-flight cap) only if the a2A1920 sensor at speed needs the parallelism.
- Files: new `docs/realtime_architecture.md`.
- Done when: the choice and the timing numbers behind it are written down.

### Phase 2 — Thread-safety

**T6 — Lock / make-atomic the ROI during measurement.**
- Goal: remove the data race in `set_roi()` vs concurrent `process()`.
- Files: `gui/image_widget.py` (disable `CircleROI` drag when locked),
  `gui/main_window.py`, `processor.py` (build one new crop bundle, assign one
  reference).
- Done when: dragging the ROI during `MEASURING` cannot crash a worker (test); docs
  updated.

### Phase 3 — Recording & long-run robustness

**T7 — Recorder thread + bounded `record_queue`.**
- Goal: disk slowness becomes backpressure, not a GUI freeze; gzip `append_frame` no
  longer runs on the GUI thread.
- Files: `core/recorder.py`, `core/pipeline.py`, `gui/main_window.py`.
- Done when: Save Frames on does not stall the GUI; the record queue is bounded; test
  passes.

**T8 — Plot downsampling.**
- Goal: keep rendering fast at 200 k+ points. Cross-ref Review A D1 / B10.
- Files: `gui/plot_widget.py`.
- Done when: a 200 k-point replay stays smooth.

### Phase 4 — Hardware robustness (unattended runs)

**T9 — Automatic camera reconnect.**
- Goal: a camera drop during a multi-hour run recovers without the operator re-clicking
  Start Video.
- Files: `camera.py`, `gui/main_window.py`.
- Done when: a simulated disconnect auto-recovers; docs updated.

**T10 — Capture-time monotonic timestamps.**
- Goal: even sample spacing for pulse/FFT — timestamp at grab time on the monotonic
  clock, not `time.time()` on the GUI thread. Cross-ref Review A B4 / T1.
- Files: `camera.py`, `core/pipeline.py`, `gui/main_window.py`.

### Phase 5 — Tooling

**T11 — Project tooling lock-in.**
- Goal: reproducible installs and static checks (only `requirements.txt` today).
- Files: `pyproject.toml`, a lockfile, `ruff` + `mypy` config.
- Done when: `ruff check` and `mypy core/` run clean in the pre-commit hook.

### Dependency order (summary)

```
T1 (cap in-flight + visible drops) --+-- T2 (no silent swallows) -- T3 (overload test)
                                     +-- T4 (intake off GUI + backpressure) -- T5 (pool vs single)
                                           -- T6 (ROI race)
                                                -- T7 (recorder thread) -- T8 (plot downsampling)
                                                     -- T9 (auto reconnect) -- T10 (capture timestamps)
                                                          -- T11 (tooling)
```

Phase 0 is independent and should land first — it stops the catastrophic
memory-growth failure regardless of the larger redesign in Phase 1.

---

## What I verified vs. assumed

- **Verified by running:** processor benchmark (4.0 ms mean / 9.3 ms p99 at 700x700);
  the backpressure experiment (23 MB parked and growing under 33 % overload with 0
  drops reported; bounded to ~5 MB with a semaphore cap).
- **Verified by reading:** every buffer, the `except Exception: continue` swallow, the
  `k2_corr <= 0` row drop, the ROI-drag data race, the synchronous GUI-thread recording,
  the absence of `dropped_count` in `gui/`, and the tooling inventory.
- **Assumed:** the Session-tab spec equals `SCOS_protocol.md` + `todo.md` E-items
  (Assumption 1); the benchmark machine approximates the lab PC (Assumption 2); the full
  test suite still passes as Review A reported (Assumption 3).
