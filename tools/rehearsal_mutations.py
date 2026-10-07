"""
Prove the rehearsal's checks can fail: run tools/rehearsal.py with one deliberate defect.

A check that has never failed may be checking nothing. Each mutation below
breaks one thing the `slowdown` scenario (rig prep 3b, todo Done item 33),
the `recovery` scenario (3c, Done item 36) or the file checks every scenario
runs (3e) claims to verify. Patches are applied in memory, in this process only;
no file is changed. A mutation run must exit 1 and name the check it was meant
to trip. If it exits 0, that check is not doing its job.

Usage:
    venv\\Scripts\\python.exe tools/rehearsal_mutations.py latch --cal-frames 60
    venv\\Scripts\\python.exe tools/rehearsal_mutations.py leak --out <empty folder>

Anything after the mutation name is passed to tools/rehearsal.py; the
scenario defaults to the one each mutation targets (MUTATIONS below).

Mutations and the check each one must trip (measured 2026-10-06):
  latch    overload_detected without its once-per-episode latch
           → "fired exactly once" (fired 44×)
  arrival  timeVec stamped when the result reaches the GUI — the pre-task-5
           design → "every timeVec value is a capture stamp" (104/104 not)
           and "every frame missing from timeVec is a counted drop"
  uncount  frames evicted from the queue but not counted → "the stall dropped
           frames, and counted them", "drops logged", "missing = dropped"
  leak     no cap on frames in flight, so the executor queue grows without bound
           → "memory bounded" (+410 MB) and "memory flat" (+312 MB); the
           overload warning never fires either
  never-rearm  (recovery) the overload flag never resets once set, so a second
           slowdown in the same session goes unwarned → "overload flag reset
           once the queue drained" and "episode 2: the re-armed warning fired
           again" (fired 0×)

Rig prep 3e — the numbers in the results file, not just their presence:
  divisor  rBFi written as bfi / (1.001 × the constant stored in Params)
           → "rBFi = bfi / Params.normalizationConstant"
  baseline the normalization constant computed without the window's last
           sample (an off-by-one in which rows form the baseline)
           → "constant recomputed from the file's first N rows"
  meta-drops  (slowdown) frames_dropped_queue written as 0 although frames
           were dropped → "metadata.frames_dropped_queue = 0 (GUI counter N)"

Not here: the 50 % re-arm *threshold*. While slowed, a blocking put() keeps the
queue at 19-20 of 20, so a wrong low mark (0.8, 0.75) never shows in a
rehearsal. tests/test_pipeline.py checks the hysteresis band directly.
"""

import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

# mutation → the scenario it runs against unless --scenario is given
MUTATIONS = {"latch": "slowdown", "arrival": "slowdown", "uncount": "slowdown",
             "leak": "slowdown", "never-rearm": "recovery",
             "divisor": "normal", "baseline": "normal", "meta-drops": "slowdown"}


def apply(mutation: str) -> None:
    import core.pipeline as P

    if mutation == "latch":
        def _check_overload(self):
            depth = self._input_q.qsize
            if depth >= self._OVERLOAD_HIGH * self._input_q.maxsize:
                self.overload_detected.emit(depth)
        P.RealtimePipeline._check_overload = _check_overload

    elif mutation == "arrival":
        from gui.main_window import MainWindow
        original = MainWindow._on_scos_result

        def _on_scos_result(self, t, *rest):
            return original(self, time.monotonic() - self._scos_worker.t0_capture, *rest)
        MainWindow._on_scos_result = _on_scos_result

    elif mutation == "uncount":
        original_put = P._DropOldestQueue.put

        def put(self, item, **kw):
            before = self._dropped
            queued = original_put(self, item, **kw)
            self._dropped = before
            return queued
        P._DropOldestQueue.put = put

    elif mutation == "leak":
        original_init = P.RealtimePipeline.__init__

        def __init__(self, processor, n_workers=3, *, max_inflight=None, parent=None):
            original_init(self, processor, n_workers, max_inflight=100_000, parent=parent)
        P.RealtimePipeline.__init__ = __init__

    elif mutation == "never-rearm":
        def _check_overload(self):
            depth = self._input_q.qsize
            if not self._overloaded and depth >= self._OVERLOAD_HIGH * self._input_q.maxsize:
                self._overloaded = True
                self.overload_detected.emit(depth)
        P.RealtimePipeline._check_overload = _check_overload

    elif mutation == "divisor":
        from core.recorder import HDF5Recorder
        original = HDF5Recorder.write_rbfi

        def write_rbfi(self, norm_constant, method, window_seconds):
            original(self, norm_constant, method, window_seconds)
            del self._f["rBFi"]
            self._f.create_dataset("rBFi",
                                   data=self._f["bfi"][:] / (norm_constant * 1.001))
        HDF5Recorder.write_rbfi = write_rbfi

    elif mutation == "baseline":
        import gui.main_window as MW
        original = MW.normalization_constant

        def normalization_constant(values, method):
            values = list(values)
            return original(values[:-1] if len(values) > 1 else values, method)
        MW.normalization_constant = normalization_constant

    elif mutation == "meta-drops":
        from core.recorder import HDF5Recorder
        original = HDF5Recorder.set_metadata

        def set_metadata(self, **attrs):
            if "frames_dropped_queue" in attrs:
                attrs["frames_dropped_queue"] = 0
            original(self, **attrs)
        HDF5Recorder.set_metadata = set_metadata


def main() -> int:
    # The Windows console (and a pipe) defaults to cp1252, which has no → or —.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] not in MUTATIONS:
        print(__doc__)
        print(f"first argument must be one of: {', '.join(MUTATIONS)}")
        return 2
    mutation, rest = sys.argv[1], sys.argv[2:]
    if "--scenario" not in rest:
        rest = ["--scenario", MUTATIONS[mutation], *rest]
    apply(mutation)
    import rehearsal
    sys.argv = ["rehearsal.py", *rest]
    print(f"=== MUTATION: {mutation} — this run is expected to FAIL ===")
    return rehearsal.main()


if __name__ == "__main__":
    sys.exit(main())
