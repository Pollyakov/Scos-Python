# What you'll see in a session — and why it's normal

For Vika, before the first session on the rig. Rig-session prep step **5b**
(`docs/todo.md`). Written 2026-10-07 against commit `e8dcdc2`.

This is a walk through one SCOS session in the Python app, in the order things happen,
with the moments that might look like a problem but aren't. Numbers are for the lab
camera's default settings — **Mono12, 8 ms, 8 dB, 20 Hz, 700 × 700, window 7, 600 dark and
600 bright frames** — unless stated otherwise.

> The main sequence below (start, both calibrations, measurement, stop) was rehearsed by
> hand on a recorded session on 2026-10-07; the cancel and stop-early paths were tested
> automatically only. The bright-calibration prompt was changed on 2026-10-08 (section 3).

---

## 1. Before the laser goes off

**When the app starts** (real camera only, since 2026-10-09) a **"Laser safety"** warning comes
first, before the main window: the subject wears laser-safety goggles for the whole
measurement; the probe comes off only after checking that the laser is off — its red
indicator light not lit — and only by pulling the rubber strap backwards. **I confirm**
opens the app; **Exit** closes it. The confirmation is written to `app.log`.

Then press **Start SCOS**. Three things follow, always in this order, because your protocol puts
them in this order (`SCOS_protocol.md`, section 0 onwards) and everything that needs the
keyboard should be done before the room is dark:

1. **G[DU/e] from the measured table** — the same `CamerasMeasuredGain.csv` that `LoadG.m`
   reads, keyed on the camera's serial number and bit depth.
   - Exact match → no pop-up at all.
   - Camera and bit depth in the table but not at this gain → a warning, **"Estimated
     G[DU/e]"**, saying G was rescaled from the closest measured gain, with the value. The
     session continues. This is your rule from 2026-09-22.
   - Camera or bit depth not in the table → **"Can't calculate SCOS: CameraSN … Mono… was
     not found in G[DU/e] Calibration file"**, and the session does not start. There is no
     fallback to a formula.
2. **Where to save** — a folder dialog, the **first time only** in each app window. Every
   later Start SCOS reuses that folder. Each run gets its own subfolder named from the
   **Recording name** field plus a timestamp, e.g. `rat3_baseline_20261009_141502`
   (spaces and characters Windows doesn't allow become `_`; with no name it's
   `scos_<timestamp>`). A second run never writes into the first run's folder.
3. **"Please turn off the laser."**

## 2. Dark calibration (600 frames)

- The parameter boxes turn dark grey: they are locked until the run ends.
- ⟨I⟩ drops to the camera's black level and the label counts **"Dark cal: n / 600"**.
  The live image keeps running, but on a dark sensor nothing much changes on screen —
  **only the counter moves. That is normal.**
- At 20 Hz it takes **at least 30 s** (600 ÷ 20). On the lab PC we expect about 30–40 s;
  it will be measured on the day.
- If the external trigger was on, the app switches it off for this step and afterwards
  puts it back to what it was before (the protocol says "set it ON"; the app restores the
  operator's choice, which is ON in a triggered session).
- You may see **"Discarding N buffered frames…"** for a moment after clicking OK. Those are
  frames the camera captured **before** you clicked OK — while the laser may still have
  been on. They are thrown away so they can't enter the calibration. Normal, and a good
  sign rather than a bad one.

## 3. Bright calibration (600 frames)

- **"Please turn on the laser. Keep the subject in the measurement area."** The bright
  frames are taken **with the subject in place** — the same way the MATLAB reference's
  `smoothingCoefficients.mat` was made, from the recording itself. On a recorded session
  this reproduces your corrected κ² to within about 1 %.
- ⟨I⟩ rises again; **"Bright cal: n / 600"**, about 30 s; then **"Cal OK — dark+bright
  done, saved Calibration.h5"**. "Discarding N buffered frames…" can appear here too, for
  the same reason (frames taken before the laser was back on).
- **There is no pause after "Cal OK".** The measurement — and with it the normalization
  baseline — starts at that moment, with no further pop-up; the prompt says so. Since the
  subject stayed in place for the bright calibration, nothing needs to move.

## 4. Normalization window and measurement

- **"Normalizing — t / 5 s (… s left)"**, and the plot stays **empty** for those 5 s.
  rBFi = BFi / (mean BFi of the first n seconds), so nothing can be drawn until the window
  closes.
- Then **"Normalized ✓"**, and the plot fills in **including** the first 5 s.
- The plot redraws **once a second**. The live image during the measurement updates only
  **every 2.5 s** — on purpose, to keep the PC's time for the κ² computation. The FPS, κ²
  and ⟨I⟩ numbers keep updating.
- After **120 s** the x-axis switches from seconds to minutes (as in your script).
- With a finite Measuring duration, a **"⏱ m:ss remaining"** countdown appears after
  normalization and the run stops itself at 0:00. Setting the duration to 1 minute or less
  offers to switch normalization to "Pulsation lower level", as the protocol says.

### Messages you may see during a run

| Message | Meaning |
|---|---|
| **"Dropped: 0"** (always shown) | Frames that were captured but never processed. Should stay 0. |
| **"Dropped: N + M lost at camera"** | M frames never reached the PC at all — the camera's buffers were full or a transfer failed. Counted from the camera's own frame numbers. |
| **"Camera: N frame(s) lost — Pylon buffers full or transfer failed (M since Start Video)"** | Same thing, as it happens, at most once a second. |
| **"SCOS overload — input queue N/20 full; camera capture is being throttled to keep up (at HH:MM:SS)"** | The PC fell behind for a moment. Instead of skipping frames, the app slows the intake. The time says *when* it happened — the message stays up until another replaces it, so it is a record of an event, not necessarily the current state. |

None of these is expected on the lab PC at 700 × 700, 20 Hz — an estimate from the
frame size; the lab PC's speed is measured on the day. If they do appear, the session is
still saved and the counts are written into the results file (section 7).

### If the run stops by itself with "Corrected κ² Is Negative"

If **every** frame in the first seconds gives corrected κ² ≤ 0, no BFi can be computed and
the app stops the run with this message. It almost always means light reached the sensor
during the dark calibration (laser still on, room light, OK clicked too early). Data
collected so far is kept. Darken the area and press Start SCOS again.

## 5. Stopping

- **Stop SCOS** → **"Measurement has ended. Please turn off the laser."** Turn it off, OK.
- The app then takes **one** new frame and checks that the mean ROI intensity
  (dark-subtracted) fell by at least **90 %** compared with the mean of the **last 5 s** of
  the measurement — the three points you confirmed on 2026-10-07.
  - Passed → nothing more is shown.
  - Failed → **"Laser May Still Be On … (measured: … DU, expected: < … DU). Continue
    anyway?"** **Yes** finishes and saves as normal; the closing message then carries
    "LASER-OFF CHECK FAILED" with both numbers. **No** throws nothing away — it lets you
    switch the laser off and checks again.
  - Skipped (says so in the closing message) when no frame arrived within 2 s, or the run
    stopped before any result.
  - *This is what is built now. Your open question 9 — what the check should protect, and
    whether its result should also go into the results file — is still waiting for your
    answer.*
- The status bar ends on **"Session finished → <folder>"**.

### Stopping early

- **During the normalization window** (e.g. at 3 s of 5): the run is normalized on whatever
  data exists — your answer to question 11. The label says **"Normalized on 3.0 s (stopped
  early)"**, and the file records the window **actually used** (3.0 s), not the 5 s that was
  set. Because the run is under 120 s, the method is the 5th percentile, as in your script.
- **During a calibration** (Stop, or Cancel at a laser prompt): everything unlocks and you
  can start again. The run's folder is deleted if it is still empty; if it already holds a
  dark-only calibration it is kept and renamed **`<name>_cancelled`**.

## 6. Normalization rule

As in `SCOSvsTime_WithNoiseSubtraction_Ver2.m`: total length (normalization window
included) **over 120 s → mean** of the first n seconds; **120 s or less → 5th
percentile**. "Pulsation lower level" always uses the 5th percentile. The baseline is fixed
from the start of the run for the whole recording (your answer to question 15).

## 7. What's in the folder

| File | Contents |
|---|---|
| `rBfi_results.h5` | `startTime`, `timeVec`, `rBFi`, `Intensity` (mean over the ROI, DU), `Params`, and also `k2_raw`, `k2_corr`, `bfi`. About 3.5 MB per hour. |
| `Calibration.h5` | one file, two groups: `dark` (`mean_dark`, `var_dark`, `mask`) and `bright` (`spIm`, `spVar`), each with `n_frames`. ≤ 10 MB at 700 × 700. |
| `rBfi_fig.png` | the plot as it was at the end (PNG for now — an interactive version is on the list, your answer to question 7). |

`Params` has ten fields: `frameRate`, `exposureTime`, `gain`, `windowSize`, `ROI`,
`bitDepth`, `normalizationConstant`, `normalizationMethod`, `normalizationWindowSec`,
`gitCommit` (the exact code version that produced the file). **No `satCapacity`
anywhere.**

### New since you last saw the format: the `metadata` group

| Field | Meaning |
|---|---|
| `camera_sn`, `camera_model`, `gain_du_per_e`, `gain_source` | which camera, the G value used, and where it came from — `table` (also when it was rescaled from the closest measured gain; whether it was rescaled is not recorded separately, it shows only as the warning at Start SCOS and in `app.log`) |
| **`time_source`** | **`camera`** — each `timeVec` value is the camera's own hardware timestamp at the start of exposure. **`pc`** — the PC clock when the frame was retrieved. The camera clock is used only after it has agreed with the PC clock within 1 % over the first 5 s; otherwise the app stays on the PC clock and logs why. Both are valid; `camera` is more exact under load. |
| **`frames_lost_camera`** | frames the camera took that never reached the PC (the "lost at camera" count) |
| **`frames_dropped_queue`** | frames that reached the PC but were not processed (the "Dropped" count) |

Both counts are from Start SCOS and should be **0**. If not, `timeVec` still has the true
time of every frame that *was* processed, so the gaps are visible in the data rather than
hidden.
