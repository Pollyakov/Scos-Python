# Rig checklist — the first session on the real camera

Rig-session prep step **5a** (`docs/todo.md`). Written 2026-10-07 against commit `e8dcdc2`; used at
the first rig session on 2026-10-08 — what passed is in "Result of the first session" below.

This is the companion to [`simulation_checklist.md`](simulation_checklist.md). That one
rehearses the whole session on a recording. This one covers what a recording **cannot**
test: the real Basler camera, its clock and lost frames, the external trigger and the
Arduino, the lab PC's speed, and settings left behind on that PC.

How to read each step: **Do** → **Expect** → **If not**. Tick the boxes as you go.
Everything here was written from reading the code, not from a run on the rig — if a step
doesn't match, the checklist may be wrong rather than the app. Note it either way.

Expected values are for the lab camera's default settings — **Mono12, 8 ms exposure, 8 dB,
20 Hz, Window Size 7, Dark/Bright Frames 600**, and the rig camera's full frame, **1216 × 1936**
(rows × columns; 700 × 700 until 2026-10-08, when the rig camera turned out to send its full
sensor) — unless a step says otherwise. Since 2026-10-11 **Cut Image** makes the camera send
only a rectangle around the ROI circle (todo U6); R2g checks it, and from then on the frames
are that size.

> **`app.log` is overwritten every time the app starts** (`main.py:28`, `mode="w"`). It is
> written to the folder you launched from — the repo folder if you follow the commands
> below. **Copy it somewhere safe before every relaunch**, e.g. into that run's session
> folder; most of the checks below are read from it.

---

## Result of the first session — 2026-10-08

Session `TestVika10082026_20261008_145048` (Vika as the subject, 1 min, External Trigger on,
20 Hz, rig camera a2A1920-160umBAS SN 40075248, USB). Checked on 2026-10-09 against that
session's `app.log`, `rBfi_results.h5` and `Calibration.h5`. **Code that ran: `0822db1`**
(`Params/gitCommit`) — before the BlockID fix `dffa06d`, so the current code has not run on
the camera yet. The ☐ boxes in the tables below are left empty so the list can be used
again at the next visit.

✅ checked in the files and OK · 👁 seen at the rig, not in any file · ⚠️ done, but not
the way the checklist says · ➖ not done

| Step | | What the files show |
|---|---|---|
| P1 | ✅ | SN 40075248 is in `CamerasMeasuredGain.csv` at Mono12, 8 dB ("DAN01", G = 0.9564) |
| R1a | ✅ | Ran `0822db1`, the latest commit that day |
| R1b | ➖ | Fast tests on the rig PC — not recorded |
| R1c | 👁 | `check_camera.py` before launch: one camera, a2A1920-160umBAS, SN 40075248, USB (output was in the chat, not saved) |
| R1d | ✅ | `app.log`: "Camera opened — model=a2A1920-160umBAS SN=40075248" |
| R1e | ➖ | Not checked that day. **For the next visit:** the app saved `scos_config.local.json` on close (15:05) with **External Trigger on, Measuring duration 1 min, Norm. type pulsation** — the next launch on this PC starts with those. Dark/Bright Frames are 600 in it |
| R1f | ➖ | Benchmark not run |
| R2a | ✅ | "Mode: real Basler camera", no errors (state-box colour not recorded) |
| R2b | ✅ | Start Video 14:45:33, IDLE → PREVIEW. The FPS label was not recorded; frame timing in the file is 50 ms (R6d) |
| R2c | ✅ | Start Video: Mono12, 8.0 ms, 8.0 dB, 20 Hz; Start SCOS: window 7; both calibrations collected 600 frames |
| R2d | ✅ | 14:45:39, 5 s after capture start: "Camera clock accepted — 1000 MHz ticks agree with the PC clock over 5.0 s". Also on the first frame: "Camera reports no frame numbers (BlockID 0)" — a false alarm (USB cameras number frames from 0), fixed in `dffa06d` (todo Done item 41) |
| R2e | ✅ | No "frame(s) lost" anywhere in `app.log` (the app logs every such warning) |
| R2f | ✅ | ROI saved in `Params`: cx 1220.9, cy 864.8, r 278.5 (Auto or Draw — not recorded) |
| R3a | ✅ | Arduino found on COM10, compiled and uploaded in 10 s: "Arduino ready — T=50 ms, exposure=8 ms on COM10"; "Trigger mode → On" |
| R3b | ⚠️ | Partly: the measurement ran on the trigger at 50 ms per frame (R6d), so the pulses reach the camera. Whether the FPS spinbox jumped — not recorded |
| R3c | ✅ | A second "Camera clock accepted" at 14:46:18, 5 s after Trigger On — and again after each grab restart in the calibrations (4 times in all) |
| R3d | ➖ | Not done (only one upload in the log) |
| R4 | ⚠️ | Different from the checklist, on purpose: Recording name "TestVika10082026"; Measuring duration 1 min; **Norm. type pulsation** (method percentile5 over the first 5 s) instead of "Number of seconds, 5 s". Both are valid settings |
| R5a | ✅ | "G = 0.956400 DU/e (source: table, SN 40075248, Mono12, 8.0 dB)" — exact row, no pop-up |
| R5b | ✅ | "Session folder created: …\TestVika10082026_20261008_145048" |
| R5c | ✅ | Dark prompt answered 14:50:58; trigger switched off for the dark calibration |
| R5d | ✅ | Really dark: `mean_dark` 0.46 DU (0.52 in the ROI) against ⟨I⟩ 113.9 DU with the laser on; 600 frames |
| R5e | ✅ | Bright prompt answered 14:51:59; trigger back on |
| R5f | ✅ | 600 bright frames |
| R5g | ✅ | Dark **30.2 s** (14:50:58.8 → 14:51:29.0), bright **30.1 s** (14:51:59.9 → 14:52:30.0) — the 30 s minimum, so the rig PC keeps up with full 1216 × 1936 frames at 20 Hz. "Flushing 2" / "Flushing 3" |
| R5h | ✅ | "Normalization window closed at 5.0 s … over 101 points"; κ²_corr > 0 in all 1300 frames (mean 0.0081); dropped 0 |
| R6a | ⚠️ | Ran 65 s, not "a few minutes". Within that: no overload, no lost frames, dropped 0. A longer run is still to do |
| R6b | ⚠️ | Only the failure path tested — the laser was left on on purpose: "Laser-off check FAILED — 114.5 DU, expected < 11.4 DU", the warning appeared, Continue kept the data. The normal path (laser really off → passes silently) has not been seen on the rig yet |
| R6c | ✅ | `app.log` is in the session folder |
| R6d | ✅ | `time_source = camera`, `frames_lost_camera = 0`, `frames_dropped_queue = 0`, `camera_sn = 40075248`. 1300 frames; time step median 50.015 ms, min 50.009, max 50.019 — no gaps |
| R6e | ✅ | All three files. `Calibration.h5` is **24.4 MB** — the "≤ 10 MB" in R6e was for 700 × 700 frames; `rBfi_results.h5` 116 KB for 65 s; `rBfi_fig.png` |
| R7 | ➖ | Deliberate overload not run (todo D5 step 3) |

**Still to do on the rig:** R1b, R3d and a run of several minutes (R6a; the longest so far is
2.3 min). *Done on 2026-10-19:* R1f, R6b (laser-off check passed with the laser really off),
R7, and the camera's frame numbers — no "no frame numbers" warning in any of that day's four
`app.log` files. **New since then:** the
camera-side crop, R2g–R2h and R6f (todo U6, written 2026-10-11 — never run on the real camera;
the pylon emulator and the playback rehearsal are all it has seen); the dark calibration at the
camera's maximum frame rate, R5d/R5g/R5h (todo U11, written
2026-10-12 — same: emulator and playback only). **For the next visit use the short sheet
[`rig_trip_U6_U10_U11.md`](rig_trip_U6_U10_U11.md)** (2026-10-15): just these checks, in order,
with `tools/check_session.py` printing each session's numbers.

---

## Before the trip — this can block the whole session

| | Do | Expect | If not |
|---|---|---|---|
| ☐ P1 | Find out **which camera is on the rig and its serial number** (ask Vika, or read the sticker / Pylon Viewer). Then look in `CamerasMeasuredGain.csv` for a row with that **SN** and **nBits = 12** (Mono12). | A row exists. Today the file has Mono12 rows for SN **40513592** (8 dB), **40075248** (8 dB), **40335410** (16, 20, 24 dB) and **24828238** (24 dB). | **No row for that SN at Mono12 → the app will refuse to measure** (step R5). This cannot be fixed at the rig: G[DU/e] must come from the measured table, never a formula (supervisor's ruling, 2026-09-22). Ask Vika for the measured G of that camera before going. |

---

## R · At the rig

### R1 · Before launching

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R1a | In the repo folder: `git log -1 --oneline` | The same commit you tested at home (`e8dcdc2`, or whatever step 4b ends on) | `git pull`, then check again |
| ☐ R1b | `venv\Scripts\python.exe -m pytest tests/ -m "not slow"` | All pass. Same set the pre-commit hook runs. | Note which test failed and the error; don't measure until it's understood |
| ☐ R1c | `venv\Scripts\python.exe check_camera.py` | `Found 1 camera(s): [0] <model>  SN:<number> …` and `Frame grabbed OK: shape=(…), …` | "No Basler cameras found" → cable, Pylon SDK, or Pylon Viewer still holding the camera (close it) |
| ☐ R1d | Compare that SN with P1 | Same SN, and its Mono12 row is in the table | Different camera than expected → redo P1 for this SN now |
| ☐ R1e | Look for **`scos_config.local.json`** in the repo folder | Either absent, or you know what's in it. This file holds the operator's last settings and **overrides the committed defaults** — e.g. Dark/Bright Frames 60 from an old test | Rename it to `scos_config.local.old.json` to start from the defaults; R2c checks the result |
| ☐ R1f | Benchmark, with the app **closed**: `venv\Scripts\python.exe bench_processor.py --width 1936 --height 1216 --window 7 --bits 12 --fps 20` (takes 30 s; width = columns, height = rows) | Last lines: `Budget at 20.0 Hz : 50.0 ms/frame`, **`Headroom … (OK)`**, and a `Max sustainable` figure. **Write down `mean` and `Max sustainable`** — R6 uses them. This is one worker; the app runs several in parallel, so its real capacity is higher. | `Overrun … (GUI will lag!)` → the lab PC is slower than expected. A session can still run (the app slows the camera rather than skipping frames), but tell me the numbers |

### R2 · Launch and preview

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R2a | `venv\Scripts\python.exe main.py` | First the **"Laser safety"** warning (goggles; probe off only after the red laser light is out, by pulling the rubber strap backwards) — read it to the subject, click **I confirm**; `app.log`: "Laser-safety warning confirmed by the operator" (since 2026-10-09; **Exit** closes the app). Then the window opens; status bar "Ready — camera not started"; state box **IDLE** (grey) | Traceback in the terminal → copy it |
| ☐ R2b | Click **Start Video** | Image appears; state box **PREVIEW** (light blue); status bar "Video running"; **FPS ≈ 20** | FPS well below 20 → see R2e |
| ☐ R2c | Look at the parameter boxes | Mono12, 8 ms, 8 dB, 20 Hz, Window Size 7, **Dark Frames 600, Bright Frames 600** | Different values → a `scos_config.local.json` is in play (R1e). Set them by hand |
| ☐ R2d | **About 5 s after** Start Video (the check needs 5 s of frames), open `app.log` and search "Camera clock" | **`Camera clock accepted — … MHz ticks agree with the PC clock`** (todo D5: the app then stamps each frame with the camera's own exposure time instead of the PC's arrival time) | **`Camera clock not used — <reason>`** → the session is still valid (PC timestamps, as before 2026-10-07), but copy the reason |
| ☐ R2e | Watch FPS and the status bar for a minute | FPS steady ≈ 20; **no** "Camera: N frame(s) lost" warning | "Camera: N frame(s) lost — Pylon buffers full or transfer failed (M since Start Video)" in preview already → cable / network adapter / PC load; note N and M |
| ☐ R2f | Set the ROI: **Auto ROI** or **Draw ROI** (drag and resize the circle) | The ROI label shows `cx=… cy=… r=…`; ⟨I⟩ updates | |
| ☐ R2g | Click **Cut Image** (todo U6: the camera itself now sends only the circle plus 26 px on every side) | The image shows **only the area around the circle**, drawn where it was; the button now reads **Full Image**; the Info label reads **`Size : W×H of 1936×1216`** — for the ROI of 2026-10-08 (r ≈ 280) about **611 × 611** (computed from that ROI; a camera that needs steps of 4 makes it 612 × 612). The ROI circle and the ROI buttons are locked. `app.log`: **"Cut Image — asking the camera for W × H at (x, y)"**, then **"Camera image area: W × H at (x, y) of the 1936 × 1216 sensor"** — the second is what the camera really uses; if its numbers differ from the first, the camera rounded them (fine, the app follows the camera). About 5 s later a new **"Camera clock accepted"** (the crop restarts grabbing). **FPS stays ≈ 20.** | "**Camera refused the image crop** … using the full sensor" in a warning / `app.log` → copy the line; the session can still run uncut. "The camera kept the full image" in the status bar → same. FPS drops after the cut → note it |
| ☐ R2h | Click **Full Image**, then **Cut Image** again | Full: the whole sensor again, `Size : 1936×1216`, ROI unlocked; Cut: the same crop as in R2g | |

### R3 · External trigger and the Arduino

Skip if this session runs on the camera's internal frame rate.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R3a | Tick **External Trigger** (the **v** shortcut was removed on 2026-10-15, todo Done item 54) | Status bar "Arduino: connecting…", then **"Arduino: trigger pulses active \| exposure=8.0 ms, FPS=20.0 Hz"**; window title **"SCOS — Trigger ACTIVE (20 Hz, 8 ms)"**. The checkbox is greyed out while the upload runs. | "Arduino Upload" warning pop-up → copy its message; the checkbox unticks itself and the camera stays on internal timing |
| ☐ R3b | Watch | Image keeps updating; **FPS ≈ 20** (now set by the Arduino's pulses); the **FPS spinbox does not jump** to some other value | FPS 0 → pulses not reaching Line2. FPS spinbox jumping → note the values (an earlier bug of this kind was fixed) |
| ☐ R3c | `app.log`, search "Camera clock" again | A **second** "Camera clock accepted" a few seconds after the switch — changing trigger mode restarts grabbing, and the clock check starts over | "not used" → copy the reason |
| ☐ R3d | Change Exposure or FPS by one step, wait 2 s | A new Arduino upload starts by itself (≈ 1 s after the last change) and ends with "trigger pulses active" showing the new values | |

### R4 · Settings for the session

Type a **Recording name**. Measuring duration **∞** (or the planned length), Norm. type
**Number of seconds, 5 s**. Leave **External Trigger** as the session needs it: the dark
calibration switches it off by itself and puts it back afterwards.

### R5 · Start SCOS — the dialogs

The order is the protocol's (`SCOS_protocol.md:11-17`) and is asserted by a test.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R5a | Click **Start SCOS** | **G[DU/e]**, one of three outcomes: **(1)** SN + Mono12 + 8 dB is an exact row → **no pop-up**, straight on. **(2)** SN + Mono12 is in the table but not at 8 dB → **"Estimated G[DU/e]"** warning, "G was rescaled from the closest measured gain (G=… DU/e). SCOS will continue." Click OK. **(3)** Not in the table → **"Can't calculate SCOS: CameraSN … Mono12 was not found in G[DU/e] Calibration file"** — the run does not start. | (3) → P1 was missed. No measurement possible with this camera today |
| ☐ R5b | — | Folder dialog (first run of this window only). Pick the results folder. | |
| ☐ R5c | — | **"Calibration — Step 1 of 2: Dark Frames"** — "Please turn off the laser." Turn the laser **off**, make the area dark, click **OK** | |
| ☐ R5d | Watch | Parameters locked (dark grey); **⟨I⟩ drops clearly** and stays steady; label **"Dark cal: n / 600"**. The camera runs at its **maximum frame rate** for this step (todo U11): the **FPS** label jumps up — ≈ 60–76 with the full image, ≈ 100–125 after Cut Image (estimates) — and drops back to 20 when the step ends. Expected **≈ 10–11 s with the full image, ≈ 5–8 s after Cut Image** (estimates from the PC benchmark; it was 30 s before U11). With Dark Frames 60: ≈ 1 s. If the trigger was on, it is switched off for this step. | ⟨I⟩ does not drop → light still reaches the sensor; Stop SCOS and start again (otherwise the run aborts later, R5h) |
| ☐ R5e | — | **"Calibration — Step 2 of 2: Bright Frames"** — "Please turn on the laser. Keep the subject in the measurement area. … The measurement starts automatically when this calibration ends." Turn the laser **on**, **leave the subject where it is**, click **OK** | |
| ☐ R5f | Watch | ⟨I⟩ back up; **"Bright cal: n / 600"**, ≈ 30 s again; then **"Cal OK — dark+bright done, saved Calibration.h5"**. "Discarding N buffered frames…" may flash first — normal. | |
| ☐ R5g | **`app.log`**: how long each calibration took. Dark = the line **"Dark calibration took X s; the camera delivered Y fps, Z fps reported; N frame(s) skipped while the PC caught up"** (todo U11), and the **"Frame-rate limit off — camera at its maximum (…)"** line just before it (exposure, gain, image area and the USB limit as the camera reports them); bright = from **"State: PREVIEW → BRIGHT_CAL"** to **"Bright calibration complete"**, compare with 600 ÷ 20 Hz = **30 s** | Dark **≈ 10–11 s** with the full image (N in the hundreds — normal, see U11) or **≈ 5–8 s** cut (N ≈ 0); exposure and gain in the "limit off" line = the session's. Bright about 30–35 s | **Dark 30 s or longer** → the maximum rate does not help on this PC (in playback the frame source competes with the GUI for the processor; the real camera should not) — copy both lines and tell me. Exposure or gain different in the "limit off" line → **stop**, copy it. Bright clearly longer than 35 s → the collector is slower than the camera. Write all times down. Don't judge by "Flushing N" in the log: the open pop-up keeps draining frames, so N stays small even on a slow PC (N was 2 in the hand-run playback, where dark took 65 s against a 15 s minimum) |
| ☐ R5h | Don't touch anything. At "Cal OK" the app goes **straight** into the measurement, with no pop-up, and the **normalization baseline (the first 5 s) starts at that moment** — so the subject must already be in place and still, which it is when it stayed there for the bright calibration. Watch | "Normalizing — t / 5 s (… s left)", plot empty; then **"Normalized ✓"** and the plot fills in. κ² positive. **"Dropped: 0"**. FPS ≈ 20 — **back at the set rate** after the dark calibration (U11); `app.log` has "Frame-rate limit back on — 20.0 Hz". | **"Corrected κ² Is Negative"** → the dark calibration saw light (R5d). **FPS far above 20** → the rate was not put back: Stop SCOS, copy `app.log`, tell me |

### R6 · Run, stop, and check the file

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R6a | Let it run a few minutes | FPS ≈ 20 the whole time; **no** "SCOS overload" message; **no** "Camera: N frame(s) lost"; Dropped stays 0 | An FPS below 20 with no warning is itself worth noting — copy the time |
| ☐ R6b | **Stop SCOS** → "Measurement Ended — Please turn off the laser." Turn it off, OK | No warning; status bar **"Session finished → <folder>"**; then the **green** window **"Laser is off — you may remove the probe"** (since 2026-10-09): check the laser's red indicator light is not lit, remove the probe by pulling the rubber strap backwards, OK | The **red "LASER MAY STILL BE ON"** window → is it? **Check again** measures again; **Continue anyway** keeps the data and is followed by a red "do NOT remove the probe yet" window. An **amber** "could not run" window → copy the closing status message |
| ☐ R6c | **Copy `app.log`** into the session folder | — | |
| ☐ R6d | Check the results file — simplest with `venv\Scripts\python.exe tools/check_session.py "<session folder>"` (2026-10-15; prints these values with OK / NOT OK / CHECK), or with: `venv\Scripts\python.exe -c "import h5py,numpy as np,sys; f=h5py.File(sys.argv[1]); print(dict(f['metadata'].attrs)); t=f['timeVec'][()].ravel(); d=np.diff(t)*1000; print(t.size,'frames; dt ms median %.2f min %.2f max %.2f' % (np.median(d), d.min(), d.max()))" "<session folder>\rBFi_resultsAndParameters.h5"` | In `metadata`: **`time_source = camera`**, **`frames_lost_camera = 0`**, **`frames_dropped_queue = 0`** (both counted from Start SCOS), and `camera_sn` = your camera. Time steps: **median ≈ 50 ms**, min and max close to 50 ms | `time_source = pc` → R2d said why. Any lost/dropped > 0, or a max step of 100, 150 ms… → frames went missing; note the numbers |
| ☐ R6e | Folder contents | `Calibration.h5` (≈ 25 MB at 1216 × 1936 — 24.4 MB on 2026-10-08; **much smaller when cut** — it scales with the crop's area, so ≈ 4 MB for a 611 × 611 crop, estimated from the area), `rBFi_resultsAndParameters.h5` (`rBfi_results.h5` before 2026-10-11; ≈ 3.5 MB per hour, an estimate not yet checked on a long run; 116 KB for 65 s on 2026-10-08), `rBfi_fig.png` | |
| ☐ R6f | *(Only if R2g was used.)* In the results file, `metadata` → **`crop`** has `OffsetX`, `OffsetY`, `Width`, `Height` = the "Camera image area" line of R2g, and `SensorWidth` 1936, `SensorHeight` 1216 (todo U10); `rBfi_fig.png` shows the same under "Image area". Compare the session's **κ²_corr** with the uncut session of 2026-10-08 (mean 0.0081, same subject and settings) | Same range. The crop itself does not change κ² (`tests/test_crop_accuracy_offline.py`: identical over the same ROI), but two runs never measure the same frames — two uncut playback runs differed by 3 %. A different subject, probe placement or ROI changes it more | Far outside (e.g. 2× or ½×) → copy both numbers and the ROI of each session |

### R7 · Deliberate overload — **last, on a throwaway session**

Purpose: see the lost-frame counting work on the real camera (todo D5, step 3). It changes
settings that the app **saves on close** — see R7f before closing the window.

**Run on 2026-10-19 — result in todo D5:** the rig camera (76.5 fps at most on the full sensor,
USB-limited) could not outrun this PC; one throttling warning, 0 lost. Two things learned:
- **R1f's "Max sustainable" is for the benchmark's ROI** (r = 364). Processing time follows the
  ROI's bounding box, not the frame: with the session's ROI (r = 243) one frame took 4.9 ms, not
  12.3 — so compare against the ROI you actually use.
- **With External Trigger off, the Arduino keeps flashing the laser** (8 ms every 50 ms), so only
  ≈ 20 % of the frames are lit. Fine for this test, which is about frame counts, not κ²; never
  measure with the trigger off (todo F3).

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R7a | External Trigger **off**. Set **Processing workers = 1**, **Exposure 2 ms**, and FPS to about **twice R1f's "Max sustainable"** (or the highest the camera accepts) | FPS label rises above "Max sustainable". The camera itself caps the rate (≈ 1 ÷ exposure, and the USB link), so if FPS won't go higher, use what you get | FPS stays at or below "Max sustainable" → this PC can't be overloaded this way; skip R7 and note it |
| ☐ R7b | Start SCOS, go through the prompts. **Keep Dark/Bright Frames at 600** (the protocol's value). *With fewer, the calibrations can end inside the camera-clock check (5 s after a camera restart); since 2026-10-14 (todo D10) the measurement then waits for it — `app.log`: "Measurement starts after the camera-clock check — N frame(s) captured during the check were not used" — so the first point appears up to ≈ 5 s late; the data stay correct* | During the run: status bar **"SCOS overload — input queue N/20 full; camera capture is being throttled to keep up (at HH:MM:SS)"** | |
| ☐ R7c | Keep it going ~30 s | **"Camera: N frame(s) lost — Pylon buffers full or transfer failed (M since Start Video)"**, at most once a second; the label reads **"Dropped: N + M lost at camera"**; the **GUI stays responsive** (the grab loop waits, the window doesn't freeze) | GUI freezes → note how long. No lost-frame warning although FPS shown < FPS set → copy both numbers |
| ☐ R7d | Stop SCOS, copy `app.log` | — | |
| ☐ R7e | R6d's command on this session's file | `frames_lost_camera` > 0. The large time steps are **whole multiples** of the frame period (a lost frame leaves a gap of its true size; frames are not bunched together) | Steps that are *not* multiples, or bunches of very short steps → copy the output. `check_session.py` compares with the **set** FPS: if it was above what the camera delivered, its time-step and dark-rate rows say NOT OK although the steps are fine — judge the steps against the camera's period (todo, Pending decisions) |
| ☐ R7f | **Before closing the window:** set Processing workers (3), Exposure (8 ms) and FPS (20 Hz) back. Close, relaunch, check R2c | Defaults back | Otherwise the next session starts at the overload settings — the app remembers the last settings for the real camera |

### R8 · Save Frames — a short session (written 2026-10-21, [`Plan_Frame_Saving.md`](Plan_Frame_Saving.md))

Every frame of the run as a TIFF, for Vika to rerun her MATLAB script on. Sizes at Mono12:
a full 1936 × 1216 frame is 4.7 MB (≈ 5.7 GB per minute at 20 Hz), a 672 × 667 cut 0.9 MB
(≈ 1.1 GB per minute); the 600 + 600 calibration frames add 5.6 GB full, 1.1 GB cut. Measured
on this PC: the disk writes ≈ 200 full frames per second, so nothing should be skipped.

| | Do | Expect | If not |
|---|---|---|---|
| ✅ R8a | Cut Image, Dark/Bright **600**, **Measuring duration 1 min** (unlimited counts as 4 h and is refused on the full frame), tick **Save Frames**, Start SCOS | No "Not Enough Disk Space". `app.log`: "Save Frames: N frames of W×H need X GB (… with the margin); … GB free" and "Saving frames → …\Frames\expT8ms_Gain8dB_FR20Hz_BL100DU" — the **BL100** is the camera's black level | "Not Enough Disk Space" → the numbers it shows; "Cannot Save Frames" (path too long) → choose an output folder nearer C:\ |
| ✅ R8b | Let it run to the end; watch the status bar | **No** "frame(s) could not be saved" | Copy the line and the time |
| ✅ R8c | In the session folder | `Frames\` with three folders and `frame_times.csv`; `…_dark` and `…_bright` hold **600** files each, the measurement folder ≈ 1200 (20 Hz × 60 s, plus the normalization seconds). `check_session.py` as usual; in the results file `metadata`: `frames_saved_*` = those counts, `frames_not_saved_*` = 0. Then `venv\Scripts\python.exe tools/check_saved_frames.py "<session folder>"` (≈ 1 min): 0 NOT OK | Copy the counts / the whole output |
| ☐ R8d | *(At home, or Vika.)* Her script on the measurement folder: `SCOSvsTime_WithNoiseSubtraction_Ver2('<…>\Frames\expT8ms_Gain8dB_FR20Hz_BL100DU', [], 7, false, mask)` with `mask = logical(h5read('<session>\Calibration.h5','/dark/mask'))'` (transposed: h5read returns it the other way round); then `venv\Scripts\python.exe tools/compare_with_matlab.py "<session folder>"` | All frames matched, "the same" order; raw κ² within ≈ 0.5 %; corrected κ² close but not identical — her spVar comes from the first 600 measurement frames, the app's from the bright frames | Copy the whole output |

**Rig, 2026-10-22 — R8a–c passed** (camera a2A1920-160umPRO, SN 40513592; session
`TestKsenia\scos_20261022_165057`, cut 500 × 495, 1 min, 1.2 GB): "Save Frames: 2400 frames of
500x495 need 1.2 GB (6.4 GB with the margin); 173.5 GB free"; saved dark 600, bright 600,
measurement 1301, not saved 0 (the dark ones at 124 fps); `check_session.py` 0 NOT OK, time steps
50.01 ms, κ²_corr ≤ 0: 0 of 1300. Files: uint16, uncompressed, 12-bit values unshifted, longest
path 173 characters; all 1300 `timeVec` points have their file; file times rise in every folder;
dark/bright means rebuilt from the saved files = `Calibration.h5` (≤ 6 × 10⁻⁶ DU); the app's κ²
recomputed from the saved files = the results file exactly (now `tools/check_saved_frames.py`: 20 OK,
0 NOT OK, 77 s). Full-frame Save Frames not run on the
rig. R8d (Vika's script) open — message sent with the path. Light weak that day: ⟨I⟩ ≈ 33 DU
(≈ 114 on 2026-10-08, same probe and settings).

---

## What to bring back

- every `app.log` you copied (R6c, R7d);
- R1f's benchmark output;
- the two calibration durations from R5g, and the two dark-calibration lines;
- R6d's printout for the real session and R7e's for the overload one;
- R8's session folder with its `Frames` (for Vika's check);
- anything that didn't match, with the time it happened.
