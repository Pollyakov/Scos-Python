# Rig trip — U6, U10, U11 (and D10)

A short sheet for the next visit to the rig. It covers only what is **new since the first
session (2026-10-08)** and has never run on the real camera:

| Task | In one line | Proven so far |
|---|---|---|
| **U6** | "Cut Image" makes the **camera itself** send only the ROI circle + 26 px on every side; both calibrations and the measurement use that crop | pylon emulator + playback rehearsals |
| **U10** | The crop (`OffsetX`, `OffsetY`, `Width`, `Height`, sensor size) is saved in the results file and `Calibration.h5` | tests + rehearsals |
| **U11** | The dark calibration runs at the camera's **maximum frame rate** (exposure and gain unchanged), then the set rate comes back | emulator + playback; **the accuracy proof (A/B) needs the real camera** |
| **D10** | After short calibrations the measurement waits for the camera-clock check, so `timeVec` uses one clock | unit tests only (playback has no camera clock) |

Each step: **Do → Expect → If not**. The IDs in brackets (R2g…) are the matching rows of the
full [`rig_checklist.md`](rig_checklist.md), which has more detail. Expected values are for
the lab settings — **Mono12, 8 ms, 8 dB, 20 Hz, Window Size 7, Dark/Bright Frames 600** —
and the ROI of 2026-10-08 (r ≈ 280). The rig camera is SN 40075248 (USB, sensor 1936 × 1216);
it is in the gain table, so no G check is needed this time.

> **`app.log` is overwritten at every launch.** Copy it into the session folder before
> closing the app — the checks below read it from there.

**Time needed:** ≈ 1 h for parts A–D; part E is optional.

**Bring:** the lens cap (part D), a subject (Vika or anyone, ≈ 10 min in place), this sheet.

---

## A · Before launching (≈ 5 min)

| | Do | Expect | If not |
|---|---|---|---|
| ☐ A1 | In the repo folder on the rig PC: `git pull`, then `git log -1 --oneline` | The latest commit from home (the one that adds this sheet, or later) | Pull failed → note the error; don't run old code |
| ☐ A2 | `venv\Scripts\python.exe -m pytest tests/ -m "not slow"` | All pass (≈ 1 min) | Copy the failing test's name and error; don't measure until it is understood |
| ☐ A3 | `venv\Scripts\python.exe check_camera.py` | `SN:40075248`, `Frame grabbed OK` | "No Basler cameras found" → close Pylon Viewer, check the USB cable |
| ☐ A4 | Look for `scos_config.local.json` in the repo folder (R1e) | Probably there, from 2026-10-08: **External Trigger on, duration 1 min, Norm. type pulsation** — the app starts with those | Fine either way — check the settings in B2 |

## B · Session 1 — cut image, full calibrations (U6 + U10 + U11) (≈ 15 min)

| | Do | Expect | If not |
|---|---|---|---|
| ☐ B1 | `venv\Scripts\python.exe main.py`, confirm the laser-safety warning, **Start Video** | PREVIEW, FPS ≈ 20 | |
| ☐ B2 | Check the settings | Mono12, 8 ms, 8 dB, 20 Hz, Window 7, **Dark 600, Bright 600**. Recording name e.g. `rig_cut_600`, duration **3 min**, Norm. type as you prefer. External Trigger as usual (on, if the Arduino is used) | Set them by hand |
| ☐ B3 | Wait 5 s; in `app.log` search **"Camera clock"** | `Camera clock accepted — 1000 MHz ticks agree …`. **No** "Camera reports no frame numbers" warning (fixed after the first session) | "not used" → copy the reason; the session is still valid |
| ☐ B4 | Set the ROI (**Auto ROI** or **Draw ROI**) | ROI label `cx=… cy=… r=…` — **write it down** (needed in D2 and D3) | |
| ☐ B5 | **U6** · Click **Cut Image** (R2g) | Only the area around the circle; the button reads **Full Image**; Info label **`Size : W×H of 1936×1216`**. For r ≈ 280: **611 × 611** if the camera allows steps of 1, **612 × 612** for steps of 4 (616 for 8). `app.log`: "Cut Image — asking the camera for …", then **"Camera image area: W × H at (x, y) of the 1936 × 1216 sensor"** — that one is what the camera really uses. ≈ 5 s later another "Camera clock accepted". **FPS stays ≈ 20** | "**Camera refused the image crop**" → copy the line; the rest of the sheet still works uncut (skip D3) |
| ☐ B6 | **Full Image**, then **Cut Image** again (R2h) | Full: `Size : 1936×1216`, ROI unlocked. Cut: the same crop as in B5 | |
| ☐ B7 | **Start SCOS** → folder → "Please turn off the laser": laser **off**, OK | Dark calibration starts | |
| ☐ B8 | **U11** · Watch the dark calibration (R5d) | ⟨I⟩ drops; **FPS jumps to ≈ 100–125** (the camera's maximum for 8 ms after the cut); "Dark cal: n / 600" finishes in **≈ 5–8 s** (it was 30 s before U11). These numbers are estimates — the real ones are what this trip measures | ⟨I⟩ does not drop → light on the sensor; Stop SCOS, start again. **"Frame Rate Not Restored"** window → stop, copy `app.log`, tell me |
| ☐ B9 | "Please turn on the laser": laser **on**, subject in place, OK | Bright calibration ≈ 30 s at **FPS ≈ 20**, then the measurement starts by itself | |
| ☐ B10 | Watch the measurement (R5h) | "Normalizing …" then "Normalized ✓", plot fills in; **FPS ≈ 20** (back at the set rate); "Dropped: 0" | **FPS far above 20** → the rate was not put back: Stop SCOS, copy `app.log`, tell me |
| ☐ B11 | Let it run to the end (3 min) or **Stop SCOS**. "Please turn off the laser": laser **really off**, OK (R6b) | **Green** window "Laser is off — you may remove the probe" — this path has not been seen on the rig yet (on 2026-10-08 the laser was left on on purpose) | Red window with the laser really off → copy the DU numbers it shows |
| ☐ B12 | **Copy `app.log` into the session folder** | — | |
| ☐ B13 | `venv\Scripts\python.exe tools/check_session.py "<session folder>"` | See the list below | Copy the whole output |

**What B13 prints.** Every line starts with a verdict:

| Verdict | Means | What to do |
|---|---|---|
| `[OK    ]` | a hard rule holds (e.g. the time steps are 50 ms, the crop is the same everywhere) | nothing |
| `[NOT OK]` | a hard rule is broken | copy the whole output, tell me (and Vika if it is about U11) |
| `[CHECK ]` | there is no hard rule — an estimate, a value that depends on the subject, or something that is fine in some sessions | compare by eye with the list below |
| `[-     ]` | information only | — |

The last line is **`Summary: N OK, M CHECK, K NOT OK`** — for session 1, **K = 0**. The exit code
is 1 when anything is NOT OK.

What it checks as a hard rule (OK / NOT OK):
- `time_source camera`; `frames_lost_camera 0`, `frames_dropped_queue 0`;
- time steps: median = 1000 / frame rate (50 ms at 20 Hz), no step ≥ 1.5× (a gap) or ≤ 0.5×
  (bunched), and the **first step** ≈ 50 ms too (D10);
- `k2_corr <= 0` in none of the points;
- dark and bright arrays have the crop's size, and the results file, dark and bright share
  **one image area** (U6, U10);
- **dark duration shorter than at the set rate** (600 frames at 20 Hz = 30 s) and the camera's
  reported rate above 20 fps (U11);
- **exposure and gain in the "Frame-rate limit off" line = the session's** (the dark noise
  depends on them — the one accuracy-critical log check);
- log lines: "Camera clock accepted" OK; "frame(s) lost", "Laser-off check FAILED",
  "frame rate not restored" NOT OK.

What stays CHECK — compare by eye:
- `k2_corr mean` — in the range of 2026-10-08 (0.0081, same rig): not identical (different
  frames, maybe another subject or ROI), but not 2× or ½×;
- `mean_dark - black level` — what the dark calibration reads above the offset the camera adds to
  every pixel (`camera black level`, recorded since 2026-10-19; the rig camera reported **100**
  on 2026-10-19 — not yet checked whether it survives a power cycle). Expected near 0 with the usual probe (inferred from 2026-10-08, not
  yet measured at black level 100); light leaking in during the dark calibration raises it (12.7 DU with a leakier test probe on 2026-10-19). No lens-capped value
  measured yet. Sessions recorded before then show plain `mean_dark` — 0.46 DU on 2026-10-08,
  when the black level was 0;
- `image area (crop)` — = the "Camera image area" line of B5;
- dark duration against the estimate: ≈ 5–8 s cut, ≈ 10–11 s full;
- `dark frames` / `bright frames` other than 600 (a short test, part E);
- `time_source pc` — the session is valid, the reason is in the "Camera clock not used" line.

**Write down:** dark X s and Y fps; bright time (from "State: PREVIEW → BRIGHT_CAL" to "Bright
calibration complete").

## C · Session 2 — full image, full calibrations (U11 without the cut) (≈ 10 min)

The same as B, but click **Full Image** before Start SCOS (B5 skipped), recording name e.g.
`rig_full_600`, duration 1–2 min. Expect:

| | Expect | If not |
|---|---|---|
| ☐ C1 | Dark calibration: FPS ≈ **60–76** (the USB link is the limit at the full size), **≈ 10–11 s**; "Dark calibration took …" says N frames skipped — **hundreds is normal** here (the PC cannot take every frame at that rate; the collector still gets 600) | **30 s or longer** → the maximum rate does not help on this PC; copy both log lines and tell me |
| ☐ C2 | `check_session.py`: `Summary: … 0 NOT OK`; image area `1936 x 1216 at (0, 0) of the 1936 x 1216 sensor` | Any NOT OK → copy the output |

## D · A/B accuracy test of U11 — lens capped, app closed (≈ 5 min)

This is the proof that the maximum frame rate gives **the same dark calibration**. Until it
passes, U11 is not done.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ D1 | Laser **off**, **lens cap on**. Close the app and Pylon Viewer (R6g) | — | |
| ☐ D2 | `venv\Scripts\python.exe tools/dark_rate_ab.py --roi CX CY R --save "<session 1 folder>\ab_full.json"` — CX CY R from B4; add `--exposure-ms`, `--gain-db`, `--fps` only if they were not 8 / 8 / 20. Press Enter at its question (R6h) | Takes ≈ 30 + 10 + 30 s. Three lines `set A / B / C` — B's "camera delivered" clearly above A's and C's (≈ 60–76 vs 20). Last line **"PASS — the maximum rate gives the same dark calibration"** | **FAIL** → U11 must not be used: copy the whole output + `ab_full.json`, tell me and Vika. **INCONCLUSIVE** → the maximum was not faster; copy the output |
| ☐ D3 | The same with the cut: add `--cut` and `--save "<session 1 folder>\ab_cut.json"` (R6i) | Same, **PASS**; B ≈ 100–125 fps | Same as D2 |

## E · Optional, if time is left

| | Do | Expect | If not |
|---|---|---|---|
| ☐ E1 | **D10** · A short session: set **Dark 60, Bright 60**, name `rig_short`, 1 min. At "Please turn on the laser" click **OK at once** (within 1–2 s — that is the case D10 is about; more likely with External Trigger on) | The measurement may start a few seconds late; `app.log` then has **"Measurement starts after the camera-clock check — N frame(s) captured during the check were not used"**. Without that line it is fine too: the check had already ended | `check_session.py`: `first time step` and the other time steps **OK**; `dark frames` / `bright frames` CHECK (60 is a short test). `first time step` NOT OK → copy the output |
| ☐ E2 | **Set Dark/Bright back to 600** before closing the app | — | Otherwise the next session starts with 60 — the app remembers the last settings |
| ☐ E3 | Leftovers from the first session: the benchmark R1f (app closed), the Arduino re-upload R3d, the deliberate overload R7 (**last** — it changes saved settings; R7f puts them back) | As in `rig_checklist.md` | |

---

## What to bring back

- the session folders (each with its `app.log`), including `ab_full.json` and `ab_cut.json`;
- the output of `check_session.py` for each session and of `dark_rate_ab.py` (D2, D3);
- the numbers written down in B13 and C1 (dark time and fps, bright time);
- anything that did not match, with the time it happened.
