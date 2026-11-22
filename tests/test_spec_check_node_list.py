"""AUG-152: `spec check` reports every resolvable block kind, acceptance included.

Measured defect (BASE `7e21ba7`): `validate_xml_specs` collected its reported node list
from `SPEC_BLOCK_TAGS` = {"requirement", "verification", "test"}, so an authored corpus
whose requirement carries an acceptance block (shipped evidence: ``specs/spec-verbs.xml``
with ``SPEC-VERBS-SELECTION-AC1``) validated, was selectable by `spec list`/`spec
assemble`, and yet `spec check` never listed it. A reader of `check` could not tell an
UNLISTED acceptance from an ABSENT one.

After this change the enumerated node list follows `SPEC_CHECK_NODE_TAGS` (the same set
plus ``acceptance``) while the typed ref-resolution rules are untouched: nothing here
widens what a `verifies=` edge may point at.

Each acceptance criterion and the test that pins it:

  AC1 acceptance ids listed, output shape otherwise unchanged .. test_check_lists_acceptance_blocks_it_validates
  AC2 shipped node set == validated req/verif/ac ids, by kind ... test_reported_node_set_equals_the_kinds_that_validate
  AC3 a corpus that validated before still validates ............ test_a_corpus_that_validated_before_still_validates
  AC3 acceptance-only dangling reference still refuses .......... test_verification_verifies_acceptance_refuses_as_before
  AC3 requirement -> acceptance edge still refuses ............... test_requirement_verifies_acceptance_refuses_as_before

Hermetic: no DuckBrain, no JEV, no network. Drives `auger spec check` in-process over
the shipped corpus and over throwaway fixtures.

ch:trace row=AUG-152 spec=specs/spec-verbs.xml#SPEC-VERBS-SELECTION-AC1 test=tests/test_spec_check_node_list.py evidence=none:the-worktree-guard-log-of-this-commit verdict=none:pending-judge commit=none:proof-is-this-file
"""

from __future__ import annotations

import contextlib
import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SHIPPED_SPECS = REPO_ROOT / "specs"
GATE = REPO_ROOT / "tests" / "gate.sh"

NS = "{urn:auger:spec:1}"

#: The dangling-reference fixture of this file: the shape is the point, not the prose.
DANGLING_ACCEPTANCE_CORPUS = """\
<spec xmlns="urn:auger:spec:1" id="AUGER-AC-DANGLE" version="1.0.0" schema-version="1.0">
  <requirement id="AC-REQ-1" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.spec" verification="analysis" verifies="AC-VER-1">
    The task packet is statement-shaped, never a closure dump.
  </requirement>
  <acceptance id="AC-AC-1" kind="acceptance" status="active" version="1.0.0"
              applies-to="auger.spec" verification="analysis" verifies="AC-REQ-1">
    An assembly refuses an empty selector with rc 2 and writes no file.
  </acceptance>
  <verification id="AC-VER-1" kind="verification" status="active" version="1.0.0"
                applies-to="auger.spec" method="analysis" type="analysis"
                verifies="AC-AC-1">
    This verification names an ACCEPTANCE block as its target: a dangling requirement
    ref, before this change and after it.
  </verification>
</spec>
"""


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def write_document_corpus(directory: Path, name: str, document: str) -> Path:
    """One-document corpus at `directory/name`; returns the corpus root (tmp_path-scoped)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(document, encoding="utf-8")
    return directory


def ids_by_kind(corpus_root: Path) -> dict[str, str]:
    """Every authored block id in a corpus, mapped to its local tag (kind).

    Sections carry no id and are not reportable blocks; the spec root element is skipped
    by the tag filter below. This is the test-side mirror of the grammar's own
    block-census, derived from the XML source instead of from the code under test.
    """
    found: dict[str, str] = {}
    for document in sorted(corpus_root.glob("*.xml")):
        for element in ET.parse(document).getroot().iter():
            local = (
                element.tag.rpartition("}")[2]
                if element.tag.startswith("{")
                else element.tag
            )
            if element.get("id") and local in {
                "requirement",
                "acceptance",
                "verification",
                "test",
            }:
                assert element.get("id") not in found, (
                    "corpus fixture has a duplicate id"
                )
                found[element.get("id")] = local
    return found


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    for required in (
        "tests/test_spec_check_node_list.py",
        "tests/test_spec_repository_root.py",
        "tests/test_xml_specs.py",
    ):
        assert required in arm[0], (
            f"{required} must be in tests/gate.sh's pytest arm — the guard's test command "
            "is the gate, so a proof file outside it is never run"
        )


# ---------------------------------------------------------------- AC1: acceptance listed


def test_check_lists_acceptance_blocks_it_validates() -> None:
    """The shipped corpus' acceptance criterion appears in `spec check`'s node list."""
    code, out, err = run_spec("check", str(SHIPPED_SPECS))
    assert code == 0, err
    payload = _payload_or_refuse(out)
    assert payload["status"] == "valid"
    assert "SPEC-VERBS-SELECTION-AC1" in payload["nodes"], payload
    # Nothing that used to be listed vanished: the pre-fix node set is a subset.
    assert {
        "SPEC-VERBS-SELECTION",
        "SPEC-VERBS-SELECTION-V1",
        "SPEC-VERBS-SELECTION-V2",
    } <= set(payload["nodes"])


# ------------------------------------------------- AC2: node set with kinds, by identity


def test_reported_node_set_equals_the_kinds_that_validate() -> None:
    """`check`'s node list for the shipped corpus is exactly the authored block census.

    The expectation is derived from the corpus XML itself (id -> local tag), so the test
    fails on either side of an omission: a block the corpus authors but `check` does not
    report, and a node `check` reports that the corpus does not author.
    """
    code, out, err = run_spec("check", str(SHIPPED_SPECS))
    assert code == 0, err
    payload = _payload_or_refuse(out)
    assert payload["status"] == "valid"
    reported = payload["nodes"]
    expected = ids_by_kind(SHIPPED_SPECS)
    assert set(expected) == set(reported), (
        f"reported node set drifted from the corpus census: missing={sorted(set(expected) - set(reported))} "
        f"extra={sorted(set(reported) - set(expected))}"
    )
    assert reported == sorted(expected), "the node list must stay sorted"
    # WITH the kind of each node: every reported id carries its authored kind, and the
    # shipped corpus' reported kinds include an acceptance block (the AUG-152 class).
    kinds = [(node_id, expected[node_id]) for node_id in reported]
    assert "acceptance" in {kind for _, kind in kinds}, kinds
    assert ("SPEC-VERBS-SELECTION-AC1", "acceptance") in kinds, kinds


# ------------------------------------------------------------- AC3: verdicts do not move


def test_a_corpus_that_validated_before_still_validates() -> None:
    """`spec validate`'s counted verdict line and `check`'s status are untouched."""
    code, out, err = run_spec("validate", str(SHIPPED_SPECS))
    assert code == 0, err
    assert err == ""
    assert out == "spec: 3 document(s), 12 block(s), 7 reference(s) ok\n", out
    pre_block_ids = ids_by_kind(SHIPPED_SPECS)
    # `spec check` still answers valid, and every non-acceptance block of the corpus is
    # still listed — the pre-change node set is intact, acceptance joined it.
    code, out, err = run_spec("check", str(SHIPPED_SPECS))
    assert code == 0, err
    payload = _payload_or_refuse(out)
    assert payload["status"] == "valid"
    assert set(payload["nodes"]) >= set(pre_block_ids)
    assert set(payload["nodes"]) - set(pre_block_ids) <= {"SPEC-VERBS-SELECTION-AC1"}


def test_verification_verifies_acceptance_refuses_as_before(
    tmp_path: Path,
) -> None:
    """A verification pointing at an acceptance id refuses, before this change and after.

    The refusal is the LOADER's typed reference check (`invalid-reference-type`), not the
    obligation pass — so adding acceptance ids to `check`'s REPORT can never have widened
    what a `verifies=` edge may resolve to.
    """
    root = write_document_corpus(
        tmp_path / "corpus", "dangle.xml", DANGLING_ACCEPTANCE_CORPUS
    )
    code, out, err = run_spec("check", str(root), "--repo-root", str(REPO_ROOT))
    assert code == 2, (out, err)
    assert out == ""
    assert "invalid-reference-type" in err, err
    assert "AC-VER-1" in err and "AC-AC-1" in err, err


def test_requirement_verifies_acceptance_refuses_as_before(
    tmp_path: Path,
) -> None:
    """A requirement's `verifies=` must stay an obligation; acceptance is not one."""
    root = write_document_corpus(
        tmp_path / "corpus",
        "req-edge.xml",
        DANGLING_ACCEPTANCE_CORPUS.replace('verifies="AC-VER-1"', 'verifies="AC-AC-1"'),
    )
    code, out, err = run_spec("check", str(root), "--repo-root", str(REPO_ROOT))
    assert code == 2, (out, err)
    assert out == ""
    assert "invalid-reference-type" in err, err
    assert "AC-AC-1" in err, err


# ------------------------------------------------------------------------ shared helpers


def _payload_or_refuse(stdout: str) -> dict:
    import json

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - shapes a readable red
        pytest.fail(f"`spec check` stdout is not a check payload: {stdout!r} ({exc})")
    assert isinstance(payload, dict) and "nodes" in payload, payload
    return payload
