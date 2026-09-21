"""
Regression coverage for `auger`'s public verbs, against a live ephemeral DuckBrain
namespace — the pytest replacement for shell-only coverage.

`tests/smoke.sh` proves the loop end to end with 15 assertions on STDOUT and knowingly
leaves its namespace behind. It cannot assert on what was actually STORED, it cannot run a
single case, and a leak is one namespace per run. This suite keeps the same ground and moves
the assertions one layer down, onto the rows:

  * after `answer`        -> re-read the `decision` row and its `option` rows over the API
  * after `toggle`        -> re-read and prove `active` actually flipped
  * after `dump --config` -> prove the stored flags did NOT change (non-mutation, on data)

The suite never hardcodes the module's table count: it reads `len(auger.COLS)` and the names
the API reports back, so it is correct whether the module declares 9 tables or 11 (AUG-007
adds `edge` and `facet` in a sibling worktree).

Tests that call JEV are marked `@pytest.mark.jev`. That marker means "this case exercises the
external decisions model", which can be unreachable for reasons that have nothing to do with
this repo; those cases skip loudly naming the reason. Everything unmarked is deterministic and
must pass every run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

import auger
from conftest import (
    CREATED,
    D001,
    D002,
    REPO_ROOT,
    TEST_NS_PREFIX,
    api_namespaces,
    leftovers,
    ns_path,
    run_cli,
    run_cli_exit,
    teardown_namespace,
)

# ---------------------------------------------------------------- helpers
_JEV_PROBE: dict = {}


def jev_or_skip() -> None:
    """Fail loudly (or skip loudly) when the decisions model cannot be reached.

    `auger check` is fail-closed: with JEV down it prints an explicit UNKNOWN instead of a
    verdict, so a test asserting a verdict cannot be honest without the model.
    """
    if "err" not in _JEV_PROBE:
        _ans, err = auger.jev("PROBE", {"already_answered": {"type": "noul",
                                                            "instructions": "Is this already answered?"}})
        _JEV_PROBE["err"] = err
        _JEV_PROBE["keys"] = len(auger._jev_keys())
    err = _JEV_PROBE["err"]
    if err:
        msg = (f"JEV is unreachable ({err}; {_JEV_PROBE['keys']} key(s) tried) — `auger check` is "
               "fail-closed, so no verdict can be asserted. Remedy: a live OpenRouter key in "
               "~/.hermes/.env or the OPENROUTER_API_KEY environment variable.")
        if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
            raise AssertionError(msg)
        pytest.skip(msg)


def rows(ns: str, table: str, query: str) -> list[dict]:
    return auger.select(ns, table, query)


def row(ns: str, table: str, query: str) -> dict:
    """Exactly one stored row, or a failure naming what came back instead."""
    found = rows(ns, table, query)
    assert len(found) == 1, f"expected exactly one {table} row for [{query}], got {found!r}"
    return found[0]


def option_flags(ns: str) -> dict[str, bool]:
    """Snapshot every option's `active` flag: the data `dump --config` must not touch."""
    return {o["id"]: bool(o.get("active")) for o in rows(ns, "option", "order=id.asc")}


def ns_name() -> str:
    """A fresh test namespace NAME. Creating it is the caller's job."""
    return TEST_NS_PREFIX + uuid.uuid4().hex[:12]


def new_bare_ns() -> str:
    """A namespace this test creates over the API and is then responsible for removing."""
    ns = ns_name()
    status, body, _ = auger.db("/api/namespaces", "POST", {"name": ns})
    assert status in (200, 201), body
    return ns


# ================================================================= init
def test_init_declares_every_table_the_module_defines(ns: str):
    """`init` reports N/N for the N tables the module declares — never a hardcoded count."""
    declared = len(auger.COLS)
    rc, out = run_cli(["-n", ns, "init"])
    assert rc == 0
    # Re-running init is idempotent and still reports the full set.
    assert f"declared: {declared}/{declared} tables" in out, out
    assert "WARNING" not in out, out

    # The declarations are real: the API serves exactly the tables the module declares.
    status, live, _ = auger.db(f"/api/ns/{ns}/tables")
    assert status == 200, live
    served = {t["name"] for t in live["tables"]}
    assert served == set(auger.COLS), \
        f"API serves {sorted(served)}, module declares {sorted(auger.COLS)}"
    assert declared >= 9  # the nine SDM registers are the floor, not a fixed count


def test_init_creates_a_namespace_the_api_then_lists(live_service: str):
    """`init` creates what it is pointed at. The assertion is on the registry, not a word."""
    ns = ns_name()
    assert ns not in api_namespaces()
    try:
        rc, out = run_cli(["-n", ns, "init"])
        assert rc == 0
        assert f"namespace {ns}: created" in out, out
        assert ns in api_namespaces()
    finally:
        assert teardown_namespace(ns) == []


# ================================================================= start / answer -> stored rows
def test_start_stores_the_project_row_and_embeds_the_seed(project: dict):
    """The seed is in the `project` row and in the embedding index, not only in stdout."""
    ns = project["ns"]
    p = row(ns, "project", f"id=eq.{project['pid']}")
    assert p["name"] == "pytesttest"
    assert p["status"] == "open"
    assert p["seed"].strip() == project["seed"].strip()

    # SUBSTRATE QUIRK (observed, not assumed): DuckBrain renders a declared `timestamp`
    # column as `{}` over the API while the JSONL on disk holds the real ISO value. Assert
    # the column is projected here, and assert the VALUE where the storage of record keeps
    # it — the design leans on the namespace being readable as plain JSONL.
    assert "created_at" in p
    disk_row = None
    with open(os.path.join(ns_path(ns), "tables", "project.jsonl")) as fh:
        for line in fh:
            if json.loads(line)["id"] == project["pid"]:
                disk_row = json.loads(line)
    assert disk_row is not None, f"{project['pid']} is not in tables/project.jsonl"
    assert disk_row["created_at"], "created_at was stored empty"
    assert disk_row["created_at"].startswith("20"), disk_row["created_at"]

    hits = auger.recall(ns, "watches directories and posts an hourly digest", limit=5)
    assert hits, "the seed was not embedded — semantic retrieval found nothing"
    assert any(h.get("key") == f"/auger/{project['pid']}/seed" for h in hits), \
        f"the seed evidence key was not retrievable: {[h.get('key') for h in hits]}"


def test_answer_writes_every_decision_field_to_the_row(decided: dict):
    """Every field `answer` was given comes back off the STORED row."""
    d = row(decided["ns"], "decision", "id=eq.D-001")
    assert d["project_id"] == decided["pid"]
    assert d["domain"] == D001["domain"]
    assert d["chosen"] == D001["chosen"]
    assert d["why_not"] == D001["why_not"]
    assert d["confidence"] == pytest.approx(D001["confidence"])
    assert d["status"] == "decided"
    assert d["evidence_key"] == f"/auger/{decided['pid']}/D-001"


def test_answer_makes_the_chosen_option_the_only_active_one(decided: dict):
    """The `active` flags are derived when the option rows are written."""
    ns, did = decided["ns"], "D-001"
    opts = rows(ns, "option", f"decision_id=eq.{did}&order=id.asc")
    assert [o["id"] for o in opts] == [f"{did}-O1", f"{did}-O2"]
    assert [o["label"] for o in opts] == D001["options"]
    assert [bool(o["active"]) for o in opts] == [True, False], opts

    # `chosen` and the flags cannot disagree in the stored record.
    assert [o["label"] for o in opts if o["active"]] == [row(ns, "decision", f"id=eq.{did}")["chosen"]]


def test_answer_tolerates_an_apostrophe_in_the_reason(decided: dict):
    """The module's headline rule: payloads reach the API as bytes, never shell-quoted."""
    tricky = "Postgres's service is what the seed's rule forbids"
    rc, out = run_cli(["-n", decided["ns"], "answer", "--id", "D-003", "--domain", "4.07",
                       "--chosen", "embedded index", "--option", "embedded index",
                       "--option", "server-backed index", "--why-not", tricky,
                       "--confidence", "0.7"])
    assert rc == 0 and "D-003 recorded" in out, out
    assert row(decided["ns"], "decision", "id=eq.D-003")["why_not"] == tricky


def test_answer_without_a_project_is_refused(live_service: str):
    """With no project row the verb refuses explicitly instead of writing a dangling decision."""
    ns = new_bare_ns()
    try:
        run_cli(["-n", ns, "init"])
        code, msg, _ = run_cli_exit(["-n", ns, "answer", "--chosen", "x", "--confidence", "0.5"])
        assert code != 0
        assert "no project row" in msg, msg
        assert rows(ns, "decision", "") == []
    finally:
        assert teardown_namespace(ns) == []


# ================================================================= status
def test_status_counts_the_stored_rows(decided: dict):
    """`status` counts what is stored; the stored rows are the assertion."""
    rc, out = run_cli(["-n", decided["ns"], "status"])
    assert rc == 0
    assert "decisions 2 |" in out, out

    stored = rows(decided["ns"], "decision", f"project_id=eq.{decided['pid']}")
    assert len(stored) == 2
    thin = [d["id"] for d in stored if d["confidence"] < auger.T_CONFIDENT]
    assert thin == ["D-002"]
    # The thin decision is surfaced by id with its real confidence — "needs drilling".
    assert "needs drilling" in out, out
    assert "D-002" in out and f"{D002['confidence']:.2f}" in out, out


# ================================================================= dump: current, hypothetical, output
def test_dump_renders_the_stored_configuration(decided: dict):
    rc, out = run_cli(["-n", decided["ns"], "dump"])
    assert rc == 0
    assert "mode: current stored state" in out, out
    assert f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002={D002['chosen']}" in out, out
    for d in (D001, D002):
        assert f"{d['did']}  ({d['domain']})" in out, out
        assert d["why_not"] in out, out


def test_dump_config_does_not_mutate_the_stored_rows(decided: dict):
    """The what-if is a projection: assert it on the DATA, not on a printed marker."""
    ns = decided["ns"]
    before = option_flags(ns)
    assert before["D-002-O1"] is True and before["D-002-O2"] is False

    rc, out = run_cli(["-n", ns, "dump", "--config", "D-002=row locking"])
    assert rc == 0 and "HYPOTHETICAL" in out, out

    after = option_flags(ns)
    assert after == before, f"dump --config mutated stored options: {before} -> {after}"
    # The decision row is untouched too, and no option row was added or lost.
    assert row(ns, "decision", "id=eq.D-002")["chosen"] == D002["chosen"]
    assert len(rows(ns, "option", "")) == len(before)


def test_dump_config_names_the_contradiction_with_the_recorded_reason(decided: dict):
    """A hypothesis contradicting the record is named, quoting the reason actually on file."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "dump", "--config", "D-002=row locking"])
    assert rc == 0

    assert "CONTRADICTIONS WITH THE RECORD" in out, out
    why_not = row(ns, "decision", "id=eq.D-002")["why_not"]  # read it, do not hardcode it
    assert why_not == D002["why_not"]
    assert why_not in out, f"the recorded reason {why_not!r} was not quoted:\n{out}"
    assert "row locking contradicts the recorded choice (staging table)" in out, out

    # The control: the CURRENT configuration is not reported as a contradiction.
    rc, current = run_cli(["-n", ns, "dump"])
    assert rc == 0
    assert "CONTRADICTIONS WITH THE RECORD" not in current, current


def test_dump_config_warns_on_an_option_that_does_not_exist(decided: dict):
    rc, out = run_cli(["-n", decided["ns"], "dump", "--config", "D-002=nonexistent option"])
    assert rc == 0
    assert "WARNING: unparsed --config entries ignored" in out, out
    assert "no such option for D-002" in out, out


def test_dump_out_writes_the_file_it_reports(decided: dict, tmp_path):
    target = tmp_path / "dump.txt"
    rc, out = run_cli(["-n", decided["ns"], "dump", "--out", str(target)])
    assert rc == 0 and f"wrote {target}" in out, out

    text = target.read_text()
    assert f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002={D002['chosen']}" in text, text
    # The file is the same projection the stdout mode prints (its first line is not a stub).
    _rc, printed = run_cli(["-n", decided["ns"], "dump"])
    assert text.splitlines()[0] == printed.splitlines()[0] == "# pytesttest — configuration dump"


# ================================================================= toggle
def test_toggle_flips_the_stored_active_flags(decided: dict):
    """The switch actually writes: re-read the rows and check the flags flipped."""
    ns = decided["ns"]
    before = option_flags(ns)

    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-002-O1", "--on", "D-002-O2"])
    assert rc == 0 and "toggled:" in out, out
    assert "D-002-O1->off (1)" in out and "D-002-O2->on (1)" in out, out

    after = option_flags(ns)
    assert after["D-002-O1"] is False
    assert after["D-002-O2"] is True
    untouched = {k: v for k, v in after.items() if k.startswith("D-001")}
    assert untouched == {k: v for k, v in before.items() if k.startswith("D-001")}

    rc, out = run_cli(["-n", ns, "dump"])
    assert f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002=row locking" in out, out


def test_toggle_set_parses_on_and_off(decided: dict):
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--set", "D-002-O1=off", "--set", "D-002-O2=on"])
    assert rc == 0
    assert "D-002-O1->off (1)" in out and "D-002-O2->on (1)" in out, out
    flags = option_flags(ns)
    assert flags["D-002-O1"] is False and flags["D-002-O2"] is True


def test_toggle_with_no_arguments_changes_nothing(decided: dict):
    ns = decided["ns"]
    before = option_flags(ns)
    rc, out = run_cli(["-n", ns, "toggle"])
    assert rc == 0 and "(nothing" in out, out
    assert option_flags(ns) == before


# ================================================================= check (the JEV-dependent verb)
@pytest.mark.jev
def test_check_retrieves_the_rows_this_suite_stored_and_reaches_one_verdict(decided: dict):
    """The ground smoke.sh cannot assert on: retrieval finds the rows THIS suite wrote."""
    jev_or_skip()
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "check", "How should we store the record of files we have seen?"])
    assert rc == 0, out

    assert "nearest stored rows (" in out, out
    keys = {f"/auger/{decided['pid']}/D-001", f"/auger/{decided['pid']}/D-002"}
    assert any(k in out for k in keys), f"retrieval did not return the stored evidence:\n{out}"
    assert sum(v in out for v in ("ALREADY ANSWERED", "NOT YET ANSWERED")) == 1, \
        f"no single verdict in the output:\n{out}"


@pytest.mark.jev
def test_check_rejects_a_question_the_evidence_does_not_cover(decided: dict):
    """The fail-side: an unrelated question is not waved through as already answered."""
    jev_or_skip()
    rc, out = run_cli(["-n", decided["ns"], "check",
                       "What message queue brokers the watcher to the digest sender?"])
    assert rc == 0, out
    assert "NOT YET ANSWERED" in out, out


def test_check_with_no_stored_evidence_says_so(live_service: str):
    """An empty namespace short-circuits before the model — deterministic, no JEV needed."""
    ns = new_bare_ns()
    try:
        rc, out = run_cli(["-n", ns, "check", "What colour is the database?"])
        assert rc == 0
        assert "no stored evidence matched" in out, out
    finally:
        assert teardown_namespace(ns) == []


def test_check_is_fail_closed_when_the_model_is_unreachable(ns: str, monkeypatch):
    """JEV down must produce an explicit UNKNOWN and a non-zero exit, never a verdict."""
    monkeypatch.setattr(auger, "jev",
                        lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401"))
    auger.remember(ns, "/probe/evidence", "We chose a single SQLite file for the record store.")
    rc, out = run_cli(["-n", ns, "check", "How is the record stored?"])
    assert rc == 1, out
    assert "JEV unavailable" in out and "fail-closed" in out, out
    assert "ALREADY ANSWERED" not in out, out


def test_check_maps_the_model_answer_to_the_threshold_verdict(ns: str, monkeypatch):
    """The thresholds live in this module, not in the model — pin that with a fixed answer."""
    def stub(value: float):
        return lambda *a, **k: ({"answers": {"already_answered": {"type": "noul", "noul": value}},
                                 "usage": {"cost": 1e-05}, "model": "stub"}, None)

    auger.remember(ns, "/probe/evidence", "We chose a single SQLite file for the record store.")

    monkeypatch.setattr(auger, "jev", stub(0.91))
    rc, out = run_cli(["-n", ns, "check", "How is the record stored?"])
    assert rc == 0, out
    assert "ALREADY ANSWERED" in out and f"threshold {auger.T_ANSWERED}" in out, out

    # One hundredth below the threshold the same model answer flips the verdict.
    monkeypatch.setattr(auger, "jev", stub(auger.T_ANSWERED - 0.01))
    rc, out = run_cli(["-n", ns, "check", "How is the record stored?"])
    assert rc == 0, out
    assert "NOT YET ANSWERED" in out, out


# ================================================================= propagate (SPEC-001 BEAT 4)
# Closure and moot cascades. The walk is driven by stored rows and stored edges, so each case
# builds its graph over the module's own helpers (`auger.insert` for question nodes — the CLI has
# no verb that writes one yet, that is BEAT 1 — and `auger.edge` for the relationships).
Q_WATCH = "What does the watcher record about the files it has seen?"
Q_STORE = "Should the watcher's record be a single SQLite file or a Postgres database?"
Q_CRASH = "What happens to the digest if the watcher crashes halfway through a run?"


def store_questions(ns: str, pid: str, *specs) -> None:
    """Store question rows: (id, text, status). The node the walks move."""
    for qid, text, status in specs:
        auger.insert(ns, "question", {
            "id": qid, "project_id": pid, "domain": "4.05", "text": text, "ring": 1,
            "qclass": "", "status": status, "jev_already_answered": -1.0, "jev_checked_at": ""})


def gate_stub(noul: float, calls: list):
    """A JEV stub that answers the one question the gate asks, and counts its calls."""
    def _stub(state, questions, *a, **k):
        calls.append(state)
        return ({"answers": {"already_answered": {"type": "noul", "noul": noul}},
                 "usage": {"cost": 1e-05}, "model": "stub"}, None)
    return _stub


def answer_cli(ns: str, did: str, domain: str, chosen: str, qid: str = "") -> tuple[int, str]:
    argv = ["-n", ns, "answer", "--id", did, "--domain", domain, "--chosen", chosen,
            "--option", chosen, "--option", "something else", "--why-not", "the seed forbids it",
            "--confidence", "0.8"]
    if qid:
        argv += ["--question-id", qid]
    return run_cli(argv)


def test_propagate_moots_the_question_a_breaks_edge_invalidates(project: dict):
    """Criterion (a): the question the invalidated decision answered is MOOT, and the reason
    naming the breaking edge is on the record — on the question's own rows, with its row intact."""
    ns, pid = project["ns"], project["pid"]
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "answered"))
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    assert answer_cli(ns, "D-002", "4.09", "an embedded index")[0] == 0
    inv = auger.edge(ns, pid, "breaks", "decision", "D-002", "decision", "D-001", source="human",
                     note="the embedded index makes the SQLite file the wrong store")
    assert inv["id"] == "E-000001", inv

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "moot: 1   reopened: 0   linked: 0" in out, out

    q = row(ns, "question", "id=eq.Q-000001")
    assert q["status"] == "moot"
    assert q["text"] == Q_WATCH, "a moot question keeps its row — it is never deleted"

    reason = "D-001 was invalidated by E-000001"
    assert auger.q_reason(ns, "Q-000001") == reason
    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets
    assert all(f["status"] == "closed" and f["closed_by"] == "D-001" and f["note"] == reason
               for f in facets), facets
    assert reason in out, out

    # Nothing was asked again and nothing else moved.
    assert auger.askable_questions(ns, pid) == []
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 2
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 1
    assert row(ns, "decision", "id=eq.D-002")["chosen"] == "an embedded index"


def test_propagate_reopens_the_stale_branch_and_reaches_the_grandchild(project: dict, monkeypatch):
    """Criterion (b): the change runs down the derives_from tree. The question the dead decision
    answered goes moot, the settled questions below it REOPEN with the reason recorded (Q4), and
    the walk is not one hop deep — the grandchild is reached. A second pass changes nothing."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.05, calls))
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "answered"),
                    ("Q-000002", Q_STORE, "linked"), ("Q-000003", Q_CRASH, "linked"))
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    assert answer_cli(ns, "D-002", "4.09", "an embedded index")[0] == 0
    auger.edge(ns, pid, "breaks", "decision", "D-002", "decision", "D-001", source="human",
               note="the embedded index makes the SQLite file the wrong store")
    auger.edge(ns, pid, "derives_from", "question", "Q-000002", "question", "Q-000001",
               source="rule", note="the storage question only exists once the record question is asked")
    auger.edge(ns, pid, "derives_from", "question", "Q-000003", "question", "Q-000002",
               source="rule", note="the crash question descends from the storage question")

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "moot: 1   reopened: 2   linked: 0" in out, out

    assert row(ns, "question", "id=eq.Q-000001")["status"] == "moot"
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open", "the stale branch did not reopen"
    assert row(ns, "question", "id=eq.Q-000003")["status"] == "open", "the cascade stopped one hop short"

    cause = "D-001 was invalidated by E-000001"
    child, grandchild = auger.q_reason(ns, "Q-000002"), auger.q_reason(ns, "Q-000003")
    assert "reopened" in child and "Q-000001" in child and cause in child, child
    assert "reopened" in grandchild and "Q-000002" in grandchild and cause in grandchild, grandchild
    assert child in out and grandchild in out, out

    # Both are settled questions that went stale, so both are askable again — and neither was
    # asked: the gate re-examined the reopened child once and left it open.
    assert [q["id"] for q in auger.askable_questions(ns, pid)] == ["Q-000002", "Q-000003"]
    assert len(calls) == 1, calls

    rc, out2 = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out2
    assert "moot: 0   reopened: 0   linked: 0" in out2, out2
    assert len(calls) == 1, "a second pass re-gated a question the gate had already examined"
    assert auger.q_reason(ns, "Q-000001") == cause, "a moot question keeps the reason it was given"


def test_ask_never_surfaces_a_question_with_an_unresolved_blocker(project: dict, monkeypatch):
    """Criterion (c): the askable surface honours the blocks edge, and the control proves the
    block — not a bug — is what held the question back."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "open"),
                    ("Q-000002", Q_STORE, "open"), ("Q-000003", Q_CRASH, "open"))
    auger.edge(ns, pid, "blocks", "question", "Q-000002", "question", "Q-000001", source="rule",
               note="the storage choice cannot be made before the record is defined")

    assert [q["id"] for q in auger.blocked_questions(ns, pid)] == ["Q-000002"]
    assert [q["id"] for q in auger.askable_questions(ns, pid)] == ["Q-000001", "Q-000003"]

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "Q-000003" in out and Q_CRASH in out, out          # the unblocked question is surfaced
    assert "blocked by an unresolved question: 1" in out, out
    assert "Q-000002" not in out, out                          # the blocked one is not surfaced
    assert Q_STORE not in out, out
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open", "not surfaced is not deleted"

    # The control: settle the blocker and the very same question becomes askable.
    auger.set_state(ns, pid, "Q-000001", "answered", "answered by D-001", closed_by="D-001")
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "blocked by an unresolved question: 0" in out, out
    assert "Q-000002" in out and Q_STORE in out, out


def test_propagate_links_a_question_the_gate_can_answer_after_its_parent_closes(
        project: dict, monkeypatch):
    """Criterion (d): once the parent closes, the child is re-examined and LINKED to the answer we
    already hold instead of being asked again — and it is not re-examined before that."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.91, calls))
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "open"), ("Q-000002", Q_STORE, "open"))
    auger.edge(ns, pid, "derives_from", "question", "Q-000002", "question", "Q-000001",
               source="rule", note="the storage question only exists once the record question is asked")
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    assert auger.recall(ns, Q_STORE, limit=5), \
        "the substrate returned no evidence for the stored decision — nothing could be linked"

    # CONTROL: while the parent is open the child is not re-examined, and nothing is linked.
    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert calls == [], "the gate ran on a question whose parent had not closed"
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open"
    assert rows(ns, "edge", "kind=eq.satisfies") == []

    # The parent closes (BEAT 2's job), and PROPAGATE re-examines the child.
    auger.set_state(ns, pid, "Q-000001", "answered", "answered by D-001", closed_by="D-001")
    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert len(calls) == 1, calls
    assert "linked: 1" in out and "D-001" in out, out

    q2 = row(ns, "question", "id=eq.Q-000002")
    assert q2["status"] == "linked", "the gate linked it instead of asking it"
    assert q2["jev_already_answered"] == pytest.approx(0.91), q2
    assert q2["jev_checked_at"], q2

    sat = rows(ns, "edge", "kind=eq.satisfies")
    assert len(sat) == 1, sat
    assert (sat[0]["src_kind"], sat[0]["src_id"]) == ("decision", "D-001")
    assert (sat[0]["dst_kind"], sat[0]["dst_id"]) == ("question", "Q-000002")
    assert sat[0]["source"] == "gate" and sat[0]["confidence"] == pytest.approx(0.91)
    assert "D-001" in auger.q_reason(ns, "Q-000002")

    # Nothing was asked: no new question, no new decision, and a linked question is not on the
    # askable surface either.
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 1
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 2
    assert auger.askable_questions(ns, pid) == []
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "Q-000002" not in out, out


# ================================================================= exit codes (subprocess is the subject)
def test_unknown_verb_exits_non_zero():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "auger.py"), "frobnicate"],
                          capture_output=True, text=True)
    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr or "frobnicate" in proc.stderr


def test_a_successful_verb_exits_zero_in_a_subprocess(project: dict):
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "auger.py"),
                           "-n", project["ns"], "status"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "decisions" in proc.stdout


# ================================================================= the broken-verb trap
def test_patch_reporting_zero_updates_leaves_the_flag_unchanged(decided: dict, monkeypatch):
    """Break the write path; prove the consequence is observable on the stored ROW.

    `auger.patch` returning `{"updated": 0}` is exactly what a silently-broken toggle looks
    like: the verb still exits 0, so only the stored row can tell you nothing was written.
    """
    ns = decided["ns"]
    monkeypatch.setattr(auger, "patch", lambda *a, **k: {"updated": 0})
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0
    assert "D-001-O1->off (0)" in out, out          # the verb reports zero rows updated
    assert option_flags(ns)["D-001-O1"] is True     # and nothing actually flipped

    monkeypatch.undo()
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0 and "D-001-O1->off (1)" in out, out
    assert option_flags(ns)["D-001-O1"] is False    # a working toggle does flip it


#: The child suite: one test per verb, with the toggle verb's write deliberately broken.
BROKEN_VERB_SUITE = '''
import auger

# The deliberate break, and nothing else: the toggle's write reports "nothing updated", so
# the toggle is silently a no-op. The other three verbs under test are untouched.
auger.patch = lambda *a, **k: {"updated": 0}


def test_alpha_toggle_flips_the_stored_flag(decided):
    from conftest import run_cli
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0 and "toggled:" in out, out
    flags = {o["id"]: bool(o.get("active")) for o in auger.select(ns, "option", "")}
    assert flags["D-001-O1"] is False, "the toggle reported success but wrote nothing"


def test_beta_dump_config_does_not_mutate(decided):
    from conftest import run_cli
    ns = decided["ns"]
    before = {o["id"]: bool(o.get("active")) for o in auger.select(ns, "option", "")}
    run_cli(["-n", ns, "dump", "--config", "D-002=row locking"])
    after = {o["id"]: bool(o.get("active")) for o in auger.select(ns, "option", "")}
    assert after == before


def test_gamma_answer_row_is_stored(decided):
    assert auger.select(decided["ns"], "decision", "id=eq.D-001")[0]["chosen"] == "single SQLite file"


def test_delta_status_counts_the_rows(decided):
    from conftest import run_cli
    rc, out = run_cli(["-n", decided["ns"], "status"])
    assert rc == 0 and "decisions 2 |" in out, out
'''


def test_a_broken_verb_fails_exactly_one_named_test(tmp_path, live_service):
    """The acceptance criterion, proved by RUNNING a suite with one verb deliberately broken.

    A child suite of four deterministic tests covers four verbs. Only the toggle's write is
    broken. The observed result must be EXACTLY ONE failure, and it must be the test that
    names the toggle: a zero means the suite never observed the broken write, and a cascade is
    the signature of one failing test corrupting the next (shared state, order dependence).
    Both fail this test.
    """
    child = tmp_path / "childtests"
    child.mkdir()
    nonce = uuid.uuid4().hex[:8]
    # The child gets its OWN conftest.py (copied, not imported) and its own rootdir via -c, so
    # it can never pick up this session's already-imported `conftest` module or its fixtures.
    shutil.copy(str(REPO_ROOT / "tests" / "conftest.py"), str(child / "conftest.py"))
    (child / f"test_broken_{nonce}.py").write_text(BROKEN_VERB_SUITE)
    (child / "pytest-child.ini").write_text("[pytest]\n")

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", "pytest-child.ini", f"test_broken_{nonce}.py",
         "-p", "no:cacheprovider", "-q", "--no-header", "-rf"],
        cwd=str(child), capture_output=True, text=True, env=env)
    report = proc.stdout + proc.stderr
    print("\n" + report)  # auditable under -s, and on failure

    assert "1 failed, 3 passed" in report, f"expected exactly one failure, got:\n{report}"
    failed = [line for line in report.splitlines() if line.startswith("FAILED")]
    assert len(failed) == 1, f"expected exactly one FAILED line, got {failed}:\n{report}"
    assert "test_alpha_toggle_flips_the_stored_flag" in failed[0], failed
    assert "wrote nothing" in report, report  # the failure names the broken behaviour
    assert "3 passed" in report, report


# ================================================================= teardown
def test_teardown_removes_both_the_registry_entry_and_the_directory(ns: str):
    """The teardown is proved here instead of being trusted: run it, then look."""
    from conftest import ns_path, teardown_namespace as take_down

    assert os.path.isdir(ns_path(ns))
    assert ns in api_namespaces()

    assert take_down(ns) == []
    assert not os.path.exists(ns_path(ns))
    assert ns not in api_namespaces()

    # Idempotent: a second teardown on an already-gone namespace is not an error.
    assert take_down(ns) == []


def test_no_test_namespace_survives_the_suite(live_service: str):
    """The leak audit. It runs in the gate, so a leaked namespace fails the gate, not a reader.

    Scoped to the namespaces THIS session created: another process can be running
    the same suite concurrently in the same workdir (a gitreins judge re-runs it),
    and its live in-flight namespace is not a leak of this run. The audit still
    fails the gate for a real leak — it just cannot be fooled by a sibling's
    correctly-managed one.
    """
    found = leftovers(scope=CREATED)
    assert found == {"api": [], "disk": []}, \
        f"test namespaces were left behind: {found} (prefix {TEST_NS_PREFIX!r})"
