#!/usr/bin/env python3
"""AUG-211: the gate's LOCK-ATTRIBUTED skip-count bound — a lock storm is not green.

The AUG-209 lock classification converts a 503 SERIALIZER_LOCKED landing into a
typed pytest.skip (conftest's run_cli/run_cli_exit). That is the right PER-TEST
answer, but it hides the aggregate: a chronic contention storm can skip dozens
of tests while the battery still exits 0, and a gate that reports PASS for
coverage it never exercised is worse than no gate (the AUG-130 lesson).

The bound therefore counts only skips ATTRIBUTED to the substrate lock — those
whose skip reason matches the AUG-209 convention ("substrate write contention
(503 SERIALIZER_LOCKED, AUG-209): ...", the text conftest's run_cli AND
run_cli_exit emit). The gate runs pytest with -rs, so every attributed skip
prints exactly one ``SKIPPED [n] <nodeid>: <reason>`` short-summary line and
this script sums the bracketed counts. Every OTHER skip is out of scope: the
hosted-CI mode (AUGER_CI=1) legitimately skips ~139 live-dependent tests, and
live runs skip on unrelated foreign-leg contention — a mode-blind ceiling that
counted all of them would red every hosted-CI run and every healthy run under
unrelated contention (the AUG-211 rework's DEFECT-1).

Over the ceiling it prints a loud SKIP STORM block naming the count, the ceiling
and the suspected 503 SERIALIZER_LOCKED cause, and exits 1 so the gate fails
instead of exiting 0 "green". Fail-closed arms:

  * no summary file readable  -> exit 1 (the skip total is UNPROVEN);
  * a skip total > 0 with NO short-summary lines at all -> exit 1 (the skips
    cannot be attributed — the -rs flag the parse depends on was lost).

Usage:
    scripts/gate_skip_bound.py --summary-file FILE [--ceiling 10]

tests/gate.sh passes the ceiling explicitly (default 10, overridable there with
AUGER_GATE_SKIP_CEILING); the total parses from the FINAL lowercase summary
line ("1183 passed, 3 skipped in 9.00s"); the uppercase ``SKIPPED [n]``
short-summary lines never inflate it.
"""

from __future__ import annotations

import argparse
import re
import sys

#: The default ceiling, restated in tests/gate.sh (AUG-211 AC2 requires both
#: places to name it). At-or-below passes; one lock-attributed skip more fails
#: the gate loudly.
DEFAULT_CEILING = 10

#: Lowercase only: the final summary tally. The ``-rs`` short-summary's
#: ``SKIPPED [n] ...`` lines are uppercase and must never inflate this count.
TOTAL_SKIP_RE = re.compile(r"\b(\d+) skipped\b")

#: The AUG-209 convention, emitted by conftest's run_cli AND run_cli_exit:
#: pytest.skip("substrate write contention (503 SERIALIZER_LOCKED, AUG-209): ...").
#: With -rs each attributed skip prints one short-summary line; the bracketed
#: count covers pytest's same-location aggregation.
LOCK_SKIP_RE = re.compile(
    r"^SKIPPED \[(\d+)\] .*substrate write contention \(503 SERIALIZER_LOCKED",
    re.MULTILINE,
)


def lock_skip_count(summary_text: str) -> int:
    """Skips ATTRIBUTED to the substrate lock (the AUG-209 skip-reason convention)."""
    return sum(int(n) for n in LOCK_SKIP_RE.findall(summary_text))


def total_skip_count(summary_text: str) -> int:
    """The skip tally from pytest's final summary line (0 when none is printed)."""
    matches = TOTAL_SKIP_RE.findall(summary_text)
    return int(matches[-1]) if matches else 0


def short_summary_lines_present(summary_text: str) -> bool:
    """True when at least one ``SKIPPED [n]`` short-summary line is in the capture."""
    return any(line.startswith("SKIPPED [") for line in summary_text.splitlines())


def within_bound(count: int, ceiling: int) -> bool:
    """True when the run's attributed skip count is at or below the stated ceiling."""
    return count <= ceiling


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AUG-211: grade the pytest arm's LOCK-ATTRIBUTED skip count "
            "against the ceiling."
        )
    )
    parser.add_argument(
        "--summary-file",
        required=True,
        help="file holding the pytest arm's captured output (with -rs short summary)",
    )
    parser.add_argument(
        "--ceiling",
        type=int,
        default=DEFAULT_CEILING,
        help="maximum acceptable LOCK-attributed skip count (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.summary_file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(
            f"AUG-211 skip-count bound could not read {args.summary_file}: {exc}; "
            "the battery's skip total is UNPROVEN — failing the gate",
            file=sys.stderr,
        )
        return 1

    total = total_skip_count(text)
    attributed = lock_skip_count(text)
    if total > 0 and not short_summary_lines_present(text):
        print(
            f"AUG-211 skip-count bound: {total} skips were reported but NO "
            f"SKIPPED [n] short-summary lines were captured — the skips are "
            "UNATTRIBUTED (the -rs flag the parse depends on was lost); "
            "failing the gate",
            file=sys.stderr,
        )
        return 1

    print(
        f"pytest lock-attributed skips: {attributed} "
        f"(of {total} total; ceiling {args.ceiling})"
    )
    if within_bound(attributed, args.ceiling):
        return 0
    print(
        f"SKIP STORM (AUG-211): the pytest arm skipped {attributed} tests under "
        f"substrate write contention (ceiling {args.ceiling}).",
        file=sys.stderr,
    )
    print(
        "A chronic 503 SERIALIZER_LOCKED substrate-contention storm converts the "
        "suite's coverage into typed skips (AUG-209) — this battery is NOT green.",
        file=sys.stderr,
    )
    print(
        "Fix the substrate contention and re-run. To accept a storm-scared run "
        "anyway, raise AUGER_GATE_SKIP_CEILING in tests/gate.sh with a named reason.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
