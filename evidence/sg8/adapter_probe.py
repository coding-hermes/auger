"""SG8 evidence: the REAL GitReins consumer accepts the packet Auger produces (AUG-124 AC-18/19/20).

This probe is evidence, not a gate arm: it imports `engine.auger_packet` from the GitReins checkout
(READ ONLY) and drives `TaskPacketAdapter` exactly as `engine.judge._prepare_auger_task` does. It is
the only place the wire format is proved against the consumer instead of against a mirror of it.

What it proves, in order:

  1  a VALID packet built from an approved plan passes `prepare()`; the context carries the board
     criteria verbatim, the packet deep-copied, and the worker summary flagged untrusted;
  2  each INVALID variant is refused by the consumer with the offending ids named — missing
     ch:trace, a trace ref outside `scope.in_scope`, an inactive status, an absent digest, and a
     test path that is not tracked at HEAD;
  3  a test path that is TRACKED but DIRTY vs HEAD is refused by `run_tests()` (the commit binding);
  4  AC-20: a RED migration test committed on the tree produces `passed=False` from the consumer's
     own harness, and Auger's verdict function refuses to let a worker summary turn it green — and
     an "absent" obligation (a selector the file does not define) fails the same way.

No judge is started: `prepare()` and `run_tests()` are the lifecycle's intake half.

Usage:  .venv/bin/python evidence/sg8/adapter_probe.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = Path("/home/kara/proj-wt-auger-xml-hardening")
GITREINS = Path("/home/kara/gitreins")

sys.path.insert(0, str(WORKTREE))
import auger  # noqa: E402

sys.path.insert(0, str(GITREINS))
from engine.auger_packet import (  # noqa: E402
    PacketValidationError,
    TaskPacketAdapter,
)

PLAN = "SG8-PROBE-PLAN"
TASK_GREEN = "SG8-PROBE-TASK-1"
TASK_RED = "SG8-PROBE-TASK-2"
BOARDCTL = shutil.which("boardctl")
FAILURES = 0


def ok(label: str, detail: str = "") -> None:
    print(f"PROBE-OK   {label:<46} {detail}".rstrip())


def bad(label: str, detail: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"PROBE-FAIL {label:<46} {detail}")


def expect(condition: bool, label: str, detail: str = "") -> None:
    (ok if condition else bad)(label, detail)


def git(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=auger@example.invalid",
            "-c",
            "user.name=auger",
            *argv,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


CORPUS_DOCUMENT = """<spec xmlns="urn:auger:spec:1" id="SG8-PROBE" version="1.0.0" schema-version="1.0">
  <requirement id="SG8-PREQ-1" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.probe" verification="test" verifies="SG8-PVER-1">
    The migration test is the obligation that decides this task.
  </requirement>
  <verification id="SG8-PVER-1" kind="verification" status="active" version="1.0.0"
                applies-to="auger.probe" method="test" type="test" runner="pytest"
                path="tests/test_migration.py" selector="test_migration_applied"
                assertions="the migration applies on the committed tree" />
  <acceptance id="SG8-PAC-1" kind="acceptance" status="active" version="1.0.0"
              applies-to="auger.probe" verification="test" verifies="SG8-PREQ-1">
    The migration test passes on the committed tree.
  </acceptance>
  <requirement id="SG8-PREQ-9" kind="requirement" status="active" version="1.0.0"
               verification="inspection">A later wave owns the rollback path.</requirement>
  <task id="SG8-PROBE-TASK-1" kind="task" status="active" version="1.0.0"
        applies-to="auger.probe" verification="test" plan="SG8-PROBE-PLAN" plan-version="1.0.0"
        in-scope="SG8-PREQ-1" acceptance="SG8-PAC-1" out-of-scope="SG8-PREQ-9">
    Prove the migration test on this tree.
    <exclusion ref="SG8-PREQ-9" reason="owned by a later wave"/>
  </task>
  <task id="SG8-PROBE-TASK-2" kind="task" status="active" version="1.0.0"
        applies-to="auger.probe" verification="test" plan="SG8-PROBE-PLAN" plan-version="1.0.0"
        in-scope="SG8-PREQ-1" acceptance="SG8-PAC-1" out-of-scope="SG8-PREQ-9"
        depends-on="SG8-PROBE-TASK-1">
    Prove the RED migration test on this tree.
    <exclusion ref="SG8-PREQ-9" reason="owned by a later wave"/>
  </task>
</spec>
"""

RED_DOCUMENT = """<spec xmlns="urn:auger:spec:1" id="SG8-PROBE-RED" version="1.0.0" schema-version="1.0">
  <requirement id="SG8-PREQ-2" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.probe" verification="test" verifies="SG8-PVER-2">
    The migration test must pass on the committed tree.
  </requirement>
  <verification id="SG8-PVER-2" kind="verification" status="active" version="1.0.0"
                applies-to="auger.probe" method="test" type="test" runner="pytest"
                path="tests/test_red_migration.py" selector="test_migration_applied"
                assertions="RED on the committed tree, by construction" />
  <acceptance id="SG8-PAC-2" kind="acceptance" status="active" version="1.0.0"
              applies-to="auger.probe" verification="test" verifies="SG8-PREQ-2">
    The migration test passes on the committed tree.
  </acceptance>
  <task id="SG8-PROBE-TASK-RED" kind="task" status="active" version="1.0.0"
        applies-to="auger.probe" verification="test" plan="SG8-PROBE-PLAN" plan-version="1.0.0"
        in-scope="SG8-PREQ-2" acceptance="SG8-PAC-2">
    Prove the RED migration test on this tree.
  </task>
</spec>
"""


def build_consumer(base: Path) -> Path:
    """A real repository with a committed GREEN test and a committed RED test."""
    repo = base / "consumer"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_migration.py").write_text(
        "def test_migration_applied():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tests" / "test_red_migration.py").write_text(
        "def test_migration_applied():\n    assert False, 'migration is red on the committed tree'\n",
        encoding="utf-8",
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "tests")
    git(repo, "commit", "-q", "-m", "the migration fixtures")
    return repo


def build_corpus(base: Path) -> Path:
    root = base / "corpus"
    root.mkdir(parents=True)
    (root / "probe.xml").write_text(CORPUS_DOCUMENT, encoding="utf-8")
    (root / "red.xml").write_text(RED_DOCUMENT, encoding="utf-8")
    return root


def approved_export(root: Path, board: Path, ledger: Path) -> dict:
    plan = auger.build_spec_plan(root, PLAN)
    auger.record_plan_approval(
        ledger, plan, digest=plan["digest"], decision="approved", approver="probe"
    )
    result = auger.export_spec_plan(root, PLAN, board=board, ledger=ledger)
    if result["status"] not in ("exported", "unchanged"):
        raise SystemExit(f"probe setup: the plan did not export: {result}")
    return plan


def clone(packet: dict) -> dict:
    return json.loads(json.dumps(packet))


def main() -> int:
    if BOARDCTL is None:
        print(
            "PROBE-FAIL boardctl is not installed; the board a packet binds to cannot be created"
        )
        return 2
    base = Path(
        tempfile.mkdtemp(prefix="sg8-adapter-", dir="/home/kara/.hermes/cache/scratch")
    )
    print(f"SG8 adapter probe — scratch {base}")
    consumer = build_consumer(base)
    root = build_corpus(base)
    board = base / "board"
    board.mkdir()
    subprocess.run(
        [
            BOARDCTL,
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
    ledger = base / "ledger"
    ledger.mkdir()
    plan = approved_export(root, board, ledger)
    print(f"plan {PLAN} digest {plan['digest']}\n")

    adapter = TaskPacketAdapter(str(consumer))
    head = git(consumer, "rev-parse", "HEAD").stdout.strip()

    def show(board_id: str) -> dict:
        done = subprocess.run(
            [BOARDCTL, "-C", str(board), "show", board_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise SystemExit(f"probe setup: boardctl show {board_id}: {done.stderr}")
        return json.loads(done.stdout)

    # ---------------------------------------------------------------- 1. the valid packet
    print("== 1 valid packet -> prepare() succeeds ==")
    board_row = show(TASK_GREEN)
    packet = auger.build_judge_packet(
        root, PLAN, TASK_GREEN, board=board, ledger=ledger, repo_root=consumer
    )
    expect(
        auger.validate_judge_packet(packet, board_row, repo_root=consumer) == [],
        "auger mirror",
        "no unresolved ids",
    )
    context = adapter.prepare(board_row, packet)
    expect(
        context["board_id"] == TASK_GREEN,
        "board_id preserved",
        str(context["board_id"]),
    )
    # The consumer normalizes `criteria` to a LIST and keeps the board's own value separately; both
    # must be the row's verbatim text, with nothing re-rendered from the packet.
    expect(
        context["criteria"] == [board_row["acceptance_criteria"]],
        "board criteria verbatim",
        repr(context["criteria"])[:80],
    )
    expect(
        context["board_acceptance_criteria"] == board_row["acceptance_criteria"],
        "board criteria unchanged in context",
        repr(context["board_acceptance_criteria"])[:60],
    )
    expect(
        context["auger_packet"]["digests"]["plan"] == plan["digest"],
        "plan digest survived",
        plan["digest"][:16],
    )
    expect(
        context["worker_summary"]["authoritative"] is False,
        "worker summary untrusted",
        str(context["worker_summary"]["trust"]),
    )
    print()

    # ---------------------------------------------------------------- 2. the invalid variants
    print("== 2 invalid variants -> PacketValidationError naming the ids ==")
    variants: list[tuple[str, dict, list[str]]] = []

    missing_trace = clone(packet)
    missing_trace.pop("ch_trace", None)
    variants.append(("missing ch_trace", missing_trace, ["ch:trace"]))

    out_of_scope = clone(packet)
    out_of_scope["ch_trace"]["spec"] = ["SG8-PREQ-9"]
    variants.append(("trace ref OUT of in_scope", out_of_scope, ["SG8-PREQ-9"]))

    inactive = clone(packet)
    for block in inactive["spec_blocks"]:
        if block["id"] == "SG8-PREQ-1":
            block["status"] = "deprecated"
    variants.append(("spec block status not active", inactive, ["SG8-PREQ-1"]))

    no_digest = clone(packet)
    no_digest["test_refs"][0]["digest"] = ""
    variants.append(("test ref has no digest", no_digest, ["SG8-PVER-1"]))

    untracked = clone(packet)
    untracked["test_refs"][0]["path"] = "tests/test_absent.py"
    variants.append(("test path not tracked at HEAD", untracked, ["SG8-PVER-1"]))

    for label, broken, expected_ids in variants:
        mirror = auger.validate_judge_packet(broken, board_row, repo_root=consumer)
        try:
            adapter.prepare(board_row, broken)
        except PacketValidationError as exc:
            named = [ref for ref in expected_ids if ref in exc.unresolved_ids]
            expect(
                bool(named),
                label,
                f"consumer refused: {exc.unresolved_ids}",
            )
            expect(
                bool(named and all(ref in mirror for ref in expected_ids)),
                f"{label} — auger mirror agrees",
                f"mirror: {mirror}",
            )
        else:
            bad(label, "the consumer ACCEPTED an invalid packet")

    # ---------------------------------------------------------------- 3. dirty vs HEAD
    print("\n== 3 tracked-but-dirty path -> run_tests() refuses (commit binding) ==")
    tracked = consumer / "tests" / "test_migration.py"
    original = tracked.read_text(encoding="utf-8")
    tracked.write_text(
        original + "\ndef test_uncommitted_extra():\n    assert True\n",
        encoding="utf-8",
    )
    try:
        adapter.run_tests(context)
    except PacketValidationError as exc:
        expect(
            "SG8-PVER-1" in exc.unresolved_ids,
            "dirty-vs-HEAD refused",
            str(exc.unresolved_ids),
        )
    else:
        bad("dirty-vs-HEAD refused", "run_tests ran a path that differs from HEAD")
    finally:
        tracked.write_text(original, encoding="utf-8")
    expect(
        git(consumer, "diff", "--quiet", "HEAD").returncode == 0,
        "consumer tree restored",
        "clean vs HEAD",
    )
    print()

    # ---------------------------------------------------------------- 4. AC-20: the red obligation
    print("== 4 AC-20 red migration test -> verdict not PASS, obligation cited ==")
    red_row = show("SG8-PROBE-TASK-RED")
    red_packet = auger.build_judge_packet(
        root, PLAN, "SG8-PROBE-TASK-RED", board=board, ledger=ledger, repo_root=consumer
    )
    claim = "All required tests pass; the migration test is green and verified."
    red_context = adapter.prepare(red_row, red_packet, worker_summary=claim)
    expect(
        red_context["worker_summary"]["authoritative"] is False,
        "claim is context only",
        "authoritative=False",
    )
    results = adapter.run_tests(red_context)
    expect(len(results) == 1, "one obligation run", str(len(results)))
    expect(
        results[0].passed is False,
        "consumer harness says FAILED",
        f"exit_code={results[0].exit_code}",
    )
    expect(
        results[0].evaluated_commit == head,
        "result bound to HEAD",
        results[0].evaluated_commit[:12],
    )
    verdict = auger.judge_packet_verdict(
        red_packet, [result.to_dict() for result in results], worker_summary=claim
    )
    expect(verdict["status"] == "fail", "auger verdict", verdict["status"])
    cited = {citation["id"]: citation["reason"] for citation in verdict["citations"]}
    expect(
        cited.get("SG8-PVER-2") == "failed-test-obligation",
        "cites the red obligation",
        json.dumps(cited),
    )

    # An ABSENT obligation (a selector the committed file does not define) fails the same way.
    absent = clone(red_packet)
    absent["test_refs"][0]["selector"] = "test_does_not_exist_on_this_tree"
    absent_context = adapter.prepare(red_row, absent, worker_summary=claim)
    absent_results = adapter.run_tests(absent_context)
    expect(
        absent_results[0].passed is False,
        "absent obligation fails",
        f"exit_code={absent_results[0].exit_code}",
    )
    absent_verdict = auger.judge_packet_verdict(
        absent, [r.to_dict() for r in absent_results], worker_summary=claim
    )
    expect(
        absent_verdict["status"] == "fail",
        "absent -> verdict fail",
        absent_verdict["status"],
    )
    print()

    # ---------------------------------------------------------------- 5. the control
    print("== 5 control: the GREEN obligation passes ==")
    green_results = adapter.run_tests(context)
    expect(
        green_results[0].passed is True,
        "green obligation passes",
        f"exit_code={green_results[0].exit_code}",
    )
    green_verdict = auger.judge_packet_verdict(
        packet, [r.to_dict() for r in green_results]
    )
    expect(
        green_verdict["status"] == "pass",
        "green -> verdict pass",
        green_verdict["status"],
    )
    expect(
        green_verdict["citations"] == [],
        "no citations on a pass",
        str(green_verdict["citations"]),
    )
    print()

    if FAILURES:
        print(f"PROBE RESULT: {FAILURES} expectation(s) UNMET")
        return 1
    print(
        "PROBE RESULT: every expectation met — the real consumer accepts Auger's packet and refuses every invalid variant"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
