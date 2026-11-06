"""AUG-119: an approved PLAN is the unit of board mutation — many tasks, ONE version/digest.

A plan is not a task and not a board row: it is the reviewed set of tasks, each carrying its ids,
its acceptance criteria, its dependencies, the spec anchors it is derived from and the evidence
expected to prove it. The board is written only after an approval that is bound to the plan's
VERSION and DIGEST, every row is read back and compared to the approved input, and a re-export of
the same approved plan changes nothing.

Measured gap at the SG8 base (`952e5dc`, 2026-11-06): `auger.py` had exactly ONE boardctl write
path — `export_task_packet`, one `<task>` block at a time, its approval a per-packet digest on the
command line or in the block. There was no plan object (no "these N tasks are the reviewed set"),
no plan version, no approval LEDGER, no multi-row export, no recovery record for a half-written
plan, and nothing that read board status/commit/verdict evidence back into Auger.

Per acceptance line and its proof:

  AC1 versioned draft plan (ids, acceptance, deps, anchors, evidence)
        .................................................... test_a_draft_plan_is_versioned_and_complete
  AC2 no mutation before a bound approval; stale/rejected refuse
        .................................................... test_an_unapproved_draft_leaves_the_board_byte_identical,
                                                              test_a_rejected_or_stale_approval_refuses_export
  AC3 boardctl create/update + exact read-back ............. test_an_approved_plan_exports_every_row_and_reads_back,
                                                              test_a_drifted_row_is_repaired_by_the_supported_update
  AC3/AC6 only boardctl, no second board store ............. test_the_plan_exporter_issues_only_boardctl_operations
  AC4 evidence reconciled in, board never overwritten ...... test_reconciliation_reads_board_evidence_without_writing,
                                                              test_reconciliation_binds_commits_and_verdicts
  AC5 idempotent export + partial-failure recovery ......... test_an_approved_plan_exports_every_row_and_reads_back,
                                                              test_a_partial_failure_is_recorded_and_resumable
  AC6 ch:trace names row/spec/test/evidence/witness ........ test_the_plan_trace_marker_names_every_field
  arm ..................................................... test_gate_arm_runs_these_files

HERMETIC: no DuckBrain, no JEV, no network, no LLM. Corpora are built in `tmp_path`; the board is a
throwaway canonical JSONL board initialised by the real `boardctl` in `tmp_path`, and the suite
skips LOUDLY (naming the remedy) only when `boardctl` is not installed on the host at all.

ch:trace row=AUG-119 spec=docs/prd/xml-spec-graph.md#requirements
         test=tests/test_plan_approval.py evidence=evidence/sg8/ verdict=none:not-judged
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

GATE = REPO_ROOT / "tests" / "gate.sh"
PRD = REPO_ROOT / "docs" / "prd" / "xml-spec-graph.md"
BOARDCTL = shutil.which("boardctl")

#: A real, git-tracked test in auger's own checkout, with a selector it really defines. A tmp_path
#: corpus names no work tree, so the obligation validator is handed `--repo-root REPO_ROOT`.
TRACKED_TEST = "tests/test_xml_specs.py"
TRACKED_SELECTOR = "test_gate_runs_these_tests"

PLAN = "SG8-E2E-PLAN"
TASK_A = "SG8-TASK-1"
TASK_B = "SG8-TASK-2"
ACCEPTANCE_A = (
    "The canonical row records the exact acceptance criteria and the excluded ids."
)
ACCEPTANCE_B = "The second task is exported only after the first one is read back."


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def draft(root: Path, plan: str = PLAN) -> dict:
    """`spec plan-draft` as JSON — the reviewed plan, not a task packet."""
    code, out, err = run_spec("plan-draft", str(root), "--plan", plan)
    assert code == 0, f"spec plan-draft refused: {err}"
    return json.loads(out)


def approve(
    root: Path,
    ledger: Path,
    digest: str,
    *,
    plan: str = PLAN,
    decision: str = "approve",
) -> dict:
    """Record an approval (or a rejection) in the ledger through the CLI."""
    code, out, err = run_spec(
        "plan-approve",
        str(root),
        "--plan",
        plan,
        f"--{decision}",
        "--digest",
        digest,
        "--approver",
        "owner",
        "--ledger",
        str(ledger),
        "--at",
        "2026-11-06T00:00:00+00:00",
    )
    assert code == 0, f"spec plan-approve refused: {err}"
    return json.loads(out)


def export(
    root: Path,
    board: Path,
    ledger: Path,
    *extra: str,
    plan: str = PLAN,
) -> tuple[int, str, str]:
    return run_spec(
        "plan-export",
        str(root),
        "--plan",
        plan,
        "--board",
        str(board),
        "--ledger",
        str(ledger),
        *extra,
    )


def approve_current(
    root: Path, board: Path, ledger: Path, **kwargs
) -> tuple[dict, str]:
    """Draft, approve the current digest, and return (plan, digest)."""
    plan = draft(root, **({"plan": kwargs["plan"]} if "plan" in kwargs else {}))
    approve(root, ledger, plan["digest"], **kwargs)
    return plan, plan["digest"]


# ---------------------------------------------------------------- fixture corpora
def _block(kind: str, block_id: str, attributes: str, text: str) -> str:
    return f"  <{kind} {attributes}>{text}</{kind}>\n"


REQUIREMENT_A = _block(
    "requirement",
    "SG8-REQ-1",
    'id="SG8-REQ-1" kind="requirement" status="active" version="1.0.0" '
    'applies-to="auger.plan" verification="test" verifies="SG8-VER-1"',
    "A plan is exported as one canonical board row per task.",
)
VERIFICATION_A = (
    '  <verification id="SG8-VER-1" kind="verification" status="active" version="1.0.0"\n'
    f'                applies-to="auger.plan" method="test" type="test" runner="pytest"\n'
    f'                path="{TRACKED_TEST}" selector="{TRACKED_SELECTOR}"\n'
    '                assertions="the exported rows name their scope" />\n'
)
ACCEPTANCE_A_BLOCK = _block(
    "acceptance",
    "SG8-AC-1",
    'id="SG8-AC-1" kind="acceptance" status="active" version="1.0.0" '
    'applies-to="auger.plan" verification="test" verifies="SG8-REQ-1"',
    ACCEPTANCE_A,
)
EXCLUDED = _block(
    "requirement",
    "SG8-REQ-9",
    'id="SG8-REQ-9" kind="requirement" status="active" version="1.0.0" '
    'verification="inspection"',
    "A later wave owns the migration path.",
)
REQUIREMENT_B = _block(
    "requirement",
    "SG8-REQ-2",
    'id="SG8-REQ-2" kind="requirement" status="active" version="1.0.0" '
    'applies-to="auger.plan" verification="test" verifies="SG8-VER-2"',
    "The plan is exported in dependency order and each row is read back.",
)
VERIFICATION_B = (
    '  <verification id="SG8-VER-2" kind="verification" status="active" version="1.0.0"\n'
    f'                applies-to="auger.plan" method="test" type="test" runner="pytest"\n'
    f'                path="{TRACKED_TEST}" selector="{TRACKED_SELECTOR}"\n'
    '                assertions="the read-back equals the approved packet" />\n'
)
ACCEPTANCE_B_BLOCK = _block(
    "acceptance",
    "SG8-AC-2",
    'id="SG8-AC-2" kind="acceptance" status="active" version="1.0.0" '
    'applies-to="auger.plan" verification="test" verifies="SG8-REQ-2"',
    ACCEPTANCE_B,
)


def task_block(
    task_id: str,
    *,
    prose: str,
    in_scope: str,
    acceptance: str = "",
    out_of_scope: str = "SG8-REQ-9",
    depends_on: str = "",
    exclusions: str = '<exclusion ref="SG8-REQ-9" reason="owned by a later wave"/>',
    plan: str = PLAN,
    plan_version: str = "1.0.0",
    version: str = "1.0.0",
) -> str:
    """One `<task>` block carrying its PLAN membership (AUG-119's new authored surface)."""
    attributes = [
        f'id="{task_id}"',
        'kind="task"',
        'status="active"',
        f'version="{version}"',
        'applies-to="auger.plan"',
        'verification="test"',
        f'plan="{plan}"',
        f'plan-version="{plan_version}"',
        f'in-scope="{in_scope}"',
    ]
    if acceptance:
        attributes.append(f'acceptance="{acceptance}"')
    if out_of_scope:
        attributes.append(f'out-of-scope="{out_of_scope}"')
    if depends_on:
        attributes.append(f'depends-on="{depends_on}"')
    return (
        "  <task " + " ".join(attributes) + ">\n"
        f"    {prose}\n"
        f"    {exclusions}\n"
        "  </task>\n"
    )


TASK_A_BLOCK = task_block(
    TASK_A,
    prose="Export the first approved task.",
    in_scope="SG8-REQ-1",
    acceptance="SG8-AC-1",
)
TASK_B_BLOCK = task_block(
    TASK_B,
    prose="Export the second approved task after the first.",
    in_scope="SG8-REQ-2",
    acceptance="SG8-AC-2",
    depends_on=TASK_A,
)


def write_plan_corpus(
    base: Path,
    *,
    task_a: str = TASK_A_BLOCK,
    task_b: str = TASK_B_BLOCK,
    extra: str = "",
) -> Path:
    """A TWO-DOCUMENT plan corpus: the plan spans files, which is the shape a plan really has."""
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan-a.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="SG8-PLAN-A" version="1.0.0" schema-version="1.0">\n'
        f"{REQUIREMENT_A}{VERIFICATION_A}{ACCEPTANCE_A_BLOCK}{EXCLUDED}{extra}{task_a}"
        "</spec>\n",
        encoding="utf-8",
    )
    (root / "plan-b.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="SG8-PLAN-B" version="1.0.0" schema-version="1.0">\n'
        f"{REQUIREMENT_B}{VERIFICATION_B}{ACCEPTANCE_B_BLOCK}{task_b}"
        "</spec>\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------- board fixtures
def boardctl_or_skip() -> str:
    """The real boardctl, or a LOUD skip naming the remedy (a silent skip reads like a pass)."""
    if BOARDCTL is None:
        pytest.skip(
            "boardctl is not installed on this host, so the canonical board a plan exports into "
            "cannot be created; install coding-hermes-tools (`boardctl`) and re-run — the AUG-119 "
            "board proofs did NOT run here"
        )
    return BOARDCTL


def make_board(base: Path) -> Path:
    board = base / "board"
    board.mkdir()
    done = subprocess.run(
        [
            boardctl_or_skip(),
            "init",
            "-C",
            str(board),
            "--project",
            "auger",
            "--namespace",
            "auger",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    board_files(board)
    return board


def board_files(board: Path) -> tuple[Path, Path]:
    found = sorted(board.rglob("tasks.jsonl"))
    assert found, f"boardctl init wrote no tasks.jsonl under {board}"
    tasks = found[0]
    events = tasks.with_name("events.jsonl")
    assert events.is_file(), f"boardctl init wrote no events.jsonl beside {tasks}"
    return tasks, events


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def board_row(board: Path, row_id: str) -> dict:
    """`boardctl show` — the READ-BACK a consumer sees, never the file we just wrote."""
    done = subprocess.run(
        [boardctl_or_skip(), "-C", str(board), "show", row_id],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def rows_with_id(board: Path, row_id: str) -> int:
    tasks, _events = board_files(board)
    return sum(
        1
        for line in tasks.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("id") == row_id
    )


def wrapper(base: Path, *, fail_on: str = "", name: str = "recording-boardctl"):
    """A boardctl wrapper that RECORDS every argv and can fail ONE create, deterministically."""
    boardctl = boardctl_or_skip()
    log = base / f"{name}.log"
    script = base / name
    lines = ["#!/bin/sh", f'printf "%s\\n" "$*" >> {log}']
    if fail_on:
        lines.append(
            f'case "$*" in *create*{fail_on}*) echo "simulated boardctl failure" >&2; '
            "exit 1;; esac"
        )
    lines.append(f'exec {boardctl} "$@"')
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script, log


# ---------------------------------------------------------------- AC1: the draft plan
def test_a_draft_plan_is_versioned_and_complete(tmp_path: Path) -> None:
    """AC1: a plan carries ids, criteria, dependencies, anchors and expected evidence — versioned.

    Every field is compared against the CORPUS rather than against a re-run of the same builder:
    the anchors name the exact authored blocks, the evidence is the verification block's own
    runner/path/selector, the acceptance text is the block's verbatim prose. The digest is checked
    for the two properties an approval depends on — it is stable across runs and it does not move
    when the corpus is copied to another directory (no absolute path leaks into it).
    """
    root = write_plan_corpus(tmp_path)
    plan = draft(root)

    assert plan["plan"] == PLAN, plan
    assert plan["version"] == "1.0.0", plan
    assert len(plan["digest"]) == 64 and plan["digest"] == auger.plan_digest(plan)
    assert [entry["task"] for entry in plan["tasks"]] == [TASK_A, TASK_B], plan["tasks"]

    first, second = plan["tasks"]
    # Identity, provenance, lifecycle and method: the task's own block, verbatim.
    assert first["source"] == f"plan-a.xml#{TASK_A}", first
    assert (first["status"], first["version"], first["method"]) == (
        "active",
        "1.0.0",
        "test",
    ), first

    # DEPENDENCIES are task-to-task, read from the corpus' own `depends-on` edge.
    assert first["dependencies"] == [], first
    assert second["dependencies"] == [TASK_A], second

    # SOURCE ANCHORS: the task and the blocks it names, each with the file it is authored in.
    anchors = {anchor["id"]: anchor for anchor in first["anchors"]}
    assert set(anchors) == {TASK_A, "SG8-REQ-1", "SG8-AC-1"}, sorted(anchors)
    assert anchors["SG8-REQ-1"]["file"] == "plan-a.xml", anchors["SG8-REQ-1"]
    assert anchors["SG8-REQ-1"]["source"] == "plan-a.xml#SG8-REQ-1"
    assert anchors[TASK_A]["source"] == f"plan-a.xml#{TASK_A}"

    # ACCEPTANCE CRITERIA travel as ids AND verbatim prose (a packet of bare ids tells a worker
    # nothing) — and the same text is what a board row will carry.
    assert [entry["id"] for entry in first["acceptance"]] == ["SG8-AC-1"], first[
        "acceptance"
    ]
    assert first["acceptance"][0]["text"] == ACCEPTANCE_A, first["acceptance"]
    assert second["acceptance"][0]["text"] == ACCEPTANCE_B

    # EXPECTED VERIFICATION EVIDENCE: the obligation the verification block declares.
    assert first["evidence"] == [
        {
            "id": "SG8-VER-1",
            "kind": "verification",
            "method": "test",
            "runner": "pytest",
            "path": TRACKED_TEST,
            "selector": TRACKED_SELECTOR,
        }
    ], first["evidence"]

    # The refs a GitReins packet will need are already typed, statused and DIGESTED here, so the
    # handoff has nothing to re-derive.
    assert [ref["id"] for ref in first["test_refs"]] == ["SG8-VER-1"], first[
        "test_refs"
    ]
    ref = first["test_refs"][0]
    assert (ref["path"], ref["selector"], ref["runner"]) == (
        TRACKED_TEST,
        TRACKED_SELECTOR,
        "pytest",
    ), ref
    assert len(ref["digest"]) == 64, ref
    assert [
        (block["id"], block["status"], len(block["digest"]))
        for block in first["spec_blocks"]
    ] == [
        ("SG8-REQ-1", "active", 64),
        ("SG8-AC-1", "active", 64),
    ], first["spec_blocks"]

    # SOURCE DIGESTS: every document the plan was drafted from, by content.
    assert plan["sources"] == {
        "plan-a.xml": hashlib.sha256((root / "plan-a.xml").read_bytes()).hexdigest(),
        "plan-b.xml": hashlib.sha256((root / "plan-b.xml").read_bytes()).hexdigest(),
    }, plan["sources"]

    # The embedded packet IS the approved statement for that task, digest and all.
    assert first["packet"]["task"] == TASK_A
    assert first["packet_digest"] == auger.task_packet_digest(first["packet"])

    # Determinism: same corpus, same bytes; a different directory, the same digest.
    assert (
        run_spec("plan-draft", str(root), "--plan", PLAN)[1]
        == run_spec("plan-draft", str(root), "--plan", PLAN)[1]
    )
    copy = tmp_path / "copy"
    shutil.copytree(root, copy)
    assert draft(copy)["digest"] == plan["digest"], (
        "the plan digest changed when the SAME corpus moved directory — an absolute path leaked "
        "into the approved value"
    )

    # A plan that no task claims is a named refusal, never an empty plan that looks approved.
    code, out, err = run_spec("plan-draft", str(root), "--plan", "SG8-NO-SUCH-PLAN")
    assert code == 2, out
    assert "unknown-plan" in err and "SG8-NO-SUCH-PLAN" in err, err

    # Two tasks claiming one plan id with DIFFERENT versions is a contradiction, not a plan.
    mixed = write_plan_corpus(
        tmp_path / "mixed",
        task_b=task_block(
            TASK_B,
            prose="Export the second approved task.",
            in_scope="SG8-REQ-2",
            acceptance="SG8-AC-2",
            depends_on=TASK_A,
            plan_version="2.0.0",
        ),
    )
    code, out, err = run_spec("plan-draft", str(mixed), "--plan", PLAN)
    assert code == 2, out
    assert "plan-version-conflict" in err and "2.0.0" in err and "1.0.0" in err, err


# ---------------------------------------------------------------- AC2/AC5: the approval gate
def test_an_unapproved_draft_leaves_the_board_byte_identical(tmp_path: Path) -> None:
    """AC2+AC5: with no approval record the export refuses and NO canonical byte moves.

    Proved three ways, because "we did not write" is easy to claim and hard to see: both canonical
    JSONL files hash identically before and after, the export exits non-zero with a typed code, and
    a boardctl wrapper on the exporter's `--boardctl` seam recorded no invocation at all.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    script, log = wrapper(tmp_path)

    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))

    code, out, err = export(root, board, ledger, "--boardctl", str(script))
    assert code == 2, f"an UNapproved plan exported: {out!r}"
    assert "unapproved-plan-digest" in err, err
    assert out == "", "a refused export printed a result"

    after = (digest_of(tasks), digest_of(events))
    assert after == before, (
        "a REFUSED export changed a canonical board file: "
        f"tasks {before[0][:12]}->{after[0][:12]}, events {before[1][:12]}->{after[1][:12]}"
    )
    assert not log.exists(), (
        "boardctl was invoked by a refused export:\n" + log.read_text(encoding="utf-8")
    )


def test_a_rejected_or_stale_approval_refuses_export(tmp_path: Path) -> None:
    """AC2: an explicitly REJECTED plan, and an approval whose plan has since changed, both refuse.

    The rejection is not a silent no-op: it is a recorded decision that outranks the earlier
    approval (the ledger is append-only and the LATEST record for a plan is the decision). The
    stale case names BOTH digests, because "your approval is stale" without the pair leaves the
    operator unable to tell a re-approval from a corrupted ledger.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    script, log = wrapper(tmp_path)
    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))

    # (a) rejection of the very digest that is current.
    plan = draft(root)
    approve(root, ledger, plan["digest"], decision="reject")
    code, out, err = export(root, board, ledger, "--boardctl", str(script))
    assert code == 2, f"a REJECTED plan exported: {out!r}"
    assert "plan-rejected" in err, err
    assert out == ""

    # (b) approved, then the corpus changed: the approval is for a plan that no longer exists.
    ledger_b = tmp_path / "ledger-b"
    ledger_b.mkdir()
    plan = draft(root)
    approve(root, ledger_b, plan["digest"])
    rewritten = write_plan_corpus(
        tmp_path / "edited",
        task_a=task_block(
            TASK_A,
            prose="Export the first approved task, now with a changed scope sentence.",
            in_scope="SG8-REQ-1",
            acceptance="SG8-AC-1",
        ),
    )
    code, out, err = export(rewritten, board, ledger_b, "--boardctl", str(script))
    assert code == 2, f"a STALE approval exported: {out!r}"
    assert "stale-plan-digest" in err, err
    assert plan["digest"] in err, (
        f"the finding does not name the approved digest: {err}"
    )
    assert draft(rewritten)["digest"] in err, (
        f"the finding does not name the current digest: {err}"
    )

    # (c) a plan-approve call cannot bless a digest that is not the plan's current one.
    code, out, err = run_spec(
        "plan-approve",
        str(rewritten),
        "--plan",
        PLAN,
        "--approve",
        "--digest",
        plan["digest"],
        "--approver",
        "owner",
        "--ledger",
        str(ledger_b),
    )
    assert code == 2, out
    assert "stale-plan-digest" in err, err

    after = (digest_of(tasks), digest_of(events))
    assert after == before, "a refused export changed a canonical board file"
    assert not log.exists(), "boardctl was invoked by a refused export"


# ---------------------------------------------------------------- AC3/AC5: export + read-back
def test_an_approved_plan_exports_every_row_and_reads_back(tmp_path: Path) -> None:
    """AC3+AC5: every approved task becomes one canonical row, read back and compared.

    The criteria the row carries are the APPROVED criteria verbatim, the scope tags are the plan's,
    and the packet JSON inside `reasoning` is byte-identical to the approved packet. A re-export of
    the same approved plan is idempotent: no second create, one row per id, and both canonical
    files byte-identical.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    plan, digest = approve_current(root, board, ledger)

    code, out, err = export(root, board, ledger)
    assert code == 0, f"an approved plan did not export: {err}"
    result = json.loads(out)
    assert result["status"] == "exported", result
    assert result["digest"] == digest
    assert [row["task"] for row in result["rows"]] == [TASK_A, TASK_B], result["rows"]
    assert [row["status"] for row in result["rows"]] == ["exported", "exported"], (
        result["rows"]
    )

    by_task = {entry["task"]: entry for entry in plan["tasks"]}
    for task_id, expectation in ((TASK_A, by_task[TASK_A]), (TASK_B, by_task[TASK_B])):
        row = board_row(board, task_id)
        assert row["id"] == task_id, row
        assert row["status"] == "pending", row
        # The board's criteria are the APPROVED text, verbatim.
        expected_criteria = "\n".join(
            f"{entry['id']}: {entry['text']}" for entry in expectation["acceptance"]
        )
        assert row["acceptance_criteria"] == expected_criteria, row[
            "acceptance_criteria"
        ]
        # Scope tags carry id AND version, in packet order.
        assert row["capability_tags"] == [
            f"in-scope:{entry['id']}@{entry['version']}"
            for entry in expectation["in_scope"]
        ] + [
            f"acceptance:{entry['id']}@{entry['version']}"
            for entry in expectation["acceptance"]
        ] + [
            f"out-of-scope:{entry['id']}@{entry['version']}"
            for entry in expectation["out_of_scope"]
        ], row["capability_tags"]
        # The approved packet survives byte for byte inside the canonical row.
        canonical = json.dumps(
            expectation["packet"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        assert canonical in row["reasoning"], row["reasoning"]
        assert expectation["trace"] in row["reasoning"], row["reasoning"]
        # What boardctl SHOWS is what the exporter reported reading back.
        exported = next(entry for entry in result["rows"] if entry["task"] == task_id)
        assert exported["readback"]["acceptance_criteria"] == row["acceptance_criteria"]
        assert exported["readback"]["capability_tags"] == row["capability_tags"]
        assert rows_with_id(board, task_id) == 1

    # IDEMPOTENT re-export: no new rows, no byte changed, nothing re-created.
    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))
    code, out, err = export(root, board, ledger)
    assert code == 0, err
    again = json.loads(out)
    assert again["status"] == "unchanged", again
    assert [row["status"] for row in again["rows"]] == ["unchanged", "unchanged"], (
        again["rows"]
    )
    assert rows_with_id(board, TASK_A) == 1 and rows_with_id(board, TASK_B) == 1
    after = (digest_of(tasks), digest_of(events))
    assert after == before, (
        "a repeated export of the SAME approved plan rewrote the board: "
        f"tasks {before[0][:12]}->{after[0][:12]}, events {before[1][:12]}->{after[1][:12]}"
    )


def test_a_drifted_row_is_repaired_by_the_supported_update(tmp_path: Path) -> None:
    """AC3's update half: an existing row whose criteria drifted is REPAIRED through boardctl.

    The exporter never writes the board itself, so the only way to bring a drifted row back to the
    approved value is boardctl's supported `update`. The proof is the read-back: the criteria and
    title are the approved ones again, the scope tags were never touched (they did not drift), and
    a third export is `unchanged` again — so the repair converged instead of oscillating.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    plan, _digest = approve_current(root, board, ledger)
    assert export(root, board, ledger)[0] == 0

    # Something edited the canonical row by hand: the approved criteria no longer hold.
    done = subprocess.run(
        [
            boardctl_or_skip(),
            "-C",
            str(board),
            "update",
            TASK_A,
            "--acceptance-criteria",
            "drifted: someone rewrote this row",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (
        board_row(board, TASK_A)["acceptance_criteria"]
        == "drifted: someone rewrote this row"
    )

    code, out, err = export(root, board, ledger)
    assert code == 0, err
    result = json.loads(out)
    assert [row["status"] for row in result["rows"]] == ["updated", "unchanged"], (
        result["rows"]
    )

    expectation = {entry["task"]: entry for entry in plan["tasks"]}[TASK_A]
    expected_criteria = "\n".join(
        f"{entry['id']}: {entry['text']}" for entry in expectation["acceptance"]
    )
    row = board_row(board, TASK_A)
    assert row["acceptance_criteria"] == expected_criteria, row["acceptance_criteria"]
    assert row["capability_tags"] == [
        f"in-scope:{entry['id']}@{entry['version']}"
        for entry in expectation["in_scope"]
    ] + [
        f"acceptance:{entry['id']}@{entry['version']}"
        for entry in expectation["acceptance"]
    ] + [
        f"out-of-scope:{entry['id']}@{entry['version']}"
        for entry in expectation["out_of_scope"]
    ], row["capability_tags"]

    code, out, err = export(root, board, ledger)
    assert code == 0, err
    assert json.loads(out)["status"] == "unchanged", out

    # A row written by a DIFFERENT plan is a conflict, not something to overwrite: boardctl's
    # supported `update` cannot rewrite scope, and a foreman may already be working that row.
    tasks, _events = board_files(board)
    rows = []
    for line in tasks.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("id") == TASK_A:
            payload["capability_tags"] = ["in-scope:SG8-OTHER-1@9.9.9"]
        rows.append(payload)
    tasks.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    code, out, err = export(root, board, ledger)
    assert code == 1, (
        f"a SCOPE conflict was not reported as a refused row: rc={code} {out!r}"
    )
    conflict = json.loads(out)["failed"]
    assert conflict["code"] == "board-row-conflict", conflict
    assert TASK_A in conflict["detail"], conflict


# ---------------------------------------------------------------- the closed write surface
def test_the_plan_exporter_issues_only_boardctl_operations(tmp_path: Path) -> None:
    """AC3/AC6: the plan exporter shells out to boardctl `create`/`update`/`show` and nothing else.

    Proved from OUTSIDE (a wrapper records every argv the exporter issued; the operations must be a
    subset of {create, update, show}) and from INSIDE (the exporter's own source names no HTTP
    client, no scheduler and no tick path), so there is no second board store to disable later.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _plan, _digest = approve_current(root, board, ledger)
    script, log = wrapper(tmp_path)

    code, out, err = export(root, board, ledger, "--boardctl", str(script))
    assert code == 0, err
    invocations = [
        line.split() for line in log.read_text(encoding="utf-8").splitlines() if line
    ]
    assert invocations, "the wrapper recorded no boardctl call at all"
    known = {"create", "show", "update", "event", "sweep-status", "dispatch", "header"}
    operations = {token for argv in invocations for token in argv if token in known}
    assert operations <= {"create", "show", "update"}, (
        f"the plan exporter invoked unexpected boardctl commands: {sorted(operations)}"
    )
    assert "create" in operations, (
        "the wrapper saw no create at all — the export never wrote a row, so the closed-surface "
        "claim is vacuous"
    )

    source = (
        inspect.getsource(auger.export_spec_plan)
        + inspect.getsource(auger._export_plan_task_row)
        + inspect.getsource(auger._boardctl)
    )
    for forbidden in (
        "urllib",
        "http.client",
        "requests",
        "socket",
        "scheduler",
        "tick",
    ):
        assert forbidden not in source.lower(), (
            f"the plan exporter source names {forbidden!r}: a plan export must not dispatch or "
            "mutate the scheduler"
        )


# ---------------------------------------------------------------- AC5: partial failure
def test_a_partial_failure_is_recorded_and_resumable(tmp_path: Path) -> None:
    """AC5: when the second row fails, the FIRST row's outcome is recorded and the run is not "done".

    A half-exported plan reported as success is the failure mode this row exists to prevent, so the
    exporter stops at the failing row, exits non-zero, prints the outcome of the row it did write and
    names the failure with boardctl's own words. The recovery record on disk says what happened and
    what to do next, and re-running the SAME approved plan completes it: the already-written row is
    `unchanged` (not re-created) and only the missing row is created.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _plan, digest = approve_current(root, board, ledger)
    script, log = wrapper(tmp_path, fail_on=TASK_B)
    record = tmp_path / "recovery.json"

    code, out, err = export(
        root, board, ledger, "--boardctl", str(script), "--out", str(record)
    )
    assert code == 1, (
        f"a PARTIAL export did not report a non-zero code: rc={code} out={out!r}"
    )
    result = json.loads(out)
    assert result["status"] == "partial", result
    assert [row["task"] for row in result["rows"]] == [TASK_A], (
        f"the row that DID land is not recorded: {result['rows']}"
    )
    assert result["rows"][0]["status"] == "exported"
    assert result["failed"]["task"] == TASK_B, result["failed"]
    assert "simulated boardctl failure" in json.dumps(result["failed"]), result[
        "failed"
    ]

    # Exactly one row landed; the failing one is absent, so the board is honest about it.
    assert rows_with_id(board, TASK_A) == 1
    assert rows_with_id(board, TASK_B) == 0

    # The recovery record is on disk, says PARTIAL, and names the resume path.
    assert record.is_file(), "a partial export left no recovery record"
    recovery = json.loads(record.read_text(encoding="utf-8"))
    assert recovery["status"] == "partial", recovery
    assert recovery["digest"] == digest, recovery
    assert recovery["completed"] == [TASK_A], recovery
    assert recovery["failed"]["task"] == TASK_B, recovery
    assert "re-run" in recovery["resume"], recovery

    # RESUME: the same approved plan, the real boardctl — the written row is untouched, the
    # missing one is created, and the plan is complete.
    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))
    code, out, err = export(root, board, ledger)
    assert code == 0, err
    resumed = json.loads(out)
    assert resumed["status"] == "exported", resumed
    assert [row["status"] for row in resumed["rows"]] == ["unchanged", "exported"], (
        resumed["rows"]
    )
    assert rows_with_id(board, TASK_A) == 1 and rows_with_id(board, TASK_B) == 1
    assert digest_of(tasks) != before[0], "the resumed export wrote nothing at all"


# ---------------------------------------------------------------- AC4/AC6: reconciliation
def test_reconciliation_reads_board_evidence_without_writing(tmp_path: Path) -> None:
    """AC4: board status/commit/verdict evidence is read INTO Auger; the board is not touched.

    The reconciliation record binds each task to its canonical row, its recorded commit (resolved in
    the repository, not merely echoed) and its verdict — with `none:<reason>` where a field has
    nothing behind it yet, so "pending" can never be read as "passed". The canonical files hash
    identically before and after, which is what "without overwriting canonical board state" means.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _plan, digest = approve_current(root, board, ledger)
    assert export(root, board, ledger)[0] == 0

    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert len(head) == 40, head
    done = subprocess.run(
        [
            boardctl_or_skip(),
            "-C",
            str(board),
            "update",
            TASK_A,
            "--status",
            "complete",
            "--evidence-run-id",
            "RUN-SG8-1",
            "--commit-hash",
            head,
            "--summary",
            "worker says the first task is done",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr

    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))

    code, out, err = run_spec(
        "plan-reconcile",
        str(root),
        "--plan",
        PLAN,
        "--board",
        str(board),
        "--repo-root",
        str(REPO_ROOT),
    )
    assert code == 0, f"spec plan-reconcile refused: {err}"
    record = json.loads(out)
    assert record["plan"] == PLAN and record["digest"] == digest, record
    assert record["writes"] == "none", record
    rows = {row["task"]: row for row in record["rows"]}
    assert rows[TASK_A]["board_status"] == "complete", rows[TASK_A]
    assert rows[TASK_A]["commit"] == head, rows[TASK_A]
    assert rows[TASK_A]["verdict"] == "none:not-judged", rows[TASK_A]
    assert rows[TASK_A]["witness"] == "boardctl:show", rows[TASK_A]
    assert rows[TASK_B]["board_status"] == "pending", rows[TASK_B]
    assert rows[TASK_B]["commit"] == "none:not-recorded", rows[TASK_B]

    after = (digest_of(tasks), digest_of(events))
    assert after == before, (
        "reconciliation changed a canonical board file: "
        f"tasks {before[0][:12]}->{after[0][:12]}, events {before[1][:12]}->{after[1][:12]}"
    )


def test_reconciliation_binds_commits_and_verdicts(tmp_path: Path) -> None:
    """AC4/AC6: a commit that does not resolve, and a verdict for another commit, are both refused.

    A reconciliation record that merely copies whatever strings the board holds is not evidence: a
    commit must exist in the repository it is claimed against, and a verdict must describe the SAME
    commit the row records. Both refusals name the task and the values, so an operator can see which
    side is wrong.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _plan, _digest = approve_current(root, board, ledger)
    assert export(root, board, ledger)[0] == 0

    # (a) a commit the repository does not have.
    absent = "0" * 40
    done = subprocess.run(
        [
            boardctl_or_skip(),
            "-C",
            str(board),
            "update",
            TASK_A,
            "--status",
            "complete",
            "--evidence-run-id",
            "RUN-SG8-2",
            "--commit-hash",
            absent,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    code, out, err = run_spec(
        "plan-reconcile",
        str(root),
        "--plan",
        PLAN,
        "--board",
        str(board),
        "--repo-root",
        str(REPO_ROOT),
    )
    assert code == 2, out
    assert "unresolved-commit" in err, err
    assert TASK_A in err and absent in err, err

    # (b) a verdict that names a DIFFERENT commit than the row records.
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    done = subprocess.run(
        [
            boardctl_or_skip(),
            "-C",
            str(board),
            "update",
            TASK_A,
            "--commit-hash",
            head,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    (verdicts / f"{TASK_A}.json").write_text(
        json.dumps(
            {"status": "pass", "commit": "f" * 40, "obligations": ["SG8-VER-1"]},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    code, out, err = run_spec(
        "plan-reconcile",
        str(root),
        "--plan",
        PLAN,
        "--board",
        str(board),
        "--repo-root",
        str(REPO_ROOT),
        "--verdict-root",
        str(verdicts),
    )
    assert code == 2, out
    assert "verdict-commit-mismatch" in err, err
    assert TASK_A in err and head in err and "f" * 40 in err, err

    # A verdict bound to the row's own commit IS reconciled (the check is not an unconditional red).
    (verdicts / f"{TASK_A}.json").write_text(
        json.dumps(
            {"status": "pass", "commit": head, "obligations": ["SG8-VER-1"]},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    code, out, err = run_spec(
        "plan-reconcile",
        str(root),
        "--plan",
        PLAN,
        "--board",
        str(board),
        "--repo-root",
        str(REPO_ROOT),
        "--verdict-root",
        str(verdicts),
    )
    assert code == 0, err
    rows = {row["task"]: row for row in json.loads(out)["rows"]}
    assert rows[TASK_A]["verdict"] == "pass", rows[TASK_A]


# ---------------------------------------------------------------- AC6: the marker
def test_the_plan_trace_marker_names_every_field(tmp_path: Path) -> None:
    """AC6: the marker carries row/spec/test/evidence/witness, with `none:<reason>` when absent.

    The doctrine's closeout marker has more fields than the exported stub used to carry, and
    `witness` was missing from the PARSER's vocabulary — a field a reader never sees is a field no
    marker may name. The exported marker is re-read from the canonical row, parsed, and resolved
    back to the exact authored blocks; a bare `none` is refused rather than tolerated.
    """
    boardctl_or_skip()
    root = write_plan_corpus(tmp_path)
    board = make_board(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    plan, _digest = approve_current(root, board, ledger)
    assert export(root, board, ledger)[0] == 0

    assert "witness" in auger.SPEC_TASK_MARKER_FIELDS, auger.SPEC_TASK_MARKER_FIELDS

    entry = plan["tasks"][0]
    marker = board_row(board, TASK_A)["reasoning"].splitlines()[-1]
    assert marker.startswith("ch:trace "), marker
    fields = auger.parse_trace_marker(marker)
    assert fields["row"] == TASK_A, fields
    assert fields["spec"] == f"plan-a.xml#{TASK_A}", fields
    assert fields["test"] == f"{TRACKED_TEST}::{TRACKED_SELECTOR}", fields
    assert fields["witness"] and fields["witness"].startswith("boardctl:"), fields
    for absent in ("evidence", "verdict", "commit"):
        assert fields[absent].startswith("none:") and len(fields[absent]) > 5, fields[
            absent
        ]
        assert " " not in fields[absent], "an absent-field reason must be one token"

    resolved = auger.resolve_trace_marker(marker, auger.load_spec_corpus(root)[2])
    assert resolved["row"] == TASK_A, resolved
    assert resolved["blocks"] == [TASK_A], resolved
    assert {item["id"] for item in resolved["scope"]} == {
        "SG8-REQ-1",
        "SG8-AC-1",
        "SG8-REQ-9",
    }, resolved["scope"]

    # The DRAFT marker (before any board exists) states its absences instead of guessing.
    draft_fields = auger.parse_trace_marker(
        auger.render_task_trace_marker(
            entry["packet"], test=f"{TRACKED_TEST}::{TRACKED_SELECTOR}"
        )
    )
    assert draft_fields["witness"] == auger.SPEC_TASK_DRAFT_WITNESS, draft_fields
    assert draft_fields["row"] == TASK_A
    assert auger.SPEC_TASK_EXPORT_WITNESS in marker, marker

    with pytest.raises(auger.SpecError) as bare:
        auger.parse_trace_marker(marker.replace("none:not-judged", "none", 1))
    assert bare.value.code == "unexplained-absent-field", bare.value

    # The PRD is where the marker contract is written down; a generated marker nothing documents
    # is the paperwork failure this row's AC6 names.
    prd = PRD.read_text(encoding="utf-8")
    assert "spec task-packet" in prd
    assert "none:<reason>" in prd


# ---------------------------------------------------------------- the gate arm
def test_gate_arm_runs_these_files() -> None:
    """This proof file must be in tests/gate.sh's pytest arm, or it is never executed."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_plan_approval.py" in arm[0], (
        "tests/test_plan_approval.py must be in tests/gate.sh's pytest arm — the guard's test "
        "command is the gate, so a proof file outside it is never run"
    )
