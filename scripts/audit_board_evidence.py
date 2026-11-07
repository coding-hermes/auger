#!/usr/bin/env python3
"""Audit that every closed board row carries machine-resolvable closure evidence (AUG-156 / goal A12).

Goal-level acceptance ``A12`` says: *every closed board row carries a resolvable evidence run id
(``.gitreins/history/<date>/<run-id>/verdict.json``); no fabricated ids, ever*.  Measured
2026-11-07 on the auger board that claim was prose, not data: 0 of 201 ``status=complete`` rows
carried a machine-checkable ``evidence_run_id``, 35 named a verdict that happened to bind to them,
6 named a verdict belonging to a DIFFERENT row, 160 named nothing at all, and one row carried the
self-declared label ``run:local-auger-RELEASE-018-2026-10-30`` that resolves to no artifact.

This script is the falsifier for that claim.  For every row with ``status=complete`` it decides ONE
of four outcomes and prints the counters; ``bound + none_reason + unresolvable == complete_rows`` by
construction and ``missing`` counts rows that say nothing:

    bound          ``evidence_run_id`` (or, on a legacy row without the field, a run id cited
                   anywhere in the row's own text) resolves to
                   ``.gitreins/history/<date>/<run-id>/verdict.json`` whose ``task_id`` EQUALS the
                   row id.  A verdict for another task is never evidence: it is ``unresolvable``.
    none_reason    ``evidence_run_id`` is ``none:<reason>`` and the reason's predicate is TRUE of
                   the repository right now (see ``NONE_REASON_RULES``).  An unexplained absence is
                   the defect this row exists to remove, so a bare ``none`` or an invented reason is
                   refused.
    unresolvable   the row cites evidence that does not resolve: an unknown run id, a verdict whose
                   ``task_id`` belongs to another row, an unaccounted foreign citation, or a
                   ``none:<reason>`` whose predicate is false.
    missing        the row cites no evidence at all and carries no annotation.

Refusal codes (``audit error (<code>)`` on stderr, exit 2 — the same error contract the ``auger
spec`` verbs use): ``board-missing``, ``board-malformed``, ``history-missing``, ``no-complete-rows``.

Usage:
    python3 scripts/audit_board_evidence.py --board .coding-hermes/board/tasks.jsonl
    python3 scripts/audit_board_evidence.py --board .coding-hermes/board/tasks.jsonl --verbose
    python3 scripts/audit_board_evidence.py --board .coding-hermes/board/tasks.jsonl --json

Exit codes: 0 every complete row is bound or carries a true ``none:<reason>`` and no row carries an
unresolvable self-declared label; 1 any missing/unresolvable/label defect; 2 usage or IO error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

NONE_PREFIX = "none:"
REASON_NO_ARTIFACT = "none:no-verdict-artifact-for-task-id"
REASON_PRE_GITREINS = "none:pre-gitreins-history-window"
FOREIGN_PREFIX = "none:cites-foreign-verdict-"
REASON_FOREIGN = FOREIGN_PREFIX + "{runs}"

# A run id is the 8-hex directory name gitreins writes under .gitreins/history/<date>/.
RUN_ID_RE = re.compile(r"^[0-9a-f]{8}$")
# Rows cite runs as bare tokens in prose ("verdict 8d67b242 (2026-10-10)") as well as by path.
RUN_TOKEN_RE = re.compile(r"\b[0-9a-f]{8}\b")
# One or more `<run-id>-task-<task-id>` segments joined by '+', so a row that cites two verdicts
# belonging to other tasks has to name both instead of hiding the second one behind the first.
FOREIGN_SEGMENT_RE = re.compile(
    r"^(?P<run>[0-9a-f]{8})-task-(?P<task>[A-Za-z0-9._-]+)$"
)

BOUND = "bound"
NONE = "none"
UNRESOLVABLE = "unresolvable"
MISSING = "missing"


class AuditError(Exception):
    """A typed refusal: ``audit error (<code>)`` on stderr, exit 2."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Verdict:
    run_id: str
    date: str
    task_id: str | None
    passed: bool | None
    path: Path
    error: str | None = None


@dataclass
class RowVerdict:
    """The audit's decision for one row."""

    row_id: str
    state: str
    run_id: str | None = None
    reason: str | None = None
    code: str | None = None
    detail: str | None = None
    legacy: bool = False
    passed: bool | None = None
    foreign_mentions: list[tuple[str, str]] = field(default_factory=list)


def iter_strings(obj: object):
    """Yield every string (keys included) inside a row, so a citation is found wherever it lives."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from iter_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_strings(value)


def load_verdicts(history: Path) -> dict[str, Verdict]:
    """Index ``.gitreins/history/<date>/<run-id>/verdict.json`` by run id.

    A run id that appears under two dates with conflicting ``task_id`` values is ambiguous and is
    kept only as an error record, so no row can bind to it.
    """
    by_run: dict[str, Verdict] = {}
    ambiguous: dict[str, str] = {}
    for path in sorted(history.glob("*/*/verdict.json")):
        run_id = path.parent.name
        date = path.parent.parent.name
        if not RUN_ID_RE.match(run_id):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            by_run[run_id] = Verdict(run_id, date, None, None, path, error=str(exc))
            continue
        task_id = data.get("task_id")
        task_id = task_id if isinstance(task_id, str) and task_id else None
        passed = data.get("passed")
        verdict = Verdict(
            run_id, date, task_id, passed if isinstance(passed, bool) else None, path
        )
        previous = by_run.get(run_id)
        if previous is not None and previous.task_id != task_id:
            ambiguous[run_id] = (
                f"run id {run_id} appears under {previous.date} (task {previous.task_id}) and "
                f"{date} (task {task_id}) with different task ids"
            )
        by_run[run_id] = verdict
    for run_id, detail in ambiguous.items():
        by_run[run_id] = Verdict(
            run_id, by_run[run_id].date, None, None, by_run[run_id].path, error=detail
        )
    return by_run


def by_task_index(verdicts: dict[str, Verdict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for run_id, verdict in verdicts.items():
        if verdict.error or not verdict.task_id:
            continue
        index.setdefault(verdict.task_id, []).append(run_id)
    return index


def row_runs(row: dict, known: set[str]) -> set[str]:
    """Every known run id the row names as a token, wherever it appears."""
    return {
        token
        for text in iter_strings(row)
        for token in RUN_TOKEN_RE.findall(text)
        if token in known
    }


def row_date(row: dict) -> str | None:
    for key in ("completed_at", "updated_at", "closed_at", "created_at"):
        value = row.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def evidence_labels(row: dict) -> list[tuple[str | None, str]]:
    """``(field, value)`` for every self-declared run label under ``detail.evidence[]``."""
    labels: list[tuple[str | None, str]] = []
    detail = row.get("detail")
    if not isinstance(detail, dict):
        return labels
    entries = detail.get("evidence")
    if not isinstance(entries, list):
        return labels
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("run_id"), str):
            labels.append(("detail.evidence[].run_id", entry["run_id"]))
    return labels


def parse_foreign_reason(reason: str) -> list[tuple[str, str]] | None:
    """``none:cites-foreign-verdict-<run>-task-<task>[+<run>-task-<task>]`` → its segments."""
    if not reason.startswith(FOREIGN_PREFIX):
        return None
    body = reason[len(FOREIGN_PREFIX) :]
    if not body:
        return None
    segments: list[tuple[str, str]] = []
    for raw in body.split("+"):
        match = FOREIGN_SEGMENT_RE.match(raw)
        if match is None:
            return None
        segments.append((match.group("run"), match.group("task")))
    return segments


def check_none_reason(
    reason: str,
    row: dict,
    verdicts: dict[str, Verdict],
    by_task: dict[str, list[str]],
    earliest: str,
    own_tokens: set[str],
    foreign: list[tuple[str, str]],
) -> str | None:
    """Return ``None`` when the reason's predicate holds, else a refusal detail.

    A closed vocabulary on purpose: every accepted ``none:<reason>`` names a fact this script
    re-checks against the repository, so an annotation cannot be invented to make the audit green.
    """
    row_id = row["id"]
    if by_task.get(row_id):
        return (
            f"{reason!r} claims no verdict exists but .gitreins/history holds "
            f"{len(by_task[row_id])} verdict(s) for task {row_id} "
            f"({', '.join(sorted(by_task[row_id]))})"
        )
    segments = parse_foreign_reason(reason)
    if segments is None and reason not in (REASON_NO_ARTIFACT, REASON_PRE_GITREINS):
        return (
            f"unknown absence reason {reason!r}; accepted: {REASON_NO_ARTIFACT}, {REASON_PRE_GITREINS}, "
            f"{FOREIGN_PREFIX}<run>-task-<task>[+<run>-task-<task>]"
        )
    if segments is not None:
        named: set[tuple[str, str]] = set()
        for run_id, task in segments:
            verdict = verdicts.get(run_id)
            if verdict is None:
                return f"{reason!r} names run {run_id}, which has no verdict artifact"
            if verdict.task_id != task:
                return f"{reason!r} names task {task} but verdict {run_id} belongs to {verdict.task_id}"
            if task == row_id:
                return f"{reason!r} names the row's own task; use a real binding instead of an absence"
            named.add((run_id, task))
        cited = set(foreign)
        unaccounted = sorted(cited - named)
        if unaccounted:
            return f"{reason!r} leaves foreign citation(s) unaccounted: " + ", ".join(
                f"{run}->{task}" for run, task in unaccounted
            )
        invented = sorted(named - cited)
        if invented:
            return f"{reason!r} names citation(s) the row does not make: " + ", ".join(
                f"{run}->{task}" for run, task in invented
            )
        return None
    if foreign:
        return (
            "row names verdict(s) belonging to other tasks ("
            + ", ".join(f"{run}->{task}" for run, task in foreign)
            + "); the absence reason must name them, e.g. "
            + FOREIGN_PREFIX
            + "+".join(f"{run}-task-{task}" for run, task in foreign)
        )
    if reason == REASON_NO_ARTIFACT:
        return None
    if reason == REASON_PRE_GITREINS:
        date = row_date(row)
        if date is None:
            return f"{reason!r} needs a completion/update date and the row has none"
        if not date < earliest:
            return (
                f"{reason!r} needs the row to predate the gitreins history window "
                f"(earliest artifact {earliest}) but the row is dated {date}"
            )
        return None
    return f"unknown absence reason {reason!r}"


def classify(
    row: dict,
    verdicts: dict[str, Verdict],
    by_task: dict[str, list[str]],
    earliest: str,
) -> RowVerdict:
    row_id = row["id"]
    known = set(verdicts)
    own_tokens = {
        run for run in row_runs(row, known) if verdicts[run].task_id == row_id
    }
    foreign = sorted(
        (run, verdicts[run].task_id or "<unreadable>")
        for run in row_runs(row, known)
        if verdicts[run].task_id != row_id
    )
    result = RowVerdict(row_id=row_id, state=MISSING, foreign_mentions=foreign)

    field_value = row.get("evidence_run_id")
    if isinstance(field_value, str) and field_value.strip():
        if field_value.startswith(NONE_PREFIX):
            reason = field_value.strip()
            refusal = check_none_reason(
                reason, row, verdicts, by_task, earliest, own_tokens, foreign
            )
            if refusal is None:
                result.state = NONE
                result.reason = reason
            else:
                result.state = UNRESOLVABLE
                result.code = "false-none-reason"
                result.detail = refusal
            return result
        run_id = field_value.strip()
        verdict = verdicts.get(run_id)
        if verdict is None:
            result.state = UNRESOLVABLE
            result.code = "unknown-run"
            result.detail = f"evidence_run_id {run_id!r} has no .gitreins/history/*/{run_id}/verdict.json"
        elif verdict.error:
            result.state = UNRESOLVABLE
            result.code = "unreadable-verdict"
            result.detail = verdict.error
        elif verdict.task_id != row_id:
            result.state = UNRESOLVABLE
            result.code = "foreign-task"
            result.detail = f"evidence_run_id {run_id} is a verdict for {verdict.task_id}, not {row_id}"
        else:
            result.state = BOUND
            result.run_id = run_id
            result.passed = verdict.passed
        return result

    # Legacy row: no machine field, so judge it by the run ids its own text cites. This keeps the
    # pre-fix board measurable (the 35/6/160 falsifier) instead of scoring every row as silent.
    if own_tokens:
        run_id = pick_run(own_tokens, verdicts)
        result.state = BOUND
        result.run_id = run_id
        result.passed = verdicts[run_id].passed
        result.legacy = True
        return result
    if foreign:
        result.state = UNRESOLVABLE
        result.code = "foreign-citation"
        result.detail = "cites only verdicts for other tasks: " + ", ".join(
            f"{r}->{t}" for r, t in foreign
        )
        return result
    return result


def pick_run(runs: set[str], verdicts: dict[str, Verdict]) -> str:
    """Deterministic choice: a passing verdict wins, then the newest date, then the run id."""
    passing = [run for run in runs if verdicts[run].passed]
    pool = passing or list(runs)
    return sorted(pool, key=lambda run: (verdicts[run].date, run), reverse=True)[0]


def find_history(board: Path, explicit: str | None) -> Path:
    if explicit:
        history = Path(explicit)
        if not history.is_dir():
            raise AuditError("history-missing", f"{history} is not a directory")
        return history
    for parent in [board.parent, *board.parent.parents]:
        candidate = parent / ".gitreins" / "history"
        if candidate.is_dir():
            return candidate
    raise AuditError("history-missing", f"no .gitreins/history found above {board}")


def load_board(board: Path) -> list[dict]:
    if not board.is_file():
        raise AuditError("board-missing", f"{board} does not exist")
    rows: list[dict] = []
    for number, line in enumerate(
        board.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AuditError("board-malformed", f"{board}:{number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise AuditError(
                "board-malformed", f"{board}:{number}: row has no string id"
            )
        rows.append(row)
    return rows


def audit(board: Path, history: Path) -> dict:
    rows = load_board(board)
    verdicts = load_verdicts(history)
    by_task = by_task_index(verdicts)
    complete = [row for row in rows if row.get("status") == "complete"]
    if not complete:
        raise AuditError("no-complete-rows", f"{board} has no row with status=complete")
    earliest = min((verdict.date for verdict in verdicts.values()), default="")

    decisions: list[RowVerdict] = []
    label_defects: list[tuple[str, str]] = []
    for row in complete:
        decision = classify(row, verdicts, by_task, earliest)
        for field_name, value in evidence_labels(row):
            if value.startswith(NONE_PREFIX):
                continue  # an explicit absence is not a claimed run
            verdict = verdicts.get(value) if RUN_ID_RE.match(value) else None
            if (
                verdict is not None
                and not verdict.error
                and verdict.task_id == row["id"]
            ):
                continue
            if verdict is not None and not verdict.error:
                label_defects.append(
                    (
                        row["id"],
                        f"{field_name}={value} resolves to task {verdict.task_id}",
                    )
                )
            else:
                label_defects.append(
                    (row["id"], f"{field_name}={value} resolves to no verdict file")
                )
        decisions.append(decision)

    counts = {BOUND: 0, NONE: 0, UNRESOLVABLE: 0, MISSING: 0}
    for decision in decisions:
        counts[decision.state] += 1
    bound_failed = [
        d.row_id for d in decisions if d.state == BOUND and d.passed is False
    ]
    legacy_bound = [d.row_id for d in decisions if d.state == BOUND and d.legacy]
    return {
        "board": str(board),
        "history": str(history),
        "rows": len(rows),
        "verdicts": len(verdicts),
        "earliest_artifact": earliest,
        "complete_rows": len(complete),
        "bound": counts[BOUND],
        "none_reason": counts[NONE],
        "unresolvable": counts[UNRESOLVABLE],
        "missing": counts[MISSING],
        "bound_failed_verdict": bound_failed,
        "bound_legacy_citation": sorted(legacy_bound),
        "label_defects": sorted(label_defects),
        "reasons": reason_histogram(decisions),
        "decisions": decisions,
        "ok": counts[MISSING] == 0
        and counts[UNRESOLVABLE] == 0
        and not label_defects
        and counts[BOUND] + counts[NONE] + counts[UNRESOLVABLE] == len(complete),
    }


def reason_histogram(decisions: list[RowVerdict]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for decision in decisions:
        if decision.state != NONE:
            continue
        key = decision.reason or ""
        if parse_foreign_reason(key) is not None:
            key = FOREIGN_PREFIX + "<run>-task-<task>[+...]"
        histogram[key] = histogram.get(key, 0) + 1
    return dict(sorted(histogram.items()))


def render(result: dict, verbose: bool) -> list[str]:
    lines = [
        f"complete_rows={result['complete_rows']} bound={result['bound']} "
        f"unresolvable={result['unresolvable']} missing={result['missing']}",
        f"none_reason={result['none_reason']} "
        f"sum=bound+none_reason+unresolvable="
        f"{result['bound'] + result['none_reason'] + result['unresolvable']} "
        f"complete_rows={result['complete_rows']}",
        f"rows={result['rows']} verdicts={result['verdicts']} "
        f"earliest_artifact={result['earliest_artifact'] or 'none'}",
        f"bound_failed_verdict={len(result['bound_failed_verdict'])} "
        f"bound_legacy_citation={len(result['bound_legacy_citation'])} "
        f"label_defects={len(result['label_defects'])}",
    ]
    for reason, count in result["reasons"].items():
        lines.append(f"reason={reason} rows={count}")
    for row_id, detail in result["label_defects"]:
        lines.append(f"LABEL {row_id} {detail}")
    for decision in result["decisions"]:
        if decision.state in {UNRESOLVABLE, MISSING}:
            lines.append(
                f"{decision.state.upper()} {decision.row_id} "
                f"{decision.code or 'silent'}: {decision.detail or 'cites no evidence and carries no none:<reason>'}"
            )
    if verbose:
        for decision in result["decisions"]:
            if decision.state == BOUND:
                passed = {True: "pass", False: "fail", None: "unknown"}[decision.passed]
                lines.append(
                    f"BOUND {decision.row_id} {decision.run_id} ({passed}"
                    f"{', legacy-prose-citation' if decision.legacy else ''})"
                )
            elif decision.state == NONE:
                lines.append(f"NONE {decision.row_id} {decision.reason}")
    lines.append("RESULT: OK" if result["ok"] else "RESULT: FAIL")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit that every status=complete board row carries resolvable verdict evidence."
    )
    parser.add_argument(
        "--board",
        default=".coding-hermes/board/tasks.jsonl",
        help="JSONL board to audit (default: %(default)s)",
    )
    parser.add_argument(
        "--history",
        default=None,
        help="gitreins history root (default: the .gitreins/history above the board)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print one line per complete row"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the result as JSON instead of text"
    )
    args = parser.parse_args(argv)

    try:
        history = find_history(Path(args.board), args.history)
        result = audit(Path(args.board), history)
    except AuditError as exc:
        print(f"audit error ({exc.code}): {exc.detail}", file=sys.stderr)
        return 2

    if args.json:
        payload = {key: value for key, value in result.items() if key != "decisions"}
        payload["decisions"] = [
            {
                "row": decision.row_id,
                "state": decision.state,
                "run_id": decision.run_id,
                "reason": decision.reason,
                "code": decision.code,
                "detail": decision.detail,
                "legacy": decision.legacy,
            }
            for decision in result["decisions"]
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(render(result, args.verbose)))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
