#!/usr/bin/env python3
"""AUG-197 (AC1): attribute a killed pytest arm to the test that was in flight.

When `tests/gate.sh`'s pytest arm hits its process budget, pytest produces NO
summary, and a guard log used to grade the kill as ``first_failing_test: none
detected`` — a timeout nobody can act on. The suite's conftest appends one JSON
line per test start and per phase report to ``$AUGER_GATE_PROGRESS_FILE``; this
script reads that file back and prints the attribution:

  * the test IN FLIGHT when the arm was killed (the last ``start`` line without
    a matching ``teardown`` report) — that is the first failing test of a
    timeout, by construction;
  * every test that FAILED before the kill (so a slow suite can be told apart
    from a stuck one), with phase and outcome;
  * the last test that COMPLETED, as the progress anchor.

The body of a JSON line is deliberately tolerant (unreadable lines are skipped,
counted and named, never fatal): the file is a crash record, not a contract.
This script never exits nonzero for missing attribution — the GATE fails closed
by its own rule; the report's job is to name what it can and say so explicitly
when it cannot.

Usage:
    scripts/gate_timeout_report.py --progress FILE [--budget 2400s]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DONE_PHASE = "teardown"  # a start with a teardown report is a FINISHED test


def read_progress(path: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the progress file. Returns (records, broken_lines)."""
    records: list[dict[str, Any]] = []
    broken: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.read().splitlines()
    except OSError as exc:
        return [], [f"<unreadable progress file {path}: {exc}>"]
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            broken.append(f"<undecodable line ({exc}): {raw[:120]!r}>")
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
        else:
            broken.append(f"<non-object line: {raw[:120]!r}>")
    return records, broken


def attribute(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the attribution facts from the progress records.

    in_flight is the last ``start`` record whose nodeid has no ``teardown``
    report yet: a call-hang produces only a setup report; a teardown-hang
    produces no teardown report. Between tests, the kill is attributed to the
    last completed test instead (there is no test in flight).
    """
    starts: list[str] = []
    finished: dict[str, bool] = {}
    reports: list[tuple[str, str, str]] = []  # (when, outcome, nodeid)
    for record in records:
        event = record.get("event")
        nodeid = str(record.get("nodeid", "<no nodeid>"))
        if event == "start":
            starts.append(nodeid)
        elif event == "report":
            when = str(record.get("when", "?"))
            outcome = str(record.get("outcome", "?"))
            reports.append((when, outcome, nodeid))
            if when == DONE_PHASE:
                finished[nodeid] = True
    in_flight: str | None = None
    for start in reversed(starts):
        if not finished.get(start):
            in_flight = start
            break
    failures = [
        (when, outcome, nodeid)
        for when, outcome, nodeid in reports
        if outcome in ("failed", "error")
    ]
    return {
        "in_flight": in_flight,
        "failures": failures,
        "last_completed": (
            reports[-1][2] if reports else None,
            f"{reports[-1][0]}:{reports[-1][1]}" if reports else None,
        ),
        "total_records": len(records),
        "total_starts": len(starts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AUG-197: attribute a pytest-arm kill to the test in flight."
    )
    parser.add_argument(
        "--progress",
        required=True,
        help="the AUGER_GATE_PROGRESS_FILE the conftest appended to",
    )
    parser.add_argument(
        "--budget",
        default="",
        help="the budget string (for the report's wording only)",
    )
    args = parser.parse_args(argv)

    records, broken = read_progress(args.progress)
    if not records:
        print("== AUG-197 pytest-arm attribution ==")
        print(f"progress file: {args.progress}")
        print("ATTRIBUTION UNAVAILABLE: the file holds no per-test records.")
        for line in broken[:5]:
            print(f"  {line}")
        if not broken:
            print(
                "  (empty file: the kill likely landed before pytest reached"
                " conftest's hook registration)"
            )
        return 0

    facts = attribute(records)
    print("== AUG-197 pytest-arm attribution ==")
    if args.budget:
        print(f"pytest arm budget: {args.budget} (killed as its process group)")
    if facts["in_flight"] is not None:
        print(
            f"first failing test (in flight when the arm was killed): {facts['in_flight']}"
        )
    else:
        print(
            "the arm was killed BETWEEN tests: no test was in flight; "
            f"last completed test: {facts['last_completed'][0]}"
        )
    if facts["failures"]:
        print(f"tests that FAILED earlier in this arm: {len(facts['failures'])}")
        for when, outcome, nodeid in facts["failures"][:10]:
            print(f"  - {nodeid} ({when}: {outcome})")
        if len(facts["failures"]) > 10:
            print(f"  ... and {len(facts['failures']) - 10} more")
    else:
        print(
            "tests that FAILED earlier in this arm: 0 (a hang, not a failure pile-up)"
        )
    if facts["last_completed"][0] is not None:
        print(
            f"last completed test: {facts['last_completed'][0]} [{facts['last_completed'][1]}]"
        )
    print(
        f"progress records: {facts['total_records']} lines, {facts['total_starts']} test starts"
    )
    for line in broken[:5]:
        print(f"  skipped unreadable progress line: {line}")
    print("see the pytest output above this block for the last live output of the arm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
