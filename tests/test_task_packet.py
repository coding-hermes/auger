"""AUG-124 AC-18/19/20: the GitReins packet Auger must PRODUCE for the adapter that consumes it.

`engine/auger_packet.py` in the GitReins repository is the consumer and it fails closed: its
`TaskPacketAdapter._unresolved` names every id it cannot resolve, and a packet that fails there is a
Tier-1 failure that never reaches a judge. So a "packet" that only looks right is worthless — it has
to carry what the adapter checks: the board id (or an explicit mapping), a resolvable `ch:trace`, a
non-empty in-scope set that does not intersect the exclusions, `spec_blocks`/`test_refs` entries with
an ACTIVE status and a real digest, a tracked test path with a selector, and a non-empty `digests`
bag. Everything here is produced from the APPROVED plan, never re-derived at handoff time, so the
digests the adapter sees are the digests that were approved.

Measured at the SG8 base (`952e5dc`, 2026-11-06): nothing on Auger's side produced this packet. The
consumer existed and refused a bad packet; there was no producer at all.

  AC-18 preserves the approved input verbatim ..... test_the_packet_preserves_the_approved_input_verbatim
  AC-18 stale approval / untracked path refused ... test_the_packet_is_bound_to_the_approved_plan,
                                                    test_a_packet_naming_an_untracked_test_path_is_refused
  AC-19 invalid trace: no judge, ids named ....... test_an_invalid_trace_starts_no_judge_and_names_the_ids
  AC-19 valid trace: lifecycle runs exactly once .. test_a_valid_trace_starts_the_lifecycle_exactly_once
  AC-20 a false summary cannot pass a red test ... test_a_false_worker_summary_cannot_pass_a_red_obligation
  arm ............................................ test_gate_arm_runs_these_files

HERMETIC: no DuckBrain, no JEV, no network, no LLM, and NO REAL JUDGE. AC-19's invocation count is
measured on an injected dispatch seam; AC-20's red obligation is a throwaway pytest file run by the
harness command the packet itself names. The live probe against the REAL adapter is evidence
(`evidence/sg8/05-adapter.txt`), deliberately not a gate arm.

ch:trace row=AUG-124 spec=docs/prd/xml-spec-graph.md#requirements
         test=tests/test_task_packet.py evidence=evidence/sg8/ verdict=none:not-judged
"""

from __future__ import annotations

import contextlib
import hashlib
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
BOARDCTL = shutil.which("boardctl")

TRACKED_TEST = "tests/test_xml_specs.py"
TRACKED_SELECTOR = "test_gate_runs_these_tests"

PLAN = "SG8-PACKET-PLAN"
TASK = "SG8-PACKET-TASK"
ACCEPTANCE = "The packet preserves the board acceptance criteria verbatim."


# ---------------------------------------------------------------- driving the CLI
def run_spec(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- fixture corpus
def write_corpus(
    base: Path,
    *,
    test_path: str = TRACKED_TEST,
    selector: str = TRACKED_SELECTOR,
    prose: str = "Export this task as one canonical board row.",
) -> Path:
    """One task, one in-scope requirement, one verification obligation, one excluded requirement."""
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    document = (
        '<spec xmlns="urn:auger:spec:1" id="SG8-PACKET" version="1.0.0" schema-version="1.0">\n'
        '  <requirement id="SG8-PREQ-1" kind="requirement" status="active" version="1.0.0"\n'
        '               applies-to="auger.packet" verification="test" verifies="SG8-PVER-1">\n'
        "    The plan export produces one canonical board row per task.\n"
        "  </requirement>\n"
        '  <verification id="SG8-PVER-1" kind="verification" status="active" version="1.0.0"\n'
        '                applies-to="auger.packet" method="test" type="test" runner="pytest"\n'
        f'                path="{test_path}" selector="{selector}"\n'
        '                assertions="the packet names the obligation exactly" />\n'
        '  <acceptance id="SG8-PAC-1" kind="acceptance" status="active" version="1.0.0"\n'
        '              applies-to="auger.packet" verification="test" verifies="SG8-PREQ-1">\n'
        f"    {ACCEPTANCE}\n"
        "  </acceptance>\n"
        '  <requirement id="SG8-PREQ-9" kind="requirement" status="active" version="1.0.0"\n'
        '               verification="inspection">\n'
        "    A later wave owns the migration path.\n"
        "  </requirement>\n"
        f'  <task id="{TASK}" kind="task" status="active" version="1.0.0"\n'
        '        applies-to="auger.packet" verification="test"\n'
        f'        plan="{PLAN}" plan-version="1.0.0"\n'
        '        in-scope="SG8-PREQ-1" acceptance="SG8-PAC-1" out-of-scope="SG8-PREQ-9">\n'
        f"    {prose}\n"
        '    <exclusion ref="SG8-PREQ-9" reason="owned by the migration wave"/>\n'
        "  </task>\n"
        "</spec>\n"
    )
    (root / "packet.xml").write_text(document, encoding="utf-8")
    return root


# ---------------------------------------------------------------- board + approval fixtures
def boardctl_or_skip() -> str:
    if BOARDCTL is None:
        pytest.skip(
            "boardctl is not installed on this host, so the canonical board row a GitReins packet "
            "is bound to cannot be created; install coding-hermes-tools (`boardctl`) and re-run — "
            "the AUG-124 board proofs did NOT run here"
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
    return board


def board_row(board: Path, row_id: str) -> dict:
    done = subprocess.run(
        [boardctl_or_skip(), "-C", str(board), "show", row_id],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def approved_plan(
    base: Path, *, repo_root: Path = REPO_ROOT, **corpus_kwargs
) -> tuple[Path, Path, Path, dict]:
    """Draft → approve → export. Returns (root, board, ledger, plan).

    `repo_root` belongs to the HANDOFF (it is where the judge's tracked paths resolve), not to the
    export: the export writes canonical rows and resolves no test obligation, so the corpus may name
    a test in a repository other than the one it is drafted in.
    """
    boardctl_or_skip()
    root = write_corpus(base, **corpus_kwargs)
    board = make_board(base)
    ledger = base / "ledger"
    ledger.mkdir()
    code, out, err = run_spec("plan-draft", str(root), "--plan", PLAN)
    assert code == 0, err
    plan = json.loads(out)
    code, out, err = run_spec(
        "plan-approve",
        str(root),
        "--plan",
        PLAN,
        "--approve",
        "--digest",
        plan["digest"],
        "--approver",
        "owner",
        "--ledger",
        str(ledger),
        "--at",
        "2026-11-06T00:00:00+00:00",
    )
    assert code == 0, err
    code, out, err = run_spec(
        "plan-export",
        str(root),
        "--plan",
        PLAN,
        "--board",
        str(board),
        "--ledger",
        str(ledger),
    )
    assert code == 0, f"the approved plan did not export: {err}"
    return root, board, ledger, plan


def judge_packet(
    root: Path,
    board: Path,
    ledger: Path,
    *,
    out: Path | None = None,
    trace: str | None = None,
    repo_root: Path = REPO_ROOT,
    worker_summary: str | None = None,
) -> tuple[int, str, str]:
    argv = [
        "judge-packet",
        str(root),
        "--plan",
        PLAN,
        "--row",
        TASK,
        "--board",
        str(board),
        "--ledger",
        str(ledger),
        "--repo-root",
        str(repo_root),
    ]
    if out is not None:
        argv.extend(["--out", str(out)])
    if trace is not None:
        argv.extend(["--trace", trace])
    if worker_summary is not None:
        argv.extend(["--worker-summary", worker_summary])
    return run_spec(*argv)


def trace_line(**overrides: str) -> str:
    """A well-formed marker for TASK, with named fields overridable one at a time."""
    fields = {
        "row": TASK,
        "spec": f"packet.xml#{TASK}",
        "test": f"{TRACKED_TEST}::{TRACKED_SELECTOR}",
        "evidence": "none:not-exported",
        "verdict": "none:not-judged",
        "commit": "none:not-implemented",
        "witness": "boardctl:show-readback",
    }
    fields.update(overrides)
    return "ch:trace " + " ".join(f"{key}={value}" for key, value in fields.items())


# ---------------------------------------------------------------- AC-18: the approved input
def test_the_packet_preserves_the_approved_input_verbatim(tmp_path: Path) -> None:
    """AC-18: acceptance criteria, scope refs, obligations, id mapping and digests survive intake.

    Every field is compared against an INDEPENDENT source — the canonical board row for the
    criteria, the approved plan for the digests, the corpus for the obligation — so a packet that
    quietly re-derived any of them from the current XML would be caught. The packet is also re-read
    from disk and re-emitted: two exports of the same approved plan are byte-identical.
    """
    root, board, ledger, plan = approved_plan(tmp_path)
    entry = plan["tasks"][0]
    row = board_row(board, TASK)
    out = tmp_path / "packet.json"

    code, stdout, err = judge_packet(root, board, ledger, out=out)
    assert code == 0, f"spec judge-packet refused an approved row: {err}"
    printed = json.loads(stdout)
    packet = printed["packet"]

    # Identity: the board id, plus an explicit mapping to the GitReins task id.
    assert packet["board_id"] == TASK, packet
    assert packet["id_mapping"]["board_id"] == TASK, packet["id_mapping"]
    assert packet["id_mapping"]["gitreins_task_id"] == TASK, packet["id_mapping"]
    assert packet["id_mapping"]["auger_task_id"] == TASK, packet["id_mapping"]

    # Acceptance criteria: the BOARD's own field, verbatim, and the approved text behind it.
    assert packet["acceptance_criteria"] == row["acceptance_criteria"], (
        "the packet does not carry the board's acceptance criteria verbatim"
    )
    assert packet["board_acceptance_criteria"] == row["acceptance_criteria"]
    assert entry["acceptance"][0]["text"] in packet["acceptance_criteria"]

    # Scope: in-scope exactly as approved, exclusions disjoint and non-empty only where approved.
    assert packet["scope"]["in_scope"] == ["SG8-PREQ-1"], packet["scope"]
    assert packet["scope"]["out_of_scope"] == ["SG8-PREQ-9"], packet["scope"]
    assert not set(packet["scope"]["in_scope"]) & set(packet["scope"]["out_of_scope"])

    # The trace the consumer validates: the row id, a spec id that IS in scope, a test id that IS
    # an obligation — ids, not paths, because `_unresolved` indexes `spec_blocks`/`test_refs` by id.
    assert packet["ch_trace"] == {
        "row": TASK,
        "spec": ["SG8-PREQ-1"],
        "test": ["SG8-PVER-1"],
    }, packet["ch_trace"]

    # Spec blocks: active status + a digest, and the digest is the APPROVED one, not a fresh hash.
    assert [block["id"] for block in packet["spec_blocks"]] == [
        "SG8-PREQ-1",
        "SG8-PAC-1",
    ], packet["spec_blocks"]
    approved = {block["id"]: block["digest"] for block in entry["spec_blocks"]}
    for block in packet["spec_blocks"]:
        assert block["status"] == "active", block
        assert block["digest"] == approved[block["id"]], block
        assert block["version"] == "1.0.0" and block["source"].endswith(
            "#" + block["id"]
        ), block

    # Test obligations: exact path, selector, runner and digest.
    assert packet["test_refs"] == [
        {
            "id": "SG8-PVER-1",
            "kind": "verification",
            "status": "active",
            "version": "1.0.0",
            "source": "packet.xml#SG8-PVER-1",
            "digest": entry["test_refs"][0]["digest"],
            "runner": "pytest",
            "path": TRACKED_TEST,
            "selector": TRACKED_SELECTOR,
        }
    ], packet["test_refs"]

    # Digests: non-empty, and the plan digest is the one that was approved.
    assert packet["digests"]["plan"] == plan["digest"], packet["digests"]
    assert packet["digests"]["packet"] == entry["packet_digest"], packet["digests"]
    assert packet["digests"]["SG8-PREQ-1"] == approved["SG8-PREQ-1"]
    assert packet["sources"] == plan["sources"], packet["sources"]

    # The packet itself is valid under Auger's mirror of the consumer's rules.
    assert auger.validate_judge_packet(packet, row) == [], auger.validate_judge_packet(
        packet, row
    )

    # On disk: the written file is the printed packet, and a second export is byte-identical.
    assert json.loads(out.read_text(encoding="utf-8"))["packet"] == packet
    first = out.read_bytes()
    digest_before = hashlib.sha256(first).hexdigest()
    code, stdout, err = judge_packet(root, board, ledger, out=out)
    assert code == 0, err
    assert json.loads(stdout)["packet"] == packet
    assert hashlib.sha256(out.read_bytes()).hexdigest() == digest_before, (
        "a repeated judge-packet export produced different bytes — the handoff is not idempotent"
    )


def test_the_packet_is_bound_to_the_approved_plan(tmp_path: Path) -> None:
    """AC-18/AC-2: editing the corpus after approval refuses the handoff; it is never re-derived.

    This is the property the consumer can never check for itself: the packet must be a statement of
    what was APPROVED. If the spec text moves after the approval, the plan digest no longer matches
    and the handoff refuses — naming both digests — instead of shipping a packet whose digests
    describe a corpus nobody reviewed.
    """
    root, board, ledger, plan = approved_plan(tmp_path)
    write_corpus(
        tmp_path, prose="Export this task, but the prose changed after approval."
    )
    code, out, err = judge_packet(root, board, ledger)
    assert code == 2, f"a stale plan still produced a packet: {out!r}"
    assert "stale-plan-digest" in err, err
    assert plan["digest"] in err, err
    current = json.loads(run_spec("plan-draft", str(root), "--plan", PLAN)[1])["digest"]
    assert current in err, f"the finding does not name the current digest: {err}"

    # And a plan that was never approved at all has no approved packet to bind to.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    root2 = write_corpus(fresh)
    board2 = make_board(fresh)
    ledger2 = fresh / "ledger"
    ledger2.mkdir()
    code, out, err = judge_packet(root2, board2, ledger2)
    assert code == 2, out
    assert "unapproved-plan-digest" in err, err


def test_a_packet_naming_an_untracked_test_path_is_refused(tmp_path: Path) -> None:
    """AC-18: an obligation whose path the consuming repository does not TRACK cannot be judged.

    The consumer resolves a test ref with `git ls-files --error-unmatch` inside its own workdir and
    refuses an untracked path, so Auger refuses it first with the same vocabulary — a packet that
    names a file the judge cannot see is a fail-closed Tier-1 result, not a packet.

    The scratch repository is a REAL one (its own package, its own committed test), never auger's
    tree: a corpus inside auger's checkout hides exactly the defect worth catching.
    """
    boardctl_or_skip()
    scratch = tmp_path / "consumer"
    (scratch / "tests").mkdir(parents=True)
    (scratch / "tests" / "test_good.py").write_text(
        "def test_good():\n    assert True\n", encoding="utf-8"
    )
    (scratch / "tests" / "test_untracked.py").write_text(
        "def test_untracked():\n    assert True\n", encoding="utf-8"
    )
    committed = subprocess.run(
        [
            "git",
            "-c",
            "user.email=auger@example.invalid",
            "-c",
            "user.name=auger",
            "init",
            "-q",
            "-b",
            "main",
            ".",
        ],
        cwd=str(scratch),
        capture_output=True,
        text=True,
        check=False,
    )
    assert committed.returncode == 0, committed.stderr
    for argv in (
        ["add", "tests/test_good.py"],
        ["commit", "-q", "-m", "the tracked test"],
    ):
        # `-c core.hooksPath=/dev/null`: a host-global hooks dir must not run inside a throwaway
        # fixture repo (a post-commit hook that dirties the clone would break the byte-clean
        # assertion at the end of this test for a reason unrelated to the packet).
        done = subprocess.run(
            [
                "git",
                "-c",
                "user.email=auger@example.invalid",
                "-c",
                "user.name=auger",
                "-c",
                "core.hooksPath=/dev/null",
                *argv,
            ],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr

    # The tracked obligation is accepted (so the refusal below is about tracking, not about paths).
    good = tmp_path / "good"
    good.mkdir()
    root, board, ledger, _plan = approved_plan(
        good, repo_root=scratch, test_path="tests/test_good.py", selector="test_good"
    )
    code, out, err = judge_packet(root, board, ledger, repo_root=scratch)
    assert code == 0, f"a TRACKED consumer test was refused: {err}"

    # The untracked sibling is refused, naming the path the judge could not resolve.
    bad = tmp_path / "bad"
    bad.mkdir()
    root, board, ledger, _plan = approved_plan(
        bad,
        repo_root=scratch,
        test_path="tests/test_untracked.py",
        selector="test_untracked",
    )
    code, out, err = judge_packet(root, board, ledger, repo_root=scratch)
    assert code == 2, f"an UNTRACKED test path produced a packet: {out!r}"
    assert "untracked-test-ref" in err, err
    assert "tests/test_untracked.py" in err, err

    # The consuming repository is untouched: this proof never writes into the tree it judges. The
    # one untracked file is the deliberate fixture, so the claim is about TRACKED content.
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=str(scratch),
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.stdout.strip() == "", status.stdout


# ---------------------------------------------------------------- AC-19: the negative pair
def test_an_invalid_trace_starts_no_judge_and_names_the_ids(tmp_path: Path) -> None:
    """AC-19: every invalid trace refuses the handoff and NAMES the row/spec/test that is wrong.

    Six shapes, because they need six different fixes: no marker at all, an id that is not in the
    corpus, an id that is in the corpus but OUT of this task's scope, a marker for a DIFFERENT row,
    a marker whose file is not the block's file, and a test value that is not one of the task's
    obligations. In every case the dispatch seam records ZERO invocations — a judge that starts on
    an invalid trace would be judging a packet nobody can resolve — and stderr names the offending
    id instead of a generic "invalid".
    """
    boardctl_or_skip()
    root, board, ledger, _plan = approved_plan(tmp_path)
    marker = board_row(board, TASK)["reasoning"].splitlines()[-1]

    cases = [
        ("no marker", "row=" + TASK, "missing-trace-marker", []),
        (
            "unknown spec id",
            trace_line(spec="packet.xml#SG8-PREQ-404"),
            "unknown-spec-id",
            ["SG8-PREQ-404"],
        ),
        (
            "out-of-scope spec ref",
            trace_line(spec="packet.xml#SG8-PREQ-9"),
            "trace-spec-out-of-scope",
            ["SG8-PREQ-9"],
        ),
        (
            "ambiguous spec ref",
            trace_line(spec=f"packet.xml#{TASK},packet.xml#SG8-PREQ-9"),
            "trace-spec-out-of-scope",
            ["SG8-PREQ-9"],
        ),
        (
            "wrong row",
            trace_line(row="SG8-NOT-THE-ROW"),
            "trace-row-mismatch",
            [],
        ),
        (
            "stale spec file",
            trace_line(spec=f"other.xml#{TASK}"),
            "stale-spec-ref",
            [TASK],
        ),
        (
            "test is not an obligation",
            trace_line(test="tests/other.py::test_other"),
            "trace-test-not-obligation",
            ["tests/other.py::test_other"],
        ),
    ]

    for label, trace, expected_code, named in cases:
        calls: list = []

        def dispatch(context, calls=calls):
            calls.append(context)
            return {"ok": True}

        with pytest.raises(auger.SpecError) as refused:
            auger.start_judge_handoff(
                root,
                PLAN,
                TASK,
                board=board,
                ledger=ledger,
                repo_root=REPO_ROOT,
                trace=trace,
                dispatch=dispatch,
            )
        assert refused.value.code == expected_code, (
            f"{label}: expected {expected_code}, got {refused.value.code}: {refused.value.detail}"
        )
        assert calls == [], (
            f"{label}: a judge was dispatched on an invalid trace: {calls}"
        )
        for value in named:
            assert value in refused.value.detail, (
                f"{label}: the refusal does not name {value!r}: {refused.value.detail}"
            )
        if label == "wrong row":
            assert "SG8-NOT-THE-ROW" in refused.value.detail, refused.value.detail
            assert TASK in refused.value.detail, refused.value.detail

    # The exported marker itself is VALID — the negatives above are not a broken fixture.
    assert auger.parse_trace_marker(marker)["row"] == TASK


# ---------------------------------------------------------------- AC-19: the positive pair
def test_a_valid_trace_starts_the_lifecycle_exactly_once(tmp_path: Path) -> None:
    """AC-19: a valid trace calls the lifecycle entry point ONCE, with the board's criteria.

    The invocation count is the whole assertion: zero on an invalid trace, exactly one per handoff
    on a valid one. The context handed over carries the board acceptance criteria VERBATIM (the
    judge's authority is the board, not a re-rendering of it), and the packet that goes with it is
    the one the consumer accepts.
    """
    boardctl_or_skip()
    root, board, ledger, _plan = approved_plan(tmp_path)
    row = board_row(board, TASK)

    calls: list = []

    def dispatch(context):
        calls.append(context)
        return {"started": "task-1"}

    result = auger.start_judge_handoff(
        root,
        PLAN,
        TASK,
        board=board,
        ledger=ledger,
        repo_root=REPO_ROOT,
        dispatch=dispatch,
    )
    assert result["status"] == "dispatched", result
    assert result["invocations"] == 1, result
    assert len(calls) == 1, calls
    handed = calls[0]
    assert handed["acceptance_criteria"] == row["acceptance_criteria"], handed
    assert handed["board_task"]["id"] == TASK, handed["board_task"]
    assert handed["packet"]["board_id"] == TASK, handed["packet"]
    assert handed["packet"]["digests"]["plan"] == result["digest"], result

    # Every handoff is one invocation, not an accumulating side effect.
    auger.start_judge_handoff(
        root,
        PLAN,
        TASK,
        board=board,
        ledger=ledger,
        repo_root=REPO_ROOT,
        dispatch=dispatch,
    )
    assert len(calls) == 2, calls

    # An unconfigured dispatch seam REFUSES: Auger never invents a judge invocation.
    with pytest.raises(auger.SpecError) as unconfigured:
        auger.start_judge_handoff(
            root,
            PLAN,
            TASK,
            board=board,
            ledger=ledger,
            repo_root=REPO_ROOT,
            dispatch=None,
        )
    assert unconfigured.value.code == "dispatch-unconfigured", unconfigured.value
    assert len(calls) == 2, "an unconfigured handoff still dispatched something"

    # The CLI path: no --dispatch-cmd refuses; a --dispatch-cmd runs EXACTLY once with the packet
    # path and the file it is handed parses back to this row's packet.
    code, out, err = run_spec(
        "judge-handoff",
        str(root),
        "--plan",
        PLAN,
        "--row",
        TASK,
        "--board",
        str(board),
        "--ledger",
        str(ledger),
        "--repo-root",
        str(REPO_ROOT),
    )
    assert code == 2, out
    assert "dispatch-unconfigured" in err, err
    assert out == "", "a refused handoff printed a result"

    record = tmp_path / "dispatch.log"
    script = tmp_path / "dispatch"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {record}\n', encoding="utf-8")
    script.chmod(0o755)
    packet_file = tmp_path / "cli-packet.json"
    code, out, err = run_spec(
        "judge-handoff",
        str(root),
        "--plan",
        PLAN,
        "--row",
        TASK,
        "--board",
        str(board),
        "--ledger",
        str(ledger),
        "--repo-root",
        str(REPO_ROOT),
        "--out",
        str(packet_file),
        "--dispatch-cmd",
        str(script),
    )
    assert code == 0, f"a configured handoff refused: {err}"
    invocation = [
        line for line in record.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(invocation) == 1, f"expected exactly one dispatch, got {invocation}"
    assert str(packet_file) in invocation[0], invocation[0]
    handed = json.loads(packet_file.read_text(encoding="utf-8"))
    assert handed["packet"]["board_id"] == TASK, handed["packet"]
    assert handed["packet"]["acceptance_criteria"] == row["acceptance_criteria"]


# ---------------------------------------------------------------- AC-20: the false summary
def test_a_false_worker_summary_cannot_pass_a_red_obligation(tmp_path: Path) -> None:
    """AC-20: a summary claiming green cannot turn a red (or absent) obligation into a PASS.

    The obligation is run for real — a throwaway pytest file that fails — and the verdict function
    is fed that failure plus a worker summary asserting the test passes. The verdict must be `fail`
    and must CITE the obligation id and the reason; the summary is recorded as untrusted context and
    has no path into the verdict. A green control run keeps the arm from being vacuously red.
    """
    boardctl_or_skip()
    root, board, ledger, _plan = approved_plan(tmp_path)
    packet = json.loads(judge_packet(root, board, ledger)[1])["packet"]

    red = tmp_path / "test_red_migration.py"
    red.write_text(
        "def test_migration_applied():\n    assert False, 'migration is red on the committed tree'\n",
        encoding="utf-8",
    )
    harness = [sys.executable, "-m", "pytest", str(red), "-q", "-p", "no:cacheprovider"]
    failed = subprocess.run(harness, capture_output=True, text=True, check=False)
    assert failed.returncode != 0, (
        "the deliberate red fixture PASSED — the arm would be vacuous"
    )

    result = {
        "id": "SG8-PVER-1",
        "target": str(red),
        "passed": False,
        "exit_code": failed.returncode,
        "output": (failed.stdout or "")[-400:],
        "command": harness,
    }
    claim = "All required tests pass; the migration test above is green and verified."
    verdict = auger.judge_packet_verdict(packet, [result], worker_summary=claim)
    assert verdict["status"] == "fail", verdict
    citations = {citation["id"]: citation for citation in verdict["citations"]}
    assert "SG8-PVER-1" in citations, verdict["citations"]
    assert citations["SG8-PVER-1"]["reason"] == "failed-test-obligation", citations
    assert verdict["worker_summary"]["authoritative"] is False, verdict[
        "worker_summary"
    ]
    assert verdict["worker_summary"]["trust"] == "untrusted", verdict["worker_summary"]
    assert verdict["worker_summary"]["value"] == claim, verdict["worker_summary"]

    # An obligation with NO run at all is also a failure, and it is named as missing — never as
    # green-by-default and never as a silent zero.
    absent = auger.judge_packet_verdict(packet, [], worker_summary=claim)
    assert absent["status"] == "fail", absent
    assert {citation["reason"] for citation in absent["citations"]} == {
        "missing-test-obligation"
    }, absent["citations"]
    assert {citation["id"] for citation in absent["citations"]} == {"SG8-PVER-1"}, (
        absent["citations"]
    )

    # CONTROL: the same packet with a genuinely passing run is a pass, so the arm is falsifiable.
    green = tmp_path / "test_green_migration.py"
    green.write_text(
        "def test_migration_applied():\n    assert True\n", encoding="utf-8"
    )
    harness = [
        sys.executable,
        "-m",
        "pytest",
        str(green),
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    passed = subprocess.run(harness, capture_output=True, text=True, check=False)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    control = auger.judge_packet_verdict(
        packet,
        [
            {
                "id": "SG8-PVER-1",
                "passed": True,
                "exit_code": 0,
                "output": "",
                "command": harness,
            }
        ],
        worker_summary="ignored either way",
    )
    assert control["status"] == "pass", control
    assert control["citations"] == [], control["citations"]


# ---------------------------------------------------------------- the gate arm
def test_gate_arm_runs_these_files() -> None:
    """This proof file must be in tests/gate.sh's pytest arm, or it is never executed."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_task_packet.py" in arm[0], (
        "tests/test_task_packet.py must be in tests/gate.sh's pytest arm — the guard's test "
        "command is the gate, so a proof file outside it is never run"
    )
