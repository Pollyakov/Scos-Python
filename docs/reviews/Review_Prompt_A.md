You are reviewing an existing scientific software project inside Claude Code with
full read access to the repository. Read the actual files; you may RUN read-only
commands (python -m pytest tests/, python bench_processor.py) to back your opinions
with real results. Do not modify any files.

Read first, in order: CLAUDE.md; docs/SCOS_protocol.md; the "SCOS GUI Project"
document, ESPECIALLY the "Session" tab (agreed with the scientific supervisor,
binding); docs/Implementation_Plan.md and the long-term real-time plan + its patches
file; then the source (core/, gui/, camera.py, processor.py, main.py, tests/).

IGNORE the demo plan (Plan_RealTime_Demo*.md) — it was a one-day shortcut, not the
target. Treat demo-era cuts as gaps to close. If code contradicts the protocol or
the Session tab, the protocol and Session tab win — report the contradiction.

Priority: measurement accuracy comes before performance, tidiness, or convenience.
When correctness and convenience conflict, choose correctness and say so.

I am new to coding and real-time image processing — explain in plain language and
define jargon the first time you use it.

## Focus of this review: scientific correctness and the measurement session

1. Scientific correctness — does the κ² formula, noise subtraction (dark, bright,
   shot G·<I>, quantization 1/12), mask shrinking, and normalization match the
   protocol and the MATLAB reference? Run the test suite and report the actual
   results.

2. Normalization logic — long record (mean of first N seconds) vs short record
   (5th-percentile of first N seconds), with N set by the user in the GUI.

3. Crash safety & data integrity — periodic append-to-HDF5 so a crash at hour 3
doesn't lose hours 0–3, clean shutdown/flush on closeEvent, and the results file
schema. Per the Session tab, results.h5 must contain: startTime, timeVec, rBFi,
Intensity, and Params (as a struct). Confirm this is honored.

4. Session flow — the full state machine (IDLE → DARK_CAL → BRIGHT_CAL →
   MEASURING_INIT → MEASURING → FINISHED), abort behavior at every stage, and the
   Session tab specifics:
   - End-of-session popup "Measurement has ended. Please turn off the laser," AND a
     check that average image intensity dropped ~90% after the user clicks OK.
   - Correct saving of results + calibration at the very start, per protocol.
   - Saving a version tagged 0.
   - Later phases, in order: add option to save raw frames → support long sessions →
     control the laser automatically.

5. Long-session concerns — memory growth over multi-hour recordings, timestamp
   accuracy for the physiological-signal analysis (uneven sampling produces a wrong
   result), re-normalization questions for long records, and reproducibility (all
   params + a code version/git tag saved with every result).

6. Anything not listed that affects measurement correctness or the session flow —
   flag it explicitly.

## Assumptions
Before diving deep, list any assumptions you're making so I can correct them.

## Output format
A) A short summary (5–8 bullets) of the most important findings, most critical first.
B) Concrete suggested changes, each with: what, why (in plain language), and the risk
   if we don't do it.
C) An ORDERED task plan for the measurement/session side. Order by dependency and
   priority, respecting the Session tab's sequence (results saving and end-of-session
   check before raw-frame saving, before long sessions, before automatic laser
   control). For each task give: a one-line goal, the files likely involved, and how
   I'll know it's done — including "update the relevant docs" as part of done for any
   task that changes behavior.
Keep explanations beginner-friendly. Where a term is unavoidable, define it once.