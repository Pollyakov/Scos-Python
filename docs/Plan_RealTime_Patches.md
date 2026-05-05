# Plan_RealTime — Proposed Patches

Review notes for [`Plan_RealTime.pdf`](Plan_RealTime.pdf) and [`Full_Plan_RealTime.pdf`](Full_Plan_RealTime.pdf).
Patches are grouped by severity. Each entry says **where** the problem is, **what** the
problem is, and **the concrete change** to make.

---

## Tier 1 — Real bugs / contradictions in the plan

These are not opinions: the plan as written would either contradict its own stated
principles, or quietly break behavior the existing code already has.

### Patch 1.1 — Pipeline code silently drops the oldest frame

**Where:** Full_Plan §9, Layer 4 (`core/pipeline.py`), `_capture_loop`.

**Problem:** The example code does this on `queue.Full`:

```python
except queue.Full:
    # queue is full — discard the OLDEST frame
    self.frame_queue.get_nowait()
    self.frame_queue.put_nowait(frame)
    self._dropped += 1
```

This contradicts the entire premise of §13 ("frames MUST NOT be silently discarded —
silent loss breaks the FFT and gives a wrong pulse rate"). Counting drops in
`self._dropped` does not make them less silent: by the time the count goes up, the
data is already corrupted, and the user has not been asked anything.

**Fix:** Replace the `except queue.Full` branch with the high-water-mark behavior
described in §13.2. Outline:

```python
def _capture_loop(self):
    while not self._stop_event.is_set():
        try:
            frame = self.source.get_frame(timeout_s=2.0)
        except Exception as e:
            self.error.emit(str(e))
            return

        # High-water mark: warn BEFORE the queue is full
        if self.frame_queue.qsize() >= int(0.8 * self.frame_queue.maxsize):
            self.overload_detected.emit(self.frame_queue.qsize())
            # caller decides: pause, reduce ROI, drop to save-only, etc.

        # Block until there is space — never drop silently.
        # If we end up blocked here, it means the user has not yet
        # responded to the overload dialog.
        self.frame_queue.put(frame)
```

Add a `overload_detected = pyqtSignal(int)` to the pipeline. The GUI binds it to
"pause + show 4-option dialog".

### Patch 1.2 — Plot update interval is contradictory (100 ms vs 1000 ms)

**Where:** Three places disagree:
- Plan_RealTime p. 2 — "QTimer every 100 ms drains result_queue"
- Full_Plan §7 — "QTimer every 1000 ms reads result_queue"
- Full_Plan §9, Layer 5 code — `self._plot_timer.setInterval(1000)`

**Problem:** Two values appear in the document; readers cannot tell which is
authoritative.

**Fix:** Standardize on **1000 ms** in all three places. Rationale: for multi-hour
recordings, 1 Hz plot updates are sufficient — `pyqtgraph` downsampling makes
individual points invisible at that scale anyway, the image widget already updates
faster (30 FPS in `PREVIEW`, every 2.5 s in `MEASURING`), and a slower plot timer
saves CPU during long sessions. Update the Plan_RealTime compact summary to match
Full_Plan's 1000 ms; the Layer 5 code is already correct.

### Patch 1.3 — Hardware trigger / Arduino integration is missing

**Where:** Full_Plan §9, Layer 2 (`core/camera_source.py`).

**Problem:** The current [`camera.py`](../camera.py) supports a hardware trigger on
`Line2` (driven by the Arduino) and an internal-frame-rate mode. Real lab recordings
use the hardware-trigger path. The plan's `CameraFrameSource` only mentions
`exposure_us, gain_db, frame_rate_hz, pixel_format` — no trigger mode, no
synchronization with `arduino_uploader.py`.

**Fix:** Extend `CameraFrameSource.__init__` to take a `trigger_mode` parameter,
and document the interaction with `arduino_uploader.py`:

```python
class CameraFrameSource(FrameSource):
    def __init__(
        self,
        exposure_us: int,
        gain_db: float,
        frame_rate_hz: float,
        pixel_format: str,
        trigger_mode: Literal["off", "line2"] = "off",
    ):
        ...
```

Add a short subsection to the plan: "External trigger interaction — when
`trigger_mode='line2'`, the camera waits for a TTL pulse from Arduino on Line2; the
`frame_rate_hz` parameter is then a *target*, not a hard setting, and the actual rate
is whatever the Arduino delivers." Reference the existing warning the GUI already
shows when frames stop arriving in external-trigger mode (commit `06d4629`).

### Patch 1.4 — Recorder thread is in the folder structure but never wired in

**Where:** Full_Plan §7 folder structure lists `core/recorder.py`, but §3 architecture
diagram is `Camera → frame_queue → Processor → result_queue → GUI`. No recorder
thread, no recording queue.

**Problem:** With 4-hour recordings, periodic-save-to-HDF5 (Problem 4 in §4) is the
only thing protecting against crash data loss. If saving runs on the GUI thread, a
slow disk flush freezes the GUI; if it runs in the processor thread, slow disk flush
backs up `frame_queue` and triggers overload. It needs its own thread.

**Fix:** Update the architecture diagram to **3 worker threads + GUI**:

```
Camera ─► frame_queue ─► Processor ─┬─► result_queue ─► GUI (QTimer 100ms)
                                    └─► record_queue ─► Recorder ─► HDF5 file
```

The processor `tee`s each `ProcessingResult` (or each raw frame, depending on
`save_frames`) into a second queue. The recorder thread `get`s from `record_queue`,
batches into chunks, and `append`s to one HDF5 file every M minutes. On
`stop()` / `closeEvent`, the recorder flushes the partial chunk before exiting.

`record_queue` should be bounded (see Patch 2.4) — disk slowness must surface as
backpressure, not as unbounded RAM growth.

### Patch 1.5 — Image-display channel is collapsed into the SCOS channel

**Where:** Full_Plan §9, Layer 4 — `frame_ready = pyqtSignal(np.ndarray)` "every
N-th frame for display".

**Problem:** The current code (see [`CLAUDE.md`](../CLAUDE.md) — "Architecture")
emits **two** signals from the camera thread:

- `frame_ready` — every frame, for SCOS processing (must not be throttled)
- `display_ready` — ≤30 FPS, for the image widget (throttled)

Collapsing them into one "every N-th frame" signal means either the SCOS path drops
frames (regression, breaks FFT) or the image widget redraws 20×/s (wasted GPU time
during long recordings).

**Fix:** Keep the dual-channel pattern. In the new architecture, the *processor*
thread is the natural place to throttle display — it already touches every frame and
knows the current state:

- Always emit `result_ready` (one per processed frame) → graph.
- Throttle `display_frame` by state:
  - `IDLE` / `PREVIEW` → 30 FPS
  - `DARK_CAL` / `BRIGHT_CAL` → 5 FPS (visual confirmation only)
  - `MEASURING` / `MEASURING_INIT` → every 2.5 s (per protocol)

### Patch 1.6 — Recording-length cap is stale

**Where:** Plan_RealTime p. 1 system-parameters table; Full_Plan §4 data-volumes
table.

**Problem:** Both PDFs say "up to several hours / max 4 h". The spec was tightened in
commit `959f3c1` ("Tighten wording on the 4-hour recording cap"). The plan should
quote whatever the final wording in [`docs/SCOS_protocol.md`](SCOS_protocol.md) is,
not paraphrase it.

**Fix:** Replace both occurrences with a verbatim quote from `SCOS_protocol.md`, and
add a footnote: "If the protocol cap changes, update both this section and
`SessionConfig.recording_minutes` default in `core/session.py`."

---

## Tier 2 — Things the plan should add

Not bugs, but the plan is incomplete without them and Phase 3 will stall when you
reach the gap.

### Patch 2.1 — Phase 0 must include a real benchmark, not just a reference recording

**Where:** Full_Plan §6 (Phase 0). Currently has steps 0.1–0.4 (recording,
offline run, sample frames, gap map). Section 7 separately says
"`process()` time — UNKNOWN, need to measure" but never schedules it.

**Problem:** The whole architecture rests on the assumption that 700×700 + window=7
fits comfortably under the 50 ms per-frame budget. If on the target lab PC it
already takes 45 ms, the design is fragile (no headroom for var_bright filtering,
HDF5 append, etc.) and 40 Hz is impossible without rewriting in numba/Cython. **You
need to know this on day 1, not week 3.**

**Fix:** Add Step 0.5 to Phase 0:

> **Step 0.5 — Benchmark on the target machine.** Run
> `python bench_processor.py --width 700 --height 700 --window 7 --duration 30 --bits 12`
> on the same lab PC the program will eventually run on. Record:
> - mean per-frame time, p95, p99
> - whether the GIL is released during `uniform_filter` (look at CPU% — if a
>   single-threaded benchmark uses >100% of one core, the GIL is being released)
>
> If p99 ≥ 35 ms (70 % of budget), Phase 1 must include an optimization plan
> (smaller default ROI, numba, separable filter) before any code is written.

### Patch 2.2 — State machine is missing a `PREVIEW` state

**Where:** Full_Plan §7, "Questions About the State Machine" table.

**Problem:** Both PDFs talk about "preview" (live image, ROI placement, focus
adjustment, exposure tweaking). But the state list jumps `IDLE → DARK_CAL`. There is
no state in which the camera is running, the image widget is updating at 30 FPS, and
no κ² / no calibration is being consumed.

**Fix:** Insert `PREVIEW` between `IDLE` and `DARK_CAL`:

| State            | What happens                                                          |
| ---------------- | --------------------------------------------------------------------- |
| `IDLE`           | nothing, waiting for "Connect"                                        |
| `PREVIEW`        | camera streaming at 30 FPS to image widget; ROI/focus/exposure tuning |
| `DARK_CAL`       | capture N1 frames without laser → `mean_dark`, `var_dark_filtered`    |
| `BRIGHT_CAL`     | capture N2 frames with laser, no subject → `var_bright_filtered`      |
| `MEASURING_INIT` | first `norm_seconds` — collect BFI for normalization                  |
| `MEASURING`      | main loop, plot rBFI                                                  |
| `FINISHED`       | flush remaining HDF5, show summary                                    |
| `ERROR`          | error, log, save accumulated data                                     |

`SCOSSession.start()` transitions `PREVIEW → DARK_CAL`, not `IDLE → DARK_CAL`.

### Patch 2.3 — `var_bright` formula is left as `# ...` in the example code

**Where:** Full_Plan §9, Layer 3, `_finish_bright_cal()`:

```python
def _finish_bright_cal(self):
    stack = np.stack(self.state.bright_buffer, axis=2)
    # var_bright = ?  per protocol — mean spatial filter of the mean
    # ... details depend on what exactly the protocol requires
    ...
```

**Problem:** This is the most subtle term in the noise correction. Leaving it as
"figure it out later" guarantees Layer 3 stalls.

**Fix:** Quote the formula from
[`docs/SCOS_protocol.md`](SCOS_protocol.md) directly into the plan. Roughly:

```python
def _finish_bright_cal(self):
    stack = np.stack(self.state.bright_buffer, axis=2)  # (H, W, N2)
    var_bright_per_pixel = stack.var(axis=2)            # temporal variance
    # Spatial smoothing with the same window as κ²:
    self.state.var_bright_filtered = uniform_filter(
        var_bright_per_pixel, size=self.config.window_size
    )
    self.state.bright_buffer.clear()
    self.state.state = State.MEASURING_INIT
```

If the protocol says something different, copy that exact text into the plan as a
quoted block — do not paraphrase.

### Patch 2.4 — Bound the `result_queue` and `record_queue`

**Where:** Full_Plan §3 — "result_queue size=∞". Full_Plan §7 —
`queue.Queue(maxsize=0)` (which means unbounded in Python's `queue` module).

**Problem:** Unbounded queues turn a slow consumer into an OOM. If the GUI hangs on
a slow paint or a modal dialog, `result_queue` will grow without limit. With 200k
results × ~250 bytes (real `dataclass` overhead, see Patch 3.1) = ~50 MB before
anyone notices. With a stuck recorder, raw frames at 1 MB each = OOM in seconds.

**Fix:** Bound both queues and treat overflow as a fatal error (not a silent drop):

```python
self.result_queue = queue.Queue(maxsize=2000)   # ~100 s of results at 20 Hz
self.record_queue = queue.Queue(maxsize=200)    # ~10 s of frames at 20 Hz
```

If the processor cannot `put` to `result_queue` within 1 s, emit `error.emit("GUI
stuck — result queue overflow")` and transition to `ERROR`. Overflow is a real bug
(not a transient slowdown), and silently dropping results would corrupt the time
axis just like dropping raw frames does.

---

## Tier 3 — Smaller fixes

### Patch 3.1 — Memory estimate for 200k results is wrong

**Where:** Full_Plan §7 — "200,000 × ~50 bytes = 10 MB — all results in memory".

**Problem:** Python `dataclass` instances are not 50 bytes. A `ProcessingResult`
with 6 floats + 1 int + 1 `None|float` is closer to 250 bytes per instance once you
include the Python object header (~16 B), `__dict__` overhead, and float boxing.
Real cost is 50 MB, not 10 MB. Still fine in absolute terms — but the number is
wrong, and the plan is supposed to be correct on first principles.

**Fix:** Either (a) update the number to "~50 MB", or (b) switch the in-memory store
to NumPy arrays (one array per field — `times`, `bfi`, `rbfi`, `mean_intensity`,
`frame_number`). Option (b) is also faster to plot and to flush to HDF5.

### Patch 3.2 — Add a "GIL note" to §3 (Solution Architecture)

**Where:** Full_Plan §3, after the architecture diagram.

**Problem:** Beginners reading the plan often ask "but Python has the GIL — does
threading actually parallelize anything?" Answer is yes for this specific workload,
but the plan never says so.

**Fix:** Add one paragraph:

> **A note on the GIL.** Python normally runs only one thread at a time because of
> the Global Interpreter Lock (GIL). However, NumPy and SciPy operations on large
> arrays — including `scipy.ndimage.uniform_filter`, which dominates our
> per-frame cost — release the GIL while the C code runs. That means the
> processor thread really does run in parallel with the GUI thread on this
> workload. If we ever rewrote the math in pure Python, this would no longer
> be true and the design would have to switch to multiprocessing.

### Patch 3.3 — State the total buffer depth explicitly

**Where:** Full_Plan §13.1, "Three buffering layers".

**Problem:** The section explains the three layers but does not add them up. Pylon
`MaxNumBuffer=20` + Python `frame_queue` size 20 = effectively **40 frames of slack
before the first drop**, which is ~2 seconds at 20 Hz. That is the actual user
reaction window, not the 800 ms quoted in the queue-fill discussion.

**Fix:** Add one sentence to §13.1:

> Total slack from the start of a slowdown until the first dropped frame is
> `MaxNumBuffer + frame_queue.maxsize = 40` frames ≈ 2 s at 20 Hz. The 80 %
> high-water mark fires after the *Python* queue alone hits 16 frames, leaving
> ~1.2 s of additional Pylon buffer to absorb the user's reaction time.

### Patch 3.4 — Remove `GenTL / GenICam` from the glossary

**Where:** Full_Plan §11 (Glossary).

**Problem:** The terms appear nowhere in the plan body. They are correct
definitions, but a glossary should only list things that actually appear in the
document. Trim it.

**Fix:** Delete the `GenTL / GenICam` entry. (If you ever do reference these terms
in `realtime_architecture.md`, put the entry there instead.)

---

## Summary checklist

When you (or whoever owns the plan) sit down to apply these:

- [ ] **1.1** Rewrite `_capture_loop` overflow branch — block, do not drop
- [ ] **1.2** Standardize on 100 ms `QTimer` everywhere
- [ ] **1.3** Add `trigger_mode` to `CameraFrameSource` + Arduino interaction note
- [ ] **1.4** Add Recorder thread + `record_queue` to architecture diagram
- [ ] **1.5** Restore dual-channel signals: `result_ready` + state-throttled `display_frame`
- [ ] **1.6** Quote the recording-cap wording verbatim from `SCOS_protocol.md`
- [ ] **2.1** Add Phase 0 Step 0.5 — benchmark on target PC
- [ ] **2.2** Insert `PREVIEW` state between `IDLE` and `DARK_CAL`
- [ ] **2.3** Quote the real `var_bright` formula instead of `# ...`
- [ ] **2.4** Bound `result_queue` and `record_queue`; overflow → `ERROR`
- [ ] **3.1** Fix memory estimate (or switch to NumPy arrays)
- [ ] **3.2** Add GIL paragraph to §3
- [ ] **3.3** Add total-buffer-depth sentence to §13.1
- [ ] **3.4** Drop `GenTL / GenICam` from glossary

Tier 1 patches should land before any Phase 3 coding starts — they describe behavior
the early code will hard-code.
Tier 2 patches should land before the layer they affect (Patch 2.1 before Phase 1,
Patch 2.2/2.3 before Layer 3, Patch 2.4 before Layer 4).
Tier 3 patches can land any time, but Patch 3.1 is worth doing now because it
changes a data-structure decision.
