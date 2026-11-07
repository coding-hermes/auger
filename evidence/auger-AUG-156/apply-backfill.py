#!/usr/bin/env python3
"""AUG-156 backfill applier — writes a machine-resolvable `evidence_run_id` onto every complete row.

One-off, and deliberately NOT a second implementation of the evidence rules: it imports the audit
(`scripts/audit_board_evidence.py`) and asks it which run id binds a row, then writes that id (or a
closed-vocabulary `none:<reason>`) as a top-level `evidence_run_id`.

Edits are SURGICAL: the board is read as raw lines, only the lines of `status=complete` rows are
re-serialized (each in its own existing style), and every untouched line is written back
byte-identically. The applier refuses to write if any re-serialized row differs from the original in
anything except `evidence_run_id` and the `detail.evidence[]` label cleanup.

    python3 evidence/auger-AUG-156/apply-backfill.py --board .coding-hermes/board/tasks.jsonl            # dry run
    python3 evidence/auger-AUG-156/apply-backfill.py --board .coding-hermes/board/tasks.jsonl --apply    # write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_board_evidence as audit  # noqa: E402  (path insert above is the import mechanism)

FIELD = "evidence_run_id"


def serialize_like(original: str, obj: dict) -> str:
    """Re-serialize one row in the style its own line already used."""
    spaced = '": ' in original
    separators = (", ", ": ") if spaced else (",", ":")
    ascii_only = all(ord(char) < 128 for char in original)
    return json.dumps(obj, separators=separators, ensure_ascii=ascii_only)


def reason_for(row: dict, decision, earliest: str) -> str:
    """Most specific TRUE absence reason: foreign citations, then pre-window, then no artifact."""
    if decision.foreign_mentions:
        segments = "+".join(
            f"{run}-task-{task}" for run, task in sorted(decision.foreign_mentions)
        )
        return audit.FOREIGN_PREFIX + segments
    date = audit.row_date(row)
    if date is not None and earliest and date < earliest:
        return audit.REASON_PRE_GITREINS
    return audit.REASON_NO_ARTIFACT


def clean_labels(
    row: dict, verdicts: dict[str, audit.Verdict], bound_run: str | None
) -> int:
    """Replace a non-verdict `detail.evidence[].run_id` with the row's real binding, or null it.

    A fabricated label is not deleted (the audit trail of the correction matters) — it is demoted to
    `removed_label`. A row that DOES bind gets the real run id in the SG-126 position boardctl's own
    `--evidence-run-id` writes; an unbound row gets `run_id: null`, so no complete row claims a run
    that resolves to nothing.
    """
    detail = row.get("detail")
    if not isinstance(detail, dict):
        return 0
    entries = detail.get("evidence")
    if not isinstance(entries, list):
        return 0
    cleaned = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("run_id")
        if not isinstance(value, str) or value.startswith(audit.NONE_PREFIX):
            continue
        verdict = verdicts.get(value) if audit.RUN_ID_RE.match(value) else None
        if verdict is not None and not verdict.error and verdict.task_id == row["id"]:
            continue
        entry.setdefault("removed_label", value)
        entry.setdefault(
            "removed_label_kind",
            "self-declared-run-label" if value.startswith("run:") else "session-id",
        )
        entry["run_id"] = bound_run
        cleaned += 1
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill evidence_run_id onto complete board rows."
    )
    parser.add_argument("--board", default=".coding-hermes/board/tasks.jsonl")
    parser.add_argument("--history", default=None)
    parser.add_argument(
        "--apply", action="store_true", help="write the board (default: dry run)"
    )
    parser.add_argument(
        "--plan-out", default=None, help="write the per-row plan as JSON here"
    )
    args = parser.parse_args(argv)

    board = Path(args.board)
    history = audit.find_history(board, args.history)
    verdicts = audit.load_verdicts(history)
    by_task = audit.by_task_index(verdicts)
    earliest = min((verdict.date for verdict in verdicts.values()), default="")
    lines = board.read_text(encoding="utf-8").splitlines()

    plan: list[dict] = []
    output: list[str] = []
    touched = 0
    labels_cleaned = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            output.append(line)
            continue
        row = json.loads(line)
        if row.get("status") != "complete":
            output.append(line)
            continue
        decision = audit.classify(row, verdicts, by_task, earliest)
        original = json.loads(line)
        if decision.state != audit.BOUND:
            # AC3: a row with no usable citation is backfilled from a REAL artifact when one exists
            # for its task id. The audit only measures what a row CLAIMS, so this promotion lives
            # here, in the writer, and uses the audit's own deterministic picker.
            own = set(by_task.get(row["id"], []))
            if own:
                decision.state = audit.BOUND
                decision.run_id = audit.pick_run(own, verdicts)
                decision.passed = verdicts[decision.run_id].passed
                decision.legacy = False
        if decision.state == audit.BOUND:
            value = decision.run_id
            state = audit.BOUND
        else:
            value = reason_for(row, decision, earliest)
            state = audit.NONE
            refusal = audit.check_none_reason(
                value,
                row,
                verdicts,
                by_task,
                earliest,
                {
                    r
                    for r in audit.row_runs(row, set(verdicts))
                    if verdicts[r].task_id == row["id"]
                },
                decision.foreign_mentions,
            )
            if refusal is not None:
                print(f"REFUSED {row['id']}: {refusal}", file=sys.stderr)
                return 2
        updated = dict(row)
        updated[FIELD] = value
        labels_cleaned += clean_labels(
            updated, verdicts, value if state == audit.BOUND else None
        )
        changed = updated != original or FIELD not in original
        if not changed:
            output.append(line)
            continue
        # Guard: nothing but the evidence binding and the label cleanup may move.
        strip = lambda r: {k: v for k, v in r.items() if k != FIELD}  # noqa: E731
        before, after = strip(original), strip(updated)
        before.pop("detail", None)
        after.pop("detail", None)
        if before != after:
            print(
                f"REFUSED {row['id']}: edit would change fields other than {FIELD}/detail.evidence",
                file=sys.stderr,
            )
            return 2
        new_line = serialize_like(line, updated)
        if json.loads(new_line) != updated:
            print(
                f"REFUSED {row['id']}: re-serialization does not round-trip",
                file=sys.stderr,
            )
            return 2
        output.append(new_line)
        touched += 1
        plan.append(
            {
                "line": number,
                "row": row["id"],
                "state": state,
                "evidence_run_id": value,
                "legacy_citation": decision.legacy,
                "verdict_passed": decision.passed if state == audit.BOUND else None,
            }
        )

    summary = {
        "board": str(board),
        "history": str(history),
        "verdicts": len(verdicts),
        "lines": len(lines),
        "touched": touched,
        "bound": sum(1 for item in plan if item["state"] == audit.BOUND),
        "none_reason": sum(1 for item in plan if item["state"] == audit.NONE),
        "labels_cleaned": labels_cleaned,
        "plan": plan,
    }
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "plan"},
            indent=1,
            sort_keys=True,
        )
    )
    if args.plan_out:
        Path(args.plan_out).write_text(
            json.dumps(plan, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    if not args.apply:
        print("dry run: no file written (pass --apply)")
        return 0
    board.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"wrote {board}: {len(output)} lines, {touched} rows updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
