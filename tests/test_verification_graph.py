"""Literal AUG-123 verification-obligation acceptance proofs.

ch:trace row=AUG-123 spec=docs/prd/xml-spec-graph.md#R15 test=tests/test_verification_graph.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

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
            '<test id="TEST-MIG-1" runner="pytest" path="auger.egg-info/PKG-INFO" selector="ref"/>',
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
