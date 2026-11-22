#!/usr/bin/env python3
"""Refuse a commit that DROPS task rows from the JSONL board (AUG-194).

Commit 45a29b9 (a lane close-out) rewrote .coding-hermes/board/tasks.jsonl from 297 rows to 2 —
the two survivors were exactly the rows that commit closed. 295 rows went invisible for about
three minutes (261 complete, 28 pending, 2 blocked, 4 legacy "done"). The pre-commit gate did not
catch it because `boardctl validate` grades a 2-row board as VALID: a short board is a valid
board. A comparison of row counts and ids against a baseline blob can see row loss; at the time
nothing on the commit path made that comparison.

Modes
  staged (default)  `git show :<board>`            vs `git show HEAD:<board>`
                    the stage-0 INDEX blob — byte for byte what the commit will contain.
  --recent          `git show HEAD:<board>`        vs `git show HEAD^:<board>`
                    post-commit mode for CI, where nothing is staged.
  --pre-push        `git show <remote_sha>:<board>` vs `git show <local_sha>:<board>` for every
                    ref pair the pre-push hook passes on stdin (one per line, git's wire format
                    `<local_ref> <local_oid> <remote_ref> <remote_oid>`; the swapped
                    `<local_oid> <remote_oid> <local_ref> <remote_ref>` order is tolerated).
                    This is the SECOND enforcement point (AUG-196): a commit made with
                    --no-verify can LAND a board shrink, so the push that would publish one is
                    refused as well. A line whose local oid is the null oid (a remote deletion)
                    is skipped — nothing of ours is published; a ref pair whose board blob is
                    byte-identical on both sides is skipped (the push does not change the
                    board path); when every stdin line is skipped the check exits 0. Empty
                    stdin never happens on a real push: it is a fail-closed error, never a
                    silent pass.

Row counting
  One JSON object per line; blank lines are ignored. A line that does not parse — invalid JSON, a
  non-object, or no string `id` — is a PARSE FAILURE: counted and reported separately, never
  folded into the row count, so a torn line can never masquerade as a shrink and a shrink can
  never hide behind one. Rows are counted, not deduplicated.

Refusals (exit 1)
  * candidate rows < baseline rows — the commit would lose rows; the message prints BOTH counts
    and the ids that disappeared.
  * any baseline id is missing from the candidate, even if the row count is equal or greater —
    same-count wholesale replacements and losses hidden by new rows are refused (AUG-195).
  * a parse failure on either side — the counts cannot be trusted.
  * the baseline cannot be resolved or read — FAIL CLOSED, naming `git fetch --deepen=1`: a
    shallow clone that cannot see the parent commit must deepen, never pass a gate it did not run.

Override
  BOARD_ALLOW_SHRINK=1|true|yes turns a count/identity refusal into an explicit NOTICE that still
  prints both counts and every lost id and exits 0. A deliberate prune is reviewable precisely
  because it has to say so out loud. Parse failures and impossible comparisons still fail closed.

HONEST LIMIT — this is the contract, not a to-do list
  Every distinct baseline id must survive, and the total row count must not decrease. Content
  edits of surviving ids and line reordering are allowed. This is not a content-integrity or
  per-id multiplicity check: duplicate-id rows may change while every distinct id survives and
  the total count does not fall. Other gates and diff review own those checks.

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
  python3 scripts/board_shrink_check.py --pre-push # ref pairs on stdin (pre-push hook)
  BOARD_ALLOW_SHRINK=1 git commit ...            # deliberate prune

Exit codes: 0 append / surviving ids with unchanged count / explicit override; 1 shrink, missing
baseline id, parse failure, or an impossible comparison (fail closed).
"""

import argparse
import json
import os
import re
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


def verdict(old, new, path, subject):
    """Shared refusal/override/OK contract for every mode (AUG-194/195/196)."""
    lost = sorted(old[1] - new[1])
    report = summary(old[0], new[0], lost)

    if old[2] or new[2]:
        print(
            f"board-shrink: parse failures baseline={old[2]} candidate={new[2]}; "
            f"fix invalid rows ({report})",
            file=sys.stderr,
        )
        return 1

    if new[0] < old[0] or lost:
        effect = (
            f"drop {old[0] - new[0]} task row(s)"
            if new[0] < old[0]
            else f"remove or replace {len(lost)} baseline row id(s)"
        )
        override = os.environ.get(ALLOW_ENV, "").strip().lower()
        if override in TRUTHY:
            print(f"board-shrink: NOTICE {ALLOW_ENV} override: {report}")
            print(
                f"board-shrink: SHRINK ACCEPTED — the {subject} will {effect} "
                f"({old[0]} -> {new[0]}); the ids above are gone from the board."
            )
            return 0
        print(f"board-shrink: REFUSED {report}", file=sys.stderr)
        print(
            f"board-shrink: the {subject} would {effect} "
            f"({old[0]} -> {new[0]}); {path} must be updated in place (append, or rewrite only "
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
        print(f"board-shrink: OK {report} — row count and ids unchanged")
    return 0


OID_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")


def is_oid(token):
    return bool(OID_RE.match(token))


def is_null_oid(oid):
    return is_oid(oid) and set(oid) == {"0"}


def parse_pre_push_line(fields):
    """(local_ref, local_sha, remote_ref, remote_sha) from one pre-push stdin line, else None.

    git sends `<local_ref> <local_oid> <remote_ref> <remote_oid>`. The swapped
    `<local_oid> <remote_oid> <local_ref> <remote_ref>` order — the one named on the
    board row — is tolerated so the check does not depend on which spelling the hook
    block received. Any other shape is unparseable and refuses closed.
    """
    if len(fields) != 4:
        return None
    oid_positions = [i for i, field in enumerate(fields) if is_oid(field)]
    if oid_positions == [1, 3]:
        return fields[0], fields[1], fields[2], fields[3]
    if oid_positions == [0, 1]:
        return fields[2], fields[0], fields[3], fields[1]
    return None


def run_pre_push(path):
    """Pre-push mode (AUG-196): grade every stdin ref pair that changes `path`.

    baseline = the board at the REMOTE oid (what is on the remote now); candidate = the board
    at the LOCAL oid (what the push would land). The same verdict contract as the commit
    path applies: every distinct baseline id must survive, the row count must not fall, and
    BOARD_ALLOW_SHRINK stays loud. Parse failures and unreadable refs fail closed with or
    without the override; refs whose board blob is identical are skipped, per line, and
    remote deletions carry no candidate at all.
    """
    lines = sys.stdin.read().splitlines()
    if not any(line.strip() for line in lines):
        print(
            "board-shrink: --pre-push got no ref lines on stdin; refusing to pass blind",
            file=sys.stderr,
        )
        return 1
    for number, line in enumerate(lines, 1):
        fields = line.split()
        if not fields:
            continue
        parsed = parse_pre_push_line(fields)
        if parsed is None:
            print(
                f"board-shrink: pre-push line {number}: cannot parse {line!r}; "
                "refusing to pass blind",
                file=sys.stderr,
            )
            return 1
        local_ref, local_sha, remote_ref, remote_sha = parsed
        if is_null_oid(local_sha):
            # Deleting the remote ref publishes nothing of ours — no candidate to grade.
            continue
        try:
            baseline = "" if is_null_oid(remote_sha) else read_blob(remote_sha, path)
            candidate = read_blob(local_sha, path)
        except (ValueError, OSError) as exc:
            print(
                f"board-shrink: pre-push line {number} "
                f"({local_ref} -> {remote_ref}): {exc}",
                file=sys.stderr,
            )
            return 1
        if baseline == candidate:
            # The push does not change the board path — per-line skip, not a cwd heuristic.
            continue
        code = verdict(
            rows(baseline, "baseline"), rows(candidate, "candidate"), path, "push"
        )
        if code != 0:
            return code
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--recent",
        action="store_true",
        help="compare HEAD^ to HEAD (CI, nothing staged)",
    )
    parser.add_argument(
        "--pre-push",
        action="store_true",
        help=(
            "ref pairs on stdin from the pre-push hook: remote:<board> is the baseline, "
            "local:<board> the candidate"
        ),
    )
    parser.add_argument("--path", default=BOARD, help=f"board path (default: {BOARD})")
    args = parser.parse_args()
    path = args.path

    try:
        if args.pre_push:
            return run_pre_push(path)
        old = rows(read_blob("HEAD^" if args.recent else "HEAD", path), "baseline")
        new = rows(read_blob("HEAD" if args.recent else "", path), "candidate")
    except (ValueError, OSError) as exc:
        print(f"board-shrink: {exc}", file=sys.stderr)
        return 1
    return verdict(old, new, path, "commit")


if __name__ == "__main__":
    sys.exit(main())
