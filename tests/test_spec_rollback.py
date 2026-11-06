"""AUG-143 [R15]: verification blocks carry a ROLLBACK declaration, and the claim is checked.

The PRD's own words (docs/prd/xml-spec-graph.md, R15/AC-17): "If rollback is supported, the
obligation includes the rollback path and its assertions; if it is not supported, that fact and
rationale are explicit." Before this row the corpus could say neither, so a reader had to assume —
and `docs/XML-SPEC-COVERAGE.md` recorded the hole ("the rollback clause is declared unsupported (no
rollback attribute in the schema)").

The declaration is a CLOSED vocabulary, in the code and in the loaded grammar:

  rollback="unsupported"     this change is not rolled back — stated, so nobody assumes otherwise
  rollback="tracked-test"    rollback IS exercised, and the proof is this block's own obligation
                             pointer (runner + path + selector), resolved against the consuming
                             repository: tracked in git, selector defined in the named file

A claim of support with no such proof is refused (`rollback-undeclared-proof`): "rollback is tested"
with nothing behind it is the decoration the whole obligation surface exists to prevent.

Arms, per acceptance line:

  AC1 closed vocabulary ........ test_the_rollback_vocabulary_is_closed_in_the_code_and_the_grammar
  AC2 claim without proof ...... test_a_rollback_claim_without_a_tracked_test_is_refused
  AC3 unsupported + visible .... test_an_unsupported_declaration_validates_and_is_visible_in_check
  AC4 corpus + parity .......... test_the_shipped_corpus_validates_and_declares_rollback
                                 test_the_grammar_accepts_every_legal_value_and_no_other

ch:trace row=AUG-143 spec=docs/XML-SPEC-SCHEMA.md#rollback-declarations
         test=tests/test_spec_rollback.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AUGER = REPO_ROOT / "auger.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
BAD_VALUE = FIXTURES / "invalid" / "invalid-rollback"
NO_PROOF = FIXTURES / "invalid" / "rollback-without-proof"
DECLARED = FIXTURES / "rollback"
SPECS = REPO_ROOT / "specs"
SCHEMA = REPO_ROOT / "specs" / "schema" / "auger-spec-1.0.xsd"
VIEW = REPO_ROOT / "docs" / "SPEC-CORPUS-VIEW.md"
GATE = REPO_ROOT / "tests" / "gate.sh"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

UNSUPPORTED_ID = "AUG-VER-ROLLBACK-UNSUPPORTED-1"
TESTED_ID = "AUG-VER-ROLLBACK-TESTED-1"


def run_spec(*argv: str) -> tuple[int, str, str]:
    done = subprocess.run(
        [sys.executable, str(AUGER), "spec", *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def xsd_enum(name: str) -> list[str]:
    root = ET.parse(SCHEMA).getroot()
    for node in root.iter(f"{XSD_NS}simpleType"):
        if node.get("name") == name:
            return [
                enumeration.get("value")
                for enumeration in node.iter(f"{XSD_NS}enumeration")
            ]
    raise AssertionError(f"the shipped schema declares no simpleType {name!r}")


# ---------------------------------------------------------------- AC1: closed vocabulary
def test_the_rollback_vocabulary_is_closed_in_the_code_and_the_grammar() -> None:
    """One closed set, in both halves: the code's constant and the grammar's enumeration.

    The XSD is loaded on every verb, so a value the grammar does not enumerate is refused as
    `schema-invalid` — but the CODE runs first (AUG-125 ordering), which is what makes the author
    see the attribute and the enum instead of a generic grammar complaint.
    """
    assert auger.SPEC_ROLLBACK_VALUES == ("unsupported", "tracked-test"), (
        auger.SPEC_ROLLBACK_VALUES
    )
    assert set(xsd_enum("rollbackDeclaration")) == set(auger.SPEC_ROLLBACK_VALUES), (
        "the schema's rollback enumeration and the code's closed set disagree"
    )

    root = ET.parse(SCHEMA).getroot()
    block = next(
        node
        for node in root.iter(f"{XSD_NS}complexType")
        if node.get("name") == "blockType"
    )
    declared = {a.get("name"): a.get("type") for a in block.iter(f"{XSD_NS}attribute")}
    assert declared.get("rollback") == "auger:rollbackDeclaration", (
        "the grammar does not declare rollback with the closed enumeration, so any corpus that "
        "uses it is refused schema-invalid"
    )

    # A value outside the vocabulary is refused by the CODE, naming the document and the block.
    assert BAD_VALUE.is_dir(), f"missing fixture: {BAD_VALUE}"
    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(BAD_VALUE))
        assert rc == 2, (
            f"{verb}: rollback=best-effort must refuse, got rc={rc}: {out!r}"
        )
        assert out == "", f"{verb}: a refusal printed a verdict"
        assert "invalid-rollback-declaration" in err, err
        assert "bad-rollback.xml" in err, f"{verb}: the document is not named: {err!r}"
        assert "AUG-VER-ROLLBACK-BAD-1" in err, (
            f"{verb}: the block is not named: {err!r}"
        )
        assert "best-effort" in err, (
            f"{verb}: the offending value is not named: {err!r}"
        )


# ---------------------------------------------------------------- AC2: claim needs proof
def test_a_rollback_claim_without_a_tracked_test_is_refused() -> None:
    """`rollback="tracked-test"` with no runner/path/selector is a claim nobody can check."""
    assert NO_PROOF.is_dir(), f"missing fixture: {NO_PROOF}"
    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(NO_PROOF))
        assert rc == 2, (
            f"{verb}: a rollback claim with no proof must refuse, got rc={rc}: {out!r}"
        )
        assert out == "", f"{verb}: a refusal printed a verdict"
        assert "rollback-undeclared-proof" in err, err
        assert "no-proof.xml" in err, f"{verb}: the document is not named: {err!r}"
        assert "AUG-VER-ROLLBACK-CLAIM-1" in err, (
            f"{verb}: the block is not named: {err!r}"
        )
        assert "unsupported" in err, (
            f"{verb}: the refusal does not name the honest alternative: {err!r}"
        )


# ---------------------------------------------------------------- AC3: declared, and visible
def test_an_unsupported_declaration_validates_and_is_visible_in_check() -> None:
    """The legal declarations validate, and `spec check` REPORTS them instead of hiding them."""
    assert DECLARED.is_dir(), f"missing fixture: {DECLARED}"
    rc, out, err = run_spec("validate", str(DECLARED), "--repo-root", str(REPO_ROOT))
    assert rc == 0, f"the legal declarations were refused: {err}"
    assert "2 block(s)" in out, out

    rc, out, err = run_spec("check", str(DECLARED), "--repo-root", str(REPO_ROOT))
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["status"] == "valid", payload
    assert payload["rollback"] == {
        UNSUPPORTED_ID: "unsupported",
        TESTED_ID: "tracked-test",
    }, (
        f"the declarations are not visible in `spec check` output: {payload.get('rollback')!r}"
    )


def test_a_declared_rollback_test_is_resolved_and_visible() -> None:
    """The `tracked-test` value is only worth stating if the cited test resolves.

    This is the selector the positive fixture cites, so the fixture's claim is checked by the same
    obligation pass that checks every other test citation — the path must exist, be tracked in git,
    and define this function.
    """
    assert (REPO_ROOT / "tests" / "test_spec_rollback.py").is_file()
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "--error-unmatch",
            "--",
            "tests/test_spec_rollback.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, "the file this fixture cites is not tracked in git"
    assert "test_a_declared_rollback_test_is_resolved_and_visible" in Path(
        __file__
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------- AC4: corpus + parity
def test_the_shipped_corpus_validates_and_declares_rollback() -> None:
    """The authored corpus carries declarations, still validates, and the generated view shows them."""
    rc, out, err = run_spec("validate", str(SPECS), "--repo-root", str(REPO_ROOT))
    assert rc == 0, err
    _root, documents, blocks = auger.load_spec_corpus(SPECS)
    references = sum(len(t) for block in blocks for t in block["refs"].values())
    assert (
        f"spec: {len(documents)} document(s), {len(blocks)} block(s), {references} reference(s) ok"
        in out
    ), out

    declared = {
        block["id"]: block["rollback"] for block in blocks if block.get("rollback")
    }
    assert declared, (
        "no authored block declares a rollback: the feature would be dead code in the corpus that "
        "ships it"
    )
    assert set(declared.values()) <= set(auger.SPEC_ROLLBACK_VALUES)

    rc, out, err = run_spec("check", str(SPECS), "--repo-root", str(REPO_ROOT))
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["rollback"] == declared, (
        "spec check does not report the shipped corpus' declarations"
    )

    view = VIEW.read_text(encoding="utf-8")
    for block_id, value in declared.items():
        assert f"### {block_id} ·" in view
        assert f"- rollback: `{value}`" in view, (
            f"the generated corpus view does not carry {block_id}'s rollback declaration — "
            "regenerate docs/SPEC-CORPUS-VIEW.md"
        )


def test_the_grammar_accepts_every_legal_value_and_no_other() -> None:
    """Language parity for the enum, measured through the loader's own schema validator."""
    schema = auger._spec_load_schema()
    template = (
        '<spec xmlns="urn:auger:spec:1" id="AUGER-SPEC-ROLLBACK-PROBE" version="1.0.0" '
        'schema-version="1.0"><verification id="AUG-VER-ROLLBACK-PROBE-1" kind="verification" '
        'status="active" version="1.0.0" method="inspection" rollback="{value}"/></spec>'
    )
    for value in auger.SPEC_ROLLBACK_VALUES:
        problems = auger._spec_schema_violations(
            schema, template.format(value=value).encode(), "probe.xml"
        )
        assert not problems, (
            f"the grammar rejects rollback={value!r}, which the code accepts: {problems}"
        )
    problems = auger._spec_schema_violations(
        schema, template.format(value="best-effort").encode(), "probe.xml"
    )
    assert problems, "the grammar accepts a rollback value outside the closed set"


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_rollback.py" in arm[0], (
        "tests/test_spec_rollback.py must be named in tests/gate.sh's pytest arm"
    )
