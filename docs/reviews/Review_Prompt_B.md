You are reviewing an existing scientific software project inside Claude Code with
full read access to the repository. Read the actual files; you may RUN read-only
commands (python -m pytest tests/, python bench_processor.py) to back your opinions
with real results. Please do not modify any files, including project memory — this
is a read-only review, and I'll decide separately what to save.

Read first, in order: CLAUDE.md; docs/SCOS_protocol.md; the "SCOS GUI Project"
document, ESPECIALLY the "Session" tab (agreed with the scientific supervisor,
binding); docs/Implementation_Plan.md and the long-term real-time plan + its patches
file; then the source (core/, gui/, camera.py, processor.py, main.py, tests/).

IGNORE the demo plan (Plan_RealTime_Demo*.md) — it was a one-day shortcut, not the
target. Treat demo-era simplifications as gaps to close. If code contradicts the
protocol or the Session tab, the protocol and Session tab win — report the
contradiction.

Priority: measurement accuracy comes before performance, tidiness, or convenience.
When correctness and convenience conflict, choose correctness and say so.

I am new to coding and real-time image processing — explain in plain language and
define terms the first time you use them.

## An observation to check
While reading the pipeline, a reviewer noted the following. Please verify whether it
is accurate, and if so, make sure your task plan addresses the underlying cause
rather than the surface symptom:

"The two buffers that are bounded and visible (Pylon 20 + _input_q 20) rarely fill.
The two that actually fill up (_inflight deque + the ThreadPoolExecutor's internal
queue) have no size limit and no backpressure, so memory can keep growing until the
program runs out of it, with no warning. Making _input_q.put() blocking doesn't help,
because the dispatcher empties _input_q faster than the camera fills it. The proper
fix is to cap how many work items are in flight at once, so the dispatcher naturally
slows down when the workers fall behind."

## Focus of this review: pipeline structure, timing, and reliability

1. Pipeline structure and flow control — the documented target is 3 worker threads +
   GUI (Camera -> frame_queue -> Processor -> result_queue -> GUI, plus record_queue
   -> Recorder -> HDF5). First, describe the structure the code ACTUALLY implements
   (if it uses a dispatcher / ThreadPoolExecutor / futures instead, say so). Then
   review buffering from end to end:
   a. List EVERY place where frames, work items, or results can pile up — not just
      things named "queue". Include the less obvious ones: the Pylon driver buffer,
      any input queues, in-flight work collections (deques, lists), the
      ThreadPoolExecutor's internal queue, Qt's queued-signal event queue, and any
      plot-widget data buffers.
   b. For EACH one, say: does it have a size limit? What is it? What happens when it
      is full — wait, discard, or keep growing? Is the user warned?
   c. Follow the flow-control chain: if the slowest stage (processing or disk) falls
      behind, does that slowdown travel all the way back so the camera thread waits
      — or does the pressure slip past a bounded queue into an unbounded one that
      keeps growing? Note the common trap: a bounded input queue does nothing if a
      fast dispatcher empties it and re-parks the work somewhere unbounded; the real
      fix is limiting the number of in-flight work items (e.g., a semaphore or a
      bounded "in-flight" window), not just making put() wait.
   d. Confirm frames are never quietly discarded anywhere in this chain, and that
      any discard is counted and logged.
   If practical, show the problem with a small read-only experiment (e.g., a test
   script that deliberately slows the consumer) rather than reasoning alone.

2. Timing budget — does one frame's processing fit within the ~50 ms available at
   20 Hz on a 700x700 frame? If bench_processor.py exists and runs without hardware,
   run it and report the measured time instead of estimating.

3. Thread-safety — any chance of two threads clashing over shared data, or of a
   worker thread touching GUI objects directly? Is all cross-thread communication
   done through Qt signals/slots?

4. Hardware handling — the Arduino Line2 hardware trigger, recovering gracefully if
   the camera disconnects and reconnects, detecting and logging missed frames, and
   the path toward controlling the laser automatically.

5. Code organization & tooling — separation of core/ (pure logic) from gui/ (widgets
   only), type hints, tests (especially the offline reference test that checks
   numbers against the MATLAB results), using logging instead of print, and project
   tooling (pyproject / lockfile / ruff / mypy / pre-commit).

6. Anything not listed that affects reliability during long, continuous runs — please
   flag it.

## Assumptions
Before going deep, list any assumptions you're making so I can correct them.

## Output format
A) A short summary (5-8 bullets) of the most important findings, most important first.
B) Concrete suggested changes, each with: what, why (in plain language), and the risk
   of skipping it.
C) An ORDERED task plan for the engineering side, arranged by dependency (for example,
   fix the buffering / flow-control model before further timing optimization). For
   each task give: a one-line goal, the files likely involved, and how I'll know it's
   done — including "update the relevant docs" as part of done for any task that
   changes behavior.
Keep explanations beginner-friendly. Define any unavoidable term once.
