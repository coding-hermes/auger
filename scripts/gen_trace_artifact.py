#!/usr/bin/env python3
"""Generate the sample end-to-end `ch:trace` artifact (AUG-141 / PRD AC-15).

PRD AC-15 asks for evidence a reviewer can follow: "requirement through board row, code, test,
verdict and commit ... proof: sample end-to-end trace artifact in `docs/prd/evidence/`". A sample
written by hand is where `row=` typos and bare `none` values come from, and it rots the day a cited
test is renamed — so the sample is BUILT here from live repository facts and this script REFUSES to
write an artifact whose pointers do not land.

Every one of the eleven doctrine fields (`docs/prd/xml-spec-graph.md`, section `ch:trace`) is either

  * RESOLVED against the real repository —
      ``row``       a row id in the tracked board `.coding-hermes/board/tasks.jsonl`
      ``spec``      `path#ID` where ID is a block in `specs/` and `path` is the file it is authored in
      ``test``      `path` or `path::selector`, where the path is tracked by `git ls-files` and the
                    selector is a definition the file actually contains
      ``doc``       a tracked repository path
      ``evidence``  a tracked repository path (an artifact, never a row id)
      ``commit``    an object name in this repository that is an ancestor of HEAD
  * or an explicit ABSENCE — `none:<reason>`. A bare `none`, an empty value, or a value this
    repository has no resolver for is refused (`unexplained-absent-field` / `unverifiable-ref`).
    ``wave``, ``prompt``, ``witness``, ``verdict`` and ``memory`` are absence-only here on purpose:
    no offline surface in this repository resolves them, and recording an unverifiable pointer
    would be exactly the silently-decaying evidence AC-15 exists to prevent.

Refusal codes (all `spec error (<code>)` on stderr, exit 2 — the same error contract every
`auger spec` verb uses):
    unknown-trace-row, unknown-spec-id, stale-spec-ref, malformed-spec-ref, unknown-test-ref,
    stale-test-ref, untracked-test-ref, unknown-ref, untracked-ref, escaping-ref, unknown-commit,
    unreachable-commit, unverifiable-ref, unexplained-absent-field, board-missing

The output is byte-stable: sorted keys, fixed indentation, no timestamps, no absolute host paths, no
interpreter path. Two runs over one tree produce identical bytes, and the committed artifact is
therefore reproducible — `--check` is what CI/tools compare against.

Usage:
    python3 scripts/gen_trace_artifact.py            # rewrite the committed artifact
    python3 scripts/gen_trace_artifact.py --check    # exit 1 when the committed file has drifted

ch:trace row=AUG-141 spec=docs/prd/xml-spec-graph.md#ch:trace wave=none:no-wave-record test=tests/test_trace_artifact.py doc=docs/prd/xml-spec-graph.md prompt=none:no-committed-prompt evidence=docs/prd/evidence/AUG-136-trace.json witness=none:worker-self-report verdict=none:no-gitreins-verdict commit=none:pre-commit memory=none:no-memory-record
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run from a checkout without installing auger
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SCHEMA_VERSION = "1.0"
GENERATED_BY = "scripts/gen_trace_artifact.py"
#: The board `spec align` and this script both read: the repository's own tracked JSONL board.
BOARD_RELATIVE = Path(".coding-hermes") / "board" / "tasks.jsonl"
ARTIFACT = REPO_ROOT / "docs" / "prd" / "evidence" / "AUG-136-trace.json"

#: The trace this script reproduces: one REAL row, one REAL spec block, one REAL tracked test, one
#: REAL commit. Each value is a CLAIM; the resolvers below are what make it evidence. A link that
#: genuinely does not apply says so and says why.
DEFAULT_TRACE = {
    "row": "AUG-136",
    "spec": "specs/spec-graph.xml#SPEC-GRAPH-CORPUS",
    "wave": "none:no-wave-record",
    "test": "tests/test_spec_corpus_view.py",
    "doc": "docs/prd/xml-spec-graph.md",
    "prompt": "none:no-committed-prompt",
    "evidence": "docs/SPEC-CORPUS-VIEW.md",
    "witness": "none:no-independent-witness",
    "verdict": "none:no-gitreins-verdict",
    "commit": "3f64a4e",
    "memory": "none:no-memory-record",
}

#: Fields whose only legal value is an explicit absence (`none:<reason>`).
ABSENCE_ONLY = ("wave", "prompt", "witness", "verdict", "memory")
#: Fields that name a committed repository path.
PATH_FIELDS = ("doc", "evidence")


# ------------------------------------------------------------------------------------- primitives
def _display(path: Path, repo_root: Path) -> str:
    """A path as the artifact records it: repository-relative, never absolute."""
    try:
        return str(Path(path).resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return Path(path).name


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _tracked(repo_root: Path, relative: str) -> bool:
    return (
        _git(repo_root, "ls-files", "--error-unmatch", "--", relative).returncode == 0
    )


def _inside(repo_root: Path, raw: str, field: str) -> Path:
    """A repository-relative path, refused when it is absolute or climbs out of the repository."""
    candidate = Path(raw)
    if candidate.is_absolute():
        raise auger.SpecError(
            "escaping-ref",
            f"{field}={raw}: a trace pointer is repository-relative — an absolute path resolves "
            "on one host and nowhere else",
        )
    target = (Path(repo_root) / candidate).resolve()
    if not target.is_relative_to(Path(repo_root).resolve()):
        raise auger.SpecError(
            "escaping-ref", f"{field}={raw}: resolves outside the repository root"
        )
    return target


# -------------------------------------------------------------------------------------- resolvers
def resolve_row(value: str, repo_root: Path) -> str:
    board = Path(repo_root) / BOARD_RELATIVE
    if not board.is_file():
        raise auger.SpecError(
            "board-missing",
            f"{_display(board, repo_root)} is missing — the row pointer cannot be proved without "
            "the tracked board",
        )
    rows: set = set()
    for line in board.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows.add(row["id"])
    if value not in rows:
        raise auger.SpecError(
            "unknown-trace-row",
            f"row={value} is not a row in {_display(board, repo_root)} — a trace that names no "
            "board row cannot be closed by one",
        )
    return f"board-row {BOARD_RELATIVE.as_posix()}"


def resolve_spec(value: str, repo_root: Path) -> str:
    relative, separator, block_id = value.partition("#")
    if not separator or not relative or not block_id:
        raise auger.SpecError(
            "malformed-spec-ref",
            f"spec={value}: want <path>#<block id>, the block's own source",
        )
    spec_root = Path(repo_root) / "specs"
    _root, _documents, blocks = auger.load_spec_corpus(spec_root)
    block = next((item for item in blocks if item.get("id") == block_id), None)
    if block is None:
        raise auger.SpecError(
            "unknown-spec-id",
            f"spec={value} names no block in {_display(spec_root, repo_root)}/",
        )
    expected = f"{_display(spec_root, repo_root)}/{block.get('source_file', '')}"
    if relative != expected:
        raise auger.SpecError(
            "stale-spec-ref",
            f"spec={value} names a document {block_id} is not authored in (it lives in "
            f"{expected!r}) — a stale source still resolves to something, which is why it is "
            "refused rather than accepted",
        )
    return f"spec-block {expected}#{block_id} ({block.get('kind')})"


def resolve_test(value: str, repo_root: Path) -> str:
    relative, separator, selector = value.partition("::")
    target = _inside(repo_root, relative, "test")
    if not target.is_file():
        raise auger.SpecError(
            "unknown-test-ref",
            f"test={value}: {relative} does not exist in this repository",
        )
    if not _tracked(repo_root, relative):
        raise auger.SpecError(
            "untracked-test-ref",
            f"test={value}: {relative} exists but is not tracked by git — an uncommitted test "
            "proves nothing to a reviewer",
        )
    if not separator:
        return f"tracked-test {relative}"
    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except (SyntaxError, OSError) as exc:
        raise auger.SpecError(
            "stale-test-ref", f"test={value}: {relative} could not be parsed ({exc})"
        ) from None
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    wanted = selector.split("::")[-1]
    if wanted not in names:
        raise auger.SpecError(
            "stale-test-ref", f"test={value}: {relative} defines no {wanted!r}"
        )
    return f"tracked-test {relative}::{wanted}"


def resolve_path(field: str, value: str, repo_root: Path) -> str:
    target = _inside(repo_root, value, field)
    if not target.is_file():
        raise auger.SpecError(
            "unknown-ref", f"{field}={value}: no such path in this repository"
        )
    if not _tracked(repo_root, value):
        raise auger.SpecError(
            "untracked-ref",
            f"{field}={value}: exists but is not tracked by git — evidence a reviewer cannot "
            "check out is not evidence",
        )
    return f"tracked-path {value}"


def resolve_commit(value: str, repo_root: Path) -> str:
    if _git(repo_root, "cat-file", "-e", f"{value}^{{commit}}").returncode != 0:
        raise auger.SpecError(
            "unknown-commit", f"commit={value} is not a commit in this repository"
        )
    if _git(repo_root, "merge-base", "--is-ancestor", value, "HEAD").returncode != 0:
        raise auger.SpecError(
            "unreachable-commit",
            f"commit={value} is not reachable from HEAD — a trace naming a commit nobody can "
            "reach from the published tip cannot be followed",
        )
    return f"git-commit {value} (ancestor of HEAD)"


def resolve_absence(field: str, value: str, repo_root: Path) -> str:
    if not value or value == "none":
        raise auger.SpecError(
            "unexplained-absent-field",
            f"{field}={value} gives no reason — write {field}=none:<reason>",
        )
    if not auger.SPEC_TRACE_NONE_RE.match(value):
        raise auger.SpecError(
            "unverifiable-ref",
            f"{field}={value}: this repository has no offline resolver for {field}=, so the value "
            f"could never be checked again — write {field}=none:<reason> instead",
        )
    return "explicit-absence"


RESOLVERS = {
    "row": resolve_row,
    "spec": resolve_spec,
    "test": resolve_test,
    "commit": resolve_commit,
}
for _name in PATH_FIELDS:
    RESOLVERS[_name] = lambda value, repo_root, _field=_name: resolve_path(  # noqa: E731
        _field, value, repo_root
    )
for _name in ABSENCE_ONLY:
    RESOLVERS[_name] = lambda value, repo_root, _field=_name: resolve_absence(  # noqa: E731
        _field, value, repo_root
    )


# ----------------------------------------------------------------------------------------- build
def build_artifact(trace: dict, repo_root: Path = REPO_ROOT) -> dict:
    """Resolve every field of one trace and return the artifact document.

    Fails on the FIRST unresolvable pointer: a partially resolved artifact is worse than none,
    because it looks like a chain.
    """
    unknown = [name for name in trace if name not in auger.SPEC_TRACE_FIELDS]
    if unknown:
        raise auger.SpecError(
            "unknown-trace-field",
            f"{unknown} is outside the eleven-field doctrine "
            f"({', '.join(auger.SPEC_TRACE_FIELDS)})",
        )
    missing = [name for name in auger.SPEC_TRACE_FIELDS if name not in trace]
    if missing:
        raise auger.SpecError(
            "unexplained-absent-field",
            f"the trace omits {missing} instead of stating them",
        )

    resolution: dict = {}
    for field in auger.SPEC_TRACE_FIELDS:
        value = str(trace[field]).strip()
        resolution[field] = RESOLVERS[field](value, repo_root)

    payload: dict = {
        field: str(trace[field]).strip() for field in auger.SPEC_TRACE_FIELDS
    }
    payload["schema_version"] = SCHEMA_VERSION
    payload["generated_by"] = GENERATED_BY
    # The marker is the claim, so it is stored as its TOKEN LIST rather than as one JSON string:
    # a literal `ch:trace ...` inside a JSON string is not line-parseable — the closing quote
    # lands in the last value for every line-oriented reader (auger's staged-diff checker breaks
    # the token run at the first token without `=`, so it tolerates the quote, but a reader that
    # does not would read `none:reason"`). `" ".join(marker)` is the exact marker line, and
    # tests/test_trace_artifact.py feeds it back through `auger.parse_trace_marker`.
    payload["marker"] = ["ch:trace"] + [
        f"{field}={payload[field]}" for field in auger.SPEC_TRACE_FIELDS
    ]
    payload["resolution"] = resolution
    return payload


def render(payload: dict) -> str:
    """The artifact's exact bytes: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------ main
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_trace_artifact.py",
        description="Generate the sample end-to-end ch:trace artifact (AUG-141 / PRD AC-15).",
    )
    for field, value in DEFAULT_TRACE.items():
        parser.add_argument(
            f"--{field}",
            default=value,
            help=f"the {field}= link (default: the AUG-136 sample)",
        )
    parser.add_argument(
        "--out",
        default=str(ARTIFACT),
        help="where the artifact lives (default: docs/prd/evidence/AUG-136-trace.json)",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="the repository every pointer resolves against (default: this checkout)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the on-disk artifact differs from a fresh build; write nothing",
    )
    return parser


def _diff_fields(on_disk: dict, fresh: dict) -> list:
    drifted = []
    for field in auger.SPEC_TRACE_FIELDS:
        before = on_disk.get(field)
        after = fresh.get(field)
        if before != after:
            drifted.append(f"{field}: on disk {before!r} != fresh {after!r}")
    return drifted


def main(argv: list | None = None) -> int:
    args = _parser().parse_args(argv)
    trace = {field: getattr(args, field) for field in auger.SPEC_TRACE_FIELDS}
    repo_root = Path(args.repo_root).expanduser()
    out = Path(args.out).expanduser()
    try:
        fresh = build_artifact(trace, repo_root)
    except auger.SpecError as exc:
        print(f"spec error ({exc.code}): {exc.detail}", file=sys.stderr)
        return 2
    text = render(fresh)

    if args.check:
        if not out.is_file():
            print(
                f"stale trace artifact: {_display(out, repo_root)} does not exist — regenerate "
                f"with `python3 {GENERATED_BY}`",
                file=sys.stderr,
            )
            return 1
        on_disk = out.read_text(encoding="utf-8")
        if on_disk != text:
            try:
                drifted = _diff_fields(json.loads(on_disk), fresh)
            except json.JSONDecodeError:
                drifted = ["the file is not the JSON document this generator writes"]
            print(
                f"stale trace artifact: {_display(out, repo_root)} differs from a fresh build",
                file=sys.stderr,
            )
            for line in drifted or ["bytes differ (formatting, not a field value)"]:
                print(f"  {line}", file=sys.stderr)
            print(f"regenerate: python3 {GENERATED_BY}", file=sys.stderr)
            return 1
        print(
            f"trace artifact matches a fresh build: {_display(out, repo_root)} "
            f"({len(text.encode('utf-8'))} bytes)"
        )
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, out)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(
        f"trace artifact: {_display(out, repo_root)} ({len(text.encode('utf-8'))} bytes) "
        f"sha256 {digest}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
