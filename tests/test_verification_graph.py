"""Literal AUG-123 verification-obligation acceptance proofs.

ch:trace row=AUG-123 spec=docs/prd/xml-spec-graph.md#R15 test=tests/test_verification_graph.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

VERIFICATION_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml" / "verification"


def _write_spec(root: Path, blocks: str) -> Path:
    spec_root = root / "specs"
    spec_root.mkdir(parents=True)
    (spec_root / "graph.xml").write_text(f"<spec>{blocks}</spec>", encoding="utf-8")
    return spec_root


def test_invalid_test_refs_fail_closed(tmp_path: Path) -> None:
    """Duplicate, dangling, stale, and untracked refs fail before bundle assembly."""
    # The untracked case needs a file that EXISTS under the repository root but is
    # not in git. A build artifact (auger.egg-info/PKG-INFO) only exists on a box
    # with an editable install, so the check passed locally and could never pass on
    # a clean checkout: make the probe file ourselves.
    untracked = REPO_ROOT / f".untracked-test-ref-probe-{os.getpid()}.txt"
    untracked.write_text("probe\n", encoding="utf-8")
    try:
        assert untracked.is_file()
        _assert_invalid_refs_fail_closed(
            tmp_path, untracked.relative_to(REPO_ROOT).as_posix()
        )
    finally:
        untracked.unlink(missing_ok=True)


def _assert_invalid_refs_fail_closed(tmp_path: Path, untracked_test_path: str) -> None:
    cases = [
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/>'
            '<test id="TEST-MIG-1"/><test id="TEST-MIG-1"/>',
            "duplicate-id",
            "REQ-MIG-1",
            "TEST-MIG-1",
        ),
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MISSING-1"/>',
            "dangling-test-ref",
            "REQ-MIG-1",
            "TEST-MISSING-1",
        ),
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/>'
            '<test id="TEST-MIG-1" runner="pytest" '
            'path="tests/test_xml_specs.py" selector="test_removed_version"/>',
            "stale-test-ref",
            "REQ-MIG-1",
            "TEST-MIG-1",
        ),
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/>'
            f'<test id="TEST-MIG-1" runner="pytest" path="{untracked_test_path}" selector="ref"/>',
            "untracked-test-ref",
            "REQ-MIG-1",
            "TEST-MIG-1",
        ),
    ]

    for blocks, error, requirement_id, test_id in cases:
        spec_root = _write_spec(tmp_path / error, blocks)
        with pytest.raises(auger.SpecValidationError) as caught:
            auger.validate_xml_specs(spec_root)
        assert error in str(caught.value)
        if error not in {"duplicate-id", "stale-test-ref", "untracked-test-ref"}:
            assert requirement_id in str(caught.value)
        assert test_id in str(caught.value)
        with pytest.raises(auger.SpecValidationError) as assembled:
            auger.assemble_xml_requirement(spec_root, requirement_id)
        assert test_id in str(assembled.value)


def test_xml_cannot_execute_arbitrary_shell(tmp_path: Path) -> None:
    """Runner names resolve to an argv vector; XML shell text is rejected."""
    obligation = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")[
        "verifications"
    ][0]
    assert obligation["runner"] == [sys.executable, "-m", "pytest"]
    assert all(isinstance(argument, str) for argument in obligation["runner"])

    shell_root = _write_spec(
        tmp_path,
        '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/>'
        '<test id="TEST-MIG-1" runner="bash -c" '
        'path="tests/test_verification_graph.py" '
        'selector="test_xml_cannot_execute_arbitrary_shell"/>',
    )
    with pytest.raises(auger.SpecValidationError, match="invalid-test-ref"):
        auger.validate_xml_specs(shell_root)


def test_migration_obligation_applies_named_file(tmp_path: Path) -> None:
    """The linked migration changes the prior schema; replacing it with no-op fails assertions."""
    obligation = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")[
        "verifications"
    ][0]
    prior = (REPO_ROOT / obligation["fixture"]).read_text(encoding="utf-8")
    migration_path = REPO_ROOT / obligation["migration"]
    migration = migration_path.read_text(encoding="utf-8")
    assert (
        migration_path
        == REPO_ROOT / "tests/fixtures/xml/verification/migrations/001_add_marker.sql"
    )
    assert "CREATE TABLE marker" in migration

    def assert_migration_result(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(marker)")}
        assert "version" in columns
        assert connection.execute("SELECT version FROM marker").fetchone() == (1,)

    database = sqlite3.connect(":memory:")
    database.executescript(prior)
    database.executescript(migration)
    assert_migration_result(database)

    no_op_database = sqlite3.connect(":memory:")
    no_op_database.executescript(prior)
    no_op_database.executescript("")
    with pytest.raises(AssertionError):
        assert_migration_result(no_op_database)


def test_method_test_is_an_obligation_without_a_type_attribute(tmp_path: Path) -> None:
    """`method="test"` marks a test obligation exactly like `type="test"` (AUG-128).

    The obligation gate keyed on `type` alone, and the shipped migration fixture writes
    `method="test"` — so the shape the docs and fixtures actually use had its path,
    selector, migration, fixture and assertions checked by nothing. Measured before the
    fix: pointing such a node at a file that does not exist returned "ok" and exit 0.
    """
    spec_root = _write_spec(
        tmp_path,
        '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/>'
        '<verification id="TEST-MIG-1" verifies="REQ-MIG-1" method="test"'
        ' runner="pytest" selector="test_whatever"'
        ' path="tests/does-not-exist.py"/>',
    )
    with pytest.raises(auger.SpecValidationError) as caught:
        auger.validate_xml_specs(spec_root)
    assert "stale-test-ref" in str(caught.value)
    assert "TEST-MIG-1" in str(caught.value)


def test_non_test_methods_are_not_forced_through_test_checks(tmp_path: Path) -> None:
    """A manual/analysis obligation must not be rejected for lacking a runner or path."""
    spec_root = _write_spec(
        tmp_path,
        '<requirement id="REQ-MAN-1" verifies="MAN-1"/>'
        '<verification id="MAN-1" verifies="REQ-MAN-1" method="manual"/>',
    )
    nodes = auger.validate_xml_specs(spec_root)
    assert "MAN-1" in nodes


def test_gate_runs_the_xml_proof_suites() -> None:
    """These proofs are only proofs if the guard/CI actually runs them (AUG-123 AC-17).

    They were absent from tests/gate.sh's pytest arm until 2026-11-03, so a red
    test_usage_skill.py sat on main while CI reported success.
    """
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text(encoding="utf-8")
    arm = [line for line in gate.splitlines() if "python3 -m pytest" in line]
    assert arm, "tests/gate.sh must have a pytest arm"
    for required in (
        "tests/test_verification_graph.py",
        "tests/test_xml_specs.py",
        "tests/test_usage_skill.py",
    ):
        assert required in arm[0], (
            f"{required} must be in tests/gate.sh's pytest arm — the guard's test "
            "command is the gate, so a suite outside it is never enforced"
        )
