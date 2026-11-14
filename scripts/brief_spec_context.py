#!/usr/bin/env python3
"""AUG-165: the brief-context builder a factory foreman calls at dispatch time.

Given a board row id, resolve the row's spec context through the module's own API
(`auger.build_spec_context` — the judged `spec context` selection, never a re-implementation)
and emit the selected blocks VERBATIM as a Markdown section the worker brief embeds.

Exit-code contract (the whole point of the seam):

  0  an answer was produced. Either the section was written (a spec-referencing task) or
     the answer was explicitly empty (`unscoped`/`inactive`/zero selection): in that case
     the brief gets ZERO bytes — never a placeholder heading — one honest line goes to
     stderr, and the machine-readable outcome goes to `--status-json` when given.
  3  the answer is `ambiguous`. Nothing is emitted; stderr names the candidate ids so the
     tick report surfaces the ambiguity instead of a brief that guessed.
  2  a typed auger refusal (missing spec root, unreadable corpus, ...). The auger error
     text goes to stderr unchanged.

The block text is read with the module's own loader (`load_spec_corpus`) so the emitted
bytes are the corpus's bytes — never re-parsed, re-wrapped or summarised here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AMBIGUOUS_EXIT = 3
REFUSAL_EXIT = 2


def plan_section(row: str, answer: dict, texts: dict) -> tuple[str, dict, int]:
    """The pure decision: (section, status_payload, exit_code) for one context answer.

    Three outcome classes and nothing else: a non-empty selection builds the section (each
    block heading carries that block's own emitted byte count, the header carries the
    answer's measured `input_bytes`); an empty selection (`unscoped`/`inactive`, or `ok`
    with zero blocks) builds NOTHING and reports `no-context`; `ambiguous` builds nothing
    and demands exit 3. No other status can reach the section builder.
    """
    status = answer.get("status")
    reason = answer.get("reason") or ""
    selected = answer.get("selected") or []
    if status == "ambiguous":
        payload = {
            "row": row,
            "outcome": "ambiguous",
            "status": status,
            "reason": reason,
            "candidates": list(answer.get("candidates") or []),
        }
        return "", payload, AMBIGUOUS_EXIT
    if status in ("unscoped", "inactive") or not selected:
        payload = {
            "row": row,
            "outcome": "no-context",
            "status": status,
            "reason": reason,
        }
        return "", payload, 0

    counts = answer.get("counts") or {}
    lines = [
        f"## SPEC CONTEXT — row {row} ({counts.get('selected', len(selected))} block(s) selected,"
        f" {counts.get('dependencies', 0)} dependency, input {counts.get('input_bytes', 0)} B)",
        "",
    ]
    for entry in selected:
        text = texts[entry["id"]]
        lines.append(
            f"### {entry['id']} — {entry['kind']} ({len(text.encode('utf-8'))} B)"
        )
        lines.append("")
        lines.append(text)
        lines.append("")
    payload = {
        "row": row,
        "outcome": "context",
        "status": status,
        "reason": reason,
        "selected": len(selected),
        "candidates": [],
    }
    return "\n".join(lines), payload, 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brief_spec_context",
        description="Build the spec-context section a worker brief embeds (AUG-165).",
    )
    parser.add_argument(
        "--row", required=True, help="board row id to resolve context for"
    )
    parser.add_argument(
        "--spec-root", default="specs", help="spec corpus root (default: specs)"
    )
    parser.add_argument(
        "--repo-root", default=None, help="consuming git work-tree top level"
    )
    parser.add_argument(
        "--out", default=None, help="section output file (default: stdout)"
    )
    parser.add_argument(
        "--status-json", default=None, help="machine-readable outcome file"
    )
    args = parser.parse_args(argv)

    try:
        answer = auger.build_spec_context(
            args.spec_root, task=args.row, repo_root=args.repo_root
        )
        section, payload, exit_code = plan_section(
            args.row, answer, _block_texts(args.spec_root)
        )
    except auger.SpecError as exc:
        print(f"brief-spec-context: {exc}", file=sys.stderr)
        if args.status_json:
            _write_status(
                args.status_json,
                {"row": args.row, "outcome": "refused", "error": str(exc)},
            )
        return REFUSAL_EXIT

    if args.status_json:
        _write_status(args.status_json, payload)

    if exit_code == AMBIGUOUS_EXIT:
        _emit(section, args.out)
        print(
            "brief-spec-context: AMBIGUOUS context for row"
            f" {args.row} ({payload['reason']}) — candidates:"
            f" {', '.join(payload['candidates'])}; no section emitted, report this",
            file=sys.stderr,
        )
        return exit_code
    if payload["outcome"] == "no-context":
        _emit(section, args.out)
        print(
            f"brief-spec-context: no spec context for row {args.row}"
            f" (status={payload['status']}, reason={payload['reason'] or '-'})"
            " — nothing embedded in the brief",
            file=sys.stderr,
        )
        return 0

    _emit(section, args.out)
    return 0


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
