"""AUG-156 / goal A12: board closure evidence must be MACHINE-resolvable, not prose.

Goal-level acceptance A12 says every closed board row carries a resolvable evidence run id
(``.gitreins/history/<date>/<run-id>/verdict.json``) and no fabricated ids, ever. Measured
2026-11-07 that claim was prose: 0 of 201 complete rows carried a machine field, 6 named a verdict
belonging to a different row, 160 named nothing, and one row wore the fabricated label
``run:local-auger-RELEASE-018-2026-10-30`` that resolves to no artifact.

``scripts/audit_board_evidence.py`` is the falsifier and this file is what keeps it honest:

  the pre-fix shape fails ................. test_a_board_with_no_evidence_bindings_fails
  a legacy prose citation still binds ..... test_a_legacy_prose_citation_binds_or_fails
  a field binding passes .................. test_a_bound_and_annotated_board_passes
  a foreign task's verdict is refused ..... test_a_verdict_for_another_task_is_refused
  a dangling run id is refused ............ test_a_dangling_run_id_is_refused
  an invented absence is refused .......... test_a_false_none_reason_is_refused
  an unknown absence is refused ........... test_an_unknown_none_reason_is_refused
  an unaccounted foreign citation refuses . test_an_unaccounted_foreign_citation_is_refused
  a self-declared label is a defect ....... test_a_self_declared_run_label_is_a_defect
  a missing board is a typed refusal ...... test_a_missing_board_is_a_typed_refusal
  the COMMITTED board audits green ........ test_the_committed_board_audits_clean
  every binding resolves to its own task .. test_every_bound_row_resolves_to_its_own_task
  no fabricated label survives on the board  test_no_complete_row_carries_a_fabricated_label
  the gate arm runs this file ............. test_gate_arm_runs_this_file

The live tests read the TRACKED board and the TRACKED ``.gitreins/history`` artifacts, so they are
deterministic from a clean checkout — no substrate, no network, no gitreins binary.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "audit_board_evidence.py"
BOARD = REPO_ROOT / ".coding-hermes" / "board" / "tasks.jsonl"
HISTORY = REPO_ROOT / ".gitreins" / "history"
GATE = REPO_ROOT / "tests" / "gate.sh"


def run_audit(board: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--board", str(board), *extra],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )


def canonical_line(stdout: str) -> str:
    return [line for line in stdout.splitlines() if line.startswith("complete_rows=")][
        0
    ]


def write_fixture(
    root: Path, rows: list[dict], verdicts: dict[str, dict]
) -> tuple[Path, Path]:
    """A throwaway board + history pair OUTSIDE the repo, in the real on-disk shape."""
    board_dir = root / ".coding-hermes" / "board"
    board_dir.mkdir(parents=True, exist_ok=True)
    board = board_dir / "tasks.jsonl"
    board.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    history = root / ".gitreins" / "history"
    history.mkdir(parents=True, exist_ok=True)
    for (date, run_id), verdict in verdicts.items():
        target = history / date / run_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    return board, history


def row(row_id: str, **extra: object) -> dict:
    base = {"id": row_id, "status": "complete", "completed_at": "2026-10-01T00:00:00Z"}
    base.update(extra)
    return base


def test_a_board_with_no_evidence_bindings_fails(tmp_path: Path) -> None:
    """The measured pre-fix shape: complete rows, no field, no citation, no artifact."""
    board, _ = write_fixture(
        tmp_path,
        [row("AUG-001"), row("AUG-002"), row("AUG-003", status="pending")],
        {},
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout + result.stderr
    assert canonical_line(result.stdout) == (
        "complete_rows=2 bound=0 unresolvable=0 missing=2"
    ), result.stdout
    assert "RESULT: FAIL" in result.stdout


def test_a_legacy_prose_citation_binds_or_fails(tmp_path: Path) -> None:
    """A row without the field is judged by the run ids its own text cites (pre-fix measurement)."""
    board, _ = write_fixture(
        tmp_path,
        [
            row(
                "AUG-001",
                foreman_note="closed on .gitreins/history/2026-09-22/aaaaaaaa",
            ),
            row("AUG-002", foreman_note="verdict bbbbbbbb (2026-09-23) passed"),
            row("AUG-003"),
        ],
        {
            ("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-001", "passed": True},
            ("2026-09-23", "bbbbbbbb"): {"task_id": "AUG-999", "passed": True},
        },
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert canonical_line(result.stdout) == (
        "complete_rows=3 bound=1 unresolvable=1 missing=1"
    ), result.stdout
    assert "UNRESOLVABLE AUG-002 foreign-citation" in result.stdout
    assert "MISSING AUG-003" in result.stdout


def test_a_bound_and_annotated_board_passes(tmp_path: Path) -> None:
    board, _ = write_fixture(
        tmp_path,
        [
            row("AUG-001", evidence_run_id="aaaaaaaa"),
            row("AUG-002", evidence_run_id="none:no-verdict-artifact-for-task-id"),
            row("AUG-003", status="pending"),
        ],
        {("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-001", "passed": True}},
    )
    result = run_audit(board)
    assert result.returncode == 0, result.stdout + result.stderr
    assert canonical_line(result.stdout) == (
        "complete_rows=2 bound=1 unresolvable=0 missing=0"
    ), result.stdout
    assert "none_reason=1" in result.stdout
    assert "sum=bound+none_reason+unresolvable=2 complete_rows=2" in result.stdout
    assert "RESULT: OK" in result.stdout


def test_a_verdict_for_another_task_is_refused(tmp_path: Path) -> None:
    """A12's core rule: a verdict for a different task is not this row's evidence."""
    board, _ = write_fixture(
        tmp_path,
        [row("AUG-001", evidence_run_id="aaaaaaaa")],
        {("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-999", "passed": True}},
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert canonical_line(result.stdout) == (
        "complete_rows=1 bound=0 unresolvable=1 missing=0"
    ), result.stdout
    assert "foreign-task" in result.stdout and "AUG-999" in result.stdout


def test_a_dangling_run_id_is_refused(tmp_path: Path) -> None:
    board, _ = write_fixture(tmp_path, [row("AUG-001", evidence_run_id="deadbeef")], {})
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert "unknown-run" in result.stdout


def test_a_false_none_reason_is_refused(tmp_path: Path) -> None:
    """An annotation cannot be invented to make the audit green: its predicate is re-checked."""
    board, _ = write_fixture(
        tmp_path,
        [row("AUG-001", evidence_run_id="none:no-verdict-artifact-for-task-id")],
        {("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-001", "passed": True}},
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert "false-none-reason" in result.stdout
    assert "aaaaaaaa" in result.stdout


def test_an_unknown_none_reason_is_refused(tmp_path: Path) -> None:
    board, _ = write_fixture(
        tmp_path, [row("AUG-001", evidence_run_id="none:felt-like-it")], {}
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert "false-none-reason" in result.stdout
    assert "unknown absence reason" in result.stdout


def test_the_pre_window_reason_is_checked_against_the_row_date(tmp_path: Path) -> None:
    board, _ = write_fixture(
        tmp_path,
        [
            row("AUG-001", completed_at="2026-09-01T00:00:00Z"),
            row("AUG-002", completed_at="2026-10-01T00:00:00Z"),
        ],
        {("2026-09-21", "aaaaaaaa"): {"task_id": "AUG-999", "passed": True}},
    )
    rows = [json.loads(line) for line in board.read_text().splitlines() if line.strip()]
    rows[0]["evidence_run_id"] = "none:pre-gitreins-history-window"
    rows[1]["evidence_run_id"] = "none:pre-gitreins-history-window"
    board.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    # AUG-001 predates the artifact window (true); AUG-002 does not (false).
    assert "UNRESOLVABLE AUG-002" in result.stdout
    assert "UNRESOLVABLE AUG-001" not in result.stdout


def test_an_unaccounted_foreign_citation_is_refused(tmp_path: Path) -> None:
    """A row that names another task's verdict must name it in its absence reason — all of them."""
    verdicts = {
        ("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-900", "passed": True},
        ("2026-09-23", "bbbbbbbb"): {"task_id": "AUG-901", "passed": True},
    }
    board, _ = write_fixture(
        tmp_path,
        [
            row(
                "AUG-001",
                foreman_note="see .gitreins/history/2026-09-22/aaaaaaaa and bbbbbbbb",
                evidence_run_id="none:no-verdict-artifact-for-task-id",
            )
        ],
        verdicts,
    )
    refused = run_audit(board)
    assert refused.returncode == 1, refused.stdout
    assert "false-none-reason" in refused.stdout
    assert "absence reason must name them" in refused.stdout

    # naming only one of the two citations is still refused
    rows = [json.loads(line) for line in board.read_text().splitlines() if line.strip()]
    rows[0]["evidence_run_id"] = "none:cites-foreign-verdict-aaaaaaaa-task-AUG-900"
    board.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    partial = run_audit(board)
    assert partial.returncode == 1, partial.stdout
    assert "unaccounted" in partial.stdout

    # naming both is accepted
    rows[0]["evidence_run_id"] = (
        "none:cites-foreign-verdict-aaaaaaaa-task-AUG-900+bbbbbbbb-task-AUG-901"
    )
    board.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    accepted = run_audit(board)
    assert accepted.returncode == 0, accepted.stdout
    assert canonical_line(accepted.stdout) == (
        "complete_rows=1 bound=0 unresolvable=0 missing=0"
    ), accepted.stdout


def test_a_self_declared_run_label_is_a_defect(tmp_path: Path) -> None:
    """AC2: `run:<anything>` that resolves to no verdict file is a fabricated label, not evidence."""
    board, _ = write_fixture(
        tmp_path,
        [
            row(
                "AUG-001",
                evidence_run_id="none:no-verdict-artifact-for-task-id",
                detail={"evidence": [{"run_id": "run:local-auger-AUG-001-2026-10-30"}]},
            ),
            row(
                "AUG-002",
                evidence_run_id="none:no-verdict-artifact-for-task-id",
                detail={"evidence": [{"run_id": "none:environment-only"}]},
            ),
        ],
        {},
    )
    result = run_audit(board)
    assert result.returncode == 1, result.stdout
    assert "label_defects=1" in result.stdout
    assert "run:local-auger-AUG-001-2026-10-30" in result.stdout
    # an explicit `none:` absence inside detail.evidence is not a claimed run
    assert "AUG-002" not in [
        line.split()[1]
        for line in result.stdout.splitlines()
        if line.startswith("LABEL")
    ]


def test_a_missing_board_is_a_typed_refusal(tmp_path: Path) -> None:
    (tmp_path / ".gitreins" / "history").mkdir(parents=True, exist_ok=True)
    result = run_audit(tmp_path / "nope" / "tasks.jsonl")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "audit error (board-missing)" in result.stderr


def test_json_mode_reports_the_same_counters(tmp_path: Path) -> None:
    board, _ = write_fixture(
        tmp_path,
        [row("AUG-001", evidence_run_id="aaaaaaaa")],
        {("2026-09-22", "aaaaaaaa"): {"task_id": "AUG-001", "passed": True}},
    )
    result = run_audit(board, "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["complete_rows"] == 1
    assert (
        payload["bound"],
        payload["none_reason"],
        payload["unresolvable"],
        payload["missing"],
    ) == (
        1,
        0,
        0,
        0,
    )
    assert payload["decisions"][0]["run_id"] == "aaaaaaaa"


# ---------------------------------------------------------------------------------------------
# The live surface: the TRACKED board must audit clean, and the bindings must hold without the
# audit agreeing with itself (each bound id is re-resolved here by hand).
# ---------------------------------------------------------------------------------------------


def verdict_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in sorted(HISTORY.glob("*/*/verdict.json")):
        index[path.parent.name] = {"date": path.parent.parent.name, "path": path}
    return index


def test_the_committed_board_audits_clean() -> None:
    result = run_audit(BOARD)
    assert result.returncode == 0, result.stdout + result.stderr
    line = canonical_line(result.stdout)
    counters = dict(part.split("=") for part in line.split())
    assert counters["unresolvable"] == "0", line
    assert counters["missing"] == "0", line
    assert int(counters["bound"]) > 0, line
    assert "RESULT: OK" in result.stdout


def test_every_bound_row_resolves_to_its_own_task() -> None:
    """Re-resolve every field binding by hand: the audit's verdict must not be self-certifying."""
    index = verdict_index()
    checked = 0
    for line in BOARD.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        board_row = json.loads(line)
        if board_row.get("status") != "complete":
            continue
        value = board_row.get("evidence_run_id")
        assert isinstance(value, str) and value.strip(), (
            f"complete row {board_row['id']} carries no machine-resolvable evidence_run_id"
        )
        if value.startswith("none:"):
            continue
        assert value in index, (
            f"{board_row['id']} cites run {value} with no verdict artifact"
        )
        verdict = json.loads((index[value]["path"]).read_text(encoding="utf-8"))
        assert verdict.get("task_id") == board_row["id"], (
            f"{board_row['id']} cites verdict {value} for task {verdict.get('task_id')}"
        )
        checked += 1
    assert checked > 0, "no row binds to a verdict artifact — the backfill did not land"


def test_no_complete_row_carries_a_fabricated_label() -> None:
    index = verdict_index()
    offenders = []
    for line in BOARD.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        board_row = json.loads(line)
        if board_row.get("status") != "complete":
            continue
        detail = board_row.get("detail")
        entries = detail.get("evidence") if isinstance(detail, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            value = entry.get("run_id")
            if value is None or (isinstance(value, str) and value.startswith("none:")):
                continue
            if value in index and not value.startswith("run:"):
                continue
            offenders.append((board_row["id"], value))
    assert not offenders, (
        f"complete rows still claim unresolvable run labels: {offenders}"
    )


def test_the_release_row_that_wore_a_fabricated_label_is_annotated() -> None:
    """AC2 names this row: its `run:local-auger-RELEASE-018-2026-10-30` claimed an artifact that never existed."""
    rows = {
        json.loads(line)["id"]: json.loads(line)
        for line in BOARD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    release = rows["RELEASE-018"]
    assert release["status"] == "complete"
    assert release["evidence_run_id"].startswith("none:"), release["evidence_run_id"]
    labels = [
        entry.get("run_id")
        for entry in (release.get("detail") or {}).get("evidence", [])
        if isinstance(entry, dict)
    ]
    assert "run:local-auger-RELEASE-018-2026-10-30" not in labels, labels


def test_gate_arm_runs_this_file() -> None:
    """A proof outside gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_board_evidence_audit.py" in arm[0], (
        "tests/test_board_evidence_audit.py is not in the gate's pytest arm — the evidence "
        "invariant would be an unrun proof"
    )
    named = [token for token in arm[0].split() if token.startswith("tests/test_")]
    missing = [name for name in named if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"tests/gate.sh's pytest arm names files that do not exist: {missing}"
    )
