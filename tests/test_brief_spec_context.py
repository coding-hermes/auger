"""AUG-165: every dispatched worker brief embeds `spec context --task` output.

The foreman's dispatch seam: `scripts/brief_spec_context.py` resolves a board row's spec
context through the module's own API (`auger.build_spec_context`, never a re-implementation)
and emits the selected blocks VERBATIM into the brief. Three outcome classes, three contracts:

  * a spec-referencing task -> a Markdown section with each block's text byte-for-byte, a
    per-block byte count and the answer's measured `input_bytes`;
  * a task with no spec references -> NOTHING in the brief (zero bytes, not a placeholder
    heading), one honest stderr line, exit 0;
  * an ambiguous answer -> NOTHING in the brief, the candidate ids on stderr, exit 3 so the
    tick report surfaces it instead of passing silently.

A typed auger refusal propagates as exit 2 with the auger error text.

HERMETIC: no DuckBrain, no JEV, no network, no LLM. Corpora are built in `tmp_path`; the
ambiguous case is a synthetic answer injected at the module's product seam.

ch:trace row=AUG-165 spec=docs/prd/xml-spec-graph.md#selection-and-assembly
         test=tests/test_brief_spec_context.py evidence=evidence/AUG-165/ verdict=none:not-judged
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SCRIPT = REPO_ROOT / "scripts" / "brief_spec_context.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("brief_spec_context", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- fixture corpus
REQ = (
    '  <requirement id="A165-REQ-1" kind="requirement" status="active" version="1.0.0"\n'
    '               applies-to="auger.brief" verification="test" verifies="A165-VER-1">\n'
    "    The brief carries the spec context the row names.\n"
    "  </requirement>\n"
)
VER = (
    '  <verification id="A165-VER-1" kind="verification" status="active" version="1.0.0"\n'
    '                applies-to="auger.brief" method="test">The brief text is compared '
    "byte for byte.</verification>\n"
)
AC = (
    '  <acceptance id="A165-AC-1" kind="acceptance" status="active" version="1.0.0"\n'
    '              applies-to="auger.brief" verification="test" verifies="A165-REQ-1">\n'
    "    The emitted section embeds the selected blocks verbatim.\n"
    "  </acceptance>\n"
)
TASK = (
    '  <task id="A165-TASK-1" kind="task" status="active" version="1.0.0"\n'
    '        applies-to="auger.brief" verification="test"\n'
    '        in-scope="A165-REQ-1" acceptance="A165-AC-1">\n'
    "    Embed the row's spec context in the dispatched brief.\n"
    "  </task>\n"
)


def spec_document(body: str) -> str:
    return (
        '<spec xmlns="urn:auger:spec:1" id="A165-BRIEF" version="1.0.0" schema-version="1.0">\n'
        f"{body}</spec>\n"
    )


def write_corpus(base: Path, *, with_task: bool = True) -> Path:
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    body = f"{REQ}{VER}{AC}" + (TASK if with_task else "")
    (root / "brief.xml").write_text(spec_document(body), encoding="utf-8")
    return root


def run_builder(root: Path, *args: str):
    """Drive `scripts/brief_spec_context.py` in-process; returns (rc, out_file, status)."""
    module = _load_script()
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = module.main(["--spec-root", str(root), *args])
        except SystemExit as exc:  # argparse / fail-loud exits
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- the context path
def test_a_spec_referencing_task_embeds_blocks_verbatim(tmp_path: Path) -> None:
    """The emitted section carries each selected block's text byte-for-byte and its own count."""
    root = write_corpus(tmp_path)
    out_path = tmp_path / "brief-section.md"
    status_path = tmp_path / "status.json"

    rc, _out, err = run_builder(
        root,
        "--row",
        "A165-TASK-1",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 0, err
    section = out_path.read_text(encoding="utf-8")

    # The same blocks the product verb selected, read through the module's own loader.
    answer = auger.build_spec_context(root, task="A165-TASK-1")
    assert answer["status"] == "ok", answer
    _r, _docs, blocks = auger.load_spec_corpus(root)
    texts = {b["id"]: b["text"] for b in blocks}

    header = section.splitlines()[0]
    assert header.startswith("## SPEC CONTEXT — row A165-TASK-1 ("), header
    assert f"{answer['counts']['selected']} block(s) selected" in header, header
    assert f"{answer['counts']['dependencies']} dependency" in header, header
    assert f"input {answer['counts']['input_bytes']} B" in header, header
    assert answer["counts"]["input_bytes"] == sum(
        p.stat().st_size for p in root.rglob("*.xml")
    ), "the header's input_bytes is not the measured corpus size"

    for entry in answer["selected"]:
        block_text = texts[entry["id"]]
        heading = (
            f"### {entry['id']} — {entry['kind']} ({len(block_text.encode('utf-8'))} B)"
        )
        assert heading in section, f"missing (or mis-counted) heading for {entry['id']}"
        assert block_text in section, (
            f"block {entry['id']} text is not embedded verbatim"
        )
        # The per-block count is that block's own emitted text, in bytes.
        assert f"{len(block_text.encode('utf-8'))} B" in section

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["row"] == "A165-TASK-1"
    assert status["outcome"] == "context"
    assert status["selected"] == answer["counts"]["selected"]


def test_plan_section_is_separable_and_pure() -> None:
    """The pure decision function returns (section, status, exit_code) without a corpus."""
    module = _load_script()
    answer = {
        "status": "ok",
        "reason": "",
        "candidates": [],
        "counts": {"selected": 1, "dependencies": 0, "input_bytes": 4242},
        "selected": [{"id": "X-1", "kind": "requirement"}],
    }
    section, status, rc = module.plan_section("ROW-1", answer, {"X-1": "the prose"})
    assert rc == 0
    assert "the prose" in section
    assert "input 4242 B" in section
    assert status["outcome"] == "context"

    empty = {**answer, "status": "unscoped", "reason": "unknown-task", "selected": []}
    section, status, rc = module.plan_section("ROW-1", empty, {})
    assert section == "" and rc == 0, "a no-context answer must not emit a placeholder"
    assert status["outcome"] == "no-context"
    assert status["reason"] == "unknown-task"

    amb = {
        **answer,
        "status": "ambiguous",
        "reason": "ambiguous-match",
        "candidates": ["A-1", "A-2"],
    }
    section, status, rc = module.plan_section("ROW-1", amb, {})
    assert section == "" and rc == 3, "an ambiguous answer must exit non-zero"
    assert status["outcome"] == "ambiguous"
    assert status["candidates"] == ["A-1", "A-2"]


# ---------------------------------------------------------------- the no-context path
def test_no_spec_references_writes_zero_bytes(tmp_path: Path) -> None:
    """A task id the corpus does not scope: 0-byte brief, honest stderr line, exit 0."""
    root = write_corpus(tmp_path, with_task=False)
    out_path = tmp_path / "brief-section.md"
    status_path = tmp_path / "status.json"

    rc, out, err = run_builder(
        root,
        "--row",
        "A165-NOPE",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 0, err
    assert out == "", "stdout must be empty when the section goes to --out"
    assert out_path.read_bytes() == b"", (
        "a no-context answer must emit ZERO bytes, not a placeholder heading"
    )
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["outcome"] == "no-context", status
    assert status["status"] == "unscoped", status
    assert status["reason"] == "unknown-task", status
    assert "unknown-task" in err, "stderr must name the reason honestly"
    assert "A165-NOPE" in err


# ---------------------------------------------------------------- the ambiguous path
def test_an_ambiguous_answer_names_candidates_and_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambiguity must surface (exit 3, candidates on stderr) instead of passing silently."""
    module = _load_script()
    root = write_corpus(tmp_path)
    out_path = tmp_path / "brief-section.md"
    status_path = tmp_path / "status.json"
    synthetic = {
        "query": {"kind": "task", "value": "A165-TASK-1"},
        "status": "ambiguous",
        "reason": "ambiguous-match",
        "task": None,
        "selected": [],
        "excluded": [],
        "candidates": ["A165-REQ-1", "A165-AC-1"],
        "scan": {"files": [], "spec_refs": [], "traces": 0},
        "counts": {"selected": 0, "dependencies": 0, "input_bytes": 0},
    }
    monkeypatch.setattr(
        module.auger, "build_spec_context", lambda *a, **k: dict(synthetic)
    )

    rc, _out, err = run_builder(
        root,
        "--row",
        "A165-TASK-1",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 3, err
    assert out_path.read_bytes() == b"", "an ambiguous answer emitted a section"
    for candidate in ("A165-REQ-1", "A165-AC-1"):
        assert candidate in err, f"stderr must name the candidate {candidate}"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["outcome"] == "ambiguous", status
    assert status["candidates"] == ["A165-REQ-1", "A165-AC-1"], status


def test_a_typed_refusal_propagates_as_exit_2(tmp_path: Path) -> None:
    """A typed auger refusal (e.g. a missing spec root) exits 2 with the auger error text."""
    missing = tmp_path / "no-such-corpus"
    rc, _out, err = run_builder(missing, "--row", "A165-TASK-1")
    assert rc == 2, err
    assert "missing-spec-root" in err, err


# ---------------------------------------------------------------- the gate arm pin
def test_gate_arm_runs_this_file() -> None:
    """The proof file must be named in tests/gate.sh's pytest arm or it never executes."""
    gate = REPO_ROOT / "tests" / "gate.sh"
    arm = [
        line
        for line in gate.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_brief_spec_context.py" in arm[0], (
        "tests/test_brief_spec_context.py must be in tests/gate.sh's pytest arm — a proof "
        "file outside it is never executed by the guard or CI"
    )
