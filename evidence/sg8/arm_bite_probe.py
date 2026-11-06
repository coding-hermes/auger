"""SG8 evidence: prove the gate-arm assertion BITES (AUG-119/AUG-124 condition of merge).

The three SG8 proof files are only covered if `tests/gate.sh`'s pytest arm names them, and
`tests/test_spec_context.py::test_gate_arm_runs_these_files` is what enforces that. An assertion
nobody has seen fail is decoration, so this probe runs it twice against a SCRATCH COPY of the arm:

  * a copy with one SG8 filename removed  -> the assertion must RAISE (proving it bites)
  * an unmodified copy                    -> the assertion must PASS

The real `tests/gate.sh` is never edited: the probe imports the test module and repoints its `GATE`
constant at the scratch copy, so the tree under test is untouched.

Usage:  .venv/bin/python evidence/sg8/arm_bite_probe.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WORKTREE = Path("/home/kara/proj-wt-auger-xml-hardening")
TEST_MODULE = WORKTREE / "tests" / "test_spec_context.py"
ARM = WORKTREE / "tests" / "gate.sh"
SCRATCH = Path("/home/kara/.hermes/cache/scratch")

REQUIRED = "tests/test_task_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sg8_spec_context_probe", TEST_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    arm_text = ARM.read_text(encoding="utf-8")
    if REQUIRED not in arm_text:
        print(f"PROBE-FAIL {REQUIRED} is not in the real arm — the probe premise is wrong")
        return 1
    narrowed = arm_text.replace(f" {REQUIRED}", "", 1)
    if narrowed == arm_text:
        print("PROBE-FAIL the removal changed nothing")
        return 1

    base = SCRATCH / "sg8-arm-bite"
    base.mkdir(parents=True, exist_ok=True)
    narrowed_path = base / "gate-narrowed.sh"
    control_path = base / "gate-control.sh"
    narrowed_path.write_text(narrowed, encoding="utf-8")
    control_path.write_text(arm_text, encoding="utf-8")

    failures = 0

    module = load_module()
    module.GATE = narrowed_path
    try:
        module.test_gate_arm_runs_these_files()
    except AssertionError as exc:
        first = str(exc).strip().splitlines()[0]
        print(f"PROBE-OK   narrowed arm -> assertion FAILED   {first[:120]}")
    else:
        print("PROBE-FAIL narrowed arm -> the assertion PASSED; it does not bite")
        failures += 1

    module = load_module()
    module.GATE = control_path
    try:
        module.test_gate_arm_runs_these_files()
    except AssertionError as exc:  # pragma: no cover - would be a probe bug
        print(f"PROBE-FAIL unmodified arm -> assertion FAILED   {exc}")
        failures += 1
    else:
        print("PROBE-OK   unmodified arm -> assertion PASSED")

    if failures:
        print(f"PROBE RESULT: {failures} expectation(s) UNMET")
        return 1
    print("PROBE RESULT: the arm assertion bites on a narrowed arm and passes on the real one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
