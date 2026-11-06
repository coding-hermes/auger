"""AUG-122: ONE query returns the minimal XML context for a task or a code location.

An agent that reaches code it does not understand must be able to ask one question and get the
exact spec blocks that govern it — the task, its in-scope requirements/acceptance/verification
blocks, and an explicit list of what is OUT of scope with the reason each was excluded. Never a
whole-document or whole-corpus dump: the unrelated active blocks in the corpus are the decoys this
file proves are absent from the answer.

Measured gap at the SG7 base (`4bcc358`, 2026-11-06): `grep -n 'spec context' auger.py` returned
nothing. `spec list`/`assemble` select by id or attribute, but nothing resolved a TASK to its scope,
nothing read a `ch:trace spec=` reference out of a source file or symbol, and no query reported the
bytes it read or produced.

Per acceptance line and its proof:

  AC1 task -> task + in-scope + out-of-scope with reasons ... test_task_context_excludes_unrelated_blocks
  AC2 code path/symbol with a ch:trace spec ref ........... test_code_trace_context
  AC3 no trace / unknown symbol / inactive / ambiguous ..... test_a_path_without_a_trace_is_unscoped,
                                                            test_an_unknown_symbol_is_unscoped,
                                                            test_an_inactive_block_is_reported_as_inactive,
                                                            test_an_ambiguous_match_is_reported
  AC4 byte-identical twice (+ golden) ..................... test_the_same_query_is_byte_identical_twice
  AC5 no decoys; selected/dependency/input/output bytes ... test_a_narrow_query_reports_no_decoys_and_measured_bytes
  AC6 read-only: no write/client/LLM seam ................. test_the_query_is_read_only
  arm ..................................................... test_gate_arm_runs_these_files

HERMETIC: no DuckBrain, no JEV, no network, no LLM. Every corpus is either built in `tmp_path` or
the committed fixture under tests/fixtures/xml/context/, and the one golden file is a committed
JSON document compared byte for byte.

ch:trace row=AUG-122 spec=docs/prd/xml-spec-graph.md#selection-and-assembly
         test=tests/test_spec_context.py evidence=evidence/sg7/ verdict=none:not-judged
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

GATE = REPO_ROOT / "tests" / "gate.sh"
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "xml" / "context"
GOLDEN_CORPUS = GOLDEN_DIR / "corpus"
GOLDEN_JSON = GOLDEN_DIR / "expected-context.json"


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def context(root: Path, *args: str) -> dict:
    """`spec context` as parsed JSON; the verb must also exit 0 (a refusal is a finding, not JSON)."""
    code, out, err = run_spec("context", str(root), *args)
    assert code == 0, f"spec context refused: {err}"
    return json.loads(out)


# ---------------------------------------------------------------- fixture corpora
TASK_IN_SCOPE = (
    '  <requirement id="SG7-REQ-1" kind="requirement" status="active" version="1.0.0"\n'
    '               applies-to="auger.export" verification="test" verifies="SG7-VER-1">\n'
    "    The exporter writes one canonical board row per task.\n"
    "  </requirement>\n"
)
TASK_VERIFICATION = (
    '  <verification id="SG7-VER-1" kind="verification" status="active" version="1.0.0"\n'
    '                applies-to="auger.export" method="test">The row is read back and '
    "compared.</verification>\n"
)
TASK_ACCEPTANCE = (
    '  <acceptance id="SG7-AC-1" kind="acceptance" status="active" version="1.0.0"\n'
    '              applies-to="auger.export" verification="test" verifies="SG7-REQ-1">\n'
    "    The canonical row records the in-scope and excluded ids.\n"
    "  </acceptance>\n"
)
OUT_OF_SCOPE = (
    '  <requirement id="SG7-REQ-9" kind="requirement" status="active" version="1.0.0"\n'
    '               verification="inspection">A later wave owns the migration path.'
    "</requirement>\n"
)
TASK = (
    '  <task id="SG7-TASK-1" kind="task" status="active" version="1.0.0"\n'
    '        applies-to="auger.export" verification="test"\n'
    '        in-scope="SG7-REQ-1" acceptance="SG7-AC-1" out-of-scope="SG7-REQ-9">\n'
    "    Export this task as one canonical board row.\n"
    '    <exclusion ref="SG7-REQ-9" reason="owned by a later wave"/>\n'
    "  </task>\n"
)
DECOYS = (
    '  <requirement id="SG7-DECOY-1" kind="requirement" status="active" version="1.0.0"\n'
    '               verification="inspection">Unrelated active requirement.</requirement>\n'
    '  <acceptance id="SG7-DECOY-AC" kind="acceptance" status="active" version="1.0.0"\n'
    '              verification="inspection" verifies="SG7-DECOY-1">Unrelated criterion.'
    "</acceptance>\n"
)
OLD = (
    '  <requirement id="SG7-REQ-OLD" kind="requirement" status="deprecated" version="1.0.0"\n'
    '               verification="inspection">Retired by a later wave.</requirement>\n'
)


def spec_document(body: str) -> str:
    return (
        '<spec xmlns="urn:auger:spec:1" id="SG7-CONTEXT" version="1.0.0" schema-version="1.0">\n'
        f"{body}</spec>\n"
    )


def write_corpus(
    base: Path,
    *,
    decoys: bool = False,
    deprecated: bool = False,
    second_file: bool = True,
) -> Path:
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    body = f"{TASK_IN_SCOPE}{TASK_VERIFICATION}{TASK_ACCEPTANCE}{OUT_OF_SCOPE}{TASK}"
    if decoys:
        body += DECOYS
    if deprecated:
        body += OLD
    (root / "tasks.xml").write_text(spec_document(body), encoding="utf-8")
    if second_file:
        padding = "Padding prose that no narrow query may ever return. " * 80
        (root / "decoy.xml").write_text(
            spec_document(
                '  <section id="SG7-DECOY-SECTION" kind="section" status="active"\n'
                '           version="1.0.0">\n'
                f"    {padding}\n"
                "  </section>\n"
                '  <requirement id="SG7-DECOY-2" kind="requirement" status="active"\n'
                '               version="1.0.0" verification="inspection">Another unrelated\n'
                "active requirement in a second document.</requirement>\n"
            ),
            encoding="utf-8",
        )
    return root


def write_source(
    base: Path, name: str, spec_ref: str, *, symbol: str = "export_row"
) -> Path:
    """A source file whose ch:trace marker names `spec_ref`, as the fleet's code really does."""
    path = base / "code" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '"""Source fixture for the context query."""\n'
        "\n"
        f"# ch:trace row=SG7-TASK-1 spec={spec_ref}\n"
        "def "
        f"{symbol}(row):\n"
        '    """Write one row."""\n'
        "    return row\n"
        "\n"
        f"def other_{symbol}(row):\n"
        "    return row\n",
        encoding="utf-8",
    )
    return path


def ids_in(payload: dict) -> list[str]:
    return [entry["id"] for entry in payload["selected"]]


# ---------------------------------------------------------------- AC1: the task scope
def test_task_context_excludes_unrelated_blocks(tmp_path: Path) -> None:
    """AC1: one query returns the task, its in-scope blocks, and the exclusions WITH reasons.

    The corpus carries two unrelated ACTIVE blocks in one document and a third in a second
    document. None of them may appear: "returns only relevant blocks" is the whole product claim,
    and a decoy that leaks makes the response a smaller dump rather than a context bundle.
    """
    root = write_corpus(tmp_path, decoys=True)
    payload = context(root, "--task", "SG7-TASK-1")

    assert payload["status"] == "ok", payload
    assert payload["query"] == {"kind": "task", "value": "SG7-TASK-1"}, payload["query"]
    assert payload["task"]["id"] == "SG7-TASK-1", payload["task"]
    assert payload["task"]["version"] == "1.0.0"
    assert payload["task"]["edge"] == "task"

    # The task, its requirement, its acceptance criterion and the verification it depends on —
    # in corpus order, each with the provenance a consumer needs.
    assert ids_in(payload) == [
        "SG7-TASK-1",
        "SG7-REQ-1",
        "SG7-VER-1",
        "SG7-AC-1",
    ], ids_in(payload)
    by_id = {entry["id"]: entry for entry in payload["selected"]}
    assert by_id["SG7-REQ-1"]["edge"] == "in-scope", by_id["SG7-REQ-1"]
    assert by_id["SG7-AC-1"]["edge"] == "acceptance", by_id["SG7-AC-1"]
    assert by_id["SG7-VER-1"]["edge"] == "verifies", by_id["SG7-VER-1"]
    for entry in payload["selected"]:
        assert entry["source"].endswith("#" + entry["id"]), entry
        assert entry["status"] == "active", entry

    # Out of scope is EXPLICIT and compact: id, lifecycle, source and the exclusion reason.
    assert [entry["id"] for entry in payload["excluded"]] == ["SG7-REQ-9"], payload[
        "excluded"
    ]
    assert payload["excluded"][0]["reason"] == "owned by a later wave", payload[
        "excluded"
    ][0]
    assert payload["excluded"][0]["status"] == "active"

    # No decoy, in any document, in any field of the response.
    dump = json.dumps(payload)
    for decoy in ("SG7-DECOY-1", "SG7-DECOY-AC", "SG7-DECOY-2"):
        assert decoy not in dump, f"{decoy} leaked into a narrow task query"

    assert payload["counts"]["selected"] == 4, payload["counts"]
    assert payload["counts"]["dependencies"] == 1, payload["counts"]


# ---------------------------------------------------------------- AC2: code / symbol traces
def test_code_trace_context(tmp_path: Path) -> None:
    """AC2: a source path carrying `ch:trace spec=<id>` resolves that block and its closure.

    The answer must carry everything a reader needs to act without opening the corpus: source file,
    block id, version, lifecycle status, applicability, verification method and the relationship
    EDGE that put each block there.
    """
    root = write_corpus(tmp_path)
    write_source(tmp_path, "service.py", "SG7-REQ-1")

    payload = context(root, "--code", "code/service.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "ok", payload
    assert payload["query"] == {"kind": "code", "value": "code/service.py"}, payload[
        "query"
    ]
    # The traced requirement, the verification plan it declares (forward `verifies`) and the
    # acceptance criterion that verifies it (inverse `verifies`) — the declared closure, nothing else.
    assert ids_in(payload) == ["SG7-REQ-1", "SG7-VER-1", "SG7-AC-1"], ids_in(payload)

    traced = payload["selected"][0]
    assert traced["id"] == "SG7-REQ-1"
    assert traced["source"] == "tasks.xml#SG7-REQ-1", traced
    assert traced["version"] == "1.0.0"
    assert traced["status"] == "active"
    assert traced["applies_to"] == ["auger.export"], traced
    assert traced["method"] == "test", traced
    assert traced["edge"] == "spec", traced

    closure = payload["selected"][1]
    assert closure["id"] == "SG7-VER-1"
    assert closure["edge"] == "verifies", closure
    assert closure["method"] == "test", closure
    assert payload["selected"][2]["edge"] == "verifies", payload["selected"][2]

    assert payload["scan"]["files"] == ["code/service.py"], payload["scan"]
    assert payload["scan"]["spec_refs"] == ["SG7-REQ-1"], payload["scan"]
    assert payload["task"] is None, payload["task"]

    # The symbol form of the same query resolves the same blocks.
    by_symbol = context(root, "--symbol", "export_row", "--search-root", str(tmp_path))
    assert by_symbol["status"] == "ok", by_symbol
    assert ids_in(by_symbol) == ["SG7-REQ-1", "SG7-VER-1", "SG7-AC-1"], ids_in(
        by_symbol
    )
    assert by_symbol["scan"]["files"] == ["code/service.py"], by_symbol["scan"]

    # `other_export_row` must NOT be mistaken for `export_row` — a substring match would make this
    # query useless on a real tree, where every symbol has siblings.
    unknown = context(root, "--symbol", "export_ro", "--search-root", str(tmp_path))
    assert unknown["status"] == "unscoped", unknown


# ---------------------------------------------------------------- AC3: the negatives
def test_a_path_without_a_trace_is_unscoped(tmp_path: Path) -> None:
    """AC3: a file with no ch:trace is an explicit `unscoped` result — never a corpus dump."""
    root = write_corpus(tmp_path, decoys=True)
    plain = tmp_path / "code" / "plain.py"
    plain.parent.mkdir(parents=True, exist_ok=True)
    plain.write_text(
        '"""No trace here."""\n\n\ndef helper():\n    return 1\n', encoding="utf-8"
    )

    payload = context(root, "--code", "code/plain.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "no-trace", payload["reason"]
    assert payload["selected"] == [], payload["selected"]
    assert payload["counts"]["selected"] == 0, payload["counts"]
    dump = json.dumps(payload)
    for unrelated in ("SG7-REQ-1", "SG7-DECOY-1", "SG7-TASK-1"):
        assert unrelated not in dump, f"an unscoped result dumped {unrelated}"

    # A trace with no `spec=` field is its own named state, not silently "no trace".
    half = tmp_path / "code" / "half.py"
    half.write_text("# ch:trace row=SG7-TASK-1 test=none:no-test\n", encoding="utf-8")
    payload = context(root, "--code", "code/half.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "trace-without-spec", payload["reason"]

    # A trace naming a block this corpus does not have names the offending id.
    stray = tmp_path / "code" / "stray.py"
    stray.write_text("# ch:trace row=SG7-TASK-1 spec=SG7-REQ-404\n", encoding="utf-8")
    payload = context(root, "--code", "code/stray.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "unknown-spec-id", payload["reason"]
    assert payload["scan"]["spec_refs"] == ["SG7-REQ-404"], payload["scan"]

    # A trace that cites a DOCUMENT anchor (`docs/x.md#section`) carries no XML block. That is its
    # own answer — `spec-names-no-block` — not an unknown id and not a corpus dump.
    doc = tmp_path / "code" / "cited_doc.py"
    doc.write_text(
        "# ch:trace row=AUG-118 spec=docs/SPEC-003-xml-specs.md#assembly\n",
        encoding="utf-8",
    )
    payload = context(root, "--code", "code/cited_doc.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "spec-names-no-block", payload["reason"]
    assert payload["selected"] == []
    assert payload["scan"]["unresolved"] == ["docs/SPEC-003-xml-specs.md#assembly"], (
        payload["scan"]
    )

    # A docstring that merely MENTIONS `ch:trace spec=` is not a marker: a marker carries `row=`.
    prose = tmp_path / "code" / "prose.py"
    prose.write_text(
        '"""Resolve the block a ch:trace spec= reference names."""\n\n\ndef helper():\n    return 1\n',
        encoding="utf-8",
    )
    payload = context(root, "--code", "code/prose.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "no-trace", payload["reason"]
    assert payload["scan"]["spec_refs"] == [], payload["scan"]


def test_an_unknown_symbol_is_unscoped(tmp_path: Path) -> None:
    """AC3: a symbol no file defines is `unscoped`/`unknown-symbol`, with no substitution."""
    root = write_corpus(tmp_path)
    write_source(tmp_path, "service.py", "SG7-REQ-1")
    payload = context(
        root, "--symbol", "no_such_symbol", "--search-root", str(tmp_path)
    )
    assert payload["status"] == "unscoped", payload
    assert payload["reason"] == "unknown-symbol", payload["reason"]
    assert payload["selected"] == []
    assert "SG7-REQ-1" not in json.dumps(payload)


def test_an_inactive_block_is_reported_as_inactive(tmp_path: Path) -> None:
    """AC3: a trace pointing at a DEPRECATED block reports `inactive` — it never picks a stand-in.

    Silently substituting the active block with the same intent would be the worst outcome: the
    agent would implement against a contract that no longer governs the code.
    """
    root = write_corpus(tmp_path, deprecated=True)
    write_source(tmp_path, "legacy.py", "SG7-REQ-OLD")
    payload = context(root, "--code", "code/legacy.py", "--repo-root", str(tmp_path))
    assert payload["status"] == "inactive", payload
    assert payload["reason"] == "block-inactive", payload["reason"]
    assert ids_in(payload) == ["SG7-REQ-OLD"], ids_in(payload)
    assert payload["selected"][0]["status"] == "deprecated", payload["selected"][0]
    assert "SG7-REQ-1" not in json.dumps(payload), (
        "an inactive match was silently replaced"
    )


def test_an_ambiguous_match_is_reported(tmp_path: Path) -> None:
    """AC3: one symbol citing two DIFFERENT blocks is `ambiguous`, and names both candidates.

    Choosing one would be a guess presented as a fact, which is exactly what an unmarked search
    does — and the reason this verb exists at all.
    """
    root = write_corpus(tmp_path)
    write_source(tmp_path, "one.py", "SG7-REQ-1", symbol="shared_helper")
    write_source(tmp_path, "two.py", "SG7-REQ-9", symbol="shared_helper")

    payload = context(root, "--symbol", "shared_helper", "--search-root", str(tmp_path))
    assert payload["status"] == "ambiguous", payload
    assert payload["reason"] == "ambiguous-match", payload["reason"]
    assert payload["candidates"] == ["SG7-REQ-1", "SG7-REQ-9"], payload["candidates"]
    assert payload["selected"] == [], "an ambiguous match emitted a selection"
    assert payload["scan"]["files"] == ["code/one.py", "code/two.py"], payload["scan"]


# ---------------------------------------------------------------- AC4: determinism
def test_the_same_query_is_byte_identical_twice() -> None:
    """AC4: same query + same XML tree -> identical ids, order and BYTES (golden pinned).

    The golden is a committed document, not a re-render of the run: it pins the whole response
    shape — counts, byte measurements and provenance order included — so a change to any of them
    is a deliberate edit to a reviewed file rather than a silent drift.
    """
    assert GOLDEN_CORPUS.is_dir(), f"missing golden corpus: {GOLDEN_CORPUS}"
    assert GOLDEN_JSON.is_file(), f"missing golden: {GOLDEN_JSON}"

    first = run_spec("context", str(GOLDEN_CORPUS), "--task", "SG7-TASK-1")
    second = run_spec("context", str(GOLDEN_CORPUS), "--task", "SG7-TASK-1")
    assert first[0] == 0, first[2]
    assert second[0] == 0, second[2]
    assert first[1] == second[1], "two identical queries produced different bytes"
    assert (
        hashlib.sha256(first[1].encode()).hexdigest()
        == hashlib.sha256(second[1].encode()).hexdigest()
    )

    golden = GOLDEN_JSON.read_text(encoding="utf-8")
    if first[1] != golden:
        raise AssertionError(
            "the context response drifted from the committed golden "
            f"{GOLDEN_JSON}.\n--- got ---\n{first[1]}\n--- want ---\n{golden}\n"
            "Regenerate deliberately only when the SHAPE change is intended."
        )

    payload = json.loads(first[1])
    assert ids_in(payload) == ["SG7-TASK-1", "SG7-REQ-1", "SG7-VER-1", "SG7-AC-1"], (
        ids_in(payload)
    )
    assert [entry["id"] for entry in payload["excluded"]] == ["SG7-REQ-9"], payload[
        "excluded"
    ]


# ---------------------------------------------------------------- AC5: measured, no decoys
def test_a_narrow_query_reports_no_decoys_and_measured_bytes(tmp_path: Path) -> None:
    """AC5: a narrow query over a decoy-heavy corpus reports counts and REAL byte measurements.

    `output_bytes` is proved against the length of the bytes the CLI actually wrote, and
    `input_bytes` against the on-disk size of the documents that were read — a self-reported
    measurement that cannot be checked is the same defect as no measurement.
    """
    root = write_corpus(tmp_path, decoys=True, second_file=True)
    code, out, err = run_spec("context", str(root), "--task", "SG7-TASK-1")
    assert code == 0, err
    payload = json.loads(out)

    counts = payload["counts"]
    assert counts["selected"] == len(payload["selected"]) == 4, counts
    assert counts["dependencies"] == 1, counts
    assert counts["output_bytes"] == len(out.encode("utf-8")), (
        f"output_bytes={counts['output_bytes']} but the verb wrote {len(out.encode())} bytes"
    )
    expected_input = sum(path.stat().st_size for path in sorted(root.rglob("*.xml")))
    assert counts["input_bytes"] == expected_input, (
        f"input_bytes={counts['input_bytes']} vs on-disk corpus {expected_input}"
    )
    # The whole corpus is 5x the selected context: the point of the query is the ratio.
    assert counts["input_bytes"] > counts["output_bytes"] * 2, counts

    dump = json.dumps(payload)
    for decoy in ("SG7-DECOY-1", "SG7-DECOY-AC", "SG7-DECOY-2"):
        assert decoy not in dump, f"{decoy} appeared in a narrow query"


# ---------------------------------------------------------------- AC6: read-only
def test_the_query_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: the query writes nothing, dispatches nothing, and calls no LLM/provider seam.

    Proved three ways: the corpus bytes are unchanged after the run, the module's write/client
    surfaces are made to EXPLODE (any call would fail the test), and the query's own source names
    no HTTP client, boardctl, or model provider.
    """
    root = write_corpus(tmp_path, decoys=True)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }

    def explode(*_args, **_kwargs):
        raise AssertionError("spec context reached a write or substrate seam")

    for seam in ("db", "insert", "patch", "delete_row", "remember", "recall"):
        if hasattr(auger, seam):
            monkeypatch.setattr(auger, seam, explode)

    payload = context(root, "--task", "SG7-TASK-1")
    assert payload["status"] == "ok", payload

    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert after == before, "the read-only context query rewrote the corpus"

    source = (
        inspect.getsource(auger.build_spec_context)
        + inspect.getsource(auger.render_spec_context)
        + inspect.getsource(auger._context_selection)
    ).lower()
    for forbidden in (
        "urllib",
        "http.client",
        "requests",
        "socket",
        "boardctl",
        "subprocess",
    ):
        assert forbidden not in source, (
            f"the context query source names {forbidden!r}: it must read XML and nothing else"
        )


# ---------------------------------------------------------------- the gate arm
def test_gate_arm_runs_these_files() -> None:
    """Every SG7/SG8 proof file must be named in tests/gate.sh's pytest arm or never executes."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    for required in (
        "tests/test_spec_context.py",
        "tests/test_xml_task_contract.py",
        "tests/test_plan_approval.py",
        "tests/test_task_context.py",
        "tests/test_task_packet.py",
    ):
        assert required in arm[0], (
            f"{required} must be in tests/gate.sh's pytest arm — the guard runs exactly this "
            "script, so a proof file outside it is never executed"
        )
