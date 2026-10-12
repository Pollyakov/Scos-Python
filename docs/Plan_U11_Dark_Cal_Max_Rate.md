# Plan — U11: dark calibration at the camera's maximum frame rate

Written 2026-10-11. Task: `docs/todo.md`, row **U11** (Vika's request, 2026-10-08).
Prerequisite **D8** (the faster dark-calibration collector) is done (todo Done item 51).
Status: **decisions D1–D3 decided 2026-10-11 (all as recommended: D1 = A, D2 = yes, D3 = no). Steps 1–7 done 2026-10-11/12 (results at the end of section 6; todo Done item 52). **Open: the rig check, step 8** (`rig_checklist.md` R5d/R5g/R5h, R6g–R6i).**

---

## 1. Goal

During the dark calibration (`DARK_CAL` — the laser is off, the app collects N1 frames,
default 600, and computes `mean_dark` and `var_dark` per pixel) the camera runs **as fast
as it can** instead of at the operator's frame rate (20 Hz on the rig). When the dark
calibration ends — whichever way it ends — the camera goes back to **exactly** the
frame-rate setting it had before.

Why: today the 600 dark frames take 30 s at 20 Hz (rig, 2026-10-08), in a dark room with
the subject waiting. The frame rate does not matter for dark frames (section 3), so this
is time that buys nothing.

**Where this comes from.** `docs/SCOS_protocol.md` says nothing about the dark-calibration
frame rate (Calibration 1, lines 30–35: acquire N1 frames, mean and variance per pixel,
spatial filter). This is Vika's request of 2026-10-08 (todo U11), not a deviation from the
protocol: the protocol's steps, the number of frames and the math stay the same.

**Must not change:** exposure, gain, pixel format, image area (the U6 crop — "Cut Image"
crops on the camera) and the trigger mode used for dark frames (already Off). Only the
spacing between frames changes.

---

## 2. How it works today (verified in the code)

| Piece | What it does now |
|---|---|
| `_start_dark_cal()` (`gui/main_window.py:1534`) | Folder → "turn off the laser" prompt → `camera.set_trigger(False)` (restarts grabbing; dark frames always use the internal frame rate) → flush stale frames → `DarkCalCollector` → state `DARK_CAL`. |
| `_apply_params()` (`camera.py:275`) | Trigger **Off**: writes `AcquisitionFrameRateEnable = True` and `AcquisitionFrameRate = frame_rate`. Trigger **On**: writes **neither** — the camera keeps whatever it had (after a dark calibration that is Enable = True at the set rate, because the dark calibration ran with the trigger Off). |
| `set_frame_rate()` (`camera.py:132`) | Writes Enable = True + the rate **live, while grabbing** — so changing these two nodes without a restart already works on this camera. |
| `_on_scos_frame()` (`main_window.py:2171`) | GUI thread, every frame, behind a queued connection. During `DARK_CAL`: FPS label, intensity stats every 0.5 s (mean + two percentiles over the ROI), then `DarkCalCollector.add_frame()`. |
| Queue during `DARK_CAL` | **Unbounded** (CLAUDE.md). If the camera sends faster than this handler runs, frames pile up in Qt's event queue — each one a full copy of the frame. |
| `_finish_dark_cal()` (`:1633`) | Saves the dark group of `Calibration.h5`, then restores the trigger (`set_trigger(True)` if it was on), then `_start_bright_cal()`. |
| `_abandon_calibration()` (`:1000`) | Every early exit (Stop SCOS, Stop Video, a camera error → Stop Video, a collector error, Cancel at the bright prompt). Restores the trigger only `if dark_pending and self._dark_cal_trigger_was_on`. |
| `closeEvent` (`:2836`) | Stops and closes the camera; mid-calibration it only deals with the folder. |
| Lost frames | `metadata/frames_lost_camera` = camera count **minus a snapshot taken when the measurement starts** (`main_window.py:1817`, `:1440`). Frames lost during the dark calibration therefore never reach the results file. The status bar warning "Camera: N frame(s) lost" would still appear. |
| Playback sources | `folder_camera.py:254` / `mock_camera.py:156` sleep `1 / frame_rate` between frames. |

---

## 3. The accuracy argument — and how we prove it

**Why the rate should not matter.** A dark frame is offset + read noise + dark current
collected **during the exposure**. Its per-pixel temporal mean and variance depend on
exposure time, gain, pixel format, image area and sensor temperature — not on how long the
camera waits between frames. All of those except temperature are held fixed (section 4.3
reads them back). Effects that could in principle depend on the rate:

| Effect | Expected size | Covered by |
|---|---|---|
| Sensor warms a little faster at a higher rate | The calibration lasts seconds; expected negligible | Rig A/B test (below) |
| Global-shutter storage node collecting dark signal during readout | Same readout time per frame at any rate | Rig A/B test |
| Consecutive frames correlated at high rate | None expected for read/shot noise | Rig A/B test |
| USB bandwidth: frames lost at the camera | Changes *which* frames are used, not their statistics | Status bar + log only; not in the results |

**The proof is a same-session A/B test on the rig** (step 6, `tools/dark_rate_ab.py`). A
comparison with the 2026-10-08 `Calibration.h5` is not enough: other day, other temperature.
The tool drives `camera.py` directly — no GUI, no laser — with the lens capped:

1. dark set A at 20 Hz, N1 frames
2. dark set B at the maximum rate, N1 frames
3. dark set C at 20 Hz again, N1 frames

All with the same exposure, gain, pixel format and crop. Each set goes through the app's own
`DarkCalCollector`, so the math is identical. Compare, inside the ROI, the mean of
`mean_dark` and the mean of `var_dark`.

- **Noise floor** = the difference between A and C (two runs at the same rate). This is
  the same idea as the 3.2 % spread measured between two identical `normal` rehearsal runs.
- **Pass:** B differs from both A and C by no more than the A–C difference (with a small
  tolerance, set from the numbers once A and C are in).
- **Fail:** stop. Do not ship U11. Ask Vika.

This test needs the camera, so it runs at the rig check (step 8). Until it passes, U11 is
"code done, rig check open", the same as U6.

---

## 4. Design

### 4.1 Camera side (`camera.py`)

A new method **`set_max_frame_rate(enabled: bool)`** and a flag `self.max_frame_rate`.

- `True`: writes `AcquisitionFrameRateEnable = False`. The camera then runs as fast as
  exposure, readout and USB bandwidth allow. It then reads back and logs
  `ResultingFrameRate` ("Dark calibration: camera at 118.3 fps").
- `False`: writes `AcquisitionFrameRateEnable = True` **and** `AcquisitionFrameRate =
  frame_rate`, explicitly. "Back to normal" is a written value, never an assumption.
- It writes **whenever the camera is open**, grabbing or not. `set_frame_rate()` writes only
  while grabbing, but the restore must also work when the camera has just stopped (pitfall
  P3).
- `_apply_params()`, trigger-Off branch: honours the flag (Enable = not max). A restart
  during the dark calibration therefore keeps the maximum rate. That should never happen,
  because the parameters and Cut/Full are locked, but it is safe if it does.
- Nothing changes in the trigger-On branch. The only thing U11 adds there is that the
  restore runs **before** the trigger is switched back on (pitfall P1).

### 4.2 Where the GUI switches it (`main_window.py`)

- **On**, in `_start_dark_cal()`: after `set_trigger(False)` and the stale-frame flush,
  just before `_set_state(State.DARK_CAL)`.
- **Off, at every exit from `DARK_CAL`:**

| Exit | Where | Test |
|---|---|---|
| N1 frames collected | `_finish_dark_cal()` — restore **first**, then `set_trigger(True)` if it was on | fake camera, trigger On before the run → after the dark calibration Enable = True, rate = set rate, trigger On |
| Stop SCOS / Stop Video during dark calibration | `_abandon_calibration()` under `if dark_pending:` **on its own** — not inside the existing `and self._dark_cal_trigger_was_on` | trigger **Off** before the run → stop mid-dark-calibration → Enable = True (the test that catches the wrong nesting) |
| Camera error (no frames for 10 s) | `_on_camera_error` → Stop Video → `_abandon_calibration` | same as above, driven through the error signal |
| Dark collector error | `_finish_dark_cal` → `_abandon_calibration` | collector raising |
| Window closed mid-dark-calibration | `closeEvent`, before `camera.stop()` | close during `DARK_CAL` → restore called |

### 4.3 What stays fixed — read back, not trusted

After switching on, read back `ExposureTime`, `Gain`, `PixelFormat` and the crop
(`applied_crop`), and log them on the same line as `ResultingFrameRate`. A unit test on the
fake camera checks that switching on and off writes nothing but those two frame-rate nodes.

### 4.4 Full sensor: the GUI must not drown (decision D1)

The operator may not click Cut Image, so the full 1216 × 1936 frame (4.7 MB) is a real
case. Estimates — step 1 measures the real numbers:

- The camera's maximum at full size in Mono12: about 75–125 fps. It is limited by the
  exposure (8 ms → at most ≈ 125 fps) and by USB bandwidth (4.7 MB per frame → ≈ 75–85 fps).
- What the GUI can take at full size: the dark collector alone is 15 ms per frame (D8), plus
  the rest of `_on_scos_frame` and the display. That is about 40–60 fps.
- So the camera outruns the GUI by roughly 20–60 fps. Over a 600-frame calibration
  (≈ 11–15 s) the queue grows to about 250–600 frames ≈ **1.2–2.8 GB** of RAM, unbounded.
  It also would not finish any sooner, because the GUI is the limit.

With the crop (e.g. 611 × 611 = 0.75 MB, collector 1.4 ms per frame) none of this happens:
the GUI keeps up with any rate the camera can reach.

**Recommended (D1 option A): skip surplus frames in the GUI during `DARK_CAL`.** In
`_on_scos_frame`, right after the FPS counter and **before** the intensity stats and
`_to_du`:

```
if state is DARK_CAL and camera.frames_emitted - self._frames_seen > K:   # K ≈ 3
    count it as skipped; return
```

- These are the same two counters `_flush_stale_frames()` already compares, so the frame
  sources need nothing new.
- A skipped frame costs microseconds, so the queue cannot grow. Peak memory ≈ K frames plus
  whatever arrives during one display redraw.
- Skipping a dark frame is harmless. Dark frames are interchangeable samples of the same
  noise, and the collector still gets exactly N1 of them. The skipped ones never reach the
  measurement: `timeVec` and the lost/dropped counts start at the measurement.
- The log reports it once at the end: "Dark calibration: 600 frames in 12.4 s, camera at
  81.2 fps, 412 skipped while the PC caught up".

### 4.5 Playback and mock sources

`folder_camera.py` and `mock_camera.py` get the same `set_max_frame_rate()`. "Maximum" means
**1 / exposure capped by USB bandwidth** (`core.crop.playback_max_rate()`: 76.5 fps for full
Mono12 frames, 125 fps for a 611 × 611 crop at 8 ms) — the limits a real camera has. *(Step 3
first said "no sleep", then 1 / exposure alone; the rehearsal showed the dark folder then
playing at ~146 fps, faster than the rig camera can send full frames, so the USB cap was added
in step 7's run-up.)* CLAUDE.md's list of what a new frame source must provide gets this
method too.

Caveat: on the dev/rig PC a full lab TIFF reads at only about 21 fps. Playback cannot show
the speed-up even with `--cut`, because the TIFF is read whole before it is cut. The
rehearsal therefore checks the **logic** (switched on, restored, queue bounded), not the
timing. The timing is a rig measurement.

### 4.6 Record the dark rate (decision D2)

The U10 precedent (the crop is saved with the results): record, in `Calibration.h5`'s
`dark` group attributes, `camera_frame_rate_hz` (what the camera reported at maximum) and
`duration_s` (first to last collected frame). Then a calibration file shows how it was taken.
*(Step 1: the pylon emulator has no node reporting the resulting rate under either name, so
`camera_frame_rate_hz` may be unavailable — then it is left out, never guessed; `duration_s`
together with `n_frames` always gives the rate the collector actually got.)*

---

## 5. Pitfalls found while planning

- **P1 — restore order changes triggered measurements.** The trigger-On branch of
  `_apply_params()` never writes the frame-rate nodes. If `set_trigger(True)` ran before
  the rate was restored, the measurement would run with Enable = **False**; today it is
  Enable = True at the set rate. The restore therefore writes both nodes explicitly and runs
  **before** the trigger restore (test in 4.2, row 1).
- **P2 — wrong nesting in `_abandon_calibration`.** Its existing guard is
  `dark_pending and _dark_cal_trigger_was_on`. The rate restore must sit under
  `dark_pending` alone, or a run with the trigger Off would leave the camera at its maximum
  rate for the next Start SCOS (test in 4.2, row 2).
- **P3 — the camera keeps its registers.** A Basler camera keeps
  `AcquisitionFrameRateEnable` until power-off. If the app closes mid-dark-calibration
  without restoring it, the next start with the trigger **On** inherits Enable = False.
  Hence the restore in `closeEvent`, and a `set_max_frame_rate()` that writes even when not
  grabbing.
- **P4 — "Camera: N frame(s) lost" during the dark calibration.** At maximum rate USB may
  drop frames at full size. They are not in the results (the snapshot is taken at the
  measurement), but the operator sees a warning. The plan: leave the warning (hiding it
  could hide a real problem) and explain it in `rig_checklist.md`.
- **P5 — sizing K and the skip.** The skip must come before `_to_du` and the percentile
  stats; otherwise the "cheap" skipped frame is not cheap. Size the full-frame case from the
  **whole handler's** time on full frames, measured in step 1, not from the 15 ms collector
  benchmark alone.
- **P6 — the vendor API.** Before relying on it, confirm in the Basler docs and the pylon
  emulator (PYLON_CAMEMU) that, on ace 2 USB:
  - `AcquisitionFrameRateEnable` is writable while grabbing (it is on the rig camera — see
    `set_frame_rate()` — but check the emulator too);
  - `ResultingFrameRate` follows the change;
  - `DeviceLinkThroughputLimitMode` may cap the rate, and we record that rather than change it.

---

## 6. Steps (in order)

| # | Step | Est. |
|---|---|---|
| 1 | **Check and measure, no app code.** (a) Basler docs + pylon emulator: the points in P6. (b) On this PC (it is the rig PC): time the whole `_on_scos_frame` in `DARK_CAL` on full-size and on 611 × 611 frames (offscreen `MainWindow`; extend `tools/bench_dark_collector.py`). Gives the real "GUI can take" numbers for 4.4 and the K. | 30–45 min |
| 2 | **`camera.py`**: `set_max_frame_rate()`, flag in `_apply_params()`, read-back + log. Fake-camera tests: on → Enable False; off → Enable True + rate, written while not grabbing too; nothing else written; a restart keeps the flag. Mutations: restore without the rate; restore only while grabbing. | 45 min |
| 3 | **Playback/mock**: the same method in `folder_camera.py` and `mock_camera.py` (1 / exposure while on). Tests. | 30 min |
| 4 | **`main_window.py`**: switch on/off at every exit (4.2 table, one test per row), the skip (4.4, if D1 = A), the end-of-calibration log line, the `Calibration.h5` attributes (if D2 = yes). Update the `_flush_stale_frames()` docstring (the D8 note asks for it). | 1.5 h |
| 5 | **Rehearsal** (`tools/rehearsal.py`): checks — the source was at maximum during `DARK_CAL`, back to the set rate at `BRIGHT_CAL` and at `MEASURING`, the queue never above K + margin during `DARK_CAL`. Mutations in `tools/rehearsal_mutations.py`: no restore, no skip. Runs: `normal`, `--cut`, `slowdown` — one at a time. | 45 min |
| 6 | **`tools/dark_rate_ab.py`** — the A/B tool of section 3 (20 Hz → max → 20 Hz, same session, ROI comparison, pass/fail printed). Tried on the pylon emulator so it runs on the rig the first time. | 45 min |
| 7 | **Docs**: CLAUDE.md (new camera method in the frame-source list; the "unbounded during DARK_CAL" note → bounded by the skip), `rig_checklist.md` R5d/R5g (the rig checklist's dark-calibration steps: new durations, below) + a new rig step for the A/B tool, `simulation_checklist.md`, `todo.md` (U11 row, Done item), `expectations.md` (one line for Vika). | 45 min |
| 8 | **Rig check (with you):** the A/B tool; a full session cut and uncut; read the dark duration and "N skipped" from `app.log`. | on the rig |

Total before the rig: about 5–5.5 h.

### Step 1 results (2026-10-11)

**1a — vendor API.** No camera attached today, so the Basler docs plus the pylon emulator
(scratchpad script, not kept — the findings are below):

- `AcquisitionFrameRateEnable` can be written **while grabbing** and while stopped. Emulator:
  20 Hz with Enable = True → **58.5 fps** received after Enable = False → **20.7 fps** after
  Enable = True + 20 Hz again. Exposure and gain read back unchanged after each switch.
- **The node that reports the resulting rate is not reliable by name.** Basler's docs use
  `BslResultingAcquisitionFrameRate` for newer cameras and `ResultingFrameRate` for the older
  ones ([Resulting Frame Rate](https://docs.baslerweb.com/resulting-acquisition-frame-rate)).
  The emulator has **neither**. The rig camera does have `ResultingFrameRate`: `get_info()`
  reads it, and on 2026-10-08 the gain-table lookup that calls `get_info()` worked (no "Could
  not read camera info" in that session's `app.log`). → Step 2 reads
  `BslResultingAcquisitionFrameRate`, then `ResultingFrameRate`, and accepts "unknown".
  Nothing depends on that value except the log line and the D2 attribute.
- **`DeviceLinkThroughputLimitMode` is On by default** on the emulator, limit 360 MB/s. Basler:
  when On it "limits the available bandwidth for data transmission to the
  `DeviceLinkThroughputLimit` parameter value", which limits the frame rate too. At 360 MB/s
  a full Mono12 frame (4.7 MB) caps the rate at **≈ 76 fps** — inside the 75–85 fps estimated
  in 4.4. Step 2 logs its mode and value next to the resulting rate; U11 does not change it.
- Docs: Basler's page on the frame-rate switch describes only the enabled case ("the
  camera's maximum frame rate is limited by the value you enter"); the emulator test is what
  confirms that Enable = False removes the limit.

**1b — what the GUI thread can take** (`tools/bench_dark_collector.py --handler`, new; this
PC = the rig PC; offscreen window, so painting a real screen may cost somewhat more):

| Frame | Per frame (collector + labels) | Stats (2 per s) | Display (≤ 30 per s) | GUI can take |
|---|---|---|---|---|
| 1216 × 1936 (full) | 14.1 ms | 22.8 ms | 4.6 ms | **≈ 58 fps** |
| 611 × 611 (cut, rig ROI 2026-10-08) | 1.46 ms | 5.5 ms | 2.0 ms | **≈ 635 fps** |

What this means:

- **Full sensor:** the camera (≈ 76 fps at the bandwidth limit) outruns the GUI (≈ 58 fps).
  Without D1's skip the queue would grow by about 18 frames per second, ≈ 85 MB/s. With the
  skip the calibration takes 600 ÷ 58 ≈ **10–11 s** (≈ 1 s for 60 frames).
- **Cut:** the GUI is ~5× faster than any rate the camera can reach (≤ 125 fps at 8 ms
  exposure). Nothing is skipped, and the camera sets the time: ≈ **5–8 s** for 600 frames
  (≈ 0.5–1 s for 60). The exact figure depends on the exposure and the readout; the rig tells.
- **K = 3** is enough. A stats call (23 ms) at 76 fps lets about 2 frames arrive, so the queue
  peaks around K + 2 ≈ 5 full frames ≈ 24 MB.

### Step 2 results (2026-10-11)

`camera.py`: `set_max_frame_rate(enabled)` (writes whenever the camera is open; `False`
writes Enable = True **and** the rate, in any trigger mode; no restart), flag
`max_frame_rate` honoured by `_apply_params()` and by `set_frame_rate()` (a rate arriving
meanwhile is stored, not written), `resulting_frame_rate()` (Bsl… name, then
`ResultingFrameRate`, else None), and one log line each way. The "on" line holds the
read-back exposure, gain, pixel format, image area and the USB throughput limit.

- `tests/test_camera.py` `TestMaxFrameRate`: 14 tests. Five deliberate mistakes, each caught:
  restore without the rate (5 tests fail), restore only while grabbing (1), `_apply_params`
  ignoring the flag (3), `set_frame_rate` ignoring the flag (1), restore by restarting the
  camera (5). Fast suite 629/629.
- Pylon emulator, the real `CameraThread`: 20.0 fps → **60.0 fps** at maximum → 20.0 fps
  restored. Exposure unchanged throughout; the restore works with the camera stopped too.
  The log on the emulator: "Frame-rate limit off — camera at its maximum (camera does not
  report its rate; exposure 8000.0 µs, gain 0.00 dB, Mono8, 4096 × 4096 at (0, 0); USB
  throughput limit On, 360 MB/s)".

### Step 3 results (2026-10-11)

`folder_camera.py`, `mock_camera.py`: `set_max_frame_rate()` + `_playback_rate()` (1 / exposure
while on, else `frame_rate`), used by the playback loop. Tests: `TestMockCameraMaxFrameRate`
(2), `TestFolderCameraMaxFrameRate` (3, including the 20 Hz control). Mutation "maximum ignored"
caught in both files.

### Step 4 results (2026-10-12)

`gui/main_window.py`:
- `_set_dark_max_rate()` — the one place that calls the frame source. It never raises (it
  runs inside Qt slots); a failed restore is shown in the status bar.
- On in `_start_dark_cal()`, after the trigger switch and the flush. Back first thing in
  `_finish_dark_cal()` (before anything that can fail, and before the trigger), in
  `_abandon_calibration()` under `dark_pending` alone, and in `closeEvent` before the camera
  stops.
- The skip in `_on_scos_frame()` (D1), after the FPS counter and before the statistics and
  `_to_du`, with `_DARK_MAX_BACKLOG = 3`. **Added while writing it:** the backlog is counted
  from the smallest value seen in this calibration. If the camera's count and the window's
  ever differed by a constant, a plain `emitted − seen > 3` would skip *every* frame and the
  calibration would never end; counted from the floor, the offset cancels.
- `Calibration.h5` `dark` attributes `duration_s` and `camera_frame_rate_hz` (the latter only
  when the camera reports one) (D2). A second log line after "Dark calibration complete":
  "Dark calibration took X s; the camera delivered Y fps[, Z fps reported]; N frame(s)
  skipped while the PC caught up". "Delivered" is counted from the camera's own frame count,
  so it is known even when the camera reports no rate.
- `_flush_stale_frames()` docstring: the queue during the dark calibration is now bounded.

`tests/test_dark_max_rate.py`: 18 tests, one per exit in the 4.2 table plus the skip and the
file record. Eight deliberate mistakes, each caught: restore after the trigger, no restore on
finish, the abandon restore nested under the trigger condition, no restore on close, no
backlog floor, skipping in every state, no skip, never switched on. Fast suite 652/652.

### Step 5 results (2026-10-12)

`tools/rehearsal.py`: `check_dark_rate()` in every run. The GUI-side spy samples the frame
source at each arrival, and the checks are: the source was at its maximum during
`DARK_CAL`; it was back at the set rate for every frame in `BRIGHT_CAL` / `MEASURING_INIT` /
`MEASURING`; the backlog did not grow by more than `DARK_BACKLOG_LIMIT` = 20 frames beyond its
value at the first post-flush dark frame; `Calibration.h5` holds `duration_s` and
`camera_frame_rate_hz` (= playback's 1 / exposure); the log has the summary line. The
`slowdown` scenario now also slows every collected dark frame by 0.1 s, so the GUI falls
behind as it would on the rig's full sensor.

**Found while running it:** the dark folder of the lab recording plays at **~146 fps**, not
~21 (its TIFFs read much faster than the recording's), so even `normal` exercises the skip.
For example, 60 frames in 3.8 s with 488 skipped. A first limit of 8 (an absolute backlog
of 8) failed at 23. Two reasons:

1. The window holds the queue at *its smallest backlog seen* + 3, so a backlog already there
   at the first dark frame (16 here, 40 in `slowdown`) is held, not drained — bounded, so not
   a leak, and the price of the floor that keeps a counter offset from stalling the
   calibration.
2. Frames keep arriving during a single slow call (a ~25 ms statistics pass at ~146 fps).

The check therefore measures **growth** beyond the first value (+6 seen, limit +20). On the
rig the backlog at the laser prompt is 2–3 frames (2026-10-08), so the held part is small
there.

| Run (60 + 60 frames) | Result | Dark calibration (from `rehearsal.log`) |
|---|---|---|
| `normal` | all checks pass | 3.8 s, source 146 fps, 488 skipped, queue +6 |
| `normal --cut` | all checks pass | queue +4 |
| `slowdown` | all checks pass | 11.0 s, source 144 fps, 1480 skipped, queue +0 (held at 40) |
| mutation `dark-no-restore` | **fails** "set rate was back…" (1141 frames with the maximum still on) | |
| mutation `dark-no-skip` (`slowdown`) | **fails** "queue did not grow…" (+1402 frames) | |

### Step 6 results (2026-10-12)

`tools/dark_rate_ab.py`: three dark sets A (set rate) → B (maximum) → C (set rate) through
`camera.py` and `DarkCalCollector`, collected on the camera thread (direct connection, no
event loop, nothing queued). It asks for the lens to be capped (`--yes` skips the question),
drops the first 0.5 s after each rate switch, and always puts the set rate back, even after
an error. It prints per set the time, the rate the camera delivered, lost frames and the
ROI means; then the verdict per quantity:

- `|B − (A+C)/2|` must not exceed the larger of `|A − C|` and 3 × the statistical error of
  that difference (N frames over P pixels, pixels taken as independent — a lower bound,
  so a lucky A = C cannot fail B).
- Exit 0 PASS, 1 FAIL (also if exposure, gain or pixel format differ between the sets), 2
  INCONCLUSIVE (the maximum was not ≥ 1.2 × faster, so nothing was compared). `--save` writes
  the numbers as JSON.

Dry run on the pylon emulator (`--emulator --frames 20 --cut --roi 2048 2048 150`): 353 × 353
crop, A 19.8 fps → B 58.3 fps → C 20.5 fps, 0 lost, JSON written. The verdict there is FAIL
and means nothing: the emulator sends a moving test pattern, not dark noise. The decision
rule has its own tests (`tests/test_dark_rate_ab.py`, 6).

### Step 7 results (2026-10-12)

**First, a fix found while measuring for the docs** (commit `6f845aa`): playback's maximum is
now capped by USB bandwidth as well (4.5). Then a fair comparison, 600 + 600 frames, back to
back on this PC:

| Dark calibration in playback | Time | Source delivered | Skipped |
|---|---|---|---|
| at the capped maximum (U11) | 38.9 s | 60.7 fps | 1745 |
| at the set rate (U11 switched off for the comparison) | 32.4 s | 30.7 fps | 388 |
| bright calibration, both runs (not touched by U11) | ≈ 35 s | | |

In playback the maximum rate makes the dark calibration **slower**, not faster. The window is
the limit (≈ 18 collected frames/s here), and a faster playback thread — one TIFF decode per
frame — takes processor time from it. The real camera thread does far less per frame, and
step 1 measured ≈ 58 full frames/s for the window alone, so the rig should still gain. This is
an **assumption until the rig shows it**: `rig_checklist.md` R5g now says what to do if the dark
calibration takes 30 s or longer.

**Docs:** CLAUDE.md (new IMPORTANT note; the queue during DARK_CAL is bounded now),
`rig_checklist.md` (R5d, R5g, R5h, the "still to do" list, new R6g–R6i for the A/B test, what
to bring back), `simulation_checklist.md` ("Before you start", B4), `expectations.md` §2 (for
Vika), `todo.md` (U11 row, Done item 52, D9 — the blocked commit of step 4, order line).

**Expected dark-calibration duration after U11** (estimates; step 1 and the rig replace
them). They depend on the exposure (the maximum rate is at most 1000 ÷ exposure in ms, so
≈ 125 fps at 8 ms), the image size (USB bandwidth and the PC's per-frame work) and the PC:

| Frames (N1) | Today, 20 Hz | Full sensor, max rate | Cut (≈ 611 × 611), max rate |
|---|---|---|---|
| 600 | 30 s | ≈ 10–11 s (the PC is the limit; step 1: 58 fps) | ≈ 5–8 s (the camera is the limit) |
| 60 | 3 s | ≈ 1 s | ≈ 0.5–1 s |

---

## 7. Decisions (all decided 2026-10-11, as recommended)

| # | Question | Options | Recommendation |
|---|---|---|---|
| D1 | Full sensor (no Cut Image): how to stop frames piling up? | **A** skip surplus frames in the GUI during `DARK_CAL` (4.4) · **B** the camera thread holds frames back (same effect, but new cross-thread state that all three frame sources must implement) · **C** maximum rate only when the image is cut; full sensor stays at the set rate · **D** no protection; measure on the rig | **A** — simplest, uses existing counters, bounded memory, full speed-up both ways |
| D2 | Record the dark rate in `Calibration.h5` (`camera_frame_rate_hz`, `duration_s`)? | yes · no | **Yes** — like U10, a file should say how it was taken; one line for Vika in `expectations.md` |
| D3 | A GUI switch to turn the maximum rate off? | yes (checkbox) · no | **No** — Vika says the rate doesn't matter; the A/B tool does the comparison without a GUI control. If the A/B test fails, U11 is not shipped at all. |

---

## 8. Not in U11

- **Moving the calibration collectors off the GUI thread** (tasks 14/16): the bigger fix
  for the queue, and a threading change. Not before the v0 tag.
- **The frame-rate nodes under the trigger On.** Whether Enable should be True or False
  while the Arduino triggers is a separate question. U11 only keeps today's state.
- **The bright calibration**: it stays at the set rate. Vika's request was about dark
  frames only, and the bright frames are taken with the laser on and the subject in place —
  whether their rate may change is her call, not assumed here.
