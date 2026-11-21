#!/usr/bin/env python3
"""Refuse a commit that DROPS task rows from the JSONL board (AUG-194).

Commit 45a29b9 (a lane close-out) rewrote .coding-hermes/board/tasks.jsonl from 297 rows to 2 —
the two survivors were exactly the rows that commit closed. 295 rows went invisible for about
three minutes (261 complete, 28 pending, 2 blocked, 4 legacy "done"). The pre-commit gate did not
catch it because `boardctl validate` grades a 2-row board as VALID: a short board is a valid
board. Only a ROW-COUNT comparison against a baseline blob can see row loss, and at HEAD nothing
on the commit path made that comparison.

Modes
  staged (default)  `git show :<board>`            vs `git show HEAD:<board>`
                    the stage-0 INDEX blob — byte for byte what the commit will contain.
  --recent          `git show HEAD:<board>`        vs `git show HEAD^:<board>`
                    post-commit mode for CI, where nothing is staged.

Row counting
  One JSON object per line; blank lines are ignored. A line that does not parse — invalid JSON, a
  non-object, or no string `id` — is a PARSE FAILURE: counted and reported separately, never
  folded into the row count, so a torn line can never masquerade as a shrink and a shrink can
  never hide behind one. Rows are counted, not deduplicated.

Refusals (exit 1)
  * candidate rows < baseline rows — the commit would lose rows; the message prints BOTH counts
    and the ids that disappeared.
  * a parse failure on either side — the counts cannot be trusted.
  * the baseline cannot be resolved or read — FAIL CLOSED, naming `git fetch --deepen=1`: a
    shallow clone that cannot see the parent commit must deepen, never pass a gate it did not run.

Override
  BOARD_ALLOW_SHRINK=1|true|yes turns the refusal into an explicit NOTICE that still prints both
  counts and every lost id and exits 0. A deliberate prune is reviewable precisely because it has
  to say so out loud.

HONEST LIMIT — this is the contract, not a to-do list
  A SAME-COUNT wholesale replacement is NOT detected. Replace all 297 rows with 297 different rows
  and this check compares equal, because the only invariant evaluated here is the row COUNT. A
  same-count rewrite has to be caught by reading the diff. What this check exists to stop is the
  failure that actually happened (297 rows became 2) and that no other gate could see.

AC4 — the truncating writer
  The writer behind 45a29b9 lives OUTSIDE this repository and is not any tracked fleet helper, so
  it cannot be patched here; it was an ad-hoc lane-session close snippet that persisted its own
  two-row set over the whole file. Evidence: every writer in this repo and in
  ~/.hermes/skills/coding-hermes-foreman/scripts/ that opens tasks.jsonl either writes LINE-WISE
  (scripts that only touch the matching line) or rewrites the WHOLE loaded row list
  (append_board_task_dispatched.py, update_board_task_notes.py, close_tick_jsonl_board.py), and
  `grep -rn 'tasks.jsonl' --include='*.py' --include='*.sh' .` finds no writer here that persists
  a filtered subset. What the incident needed is therefore a quarantine, not a patch: a wholesale
  rewrite can no longer LAND without BOARD_ALLOW_SHRINK, wherever it comes from.

Usage
  python3 scripts/board_shrink_check.py          # staged index vs HEAD (pre-commit hook)
  python3 scripts/board_shrink_check.py --recent # HEAD vs HEAD^ (CI)
  BOARD_ALLOW_SHRINK=1 git commit ...            # deliberate prune

Exit codes: 0 append / unchanged count / explicit override; 1 shrink, parse failure, or an
impossible comparison (fail closed).
"""

import argparse
import json
import os
import subprocess
import sys

BOARD = ".coding-hermes/board/tasks.jsonl"
ALLOW_ENV = "BOARD_ALLOW_SHRINK"
TRUTHY = ("1", "true", "yes")
DEEPEN_HINT = "git fetch --deepen=1"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def resolve(revision):
    """Full sha for `revision` when it names a commit, else None."""
    result = git("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def having_commits():
    return resolve("HEAD") is not None


def unmerged(path):
    return bool(git("ls-files", "-u", "--", path).stdout.strip())


def read_blob(revision, path):
    """Text of `<revision>:<path>`, where revision "" means the staged index.

    Returns "" when the board is simply absent at that revision (it is being added or deleted) —
    an absent board has 0 rows, it is not an error. Raises ValueError only when the blob cannot
    be READ, which is the fail-closed case.
    """
    if revision:
        if resolve(revision) is None:
            if revision == "HEAD" and not having_commits():
                # A repository's first commit has no baseline to shrink from.
                return ""
            raise ValueError(
                f"cannot resolve {revision}:{path}; the row count has no baseline to compare "
                f"against (a shallow clone must run {DEEPEN_HINT} and re-run — this check must "
                f"not pass blind)"
            )
    result = git("show", f"{revision}:{path}")
    if result.returncode == 0:
        return result.stdout
    if unmerged(path):
        raise ValueError(
            f"{path} is unmerged in the index; resolve the conflict before committing"
        )
    return ""


def rows(text, label):
    ids = set()
    count = errors = 0
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not a JSON object")
            ident = row.get("id")
            if not isinstance(ident, str) or not ident:
                raise ValueError("missing string id")
        except (ValueError, TypeError) as exc:
            print(
                f"board-shrink: {label} line {number}: invalid task row: {exc}",
                file=sys.stderr,
            )
            errors += 1
            continue
        count += 1
        ids.add(ident)
    return count, ids, errors


def summary(old, new, lost):
    return (
        f"baseline={old} candidate={new} "
        f"lost ids={', '.join(lost) if lost else '(none)'}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--recent",
        action="store_true",
        help="compare HEAD^ to HEAD (CI, nothing staged)",
    )
    parser.add_argument("--path", default=BOARD, help=f"board path (default: {BOARD})")
    args = parser.parse_args()
    path = args.path

    try:
        old = rows(read_blob("HEAD^" if args.recent else "HEAD", path), "baseline")
        new = rows(read_blob("HEAD" if args.recent else "", path), "candidate")
    except (ValueError, OSError) as exc:
        print(f"board-shrink: {exc}", file=sys.stderr)
        return 1

    lost = sorted(old[1] - new[1])
    report = summary(old[0], new[0], lost)

    if old[2] or new[2]:
        print(
            f"board-shrink: parse failures baseline={old[2]} candidate={new[2]}; "
            f"fix invalid rows ({report})",
            file=sys.stderr,
        )
        return 1

    if new[0] < old[0]:
        override = os.environ.get(ALLOW_ENV, "").strip().lower()
        if override in TRUTHY:
            print(f"board-shrink: NOTICE {ALLOW_ENV} override: {report}")
            print(
                f"board-shrink: SHRINK ACCEPTED — {old[0] - new[0]} task row(s) dropped "
                f"({old[0]} -> {new[0]}); the ids above are gone from the board."
            )
            return 0
        print(f"board-shrink: REFUSED {report}", file=sys.stderr)
        print(
            f"board-shrink: the commit would drop {old[0] - new[0]} task row(s) "
            f"({old[0]} -> {new[0]}); {BOARD} must be updated in place (append, or rewrite only "
            f"the changed lines) — never replaced with a filtered row set.",
            file=sys.stderr,
        )
        if lost:
            print(
                f"board-shrink: lost ids ({len(lost)}): {', '.join(lost)}",
                file=sys.stderr,
            )
        print(
            f"board-shrink: deliberate prune? re-run with {ALLOW_ENV}=1 and say why.",
            file=sys.stderr,
        )
        return 1

    if new[0] > old[0]:
        print(f"board-shrink: OK {report} — {new[0] - old[0]} row(s) appended")
    else:
        print(
            f"board-shrink: OK {report} — row count unchanged (a same-count wholesale "
            f"replacement is NOT detected; read the diff)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
