"""AUG-200: one command from a board row id to the brief the builder sees.

The dispatch-path enrichment hook (`scripts/dispatch_spec_enrich.py`) resolves a
row's `detail.metadata.spec_query` through the REAL surfaces only —
`auger.build_spec_context` for governing blocks with closure and exclusion
reasons, `auger.recall` for decision provenance (tier-attributed, supersession
marked) — and embeds the row's own acceptance criteria beside them. Contracts:

  * a spec-bearing row -> a Markdown section naming block ids, provenance
    sources (decision id + store key + score), the exclusions WITH reasons, and
    the row's acceptance criteria verbatim; exit 0;
  * a row with no spec_query -> the brief is UNCHANGED (zero section bytes,
    never a placeholder), one honest stderr line, `no-spec` in the status,
    exit 0;
  * a malformed query, an unknown/duplicated row id, an explicitly-filed
    `spec_task` that names nothing real, or a substrate failure -> a typed
    refusal, exit 2, nothing invented;
  * an ambiguous spec context -> exit 3 with the candidate ids.

HERMETIC: no DuckBrain, no JEV, no network, no LLM. The corpus is built in
`tmp_path`; the substrate-facing calls (`recall`, `retrieval_tier`,
`supersession_for`, and — only for the ambiguous injection —
`build_spec_context`) are faked at the module seam.

ch:trace row=AUG-200 spec=docs/prd/xml-spec-graph.md#2-selection-and-assembly test=tests/test_dispatch_spec_enrich.py evidence=evidence/AUG-200/ verdict=none:not-judged commit=none:pre-commit
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

SCRIPT = REPO_ROOT / "scripts" / "dispatch_spec_enrich.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("dispatch_spec_enrich", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- fixture corpus
REQ = (
    '  <requirement id="A200-REQ-1" kind="requirement" status="active" version="1.0.0"\n'
    '               applies-to="auger.dispatch" verification="test" verifies="A200-VER-1">\n'
    "    The dispatch brief carries the spec context the row names.\n"
    "  </requirement>\n"
)
VER = (
    '  <verification id="A200-VER-1" kind="verification" status="active" version="1.0.0"\n'
    '                applies-to="auger.dispatch" method="test">The emitted section names\n'
    "  block ids and provenance sources.</verification>\n"
)
AC = (
    '  <acceptance id="A200-AC-1" kind="acceptance" status="active" version="1.0.0"\n'
    '              applies-to="auger.dispatch" verification="test" verifies="A200-REQ-1">\n'
    "    The section embeds the selected blocks verbatim.\n"
    "  </acceptance>\n"
)
TASK = (
    '  <task id="A200-TASK-1" kind="task" status="active" version="1.0.0"\n'
    '        applies-to="auger.dispatch" verification="test"\n'
    '        in-scope="A200-REQ-1" acceptance="A200-AC-1">\n'
    "    Enrich the dispatched brief with the row's spec query.\n"
    "  </task>\n"
)


def spec_document(body: str) -> str:
    return (
        '<spec xmlns="urn:auger:spec:1" id="A200-DISPATCH" version="1.0.0" schema-version="1.0">\n'
        f"{body}</spec>\n"
    )


def write_corpus(base: Path) -> Path:
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    (root / "dispatch.xml").write_text(
        spec_document(f"{REQ}{VER}{AC}{TASK}"), encoding="utf-8"
    )
    return root


def write_board(base: Path, *rows: dict) -> Path:
    board = base / "board" / "tasks.jsonl"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return board


def make_row(row_id: str, *, spec_query: dict | None = "DEFAULT", **overrides) -> dict:
    row = {
        "id": row_id,
        "status": "pending",
        "title": f"row {row_id}",
        "detail": {"metadata": {}, "fingerprint": "f" * 64},
        "priority": "P2",
        "acceptance_criteria": "AC1: the builder sees the governing decisions.",
        "type": "feat",
    }
    if spec_query == "DEFAULT":
        spec_query = {
            "project": "P-CHD-SPEC-SYSTEM",
            "recall": "spec query on board rows feeds the worker brief",
            "recall_limit": 3,
            "spec_task": "A200-TASK-1",
        }
    if spec_query is not None:
        row["detail"]["metadata"]["spec_query"] = spec_query
    row.update(overrides)
    return row


def run_hook(module, board: Path, corpus: Path, *args: str):
    """Drive the hook in-process; returns (rc, section_text, stderr_text, status_dict|None)."""
    out_path = Path(args[args.index("--out") + 1]) if "--out" in args else None
    status_path = (
        Path(args[args.index("--status-json") + 1]) if "--status-json" in args else None
    )
    argv = ["--board", str(board), "--spec-root", str(corpus), *args]
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = module.main(argv)
        except SystemExit as exc:  # argparse / fail-loud exits
            rc = exc.code if isinstance(exc.code, int) else 1
    section = (
        out_path.read_text(encoding="utf-8")
        if out_path and out_path.exists()
        else ("" if out_path else out.getvalue())
    )
    status = (
        json.loads(status_path.read_text(encoding="utf-8")) if status_path else None
    )
    return rc, section, err.getvalue(), status


# ---------------------------------------------------------------- fakes
@pytest.fixture()
def fake_substrate(monkeypatch):
    """The substrate-facing seam: recall hits, tier, supersession — no network."""
    calls = {}

    def fake_recall(ns, q, limit=5, missing_ok=False, project_id=None, timeout=60):
        calls["recall"] = {"ns": ns, "q": q, "limit": limit, "project_id": project_id}
        return [
            {
                "key": f"/auger/{project_id}/D-021",
                "score": 0.5,
                "content": "D-021: the row query convention is boardctl-compatible.",
            },
            {
                "key": f"/auger/{project_id}/D-022",
                "score": 0.492,
                "content": "D-022: supervisors enqueue work for foremen.",
            },
        ][:limit]

    def fake_tier(timeout=30):
        calls["tier"] = True
        return "semantic"

    def fake_supersession(ns, decision_id):
        return (True, "D-999") if decision_id == "D-021" else (False, "")

    monkeypatch.setattr(auger, "recall", fake_recall)
    monkeypatch.setattr(auger, "retrieval_tier", fake_tier)
    monkeypatch.setattr(auger, "supersession_for", fake_supersession)
    return calls


# ---------------------------------------------------------------- the context path
def test_spec_bearing_row_embeds_blocks_decisions_and_acceptance(
    tmp_path: Path, fake_substrate
) -> None:
    """Happy path: governing blocks VERBATIM + decision provenance + the row's own ACs."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(tmp_path, make_row("AUG-200"))
    out_path = tmp_path / "brief-section.md"
    status_path = tmp_path / "status.json"

    rc, section, err, status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-200",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 0, err
    assert "enriched brief for row AUG-200" in err, err

    # The same blocks the product verb selected, read through the module's own loader.
    answer = auger.build_spec_context(corpus, task="A200-TASK-1")
    assert answer["status"] == "ok", answer
    _r, _docs, blocks = auger.load_spec_corpus(corpus)
    texts = {b["id"]: b["text"] for b in blocks}

    header = section.splitlines()[0]
    assert header.startswith("## DISPATCH SPEC CONTEXT — row AUG-200"), header
    assert "project P-CHD-SPEC-SYSTEM" in header, header

    for entry in answer["selected"]:
        block_text = texts[entry["id"]]
        assert block_text in section, (
            f"block {entry['id']} text is not embedded verbatim"
        )
        assert f"#### {entry['id']} — {entry['kind']}" in section, entry["id"]

    for exc in answer["excluded"]:
        assert f"> excluded: {exc['id']}" in section, exc
        assert exc["reason"] in section, exc

    # Decision provenance: id, store key, score, tier attribution, supersession mark.
    assert "**D-021** [SUPERSEDED by D-999]" in section, section
    assert "(`/auger/P-CHD-SPEC-SYSTEM/D-021`)" in section, section
    assert "0.500" in section and "0.492" in section, section
    assert "retrieval tier: semantic" in section, section
    assert fake_substrate["recall"]["project_id"] == "P-CHD-SPEC-SYSTEM"
    assert fake_substrate["recall"]["limit"] == 3

    # The row's own acceptance contract rides in the section.
    assert "### Row acceptance criteria" in section
    assert "AC1: the builder sees the governing decisions." in section

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["outcome"] == "context"
    assert status["row"] == "AUG-200"
    assert status["selected"] == answer["counts"]["selected"]
    assert status["decision_ids"] == ["D-021", "D-022"]
    assert status["recall_hits"] == 2


def test_no_spec_query_leaves_brief_unchanged(tmp_path: Path, fake_substrate) -> None:
    """A row without spec_query: zero section bytes, honest marker, exit 0."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(
        tmp_path,
        make_row("AUG-100", spec_query=None),
        make_row("AUG-101", detail={"metadata": {}}),
        make_row("AUG-102", detail="legacy string detail"),
    )
    for row_id in ("AUG-100", "AUG-101", "AUG-102"):
        out_path = tmp_path / f"brief-{row_id}.md"
        status_path = tmp_path / f"status-{row_id}.json"
        rc, section, err, status = run_hook(
            module,
            board,
            corpus,
            "--row",
            row_id,
            "--out",
            str(out_path),
            "--status-json",
            str(status_path),
        )
        assert rc == 0, err
        assert section == "" and out_path.read_text(encoding="utf-8") == "", (
            "must not emit a placeholder"
        )
        assert "carries no spec_query" in err, err
        assert status["outcome"] == "no-spec", status


# ---------------------------------------------------------------- refusal paths
def test_malformed_spec_query_is_a_typed_refusal(
    tmp_path: Path, fake_substrate
) -> None:
    """Unknown keys and a recall without its project refuse loudly — never guess."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    out_path = tmp_path / "brief.md"
    status_path = tmp_path / "status.json"

    board = write_board(
        tmp_path,
        make_row(
            "AUG-110", spec_query={"querry": "typo", "recall": "x", "project": "p"}
        ),
    )
    rc, _s, err, status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-110",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 2, err
    assert "unknown key(s): querry" in err, err
    assert out_path.read_text(encoding="utf-8") == ""
    assert status["outcome"] == "refused"

    status_path.write_text("{}", encoding="utf-8")
    board2 = write_board(
        tmp_path / "b2",
        make_row("AUG-111", spec_query={"recall": "x", "recall_limit": 2}),
    )
    rc, _s, err, status = run_hook(
        module,
        board2,
        corpus,
        "--row",
        "AUG-111",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 2, err
    assert "requires a non-empty 'project'" in err, err

    board3 = write_board(
        tmp_path / "b3",
        make_row("AUG-112", spec_query={"spec_task": "t", "recall_limit": 0}),
    )
    rc, _s, err, _status = run_hook(
        module,
        board3,
        corpus,
        "--row",
        "AUG-112",
        "--out",
        str(out_path),
    )
    assert rc == 2, err
    assert "recall_limit" in err, err


def test_unknown_spec_task_surfaces_instead_of_silent_empty_brief(
    tmp_path: Path, fake_substrate
) -> None:
    """An explicitly-filed spec_task that names nothing real must exit 2, not 0."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(
        tmp_path,
        make_row("AUG-120", spec_query={"spec_task": "NO-SUCH-TASK"}),
    )
    out_path = tmp_path / "brief.md"
    status_path = tmp_path / "status.json"
    rc, _s, err, status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-120",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 2, err
    assert "status=unscoped" in err and "unknown-task" in err, err
    assert out_path.read_text(encoding="utf-8") == ""
    assert status["outcome"] == "refused" and status["reason"] == "unknown-task"


def test_ambiguous_context_demands_exit_3(tmp_path: Path, monkeypatch) -> None:
    """The ambiguous answer is injected at the product seam; candidates hit stderr."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(
        tmp_path,
        make_row("AUG-130", spec_query={"spec_task": "A200-TASK-1"}),
    )
    ambiguous = {
        "status": "ambiguous",
        "reason": "ambiguous-match",
        "candidates": ["A200-TASK-1", "A200-TASK-2"],
        "selected": [],
        "excluded": [],
        "counts": {"selected": 0, "dependencies": 0, "input_bytes": 10},
    }
    monkeypatch.setattr(auger, "build_spec_context", lambda *a, **k: dict(ambiguous))
    out_path = tmp_path / "brief.md"
    status_path = tmp_path / "status.json"
    rc, _s, err, status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-130",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 3, err
    assert "A200-TASK-1, A200-TASK-2" in err, err
    assert out_path.read_text(encoding="utf-8") == ""
    assert status["outcome"] == "ambiguous" and status["candidates"] == [
        "A200-TASK-1",
        "A200-TASK-2",
    ]


def test_substrate_failure_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    """AUG-075: a store that cannot be asked is an error — refusal with its own words."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(
        tmp_path,
        make_row("AUG-140", spec_query={"project": "P-X", "recall": "q"}),
    )
    monkeypatch.setattr(auger, "retrieval_tier", lambda timeout=30: "semantic")
    monkeypatch.setattr(
        auger,
        "recall",
        lambda *a, **k: (_ for _ in ()).throw(
            auger.SubstrateError("substrate read failed for 'auger' (status 0): down")
        ),
    )
    out_path = tmp_path / "brief.md"
    status_path = tmp_path / "status.json"
    rc, _s, err, status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-140",
        "--out",
        str(out_path),
        "--status-json",
        str(status_path),
    )
    assert rc == 2, err
    assert "substrate read failed" in err, err
    assert out_path.read_text(encoding="utf-8") == ""
    assert status["outcome"] == "refused"


def test_row_not_found_and_duplicate_rows_refuse(
    tmp_path: Path, fake_substrate
) -> None:
    """Zero or many physical rows with the id: typed refusals naming the lines."""
    module = _load_script()
    corpus = write_corpus(tmp_path)
    board = write_board(tmp_path, make_row("AUG-150"))
    out_path = tmp_path / "brief.md"
    rc, _s, err, _status = run_hook(
        module,
        board,
        corpus,
        "--row",
        "AUG-999",
        "--out",
        str(out_path),
    )
    assert rc == 2 and "not found" in err, err

    dup = write_board(tmp_path / "dup", make_row("AUG-150"), make_row("AUG-150"))
    rc, _s, err, _status = run_hook(
        module,
        dup,
        corpus,
        "--row",
        "AUG-150",
        "--out",
        str(out_path),
    )
    assert rc == 2 and "multiple lines" in err, err


# ---------------------------------------------------------------- the pure decision
def test_plan_section_is_separable_and_pure() -> None:
    """Four outcome classes, no corpus, no substrate: (section, status, exit_code)."""
    module = _load_script()
    query = {"project": "P", "recall": "q", "recall_limit": 5, "spec_task": "T-1"}
    answer = {
        "status": "ok",
        "reason": "",
        "candidates": [],
        "counts": {"selected": 1, "dependencies": 0, "input_bytes": 4242},
        "selected": [{"id": "X-1", "kind": "requirement"}],
        "excluded": [{"id": "Y-1", "reason": "out-of-scope"}],
    }
    hits = [{"key": "/auger/P/D-1", "score": 0.4, "content": "c"}]

    section, status, rc = module.plan_section(
        "ROW-1",
        query,
        "AC1: ship it",
        answer,
        hits,
        {"X-1": "the prose"},
        "semantic",
        namespace="",
    )
    assert rc == 0
    assert "the prose" in section and "input 4242 B" in section
    assert "> excluded: Y-1 — out-of-scope" in section
    assert "AC1: ship it" in section and "**D-1**" in section
    assert status["outcome"] == "context" and status["decision_ids"] == ["D-1"]

    empty_ctx = {**answer, "selected": [], "excluded": []}
    section, status, rc = module.plan_section(
        "ROW-1",
        query,
        None,
        empty_ctx,
        [],
        {},
        None,
        namespace="",
    )
    assert section == "" and rc == 0, "an explicitly empty answer is exit 0, surfaced"
    assert status["outcome"] == "no-context"

    amb = {
        **answer,
        "status": "ambiguous",
        "reason": "ambiguous-match",
        "candidates": ["A-1", "A-2"],
    }
    section, status, rc = module.plan_section(
        "ROW-1",
        query,
        None,
        amb,
        None,
        {},
        None,
        namespace="",
    )
    assert section == "" and rc == 3
    assert status["outcome"] == "ambiguous"

    for bad_status, why in (
        ("unscoped", "unknown-task"),
        ("inactive", "block-inactive"),
    ):
        bad = {**answer, "status": bad_status, "reason": why, "selected": []}
        section, status, rc = module.plan_section(
            "ROW-1",
            query,
            None,
            bad,
            hits,
            {},
            None,
            namespace="",
        )
        assert section == "" and rc == 2, (
            f"an explicit {bad_status} query must refuse, not silently emit"
        )
        assert status["outcome"] == "refused"

    section, status, rc = module.plan_section(
        "ROW-1",
        None,
        None,
        None,
        None,
        {},
        None,
        namespace="",
    )
    assert section == "" and rc == 0 and status["outcome"] == "no-spec"
