"""XML specification authoring, selection and assembly — AUG-118.

These tests are HERMETIC: no DuckBrain, no JEV, no network, no clock. They read the checked-in
corpus under tests/fixtures/xml/ and drive `auger spec ...` in-process, so they run in both the
live gate and hosted CI (tests/gate.sh names this file in its pytest arm).

What each acceptance criterion is proven by:

  AC-1  versioned schema + stable IDs + typed attributes .... test_schema_matches_the_validator
  AC-2  selection by ID/attributes, >=2 files, deterministic . test_shared_block_reference,
                                                              test_selects_only_requested_blocks_
                                                              and_their_dependencies,
                                                              test_selection_is_byte_identical
  AC-3  refusals: unresolved ref, duplicate ID, cycle,
        unsupported schema version, bad verification
        metadata, no partial document emitted .............. test_refusals_are_typed_and_emit_no
                                                              _document, test_empty_selector_refused
  AC-4  Markdown export golden unchanged; XML selection
        emits requested blocks + explicit dependencies ..... test_markdown_export_is_unchanged,
                                                              test_selection_matches_the_golden
  AC-5  spec doc records rationale/alternatives/verification
        + ch:trace row=AUG-118; the gate runs this file ... test_spec_doc_records_the_decision,
                                                              test_gate_runs_these_tests

ch:trace row=AUG-118 spec=docs/SPEC-003-xml-specs.md#verification
         test=tests/test_xml_specs.py doc=docs/SPEC-003-xml-specs.md
         evidence=tests/fixtures/xml/expected
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import sqlite3
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
SPECS = FIXTURES / "specs"
VERIFICATION_FIXTURES = FIXTURES / "verification"
INVALID = FIXTURES / "invalid"
EXPECTED = FIXTURES / "expected"
SCHEMA = REPO_ROOT / "specs" / "schema" / "auger-spec-1.0.xsd"
SPEC_DOC = REPO_ROOT / "docs" / "SPEC-003-xml-specs.md"
GATE = REPO_ROOT / "tests" / "gate.sh"

XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

#: The export golden's row set. `render_export` reads through `select`/`graph_edges`; patching
#: exactly those two seams makes the golden hermetic. The golden file was captured from the BASE
#: commit (86764e7, before this feature) and re-verified identical against that revision, so a
#: drift here means the export contract changed — which AUG-118 forbids.
EXPORT_ROWS = {
    "project": [
        {
            "id": "P-000001",
            "name": "golden-project",
            "seed": "one line of seed text",
            "core_statement": "the thing this project is about",
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
        }
    ],
    "decision": [
        {
            "id": "D-001",
            "project_id": "P-000001",
            "domain": "4.01",
            "question_id": "Q-000001",
            "chosen": "ship the additive feature",
            "why_not": '{"O2": "breaks the export contract"}',
            "reversal_cost": "cheap",
            "confidence": 0.8,
            "status": "answered",
            "evidence_key": "/P-000001/decision/D-001",
            "scope": "",
        },
        {
            "id": "D-002",
            "project_id": "P-000001",
            "domain": "4.02",
            "question_id": "",
            "chosen": "keep Markdown untouched",
            "why_not": "",
            "reversal_cost": None,
            "confidence": 0.55,
            "status": "answered",
            "evidence_key": "",
            "scope": "bundle",
        },
    ],
    "option": [
        {
            "id": "D-001-O1",
            "decision_id": "D-001",
            "label": "additive",
            "costs": "one new module",
            "breaks": "nothing",
            "active": True,
        },
        {
            "id": "D-001-O2",
            "decision_id": "D-001",
            "label": "replace",
            "costs": "migration",
            "breaks": "every existing consumer",
            "active": False,
        },
    ],
    "unknown": [
        {
            "id": "U-001",
            "project_id": "P-000001",
            "text": "does the substrate page at scale?",
            "owner": "unassigned",
            "trigger": "first 10k rows",
            "containment": "page the reads",
        }
    ],
}


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def assembled_ids(text: str) -> list[str]:
    """The block IDs an assembled Markdown document names, in output order."""
    return [
        line[3:].split(" · ", 1)[0]
        for line in text.splitlines()
        if line.startswith("## ")
    ]


# ---------------------------------------------------------------- AC-1: the schema
def xsd_type(name: str):
    root = ET.parse(SCHEMA).getroot()
    for node in root.iter(f"{XSD_NS}simpleType"):
        if node.get("name") == name:
            return node
    raise AssertionError(f"schema has no simpleType {name!r}")


def xsd_enum(name: str) -> list[str]:
    restriction = xsd_type(name).find(f"{XSD_NS}restriction")
    return [node.get("value") for node in restriction.iter(f"{XSD_NS}enumeration")]


def xsd_pattern(name: str) -> str:
    restriction = xsd_type(name).find(f"{XSD_NS}restriction")
    return restriction.find(f"{XSD_NS}pattern").get("value")


def test_schema_matches_the_validator() -> None:
    """AC-1: one versioned grammar, and the code enforces exactly what the XSD declares."""
    assert SCHEMA.is_file(), f"missing versioned schema: {SCHEMA}"
    root = ET.parse(SCHEMA).getroot()

    # Versioned: the schema carries its own version AND targets the document namespace.
    assert root.get("version") == auger.SPEC_SCHEMA_VERSION
    assert root.get("targetNamespace") == auger.SPEC_XML_NAMESPACE

    # The closed sets the validator enforces are the ones the grammar publishes.
    assert xsd_enum("blockKind") == list(auger.SPEC_BLOCK_KINDS)
    assert xsd_enum("lifecycleStatus") == list(auger.SPEC_STATUSES)
    assert xsd_enum("verificationMethod") == list(auger.SPEC_VERIFICATION_METHODS)
    assert xsd_enum("schemaVersion") == list(auger.SPEC_SCHEMA_VERSIONS)

    # Stable identity and version shapes are the same regular expressions, byte for byte.
    assert xsd_pattern("stableId") == auger.SPEC_ID_RE.pattern
    assert xsd_pattern("semanticVersion") == auger.SPEC_VERSION_RE.pattern
    assert xsd_pattern("scopeToken") == auger.SPEC_APPLIES_RE.pattern

    # Every attribute the editor-facing grammar advertises is one the block model reads.
    block_type = None
    for node in root.iter(f"{XSD_NS}complexType"):
        if node.get("name") == "blockType":
            block_type = node
    assert block_type is not None, "schema has no blockType"
    declared = {
        attribute.get("name"): attribute.get("use")
        for attribute in block_type.iter(f"{XSD_NS}attribute")
    }
    for required in ("id", "kind", "status", "version"):
        assert declared.get(required) == "required", (
            f"{required} must be a required attribute in the schema"
        )
    for optional in ("applies-to", "verification", "method"):
        assert optional in declared
    for edge in auger.SPEC_EDGE_ATTRS:
        assert edge in declared, f"edge attribute {edge} missing from the schema"


# ---------------------------------------------------------------- AC-2: selection and assembly
def test_shared_block_reference() -> None:
    """AC-2: a block authored once is reused across files by stable ID, not by a copy."""
    code, out, err = run_spec("validate", str(SPECS))
    assert code == 0, err
    assert "3 document(s)" in out and "10 block(s)" in out, out

    _root, documents, blocks = auger.load_spec_corpus(SPECS)
    by_id = {block["id"]: block for block in blocks}
    assert len(blocks) == len(by_id), "a stable ID was declared twice"

    shared = by_id["AUG-SHARED-DETERMINISM"]
    assert shared["source"] == "shared.xml#AUG-SHARED-DETERMINISM"
    # The fragment's blocks are cited to the file they are AUTHORED in, even though export.xml
    # splices them — a citation points at the source of truth, not at the assembly site.
    assert shared["applies_to"] == ("auger.export", "auger.spec")
    assert shared["version"] == "1.1.0"

    # The second document reuses that block by ID, with no include of its own.
    rebuilt = by_id["AUG-REQ-030"]
    assert rebuilt["refs"]["depends-on"] == ("AUG-SHARED-DETERMINISM",)
    assert rebuilt["source_file"] == "assembly.xml"
    assert "shared.xml" in documents and "assembly.xml" in documents


def test_selects_only_requested_blocks_and_their_dependencies() -> None:
    """AC-2/AC-4: matched blocks plus their explicit closure — nothing else, from two files."""
    code, out, err = run_spec("assemble", str(SPECS), "--id", "AUG-REQ-030")
    assert code == 0, err
    ids = assembled_ids(out)
    assert ids == ["AUG-REQ-030", "AUG-AC-030-1", "AUG-SHARED-DETERMINISM"], out

    # AUG-SHARED-CITATION is named only by `refs=`, which is navigable but NEVER expands.
    # Its ID legitimately appears as the matched block's own edge listing; it must never be
    # EMITTED as a block.
    assert "AUG-SHARED-CITATION" not in ids, out
    # The requirement's acceptance criterion and its depends-on target are the closure.
    assert "- selection: dependency (via AUG-REQ-030 verifies)" in out
    assert "- selection: dependency (via AUG-REQ-030 depends-on)" in out
    sources = {line.split("- source: ", 1)[1] for line in out.splitlines() if "- source: " in line}
    assert sources == {
        "assembly.xml#AUG-REQ-030",
        "assembly.xml#AUG-AC-030-1",
        "shared.xml#AUG-SHARED-DETERMINISM",
    }
    # Two source FILES, which is the cross-file requirement.
    assert len({source.split("#", 1)[0] for source in sources}) == 2


def test_selection_matches_the_golden() -> None:
    """AC-4: the attribute selection is pinned to a checked-in golden document."""
    code, out, err = run_spec(
        "assemble", str(SPECS),
        "--kind", "requirement", "--applies-to", "auger.export", "--status", "active",
    )
    assert code == 0, err
    golden = (EXPECTED / "assembly-active-auger-export.md").read_text(encoding="utf-8")
    assert out == golden
    assert assembled_ids(out) == [
        "AUG-SHARED-DETERMINISM",
        "AUG-REQ-014",
        "AUG-AC-014-1",
        "AUG-VER-014-1",
    ]
    # Decoys: another scope, and a deprecated requirement in the selected scope.
    assert "AUG-REQ-020" not in out
    assert "AUG-REQ-021" not in out
    assert "AUGER-EXPORT-SURFACE" not in out, "a section matched a requirement selector"


def test_no_deps_emits_only_the_matched_block() -> None:
    """The closure is explicit and switchable: --no-deps is the matched set alone."""
    code, out, err = run_spec("assemble", str(SPECS), "--id", "AUG-REQ-014", "--no-deps")
    assert code == 0, err
    assert assembled_ids(out) == ["AUG-REQ-014"]
    assert "matched: 1 · dependencies: 0 · total: 1" in out


def test_selection_is_byte_identical_across_runs() -> None:
    """AC-2: deterministic bytes and provenance order; the JSON view carries both counts."""
    args = (
        "assemble", str(SPECS), "--kind", "requirement", "--applies-to", "auger.export",
        "--status", "active", "--format", "json",
    )
    first_code, first, first_err = run_spec(*args)
    second_code, second, second_err = run_spec(*args)
    assert (first_code, second_code) == (0, 0), (first_err, second_err)
    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(
        second.encode()
    ).hexdigest()

    payload = json.loads(first)
    assert payload["schema_version"] == auger.SPEC_SCHEMA_VERSION
    assert payload["counts"] == {"matched": 2, "dependencies": 2, "total": 4}
    assert payload["selection"] == (
        "kind=requirement, applies-to=auger.export, status=active"
    )
    assert [block["id"] for block in payload["blocks"]] == [
        "AUG-SHARED-DETERMINISM",
        "AUG-REQ-014",
        "AUG-AC-014-1",
        "AUG-VER-014-1",
    ]
    # Every block is cited to its source file and carries its own version metadata.
    for block in payload["blocks"]:
        assert block["source"] == f"{block['source'].split('#')[0]}#{block['id']}"
        assert block["version"]
        assert block["status"] in auger.SPEC_STATUSES


def test_list_reports_ids_attributes_and_citations() -> None:
    """The corpus is inspectable without assembling: id, kind, status, version, citation."""
    code, out, err = run_spec("list", str(SPECS), "--kind", "requirement")
    assert code == 0, err
    assert "AUG-REQ-014\trequirement\tactive\t1.0.0\texport.xml#AUG-REQ-014" in out
    assert "AUG-REQ-021\trequirement\tdeprecated\t0.9.0" in out
    assert "AUG-AC-014-1" not in out, "a kind filter leaked another kind"


def test_version_and_status_selectors_are_exact() -> None:
    """The remaining two attributes select exactly, not fuzzily."""
    code, out, err = run_spec("list", str(SPECS), "--version", "1.1.0")
    assert code == 0, err
    assert [line.split("\t", 1)[0] for line in out.splitlines()] == [
        "AUG-SHARED-DETERMINISM"
    ]

    code, out, err = run_spec("list", str(SPECS), "--status", "deprecated")
    assert code == 0, err
    assert [line.split("\t", 1)[0] for line in out.splitlines()] == ["AUG-REQ-021"]


def test_verifies_expands_from_the_requirement_not_from_the_criterion() -> None:
    """Directionality is explicit: the requirement owns its criteria, not the reverse.

    Selecting a requirement pulls the blocks that declare `verifies=<it>`; selecting a
    verification block alone pulls nothing, because the closure is defined from the contract
    the caller asked for. Documented in docs/SPEC-003-xml-specs.md#assembly-and-selection.
    """
    code, out, err = run_spec("assemble", str(SPECS), "--id", "AUG-VER-014-1")
    assert code == 0, err
    assert assembled_ids(out) == ["AUG-VER-014-1"]
    assert "matched: 1 · dependencies: 0 · total: 1" in out


# ---------------------------------------------------------------- AC-3: refusals
REFUSALS = [
    ("unresolved-reference", "unresolved-reference"),
    ("duplicate-id", "duplicate-id"),
    ("reference-cycle", "reference-cycle"),
    ("schema-version", "unsupported-schema-version"),
    ("invalid-verification-method", "invalid-verification-method"),
    ("missing-verification-method", "invalid-verification-method"),
    ("include-cycle", "include-cycle"),
    ("external-entity", "external-entity-forbidden"),
]


@pytest.mark.parametrize("fixture,code_name", REFUSALS)
def test_refusals_are_typed_and_emit_no_document(fixture: str, code_name: str,
                                                 tmp_path: Path) -> None:
    """AC-3: each integrity failure is a typed non-zero refusal with no partial document."""
    root = INVALID / fixture
    assert root.is_dir(), f"missing refusal fixture {root}"

    validate_code, validate_out, validate_err = run_spec("validate", str(root))
    assert validate_code == 2, (fixture, validate_out, validate_err)
    assert validate_out == "", f"{fixture} emitted output before refusing: {validate_out!r}"
    assert f"spec error ({code_name})" in validate_err, validate_err

    target = tmp_path / "assembled.md"
    assemble_code, assemble_out, assemble_err = run_spec(
        "assemble", str(root), "--kind", "requirement", "--out", str(target)
    )
    assert assemble_code == 2, (fixture, assemble_out, assemble_err)
    assert assemble_out == "", f"{fixture} emitted output before refusing: {assemble_out!r}"
    assert f"spec error ({code_name})" in assemble_err, assemble_err
    assert not target.exists(), f"{fixture} left a partial document at {target}"

    # The same refusal is reachable through the API, typed rather than stringly.
    with pytest.raises(auger.SpecError) as caught:
        auger.load_spec_corpus(root)
    assert caught.value.code == code_name


def test_empty_selector_refused(tmp_path: Path) -> None:
    """Assembly is never a silent whole-corpus dump."""
    target = tmp_path / "assembled.md"
    code, out, err = run_spec("assemble", str(SPECS), "--out", str(target))
    assert code == 2, (out, err)
    assert out == ""
    assert "spec error (empty-selector)" in err
    assert not target.exists()


def test_unknown_id_is_a_typed_no_match(tmp_path: Path) -> None:
    target = tmp_path / "assembled.md"
    code, out, err = run_spec(
        "assemble", str(SPECS), "--id", "AUG-NOT-A-BLOCK", "--out", str(target)
    )
    assert code == 2, (out, err)
    assert out == ""
    assert "spec error (no-match)" in err
    assert not target.exists()


def test_successful_out_is_written_atomically(tmp_path: Path) -> None:
    """The happy path writes the file the failure path must never create."""
    target = tmp_path / "assembled.md"
    code, out, err = run_spec("assemble", str(SPECS), "--id", "AUG-REQ-030",
                              "--out", str(target))
    assert code == 0, err
    assert out.strip() == f"wrote 3 block(s) to {target}"
    assert target.read_text(encoding="utf-8").startswith("# Auger spec assembly")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["assembled.md"], (
        "a temporary file survived the atomic write"
    )


# ---------------------------------------------------------------- AC-4: export unchanged
def test_markdown_export_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4: the default Markdown export contract is byte-identical to the base commit."""
    monkeypatch.setattr(
        auger,
        "select",
        lambda ns, table, query="": [dict(row) for row in EXPORT_ROWS.get(table, [])],
    )
    monkeypatch.setattr(auger, "graph_edges", lambda ns, project_id: [])
    first = auger.render_export("golden-ns")
    second = auger.render_export("golden-ns")
    golden = (EXPECTED / "markdown-export-golden.md").read_text(encoding="utf-8")
    assert first == golden
    assert second == first, "the export renderer is not deterministic"


# ---------------------------------------------------------------- AC-5: the record
def test_spec_doc_records_the_decision() -> None:
    """AC-5: rationale, alternatives, verification, and a resolvable ch:trace anchor."""
    assert SPEC_DOC.is_file(), f"missing spec doc: {SPEC_DOC}"
    doc = SPEC_DOC.read_text(encoding="utf-8")
    for heading in ("## Rationale", "## Alternatives considered", "## Verification"):
        assert heading in doc, f"spec doc lost its {heading!r} section"
    assert "ch:trace row=AUG-118" in doc
    assert "spec=docs/SPEC-003-xml-specs.md#" in doc, (
        "the trace must name a repository-relative spec anchor"
    )
    assert "tests/test_xml_specs.py" in doc, "the spec must name its test evidence"
    assert "tests/fixtures/xml/expected" in doc, "the spec must name its golden evidence"


def test_gate_runs_these_tests() -> None:
    """AC-5: CI and the commit guard run this file, via tests/gate.sh's pytest arm."""
    gate = GATE.read_text(encoding="utf-8")
    assert "tests/test_xml_specs.py" in gate, (
        "tests/gate.sh must run tests/test_xml_specs.py — the guard's test command is the gate"
    )
    arm = [
        line for line in gate.splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm and "tests/test_xml_specs.py" in arm[0], (
        "the file must be in the pytest arm's explicit file list"
    )


def test_assembles_migration_obligation_with_repo_harness_metadata():
    bundle = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")
    obligation = bundle["verifications"][0]
    assert obligation == {
        "id": "TEST-MIG-1",
        "type": "test",
        "path": "tests/test_xml_specs.py",
        "selector": "test_migration_obligation_applies_named_file_and_detects_noop",
        "runner": [sys.executable, "-m", "pytest"],
        "fixture": "tests/fixtures/xml/verification/prior_schema.sql",
        "migration": "tests/fixtures/xml/verification/migrations/001_add_marker.sql",
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
    obligation = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")[
        "verifications"
    ][0]
    prior = (REPO_ROOT / obligation["fixture"]).read_text(encoding="utf-8")
    migration_path = REPO_ROOT / obligation["migration"]
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
    argv = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")[
        "verifications"
    ][0]["runner"]
    assert isinstance(argv, list)
    assert argv == [sys.executable, "-m", "pytest"]
