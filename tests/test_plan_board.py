"""AUG-119 — the approved plan ↔ boardctl task lifecycle.

What this file pins, in the order the acceptance criteria name it:

  1. A draft is VERSIONED and DIGEST-ADDRESSED, and carries task ids, acceptance criteria,
     dependencies, source spec anchors, expected verification evidence and a trace marker.
  2. Board mutation is gated on an approval bound to THOSE BYTES: unapproved, stale and
     rejected plans refuse, and `tasks.jsonl` / `events.jsonl` stay byte-identical.
  3. After approval the write goes through the boardctl CLI (there is no second task store),
     and every row is READ BACK over `boardctl show` and compared field by field.
  4. Board status/commit/verdict evidence is reconciled INTO Auger's own ledger using only the
     read verb — Auger neither dispatches nor rewrites canonical board state.
  5. Repeated export is idempotent, a partial failure names what landed and resumes without
     duplicating, and an unapproved draft touches neither board file.
  6. The emitted `ch:trace` carries all eleven doctrine fields, with an explicit
     `none:<reason>` for every link that is honestly absent.

Two kinds of board are used. The REAL boardctl tests skip when the binary is absent (a hosted
CI runner has none) and prove the true CLI contract. The STUB tests are hermetic and always run:
they record every invocation, so "auger called boardctl create" is an assertion rather than an
inference from a file that appeared.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import auger
from conftest import run_cli, run_cli_exit

BOARDCTL = shutil.which("boardctl")
needs_boardctl = pytest.mark.skipif(
    BOARDCTL is None,
    reason="boardctl not on PATH — the canonical-board arms need the real CLI (not passed)",
)


# ---------------------------------------------------------------- fixtures + helpers
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A private plan ledger root; never the operator's real ~/.auger/plans."""
    root = tmp_path / "ledger"
    monkeypatch.setenv(auger.PLANS_DIR_ENV, str(root))
    return root


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A throwaway canonical board, bootstrapped by boardctl itself."""
    root = tmp_path / "repo"
    root.mkdir()
    init = subprocess.run(
        [BOARDCTL, "-C", str(root), "init", "--project", "probe", "--namespace", "probe"],
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    monkeypatch.delenv(auger.BOARD_DIR_ENV, raising=False)
    return root


def board_files(board: Path) -> tuple:
    """The two canonical files as BYTES — a missing file counts as empty, never as absent."""
    board_dir = board / ".coding-hermes" / "board"
    return tuple(
        (board_dir / name).read_bytes() if (board_dir / name).exists() else b""
        for name in ("tasks.jsonl", "events.jsonl")
    )


def board_rows(board: Path) -> list:
    path = board / ".coding-hermes" / "board" / "tasks.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def board_row(board: Path, task_id: str) -> dict:
    for row in board_rows(board):
        if row.get("id") == task_id:
            return row
    raise AssertionError(f"{task_id} is not on the board")


def task(task_id, **overrides) -> dict:
    base = {
        "id": task_id,
        "title": f"{task_id} title",
        "priority": "P2",
        "complexity": 2,
        "acceptance_criteria": [f"1. {task_id} does the thing.", "2. It is proved."],
        "spec_anchors": ["docs/prd/xml-spec-graph.md#state-model"],
        "verification": ["bash tests/gate.sh"],
    }
    base.update(overrides)
    return base


def plan_file(tmp_path, tasks, plan_id="AUG-PLAN-1", **extra) -> str:
    doc = {"plan_id": plan_id, "project": "auger", "tasks": tasks}
    doc.update(extra)
    path = tmp_path / f"{plan_id}-{len(tasks)}.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(path)


def draft(plan_id: str, source: str) -> tuple:
    return run_cli(["plan", "draft", "--plan", plan_id, "--from", source])


def approve(plan_id: str, *extra) -> tuple:
    return run_cli(["plan", "approve", "--plan", plan_id, "--by", "reviewer", *extra])


def export(plan_id: str, board_root, *extra) -> tuple:
    return run_cli(
        ["plan", "export", "--plan", plan_id, "--board", str(board_root), *extra]
    )


def trace_of(reasoning: str) -> dict:
    marker = next(
        (ln for ln in reasoning.splitlines() if ln.startswith("ch:trace ")), None
    )
    assert marker is not None, f"no ch:trace marker in {reasoning!r}"
    return dict(re.findall(r"(\w+)=(\S+)", marker))


STUB_BOARDCTL = '''#!/usr/bin/env python3
"""Stand-in boardctl: the same argv contract, a JSONL board, and a verb log."""
import json, os, sys

argv = sys.argv[1:]
log = os.environ["STUB_BOARDCTL_LOG"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")

if len(argv) < 2 or argv[0] != "-C":
    sys.stderr.write("stub boardctl: expected -C <board>\\n")
    raise SystemExit(2)
board = os.path.join(argv[1], ".coding-hermes", "board")
os.makedirs(board, exist_ok=True)
tasks_path = os.path.join(board, "tasks.jsonl")
events_path = os.path.join(board, "events.jsonl")
verb, rest = argv[2], argv[3:]


def load():
    if not os.path.exists(tasks_path):
        return []
    out = []
    with open(tasks_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def dump(rows):
    with open(tasks_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\\n")


def flags(rest, names):
    values, i = {}, 0
    while i < len(rest):
        if rest[i] in names:
            values[rest[i]] = rest[i + 1]
            i += 2
        else:
            i += 1
    return values


def event(kind, task_id):
    with open(events_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_type": kind, "task_id": task_id}) + "\\n")


rows = load()
if verb == "show":
    tid = rest[0]
    for row in rows:
        if row["id"] == tid:
            print(json.dumps(row, indent=2, sort_keys=True))
            raise SystemExit(0)
    sys.stderr.write('boardctl: task "%s" not found (searched tasks.jsonl)\\n' % tid)
    raise SystemExit(1)
if verb == "list":
    print(json.dumps(rows, indent=2, sort_keys=True))
    raise SystemExit(0)
if verb == "create":
    got = flags(rest, {"--id", "--title", "--priority", "--complexity", "--depends-on",
                       "--reasoning", "--acceptance-criteria", "--capability-tags"})
    tid = got["--id"]
    if any(row["id"] == tid for row in rows):
        sys.stderr.write('boardctl: task id "%s" already exists\\n' % tid)
        raise SystemExit(1)
    if os.environ.get("STUB_BOARDCTL_FAIL_ID") == tid:
        sys.stderr.write("boardctl: injected create failure for %s\\n" % tid)
        raise SystemExit(1)
    rows.append({
        "id": tid,
        "title": got.get("--title", ""),
        "priority": got.get("--priority", "P2"),
        "complexity": int(got.get("--complexity", 2)),
        "depends_on": [d for d in got.get("--depends-on", "").split(",") if d],
        "capability_tags": [t for t in got.get("--capability-tags", "").split(",") if t],
        "acceptance_criteria": got.get("--acceptance-criteria", ""),
        "reasoning": got.get("--reasoning", ""),
        "status": "pending",
        "worker_status": "pending",
        "commit_hash": None,
        "guard_result": None,
        "ci_result": None,
        "blocked_reason": None,
        "detail": {},
    })
    dump(rows)
    event("task_created", tid)
    print("created task %s" % tid)
    raise SystemExit(0)
if verb == "update":
    tid = rest[0]
    got = flags(rest, {"--title", "--priority", "--acceptance-criteria", "--commit-hash",
                       "--guard", "--ci", "--worker-status"})
    for row in rows:
        if row["id"] == tid:
            mapping = {"--title": "title", "--priority": "priority",
                       "--acceptance-criteria": "acceptance_criteria",
                       "--commit-hash": "commit_hash", "--guard": "guard_result",
                       "--ci": "ci_result", "--worker-status": "worker_status"}
            for flag, key in mapping.items():
                if flag in got:
                    row[key] = got[flag]
            dump(rows)
            event("task_updated", tid)
            print("updated task %s" % tid)
            raise SystemExit(0)
    sys.stderr.write('boardctl: task "%s" not found\\n' % tid)
    raise SystemExit(1)
sys.stderr.write("stub boardctl: unsupported verb %r\\n" % verb)
raise SystemExit(2)
'''


@pytest.fixture
def stub_boardctl(tmp_path, monkeypatch):
    """A recording boardctl stand-in plus the board it owns. Always runs, no real CLI needed."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "boardctl"
    binary.write_text(STUB_BOARDCTL, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "boardctl-calls.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("STUB_BOARDCTL_LOG", str(log))
    monkeypatch.delenv(auger.BOARDCTL_ENV, raising=False)
    monkeypatch.delenv("STUB_BOARDCTL_FAIL_ID", raising=False)
    board_root = tmp_path / "stubboard"
    (board_root / ".coding-hermes" / "board").mkdir(parents=True)

    class Stub:
        root = board_root
        log_path = log

        @staticmethod
        def verbs() -> list:
            if not log.exists():
                return []
            return [json.loads(line)[2] for line in log.read_text().splitlines() if line]

    return Stub()


# ---------------------------------------------------------------- 1. drafting (offline)
def test_draft_versions_and_digests_are_content_addressed(ledger, tmp_path):
    source = plan_file(tmp_path, [task("AUG-901")])
    rc, out = draft("AUG-PLAN-1", source)
    assert rc == 0 and "v1 digest=" in out
    first = auger.plan_ledger_read("AUG-PLAN-1")

    changed = plan_file(tmp_path, [task("AUG-901", title="A different title")])
    rc, out = draft("AUG-PLAN-1", changed)
    assert rc == 0 and "v2 digest=" in out
    events = auger.plan_ledger_read("AUG-PLAN-1")

    assert [event["version"] for event in events if event["kind"] == "draft"] == [1, 2]
    digests = [event["digest"] for event in events if event["kind"] == "draft"]
    assert digests[0] != digests[1]
    # The digest is taken over the canonical plan body, so it is reproducible from the file.
    assert digests[1] == auger.plan_digest(events[-1]["plan"])
    assert first[0]["plan"]["tasks"][0]["id"] == "AUG-901"
    assert len(digests[0]) == 64


def test_draft_carries_criteria_dependencies_anchors_and_evidence(ledger, tmp_path):
    tasks = [
        task("AUG-901"),
        task("AUG-902", depends_on=["AUG-901"], verification=["pytest tests/test_plan_board.py"]),
    ]
    rc, out = draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert rc == 0, out
    plan = auger.plan_state("AUG-PLAN-1")["plan"]
    first, second = plan["tasks"]
    assert first["acceptance_criteria"] == [
        "1. AUG-901 does the thing.",
        "2. It is proved.",
    ]
    assert second["depends_on"] == ["AUG-901"]
    assert first["spec_anchors"] == ["docs/prd/xml-spec-graph.md#state-model"]
    assert second["verification"] == ["pytest tests/test_plan_board.py"]
    assert first["trace"]["row"] == "AUG-901"


def test_draft_refuses_a_task_id_boardctl_would_reject(ledger, tmp_path):
    rc, message, _ = run_cli_exit(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", plan_file(tmp_path, [task("aug-901")])]
    )
    assert rc != 0
    assert "boardctl" in message and "aug-901" in message
    assert auger.plan_ledger_read("AUG-PLAN-1") == []


def test_draft_refuses_a_blank_acceptance_criteria_list(ledger, tmp_path):
    tasks = [task("AUG-901", acceptance_criteria=["   ", ""])]
    rc, message, _ = run_cli_exit(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", plan_file(tmp_path, tasks)]
    )
    assert rc != 0 and "acceptance_criteria is required" in message


def test_draft_refuses_a_blank_trace_value_instead_of_claiming_a_link(ledger, tmp_path):
    tasks = [task("AUG-901", trace={"test": "  "})]
    rc, message, _ = run_cli_exit(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", plan_file(tmp_path, tasks)]
    )
    assert rc != 0 and "blank" in message and "none:<reason>" in message


def test_draft_refuses_none_without_a_reason(ledger, tmp_path):
    tasks = [task("AUG-901", trace={"evidence": "none:"})]
    rc, message, _ = run_cli_exit(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", plan_file(tmp_path, tasks)]
    )
    assert rc != 0 and "carries no reason" in message


def test_draft_refuses_a_task_with_no_spec_anchor_and_no_explicit_none(ledger, tmp_path):
    tasks = [task("AUG-901", spec_anchors=[])]
    rc, message, _ = run_cli_exit(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", plan_file(tmp_path, tasks)]
    )
    assert rc != 0 and "spec anchor" in message


def test_draft_of_an_unchanged_plan_does_not_mint_a_version(ledger, tmp_path):
    source = plan_file(tmp_path, [task("AUG-901")])
    draft("AUG-PLAN-1", source)
    rc, out = draft("AUG-PLAN-1", source)
    assert rc == 0 and "unchanged" in out
    drafts = [e for e in auger.plan_ledger_read("AUG-PLAN-1") if e["kind"] == "draft"]
    assert len(drafts) == 1


def test_topological_order_puts_dependencies_first():
    tasks = [
        auger.normalize_plan_task(task("AUG-903", depends_on=["AUG-902"]), 0),
        auger.normalize_plan_task(task("AUG-901"), 1),
        auger.normalize_plan_task(task("AUG-902", depends_on=["AUG-901"]), 2),
    ]
    assert [t["id"] for t in auger.plan_topological_order(tasks)] == [
        "AUG-901",
        "AUG-902",
        "AUG-903",
    ]


def test_topological_order_refuses_a_cycle_by_name():
    tasks = [
        auger.normalize_plan_task(task("AUG-901", depends_on=["AUG-902"]), 0),
        auger.normalize_plan_task(task("AUG-902", depends_on=["AUG-901"]), 1),
    ]
    with pytest.raises(SystemExit) as exc:
        auger.plan_topological_order(tasks)
    assert "dependency cycle" in str(exc.value)
    assert "AUG-901" in str(exc.value) and "AUG-902" in str(exc.value)


def test_trace_marker_names_all_eleven_fields_and_every_absent_one_by_reason():
    normalized = auger.normalize_plan_task(task("AUG-901"), 0)
    marker = auger.plan_trace_marker(normalized)
    fields = trace_of(marker)
    assert set(auger.TRACE_FIELDS) <= set(fields)
    assert fields["row"] == "AUG-901"
    assert fields["spec"] == "docs/prd/xml-spec-graph.md#state-model"
    for field in ("evidence", "witness", "verdict", "commit", "test", "memory", "wave"):
        assert fields[field].startswith("none:"), field
        assert len(fields[field]) > len("none:"), field


def test_approve_requires_a_named_signer(ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    rc, message, _ = run_cli_exit(["plan", "approve", "--plan", "AUG-PLAN-1", "--by", " "])
    assert rc != 0 and "--by" in message
    assert auger.plan_state("AUG-PLAN-1")["decision"] is None


def test_approve_refuses_a_digest_that_is_not_current(ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    rc, message, _ = run_cli_exit(
        ["plan", "approve", "--plan", "AUG-PLAN-1", "--digest", "deadbeef", "--by", "reviewer"]
    )
    assert rc != 0 and "not the bytes named" in message
    assert auger.plan_state("AUG-PLAN-1")["decision"] is None


def test_plans_dir_flag_overrides_the_environment(ledger, tmp_path, monkeypatch):
    monkeypatch.setenv(auger.PLANS_DIR_ENV, str(ledger))
    explicit = tmp_path / "explicit-ledger"
    source = plan_file(tmp_path, [task("AUG-901")])
    rc, out = run_cli(
        ["plan", "draft", "--plan", "AUG-PLAN-1", "--from", source, "--plans-dir", str(explicit)]
    )
    assert rc == 0, out
    assert (explicit / "AUG-PLAN-1.jsonl").exists()
    assert not (ledger / "AUG-PLAN-1.jsonl").exists()


def test_show_reports_the_approval_state_and_the_marker(ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    rc, out = run_cli(["plan", "show", "--plan", "AUG-PLAN-1"])
    assert rc == 0 and "no approval bound to this digest" in out
    rc, out = run_cli(["plan", "show", "--plan", "AUG-PLAN-1", "--json"])
    payload = json.loads(out)
    assert payload["decision"] is None
    assert payload["plan"]["tasks"][0]["id"] == "AUG-901"


# ---------------------------------------------------------------- 2. the approval gate
@needs_boardctl
def test_unapproved_draft_export_leaves_the_board_byte_identical(ledger, board, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    before = board_files(board)
    rc, message, out = run_cli_exit(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)]
    )
    assert rc != 0
    assert "UNAPPROVED" in message and "digest" in message
    assert board_files(board) == before
    assert [row["id"] for row in board_rows(board)] == ["NEVER-DONE"]
    assert out == ""


@needs_boardctl
def test_stale_approval_refuses_export_and_leaves_the_board_byte_identical(
    ledger, board, tmp_path
):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    changed = plan_file(tmp_path, [task("AUG-901", title="Re-drafted after approval")])
    rc, out = draft("AUG-PLAN-1", changed)
    assert rc == 0 and "v2" in out

    before = board_files(board)
    rc, message, _ = run_cli_exit(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)]
    )
    assert rc != 0
    assert "STALE" in message and "v1" in message
    assert board_files(board) == before


@needs_boardctl
def test_rejected_plan_refuses_export_and_leaves_the_board_byte_identical(
    ledger, board, tmp_path
):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    rc, out = run_cli(
        ["plan", "reject", "--plan", "AUG-PLAN-1", "--by", "reviewer", "--reason", "too thin"]
    )
    assert rc == 0 and "rejected" in out
    before = board_files(board)
    rc, message, _ = run_cli_exit(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)]
    )
    assert rc != 0 and "REJECTED" in message and "too thin" in message
    assert board_files(board) == before


@needs_boardctl
def test_reject_requires_a_reason(ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    rc, message, _ = run_cli_exit(
        ["plan", "reject", "--plan", "AUG-PLAN-1", "--by", "reviewer", "--reason", "  "]
    )
    assert rc != 0 and "--reason" in message


# ---------------------------------------------------------------- 3. export + read-back
@needs_boardctl
def test_approved_export_creates_rows_and_reads_back_exact_ids_and_criteria(
    ledger, board, tmp_path
):
    tasks = [
        task("AUG-901", capability_tags=["planning", "boardctl"]),
        task("AUG-902", depends_on=["AUG-901"], acceptance_criteria="1. Single-line criterion."),
    ]
    draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert approve("AUG-PLAN-1")[0] == 0

    rc, out = export("AUG-PLAN-1", board)
    assert rc == 0, out
    assert "created     AUG-901" in out and "created     AUG-902" in out
    assert "read-back: 2/2 row(s) verified" in out
    assert "RESULT: board matches the approved plan" in out

    row = board_row(board, "AUG-901")
    assert row["id"] == "AUG-901"
    assert row["title"] == "AUG-901 title"
    assert row["priority"] == "P2"
    assert row["complexity"] == 2
    assert row["acceptance_criteria"] == "1. AUG-901 does the thing.\n2. It is proved."
    assert sorted(row["capability_tags"]) == ["boardctl", "planning"]
    assert board_row(board, "AUG-902")["depends_on"] == ["AUG-901"]
    assert board_row(board, "AUG-902")["acceptance_criteria"] == "1. Single-line criterion."

    # The row carries the plan's provenance and a complete, explicit trace marker.
    assert "plan=AUG-PLAN-1@v1 digest=" in row["reasoning"]
    assert "verify: bash tests/gate.sh" in row["reasoning"]
    fields = trace_of(row["reasoning"])
    assert fields["row"] == "AUG-901"
    assert fields["spec"] == "docs/prd/xml-spec-graph.md#state-model"
    assert fields["evidence"].startswith("none:")


@needs_boardctl
def test_second_export_of_the_same_plan_is_idempotent(ledger, board, tmp_path):
    tasks = [task("AUG-901"), task("AUG-902", depends_on=["AUG-901"])]
    draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert approve("AUG-PLAN-1")[0] == 0
    assert export("AUG-PLAN-1", board)[0] == 0

    before = board_files(board)
    rc, out = export("AUG-PLAN-1", board)
    assert rc == 0, out
    assert "unchanged   AUG-901" in out and "unchanged   AUG-902" in out
    assert board_files(board) == before

    lines = board_rows(board)
    planned = [row["id"] for row in lines if row["id"].startswith("AUG-9")]
    assert planned == ["AUG-901", "AUG-902"]
    assert len({row["id"] for row in lines}) == len(lines)  # no duplicate row per id
    # Two export events, so the ledger records the attempt without duplicating board rows.
    exports = auger.plan_state("AUG-PLAN-1")["exports"]
    assert len(exports) == 2 and all(event["ok"] for event in exports)


@needs_boardctl
def test_export_dry_run_writes_neither_board_nor_ledger(ledger, board, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    before = board_files(board)
    ledger_before = (ledger / "AUG-PLAN-1.jsonl").read_bytes()
    rc, out = export("AUG-PLAN-1", board, "--dry-run")
    assert rc == 0 and "create      AUG-901" in out and "nothing written" in out
    assert board_files(board) == before
    assert (ledger / "AUG-PLAN-1.jsonl").read_bytes() == ledger_before


@needs_boardctl
def test_partial_failure_names_what_landed_and_resumes_without_duplicates(
    ledger, board, tmp_path
):
    tasks = [task("AUG-901"), task("AUG-902", depends_on=["AUG-901"]), task("AUG-903", depends_on=["AUG-902"])]
    draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert approve("AUG-PLAN-1")[0] == 0

    # A hand-made row with the same id but no plan provenance: boardctl cannot rewrite it.
    made = subprocess.run(
        [BOARDCTL, "-C", str(board), "create", "--id", "AUG-902", "--title", "Hand made"],
        capture_output=True,
        text=True,
    )
    assert made.returncode == 0, made.stderr

    rc, out = run_cli(["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)])
    assert rc != 0
    assert "created     AUG-901" in out
    assert "drift       AUG-902" in out and "reasoning" in out
    assert "skipped     AUG-903" in out
    assert "PARTIAL" in out
    assert board_row(board, "AUG-901")["title"] == "AUG-901 title"

    # Resolve the obstruction, re-run the SAME approved export: it resumes, no duplicates.
    board_file = board / ".coding-hermes" / "board" / "tasks.jsonl"
    kept = [row for row in board_rows(board) if row["id"] != "AUG-902"]
    board_file.write_text("".join(json.dumps(row) + "\n" for row in kept))
    rc, out = export("AUG-PLAN-1", board)
    assert rc == 0, out
    assert "unchanged   AUG-901" in out
    assert "created     AUG-902" in out and "created     AUG-903" in out

    ids = [row["id"] for row in board_rows(board)]
    assert ids.count("AUG-901") == 1
    assert ids.count("AUG-902") == 1
    assert ids.count("AUG-903") == 1


@needs_boardctl
def test_export_refuses_a_dependency_that_is_neither_planned_nor_on_the_board(
    ledger, board, tmp_path
):
    tasks = [task("AUG-901", depends_on=["AUG-999"])]
    draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert approve("AUG-PLAN-1")[0] == 0
    before = board_files(board)
    rc, message, _ = run_cli_exit(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)]
    )
    assert rc != 0 and "AUG-999" in message and "nothing was written" in message
    assert board_files(board) == before


@needs_boardctl
def test_export_update_mode_rewrites_a_drifted_row(ledger, board, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    assert export("AUG-PLAN-1", board)[0] == 0

    drifter = subprocess.run(
        [BOARDCTL, "-C", str(board), "update", "AUG-901", "--title", "Edited by hand"],
        capture_output=True,
        text=True,
    )
    assert drifter.returncode == 0, drifter.stderr

    rc, out = run_cli(["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(board)])
    assert rc != 0 and "drift" in out.lower() and "--update" in out

    rc, out = export("AUG-PLAN-1", board, "--update")
    assert rc == 0, out
    assert "updated     AUG-901" in out
    assert board_row(board, "AUG-901")["title"] == "AUG-901 title"


# ---------------------------------------------------------------- 4. reconcile (read-only)
@needs_boardctl
def test_reconcile_records_board_evidence_without_writing_the_board(ledger, board, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901"), task("AUG-902")]))
    assert approve("AUG-PLAN-1")[0] == 0
    assert export("AUG-PLAN-1", board)[0] == 0
    commit = "a" * 40
    marked = __import__("subprocess").run(
        [
            BOARDCTL,
            "-C",
            str(board),
            "update",
            "AUG-901",
            "--commit-hash",
            commit,
            "--guard",
            "PASS",
            "--ci",
            "GREEN",
            "--worker-status",
            "in_progress",
        ],
        capture_output=True,
        text=True,
    )
    assert marked.returncode == 0, marked.stderr

    before = board_files(board)
    rc, out = run_cli(
        ["plan", "reconcile", "--plan", "AUG-PLAN-1", "--board", str(board), "--json"]
    )
    assert rc == 0, out
    record = json.loads(out)
    rows = {row["id"]: row for row in record["rows"]}
    assert rows["AUG-901"]["commit"] == commit
    assert rows["AUG-901"]["guard"] == "PASS" and rows["AUG-901"]["ci"] == "GREEN"
    assert rows["AUG-901"]["worker_status"] == "in_progress"
    assert rows["AUG-901"]["status"] == "pending"
    assert rows["AUG-902"]["ci"].startswith("none:")
    assert rows["AUG-901"]["verdict"].startswith("none:")
    assert board_files(board) == before
    reconciled = auger.plan_state("AUG-PLAN-1")["reconciles"]
    assert len(reconciled) == 1 and reconciled[0]["digest"] == record["digest"]


def test_reconcile_uses_only_the_read_verb_and_writes_only_the_ledger(stub_boardctl, ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    assert export("AUG-PLAN-1", stub_boardctl.root)[0] == 0
    tasks_before = (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl").read_bytes()
    calls_before = len(stub_boardctl.verbs())

    rc, out = run_cli(
        ["plan", "reconcile", "--plan", "AUG-PLAN-1", "--board", str(stub_boardctl.root)]
    )
    assert rc == 0, out
    verbs = stub_boardctl.verbs()[calls_before:]
    assert verbs == ["show"], verbs
    assert (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl").read_bytes() == (
        tasks_before
    )
    assert auger.plan_state("AUG-PLAN-1")["reconciles"][0]["rows"][0]["present"] is True


# ---------------------------------------------------------------- 5. the CLI is the only writer
def test_export_goes_through_the_boardctl_cli(stub_boardctl, ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901"), task("AUG-902", depends_on=["AUG-901"])]))
    assert approve("AUG-PLAN-1")[0] == 0
    rc, out = export("AUG-PLAN-1", stub_boardctl.root)
    assert rc == 0, out
    verbs = stub_boardctl.verbs()
    assert verbs.count("create") == 2
    assert set(verbs) <= {"show", "create"}
    created = [
        json.loads(line)
        for line in (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    assert [row["id"] for row in created] == ["AUG-901", "AUG-902"]
    assert created[1]["depends_on"] == ["AUG-901"]


def test_a_boardctl_create_failure_is_named_and_the_run_stops_there(stub_boardctl, ledger, tmp_path, monkeypatch):
    tasks = [task("AUG-901"), task("AUG-902", depends_on=["AUG-901"]), task("AUG-903", depends_on=["AUG-902"])]
    draft("AUG-PLAN-1", plan_file(tmp_path, tasks))
    assert approve("AUG-PLAN-1")[0] == 0
    monkeypatch.setenv("STUB_BOARDCTL_FAIL_ID", "AUG-902")

    rc, out = run_cli(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(stub_boardctl.root)]
    )
    assert rc != 0
    assert "created     AUG-901" in out
    assert "failed      AUG-902" in out and "injected create failure" in out
    assert "skipped     AUG-903" in out
    assert stub_boardctl.verbs().count("create") == 2  # AUG-901 and the failed AUG-902

    monkeypatch.delenv("STUB_BOARDCTL_FAIL_ID")
    rc, out = export("AUG-PLAN-1", stub_boardctl.root)
    assert rc == 0, out
    assert "unchanged   AUG-901" in out and "created     AUG-902" in out
    rows = [
        json.loads(line)
        for line in (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    assert [row["id"] for row in rows] == ["AUG-901", "AUG-902", "AUG-903"]


def test_export_refuses_loudly_when_boardctl_is_missing(ledger, tmp_path, monkeypatch):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    monkeypatch.setenv(auger.BOARDCTL_ENV, "definitely-not-a-boardctl")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    rc, message, _ = run_cli_exit(
        ["plan", "export", "--plan", "AUG-PLAN-1", "--board", str(tmp_path)]
    )
    assert rc != 0
    assert "boardctl not found" in message and auger.BOARDCTL_ENV in message


def test_plan_ledger_is_append_only_across_commands(ledger, tmp_path):
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    path = ledger / "AUG-PLAN-1.jsonl"
    lines = path.read_text().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["draft", "approval"]
    # Approving binds the exact digest the draft wrote, and the ledger keeps both lines.
    assert json.loads(lines[1])["digest"] == json.loads(lines[0])["digest"]
    assert json.loads(lines[1])["version"] == 1


def test_a_row_deleted_outside_auger_is_recreated_on_the_next_export(stub_boardctl, ledger, tmp_path):
    """Export is driven by the board's CURRENT state, not by a cache of what it once wrote."""
    draft("AUG-PLAN-1", plan_file(tmp_path, [task("AUG-901")]))
    assert approve("AUG-PLAN-1")[0] == 0
    assert export("AUG-PLAN-1", stub_boardctl.root)[0] == 0
    (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl").write_text("")

    rc, out = export("AUG-PLAN-1", stub_boardctl.root)
    assert rc == 0, out
    assert "created     AUG-901" in out
    rows = [
        json.loads(line)
        for line in (stub_boardctl.root / ".coding-hermes" / "board" / "tasks.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    assert [row["id"] for row in rows] == ["AUG-901"]
