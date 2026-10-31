from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import auger

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "xml"


def test_assembles_migration_obligation_with_repo_harness_metadata():
    bundle = auger.assemble_xml_requirement(FIXTURES / "specs", "REQ-MIG-1")
    obligation = bundle["verifications"][0]
    assert obligation == {
        "id": "TEST-MIG-1",
        "type": "test",
        "path": "tests/test_xml_specs.py",
        "selector": "test_migration_obligation_applies_named_file_and_detects_noop",
        "runner": [sys.executable, "-m", "pytest"],
        "fixture": "tests/fixtures/xml/prior_schema.sql",
        "migration": "tests/fixtures/xml/migrations/001_add_marker.sql",
        "assertions": "marker table exists; marker.version equals 1",
    }
    assert "shell" not in str(obligation).lower()


@pytest.mark.parametrize(
    ("xml", "error"),
    [
        (
            '<requirement id="TEST-MIG-1" verifies="TEST-MIG-1"/><test id="TEST-MIG-1"/>',
            "duplicate-id",
        ),
        ('<requirement id="REQ-MIG-1" verifies="MISSING"/>', "dangling-test-ref"),
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/><verification id="TEST-MIG-1" type="test" runner="pytest" path="tests/test_xml_specs.py" selector="not_a_real_test"/>',
            "stale-test-ref",
        ),
        (
            '<requirement id="REQ-MIG-1" verifies="TEST-MIG-1"/><verification id="TEST-MIG-1" type="test" runner="bash -c" path="tests/test_xml_specs.py" selector="test_migration_obligation_applies_named_file_and_detects_noop"/>',
            "invalid-test-ref",
        ),
    ],
)
def test_rejects_duplicate_dangling_stale_and_unregistered_test_refs(
    tmp_path, xml, error
):
    root = tmp_path / "specs"
    root.mkdir()
    (root / "bad.xml").write_text(f'<spec id="bad">{xml}</spec>', encoding="utf-8")
    with pytest.raises(auger.SpecValidationError, match=error):
        auger.validate_xml_specs(root)


def test_migration_obligation_applies_named_file_and_detects_noop():
    obligation = auger.assemble_xml_requirement(FIXTURES / "specs", "REQ-MIG-1")[
        "verifications"
    ][0]
    prior = (REPO / obligation["fixture"]).read_text(encoding="utf-8")
    migration_path = REPO / obligation["migration"]
    migration = migration_path.read_text(encoding="utf-8")
    assert "CREATE TABLE marker" in migration

    database = sqlite3.connect(":memory:")
    database.executescript(prior)
    database.executescript(migration)

    def assert_migration_result(connection):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(marker)")}
        assert "version" in columns
        assert connection.execute("SELECT version FROM marker").fetchone() == (1,)

    assert_migration_result(database)

    no_op_database = sqlite3.connect(":memory:")
    no_op_database.executescript(prior)
    no_op_database.executescript("")
    with pytest.raises(AssertionError):
        assert_migration_result(no_op_database)


def test_serialized_runner_is_argument_vector_not_shell_command():
    argv = auger.assemble_xml_requirement(FIXTURES / "specs", "REQ-MIG-1")[
        "verifications"
    ][0]["runner"]
    assert isinstance(argv, list)
    assert argv == [sys.executable, "-m", "pytest"]
