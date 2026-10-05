---
name: wrap-up
description: End a working session cleanly — stop leftover processes, rescue results that only live in the scratchpad, check git, fill test gaps, update docs/todo.md and memory, then give one handoff report with pending decisions and questions for the supervisor. Use when the user types /wrap-up or asks to finish, close or end the session.
---

# /wrap-up — end the session cleanly

The next session starts knowing only two things: the **repo** and the **memory files**.
Everything else — the chat, the scratchpad, numbers printed in a terminal — is gone. This
skill makes sure nothing important lives only there, and that the user leaves with a clear
picture of what is done, what is not, and what is waiting for them.

**Order matters: look first, report, ask, and only then act.** Steps 1–6 gather facts and
change nothing in git. The report (step 7) asks the questions. Commits, pushes and writing
pending decisions into `todo.md` happen only after the user answers (step 8). Memory is the one
thing updated directly, as usual.

---

## 1 · Leftovers

- **Background tasks** this session started: stop any still running.
- **Stray processes** this session started (e.g. a headless rehearsal stuck on a hidden
  dialog): find them by command line, e.g. Python processes whose command line contains
  this session's scratchpad path. Stop only those. **Never** stop a process this session did
  not start — the user may have the app or other programs open.
- If a process may have opened a window on the user's screen, say so in the report.

## 2 · The scratchpad

The scratchpad belongs to one session and can disappear. List what is in it and, for
anything worth keeping (a script that worked, a patched copy, measured output), propose
where it goes: `tools/` for scripts, the relevant doc for numbers. If it should not go into
the repo, at least note in memory where it is and why it matters.

## 3 · Git

- `git status --short` — for every changed or new file, one line: what it is and which task
  it belongs to.
- **Private files must stay out of git:** `scos_config.local.json`, the personal English
  log, anything in `.gitignore`. Flag them if they appear staged.
- **Stray files:** an `app.log`, session/results folders, `scos_config.json` changed by
  accident (the app never writes it; tests are redirected), files written into the repo by
  a test or rehearsal.
- **Unpushed commits:** `git log origin/main..HEAD --oneline` — count them.
- Prepare, but do not run: a proposed grouping into commits, each with a descriptive message
  in the project's style (what, why, numbers, tests; ends with the attribution line).

## 4 · Tests — fill the gaps only, don't repeat

The fast suite already runs after code changes and again in the pre-commit hook.
- Code changed **after** the last test run this session → run `python -m pytest tests/ -q -m "not slow"` once.
- The session touched **the math** (`processor.py`, `core/`, gain lookup, calibration) and
  the slow MATLAB tests were not run → run `python -m pytest tests/ -q` (all, ~minutes).
  CLAUDE.md: the offline reference test must never break.
- Otherwise report the last result as it stands. Always give real numbers ("357 passed"),
  or say plainly that tests were not run and why.

## 5 · Documentation

- **`docs/todo.md`** is the only task list:
  - status of every task that moved;
  - a finished task gets a Done-table entry in house style — what, why, measured numbers,
    tests, mutations checked;
  - **Actual** column of the rig-prep table: approximate time per finished task, from
    commit timestamps (`git log --format="%ad %s" --date=iso`) and the session's span. Say
    it is approximate. Compare with **Est.** in the report;
  - bugs found but not fixed → into the task they belong to (or the backlog).
- **Measured numbers and test results** go into the doc they belong to (todo.md, the
  checklist, a commit message) — never only into the chat.
- **`CLAUDE.md`** — only when a gotcha or the architecture changed. It is not a diary.
- **`docs/open_questions.md`** — new questions for the supervisor.
- **Docs this session proved wrong:** fix them if the fix is small and certain; otherwise
  list them. Either way, they appear in the report.

## 6 · Memory

- **Where we stopped:** update the current-task memory — next step, open decisions, what we
  are waiting for (e.g. supervisor's answers). **Absolute dates only** (2026-10-05, never
  "tomorrow").
- **New rules from the user** — corrections and confirmed preferences — become feedback
  memories, with the reason.
- **Hygiene:** update an existing memory instead of adding a duplicate; delete one that
  turned out wrong; keep the `MEMORY.md` index line in step.

---

## 7 · The report

Plain language, beginner-friendly. Leave out any section that has nothing in it. In this
order:

1. **Done this session** — tasks, with commit IDs.
2. **Cleaned up** — processes stopped, scratchpad items moved (or "nothing to clean").
3. **Tests** — what ran, the result, or why nothing needed running.
4. **Docs that turned out wrong** — what was fixed, what still needs fixing.
5. **Pending decisions** — every decision waiting for the user, each in one or two lines:
   the question, the options, my recommendation and why. **Suggest deciding now.**
6. **Questions for the supervisor** — the full text, ready to copy and send (also added to
   `docs/open_questions.md`). Say "none" if there are none.
7. **Estimate vs actual** — for tasks finished this session.
8. **Lessons learned** — one or two sentences: what went wrong or nearly went wrong, and
   how to avoid it. If it is a rule worth keeping, it also goes into memory.
9. **Uncommitted and unpushed** — the proposed commits (files + message summary), then ask:
   commit? push?
10. **Next step** — the exact next task.
11. **Fresh session?** — see the rule below.

## 8 · After the user answers

- **Commit** only what the user approved, with the messages shown. **Push** only on an explicit
  yes.
- **Pending decisions the user made** → apply them and record them where they belong.
- **Pending decisions the user declined to make now** → write them into `docs/todo.md`, in a
  "Pending decisions" list at the top of the current work section, so they are visible in
  the repo and not only in memory.
- End with a short confirmation: commit IDs, what was written where, working tree clean or
  not.

---

## When to suggest a fresh session

Every message re-reads the whole conversation, so a long session costs more per message and
can get less sharp. Memory and `todo.md` carry over everything that was saved properly, so
a fresh start loses nothing. Suggest one when **both** hold:

1. the current task is finished and committed — a natural break; and
2. the next task is a different `todo.md` row, **or** the conversation is long — roughly
   **300k tokens or more** in `/context` (a rule of thumb, not an official limit).

The model cannot reliably see its own context size, so phrase it as "this is a good point to
start fresh" and mention that `/context` shows the exact figure.

## Never

- commit or push without an explicit yes;
- delete a file, folder or branch without looking at it first and asking;
- stop a process this session did not start;
- re-run tests that already passed on unchanged code just to have run them;
- leave a measured number, a decision or a found bug only in the chat.
