"""AUG-124 AC-16: a task packet's scope is EXPLICIT — in-scope across files, exclusions with reasons.

The criterion is about the SHAPE of the answer, not about a second implementation: a task packet
that names references in several documents, mixes active and inactive blocks, and sits in a corpus
full of unrelated decoys must still answer with exactly the task, the in-scope blocks and the
required graph closure — plus the out-of-scope ids WITH the reason each was excluded. Every returned
block carries its source id, the file it is authored in, its version and its lifecycle status; no
decoy appears anywhere in the response.

Measured at the SG8 base (`952e5dc`, 2026-11-06): the AUG-122 `spec context` verb exists and is the
one query that answers this — so this file drives the SHIPPED verb rather than re-implementing the
selection, and its job is to pin the exact scope/exclusion/closure shape across documents, which is
what AUG-124 AC-16 asks for and what nothing had yet proved for a multi-file, mixed-lifecycle
corpus.

  AC-16 scope + exclusions are explicit ....... test_scope_and_exclusions_are_explicit
  arm ......................................... test_gate_arm_runs_these_files

HERMETIC: no DuckBrain, no JEV, no network, no LLM. The corpus is built in `tmp_path` and names no
test obligation, so nothing outside the corpus is resolved.

ch:trace row=AUG-124 spec=docs/prd/xml-spec-graph.md#requirements
         test=tests/test_task_context.py evidence=evidence/sg8/ verdict=none:not-judged
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

GATE = REPO_ROOT / "tests" / "gate.sh"

TASK = "SG8-SCOPE-TASK"
IN_SCOPE = ("SG8-REQ-A", "SG8-REQ-OLD", "SG8-REQ-B")
EXCLUDED = ("SG8-REQ-Z",)
DECOYS = ("SG8-DECOY-REQ", "SG8-DECOY-AC", "SG8-DECOY-OLD")


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def write_scope_corpus(base: Path) -> Path:
    """THREE documents: in-scope refs spread across two, decoys in a third, one inactive block.

    `scope-a.xml` holds the task, one active requirement with its verification plan and one
    acceptance criterion, and a DEPRECATED requirement that only a deliberate
    `acknowledges=` may pull into scope. `scope-b.xml` holds a second active in-scope
    requirement (which depends on the first, so the closure crosses documents) and the
    out-of-scope target. `decoys.xml` holds unrelated active and inactive blocks whose only job
    is to be absent from the answer.
    """
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    (root / "scope-a.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="SG8-SCOPE-A" version="1.0.0" schema-version="1.0">\n'
        '  <requirement id="SG8-REQ-A" kind="requirement" status="active" version="1.0.0"\n'
        '               applies-to="auger.scope" verification="test" verifies="SG8-VER-A">\n'
        "    A plan is exported as one canonical board row per task.\n"
        "  </requirement>\n"
        '  <verification id="SG8-VER-A" kind="verification" status="active" version="1.0.0"\n'
        '                applies-to="auger.scope" method="test">\n'
        "    Each exported row is read back and compared byte for byte.\n"
        "  </verification>\n"
        '  <acceptance id="SG8-AC-A" kind="acceptance" status="active" version="1.0.0"\n'
        '              applies-to="auger.scope" verification="test" verifies="SG8-REQ-A">\n'
        "    The row records the in-scope and excluded ids.\n"
        "  </acceptance>\n"
        '  <requirement id="SG8-REQ-OLD" kind="requirement" status="deprecated" version="1.0.0"\n'
        '               verification="inspection">\n'
        "    Retired text the task deliberately acknowledges.\n"
        "  </requirement>\n"
        f'  <task id="{TASK}" kind="task" status="active" version="1.0.0"\n'
        '        applies-to="auger.scope" verification="test"\n'
        '        in-scope="SG8-REQ-A SG8-REQ-OLD SG8-REQ-B" acceptance="SG8-AC-A"\n'
        '        out-of-scope="SG8-REQ-Z" acknowledges="SG8-REQ-OLD">\n'
        "    Export this task as one canonical board row.\n"
        '    <exclusion ref="SG8-REQ-Z" reason="owned by the migration wave"/>\n'
        "  </task>\n"
        "</spec>\n",
        encoding="utf-8",
    )
    (root / "scope-b.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="SG8-SCOPE-B" version="1.0.0" schema-version="1.0">\n'
        '  <requirement id="SG8-REQ-B" kind="requirement" status="active" version="2.0.0"\n'
        '               applies-to="auger.scope" verification="test" depends-on="SG8-REQ-A">\n'
        "    The second in-scope requirement lives in another document.\n"
        "  </requirement>\n"
        '  <requirement id="SG8-REQ-Z" kind="requirement" status="active" version="1.0.0"\n'
        '               verification="inspection">\n'
        "    A later wave owns the migration path.\n"
        "  </requirement>\n"
        "</spec>\n",
        encoding="utf-8",
    )
    (root / "decoys.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="SG8-SCOPE-DECOYS" version="1.0.0" schema-version="1.0">\n'
        '  <section id="SG8-DECOY-SECTION" kind="section" status="active" version="1.0.0">\n'
        "    Padding prose no narrow query may return. Padding prose no narrow query may return.\n"
        "    Padding prose no narrow query may return. Padding prose no narrow query may return.\n"
        "  </section>\n"
        '  <requirement id="SG8-DECOY-REQ" kind="requirement" status="active" version="1.0.0"\n'
        '               verification="inspection">Unrelated active requirement.</requirement>\n'
        '  <acceptance id="SG8-DECOY-AC" kind="acceptance" status="active" version="1.0.0"\n'
        '              verification="inspection" verifies="SG8-DECOY-REQ">\n'
        "    Unrelated criterion.\n"
        "  </acceptance>\n"
        '  <requirement id="SG8-DECOY-OLD" kind="requirement" status="deprecated" version="1.0.0"\n'
        '               verification="inspection">Unrelated retired requirement.</requirement>\n'
        "</spec>\n",
        encoding="utf-8",
    )
    return root


def test_scope_and_exclusions_are_explicit(tmp_path: Path) -> None:
    """AC-16: only the task, its in-scope blocks and the closure — plus explicit exclusions.

    Four separate claims, each measured rather than asserted from the implementation's docstring:

    1. the SELECTION is exactly the task + its in-scope refs (two documents) + the graph closure
       that scope requires (one acceptance criterion, one verification plan, one `depends-on`
       target), and the deprecated block appears ONLY because the task acknowledged it — carrying
       its real `deprecated` status rather than being silently relabelled;
    2. every returned block carries its source id, file, version and lifecycle status, so a reader
       needs no second lookup;
    3. the out-of-scope ids are present WITH the author's reason, and are NOT in the selection;
    4. no decoy from any document appears anywhere in the serialized answer.
    """
    root = write_scope_corpus(tmp_path)
    code, out, err = run_spec("context", str(root), "--task", TASK)
    assert code == 0, f"spec context refused a well-formed task corpus: {err}"
    payload = json.loads(out)
    assert payload["status"] == "ok", payload
    assert payload["query"] == {"kind": "task", "value": TASK}, payload["query"]

    selected = payload["selected"]
    ids = [entry["id"] for entry in selected]
    assert ids[0] == TASK, f"the task must be the first entry of its own context: {ids}"
    assert set(ids) == {
        TASK,
        "SG8-REQ-A",
        "SG8-REQ-B",
        "SG8-REQ-OLD",
        "SG8-AC-A",
        "SG8-VER-A",
    }, f"the selection is not exactly the task + scope + closure: {ids}"
    assert len(ids) == len(set(ids)), f"a block is returned twice: {ids}"

    by_id = {entry["id"]: entry for entry in selected}
    # Cross-document provenance: the same answer names blocks authored in two different files.
    files = {entry["id"]: entry["source"].split("#")[0] for entry in selected}
    assert files["SG8-REQ-A"] == "scope-a.xml", files
    assert files["SG8-REQ-B"] == "scope-b.xml", files
    for entry in selected:
        assert entry["source"] == f"{files[entry['id']]}#{entry['id']}", entry
        assert entry["version"], f"{entry['id']} carries no version"
        assert entry["status"], f"{entry['id']} carries no lifecycle status"
        assert entry["kind"], f"{entry['id']} carries no kind"

    # The inactive block is included on purpose, and its status is reported truthfully.
    assert by_id["SG8-REQ-OLD"]["status"] == "deprecated", by_id["SG8-REQ-OLD"]
    assert by_id["SG8-REQ-OLD"]["edge"] == "in-scope", by_id["SG8-REQ-OLD"]
    # The edges that put each closure block in the answer are named, not implied.
    assert by_id["SG8-REQ-A"]["edge"] == "in-scope", by_id["SG8-REQ-A"]
    assert by_id["SG8-REQ-B"]["edge"] == "in-scope", by_id["SG8-REQ-B"]
    assert by_id["SG8-AC-A"]["edge"] == "acceptance", by_id["SG8-AC-A"]
    assert by_id["SG8-VER-A"]["edge"] in {"verifies", "depends-on"}, by_id["SG8-VER-A"]
    # A version is carried per block, so a cross-version scope is visible in the answer.
    assert by_id["SG8-REQ-B"]["version"] == "2.0.0", by_id["SG8-REQ-B"]

    # OUT OF SCOPE is explicit and carries the reason, and never overlaps the selection.
    excluded = {entry["id"]: entry for entry in payload["excluded"]}
    assert set(excluded) == set(EXCLUDED), payload["excluded"]
    assert excluded["SG8-REQ-Z"]["reason"] == "owned by the migration wave", excluded[
        "SG8-REQ-Z"
    ]
    assert excluded["SG8-REQ-Z"]["status"] == "active"
    assert not (set(excluded) & set(ids)), "an id is both selected and excluded"

    # Counters agree with the data they count (a summary that disagrees with the body is a lie).
    assert payload["counts"]["selected"] == len(selected), payload["counts"]
    assert payload["counts"]["dependencies"] == 1, payload["counts"]

    # No decoy and no whole-corpus dump, in any field.
    dump = json.dumps(payload)
    for decoy in DECOYS:
        assert decoy not in dump, f"{decoy} leaked into a narrow task query"
    assert "SG8-REQ-Z" not in json.dumps(selected), (
        "an excluded id leaked into the selection"
    )
    assert "Padding prose" not in dump, (
        "an unrelated section's body leaked into the answer"
    )

    # The same answer through the Python surface, so the CLI is not the only thing pinned.
    direct = auger.build_spec_context(root, task=TASK)
    assert [entry["id"] for entry in direct["selected"]] == ids, direct["selected"]
    assert [entry["id"] for entry in direct["excluded"]] == [
        entry["id"] for entry in payload["excluded"]
    ], direct["excluded"]


def test_gate_arm_runs_these_files() -> None:
    """This proof file must be in tests/gate.sh's pytest arm, or it is never executed."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_task_context.py" in arm[0], (
        "tests/test_task_context.py must be in tests/gate.sh's pytest arm — a proof file outside "
        "it is never executed by the guard or CI"
    )
