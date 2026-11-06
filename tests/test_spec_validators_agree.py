"""AUG-134: one corpus, one verdict — `spec validate` and `spec check` must agree.

Measured defect (2026-11-05, base `10ef0d7`): on the SAME directory
`spec validate tests/fixtures/xml/specs` reported `3 document(s), 10 block(s), 6
reference(s) ok` and exited 0, while `spec check` on that directory exited 2 with
`invalid-root: .../assembly.xml: expected <spec>`. Two parsers of one corpus: the
authoring loader matched the Clark-notation `{urn:auger:spec:1}spec`, the obligation
validator compared the bare `document.tag`. A caller could get either answer for the
same bytes depending on which verb they happened to type.

The fix is one root/shape loader (`_spec_read_document`, reached through
`_xml_spec_documents`) that both verbs use, and one verdict: `spec validate` runs the
same obligation pass `spec check` runs, over documents loaded ONCE. These tests drive
both verbs as subprocesses against the real `auger.py`, over every corpus root under
`tests/fixtures/xml/`, and assert they agree — plus a table of the verdict each root
must earn, because agreement alone would also pass if both verbs refused everything.

ch:trace row=AUG-134 spec=docs/XML-SPEC-COVERAGE.md test=tests/test_spec_validators_agree.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AUGER = REPO_ROOT / "auger.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
GATE = REPO_ROOT / "tests" / "gate.sh"

#: Every corpus root under the fixture tree, and the refusal each one must produce.
#: `None` means the corpus is valid: BOTH verbs must exit 0 and print a valid verdict.
#: This table is the non-vacuity check on the agreement below.
EXPECTED = {
    "specs": None,
    "verification": None,
    # AUG-150: the method vocabulary fixture — every verification method and NO test pointer, so
    # it is valid and proves the obligation gate does not over-block a verification PLAN.
    "methods": None,
    # AUG-121/AUG-122: the task-scope + context fixture. Valid, and the `spec context` proof's
    # golden corpus — registering it here means BOTH validators also grade it on every run.
    "context": None,
    "context/corpus": None,
    "expected": "empty-corpus",
    "invalid": "external-entity-forbidden",
    "invalid/duplicate-id": "duplicate-id",
    "invalid/external-entity": "external-entity-forbidden",
    "invalid/include-cycle": "include-cycle",
    "invalid/invalid-verification-method": "invalid-verification-method",
    "invalid/missing-verification-method": "invalid-verification-method",
    # AUG-150: three corpora that differ ONLY in which attribute carries the word `test` (or in
    # carrying neither). All three cite a path that does not exist, so all three refuse — the
    # proof that the obligation check is keyed on the pointer, not on one spelling.
    "invalid/obligation-attr-pair/type-only": "invalid-spec-graph",
    "invalid/obligation-attr-pair/method-only": "invalid-spec-graph",
    "invalid/obligation-attr-pair/neither": "invalid-spec-graph",
    # AUG-138: a `decision-ref` with no namespace to resolve in is NO VERDICT — the reference is
    # refused before any request is made, so both verbs agree without a substrate.
    "invalid/decision-ref-no-namespace": "missing-decision-namespace",
    # AUG-143: the rollback declaration is a closed vocabulary, and a claim of support needs a
    # proof — both refuse before any repository resolution is interesting.
    "invalid/invalid-rollback": "invalid-rollback-declaration",
    "invalid/rollback-without-proof": "invalid-spec-graph",
    # AUG-143: both legal declarations on blocks that earn them (this corpus is valid).
    "rollback": None,
    "invalid/reference-cycle": "reference-cycle",
    "invalid/schema-invalid": "schema-invalid",
    "invalid/schema-version": "unsupported-schema-version",
    "invalid/stale-test-obligation": "invalid-spec-graph",
    "invalid/unresolved-reference": "unresolved-reference",
}


def run_verb(verb: str, root: Path) -> tuple[int, str, str]:
    """Run `auger spec <verb> <root>` as a subprocess, exactly as a user would."""
    done = subprocess.run(
        [sys.executable, str(AUGER), "spec", verb, str(root)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def error_class(stderr: str) -> str:
    match = re.search(r"spec error \(([a-z0-9-]+)\):", stderr)
    assert match, f"a refusal must name its error class, got stderr={stderr!r}"
    return match.group(1)


def corpus_roots() -> set[str]:
    """Every directory in the fixture tree that a caller could point a verb at."""
    holding_documents = {
        path.parent.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*.xml")
    }
    top_level = {path.name for path in FIXTURES.iterdir() if path.is_dir()}
    return holding_documents | top_level


def test_the_table_covers_every_fixture_corpus() -> None:
    """A new fixture directory must come with an expected verdict, not ride along."""
    missing = corpus_roots() - set(EXPECTED)
    assert not missing, (
        f"fixture corpora with no expected verdict in this table: {sorted(missing)} — "
        "add them, so an agreement test cannot pass by covering only the cases it likes"
    )
    assert not set(EXPECTED) - corpus_roots(), (
        f"the table names corpora that do not exist: {sorted(set(EXPECTED) - corpus_roots())}"
    )


@pytest.mark.parametrize("relpath", sorted(EXPECTED))
def test_both_verbs_reach_the_same_verdict(relpath: str) -> None:
    """Same root in, same verdict out — for every fixture corpus, valid or refused."""
    root = FIXTURES / relpath
    assert root.is_dir(), f"missing fixture corpus: {root}"
    validate_rc, validate_out, validate_err = run_verb("validate", root)
    check_rc, check_out, check_err = run_verb("check", root)
    expected = EXPECTED[relpath]

    assert (validate_rc, check_rc) == ((0, 0) if expected is None else (2, 2)), (
        f"{relpath}: spec validate rc={validate_rc} / spec check rc={check_rc} — the two "
        f"verbs must agree\n  validate out={validate_out!r}\n  validate err={validate_err!r}\n"
        f"  check out={check_out!r}\n  check err={check_err!r}"
    )

    if expected is None:
        assert "spec:" in validate_out and "ok" in validate_out, validate_out
        payload = json.loads(check_out)
        assert payload["status"] == "valid", payload
        assert payload["nodes"], (
            "a valid corpus with no obligation nodes proves nothing"
        )
        assert validate_err == "" and check_err == ""
        return

    # A refusal never leaves a caller with a partial document.
    assert validate_out == "", f"{relpath}: validate printed output before refusing"
    assert check_out == "", f"{relpath}: check printed output before refusing"
    assert error_class(validate_err) == expected, validate_err
    assert error_class(check_err) == expected, check_err
    # Same diagnostic, not merely the same class: both verbs run ONE loader, so the
    # offending document and the reason must be byte-identical between them.
    assert validate_err == check_err, (
        f"{relpath}: the verbs disagree on the diagnostic\n"
        f"  validate: {validate_err!r}\n  check:    {check_err!r}"
    )
    # And the diagnostic is actionable: it names the evidence it refused — the document,
    # or the block ids on the offending edge. A bare error class with no referent is not
    # a diagnosis a caller can act on.
    names_evidence = ".xml" in validate_err or re.search(
        r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b", validate_err
    )
    assert names_evidence, (
        f"{relpath}: the refusal names neither a document nor a block id: {validate_err!r}"
    )


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_validators_agree.py" in arm[0], (
        "tests/test_spec_validators_agree.py must be named in tests/gate.sh's pytest arm"
    )
