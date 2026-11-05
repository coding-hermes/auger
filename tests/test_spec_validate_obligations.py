"""AUG-132: `spec validate` enforces test obligations, not just block counts.

Measured defect (2026-11-05, base `10ef0d7`): a corpus whose `<verification>` block
named `tests/does-not-exist.py` validated clean —
`spec: 1 document(s), 2 block(s), 1 reference(s) ok`, exit 0. `spec validate` counted
documents, blocks and references and resolved nothing; the obligation validator that
does resolve paths had no route into it (AUG-126 gave that validator its own verb,
`spec check`, which is exactly how one corpus came to have two opposite verdicts,
AUG-134).

After this change `spec validate` IS the obligation gate: the corpus is loaded once by
`load_spec_corpus` and the obligation pass runs over those same documents, so a test
obligation naming a path that is not a tracked file in the repository refuses the run
and names the document, the block id and the resolved path.

ch:trace row=AUG-132 spec=docs/XML-SPEC-COVERAGE.md#known-documentation-gaps test=tests/test_spec_validate_obligations.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AUGER = REPO_ROOT / "auger.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
SPECS = FIXTURES / "specs"
VERIFICATION = FIXTURES / "verification"
STALE = FIXTURES / "invalid" / "stale-test-obligation"
STALE_DOCUMENT = STALE / "stale-test-obligation.xml"
GATE = REPO_ROOT / "tests" / "gate.sh"


def run_spec(*argv: str) -> tuple[int, str, str]:
    done = subprocess.run(
        [sys.executable, str(AUGER), "spec", *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def test_validate_refuses_an_obligation_that_names_a_missing_test_path() -> None:
    """The falsifier: this corpus exits 0 on the pre-change code and must exit 2 now."""
    assert STALE_DOCUMENT.is_file(), f"missing fixture: {STALE_DOCUMENT}"
    rc, out, err = run_spec("validate", str(STALE))
    assert rc == 2, (
        f"a corpus whose test path does not exist must refuse, got rc={rc}: {out!r}"
    )
    assert out == "", "a refusal must not print a partial verdict before failing"
    assert "spec error (invalid-spec-graph)" in err, err
    # The refusal must be actionable: which obligation, in which document, at which path.
    assert "stale-test-ref" in err, err
    assert "stale-test-obligation.xml" in err, (
        f"the refused document is not named: {err!r}"
    )
    assert "AUG-VER-STALE-1" in err, f"the refused block id is not named: {err!r}"
    assert "tests/does-not-exist.py" in err, f"the declared path is not named: {err!r}"
    resolved = str(REPO_ROOT / "tests" / "does-not-exist.py")
    assert resolved in err, f"the RESOLVED path is not named: {err!r}"


def test_the_refusal_comes_from_the_obligation_not_the_root_loader() -> None:
    """Non-vacuity: the fixture is valid under the root/shape grammar.

    Without this, the refusal above could be a schema-version or namespacing error and
    the test would still pass while proving nothing about obligations.
    """
    root, documents, blocks = auger.load_spec_corpus(STALE)
    assert documents.keys() == {"stale-test-obligation.xml"}
    assert root == STALE.resolve()
    assert {block["id"] for block in blocks} == {"AUG-REQ-STALE-1", "AUG-VER-STALE-1"}
    obligation = next(block for block in blocks if block["id"] == "AUG-VER-STALE-1")
    assert obligation["kind"] == "verification"
    assert obligation["method"] == "test"


def test_shipped_valid_corpus_still_validates() -> None:
    """The tightening must not refuse the corpus it ships. `method="test"` without a
    path states a METHOD, not a test location: the authoring grammar requires exactly
    that shape, and treating it as a broken obligation would refuse this corpus."""
    rc, out, err = run_spec("validate", str(SPECS))
    assert rc == 0, err
    assert "spec: 3 document(s), 10 block(s), 6 reference(s) ok" in out, out


def test_a_resolving_obligation_still_passes_validate() -> None:
    """The positive control: the same code path, with a tracked path and a real selector."""
    rc, out, err = run_spec("validate", str(VERIFICATION))
    assert rc == 0, err
    assert "spec: 1 document(s), 2 block(s), 1 reference(s) ok" in out, out


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_validate_obligations.py" in arm[0], (
        "tests/test_spec_validate_obligations.py must be named in tests/gate.sh's pytest arm"
    )
