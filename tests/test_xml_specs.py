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
import os
import sqlite3
import json
import subprocess
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

    # Stable identity and version shapes are the same regular expressions, MODULO the anchors an
    # XSD facet must not carry: an XSD pattern is implicitly anchored over the whole lexical value,
    # and `^`/`$` are ORDINARY CHARACTERS in XSD 1.0 — keeping them in the facet would require a
    # literal caret and reject every real ID.
    #
    # AUG-125: byte identity was the wrong invariant. It pinned the Python-dialect `(?:...)` group
    # into the facet, and every XSD 1.0 processor rejects such a facet outright, so "the schema
    # matches the code" held while the published schema was loadable by nothing. The language is
    # now pinned where it belongs: tests/test_xsd_conformance.py validates the same accepted and
    # rejected samples against the shipped schema through a real processor.
    for type_name, pattern in (
        ("stableId", auger.SPEC_ID_RE.pattern),
        ("semanticVersion", auger.SPEC_VERSION_RE.pattern),
        ("scopeToken", auger.SPEC_APPLIES_RE.pattern),
    ):
        facet = xsd_pattern(type_name)
        assert facet == pattern.removeprefix("^").removesuffix("$"), (
            f"{type_name}: the facet and the Python pattern disagree beyond the anchor idiom"
        )
        assert "(?" not in facet, (
            f"{type_name} facet uses a Python-dialect group (lookahead/non-capturing), which an "
            "XSD 1.0 processor rejects — the whole schema then fails to load"
        )

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
    sources = {
        line.split("- source: ", 1)[1]
        for line in out.splitlines()
        if "- source: " in line
    }
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
        "assemble",
        str(SPECS),
        "--kind",
        "requirement",
        "--applies-to",
        "auger.export",
        "--status",
        "active",
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
    code, out, err = run_spec(
        "assemble", str(SPECS), "--id", "AUG-REQ-014", "--no-deps"
    )
    assert code == 0, err
    assert assembled_ids(out) == ["AUG-REQ-014"]
    assert "matched: 1 · dependencies: 0 · total: 1" in out


def test_selection_is_byte_identical_across_runs() -> None:
    """AC-2: deterministic bytes and provenance order; the JSON view carries both counts."""
    args = (
        "assemble",
        str(SPECS),
        "--kind",
        "requirement",
        "--applies-to",
        "auger.export",
        "--status",
        "active",
        "--format",
        "json",
    )
    first_code, first, first_err = run_spec(*args)
    second_code, second, second_err = run_spec(*args)
    assert (first_code, second_code) == (0, 0), (first_err, second_err)
    assert first == second
    assert (
        hashlib.sha256(first.encode()).hexdigest()
        == hashlib.sha256(second.encode()).hexdigest()
    )

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
def test_refusals_are_typed_and_emit_no_document(
    fixture: str, code_name: str, tmp_path: Path
) -> None:
    """AC-3: each integrity failure is a typed non-zero refusal with no partial document."""
    root = INVALID / fixture
    assert root.is_dir(), f"missing refusal fixture {root}"

    validate_code, validate_out, validate_err = run_spec("validate", str(root))
    assert validate_code == 2, (fixture, validate_out, validate_err)
    assert validate_out == "", (
        f"{fixture} emitted output before refusing: {validate_out!r}"
    )
    assert f"spec error ({code_name})" in validate_err, validate_err

    target = tmp_path / "assembled.md"
    assemble_code, assemble_out, assemble_err = run_spec(
        "assemble", str(root), "--kind", "requirement", "--out", str(target)
    )
    assert assemble_code == 2, (fixture, assemble_out, assemble_err)
    assert assemble_out == "", (
        f"{fixture} emitted output before refusing: {assemble_out!r}"
    )
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


def test_unknown_id_is_a_reported_empty_selection_not_a_refusal(tmp_path: Path) -> None:
    """AUG-149: a selector that matches nothing is a REPORT (exit 0), never silence.

    Before this, `--id AUG-NOT-A-BLOCK` was a typed `no-match` refusal while `list` on the same
    selector exited 0 with zero bytes and no diagnostic — two contracts for one shape.
    """
    target = tmp_path / "assembled.md"
    code, out, err = run_spec(
        "assemble", str(SPECS), "--id", "AUG-NOT-A-BLOCK", "--out", str(target)
    )
    assert code == 0, (out, err)
    assert "spec note (no-match)" in err, err
    assert "AUG-NOT-A-BLOCK" in err, err
    assert out.strip() == f"wrote 0 block(s) to {target}", out
    assert target.is_file(), "an empty selection still renders the document it wrote"
    assert "# Auger spec assembly — 0 block(s)" in target.read_text(encoding="utf-8")


def test_successful_out_is_written_atomically(tmp_path: Path) -> None:
    """The happy path writes the file the failure path must never create."""
    target = tmp_path / "assembled.md"
    code, out, err = run_spec(
        "assemble", str(SPECS), "--id", "AUG-REQ-030", "--out", str(target)
    )
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
    assert "tests/fixtures/xml/expected" in doc, (
        "the spec must name its golden evidence"
    )


def test_gate_runs_these_tests() -> None:
    """AC-5: CI and the commit guard run this file, via tests/gate.sh's pytest arm."""
    gate = GATE.read_text(encoding="utf-8")
    assert "tests/test_xml_specs.py" in gate, (
        "tests/gate.sh must run tests/test_xml_specs.py — the guard's test command is the gate"
    )
    arm = [line for line in gate.splitlines() if "python3 -m pytest" in line]
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
    # The root/shape grammar is one loader for every verb now (AUG-134), so this probe
    # corpus carries the same `<spec>` metadata a real corpus does; otherwise the run
    # refuses on the root before reaching the obligation rule each case is proving.
    (root / "bad.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="AUGER-TEST-OBLIGATIONS" version="1.0.0" '
        f'schema-version="1.0">{xml}</spec>',
        encoding="utf-8",
    )
    with pytest.raises(auger.SpecValidationError, match=error):
        auger.validate_xml_specs(root, repository_root=REPO_ROOT)


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


# --------------------------------- AC-17: the obligation verbs are reachable from the CLI
# AUG-126: the obligation validator resolved paths, selectors, migrations and fixtures but had
# no CLI verb, so it could only be reached from Python — these four tests keep it reachable,
# because a capability that exists only in Python is one the drill workflow cannot use.
# AUG-132/AUG-134 closed the other half: `spec validate` used to COUNT documents/blocks/
# references and reported "ok" for a corpus whose test reference pointed at
# tests/does-not-exist.py, while `spec check` refused the namespaced fixture corpus as
# `invalid-root`. Both verbs now run one verdict from one loader — pinned by
# tests/test_spec_validate_obligations.py and tests/test_spec_validators_agree.py — and these
# four keep the verbs themselves reachable.


def test_spec_check_validates_obligations_not_just_counts() -> None:
    code, out, err = run_spec("check", str(VERIFICATION_FIXTURES))
    assert code == 0, err
    payload = json.loads(out)
    assert payload["status"] == "valid"
    assert "REQ-MIG-1" in payload["nodes"]


def test_spec_check_refuses_a_missing_test_path(tmp_path: Path) -> None:
    text = (VERIFICATION_FIXTURES / "migration.xml").read_text(encoding="utf-8")
    assert 'path="tests/test_xml_specs.py"' in text, "fixture drifted; update this test"
    bad = tmp_path / "bad.xml"
    bad.write_text(
        text.replace(
            'path="tests/test_xml_specs.py"', 'path="tests/does-not-exist.py"'
        ),
        encoding="utf-8",
    )
    code, _out, err = run_spec("check", str(tmp_path), "--repo-root", str(REPO_ROOT))
    assert code == 2, "a missing test path must refuse, not report ok"
    assert "stale-test-ref" in err
    assert "TEST-MIG-1" in err


def test_spec_bundle_emits_the_requirement_verification_set() -> None:
    code, out, err = run_spec("bundle", str(VERIFICATION_FIXTURES), "REQ-MIG-1")
    assert code == 0, err
    payload = json.loads(out)
    assert payload["id"] == "REQ-MIG-1"
    ids = [v["id"] for v in payload["verifications"]]
    assert "TEST-MIG-1" in ids
    first = payload["verifications"][0]
    assert first["path"] == "tests/test_xml_specs.py"
    assert first["selector"]


def test_spec_bundle_refuses_an_unknown_requirement() -> None:
    code, _out, err = run_spec("bundle", str(VERIFICATION_FIXTURES), "REQ-NOPE")
    assert code == 2
    assert "unknown-requirement" in err


# ------------------------------------------------- AUG-144: bundle follows BOTH edge directions
# `spec bundle` collected only the refs a requirement names FORWARD (`verifies=` and
# `<verification-ref>` children), so every obligation that names the requirement — the shape the
# shipped corpus uses and the one `spec check`/`assemble` walk — was invisible and the verb
# answered {"verifications": []} for a requirement that HAS obligations. An empty list is now a
# REPORT (`status: no-obligations`, named on stderr), never a blank where obligations exist.

BUNDLE_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<spec xmlns="urn:auger:spec:1" id="AUGER-BUNDLE-MIX" version="1.0.0" schema-version="1.0">
  <requirement id="BM-REQ-1" kind="requirement" status="active" version="1.0.0"
               verification="test" verifies="BM-VER-2">
    <verification-ref ref="BM-VER-2"/>
  </requirement>
  <verification id="BM-VER-2" kind="verification" status="active" version="1.0.0"
                method="test"/>
  <verification id="BM-VER-1" kind="verification" status="active" version="1.0.0"
                method="analysis" verifies="BM-REQ-1"/>
</spec>
"""

BUNDLE_BOTH_DIRECTIONS = """<?xml version="1.0" encoding="UTF-8"?>
<spec xmlns="urn:auger:spec:1" id="AUGER-BUNDLE-CYCLE" version="1.0.0" schema-version="1.0">
  <requirement id="BC-REQ-1" kind="requirement" status="active" version="1.0.0"
               verification="test" verifies="BC-VER-1"/>
  <verification id="BC-VER-1" kind="verification" status="active" version="1.0.0"
                method="test" verifies="BC-REQ-1"/>
</spec>
"""


def write_corpus(directory: Path, name: str, document: str) -> Path:
    """One-document corpus at `directory/name`; returns the corpus root."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(document, encoding="utf-8")
    return directory


def test_bundle_follows_the_inverse_verifies_edge_in_the_shipped_corpus() -> None:
    """AUG-144 PASS: AUG-VER-014-1 names AUG-REQ-014, so it IS that requirement's obligation."""
    code, out, err = run_spec("bundle", str(SPECS), "AUG-REQ-014")
    assert code == 0, err
    payload = json.loads(out)
    assert payload["id"] == "AUG-REQ-014"
    assert "verifications" in payload, payload
    ids = [obligation["id"] for obligation in payload["verifications"]]
    assert "AUG-VER-014-1" in ids, (
        f"bundle dropped the inverse verifies edge; got {ids!r}"
    )
    # Source order (acceptance block precedes the verification block in export.xml), and every
    # block that names the requirement is present — not just the ones the requirement names.
    assert ids == ["AUG-AC-014-1", "AUG-VER-014-1"], ids
    assert payload["status"] == "ok", payload
    assert payload["count"] == len(ids), payload


def test_bundle_collects_forward_and_inverse_edges_once_each(tmp_path: Path) -> None:
    """An edge declared twice is one obligation; a block naming the requirement is included."""
    root = write_corpus(tmp_path / "corpus", "mixed.xml", BUNDLE_MIXED)
    code, out, err = run_spec(
        "bundle", str(root), "BM-REQ-1", "--repo-root", str(REPO_ROOT)
    )
    assert code == 0, err
    payload = json.loads(out)
    ids = [obligation["id"] for obligation in payload["verifications"]]
    assert ids == ["BM-VER-2", "BM-VER-1"], ids
    assert len(ids) == len(set(ids)), f"an edge was collected twice: {ids!r}"


def test_bundle_says_no_obligations_instead_of_an_anonymous_empty_list() -> None:
    """An empty bundle is a report with a name, not a blank that could be a broken lookup."""
    code, out, err = run_spec("bundle", str(SPECS), "AUG-REQ-020")
    assert code == 0, err
    payload = json.loads(out)
    assert payload["id"] == "AUG-REQ-020"
    assert payload["verifications"] == []
    assert payload["status"] == "no-obligations", payload
    assert payload["count"] == 0, payload
    assert "no-obligations" in err and "AUG-REQ-020" in err, err


def test_bundle_unknown_requirement_is_its_own_typed_code() -> None:
    """A missing requirement keeps a distinct refusal that no real bundle can produce."""
    code, out, err = run_spec("bundle", str(SPECS), "AUG-NOT-A-REQUIREMENT")
    assert code == 2, (out, err)
    assert out == ""
    assert "spec error (unknown-requirement)" in err, err
    assert "invalid-spec-graph" not in err, (
        f"a missing requirement must not borrow the graph-integrity code: {err!r}"
    )


def test_check_list_and_bundle_agree_on_every_node() -> None:
    """Every node `check` lists is resolvable by `list --id`; every requirement also by `bundle`.

    `bundle` is requirement-shaped by definition, so a verification id is refused BY NAME rather
    than answered with an empty obligation list — the agreement asserted here is that no verb
    calls a node that `check` lists "unknown", and that no requirement is answered with silence.
    """
    code, out, err = run_spec("check", str(SPECS), "--repo-root", str(REPO_ROOT))
    assert code == 0, err
    nodes = json.loads(out)["nodes"]
    assert nodes, (
        "the shipped corpus must list nodes for this agreement to mean anything"
    )
    requirements = 0
    for node_id in nodes:
        code, out, err = run_spec("list", str(SPECS), "--id", node_id)
        assert code == 0, err
        rows = [line.split("\t") for line in out.splitlines() if line]
        assert len(rows) == 1 and rows[0][0] == node_id, (node_id, out)
        kind = rows[0][1]
        bcode, bout, berr = run_spec(
            "bundle", str(SPECS), node_id, "--repo-root", str(REPO_ROOT)
        )
        if kind == "requirement":
            requirements += 1
            assert bcode == 0, (node_id, berr)
            payload = json.loads(bout)
            assert payload["id"] == node_id
            assert payload["count"] == len(payload["verifications"])
        else:
            assert bcode == 2, (node_id, kind, bout)
            assert "spec error (unknown-requirement)" in berr, (node_id, berr)
    assert requirements, (
        "the shipped corpus must contain requirements for this to prove anything"
    )


def test_bundle_refuses_a_requirement_declaring_both_edge_directions(
    tmp_path: Path,
) -> None:
    """Both directions is a cycle, not a silently deduplicated pair."""
    root = write_corpus(tmp_path / "corpus", "cycle.xml", BUNDLE_BOTH_DIRECTIONS)
    code, out, err = run_spec(
        "bundle", str(root), "BC-REQ-1", "--repo-root", str(REPO_ROOT)
    )
    assert code == 2, (out, err)
    assert out == ""
    assert "spec error (reference-cycle)" in err, err


# ------------------------------------------- AUG-145: `--out` never crashes, and never half-writes
# `--out` into a missing parent raised FileNotFoundError out of `tempfile.mkstemp` (auger.py's
# `_spec_write_atomic`) and exited 1 with a raw traceback — the one path in the whole `spec`
# surface that answered a caller with an unclassifiable Python stack instead of `spec error
# (<code>)` and rc 2. These drive a REAL subprocess on purpose: in-process the exception would
# surface as a test error, and the defect IS the traceback the process prints.


def run_spec_process(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` as its own process; returns (exit_code, stdout, stderr)."""
    process = subprocess.run(
        [sys.executable, str(REPO_ROOT / "auger.py"), "spec", *argv],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return process.returncode, process.stdout, process.stderr


def test_assemble_out_refuses_a_missing_parent_directory_without_a_traceback(
    tmp_path: Path,
) -> None:
    """A refusal, not a crash: typed code on stderr, rc 2, empty stdout, nothing created."""
    target = tmp_path / "missing" / "sub" / "asm.json"
    code, out, err = run_spec_process(
        "assemble",
        str(SPECS),
        "--id",
        "AUG-REQ-030",
        "--format",
        "json",
        "--out",
        str(target),
    )
    assert "Traceback" not in err, f"--out crashed instead of refusing:\n{err}"
    assert code == 2, (code, out, err)
    assert out == "", out
    assert "spec error (missing-out-directory)" in err, err
    assert not target.exists(), "a refusal wrote the document"
    assert not (tmp_path / "missing").exists(), (
        "a refusal created the missing parent tree"
    )


def test_assemble_out_refusal_leaves_the_directory_byte_identical(
    tmp_path: Path,
) -> None:
    """`--out` naming a DIRECTORY is refused with no temporary file left behind."""
    target = tmp_path / "asm.json"
    target.mkdir()
    before = sorted(path.name for path in tmp_path.iterdir())
    code, out, err = run_spec_process(
        "assemble",
        str(SPECS),
        "--id",
        "AUG-REQ-030",
        "--format",
        "json",
        "--out",
        str(target),
    )
    assert "Traceback" not in err, f"--out crashed instead of refusing:\n{err}"
    assert code == 2, (code, out, err)
    assert out == "", out
    assert "spec error (invalid-out-path)" in err, err
    assert sorted(path.name for path in tmp_path.iterdir()) == before, (
        "a refusal left a partial or temporary file behind"
    )


def test_assemble_out_into_an_existing_directory_writes_exactly_one_file(
    tmp_path: Path,
) -> None:
    """The success path is unchanged: one atomic file, one file on disk, rc 0."""
    directory = tmp_path / "nested"
    directory.mkdir()
    target = directory / "asm.json"
    code, out, err = run_spec_process(
        "assemble",
        str(SPECS),
        "--id",
        "AUG-REQ-030",
        "--format",
        "json",
        "--out",
        str(target),
    )
    assert code == 0, (code, out, err)
    assert "Traceback" not in err, err
    assert out.strip() == f"wrote 3 block(s) to {target}", out
    assert sorted(path.name for path in directory.iterdir()) == ["asm.json"], (
        "the atomic write left a temporary sibling behind"
    )
    assert json.loads(target.read_text(encoding="utf-8"))["counts"]["total"] == 3


# ------------------------------------------ AUG-149: the three selection shapes, one contract each
# The empty-selector case was already refused, but a selector that MATCHED NOTHING exited 0 with
# zero bytes and no diagnostic on `list`, and refused `no-match` on `assemble` — two contracts for
# one shape, and the silent one is indistinguishable from a tool with nothing to say. The three
# shapes now have one stated contract per verb (in each verb's --help epilogue and docs/VERBS.md):
# no selector, selector with no match, selector with matches.


def test_list_selector_with_no_match_reports_it_on_stderr_and_exits_zero() -> None:
    code, out, err = run_spec("list", str(SPECS), "--applies-to", "nope.none")
    assert code == 0, (out, err)
    assert out == "", "an empty selection prints no rows"
    assert "spec note (no-match)" in err, f"the empty selection was silent: {err!r}"
    assert "applies-to=nope.none" in err, err


def test_list_without_a_selector_lists_the_whole_corpus() -> None:
    """Shape 1 for `list`: no selector is the listing verb's documented default."""
    code, out, err = run_spec("list", str(SPECS))
    assert code == 0, err
    assert out.strip(), "list with no selector must list the corpus"
    assert "no-match" not in err, err


def test_list_selector_with_matches_is_quiet() -> None:
    code, out, err = run_spec("list", str(SPECS), "--kind", "requirement")
    assert code == 0, err
    assert out.strip()
    assert err == "", err


def test_assemble_selector_with_no_match_emits_a_valid_empty_document() -> None:
    code, out, err = run_spec(
        "assemble", str(SPECS), "--applies-to", "nope.none", "--format", "json"
    )
    assert code == 0, (out, err)
    payload = json.loads(out)
    assert payload["counts"] == {"matched": 0, "dependencies": 0, "total": 0}, payload
    assert payload["blocks"] == [], payload
    assert payload["selection"] == "applies-to=nope.none", payload
    assert "spec note (no-match)" in err, err


def test_assemble_without_a_selector_is_still_refused() -> None:
    """Shape 1 for `assemble`: never a silent whole-corpus dump."""
    code, out, err = run_spec("assemble", str(SPECS), "--format", "json")
    assert code == 2, (out, err)
    assert out == ""
    assert "spec error (empty-selector)" in err, err
    assert "no-match" not in err, err


def test_assemble_selector_with_matches_exits_zero_and_is_quiet() -> None:
    code, out, err = run_spec(
        "assemble", str(SPECS), "--kind", "requirement", "--format", "json"
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["counts"]["matched"] >= 1, payload
    assert err == "", err


def test_both_verbs_state_the_three_shape_exit_code_contract_in_help() -> None:
    for verb in ("list", "assemble"):
        code, out, err = run_spec(verb, "--help")
        assert code == 0, (verb, err)
        assert "exit codes:" in out, (verb, out)
        assert "no selector" in out, (verb, out)
        assert "no match" in out, (verb, out)


# AUG-144 PASS line 1: the row asked for a fixture pair that is byte-identical except for the
# xmlns declaration. The grammar (elementFormDefault="qualified") REQUIRES urn:auger:spec:1, so
# the namespace-less twin cannot be the positive control it was written as — it is refused. The
# pair below still proves the distinction, in the only direction the grammar allows: the
# namespaced document resolves, and the identical document minus that one declaration is refused
# as schema-invalid. (AUG-125 namespaced the last un-namespaced fixture for this reason.)
BUNDLE_NAMESPACED = """<?xml version="1.0" encoding="UTF-8"?>
<spec xmlns="urn:auger:spec:1" id="AUGER-NS-PROBE" version="1.0.0" schema-version="1.0">
  <requirement id="NS-REQ-1" kind="requirement" status="active" version="1.0.0"
               verification="test"/>
  <verification id="NS-VER-1" kind="verification" status="active" version="1.0.0"
                method="test" verifies="NS-REQ-1"/>
</spec>
"""


def test_bundle_resolves_the_namespaced_shape_and_the_grammar_requires_the_namespace(
    tmp_path: Path,
) -> None:
    namespaced = write_corpus(tmp_path / "ns", "doc.xml", BUNDLE_NAMESPACED)
    plain_root = tmp_path / "plain"
    plain_root.mkdir()
    twin = BUNDLE_NAMESPACED.replace(' xmlns="urn:auger:spec:1"', "", 1)
    assert twin != BUNDLE_NAMESPACED, (
        "the twin must differ from the control by exactly the xmlns"
    )
    (plain_root / "doc.xml").write_text(twin, encoding="utf-8")

    code, out, err = run_spec(
        "bundle", str(namespaced), "NS-REQ-1", "--repo-root", str(REPO_ROOT)
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["id"] == "NS-REQ-1"
    assert [block["id"] for block in payload["verifications"]] == ["NS-VER-1"], payload

    twin_code, twin_out, twin_err = run_spec(
        "bundle", str(plain_root), "NS-REQ-1", "--repo-root", str(REPO_ROOT)
    )
    assert twin_code == 2, (twin_out, twin_err)
    assert twin_out == "", twin_out
    assert "spec error (schema-invalid)" in twin_err, twin_err
