"""AUG-150 (proof-only residual): the obligation gate keys on the POINTER, not on one spelling.

The AUG-128 rule this row exists for is "the obligation check accepts either `type="test"` OR
`method="test"`". The mechanism that makes that true is narrower than the sentence: the
obligation pass runs for a `<test>` block, or for ANY block that carries a `runner`/`path`/
`selector` POINTER, and `type`/`method` are the closed METHOD vocabulary validated on the same
node. So `type="test"` is not a workaround and never was required — and neither spelling gates
the check.

Measured baseline at this row (the driver's probe, base `b93630b`): stripping every
`type="test"` from a copy of `specs/` and mutating one cited path still refuses
`stale-test-ref`, and the same corpus with real paths validates `ok`. The defect was already
fixed; what was missing were the PROOFS. This file is them, per acceptance line:

  AC1 either spelling refused .. test_both_spellings_are_refused_when_the_cited_path_is_missing
                                  test_the_pair_differs_only_in_the_attribute_that_carries_test
  AC2 no type workaround ....... test_the_shipped_corpus_is_checked_without_the_type_workaround
                                  test_the_doc_does_not_require_the_type_workaround
  AC3 no over-blocking ......... test_a_non_test_method_is_not_forced_through_the_test_checks
  AC4 corpus is not decoration . test_the_shipped_corpus_is_not_decoration

Every refusal assertion names the DOCUMENT, the BLOCK ID and the RESOLVED PATH, because a
diagnostic an author cannot act on is another round trip.

ch:trace row=AUG-150 spec=docs/XML-SPEC-SCHEMA.md#the-repository-root
         test=tests/test_spec_obligation_gate.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AUGER = REPO_ROOT / "auger.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
SPECS = REPO_ROOT / "specs"
DOC = REPO_ROOT / "docs" / "XML-SPEC-SCHEMA.md"
GATE = REPO_ROOT / "tests" / "gate.sh"
PAIR = FIXTURES / "invalid" / "obligation-attr-pair"
METHODS = FIXTURES / "methods"
BOGUS_PATH = "tests/does-not-exist.py"
BOGUS_SELECTOR = "test_attr_pair"
PAIR_BLOCK = "AUG-TEST-ATTRPAIR-1"
NAMESPACE = "{urn:auger:spec:1}"


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` as a subprocess, exactly as a user would."""
    done = subprocess.run(
        [sys.executable, str(AUGER), "spec", *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


# ---------------------------------------------------------------- AC1: either spelling
def test_the_pair_differs_only_in_the_attribute_that_carries_test() -> None:
    """Non-vacuity for the refusal below: the three fixtures are ONE document, one line apart.

    Without this the "both spellings are refused" claim could be carried by three unrelated
    documents that differ in an id, a path or a block kind — and would then prove nothing about
    the attribute. The three texts are byte-comparable once that one line is removed.
    """
    type_only = (PAIR / "type-only" / "attr-pair-type.xml").read_text(encoding="utf-8")
    method_only = (PAIR / "method-only" / "attr-pair-method.xml").read_text(
        encoding="utf-8"
    )
    neither = (PAIR / "neither" / "attr-pair-neither.xml").read_text(encoding="utf-8")

    assert (
        'type="test"' in type_only and "method=" not in type_only.split("<test", 1)[1]
    )
    assert 'method="test"' in method_only
    assert 'type="test"' not in method_only
    assert 'type="test"' not in neither and 'method="test"' not in neither

    type_line = '        type="test"\n'
    method_line = '        method="test"\n'
    assert type_line in type_only and method_line in method_only
    assert type_only.replace(type_line, "") == neither, (
        "the type-carrying fixture differs from the attribute-free one by more than the one "
        "attribute line — the pair proves nothing"
    )
    assert method_only.replace(method_line, "") == neither, (
        "the method-carrying fixture differs from the attribute-free one by more than the one "
        "attribute line — the pair proves nothing"
    )


@pytest.mark.parametrize(
    "case",
    ("type-only", "method-only", "neither"),
)
@pytest.mark.parametrize("verb", ("validate", "check"))
def test_both_spellings_are_refused_when_the_cited_path_is_missing(
    case: str, verb: str
) -> None:
    """A missing cited path is refused whichever attribute spells the method — or neither.

    The third case is the one that makes this more than a spelling test: a citation with NO
    `type`/`method` attribute at all is still checked, so no author needs a vocabulary
    workaround to be graded.
    """
    root = PAIR / case
    assert root.is_dir(), f"missing fixture corpus: {root}"
    rc, out, err = run_spec(verb, str(root))
    assert rc == 2, (
        f"{case}/{verb}: a missing test path must refuse, got rc={rc}: {out!r}"
    )
    assert out == "", f"{case}/{verb}: a refusal printed a partial verdict"
    assert f"spec error ({verb_error_class(verb)})" in err, err
    assert "stale-test-ref" in err, f"{case}/{verb}: wrong refusal class: {err!r}"
    document = next(root.glob("*.xml")).name
    assert document in err, f"{case}/{verb}: the refused document is not named: {err!r}"
    assert PAIR_BLOCK in err, (
        f"{case}/{verb}: the refused block id is not named: {err!r}"
    )
    assert BOGUS_PATH in err, f"{case}/{verb}: the declared path is not named: {err!r}"
    resolved = str(REPO_ROOT / BOGUS_PATH)
    assert resolved in err, f"{case}/{verb}: the RESOLVED path is not named: {err!r}"


def verb_error_class(verb: str) -> str:
    """Both verbs wrap a graph failure in the same outer code (one loader, one verdict)."""
    assert verb in ("validate", "check")
    return "invalid-spec-graph"


# ---------------------------------------------------------------- AC3: no over-blocking
def test_a_non_test_method_is_not_forced_through_the_test_checks() -> None:
    """A method-only block has no test LOCATION, so the obligation checks must not fire.

    This is the direction a tightening breaks first: `method="test"` with no runner/path is the
    shape the authoring grammar documents as a verification PLAN and the shape the shipped
    corpus uses, and `demo`/`analysis`/`inspection` never name a test at all.
    """
    assert METHODS.is_dir(), f"missing fixture corpus: {METHODS}"
    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(METHODS))
        assert rc == 0, (
            f"{verb}: a method-only corpus must validate, got rc={rc}: {err}"
        )
        assert "ok" in out or '"status": "valid"' in out, out

    # Non-vacuity: the corpus really does carry all four methods and no pointer.
    _root, _documents, blocks = auger.load_spec_corpus(METHODS)
    methods = {block["method"] for block in blocks if block["kind"] == "verification"}
    assert methods == set(auger.SPEC_VERIFICATION_METHODS), methods
    for block in blocks:
        assert not any(
            block["obligation"].get(key) for key in ("runner", "path", "selector")
        ), f"{block['id']} carries a test pointer, so it is not a method-only fixture"


# ------------------------------------------------- AC2/AC4: the shipped corpus is checked
def _copy_corpus(tmp_path: Path) -> Path:
    """A throwaway copy of the AUTHORED corpus (never mutate `specs/` itself)."""
    corpus = tmp_path / "specs"
    shutil.copytree(SPECS, corpus)
    return corpus


def _expected_verdict() -> str:
    """The verdict line for the shipped corpus, measured from the corpus itself."""
    _root, documents, blocks = auger.load_spec_corpus(SPECS)
    references = sum(
        len(targets) for block in blocks for targets in block["refs"].values()
    )
    return (
        f"spec: {len(documents)} document(s), {len(blocks)} block(s), "
        f"{references} reference(s) ok"
    )


def test_the_shipped_corpus_is_checked_without_the_type_workaround(
    tmp_path: Path,
) -> None:
    """AC2: the authored corpus validates with every `type="test"` attribute REMOVED.

    The doc's own recommendation may keep `type="test"`; the point proved here is that the corpus
    does not NEED it — the same ids, pointers and prose validate and are graded per obligation.
    """
    corpus = _copy_corpus(tmp_path)
    stripped = 0
    for document in sorted(corpus.glob("*.xml")):
        text = document.read_text(encoding="utf-8")
        stripped += text.count('type="test"')
        document.write_text(text.replace('type="test"', ""), encoding="utf-8")
    assert stripped, (
        'the shipped corpus declares no type="test" at all, so stripping it proves nothing — '
        "update this arm when the corpus stops carrying the attribute"
    )

    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(corpus), "--repo-root", str(REPO_ROOT))
        assert rc == 0, (
            f"{verb}: the corpus without the type workaround was refused: {err}"
        )
        if verb == "validate":
            assert _expected_verdict() in out, out


def test_the_shipped_corpus_is_not_decoration(tmp_path: Path) -> None:
    """AC4: at least one block in `specs/` is really resolved, not merely counted.

    The control is the unmutated copy (rc 0); the falsifier mutates ONE cited path to a file that
    does not exist and requires a refusal naming the document, the block and the resolved path.
    Without this the corpus could become an id list nobody resolves again.
    """
    corpus = _copy_corpus(tmp_path)
    rc, out, _err = run_spec("validate", str(corpus), "--repo-root", str(REPO_ROOT))
    assert rc == 0, f"the unmutated copy must validate: rc={rc} {out!r}"

    target = document = block_id = original = None
    for candidate in sorted(corpus.glob("*.xml")):
        text = candidate.read_text(encoding="utf-8")
        match = re.search(r'path="([^"]+)"', text)
        if not match:
            continue
        original = match.group(1)
        target, document = candidate, candidate.name
        element = next(
            node
            for node in ET.parse(candidate).getroot().iter()
            if node.get("path") == original
        )
        block_id = element.get("id")
        candidate.write_text(
            text.replace(f'path="{original}"', f'path="{BOGUS_PATH}"', 1),
            encoding="utf-8",
        )
        break

    assert target is not None, (
        "no authored block in specs/ cites a test path — nothing in the corpus is resolved by "
        "the obligation gate, so this corpus cannot prove anything"
    )
    assert (REPO_ROOT / original).is_file(), (
        f"the corpus cites {original}, which does not exist in the repository"
    )

    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(corpus), "--repo-root", str(REPO_ROOT))
        assert rc == 2, (
            f"{verb}: a mutated corpus path must refuse, got rc={rc}: {out!r}"
        )
        assert out == "", f"{verb}: a refusal printed a partial verdict"
        assert "stale-test-ref" in err, err
        assert document in err, f"{verb}: the refused document is not named: {err!r}"
        assert block_id in err, f"{verb}: the refused block id is not named: {err!r}"
        assert BOGUS_PATH in err, f"{verb}: the mutated path is not named: {err!r}"
        assert str(REPO_ROOT / BOGUS_PATH) in err, (
            f"{verb}: the RESOLVED path is not named: {err!r}"
        )


# ---------------------------------------------------------------- AC2: doc reconciliation
def test_the_doc_does_not_require_the_type_workaround() -> None:
    """The reconciled documentation states the shape the code actually checks.

    Pinned tokens, because the doc is what an author reads BEFORE writing XML: it must name the
    pointer attributes (`runner`/`selector`/`path`), say that `type`/`method` state a METHOD and
    not a location, and say plainly that `type="test"` is not required.
    """
    text = DOC.read_text(encoding="utf-8")
    for token in ("runner", "selector", "path"):
        assert f"`{token}`" in text, f"the schema doc never names `{token}`"
    assert "states a METHOD" in text, (
        "the schema doc no longer explains that type/method state a METHOD, not a location"
    )
    assert '`type="test"` is not required' in text, (
        "the schema doc does not state that the obligation check accepts method-only citations "
        "(AUG-150 reconciliation)"
    )


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_obligation_gate.py" in arm[0], (
        "tests/test_spec_obligation_gate.py must be named in tests/gate.sh's pytest arm"
    )
