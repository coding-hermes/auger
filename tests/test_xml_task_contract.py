"""AUG-121: a task is a SCOPED spec block — exact in-scope refs, excluded refs with reasons.

A task row is a small spec for one exact request: it names the reusable blocks in scope, the
blocks explicitly OUT of scope with the reason each is excluded, the acceptance criteria it must
satisfy, and the method that proves it. This file proves the contract end to end and, at equal
weight, proves every way an author can get it WRONG is refused with a typed diagnostic that names
the task and the block — never a partial packet.

Measured gap at the SG7 base (`4bcc358`, 2026-11-06): `spec validate` accepts
`section|requirement|acceptance|verification` only, no `<task>` block exists in the grammar, the
schema declares no `in-scope`/`out-of-scope`/`acknowledges` attribute, nothing carries an
exclusion REASON, and there is no approved-plan export to boardctl at all — so a task "packet"
could only be assembled by hand from a prose row, which is exactly the unfixed defect.

Per acceptance line and its proof:

  AC1 typed, resolving task scope ........... test_task_scope_contract
  AC2 missing ref ........................... test_a_task_naming_a_missing_block_is_refused
  AC2 duplicate ref ......................... test_multiple_scope_refusals_are_typed (duplicate)
  AC2 inactive without acknowledgment ....... test_an_inactive_reference_needs_acknowledgment
  AC2 cross-version ref ..................... test_a_cross_version_reference_is_refused
  AC2 exclusion without a reason ............ test_an_out_of_scope_entry_without_a_reason_is_refused
  AC3 approved digest -> one board row ...... test_an_approved_plan_digest_exports_one_board_row
  AC4 unapproved/stale digest, no write ..... test_an_unapproved_or_stale_digest_leaves_the_board_untouched
  AC5 generated ch:trace marker resolves .... test_the_generated_trace_marker_resolves
  AC6 boardctl create/readback only ......... test_the_exporter_only_invokes_boardctl_create_and_readback
  arm  ..................................... test_gate_arm_runs_these_files

HERMETIC: no DuckBrain, no JEV, no network. Corpora are built in `tmp_path`; the two board tests
shell out to the real `boardctl` against a throwaway board initialised in `tmp_path`, and skip
LOUDLY (naming the remedy) only when `boardctl` is not installed on the host at all.

ch:trace row=AUG-121 spec=docs/prd/xml-spec-graph.md#requirements
         test=tests/test_xml_task_contract.py evidence=evidence/sg7/ verdict=none:not-judged
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

#: A real, git-tracked test in auger's own checkout. A tmp_path corpus names no work tree, so the
#: obligation validator is handed `--repo-root REPO_ROOT` and the only paths it may resolve are the
#: repository's own tracked files. Naming a path auger does not track would refuse
#: `untracked-test-ref` for a reason that has nothing to do with the task contract.
TRACKED_TEST = "tests/test_xml_specs.py"
TRACKED_SELECTOR = "test_gate_runs_these_tests"


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def validate(root: Path) -> tuple[int, str, str]:
    """`spec validate` with the repository root stated explicitly (AUG-127)."""
    return run_spec("validate", str(root), "--repo-root", str(REPO_ROOT))


def packet_of(root: Path, task: str = "SG7-TASK-1") -> dict:
    """`spec task-packet` as JSON: the packet, its approved digest, and its trace marker."""
    code, out, err = run_spec("task-packet", str(root), "--task", task)
    assert code == 0, f"spec task-packet refused: {err}"
    return json.loads(out)


# ---------------------------------------------------------------- fixture corpora
REQUIREMENT = (
    '  <requirement id="SG7-REQ-1" kind="requirement" status="active" version="1.0.0"\n'
    '               verification="test" verifies="SG7-VER-1">The exporter writes one board '
    "row per task.</requirement>\n"
)
VERIFICATION = (
    '  <verification id="SG7-VER-1" kind="verification" status="active" version="1.0.0"\n'
    f'                method="test" runner="pytest" path="{TRACKED_TEST}"\n'
    f'                selector="{TRACKED_SELECTOR}"\n'
    '                assertions="the exported row names the scope" />\n'
)
ACCEPTANCE = (
    '  <acceptance id="SG7-AC-1" kind="acceptance" status="active" version="1.0.0"\n'
    '              verification="test" verifies="SG7-REQ-1">The canonical row records the\n'
    "in-scope and excluded block ids with their versions.</acceptance>\n"
)
EXCLUDED = (
    '  <requirement id="SG7-REQ-9" kind="requirement" status="active" version="1.0.0"\n'
    '               verification="inspection">A later wave owns the migration path.'
    "</requirement>\n"
)


def task_block(
    *,
    in_scope: str = "SG7-REQ-1",
    acceptance: str = "SG7-AC-1",
    out_of_scope: str = "SG7-REQ-9",
    acknowledges: str = "",
    scope_version: str = "",
    plan_digest: str = "",
    exclusions: str = '<exclusion ref="SG7-REQ-9" reason="owned by a later wave"/>',
) -> str:
    """One `<task>` block in the shipped grammar, with every scope attribute overridable."""
    attributes = [
        'id="SG7-TASK-1"',
        'kind="task"',
        'status="active"',
        'version="1.0.0"',
        'verification="test"',
        f'in-scope="{in_scope}"',
    ]
    if acceptance:
        attributes.append(f'acceptance="{acceptance}"')
    if out_of_scope:
        attributes.append(f'out-of-scope="{out_of_scope}"')
    if acknowledges:
        attributes.append(f'acknowledges="{acknowledges}"')
    if scope_version:
        attributes.append(f'scope-version="{scope_version}"')
    if plan_digest:
        attributes.append(f'plan-digest="{plan_digest}"')
    return (
        "  <task " + " ".join(attributes) + ">\n"
        "    Export this task as one canonical board row.\n"
        f"    {exclusions}\n"
        "  </task>\n"
    )


def write_corpus(
    base: Path,
    *,
    blocks: str = "",
    deprecated_requirement: bool = False,
    **task_kwargs,
) -> Path:
    """A corpus holding the four reference blocks plus one `<task>`, under `base`."""
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    extra = ""
    if deprecated_requirement:
        extra = (
            '  <requirement id="SG7-REQ-OLD" kind="requirement" status="deprecated"\n'
            '               version="1.0.0" verification="inspection">Retired by a later '
            "wave.</requirement>\n"
        )
    document = (
        '<spec xmlns="urn:auger:spec:1" id="SG7-TASKS" version="1.0.0" schema-version="1.0">\n'
        f"{REQUIREMENT}{VERIFICATION}{ACCEPTANCE}{EXCLUDED}{extra}{blocks}"
        f"{task_block(**task_kwargs)}"
        "</spec>\n"
    )
    (root / "tasks.xml").write_text(document, encoding="utf-8")
    return root


def spec_blocks(root: Path) -> tuple:
    """Load the corpus through auger's own loader (the one every verb uses)."""
    return auger.load_spec_corpus(root)


# ---------------------------------------------------------------- AC1: the typed contract
def test_task_scope_contract(tmp_path: Path) -> None:
    """AC1: a task's id, lifecycle, scope, reasons, acceptance and method are typed + resolving.

    Every reference must land on an EXISTING block: the packet is compared field by field against
    the corpus, and each id it names is checked against `spec list`, which is a second reader of
    the same corpus — a packet that invented an id could not survive both.
    """
    root = write_corpus(tmp_path)
    code, out, err = validate(root)
    assert code == 0, f"a well-formed task corpus was refused: {err}"
    assert "spec: 1 document(s), 5 block(s), 5 reference(s) ok" in out, out

    listed = run_spec("list", str(root), "--kind", "task")
    assert listed[0] == 0, listed[2]
    assert "SG7-TASK-1\ttask\tactive\t1.0.0\ttasks.xml#SG7-TASK-1" in listed[1], listed[
        1
    ]

    payload = packet_of(root)
    packet = payload["packet"]
    assert packet["task"] == "SG7-TASK-1"
    assert packet["source"] == "tasks.xml#SG7-TASK-1"
    assert (packet["status"], packet["version"]) == ("active", "1.0.0")
    assert packet["method"] == "test", packet
    assert packet["scope_version"] is None

    # In-scope refs: typed records, not bare ids, and every field comes from the corpus.
    assert [entry["id"] for entry in packet["in_scope"]] == ["SG7-REQ-1"], packet
    entry = packet["in_scope"][0]
    assert entry["kind"] == "requirement"
    assert entry["status"] == "active"
    assert entry["version"] == "1.0.0"
    assert entry["method"] == "test"
    assert entry["source"] == "tasks.xml#SG7-REQ-1"
    assert entry["edge"] == "in-scope"

    # Acceptance refs are their own typed list, and the verification obligation travels with it.
    assert [each["id"] for each in packet["acceptance"]] == ["SG7-AC-1"], packet
    assert packet["acceptance"][0]["kind"] == "acceptance"
    assert packet["acceptance"][0]["edge"] == "acceptance"

    # Out-of-scope refs carry the reason that excludes them — the whole point of the row.
    assert [each["id"] for each in packet["out_of_scope"]] == ["SG7-REQ-9"], packet
    excluded = packet["out_of_scope"][0]
    assert excluded["reason"] == "owned by a later wave", excluded
    assert excluded["version"] == "1.0.0"
    assert excluded["edge"] == "out-of-scope"

    # Resolution, read from the OTHER verb: every named id is a block this corpus really has.
    everything = run_spec("list", str(root))
    assert everything[0] == 0, everything[2]
    known = {line.split("\t")[0] for line in everything[1].splitlines() if line.strip()}
    named = {
        entry["id"]
        for entry in (
            *packet["in_scope"],
            *packet["acceptance"],
            *packet["out_of_scope"],
        )
    }
    assert named <= known, (
        f"the packet names ids the corpus does not have: {sorted(named - known)}"
    )

    # The digest is a function of the packet: same corpus, same digest, no clock, no wall time.
    assert auger.task_packet_digest(packet) == payload["digest"]
    assert len(payload["digest"]) == 64


# ---------------------------------------------------------------- AC2: the negatives
def test_a_task_naming_a_missing_block_is_refused(tmp_path: Path) -> None:
    """AC2: a dangling in-scope id is a typed finding naming the TASK and the BLOCK, no packet."""
    root = write_corpus(tmp_path, in_scope="SG7-REQ-404")
    code, out, err = validate(root)
    assert code == 2, f"a task naming a nonexistent block was accepted: {out!r}"
    assert out == "", "a refusal emitted a partial verdict/packet"
    assert "spec error (unresolved-reference)" in err, err
    assert "SG7-TASK-1" in err, f"the finding does not name the task: {err}"
    assert "SG7-REQ-404" in err, f"the finding does not name the block: {err}"

    # And the packet verb refuses too: a partial packet is worse than no packet.
    code, out, err = run_spec("task-packet", str(root), "--task", "SG7-TASK-1")
    assert code == 2, out
    assert out == ""


def test_multiple_scope_refusals_are_typed(tmp_path: Path) -> None:
    """AC2: a duplicated scope reference, a conflict, and a wrong acceptance type each refuse.

    Three distinct authoring mistakes, three distinct codes: a duplicate is an author copy-paste,
    a conflict says the same block is both in and out of scope, and an acceptance ref pointing at
    a requirement is a type error. Collapsing them into one error would tell the author nothing.
    """
    root = write_corpus(tmp_path, in_scope="SG7-REQ-1 SG7-REQ-1")
    code, _out, err = validate(root)
    assert code == 2, "a duplicated in-scope reference was accepted"
    assert "task-duplicate-reference" in err, err
    assert "SG7-TASK-1" in err and "SG7-REQ-1" in err, err

    root = write_corpus(
        tmp_path / "conflict",
        out_of_scope="SG7-REQ-1 SG7-REQ-9",
        exclusions='<exclusion ref="SG7-REQ-1" reason="dup"/>'
        '<exclusion ref="SG7-REQ-9" reason="later wave"/>',
    )
    code, _out, err = validate(root)
    assert code == 2, "a block both inside and outside the scope was accepted"
    assert "task-scope-conflict" in err, err
    assert "SG7-REQ-1" in err, err

    root = write_corpus(tmp_path / "acceptance", acceptance="SG7-REQ-1")
    code, _out, err = validate(root)
    assert code == 2, "an acceptance ref pointing at a requirement was accepted"
    assert "task-invalid-acceptance-ref" in err, err
    assert "SG7-TASK-1" in err and "SG7-REQ-1" in err, err


def test_an_inactive_reference_needs_acknowledgment(tmp_path: Path) -> None:
    """AC2: an in-scope DEPRECATED block is refused unless the task explicitly acknowledges it.

    Lifecycle is part of the contract: silently pulling a retired block into a task's scope is how
    a worker ends up implementing against superseded text. The acknowledgment is a deliberate,
    inspectable act, and the packet then reports the block's real status instead of hiding it.
    """
    root = write_corpus(
        tmp_path,
        deprecated_requirement=True,
        in_scope="SG7-REQ-1 SG7-REQ-OLD",
    )
    code, _out, err = validate(root)
    assert code == 2, "an unacknowledged deprecated block was accepted into scope"
    assert "inactive-reference-unacknowledged" in err, err
    assert "SG7-TASK-1" in err and "SG7-REQ-OLD" in err, err
    assert "deprecated" in err, f"the finding does not name the lifecycle state: {err}"

    root = write_corpus(
        tmp_path / "acknowledged",
        deprecated_requirement=True,
        in_scope="SG7-REQ-1 SG7-REQ-OLD",
        acknowledges="SG7-REQ-OLD",
    )
    code, _out, err = validate(root)
    assert code == 0, f"an acknowledged deprecated block was still refused: {err}"
    packet = packet_of(root)["packet"]
    old = [entry for entry in packet["in_scope"] if entry["id"] == "SG7-REQ-OLD"]
    assert old and old[0]["status"] == "deprecated", packet
    assert packet["acknowledges"] == ["SG7-REQ-OLD"], packet


def test_a_cross_version_reference_is_refused(tmp_path: Path) -> None:
    """AC2: a scope pinned to one contract version refuses a ref from another version.

    Without the pin a task silently mixes a 1.0.0 requirement with a 2.0.0 acceptance criterion,
    and the "exact mini-spec" claim is false at the one place it matters. Pinned, the mismatch is
    named with both versions so the author can see it.
    """
    root = write_corpus(tmp_path, scope_version="2.0.0")
    code, _out, err = validate(root)
    assert code == 2, "a cross-version scope reference was accepted"
    assert "cross-version-reference" in err, err
    assert "SG7-TASK-1" in err and "SG7-REQ-1" in err, err
    assert "2.0.0" in err and "1.0.0" in err, err

    root = write_corpus(tmp_path / "pinned", scope_version="1.0.0")
    code, _out, err = validate(root)
    assert code == 0, f"a scope pinned to the blocks' own version was refused: {err}"
    assert packet_of(root)["packet"]["scope_version"] == "1.0.0"


def test_an_out_of_scope_entry_without_a_reason_is_refused(tmp_path: Path) -> None:
    """AC2: an excluded id with no `<exclusion reason=...>` is refused, never defaulted.

    "Out of scope" without a reason is indistinguishable from "I forgot", and the reason is the
    only thing that stops the next agent from re-litigating the exclusion. Both shapes are refused:
    no exclusion element at all, and an element carrying an empty reason.
    """
    root = write_corpus(tmp_path, exclusions="")
    code, out, err = validate(root)
    assert code == 2, f"an out-of-scope entry with no reason was accepted: {out!r}"
    assert out == ""
    assert "missing-exclusion-reason" in err, err
    assert "SG7-TASK-1" in err and "SG7-REQ-9" in err, err

    root = write_corpus(
        tmp_path / "empty-reason",
        exclusions='<exclusion ref="SG7-REQ-9" reason=""/>',
    )
    code, _out, err = validate(root)
    assert code == 2, "an exclusion with an empty reason was accepted"
    assert "missing-exclusion-reason" in err, err

    # An exclusion naming a block that is NOT excluded is a contradiction, not a decoration.
    root = write_corpus(
        tmp_path / "stray",
        exclusions='<exclusion ref="SG7-REQ-9" reason="later wave"/>'
        '<exclusion ref="SG7-REQ-1" reason="not actually excluded"/>',
    )
    code, _out, err = validate(root)
    assert code == 2, "an exclusion for a block that is not out of scope was accepted"
    assert "unexpected-exclusion" in err, err
    assert "SG7-REQ-1" in err, err


# ---------------------------------------------------------------- board fixtures (AC3/AC4/AC6)
def boardctl_or_skip() -> str:
    """The real boardctl, or a LOUD skip naming the remedy (a silent skip reads like a pass)."""
    if BOARDCTL is None:
        pytest.skip(
            "boardctl is not installed on this host, so the board a task exports into cannot be "
            "created; install coding-hermes-tools (`boardctl`) and re-run — the AC3/AC4/AC6 "
            "proofs did NOT run here"
        )
    return BOARDCTL


def make_board(base: Path) -> Path:
    """An ephemeral canonical board (tasks.jsonl + events.jsonl) from the real boardctl.

    boardctl decides where the JSONL files live (`<C>/.coding-hermes/board/` for a repo root, the
    directory itself for a bare board dir), so the layout is DISCOVERED rather than assumed —
    asserting one shape would pin a boardctl implementation detail this test does not own.
    """
    boardctl = boardctl_or_skip()
    board = base / "board"
    board.mkdir()
    done = subprocess.run(
        [
            boardctl,
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
    board_files(board)  # fails loudly (with the real tree) when init wrote nothing
    return board


def board_files(board: Path) -> tuple[Path, Path]:
    found = sorted(board.rglob("tasks.jsonl"))
    assert found, (
        f"boardctl init wrote no tasks.jsonl under {board}: {sorted(map(str, board.rglob('*')))}"
    )
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


def export(root: Path, board: Path, *extra: str) -> tuple[int, str, str]:
    return run_spec(
        "task-export", str(root), "--task", "SG7-TASK-1", "--board", str(board), *extra
    )


# ---------------------------------------------------------------- AC3: approved export
def test_an_approved_plan_digest_exports_one_board_row(tmp_path: Path) -> None:
    """AC3: the approved packet becomes ONE canonical row, and the read-back matches byte for byte.

    The row's scope is carried in existing supported fields only (`capability_tags` and
    `reasoning`) — `depends_on` cannot hold spec ids (boardctl resolves it against real task ids
    and refuses the create), so the ids ride the field that exists for machine-readable tags. The
    comparison is against an INDEPENDENTLY produced string: the canonical packet JSON from
    `spec task-packet`, re-serialized here, must appear verbatim in what boardctl shows.
    """
    boardctl_or_skip()
    root = write_corpus(tmp_path)
    board = make_board(tmp_path)
    payload = packet_of(root)

    code, out, err = export(root, board, "--plan-digest", payload["digest"])
    assert code == 0, f"an approved plan digest did not export: {err}"
    result = json.loads(out)
    assert result["status"] == "exported", result
    assert result["row"] == "SG7-TASK-1"
    assert result["digest"] == payload["digest"]

    row = board_row(board, "SG7-TASK-1")
    assert row["id"] == "SG7-TASK-1"
    assert row["capability_tags"] == [
        "in-scope:SG7-REQ-1@1.0.0",
        "acceptance:SG7-AC-1@1.0.0",
        "out-of-scope:SG7-REQ-9@1.0.0",
    ], row["capability_tags"]

    canonical = json.dumps(
        payload["packet"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert canonical in row["reasoning"], (
        "the canonical packet JSON is not byte-identical inside the canonical row:\n"
        f"{row['reasoning']}"
    )
    assert payload["trace"] in row["reasoning"], row["reasoning"]
    # What boardctl SHOWS is exactly what the exporter reported reading back.
    assert row["reasoning"] == result["readback"]["reasoning"]
    assert row["capability_tags"] == result["readback"]["capability_tags"]

    # The version of every named block is in the row, so a later reader needs no second lookup.
    assert "@1.0.0" in row["capability_tags"][0]
    assert rows_with_id(board, "SG7-TASK-1") == 1

    # A repeated export of the same approved plan is idempotent: one row, and no second create.
    code, out, err = export(root, board, "--plan-digest", payload["digest"])
    assert code == 0, err
    assert json.loads(out)["status"] == "unchanged", out
    assert rows_with_id(board, "SG7-TASK-1") == 1


# ---------------------------------------------------------------- AC4: refused export
def test_an_unapproved_or_stale_digest_leaves_the_board_untouched(
    tmp_path: Path,
) -> None:
    """AC4: with no approval, or an approval that no longer matches, boardctl is never invoked.

    Proved three ways, because "we did not write" is easy to claim and hard to see: both canonical
    JSONL files hash identically before and after, the export exits non-zero with a typed code,
    and a boardctl WRAPPER placed on the exporter's `--boardctl` seam records no invocation at all
    (a refused export that still shells out could create a row and fail later).
    """
    boardctl = boardctl_or_skip()
    root = write_corpus(tmp_path)
    board = make_board(tmp_path)

    wrapper = tmp_path / "recording-boardctl"
    log = tmp_path / "boardctl-invocations.log"
    wrapper.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexec {boardctl} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    tasks, events = board_files(board)
    before = (digest_of(tasks), digest_of(events))

    # (a) no approval anywhere: not in the block, not on the command line.
    code, out, err = export(root, board, "--boardctl", str(wrapper))
    assert code == 2, f"an UNapproved plan exported: {out!r}"
    assert "unapproved-plan-digest" in err, err
    assert out == "", "a refused export printed a result"

    # (b) a digest that is well formed but no longer matches the plan (stale).
    stale = "0" * 64
    code, out, err = export(
        root, board, "--plan-digest", stale, "--boardctl", str(wrapper)
    )
    assert code == 2, f"a STALE plan digest exported: {out!r}"
    assert "stale-plan-digest" in err, err
    assert stale in err, f"the finding does not name the approved digest: {err}"

    # (c) a malformed approval is its own refusal, not a silent mismatch.
    code, out, err = export(
        root, board, "--plan-digest", "not-a-digest", "--boardctl", str(wrapper)
    )
    assert code == 2, out
    assert "invalid-plan-digest" in err, err

    after = (digest_of(tasks), digest_of(events))
    assert after == before, (
        "a REFUSED export changed a canonical board file: "
        f"tasks {before[0][:12]}->{after[0][:12]}, events {before[1][:12]}->{after[1][:12]}"
    )
    assert not log.exists(), (
        "boardctl was invoked by a refused export:\n" + log.read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------- AC5: the trace marker
def test_the_generated_trace_marker_resolves(tmp_path: Path) -> None:
    """AC5: row/spec resolve to the canonical task and its exact XML ids; absent fields are `none:`.

    A generated marker that names a doc heading instead of a block would be the paperwork failure
    the PRD's risk list names, so the `spec=` value is the block's own `file.xml#ID` source and it
    is resolved back to that block id here. `evidence`/`verdict`/`commit` are ABSENT at export
    time and must say why — a bare `none` is refused.
    """
    root = write_corpus(tmp_path)
    payload = packet_of(root)
    marker = payload["trace"]

    assert marker.startswith("ch:trace "), marker
    fields = auger.parse_trace_marker(marker)
    assert fields["row"] == "SG7-TASK-1", fields
    assert fields["spec"] == "tasks.xml#SG7-TASK-1", fields
    assert fields["test"] == f"{TRACKED_TEST}::{TRACKED_SELECTOR}", fields
    for absent in ("evidence", "verdict", "commit"):
        assert fields[absent].startswith("none:") and len(fields[absent]) > 5, fields[
            absent
        ]
    assert " " not in fields["evidence"], "an absent-field reason must be one token"

    resolved = auger.resolve_trace_marker(marker, spec_blocks(root)[2])
    assert resolved["row"] == "SG7-TASK-1", resolved
    assert resolved["blocks"] == ["SG7-TASK-1"], resolved
    assert resolved["missing"] == [], resolved

    # The marker's row is the CANONICAL task id and its spec names the exact XML ids of the scope.
    assert {entry["id"] for entry in resolved["scope"]} == {
        "SG7-REQ-1",
        "SG7-AC-1",
        "SG7-REQ-9",
    }, resolved["scope"]
    for entry in resolved["scope"]:
        assert entry["source"].endswith("#" + entry["id"]), entry

    # A bare `none` (no reason) and an unknown field are refused by the parser, not tolerated.
    with pytest.raises(auger.SpecError) as bare:
        auger.parse_trace_marker(marker.replace("none:not-judged", "none", 1))
    assert bare.value.code == "unexplained-absent-field", bare.value
    with pytest.raises(auger.SpecError) as unknown:
        auger.parse_trace_marker(marker + " nope=1")
    assert unknown.value.code == "unknown-trace-field", unknown.value

    # The trace contract this marker obeys is WRITTEN DOWN where the doctrine lives.
    prd = PRD.read_text(encoding="utf-8")
    assert "spec task-packet" in prd, (
        "the PRD does not document the generated task marker"
    )
    assert "none:<reason>" in prd


# ---------------------------------------------------------------- AC6: the closed surface
def test_the_exporter_only_invokes_boardctl_create_and_readback(tmp_path: Path) -> None:
    """AC6: the exporter runs boardctl `create`/`show` and nothing else — no dispatcher client.

    Proved from OUTSIDE: a wrapper on the `--boardctl` seam records every argv, and the operations
    it saw must be a subset of {create, show}. Proved from INSIDE: the exporter's own source names
    no HTTP client and no scheduler/API mutation path, so there is no second write surface to
    disable later.
    """
    boardctl = boardctl_or_skip()
    root = write_corpus(tmp_path)
    board = make_board(tmp_path)
    payload = packet_of(root)

    wrapper = tmp_path / "recording-boardctl"
    log = tmp_path / "boardctl-invocations.log"
    wrapper.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexec {boardctl} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    code, out, err = export(
        root, board, "--plan-digest", payload["digest"], "--boardctl", str(wrapper)
    )
    assert code == 0, err
    invocations = [
        line.split() for line in log.read_text(encoding="utf-8").splitlines() if line
    ]
    assert invocations, "the wrapper recorded no boardctl call at all"
    operations = {
        token
        for argv in invocations
        for token in argv
        if token in {"create", "show", "update", "event", "sweep-status", "dispatch"}
    }
    assert operations == {"create", "show"}, (
        f"the exporter invoked unexpected boardctl commands: {sorted(operations)}"
    )

    source = inspect.getsource(auger.export_task_packet) + inspect.getsource(
        auger._boardctl
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
            f"the exporter source names {forbidden!r}: a task export must not dispatch or "
            "mutate the scheduler"
        )


# ---------------------------------------------------------------- the gate arm
def test_gate_arm_runs_these_files() -> None:
    """This proof file must be in tests/gate.sh's pytest arm, or it is never executed.

    The guard runs exactly `bash tests/gate.sh`, so a proof file outside the arm is unrun in CI and
    in the guard while still looking like coverage. `tests/test_spec_context.py` asserts BOTH SG7
    files, so dropping either name from the arm fails a test in the shipped tree — prove it BITES
    by removing a name from the arm (the evidence transcript keeps that pair).
    """
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    for required in ("tests/test_xml_task_contract.py",):
        assert required in arm[0], (
            f"{required} must be in tests/gate.sh's pytest arm — the guard's test command is the "
            "gate, so a proof file outside it is never run"
        )
