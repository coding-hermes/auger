#!/usr/bin/env python3
"""AUG-200: the dispatch-path enrichment hook — one command from a board row id
to the brief-ready spec section the builder sees.

The spec-query convention (documented, machine-readable):

  A board row carries its auger spec query at `detail.metadata.spec_query` as a
  JSON object:

      "spec_query": {
        "project":      "P-CHD-SPEC-SYSTEM",   # required when `recall` is set: the -p target
        "recall":       "<free text>",         # optional: decision-provenance query
        "recall_limit": 5,                     # optional: default 5
        "spec_task":    "<corpus task id>"     # optional: governing blocks via spec context
      }

  The object must set at least one of `recall` / `spec_task`; unknown keys are a
  typed refusal (a typo must not read as "no query"). A row without the key, a
  `detail` that is not an object, or an empty `metadata` resolves to `no-spec`:
  the brief is left unchanged (zero section bytes, never a placeholder) with an
  explicit marker on stderr and in `--status-json`.

The resolver uses ONLY the real surfaces — never a re-implementation:

  * `auger.build_spec_context(spec_root, task=<spec_task>)` for the governing
    blocks: the task, its in-scope closure, and the explicit exclusions WITH
    their reasons (the judged AUG-122 answer);
  * `auger.recall(ns, query, limit, project_id=...)` for decision provenance,
    with the retrieval tier attributed (AUG-092) and superseded decisions
    marked (AUG-078) so a builder never silently builds on a mooted decision;
  * the module's own block loader, so emitted block text is the corpus's bytes.

The emitted section names block ids and provenance sources (decision ids and
their store keys). It is never a whole-corpus dump: exclusions are listed by id
with their reason, decisions are capped at `recall_limit`.

Exit-code contract (what a dispatch path keys on):

  0  an answer was produced. Either the section was written (`context`), or the
     answer was explicitly empty (`no-spec` / `no-context`): the section file
     gets ZERO bytes, one honest line goes to stderr, and the machine-readable
     outcome goes to `--status-json` when given. The row's own acceptance
     criteria ride inside every written section, so the builder sees the
     contract and the governing spec together.
  3  `ambiguous` spec context. Nothing is invented — the section file is left
     ZERO bytes (never a placeholder) and stderr names the candidate ids so
     the tick report surfaces the ambiguity instead of a brief that guessed.
  2  a typed refusal: malformed `spec_query`, row not found / duplicated on the
     board, a `spec_task` that names nothing real (`unscoped`) or only inactive
     blocks (`inactive`) — an explicitly-filed query that resolves to nothing
     must surface, not silently produce an empty brief — a corpus SpecError, or
     a substrate SubstrateError (auger's fail-closed AUG-075 wording, unchanged).
     The section file is left ZERO bytes (never a placeholder): the dispatch
     loop keys on the exit code; a composer that keys on the file still reads
     an explicitly-empty section, and the refusal is on stderr + --status-json.

ch:trace row=AUG-200 spec=docs/prd/xml-spec-graph.md#2-selection-and-assembly test=tests/test_dispatch_spec_enrich.py evidence=evidence/AUG-200/ verdict=none:not-judged commit=none:pre-commit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AMBIGUOUS_EXIT = 3
REFUSAL_EXIT = 2
DEFAULT_RECALL_LIMIT = 5
SPEC_QUERY_KEYS = {"project", "recall", "recall_limit", "spec_task"}
SNIPPET_CHARS = 200


class SpecQueryError(ValueError):
    """A typed refusal: the row's spec_query is malformed or unresolvable."""


def load_row(board_path: str | Path, row_id: str) -> dict:
    """The board row with exactly this id — typed refusals on zero or many."""
    path = Path(board_path)
    if not path.is_file():
        raise SpecQueryError(f"board file not found: {path}")
    matches: list[tuple[int, dict]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id") == row_id:
                matches.append((line_no, row))
    if not matches:
        raise SpecQueryError(f"row {row_id} not found in {path}")
    if len(matches) > 1:
        where = ", ".join(str(n) for n, _ in matches)
        raise SpecQueryError(
            f"row {row_id} appears on multiple lines ({where}) — ambiguous, refusing"
        )
    return matches[0][1]


def spec_query_of(row: dict) -> dict | None:
    """The row's validated spec query, or None when the row carries none.

    Fail-closed on shape: an object with unknown keys, a `recall` without its
    `project`, a non-positive `recall_limit`, or a non-string query text is a
    typed SpecQueryError — never read as "no query" and never guessed around.
    """
    detail = row.get("detail")
    if detail is None:
        return None
    if not isinstance(detail, dict):
        return None  # a string/legacy detail carries no machine-readable query
    metadata = detail.get("metadata")
    if metadata is None or metadata == {}:
        return None
    if not isinstance(metadata, dict):
        return None
    query = metadata.get("spec_query")
    if query is None:
        return None
    if not isinstance(query, dict):
        raise SpecQueryError(
            f"spec_query must be an object, got {type(query).__name__}"
        )
    unknown = sorted(set(query) - SPEC_QUERY_KEYS)
    if unknown:
        raise SpecQueryError(f"spec_query has unknown key(s): {', '.join(unknown)}")
    recall = query.get("recall")
    spec_task = query.get("spec_task")
    if recall is None and spec_task is None:
        raise SpecQueryError("spec_query sets neither 'recall' nor 'spec_task'")
    for key, value in (("recall", recall), ("spec_task", spec_task)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise SpecQueryError(f"spec_query.{key} must be a non-empty string")
    project = query.get("project")
    if recall is not None and (not isinstance(project, str) or not project.strip()):
        raise SpecQueryError(
            "spec_query.recall requires a non-empty 'project' (the auger -p target)"
        )
    limit = query.get("recall_limit", DEFAULT_RECALL_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise SpecQueryError(
            f"spec_query.recall_limit must be a positive integer, got {limit!r}"
        )
    return {
        "project": project,
        "recall": recall,
        "recall_limit": limit,
        "spec_task": spec_task,
    }


def plan_section(
    row_id: str,
    query: dict | None,
    acceptance: str | list | None,
    context_answer: dict | None,
    recall_hits: list | None,
    texts: dict,
    tier: str | None,
    namespace: str = "",
) -> tuple[str, dict, int]:
    """The pure decision: (section, status_payload, exit_code) for one row.

    Four outcome classes and nothing else: `no-spec` (the row carries no query —
    unchanged brief), `context` (a section was built from real surfaces),
    `ambiguous` (exit 3 demanded), and `refused` (an explicit query that names
    nothing real, or a typed auger refusal — exit 2). An explicitly-empty
    retrieval (`no-context`: the surfaces answered, both answers empty) stays
    exit 0 with the empty answer surfaced in the payload and on stderr.
    """
    if query is None:
        return "", {"row": row_id, "outcome": "no-spec"}, 0

    status = (context_answer or {}).get("status")
    reason = (context_answer or {}).get("reason") or ""
    if status == "ambiguous":
        payload = {
            "row": row_id,
            "outcome": "ambiguous",
            "status": status,
            "reason": reason,
            "candidates": list((context_answer or {}).get("candidates") or []),
            "recall_hits": None,
            "decision_ids": [],
        }
        return "", payload, AMBIGUOUS_EXIT
    if status in ("unscoped", "inactive"):
        # The query was EXPLICITLY filed on the row; naming nothing real must
        # surface, not silently degrade to an unchanged brief.
        payload = {
            "row": row_id,
            "outcome": "refused",
            "status": status,
            "reason": reason,
            "recall_hits": None,
            "decision_ids": [],
        }
        return "", payload, REFUSAL_EXIT

    selected = (context_answer or {}).get("selected") or []
    hits = recall_hits or []
    if not selected and not hits:
        payload = {
            "row": row_id,
            "outcome": "no-context",
            "status": status,
            "reason": reason,
            "recall_hits": 0,
            "decision_ids": [],
        }
        return "", payload, 0

    lines: list[str] = []
    counts = (context_answer or {}).get("counts") or {}
    sources = []
    if context_answer is not None:
        sources.append(f"auger spec context --task {query['spec_task']}")
    if recall_hits is not None:
        sources.append(f"auger recall --limit {query['recall_limit']}")
    lines.append(
        f"## DISPATCH SPEC CONTEXT — row {row_id}"
        f" (project {query.get('project') or 'n/a'})"
    )
    lines.append("")
    lines.append(f"_Resolved via: {'; '.join(sources)}._")
    lines.append("")

    # --- the row's own acceptance contract, so the builder sees it with the spec
    if acceptance:
        lines.append("### Row acceptance criteria")
        lines.append("")
        if isinstance(acceptance, list):
            for item in acceptance:
                lines.append(f"- {item}")
        else:
            lines.append(str(acceptance))
        lines.append("")

    # --- governing blocks with closure and exclusion reasons
    if context_answer is not None:
        lines.append(
            f"### Governing spec blocks ({counts.get('selected', len(selected))}"
            f" block(s) selected, {counts.get('dependencies', 0)} dependency,"
            f" corpus input {counts.get('input_bytes', 0)} B)"
        )
        lines.append("")
        for entry in selected:
            text = texts.get(entry["id"], "")
            lines.append(
                f"#### {entry['id']} — {entry['kind']} ({len(text.encode('utf-8'))} B)"
            )
            lines.append("")
            lines.append(text)
            lines.append("")
        excluded = (context_answer or {}).get("excluded") or []
        for exc in excluded:
            exc_id = exc.get("id", "?")
            exc_why = exc.get("reason") or exc.get("why") or "-"
            lines.append(f"> excluded: {exc_id} — {exc_why}")
        if excluded:
            lines.append("")

    # --- decision provenance: ids, scores, tier, supersession marks
    if recall_hits is not None:
        tier_line = (
            auger.tier_note(tier)
            if tier
            else "retrieval tier: unattributed (/health not read)"
        )
        lines.append(f"### Top decisions ({tier_line})")
        lines.append("")
        if not hits:
            lines.append(
                "no decisions matched — the store answered empty (a real empty,"
                " not a failure)"
            )
        for hit in hits:
            key = hit.get("key", "")
            did = auger.decision_named(key) or "?"
            score = hit.get("score")
            score_s = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
            superseded, successor = _supersession(namespace, did)
            mark = f" [SUPERSEDED by {successor}]" if superseded else ""
            content = (hit.get("content", "") or "")[:SNIPPET_CHARS]
            lines.append(f"- {score_s} **{did}**{mark} (`{key}`): {content}")
        lines.append("")

    payload = {
        "row": row_id,
        "outcome": "context",
        "status": status,
        "reason": reason,
        "selected": len(selected),
        "excluded": len((context_answer or {}).get("excluded") or []),
        "recall_hits": len(hits),
        "decision_ids": [auger.decision_named(h.get("key", "")) for h in hits],
        "candidates": [],
    }
    return "\n".join(lines) + "\n", payload, 0


def _supersession(namespace: str, decision_id: str) -> tuple[bool, str]:
    """Whether `decision_id` is superseded — decorate only, never fail the section."""
    if not namespace or not decision_id:
        return (False, "")
    try:
        return auger.supersession_for(namespace, decision_id)
    except Exception:
        return (False, "")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dispatch_spec_enrich",
        description=(
            "Resolve a board row's spec query into the brief-ready section the"
            " builder sees (AUG-200)."
        ),
    )
    parser.add_argument("--row", required=True, help="board row id to enrich")
    parser.add_argument(
        "--board",
        default=str(REPO_ROOT / ".coding-hermes" / "board" / "tasks.jsonl"),
        help="board tasks.jsonl path (default: this repo's board)",
    )
    parser.add_argument(
        "--spec-root", default=str(REPO_ROOT / "specs"), help="spec corpus root"
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="git work-tree top level the corpus' paths are relative to",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="auger namespace for recall (default: AUGER_NS env, else 'auger')",
    )
    parser.add_argument(
        "--out", default=None, help="section output file (default: stdout)"
    )
    parser.add_argument(
        "--status-json", default=None, help="machine-readable outcome file"
    )
    args = parser.parse_args(argv)

    namespace = args.namespace or os.environ.get("AUGER_NS", "auger")

    try:
        row = load_row(args.board, args.row)
        query = spec_query_of(row)
        if query is None:
            section, payload, exit_code = plan_section(
                args.row, None, None, None, None, {}, None
            )
        else:
            context_answer = None
            recall_hits = None
            tier = None
            if query["spec_task"]:
                context_answer = auger.build_spec_context(
                    args.spec_root,
                    task=query["spec_task"],
                    repo_root=args.repo_root,
                )
            if query["recall"]:
                tier = auger.retrieval_tier()
                recall_hits = auger.recall(
                    namespace,
                    query["recall"],
                    query["recall_limit"],
                    project_id=query["project"],
                )
            section, payload, exit_code = plan_section(
                args.row,
                query,
                row.get("acceptance_criteria"),
                context_answer,
                recall_hits,
                _block_texts(args.spec_root) if context_answer is not None else {},
                tier,
                namespace=namespace,
            )
    except auger.SpecError as exc:
        return _refused(args, f"auger SpecError: {exc}")
    except auger.SubstrateError as exc:
        return _refused(args, f"substrate unavailable — cannot recall: {exc}")
    except SpecQueryError as exc:
        return _refused(args, f"spec_query refusal: {exc}")

    if args.status_json:
        _write_status(args.status_json, payload)

    if exit_code == AMBIGUOUS_EXIT:
        if args.out:
            _emit(section, args.out)
        print(
            "dispatch-spec-enrich: AMBIGUOUS context for row"
            f" {args.row} ({payload['reason']}) — candidates:"
            f" {', '.join(payload['candidates'])}; section left EMPTY, report this",
            file=sys.stderr,
        )
        return exit_code
    if payload["outcome"] == "refused":
        if args.out:
            _emit(section, args.out)
        print(
            f"dispatch-spec-enrich: refusal for row {args.row}"
            f" (status={payload.get('status')}, reason={payload.get('reason') or '-'})"
            " — section left EMPTY, report this",
            file=sys.stderr,
        )
        return exit_code
    if payload["outcome"] == "no-spec":
        _emit(section, args.out)
        print(
            f"dispatch-spec-enrich: row {args.row} carries no spec_query —"
            " brief unchanged (no-spec)",
            file=sys.stderr,
        )
        return 0
    if payload["outcome"] == "no-context":
        _emit(section, args.out)
        print(
            f"dispatch-spec-enrich: no context for row {args.row}"
            f" (status={payload.get('status')}, reason={payload.get('reason') or '-'},"
            f" recall_hits={payload.get('recall_hits')}) — nothing embedded",
            file=sys.stderr,
        )
        return 0

    _emit(section, args.out)
    print(
        f"dispatch-spec-enrich: enriched brief for row {args.row}"
        f" ({payload.get('selected', 0)} block(s),"
        f" {payload.get('recall_hits', 0)} decision(s))",
        file=sys.stderr,
    )
    return 0


def _refused(args, message: str) -> int:
    print(f"dispatch-spec-enrich: {message}", file=sys.stderr)
    if args.out:
        _emit("", args.out)
    if args.status_json:
        _write_status(
            args.status_json,
            {"row": args.row, "outcome": "refused", "error": message},
        )
    return REFUSAL_EXIT


def _emit(section: str, out: str | None) -> None:
    """Write the section — zero bytes when there is no section, never a placeholder."""
    if out:
        out_path = Path(out)
        if not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(section, encoding="utf-8")
    else:
        sys.stdout.write(section)


def _block_texts(spec_root) -> dict:
    """id -> block text via the module's own loader, so emitted bytes are the corpus's bytes."""
    _root, _documents, blocks = auger.load_spec_corpus(spec_root)
    return {block["id"]: block["text"] for block in blocks}


def _write_status(path: str, payload: dict) -> None:
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
