# Plan — a steady light leak warns, a flickering one stops (open question 21)

Status: **built 2026-10-28** (todo Done item 64). D1–D4 decided by the user the same day, all
as proposed — D2 the strict setting. Differences from the plan below: the red stop window was
left unchanged (the log says which rule stopped the run); `expectations.md` has no passage on
this check, so it was not changed. Confirmed by measurement: light under the limit is not
judged for flicker (step 4) — the camera's own jitter, 1/25 of the limit on the 2026-10-22
dark frames, is shared with the measurement.

## 1. What Vika decided (2026-10-28)

> If the leak is flicking during the dark calibration - it is very bad and the measurements
> should stop. If the leak is steady, it's not perfect, but the measurement is still valid, so
> in this case I would like only to have a warning for the user, for him to decide if to
> continue or to stop.

(Full text and reasoning: `open_questions.md`, "Light leaking through the probe".)

**Today** (`core/dark_light.py`, todo Done item 61) any light above the limit stops the run:
in each of the first dark frames (`MainWindow._watch_dark_light`) and in the finished dark mean
(`_check_dark_mean_light`). Limit = 10 % of the live picture's ⟨I⟩ (never under 2 DU), or 5 DU
with no live picture.

## 2. Three cases, not two

Her answer is about **leaks**. A laser left on must still stop the run, and must never be
labelled "steady" and get a Continue button.

| Case | What it looks like | Action |
|---|---|---|
| Laser on | light close to the live picture's level — in every frame (laser on all the time) or in some frames (the Arduino keeps flashing the laser while the camera runs untriggered) | **stop** (as now) |
| Flickering leak | light above the limit, changes from frame to frame | **stop** (as now) |
| Steady leak | light above the limit, the same in every frame | **warning: Continue / Stop** — new |
| Dark | light below the limit | pass (as now) |

## 3. How to tell them apart

**Laser on — a level ceiling (D1).** The light must have dropped at least to a set fraction of
the live picture's ⟨I⟩. Proposed: **50 %**. Rig numbers: laser 33 DU (2026-10-22), leak 12.7 DU
(2026-10-19). With both together the live picture is ≈ 46 DU, so the leak is ≈ 28 % of it and is
treated as a leak; the laser is 100 % and stops. The margin is not large: a leak brighter than
half the live picture would be treated as the laser and stop the run — the safe direction.

**Flicker — what it does to the result (D2).** Flicker that is the same over the ROI, with a
frame-to-frame standard deviation σ_f (of the per-frame ROI mean), adds about σ_f² to every
pixel's `var_dark`. The measurement frames do not share it, so κ²_corr comes out low by about

    Δκ² ≈ σ_f² / ⟨I⟩²        (⟨I⟩ = the live picture's intensity, known before the prompt)

A steady leak's per-frame mean still scatters a little from pixel noise (pixel variance /
number of ROI pixels — ≈ 5 × 10⁻⁵ DU² for the 2026-10-19 ROI); that part is subtracted, since
it is the same in the measurement.

Room light flickers with the mains (100 Hz), so it is never perfectly steady. Measured on the
rig (2026-10-19, ≈ 50 DU of room light, no probe): the frame mean wobbled 2.6 DU at the
maximum frame rate and 1.6 DU at 20 Hz; the flicker added ≈ 7.8 DU² to the pixel variance. Scaled
to the 12.7 DU leak of 2026-10-19: σ_f² ≈ 0.5 DU². What that leak would have done:

| Signal ⟨I⟩ | κ²_corr seen | Δκ² from the leak's flicker | Error |
|---|---|---|---|
| 114 DU (2026-10-08) | 0.0081 | 0.5 / 114² ≈ 3.8 × 10⁻⁵ | ≈ 0.5 % low |
| 33 DU (2026-10-22) | 0.0017 | 0.5 / 33² ≈ 4.6 × 10⁻⁴ | ≈ 27 % low |

The same leak is harmless at strong signal and ruins the result at weak signal. That is why the
proposed rule judges the **effect** (Δκ²), not the size of the flicker alone.

Proposed limit: **Δκ² ≤ 1 × 10⁻⁵** = steady (≈ 0.1 % of 0.0081, ≈ 0.6 % of 0.0017). With it
the 2026-10-19 leak would have **stopped** the run at either signal level — room light through a
probe will usually count as flickering; a truly steady source (daylight, a DC lamp) warns.

No live picture (it was already dark before Start SCOS, `basis = "fixed"`): there is no ⟨I⟩
to judge the effect against, nor a ceiling → **stop, as now** (proposed default).

## 4. Where it happens in the code

1. `core/dark_light.py`: new pure functions — the ceiling, and `flicker_effect(frame_means,
   pixel_var, n_pixels, reference_du)` → Δκ². Unit tests with made-up series.
2. First dark frames (`_watch_dark_light`): stop at once only when a frame reaches the **laser
   ceiling** (a flashing laser is caught here within the first second, as now). A frame above
   the light limit but under the ceiling no longer stops by itself.
3. Every collected dark frame: keep its ROI mean (a running sum and sum of squares — no list).
   On the full sensor the GUI thread is the limit; **time it first** with
   `tools/bench_dark_collector.py --handler` (the first-frame check already does this per frame).
4. Finished dark mean (`_check_dark_mean_light`), after the frame-rate restore, before anything
   is stored or written:
   below the limit → pass; at the ceiling → stop; flicker Δκ² over the limit → stop;
   otherwise → **the warning window**.
   - Frames keep arriving while a window is open, and the collector is already done, so
     `_finish_dark_cal` must not be entered again behind the window — guard it (e.g. take the
     collector out of reach before the window opens).
   - Stop → `_abandon_calibration()` first, window second, as for every other stop.
5. The warning window, in `gui/safety_dialog.py` (amber, not red — it is a judgement call, not
   an error): the leak in DU, the flicker effect, and two buttons (D3). Preview it with
   `tools/preview_safety_windows.py` before it is wired in.
6. Record (D4).
7. Rehearsal: a new answer in `tools/rehearsal.py`'s `answers` table for the new title, and a
   way to add a steady / a flickering offset to the dark playback; mutations for both.
8. Tests: `tests/test_dark_light_check.py` — steady → warning, Continue → calibration written
   with the leak recorded; Stop → abandoned; flicker → stop; laser ceiling in the first frames →
   stop; no reference → stop. Each one checked by a deliberate mistake.
9. Docs: the CLAUDE.md note ("kept as a stop until Vika answers" → the new rule), `rig_checklist.md`
   R10 (laser on — still a stop) + a new steady-leak row, todo Done item, `expectations.md`.

## 5. Decisions

| # | Question | Proposed |
|---|---|---|
| D1 | Laser ceiling — light at least this fraction of the live picture = the laser, stop | **50 %** |
| D2 | Flickering = Δκ² above this | **1 × 10⁻⁵** (the effect, not the flicker size) |
| D3 | Default button in the warning | **Stop** (Enter must not continue a run with a leak) |
| D4 | Where a continued leak is recorded | `Calibration.h5` `dark/light_check = "leak - operator continued"` with mean, σ_f, Δκ² and the limits; **and** the same in the results file's `metadata`, which is the file Vika opens |
