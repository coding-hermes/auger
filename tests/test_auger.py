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
the API reports back, so it is correct whether the module declares 9 tables or 13 (AUG-007 adds
`edge` and `facet`; AUG-009 adds `bundle` and `bundle_member` and `decision.scope`).

Tests that call JEV are marked `@pytest.mark.jev`. That marker means "this case exercises the
external decisions model", which can be unreachable for reasons that have nothing to do with
this repo; those cases skip loudly naming the reason. Everything unmarked is deterministic and
must pass every run.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

import pytest

import auger
from conftest import (
    CREATED,
    D001,
    D002,
    REPO_ROOT,
    TEST_NS_PREFIX,
    answer,
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
        _ans, err = auger.jev(
            "PROBE",
            {
                "already_answered": {
                    "type": "noul",
                    "instructions": "Is this already answered?",
                }
            },
        )
        _JEV_PROBE["err"] = err
        _JEV_PROBE["keys"] = len(auger._jev_keys())
    err = _JEV_PROBE["err"]
    if err:
        msg = (
            f"JEV is unreachable ({err}; {_JEV_PROBE['keys']} key(s) tried) — `auger check` is "
            "fail-closed, so no verdict can be asserted. Remedy: a live OpenRouter key in "
            "~/.hermes/.env or the OPENROUTER_API_KEY environment variable."
        )
        if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
            raise AssertionError(msg)
        pytest.skip(msg)


def rows(ns: str, table: str, query: str) -> list[dict]:
    return auger.select(ns, table, query)


def row(ns: str, table: str, query: str) -> dict:
    """Exactly one stored row, or a failure naming what came back instead."""
    found = rows(ns, table, query)
    assert len(found) == 1, (
        f"expected exactly one {table} row for [{query}], got {found!r}"
    )
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
    assert served == set(auger.COLS), (
        f"API serves {sorted(served)}, module declares {sorted(auger.COLS)}"
    )
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
    assert any(h.get("key") == f"/auger/{project['pid']}/seed" for h in hits), (
        f"the seed evidence key was not retrievable: {[h.get('key') for h in hits]}"
    )


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
    assert [o["label"] for o in opts if o["active"]] == [
        row(ns, "decision", f"id=eq.{did}")["chosen"]
    ]


def test_answer_tolerates_an_apostrophe_in_the_reason(decided: dict):
    """The module's headline rule: payloads reach the API as bytes, never shell-quoted."""
    tricky = "Postgres's service is what the seed's rule forbids"
    rc, out = run_cli(
        [
            "-n",
            decided["ns"],
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "embedded index",
            "--option",
            "embedded index",
            "--option",
            "server-backed index",
            "--why-not",
            tricky,
            "--confidence",
            "0.7",
        ]
    )
    assert rc == 0 and "D-003 recorded" in out, out
    assert row(decided["ns"], "decision", "id=eq.D-003")["why_not"] == tricky


def test_answer_without_a_project_is_refused(live_service: str):
    """With no project row the verb refuses explicitly instead of writing a dangling decision."""
    ns = new_bare_ns()
    try:
        run_cli(["-n", ns, "init"])
        code, msg, _ = run_cli_exit(
            ["-n", ns, "answer", "--chosen", "x", "--confidence", "0.5"]
        )
        assert code != 0
        assert "no project row" in msg, msg
        assert rows(ns, "decision", "") == []
    finally:
        assert teardown_namespace(ns) == []


# ================================================================= bundles (AUG-009, SPEC-002 section 1)
def scheduler_or_skip() -> str:
    """The fleet's scheduler DB, or a loud skip: a CI runner has no fleet and no DB.

    Membership is verified against the fleet's own `projects` table. Without it there is nothing to
    verify AGAINST — the unverifiable path is exercised separately, by pointing the override at a
    path that cannot answer.
    """
    path = auger.scheduler_db_path()
    if path is None:
        msg = (
            "bundle membership is verified against the fleet's scheduler DB (`projects` table) "
            "and this host has none that can be read. Remedy: set AUGER_SCHEDULER_DB to a "
            "scheduler.db with a `projects` table (the fleet daemon's DB on the fleet host is "
            "~/.hermes/coding-hermes/scheduler.db)."
        )
        if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
            raise AssertionError(msg)
        pytest.skip(msg)
    return path


def fleet_project(prefer: str = "bunker") -> str:
    """A real project name from the fleet, preferring one SPEC-002's own membership names."""
    have = auger.known_projects() or set()
    if prefer in have:
        return prefer
    assert have, "the scheduler DB answered with no projects at all"
    return sorted(have)[0]


def test_init_declares_the_bundle_tables_and_the_scope_column(ns: str):
    """Criterion 1: both tables and `decision.scope` are DECLARED, as the API serves them.

    Asserted on the declaration the API returns rather than on the file init wrote: the registry is
    what a reader queries, and a declaration the API cannot see is not a table.
    """
    status, live, _ = auger.db(f"/api/ns/{ns}/tables")
    assert status == 200, live
    by_name = {t["name"]: [c["name"] for c in t["columns"]] for t in live["tables"]}
    assert set(by_name) == set(auger.COLS), (
        f"API serves {sorted(by_name)}, module declares {sorted(auger.COLS)}"
    )
    assert by_name["bundle"] == ["id", "name", "description", "contract", "status"]
    assert by_name["bundle_member"] == ["id", "bundle_id", "project", "role", "note"]
    assert by_name["decision"][-1] == "scope"

    # idempotent: a second init rewrites nothing and still reports the full set
    declared = len(auger.COLS)
    rc, out = run_cli(["-n", ns, "init"])
    assert rc == 0 and f"declared: {declared}/{declared} tables" in out, out
    assert "wrote declarations" not in out, (
        f"init rewrote declarations it had already written:\n{out}"
    )


def test_bundle_rows_round_trip_with_their_closed_set_status(ns: str):
    """The bundle table's fields come back off the stored row, and status is a closed set."""
    b = auger.bundle(
        ns,
        "agent-ecosystem",
        description="the bus, its hosts and their consumers",
        contract="crier/specs/AGENT-ECOSYSTEM.md",
        status="confirmed",
    )
    assert b["id"].startswith("B-"), b
    stored = row(ns, "bundle", f"id=eq.{b['id']}")
    assert stored["name"] == "agent-ecosystem"
    assert stored["contract"] == "crier/specs/AGENT-ECOSYSTEM.md"
    assert stored["status"] == "confirmed"

    # A contract-less bundle is a real shape (SPEC-002 section 3: B-02 has no spanning spec yet).
    plain = auger.bundle(ns, "memory-core")
    assert row(ns, "bundle", f"id=eq.{plain['id']}")["contract"] == ""
    assert plain["id"] != b["id"], "two bundles got the same id"

    with pytest.raises(SystemExit) as ei:
        auger.bundle(ns, "games", status="maybe")
    assert "maybe" in str(ei.value) and "proposed" in str(ei.value), str(ei.value)
    assert len(rows(ns, "bundle", "")) == 2, "a refused bundle was stored anyway"


def test_unknown_role_and_missing_bundle_are_refused_without_a_fleet_db(ns: str):
    """The two refusals that need no scheduler DB — enforced before membership is even looked up."""
    with pytest.raises(SystemExit) as ei:
        auger.bundle_member(ns, "B-999999", "bunker", "owner")
    assert "B-999999" in str(ei.value) and "does not exist" in str(ei.value), str(
        ei.value
    )

    b = auger.bundle(ns, "agent-ecosystem")
    with pytest.raises(SystemExit) as ei:
        auger.bundle_member(ns, b["id"], "bunker", "observer")
    assert "observer" in str(ei.value) and "test-target" in str(ei.value), str(ei.value)
    assert rows(ns, "bundle_member", "") == [], "a refused member was stored anyway"


def test_a_bundle_member_naming_a_real_fleet_project_is_stored(ns: str):
    """Criterion 4, the accepted half: a project the fleet HAS becomes a member."""
    scheduler_or_skip()
    b = auger.bundle(ns, "agent-ecosystem", contract="crier/specs/AGENT-ECOSYSTEM.md")
    project = fleet_project("bunker")
    m = auger.bundle_member(
        ns, b["id"], project, "test-target", note="the deployment host"
    )
    assert m["id"].startswith("BM-"), m
    stored = row(ns, "bundle_member", f"id=eq.{m['id']}")
    assert (stored["bundle_id"], stored["project"], stored["role"]) == (
        b["id"],
        project,
        "test-target",
    )
    assert stored["note"] == "the deployment host"

    # the same membership written twice is the same membership: refused, not doubled
    with pytest.raises(SystemExit) as ei:
        auger.bundle_member(ns, b["id"], project, "owner")
    assert "already a member" in str(ei.value), str(ei.value)
    assert len(rows(ns, "bundle_member", "")) == 1


def test_a_bundle_member_naming_a_project_with_no_scheduler_row_is_refused(ns: str):
    """Criterion 4: a member naming nothing the fleet has is refused, and no row is written."""
    scheduler_or_skip()
    b = auger.bundle(ns, "agent-ecosystem")
    with pytest.raises(SystemExit) as ei:
        auger.bundle_member(ns, b["id"], "no-such-project-zzz", "consumer")
    msg = str(ei.value)
    assert "no-such-project-zzz" in msg and "scheduler" in msg, msg
    assert rows(ns, "bundle_member", "") == [], (
        "a member naming no fleet project was stored"
    )


def test_bundle_membership_refuses_when_no_scheduler_db_can_be_read(
    ns: str, monkeypatch, tmp_path
):
    """Criterion 4's other half: a MISSING DB refuses clearly and never raises out of sqlite.

    A CI runner has no fleet DB, and `auger` must still say what it could not verify and why. The
    override is exclusive, so this cannot be answered from the fleet's real DB by accident.
    """
    missing = tmp_path / "no-fleet-here" / "scheduler.db"
    monkeypatch.setenv(auger.SCHEDULER_DB_ENV, str(missing))
    assert auger.scheduler_db_path() is None
    assert auger.known_projects() is None

    empty = tmp_path / "zero-byte.db"  # a file that exists and is not a scheduler DB
    empty.write_text("")
    monkeypatch.setenv(auger.SCHEDULER_DB_ENV, str(empty))
    assert auger.scheduler_db_path() is None, "a 0-byte file answered as a scheduler DB"

    monkeypatch.setenv(auger.SCHEDULER_DB_ENV, str(missing))
    b = auger.bundle(ns, "agent-ecosystem")
    with pytest.raises(SystemExit) as ei:
        auger.bundle_member(ns, b["id"], "bunker", "owner")
    msg = str(ei.value)
    assert "cannot verify" in msg and auger.SCHEDULER_DB_ENV in msg, msg
    assert rows(ns, "bundle_member", "") == []


def test_a_bundle_scoped_decision_round_trips_through_the_api(project: dict):
    """Criterion 2: `scope bundle` survives the write, the API re-read, and the default reader."""
    ns = project["ns"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-010",
            "--domain",
            "9.01",
            "--chosen",
            "one message envelope",
            "--option",
            "one message envelope",
            "--option",
            "one envelope per member",
            "--why-not",
            "the siblings must agree on the wire shape",
            "--confidence",
            "0.9",
            "--scope",
            "bundle",
        ]
    )
    assert rc == 0 and "D-010 recorded" in out, out
    assert "scope bundle" in out, out

    stored = row(ns, "decision", "id=eq.D-010")
    assert stored["scope"] == "bundle"
    assert auger.decision_scope(stored) == "bundle"
    with open(os.path.join(ns_path(ns), "tables", "decision.jsonl")) as fh:
        disk = [json.loads(line) for line in fh if line.strip()]
    assert [r["scope"] for r in disk if r["id"] == "D-010"] == ["bundle"], disk


def test_the_default_decision_scope_is_project_and_sends_no_scope_key(decided: dict):
    """Criterion 3: without `--scope` the behaviour AND the stored payload are unchanged.

    Asserted on the namespace's own JSONL — the storage of record — because "the default is
    project" has to mean the column was never written, not that a reader is papering over it.
    """
    ns = decided["ns"]
    d = row(ns, "decision", "id=eq.D-001")
    assert not d.get("scope"), f"a decision written without --scope carries one: {d!r}"
    assert auger.decision_scope(d) == "project"
    assert auger.decision_scope({}) == "project", (
        "an absent scope must read as the default"
    )

    with open(os.path.join(ns_path(ns), "tables", "decision.jsonl")) as fh:
        disk = [json.loads(line) for line in fh if line.strip()]
    # The declared column is MATERIALISED as JSON null for a row that never carried a scope (the
    # namespace writer projects every declared column), so the assertion is on the value: nothing
    # was written that a reader could mistake for a scope, and none of "project"/"bundle" appears.
    assert disk and all(not r.get("scope") for r in disk), disk

    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-011",
            "--chosen",
            "staging table",
            "--confidence",
            "0.7",
            "--scope",
            "project",
        ]
    )
    assert rc == 0 and "scope project" in out, out
    assert "WARNING" not in out, out


def test_an_unknown_decision_scope_is_refused(project: dict):
    """The scope is a closed set, refused by name — like EDGE_KINDS is."""
    ns = project["ns"]
    code, msg, _ = run_cli_exit(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-012",
            "--chosen",
            "x",
            "--confidence",
            "0.5",
            "--scope",
            "bundle-ish",
        ]
    )
    assert code != 0
    assert "bundle-ish" in msg and "project" in msg and "bundle" in msg, msg
    assert rows(ns, "decision", "id=eq.D-012") == []


def _declare_tables_as_a_namespace_that_predates_scope(ns: str) -> None:
    """Write the declarations an OLD namespace has: `decision` with no `scope` column.

    Only the three tables the `answer` flow touches are declared. They are written BEFORE the API
    is asked anything, because DuckBrain caches a namespace's declarations on its first read of
    them — a declaration written after that read is invisible to the running server, which is the
    exact situation this reproduces. Nothing is copied from init's declare path except the column
    lists, and `scope` is removed from the module's own list so this stays a pre-AUG-009 shape even
    as the model grows.
    """
    tables = os.path.join(ns_path(ns), "tables")
    os.makedirs(tables, exist_ok=True)
    for name in ("project", "decision", "option"):
        cols = [
            (c, t)
            for c, t in auger.COLS[name]
            if not (name == "decision" and c == "scope")
        ]
        decl = {
            "name": name,
            "format": "jsonl-objects",
            "primary": "id",
            "glob": f"tables/{name}.jsonl",
            "columns": [{"name": c, "type": t} for c, t in cols],
        }
        with open(os.path.join(tables, f"{name}.table.json"), "w") as f:
            f.write(json.dumps(decl, indent=1))


def test_a_namespace_declared_before_scope_keeps_working(live_service: str):
    """Criterion 3's hard half: an old namespace keeps working, and loses its scope OUT LOUD.

    DuckBrain never introspects or migrates a declared table, and its registry cache lives for the
    process, so a namespace read before this change is served a `decision` table without `scope`.
    The default path sends no scope key and is therefore untouched. A BUNDLE-scoped decision cannot
    be stored there, and auger says exactly that instead of recording a contract as a local choice.
    """
    ns = new_bare_ns()
    try:
        _declare_tables_as_a_namespace_that_predates_scope(ns)
        rc, out = run_cli(
            [
                "-n",
                ns,
                "start",
                "--name",
                "oldns",
                "--id",
                "P-OLD",
                "--seed",
                "one small CLI on one box",
            ]
        )
        assert rc == 0 and "seed stored" in out, out

        status, live, _ = auger.db(f"/api/ns/{ns}/tables")
        assert status == 200, live
        served = {t["name"]: [c["name"] for c in t["columns"]] for t in live["tables"]}
        assert "scope" not in served["decision"], (
            f"the API is not serving the pre-AUG-009 declaration this test set up: {served}"
        )

        # the default path: same verb, same payload, no warning
        rc, out = run_cli(
            [
                "-n",
                ns,
                "answer",
                "--id",
                "D-001",
                "--chosen",
                "single SQLite file",
                "--confidence",
                "0.8",
            ]
        )
        assert rc == 0 and "WARNING" not in out, out
        assert auger.decision_scope(row(ns, "decision", "id=eq.D-001")) == "project"

        # a bundle-scoped decision cannot be stored here: stored project-scoped, SAID OUT LOUD
        rc, out = run_cli(
            [
                "-n",
                ns,
                "answer",
                "--id",
                "D-002",
                "--chosen",
                "one envelope",
                "--confidence",
                "0.9",
                "--scope",
                "bundle",
            ]
        )
        assert rc == 0, out
        assert "WARNING" in out and "NOT bundle-scoped" in out, out
        stored = row(ns, "decision", "id=eq.D-002")
        assert not stored.get("scope"), stored
        assert auger.decision_scope(stored) == "project"
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
    assert (
        f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002={D002['chosen']}" in out
    ), out
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
    why_not = row(ns, "decision", "id=eq.D-002")[
        "why_not"
    ]  # read it, do not hardcode it
    assert why_not == D002["why_not"]
    assert why_not in out, f"the recorded reason {why_not!r} was not quoted:\n{out}"
    assert "row locking contradicts the recorded choice (staging table)" in out, out

    # The control: the CURRENT configuration is not reported as a contradiction.
    rc, current = run_cli(["-n", ns, "dump"])
    assert rc == 0
    assert "CONTRADICTIONS WITH THE RECORD" not in current, current


def test_dump_config_warns_on_an_option_that_does_not_exist(decided: dict):
    rc, out = run_cli(
        ["-n", decided["ns"], "dump", "--config", "D-002=nonexistent option"]
    )
    assert rc == 0
    assert "WARNING: unparsed --config entries ignored" in out, out
    assert "no such option for D-002" in out, out


def test_dump_out_writes_the_file_it_reports(decided: dict, tmp_path):
    target = tmp_path / "dump.txt"
    rc, out = run_cli(["-n", decided["ns"], "dump", "--out", str(target)])
    assert rc == 0 and f"wrote {target}" in out, out

    text = target.read_text()
    assert (
        f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002={D002['chosen']}" in text
    ), text
    # The file is the same projection the stdout mode prints (its first line is not a stub).
    _rc, printed = run_cli(["-n", decided["ns"], "dump"])
    assert (
        text.splitlines()[0]
        == printed.splitlines()[0]
        == "# pytesttest — configuration dump"
    )


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
    assert f"ACTIVE CONFIGURATION: D-001={D001['chosen']}, D-002=row locking" in out, (
        out
    )


def test_toggle_set_parses_on_and_off(decided: dict):
    ns = decided["ns"]
    rc, out = run_cli(
        ["-n", ns, "toggle", "--set", "D-002-O1=off", "--set", "D-002-O2=on"]
    )
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


# ================================================================= one option per decision (AUG-015)
D003 = dict(
    did="D-003",
    domain="4.07",
    chosen="stream the log from disk",
    options=["stream the log from disk", "grep per request", "in-memory cache"],
    why_not="the seed forbids a second service",
    confidence=0.6,
)


def three_option_decision(decided: dict) -> dict:
    """The board's reproduction shape: ONE decision with three alternatives, the first chosen.

    Two options can be brought into conflict by a single `--on`; three is where "the other one"
    stops being well defined, so the flip has to be a SET and not a partner.
    """
    ns = decided["ns"]
    rc, out = answer(ns, **D003)
    assert rc == 0 and "D-003 recorded" in out, out
    return {**decided, "D003": D003}


def test_toggle_on_deactivates_the_siblings_so_one_decision_has_one_configuration(
    decided: dict,
):
    """AUG-015: `--on` is a SELECTION — the siblings go off, visibly, in the same write."""
    state = three_option_decision(decided)
    ns = state["ns"]
    before = option_flags(ns)
    assert [before[f"D-003-O{i}"] for i in (1, 2, 3)] == [True, False, False], before

    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-003-O3"])
    assert rc == 0, out
    assert "D-003-O3->on (1)" in out, out
    assert "one option per decision D-003" in out, (
        out
    )  # the flip is REPORTED, never silent

    # The stored rows are the assertion: exactly one active option, and it is the one asked for.
    after = option_flags(ns)
    assert [after[f"D-003-O{i}"] for i in (1, 2, 3)] == [False, False, True], after
    active = [
        o["id"]
        for o in rows(ns, "option", "decision_id=eq.D-003&order=id.asc")
        if o["active"]
    ]
    assert active == ["D-003-O3"], active
    # The sibling's own decision is untouched: exclusivity is per decision, not per namespace.
    assert after["D-001-O1"] is True and after["D-002-O1"] is True, after

    # And the render agrees: one binding for D-003, with nothing to warn about.
    rc, out = run_cli(["-n", ns, "dump"])
    assert rc == 0, out
    assert (
        "ACTIVE CONFIGURATION: D-001=single SQLite file, D-002=staging table, "
        "D-003=in-memory cache"
    ) in out, out
    assert "!= 1 active option" not in out, out


def test_several_activations_in_one_call_still_leave_one_active_option(decided: dict):
    """The flip judges siblings by THIS call's own writes, never by a snapshot taken before the
    first PATCH: `--on A --on B` would otherwise leave both live — the same defect by another door."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-001-O2", "--on", "D-001-O1"])
    assert rc == 0, out

    flags = option_flags(ns)
    assert [flags["D-001-O1"], flags["D-001-O2"]] == [True, False], (out, flags)
    rc, dump = run_cli(["-n", ns, "dump"])
    assert "!= 1 active option" not in dump, dump
    line = next(
        ln for ln in dump.splitlines() if ln.startswith("ACTIVE CONFIGURATION:")
    )
    assert (
        line == "ACTIVE CONFIGURATION: D-001=single SQLite file, D-002=staging table"
    ), line


def test_the_additive_escape_hatch_keeps_both_active_and_dump_warns(decided: dict):
    """`--additive` keeps the old behaviour — and dump then NAMES the collision instead of
    rendering one decision twice as THE configuration."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-002-O2", "--additive"])
    assert rc == 0 and "D-002-O2->on (1)" in out, out
    assert "one option per decision" not in out, out  # additive claims no exclusivity

    flags = option_flags(ns)
    assert flags["D-002-O1"] is True and flags["D-002-O2"] is True, flags

    rc, out = run_cli(["-n", ns, "dump"])
    assert rc == 0, out
    assert "WARNING: decisions with != 1 active option" in out, out
    assert "  - D-002: 2 of 2 options active (D-002-O1, D-002-O2)" in out, out
    # Warned, not crashed: the render still happens.
    assert (
        "ACTIVE CONFIGURATION: D-001=single SQLite file, D-002=staging table, "
        "D-002=row locking"
    ) in out, out


def test_status_warns_when_two_options_of_one_decision_are_active(decided: dict):
    """`status` reports the same collision, and still prints the confidence map it is for."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0 and "!= 1 active option" not in out, out  # a clean record is silent

    run_cli(["-n", ns, "toggle", "--on", "D-002-O2", "--additive"])
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "WARNING: decisions with != 1 active option" in out, out
    assert "  - D-002: 2 of 2 options active (D-002-O1, D-002-O2)" in out, out
    assert "decisions 2 |" in out, out  # warn AND continue: the map is still there


def test_the_warning_covers_the_other_half_of_the_invariant(decided: dict):
    """Zero active options is `!= 1` too: the decision silently leaves the configuration."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-002-O1"])
    assert rc == 0 and option_flags(ns)["D-002-O1"] is False, out

    rc, dump = run_cli(["-n", ns, "dump"])
    assert rc == 0, dump
    assert "WARNING: decisions with != 1 active option" in dump, dump
    assert "  - D-002: 0 of 2 options active" in dump, dump
    line = next(
        ln for ln in dump.splitlines() if ln.startswith("ACTIVE CONFIGURATION:")
    )
    assert line == "ACTIVE CONFIGURATION: D-001=single SQLite file", line

    rc, status = run_cli(["-n", ns, "status"])
    assert rc == 0 and "  - D-002: 0 of 2 options active" in status, status


# ================================================================= check (the JEV-dependent verb)
@pytest.mark.jev
def test_check_retrieves_the_rows_this_suite_stored_and_reaches_one_verdict(
    decided: dict,
):
    """The ground smoke.sh cannot assert on: retrieval finds the rows THIS suite wrote."""
    jev_or_skip()
    ns = decided["ns"]
    rc, out = run_cli(
        ["-n", ns, "check", "How should we store the record of files we have seen?"]
    )
    assert rc == 0, out

    assert "nearest stored rows (" in out, out
    keys = {f"/auger/{decided['pid']}/D-001", f"/auger/{decided['pid']}/D-002"}
    assert any(k in out for k in keys), (
        f"retrieval did not return the stored evidence:\n{out}"
    )
    assert sum(v in out for v in ("ALREADY ANSWERED", "NOT YET ANSWERED")) == 1, (
        f"no single verdict in the output:\n{out}"
    )


@pytest.mark.jev
def test_check_rejects_a_question_the_evidence_does_not_cover(decided: dict):
    """The fail-side: an unrelated question is not waved through as already answered."""
    jev_or_skip()
    rc, out = run_cli(
        [
            "-n",
            decided["ns"],
            "check",
            "What message queue brokers the watcher to the digest sender?",
        ]
    )
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
    monkeypatch.setattr(
        auger, "jev", lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401")
    )
    auger.remember(
        ns, "/probe/evidence", "We chose a single SQLite file for the record store."
    )
    rc, out = run_cli(["-n", ns, "check", "How is the record stored?"])
    assert rc == 1, out
    assert "JEV unavailable" in out and "fail-closed" in out, out
    assert "ALREADY ANSWERED" not in out, out


def test_check_maps_the_model_answer_to_the_threshold_verdict(ns: str, monkeypatch):
    """The thresholds live in this module, not in the model — pin that with a fixed answer."""

    def stub(value: float):
        return lambda *a, **k: (
            {
                "answers": {"already_answered": {"type": "noul", "noul": value}},
                "usage": {"cost": 1e-05},
                "model": "stub",
            },
            None,
        )

    auger.remember(
        ns, "/probe/evidence", "We chose a single SQLite file for the record store."
    )

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
        auger.insert(
            ns,
            "question",
            {
                "id": qid,
                "project_id": pid,
                "domain": "4.05",
                "text": text,
                "ring": 1,
                "qclass": "",
                "status": status,
                "jev_already_answered": -1.0,
                "jev_checked_at": "",
            },
        )


def gate_stub(noul: float, calls: list):
    """A JEV stub that answers the one question the gate asks, and counts its calls."""

    def _stub(state, questions, *a, **k):
        calls.append(state)
        return (
            {
                "answers": {"already_answered": {"type": "noul", "noul": noul}},
                "usage": {"cost": 1e-05},
                "model": "stub",
            },
            None,
        )

    return _stub


def answer_cli(
    ns: str, did: str, domain: str, chosen: str, qid: str = ""
) -> tuple[int, str]:
    argv = [
        "-n",
        ns,
        "answer",
        "--id",
        did,
        "--domain",
        domain,
        "--chosen",
        chosen,
        "--option",
        chosen,
        "--option",
        "something else",
        "--why-not",
        "the seed forbids it",
        "--confidence",
        "0.8",
    ]
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
    inv = auger.edge(
        ns,
        pid,
        "breaks",
        "decision",
        "D-002",
        "decision",
        "D-001",
        source="human",
        note="the embedded index makes the SQLite file the wrong store",
    )
    # The id is read off the write, not assumed to be E-000001: the answer above named a question,
    # so it wrote the `closes` edge first (BEAT 2) and that edge holds the lower id.
    assert inv["id"].startswith("E-"), inv

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "moot: 1   reopened: 0   linked: 0" in out, out

    q = row(ns, "question", "id=eq.Q-000001")
    assert q["status"] == "moot"
    assert q["text"] == Q_WATCH, "a moot question keeps its row — it is never deleted"

    reason = f"D-001 was invalidated by {inv['id']}"
    assert auger.q_reason(ns, "Q-000001") == reason
    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets
    assert all(
        f["status"] == "closed" and f["closed_by"] == "D-001" and f["note"] == reason
        for f in facets
    ), facets
    assert reason in out, out

    # Nothing was asked again and nothing else moved.
    assert auger.askable_questions(ns, pid) == []
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 2
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 1
    assert row(ns, "decision", "id=eq.D-002")["chosen"] == "an embedded index"


def test_propagate_reopens_the_stale_branch_and_reaches_the_grandchild(
    project: dict, monkeypatch
):
    """Criterion (b): the change runs down the derives_from tree. The question the dead decision
    answered goes moot, the settled questions below it REOPEN with the reason recorded (Q4), and
    the walk is not one hop deep — the grandchild is reached. A second pass changes nothing."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.05, calls))
    store_questions(
        ns,
        pid,
        ("Q-000001", Q_WATCH, "answered"),
        ("Q-000002", Q_STORE, "linked"),
        ("Q-000003", Q_CRASH, "linked"),
    )
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    assert answer_cli(ns, "D-002", "4.09", "an embedded index")[0] == 0
    inv = auger.edge(
        ns,
        pid,
        "breaks",
        "decision",
        "D-002",
        "decision",
        "D-001",
        source="human",
        note="the embedded index makes the SQLite file the wrong store",
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage question only exists once the record question is asked",
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000003",
        "question",
        "Q-000002",
        source="rule",
        note="the crash question descends from the storage question",
    )

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "moot: 1   reopened: 2   linked: 0" in out, out

    assert row(ns, "question", "id=eq.Q-000001")["status"] == "moot"
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open", (
        "the stale branch did not reopen"
    )
    assert row(ns, "question", "id=eq.Q-000003")["status"] == "open", (
        "the cascade stopped one hop short"
    )

    # Both reasons name the BREAKING EDGE, whose id is read off the write: the answer above named a
    # question, so BEAT 2's `closes` edge holds the lowest id in this namespace.
    cause = f"D-001 was invalidated by {inv['id']}"
    child, grandchild = auger.q_reason(ns, "Q-000002"), auger.q_reason(ns, "Q-000003")
    assert "reopened" in child and "Q-000001" in child and cause in child, child
    assert (
        "reopened" in grandchild and "Q-000002" in grandchild and cause in grandchild
    ), grandchild
    assert child in out and grandchild in out, out

    # Both are settled questions that went stale, so both are askable again — and neither was
    # asked: the gate re-examined the reopened child once and left it open.
    assert [q["id"] for q in auger.askable_questions(ns, pid)] == [
        "Q-000002",
        "Q-000003",
    ]
    assert len(calls) == 1, calls

    rc, out2 = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out2
    assert "moot: 0   reopened: 0   linked: 0" in out2, out2
    assert len(calls) == 1, (
        "a second pass re-gated a question the gate had already examined"
    )
    assert auger.q_reason(ns, "Q-000001") == cause, (
        "a moot question keeps the reason it was given"
    )


def test_ask_never_surfaces_a_question_with_an_unresolved_blocker(
    project: dict, monkeypatch
):
    """Criterion (c): the askable surface honours the blocks edge, and the control proves the
    block — not a bug — is what held the question back."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    store_questions(
        ns,
        pid,
        ("Q-000001", Q_WATCH, "open"),
        ("Q-000002", Q_STORE, "open"),
        ("Q-000003", Q_CRASH, "open"),
    )
    auger.edge(
        ns,
        pid,
        "blocks",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage choice cannot be made before the record is defined",
    )

    assert [q["id"] for q in auger.blocked_questions(ns, pid)] == ["Q-000002"]
    assert [q["id"] for q in auger.askable_questions(ns, pid)] == [
        "Q-000001",
        "Q-000003",
    ]

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "Q-000003" in out and Q_CRASH in out, (
        out
    )  # the unblocked question is surfaced
    assert "blocked by an unresolved question: 1" in out, out
    assert "Q-000002" not in out, out  # the blocked one is not surfaced
    assert Q_STORE not in out, out
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open", (
        "not surfaced is not deleted"
    )

    # The control: settle the blocker and the very same question becomes askable.
    auger.set_state(
        ns, pid, "Q-000001", "answered", "answered by D-001", closed_by="D-001"
    )
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "blocked by an unresolved question: 0" in out, out
    assert "Q-000002" in out and Q_STORE in out, out


def test_propagate_links_a_question_the_gate_can_answer_after_its_parent_closes(
    project: dict, monkeypatch
):
    """Criterion (d): once the parent closes, the child is re-examined and LINKED to the answer we
    already hold instead of being asked again — and it is not re-examined before that."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.91, calls))
    store_questions(
        ns, pid, ("Q-000001", Q_WATCH, "open"), ("Q-000002", Q_STORE, "open")
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage question only exists once the record question is asked",
    )
    # The control arm needs the parent to stay OPEN, so this answer names no question: BEAT 2
    # closes the question a decision names, and an answer that names none closes none (AUG-002).
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file")[0] == 0
    assert auger.recall(ns, Q_STORE, limit=5), (
        "the substrate returned no evidence for the stored decision — nothing could be linked"
    )

    # CONTROL: while the parent is open the child is not re-examined, and nothing is linked.
    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert calls == [], "the gate ran on a question whose parent had not closed"
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open"
    assert rows(ns, "edge", "kind=eq.satisfies") == []

    # The parent closes (BEAT 2's job in the verb; driven at the store here so this case isolates
    # PROPAGATE from ANSWER), and PROPAGATE re-examines the child.
    auger.set_state(
        ns, pid, "Q-000001", "answered", "answered by D-001", closed_by="D-001"
    )
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


# ================================================================= answer -> closes (SPEC-001 BEAT 2 / AUG-002)
# The half of ANSWER the spec calls for and the CLI never did: the record named the question it
# answers, and no edge, no state change and no reader followed. Each case asserts the STORED rows —
# the edge and the question — not just the printed line.
def test_answer_with_a_question_id_closes_that_question(project: dict):
    """A decision that names a question IS its answer: the edge, the state and the reason all land."""
    ns, pid = project["ns"], project["pid"]
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "open"))

    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")
    assert rc == 0, out
    assert row(ns, "decision", "id=eq.D-001")["question_id"] == "Q-000001"

    e = row(ns, "edge", "kind=eq.closes")
    assert (e["src_kind"], e["src_id"], e["dst_kind"], e["dst_id"]) == (
        "decision",
        "D-001",
        "question",
        "Q-000001",
    ), e
    assert e["source"] == "rule" and "BEAT 2" in e["note"], e

    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered", (
        "the question stayed open"
    )
    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets
    assert all(
        f["status"] == "closed"
        and f["closed_by"] == "D-001"
        and "answered by D-001" in f["note"]
        for f in facets
    ), facets

    # The verb says what it did, and it is off the askable surface afterwards.
    assert "closed Q-000001" in out, out
    assert auger.askable_questions(ns, pid) == [], (
        "an answered question is still askable"
    )


def test_answer_without_a_question_id_touches_no_question_row(project: dict):
    """The zero-change case: no --question-id means no edge, no state flip, the same output shape."""
    ns, pid = project["ns"], project["pid"]
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "open"))

    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file")
    assert rc == 0, out
    assert "closed" not in out, out
    assert row(ns, "decision", "id=eq.D-001")["question_id"] == ""
    assert rows(ns, "edge", "kind=eq.closes") == []
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "open"
    assert rows(ns, "facet", "question_id=eq.Q-000001") == [], "no link, no facet write"


def test_answer_refuses_a_question_id_that_is_not_stored(project: dict):
    """A typo must stop here rather than store a link to nothing and flip a row that is not there."""
    ns = project["ns"]
    code, msg, _out = run_cli_exit(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-001",
            "--domain",
            "4.05",
            "--chosen",
            "single SQLite file",
            "--question-id",
            "Q-000404",
        ]
    )
    assert code != 0
    assert "refused" in msg and "Q-000404" in msg, msg
    assert rows(ns, "edge", "kind=eq.closes") == []
    assert rows(ns, "facet", "") == []
    # The refusal is about the LINK, not about the answer: the decision the call recorded first
    # stands, the same way a model that is down does not unrecord an answer (BEAT 3's doctrine).
    assert row(ns, "decision", "id=eq.D-001")["question_id"] == "Q-000404"


def test_status_reports_the_open_branches_and_their_depth(project: dict):
    """The status line comes off the ROWS: the open question count and the longest stored chain."""
    ns, pid = project["ns"], project["pid"]
    store_questions(
        ns,
        pid,
        ("Q-000001", Q_WATCH, "open"),
        ("Q-000002", Q_STORE, "open"),
        ("Q-000003", Q_CRASH, "open"),
    )
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    # The shape the feedback engine writes: the answer raised Q-000002 (`opens`) and Q-000002
    # descends from the question that answer closed (`derives_from`) — two levels below Q-000001.
    auger.edge(
        ns,
        pid,
        "opens",
        "decision",
        "D-001",
        "question",
        "Q-000002",
        source="rule",
        note="the confidence in D-001 is thin, so the engine proposed a follow-up",
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="Q-000002 drills D-001, which answers Q-000001",
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000003",
        "question",
        "Q-000002",
        source="rule",
        note="the crash question descends from the storage question",
    )

    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "branches: 2 open | max depth 3" in out, out


def test_status_on_a_project_with_no_questions_adds_no_branch_line(project: dict):
    """Empty is not a crash and not a fabricated zero-line: nothing stored, nothing claimed."""
    rc, out = run_cli(["-n", project["ns"], "status"])
    assert rc == 0, out
    assert "branches:" not in out, out


def test_branch_depth_follows_dependency_and_never_hangs_on_a_cycle():
    """Pure function, no namespace: the walk's two rules and its cycle guard.

    `derives_from` and `blocks` both put the source one level below the destination; a
    decision -> question `opens` edge is NOT a level (the level it adds is recorded as the
    `derives_from` back to the question that answer closed, so counting both double-counts).
    """

    def q(qid):
        return {"id": qid}

    def e(kind, src, dst):
        return {
            "kind": kind,
            "src_kind": "question",
            "src_id": src,
            "dst_kind": "question",
            "dst_id": dst,
        }

    # Q-2 cannot be answered until Q-1 is, and Q-3 descends from Q-2: three levels.
    assert (
        auger.branch_depth(
            [q("Q-1"), q("Q-2"), q("Q-3")],
            [e("blocks", "Q-2", "Q-1"), e("derives_from", "Q-3", "Q-2")],
        )
        == 3
    )
    assert (
        auger.branch_depth(
            [q("Q-1"), q("Q-2")],
            [
                {
                    "kind": "opens",
                    "src_kind": "decision",
                    "src_id": "D-1",
                    "dst_kind": "question",
                    "dst_id": "Q-2",
                }
            ],
        )
        == 1
    )
    # A cycle is one level, not a hang and not an invented length.
    assert (
        auger.branch_depth(
            [q("Q-1"), q("Q-2")],
            [e("derives_from", "Q-2", "Q-1"), e("derives_from", "Q-1", "Q-2")],
        )
        == 1
    )
    assert auger.branch_depth([], []) == 0


# ================================================================= exit codes (subprocess is the subject)
def test_unknown_verb_exits_non_zero():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "auger.py"), "frobnicate"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr or "frobnicate" in proc.stderr


def test_a_successful_verb_exits_zero_in_a_subprocess(project: dict):
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "auger.py"), "-n", project["ns"], "status"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "decisions" in proc.stdout


# ================================================================= the broken-verb trap
def test_patch_reporting_zero_updates_leaves_the_flag_unchanged(
    decided: dict, monkeypatch
):
    """Break the write path; prove the consequence is observable on the stored ROW.

    `auger.patch` returning `{"updated": 0}` is exactly what a silently-broken toggle looks
    like: the verb still exits 0, so only the stored row can tell you nothing was written.
    """
    ns = decided["ns"]
    monkeypatch.setattr(auger, "patch", lambda *a, **k: {"updated": 0})
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0
    assert "D-001-O1->off (0)" in out, out  # the verb reports zero rows updated
    assert option_flags(ns)["D-001-O1"] is True  # and nothing actually flipped

    monkeypatch.undo()
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0 and "D-001-O1->off (1)" in out, out
    assert option_flags(ns)["D-001-O1"] is False  # a working toggle does flip it


#: The child suite: one test per verb, with the toggle verb's write deliberately broken.
BROKEN_VERB_SUITE = """
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
"""


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
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "pytest-child.ini",
            f"test_broken_{nonce}.py",
            "-p",
            "no:cacheprovider",
            "-q",
            "--no-header",
            "-rf",
        ],
        cwd=str(child),
        capture_output=True,
        text=True,
        env=env,
    )
    report = proc.stdout + proc.stderr
    print("\n" + report)  # auditable under -s, and on failure

    assert "1 failed, 3 passed" in report, (
        f"expected exactly one failure, got:\n{report}"
    )
    failed = [line for line in report.splitlines() if line.startswith("FAILED")]
    assert len(failed) == 1, (
        f"expected exactly one FAILED line, got {failed}:\n{report}"
    )
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
    assert found == {"api": [], "disk": []}, (
        f"test namespaces were left behind: {found} (prefix {TEST_NS_PREFIX!r})"
    )


# ================================================================= 429 backpressure on teardown
# DuckBrain's limiter answers 429 with the wait in the BODY (`retryAfter`) and/or in the
# `Retry-After` header. The teardown path is the one place where an unretried 429 is not a
# retry lost but STATE LOST: the registry row survives while the directory is removed. These
# cases are OFFLINE — the wire (`urlopen`) and the filesystem root (`ns_dir`) are the only
# two fakes — so they run in gate.sh's quiet arm AND under the busy one that tripped the
# limiter in the first place, and they never touch the real DuckBrain.
RETRY_HINT_BODY = {"error": "too many requests", "retryAfter": 0.25}

#: Scripted payload sentinel: "the registry as the fake currently holds it", so a script does
#: not have to know which rows exist before the test has run.
REGISTRY = object()


class _FakeResponse:
    """The part of `http.client.HTTPResponse` that `auger._req` actually reads."""

    def __init__(self, status: int, payload, headers: dict | None = None):
        self.status = status
        self._body = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeDuckBrain:
    """A scripted, OFFLINE stand-in for the two endpoints the teardown path touches.

    The script is consumed in ORDER, one entry per HTTP request, and an UNSCRIPTED request
    fails the test rather than being served. That is the point: a request this section did
    not allow (a second DELETE from a hand-rolled retry loop, or a call that bypassed the
    module's transport) is a defect of exactly the kind being guarded, so it must be loud.
    """

    def __init__(self, ns: str, script: list[tuple[int, object, dict]]):
        self.ns = ns
        self.script = list(script)
        self.requests: list[
            tuple[str, str]
        ] = []  # (method, path) per HTTP request, in order
        self.sleeps: list[float] = []  # every backoff the transport asked for
        self.req_calls: list[str] = []  # every auger._req CALL (not per-request)
        self.registered = True

    def urlopen(self, req, timeout=None):  # noqa: ARG002 - mirrors the transport's signature
        method, url = req.get_method(), req.full_url
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        self.requests.append((method, path))
        assert self.script, (
            f"unscripted request {method} {path} — the teardown made a call this test does not allow"
        )
        status, payload, headers = self.script.pop(0)
        if payload is REGISTRY:
            payload = {"namespaces": [{"name": self.ns}] if self.registered else []}
        if status == 429:
            raise urllib.error.HTTPError(
                url,
                429,
                "Too Many Requests",
                headers,
                io.BytesIO(json.dumps(payload).encode()),
            )
        if method == "DELETE":
            self.registered = False
        return _FakeResponse(status, payload, headers)

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.requests if m == method)


@pytest.fixture
def offline_duckbrain(monkeypatch, tmp_path):
    """Install the scripted transport and expose the counters the assertions read.

    Both boundaries are fake and everything between them is real: `teardown_namespace` runs
    the module's own deletion, its own 429 retry and its own registry re-check. `sleep` is
    recorded instead of taken, so a bounded linear backoff costs the suite no wall-clock time.
    """

    def install(script):
        fake = FakeDuckBrain(TEST_NS_PREFIX + "429mocked", script)
        real_req = auger._req

        def spy_req(
            url,
            method="GET",
            body=None,
            headers=None,
            timeout=45,
            retries=auger.RETRIES,
        ):
            fake.req_calls.append(method)
            return real_req(url, method, body, headers, timeout, retries)

        monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
        monkeypatch.setattr(auger, "_req", spy_req)
        monkeypatch.setattr(auger.time, "sleep", fake.sleeps.append)
        monkeypatch.setattr(auger, "_token", lambda: "offline-test-token")
        monkeypatch.setattr(auger, "ns_dir", lambda n: str(tmp_path / n))
        return fake

    return install


def test_teardown_survives_a_429_on_its_delete(offline_duckbrain):
    """(a) teardown completes; (b) the retry that saved it was the HELPER's, not a bypass."""
    fake = offline_duckbrain(
        [
            (429, RETRY_HINT_BODY, {}),  # the first DELETE meets the limiter
            (200, {"deleted": True}, {}),  # retried through
            (200, REGISTRY, {}),  # the registry re-check
        ]
    )

    problems = teardown_namespace(fake.ns)

    assert problems == [], (
        f"teardown reported problems over a 429 it should have retried: {problems}"
    )
    assert fake.registered is False, "the namespace is gone: the DELETE did get through"
    assert fake.count("DELETE") == 2, fake.requests
    # NO BYPASS, stated as a relationship between the two counters: TWO _req calls (the
    # delete, the re-check) issued THREE HTTP requests, so the second DELETE came from INSIDE
    # the retry helper. A retry written at the call site would show three _req calls, and a
    # request that bypassed the module's transport would not appear in req_calls at all.
    assert fake.req_calls == ["DELETE", "GET"], fake.req_calls
    assert len(fake.requests) == 3, fake.requests
    assert fake.sleeps == [0.25], (
        f"the server's own retryAfter hint was not honoured: {fake.sleeps}"
    )


def test_teardown_honours_the_retry_after_header_when_the_body_is_silent(
    offline_duckbrain,
):
    """The header is the other half of the hint, and it must beat the built-in default."""
    fake = offline_duckbrain(
        [
            (429, {"error": "too many requests"}, {"Retry-After": "2"}),
            (200, {"deleted": True}, {}),
            (200, REGISTRY, {}),
        ]
    )

    assert teardown_namespace(fake.ns) == []
    assert fake.count("DELETE") == 2, fake.requests
    assert fake.sleeps == [2.0], (
        f"Retry-After was ignored (a default-wait would show [1.0]): {fake.sleeps}"
    )


def test_teardown_survives_a_429_on_its_registry_re_check(offline_duckbrain):
    """The teardown's other call rides the same retry, so it cannot fail the fixture over a 429.

    An unretried 429 on this GET is not a leak, but it is a false alarm: the re-check reads
    an error body where it expects a namespace list and the fixture fails a teardown that in
    fact succeeded.
    """
    fake = offline_duckbrain(
        [
            (200, {"deleted": True}, {}),
            (429, RETRY_HINT_BODY, {}),
            (200, REGISTRY, {}),
        ]
    )

    assert teardown_namespace(fake.ns) == []
    assert fake.count("GET") == 2, fake.requests
    assert fake.req_calls == ["DELETE", "GET"], fake.req_calls


def test_a_saturated_limiter_is_bounded_and_the_leak_is_reported(offline_duckbrain):
    """Giving up is BOUNDED and LOUD — never an unbounded loop, never a silent leak."""
    retries = auger.TEARDOWN_RETRIES
    fake = offline_duckbrain(
        [(429, RETRY_HINT_BODY, {})] * (retries + 1) + [(200, REGISTRY, {})]
    )

    problems = teardown_namespace(fake.ns)

    assert fake.count("DELETE") == retries + 1, (
        f"expected one try plus {retries} retries, saw {fake.count('DELETE')}: {fake.requests}"
    )
    assert len(fake.sleeps) == retries, (
        f"one backoff per retry, none for the first try: {fake.sleeps}"
    )
    assert fake.registered is True, (
        "the row must still be there for this case to mean anything"
    )
    assert any("429" in p for p in problems), problems
    assert any("still listed" in p for p in problems), problems


# ================================================================= the bundle boundary (SPEC-002 2.1/2.2)
# AUG-010. Two places in the engine cross the line between bundled projects:
#
#   * the GATE (2.1) searches EVERY member's namespace, so a question a sibling already answered is
#     LINKED to that sibling's decision instead of being re-decided and drifting — cross-project
#     decision dedupe. The link carries the sibling's project name, so a reader sees where the
#     answer came from.
#   * the IMPACT pass (2.2) walks the members of a BUNDLE-scoped answer and records what the answer
#     does to each sibling's contracts: `affects` for a change, `breaks` plus an ESCALATION for an
#     invalidation. The engine records the break and surfaces it; it never rewrites the sibling.
#
# The MODEL is faked in these cases — JEV is an external service, and what is under test here is the
# engine's behaviour around it, which the `auger check` cases already cover end to end. The
# SUBSTRATE is not faked: every namespace, project, decision, edge and escalation below is real
# DuckBrain, so these cases skip loudly on a host without one and need no OpenRouter key.
def two_fleet_projects() -> tuple[str, str]:
    """Two DISTINCT real project names — a bundle member names a project the fleet HAS."""
    scheduler_or_skip()
    have = auger.known_projects() or set()
    ordered = [
        p for p in ("bunker", "crier", "terminal-jail", "coding-hermes") if p in have
    ]
    ordered += sorted(have)
    picked: list[str] = []
    for name in ordered:
        if name not in picked:
            picked.append(name)
    if len(picked) < 2:
        pytest.skip(
            f"the fleet's `projects` table holds {len(picked)} name(s): a bundle needs two"
        )
    return picked[0], picked[1]


def start_named_project(ns: str, name: str, pid: str) -> None:
    """A project whose NAME is what a `bundle_member` row names (start defaults it to the ns)."""
    rc, out = run_cli(
        [
            "-n",
            ns,
            "start",
            "--name",
            name,
            "--id",
            pid,
            "--seed",
            f"{name}: one box, one job, one contract the siblings must honour",
        ]
    )
    assert rc == 0 and "seed stored" in out, out


def bundle_with(ns: str, *members: str) -> dict:
    """A bundle over the given member projects, written through the module's own writers."""
    b = auger.bundle(
        ns,
        "agent-ecosystem",
        description="the bus, its hosts and their consumers",
        contract="crier/specs/AGENT-ECOSYSTEM.md",
        status="confirmed",
    )
    for i, member in enumerate(members):
        auger.bundle_member(ns, b["id"], member, "owner" if i == 0 else "consumer")
    return b


def sibling_decision(ns: str, pid: str, did: str, chosen: str) -> dict:
    """A BUNDLE-scoped decision in a sibling's namespace, via insert_decision (never a raw db())."""
    spec = {
        "id": did,
        "project_id": pid,
        "domain": "9.01",
        "question_id": "",
        "chosen": chosen,
        "why_not": "the siblings must agree on the wire shape",
        "reversal_cost": "",
        "confidence": 0.9,
        "status": "decided",
        "evidence_key": f"/auger/{pid}/{did}",
        "scope": "bundle",
    }
    warning = auger.insert_decision(ns, spec)
    assert not warning, warning
    return spec


def record_decision(
    ns: str, did: str, chosen: str, *, scope: str = "", qid: str = ""
) -> tuple[int, str]:
    """`auger answer` with its rejected alternatives — optional scope, optional question it answers."""
    argv = [
        "-n",
        ns,
        "answer",
        "--id",
        did,
        "--domain",
        "9.01",
        "--chosen",
        chosen,
        "--option",
        chosen,
        "--option",
        "keep the shape the siblings already agreed",
        "--why-not",
        "a stream cannot carry the required per-message key",
        "--confidence",
        "0.9",
    ]
    if scope:
        argv += ["--scope", scope]
    if qid:
        argv += ["--question-id", qid]
    return run_cli(argv)


def impact_stub(score: float, calls: list, confidence: float = 0.8):
    """A JEV stub that answers the impact pass's one score, and counts the questions it was asked."""

    def _stub(state, questions, *a, **k):
        calls.append(questions)
        return (
            {
                "answers": {
                    "impact": {
                        "type": "score",
                        "score": score,
                        "confidence": confidence,
                    }
                },
                "usage": {"cost": 1e-05},
                "model": "stub",
            },
            None,
        )

    return _stub


@pytest.fixture
def sibling_ns(live_service: str) -> str:
    """A SECOND ephemeral namespace: where a sibling project's rows live. Real, and torn down."""
    ns = new_bare_ns()
    CREATED.append(ns)
    try:
        rc, out = run_cli(["-n", ns, "init"])
        assert rc == 0 and "declared:" in out, out
        yield ns
    finally:
        problems = teardown_namespace(ns)
        if problems:
            pytest.fail(f"teardown of {ns} was incomplete: " + "; ".join(problems))


def bundled_pair(
    ns: str, sibling_ns: str, monkeypatch, *, decision: tuple | None = None
) -> tuple[str, str]:
    """The shared arrangement: two bundled projects, the sibling's namespace reachable via the seam.

    `member_namespace` is the ONE place the engine turns a member's project name into the namespace
    its rows live in, so that is the seam this points at a real second namespace. Monkeypatched
    rather than worked around: the alternative would be writing rows into whatever namespace the
    fleet's project name happens to name, which no test may do.
    """
    home, sib = two_fleet_projects()
    start_named_project(ns, home, "P-HOME")
    start_named_project(sibling_ns, sib, "P-SIB")
    bundle_with(ns, home, sib)
    monkeypatch.setattr(
        auger, "member_namespace", lambda p: sibling_ns if p == sib else p
    )
    if decision is not None:
        sibling_decision(sibling_ns, "P-SIB", *decision)
    return home, sib


def test_the_gate_links_an_answer_held_in_a_sibling_namespace(
    ns: str, sibling_ns: str, monkeypatch
):
    """SPEC-002 2.1: the gate pools every member's namespace, and the link names where it came from."""
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "a sandbox isolates the filesystem per process"),
    )
    store_questions(
        ns, "P-HOME", ("Q-000001", Q_WATCH, "answered"), ("Q-000002", Q_STORE, "open")
    )
    auger.edge(
        ns,
        "P-HOME",
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage question only exists once the record question is asked",
    )

    seen: list[str] = []

    def fake_recall(namespace, q, limit=5):
        seen.append(namespace)
        if namespace == sibling_ns:
            return [
                {
                    "key": "/auger/P-SIB/D-007",
                    "score": 0.93,
                    "content": "Decision D-007: we chose a sandbox isolates the filesystem per "
                    "process. Rejected alternatives: shared mounts.",
                }
            ]
        return []

    monkeypatch.setattr(auger, "recall", fake_recall)
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.91, calls))

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "linked: 1" in out, out
    assert len(calls) == 1, (
        f"the pooling must stay ONE noul, the model was asked {len(calls)} times"
    )
    assert seen == [ns, sibling_ns], (
        f"the gate did not pool the home namespace and the sibling's, in that order: {seen}"
    )

    assert row(ns, "question", "id=eq.Q-000002")["status"] == "linked"
    sat = rows(ns, "edge", "kind=eq.satisfies")
    assert len(sat) == 1, sat
    assert (sat[0]["src_kind"], sat[0]["src_id"]) == ("decision", "D-007"), sat[0]
    assert (sat[0]["dst_kind"], sat[0]["dst_id"]) == ("question", "Q-000002"), sat[0]
    assert sat[0]["src_project"] == sib, (
        f"the satisfies edge does not name the sibling the answer came from: {sat[0]!r}"
    )
    reason = auger.q_reason(ns, "Q-000002")
    assert "D-007" in reason and sib in reason, reason
    assert sib in out, out


def test_the_gate_survives_a_sibling_namespace_that_cannot_answer(ns: str, monkeypatch):
    """A sibling's namespace is FOREIGN: missing, undeclared or unreachable is 'no evidence there'.

    The asking project's own verb must not die over a namespace it does not own, and it must not
    link to a decision it could not read back — the answer would be invented.
    """
    home, sib = two_fleet_projects()
    start_named_project(ns, home, "P-HOME")
    bundle_with(ns, home, sib)
    missing = TEST_NS_PREFIX + "absent-" + uuid.uuid4().hex[:8]
    assert missing not in api_namespaces(), (
        "this case needs a namespace that does not exist"
    )
    monkeypatch.setattr(auger, "member_namespace", lambda p: missing if p == sib else p)

    store_questions(
        ns, "P-HOME", ("Q-000001", Q_WATCH, "answered"), ("Q-000002", Q_STORE, "open")
    )
    auger.edge(
        ns,
        "P-HOME",
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage question only exists once the record question is asked",
    )
    monkeypatch.setattr(
        auger,
        "recall",
        lambda n, q, limit=5: (
            [
                {
                    "key": "/auger/P-GONE/D-999",
                    "score": 0.95,
                    "content": "a decision we cannot read back",
                }
            ]
            if n == missing
            else []
        ),
    )
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.95, calls))

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open", (
        "the gate linked to a decision that no namespace could serve"
    )
    assert rows(ns, "edge", "kind=eq.satisfies") == []
    assert "Q-000002" in out and "WARNING" in out, out


def _declare_tables_without_the_cross_project_edge_columns(ns: str) -> None:
    """Write the declarations an AUG-009-era namespace has: `edge` without the two project columns.

    Only those columns are removed, from the module's own column lists, so this stays a pre-AUG-010
    shape even as the model grows. It is written BEFORE the API is asked anything, because DuckBrain
    caches a namespace's declarations on its first read of them — that in-process cache is the whole
    reason a pre-AUG-010 namespace refuses a cross-project edge at all.
    """
    tables = os.path.join(ns_path(ns), "tables")
    os.makedirs(tables, exist_ok=True)
    for name, cols in auger.COLS.items():
        keep = [
            (c, t)
            for c, t in cols
            if not (name == "edge" and c in ("src_project", "dst_project"))
        ]
        decl = {
            "name": name,
            "format": "jsonl-objects",
            "primary": "id",
            "glob": f"tables/{name}.jsonl",
            "columns": [{"name": c, "type": t} for c, t in keep],
        }
        with open(os.path.join(tables, f"{name}.table.json"), "w") as f:
            f.write(json.dumps(decl, indent=1))


def test_a_namespace_declared_before_the_cross_project_edge_columns_keeps_working(
    live_service: str, sibling_ns: str, monkeypatch
):
    """A namespace from before AUG-010 stores the cross-project link ANYWAY, and says what it lost.

    The same hard case AUG-009 had for `decision.scope`, one table over: the running server serves
    `edge` without src_project/dst_project, so the first insert that carries one is refused. The link
    must not be lost and must not be lost SILENTLY: the row is stored without the field, its own note
    says so, and the remedy is printed — while the sibling is still named in the question's reason,
    so a reader can still see the answer came from another project.
    """
    ns = new_bare_ns()
    CREATED.append(ns)
    try:
        _declare_tables_without_the_cross_project_edge_columns(ns)
        home, sib = two_fleet_projects()
        start_named_project(ns, home, "P-HOME")
        start_named_project(sibling_ns, sib, "P-SIB")
        bundle_with(ns, home, sib)
        monkeypatch.setattr(
            auger, "member_namespace", lambda p: sibling_ns if p == sib else p
        )
        sibling_decision(
            sibling_ns,
            "P-SIB",
            "D-007",
            "a sandbox isolates the filesystem per process",
        )

        status, live, _ = auger.db(f"/api/ns/{ns}/tables")
        assert status == 200, live
        served = {t["name"]: [c["name"] for c in t["columns"]] for t in live["tables"]}
        assert "src_project" not in served["edge"], (
            f"the API is not serving the pre-AUG-010 declaration this test set up: {served['edge']}"
        )

        store_questions(
            ns,
            "P-HOME",
            ("Q-000001", Q_WATCH, "answered"),
            ("Q-000002", Q_STORE, "open"),
        )
        auger.edge(
            ns,
            "P-HOME",
            "derives_from",
            "question",
            "Q-000002",
            "question",
            "Q-000001",
            source="rule",
            note="the storage question only exists once the record question is asked",
        )
        monkeypatch.setattr(
            auger,
            "recall",
            lambda n, q, limit=5: (
                [
                    {
                        "key": "/auger/P-SIB/D-007",
                        "score": 0.92,
                        "content": "Decision D-007: a sandbox isolates the filesystem per process.",
                    }
                ]
                if n == sibling_ns
                else []
            ),
        )
        monkeypatch.setattr(auger, "jev", gate_stub(0.90, []))

        rc, out = run_cli(["-n", ns, "propagate"])
        assert rc == 0, out
        assert "linked: 1" in out, out
        assert "WARNING" in out and "src_project" in out and "auger init" in out, out
        assert sib in out, (
            out
        )  # the downgrade never loses WHICH project the link crossed to

        sat = rows(ns, "edge", "kind=eq.satisfies")
        assert len(sat) == 1, sat
        assert (sat[0]["src_kind"], sat[0]["src_id"]) == ("decision", "D-007"), sat[0]
        assert (sat[0]["dst_kind"], sat[0]["dst_id"]) == ("question", "Q-000002"), sat[
            0
        ]
        assert not sat[0].get("src_project"), (
            f"a column the API does not serve was stored: {sat[0]!r}"
        )
        assert "stored without src_project" in sat[0]["note"], sat[0]["note"]
    finally:
        assert teardown_namespace(ns) == []


def test_a_bundle_scoped_answer_escalates_a_break_instead_of_rewriting_it(
    ns: str, sibling_ns: str, monkeypatch
):
    """SPEC-002 2.2: an invalidation is recorded and surfaced — and the sibling's row is untouched."""
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    calls: list = []
    monkeypatch.setattr(auger, "jev", impact_stub(2.0, calls))  # 2 of 2: invalidated

    rc, out = record_decision(ns, "D-012", "the envelope is NDJSON", scope="bundle")
    assert rc == 0, out
    assert "D-012 recorded" in out and "scope bundle" in out, out
    assert len(calls) == 1, (
        f"one answer asked the model {len(calls)} times for one member"
    )
    assert (
        f"ESCALATION: answer D-012 breaks {sib}'s D-007 — recorded, not rewritten"
        in out
    ), out

    brk = rows(ns, "edge", "kind=eq.breaks")
    assert len(brk) == 1, brk
    assert (brk[0]["src_kind"], brk[0]["src_id"]) == ("decision", "D-012"), brk[0]
    assert (brk[0]["dst_kind"], brk[0]["dst_id"]) == ("decision", "D-007"), brk[0]
    assert brk[0]["dst_project"] == sib, (
        f"the break does not name the sibling: {brk[0]!r}"
    )
    assert brk[0]["source"] == "impact" and brk[0]["confidence"] == pytest.approx(
        2.0
    ), brk[0]

    esc = rows(ns, "escalation", "")
    assert len(esc) == 1, esc
    assert sib in esc[0]["question"] and "D-007" in esc[0]["question"], esc
    assert esc[0]["project_id"] == "P-HOME" and esc[0]["status"] == "open", esc

    # recorded, NOT rewritten: the sibling's own row still says what the sibling decided.
    other = row(sibling_ns, "decision", "id=eq.D-007")
    assert other["chosen"] == "the envelope is one JSON object per message", other


def test_a_bundle_scoped_answer_that_changes_a_sibling_records_an_affects_edge(
    ns: str, sibling_ns: str, monkeypatch
):
    """The middle verdict: `affects`, with the sibling named — and no escalation."""
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    calls: list = []
    monkeypatch.setattr(auger, "jev", impact_stub(1.0, calls))  # 1 of 2: changed

    rc, out = record_decision(ns, "D-015", "the envelope is NDJSON", scope="bundle")
    assert rc == 0, out
    assert "ESCALATION" not in out, out
    aff = rows(ns, "edge", "kind=eq.affects")
    assert len(aff) == 1, aff
    assert (aff[0]["src_id"], aff[0]["dst_id"]) == ("D-015", "D-007"), aff[0]
    assert aff[0]["dst_project"] == sib and aff[0]["source"] == "impact", aff[0]
    assert rows(ns, "escalation", "") == [], "a CHANGE was escalated as a break"


def test_a_bundle_scoped_answer_that_leaves_a_sibling_unchanged_writes_nothing(
    ns: str, sibling_ns: str, monkeypatch
):
    """Unchanged -> nothing on the record, for a member the pass did ask about."""
    _home, _sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    calls: list = []
    monkeypatch.setattr(auger, "jev", impact_stub(0.0, calls))  # 0 of 2: unchanged

    rc, out = record_decision(ns, "D-016", "the envelope is NDJSON", scope="bundle")
    assert rc == 0 and "ESCALATION" not in out, out
    assert len(calls) == 1, f"the member was not asked at all: {calls}"
    assert rows(ns, "edge", "") == [], "an unchanged sibling gained an edge"
    assert rows(ns, "escalation", "") == []


def test_a_project_scoped_answer_crosses_no_boundary_at_all(
    ns: str, sibling_ns: str, monkeypatch
):
    """The rule that keeps the pass finite: only BUNDLE-scoped decisions cross the boundary."""
    _home, _sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    calls: list = []
    monkeypatch.setattr(
        auger, "jev", impact_stub(2.0, calls)
    )  # a verdict that WOULD break it

    rc, out = record_decision(
        ns, "D-013", "the envelope is NDJSON"
    )  # no --scope: project-local
    assert rc == 0, out
    assert calls == [], (
        f"a project-local answer asked the model about its bundle: {calls}"
    )
    assert rows(ns, "edge", "") == [], (
        "a project-local answer wrote a cross-project edge"
    )
    assert rows(ns, "escalation", "") == []
    assert row(ns, "decision", "id=eq.D-013")["chosen"] == "the envelope is NDJSON"
    assert (
        row(sibling_ns, "decision", "id=eq.D-007")["chosen"]
        == "the envelope is one JSON object per message"
    )


def test_an_unreachable_model_in_the_impact_pass_skips_the_member_and_still_records(
    ns: str, sibling_ns: str, monkeypatch
):
    """Fail-closed: an unknown verdict writes NO edge, is surfaced, and does not fail the answer."""
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    monkeypatch.setattr(
        auger, "jev", lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401")
    )

    rc, out = record_decision(ns, "D-014", "the envelope is NDJSON", scope="bundle")
    assert rc == 0, out
    assert "D-014 recorded" in out, out
    assert "WARNING" in out and sib in out and "JEV" in out, out
    assert rows(ns, "edge", "") == [], (
        "an edge was written on a verdict the model never gave"
    )
    assert rows(ns, "escalation", "") == []
    assert (
        row(sibling_ns, "decision", "id=eq.D-007")["chosen"]
        == "the envelope is one JSON object per message"
    )


def test_a_break_against_a_sibling_never_moots_a_local_question(
    ns: str, sibling_ns: str, monkeypatch
):
    """Ids are per NAMESPACE, so a sibling's D-007 and this project's D-007 can both exist.

    The rule walk reads `breaks` edges to moot the questions a dead decision answered. A break
    recorded against a SIBLING's D-007 (SPEC-002 2.2) is an escalation for that sibling, not this
    project's D-007 dying — reading it as local would moot a question nobody invalidated.
    """
    _home, _sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    store_questions(ns, "P-HOME", ("Q-000001", Q_WATCH, "answered"))
    assert record_decision(ns, "D-007", "single SQLite file", qid="Q-000001")[0] == 0
    monkeypatch.setattr(auger, "jev", impact_stub(2.0, []))
    rc, out = record_decision(ns, "D-012", "the envelope is NDJSON", scope="bundle")
    assert rc == 0 and "ESCALATION" in out, out

    rc, out = run_cli(["-n", ns, "propagate", "--no-gate"])
    assert rc == 0, out
    assert "moot: 0   reopened: 0" in out, out
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered", (
        "a break against a SIBLING's D-007 mooted this project's own D-007"
    )


def test_the_gate_with_no_bundle_membership_recalls_its_own_namespace_only(
    project: dict, monkeypatch
):
    """The no-bundle path must not regress: one namespace, one noul, a link with no src_project."""
    ns, pid = project["ns"], project["pid"]
    assert record_decision(ns, "D-001", "single SQLite file")[0] == 0
    store_questions(
        ns, pid, ("Q-000001", Q_WATCH, "answered"), ("Q-000002", Q_STORE, "open")
    )
    auger.edge(
        ns,
        pid,
        "derives_from",
        "question",
        "Q-000002",
        "question",
        "Q-000001",
        source="rule",
        note="the storage question only exists once the record question is asked",
    )

    seen: list[str] = []

    def fake_recall(namespace, q, limit=5):
        seen.append(namespace)
        return (
            [
                {
                    "key": f"/auger/{pid}/D-001",
                    "score": 0.9,
                    "content": "Decision D-001: we chose single SQLite file.",
                }
            ]
            if namespace == ns
            else []
        )

    monkeypatch.setattr(auger, "recall", fake_recall)
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.93, calls))

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert seen == [ns], f"a project with no bundle membership recalled {seen}"
    assert len(calls) == 1 and "linked: 1" in out, out
    sat = rows(ns, "edge", "kind=eq.satisfies")
    assert len(sat) == 1 and sat[0]["src_id"] == "D-001", sat
    assert not sat[0].get("src_project"), (
        f"a same-namespace link carries a src_project: {sat[0]!r}"
    )


# ================================================================= the feedback engine (AUG-001)
# docs/DESIGN.md R12 + its open decision 4, built on the graph AUG-007..AUG-011 landed, with the
# two recorded leanings of SPEC-001 section 7 taken as the working defaults: Q2 — a LARGE MODEL
# proposes and JEV gates and ranks; Q5 — the run is unattended, stops only at a priority judgment,
# records an escalation with a default and continues on the default.
#
# The MODELS are faked in the cases below (the proposer is an external chat model and JEV is an
# external decisions model; what is under test is the engine's behaviour AROUND them — what it
# stores, what it refuses to ask, and what it does with a ceiling). `test_feedback_live_*` at the
# end runs the real pair. The SUBSTRATE is never faked: every project, decision, question, facet,
# edge and escalation asserted below is re-read from DuckBrain.
#
# Every acceptance criterion of AUG-001 is asserted on STORED ROWS, not on stdout:
#   (1) a 0.41-confidence decision produces a named follow-up question  -> the `question` row, its
#       facets and the `opens` edge;
#   (2) a question the gate scores >= 0.55 is REFUSED, not asked        -> state `linked` + the
#       `satisfies` edge, and an EMPTY askable surface;
#   (3) the run stops at the ceiling and records `budget-thin: true`    -> the stopped questions
#       keep their rows in state `budget_thin` with their reason, plus the marker escalation row.
_PROBE_ERR: dict = {}
FOLLOWUP_Q = (
    "How is the staging table drained if the writer stops halfway through a run?"
)


def proposer_stub(texts, calls: list):
    """A proposer stub: one scripted question per call, and the state each call was given."""
    queue = list(texts)

    def _stub(state, *a, **k):
        calls.append(state)
        if not queue:
            return "", "the stub ran out of questions"
        return queue.pop(0), ""

    return _stub


def proposer_down(err: str = "all 6 proposer keys failed; last: HTTP 401"):
    """A proposer that is unreachable — the fail-closed case."""

    def _stub(state, *a, **k):  # noqa: ARG001 - mirrors the real proposer's signature
        return "", err

    return _stub


def recall_hits(ns: str, pid: str, *decisions: str):
    """The gate's retrieval, stubbed to the named decisions' own evidence rows.

    The keys are the ones `auger answer` embeds, so the gate can name a decision from a hit —
    which is what makes the difference between a LINK and an unlinkable verdict testable.
    """

    def _stub(namespace, q, limit=5):  # noqa: ARG001 - mirrors recall's signature
        return (
            [
                {
                    "key": f"/auger/{pid}/{d}",
                    "score": 0.9,
                    "content": f"Decision {d}: we chose the recorded option.",
                }
                for d in decisions
            ]
            if namespace == ns
            else []
        )

    return _stub


def proposer_or_skip() -> None:
    """Fail loudly (or skip loudly) when the question proposer cannot be reached."""
    if "err" not in _PROBE_ERR:
        text, err = auger.propose_question(
            "THE DECISION WHOSE CONFIDENCE IS TOO LOW TO BUILD ON:\n"
            "D-000 (probe): the probe\nconfidence: 0.4\n"
            "why the rejected alternatives lost: the probe"
        )
        _PROBE_ERR["err"] = err if not text else ""
    if _PROBE_ERR["err"]:
        msg = (
            f"the question proposer is unreachable ({_PROBE_ERR['err']}) — the feedback engine "
            f"is fail-closed and proposes nothing without it. Remedy: a live OpenRouter key in "
            f"~/.hermes/.env or OPENROUTER_API_KEY."
        )
        if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
            raise AssertionError(msg)
        pytest.skip(msg)


def test_feedback_turns_a_thin_decision_into_a_named_followup(
    decided: dict, monkeypatch
):
    """Criterion 1: the 0.41 decision becomes a NAMED question on the record — its own row, its
    facets, and the `opens` edge that makes the branch trackable from the decision that raised it."""
    ns, pid = decided["ns"], decided["pid"]
    assert [d["id"] for d in auger.thin_decisions(ns, pid)] == ["D-002"], (
        "the thin-decision pick does not start from the lowest confidence"
    )
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, calls))
    monkeypatch.setattr(auger, "propose_question", proposer_stub([FOLLOWUP_Q], []))

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert FOLLOWUP_Q in out and "ASKED" in out, out
    assert len(calls) == 1, (
        f"the gate did not run exactly once on the proposed question: {calls}"
    )

    q = row(ns, "question", "qclass=eq.follow_up")
    assert q["status"] == "open", (
        "a question below the answered threshold must be ASKED"
    )
    assert q["text"] == FOLLOWUP_Q
    assert q["domain"] == D002["domain"], (
        "the drill left the subject the decision lives in"
    )
    assert q["ring"] == 1 and q["jev_checked_at"], (
        q
    )  # depth recorded; the gate's verdict logged
    assert q["jev_already_answered"] == pytest.approx(0.10), q

    opens = row(ns, "edge", "kind=eq.opens")
    assert (opens["src_kind"], opens["src_id"]) == ("decision", "D-002")
    assert (opens["dst_kind"], opens["dst_id"]) == ("question", q["id"])
    assert opens["source"] == "rule", opens
    # Nothing was linked and nothing derived: D-002 answers no question and the gate refused nothing.
    assert rows(ns, "edge", "kind=eq.satisfies") == []
    assert rows(ns, "edge", "kind=eq.derives_from") == []

    facets = rows(ns, "facet", f"question_id=eq.{q['id']}&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets
    assert all(f["status"] == "open" for f in facets), (
        f"an OPEN question must keep its facets open — it is only partly resolved: {facets}"
    )

    assert [x["id"] for x in auger.askable_questions(ns, pid)] == [q["id"]], (
        "the new question is not on the askable surface: the batch did not reach the next pass"
    )
    # The decision itself is untouched: the engine asks, it does not re-decide.
    assert row(ns, "decision", "id=eq.D-002")["confidence"] == pytest.approx(0.41)


def test_feedback_refuses_a_question_the_gate_already_answers(
    decided: dict, monkeypatch
):
    """Criterion 2: a proposed question JEV scores >= T_ANSWERED is REFUSED — recorded `linked` to
    the answer that already exists, with a `satisfies` edge, and never asked of anyone."""
    ns, pid = decided["ns"], decided["pid"]
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-001"))
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.91, calls))
    monkeypatch.setattr(
        auger,
        "propose_question",
        proposer_stub(["What file layout should the record of seen files use?"], []),
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert "REFUSED" in out and "NOT asked" in out and "asked: 0" in out, out
    assert len(calls) == 1, f"the gate did not run exactly once: {calls}"

    q = row(ns, "question", "qclass=eq.follow_up")
    assert q["status"] == "linked", "the question was ASKED after the gate refused it"
    assert q["jev_already_answered"] == pytest.approx(0.91), q
    assert q["jev_checked_at"], q
    reason = auger.q_reason(ns, q["id"])
    assert "already answered" in reason and "D-001" in reason, reason

    sat = row(ns, "edge", "kind=eq.satisfies")
    assert (sat["src_kind"], sat["src_id"]) == ("decision", "D-001")
    assert (sat["dst_kind"], sat["dst_id"]) == ("question", q["id"])
    assert sat["source"] == "gate" and sat["confidence"] == pytest.approx(0.91), sat

    # The branch is still trackable: the question was RAISED by D-002 and satisfied by D-001.
    assert row(ns, "edge", "kind=eq.opens")["src_id"] == "D-002"
    assert auger.askable_questions(ns, pid) == [], (
        "a refused question reached the askable surface"
    )
    assert row(ns, "decision", "id=eq.D-002")["confidence"] == pytest.approx(0.41)


def test_feedback_stops_at_the_ceiling_and_records_budget_thin(
    project: dict, monkeypatch
):
    """Criterion 3: the run STOPS at the ceiling and records `budget-thin: true`. The questions it
    could not ask keep their TEXT and their reason in state `budget_thin` — recorded, never dropped."""
    ns, pid = project["ns"], project["pid"]
    for did, conf in (("D-002", 0.41), ("D-003", 0.35), ("D-004", 0.30)):
        rc, out = answer(
            ns,
            did=did,
            domain="4.06",
            chosen=f"option for {did}",
            options=[f"option for {did}", "the other one"],
            why_not="the seed forbids the other one",
            confidence=conf,
        )
        assert rc == 0 and f"{did} recorded" in out, out
    assert [d["id"] for d in auger.thin_decisions(ns, pid)] == [
        "D-004",
        "D-003",
        "D-002",
    ], "the thinnest decision is not the one the run starts from"

    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    monkeypatch.setattr(
        auger,
        "propose_question",
        proposer_stub([f"Follow-up for {d}?" for d in ("D-004", "D-003", "D-002")], []),
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert "budget-thin: true" in out, out
    assert "asked: 1" in out and "budget-thin: 2" in out, out

    stored = rows(ns, "question", "qclass=eq.follow_up&order=id.asc")
    assert len(stored) == 3, (
        f"the ceiling DROPPED questions instead of recording them: {stored}"
    )
    asked = [q for q in stored if q["status"] == "open"]
    stopped = [q for q in stored if q["status"] == "budget_thin"]
    assert len(asked) == 1 and len(stopped) == 2, [q["status"] for q in stored]
    assert all(q["text"].endswith("?") for q in stored), (
        f"a stopped question lost its text: {[q['text'] for q in stored]}"
    )

    opens = {e["dst_id"]: e["src_id"] for e in rows(ns, "edge", "kind=eq.opens")}
    assert opens[asked[0]["id"]] == "D-004", (
        f"the run did not ask its thinnest decision: {opens}"
    )
    for q in stopped:
        reason = auger.q_reason(ns, q["id"])
        assert "budget-thin" in reason and "ceiling of 1" in reason, reason
        facets = rows(ns, "facet", f"question_id=eq.{q['id']}")
        assert facets and all(f["status"] == "closed" for f in facets), (
            f"a budget_thin question has no recorded resolution: {facets}"
        )

    # The run-level marker, on a stored row rather than only in the output.
    esc = rows(ns, "escalation", "")
    assert len(esc) == 1, esc
    assert "budget-thin: true" in esc[0]["question"], esc[0]
    assert (
        "ceiling of 1" in esc[0]["question"]
        and "2 proposed question(s)" in esc[0]["question"]
    ), esc[0]
    assert esc[0]["default_action"] and esc[0]["risk"] and esc[0]["status"] == "open", (
        esc[0]
    )
    assert esc[0]["project_id"] == pid and stopped[0]["id"] in esc[0]["options"], esc[0]

    assert [q["id"] for q in auger.askable_questions(ns, pid)] == [asked[0]["id"]]


def test_a_budget_thin_question_is_still_owed_to_its_decision(
    project: dict, monkeypatch
):
    """The ceiling bounds ASKING, not the work: a question the ceiling stopped was never asked, so
    its decision is still owed one and the next run picks the decision up. The stopped row keeps
    its own state and its reason — nothing is rewritten and nothing is deleted."""
    ns, pid = project["ns"], project["pid"]
    for did, conf in (("D-002", 0.41), ("D-003", 0.35)):
        assert (
            answer(
                ns,
                did=did,
                domain="4.06",
                chosen=f"option for {did}",
                options=[f"option for {did}", "the other one"],
                why_not="the seed forbids the other one",
                confidence=conf,
            )[0]
            == 0
        )
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    calls: list = []
    monkeypatch.setattr(
        auger,
        "propose_question",
        proposer_stub(
            [
                "First run for D-003?",
                "First run for D-002?",
                "Second run for D-003?",
                "Second run for D-002?",
            ],
            calls,
        ),
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "0"])
    assert rc == 0 and "budget-thin: true" in out, out
    assert "asked: 0" in out and "budget-thin: 2" in out, out
    assert [q["status"] for q in rows(ns, "question", "order=id.asc")] == [
        "budget_thin"
    ] * 2
    assert len(calls) == 2, calls

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert "already drilled" not in out, (
        "a budget_thin question was treated as a live drill"
    )
    stored = rows(ns, "question", "order=id.asc")
    assert [q["status"] for q in stored] == [
        "budget_thin",
        "budget_thin",
        "open",
        "budget_thin",
    ], [q["status"] for q in stored]
    asked = [q for q in stored if q["status"] == "open"]
    assert len(asked) == 1 and "Second run for D-003?" == asked[0]["text"]
    assert opens_of(ns, asked[0]["id"]) == "D-003", (
        "the next run drilled a decision that was not the thinnest still waiting"
    )


def test_feedback_does_not_drill_a_decision_twice(decided: dict, monkeypatch):
    """A decision with a LIVE follow-up is left alone: the question is on the board already, and a
    second one would be the duplicate the gate exists to prevent."""
    ns, pid = decided["ns"], decided["pid"]
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    calls: list = []
    monkeypatch.setattr(
        auger, "propose_question", proposer_stub([FOLLOWUP_Q, "a duplicate?"], calls)
    )

    assert run_cli(["-n", ns, "feedback", "--budget", "2"])[0] == 0
    assert len(calls) == 1, calls

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "2"])
    assert rc == 0, out
    assert "already drilled" in out and "D-002" in out, out
    assert len(calls) == 1, "a decision with a live follow-up was drilled a second time"
    assert len(rows(ns, "question", "")) == 1
    assert len(rows(ns, "edge", "kind=eq.opens")) == 1


def test_feedback_records_a_priority_judgment_and_continues_on_the_default(
    project: dict, monkeypatch
):
    """Q5: a decision thin enough to be a priority judgment is ESCALATED with a default, and the
    unattended run continues on that default instead of waiting for a person."""
    ns, pid = project["ns"], project["pid"]
    assert (
        answer(
            ns,
            did="D-002",
            domain="4.06",
            chosen="staging table",
            options=["staging table", "row locking"],
            why_not="no concurrent writer",
            confidence=0.21,
        )[0]
        == 0
    )
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    monkeypatch.setattr(
        auger, "propose_question", proposer_stub(["Who drains the staging table?"], [])
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out

    esc = row(ns, "escalation", "")
    assert esc["project_id"] == pid and esc["status"] == "open", esc
    assert "priority judgment" in esc["question"] and "0.21" in esc["question"], esc
    assert "DEFAULT" in esc["default_action"] and "D-002" in esc["default_action"], esc
    assert "0.21" in esc["risk"], esc
    assert "escalations:" in out and esc["id"] in out, out

    # ...and it CONTINUED on the default: the question it would have asked was asked.
    q = row(ns, "question", "qclass=eq.follow_up")
    assert q["status"] == "open", (
        "the run stopped at the priority judgment instead of continuing"
    )
    assert q["jev_already_answered"] == pytest.approx(0.10)


def test_feedback_invents_nothing_when_the_proposer_is_unreachable(
    decided: dict, monkeypatch
):
    """Fail-closed: no question is proposed, so no question is stored, asked or linked — and the
    decisions the run could not drill are named rather than silently skipped."""
    ns = decided["ns"]
    monkeypatch.setattr(auger, "propose_question", proposer_down())

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 1, out
    assert "proposer unavailable" in out and "D-002" in out, out
    assert rows(ns, "question", "") == [], "a question was stored without a proposal"
    assert rows(ns, "edge", "") == []
    assert rows(ns, "escalation", "") == []
    assert row(ns, "decision", "id=eq.D-002")["confidence"] == pytest.approx(0.41)


def test_feedback_asks_nothing_when_the_gate_is_unreachable(decided: dict, monkeypatch):
    """Fail-closed on the other side: with no verdict from the gate, the proposed question is NOT
    asked (that is exactly the duplicate the gate prevents) — and its text is printed, not lost."""
    ns, pid = decided["ns"], decided["pid"]
    monkeypatch.setattr(auger, "propose_question", proposer_stub([FOLLOWUP_Q], []))
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(
        auger, "jev", lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401")
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 1, out
    assert "gate unavailable" in out and FOLLOWUP_Q in out and "D-002" in out, out
    assert rows(ns, "question", "") == [], (
        "a question was asked on a verdict the gate never gave"
    )
    assert rows(ns, "edge", "") == []


def test_feedback_does_not_ask_a_question_it_cannot_link(decided: dict, monkeypatch):
    """The gate can score a question answered while the winning evidence names NO decision. Asking it
    would ask a duplicate, and linking it would invent a link — so it is neither, and the reason is
    on the record."""
    ns = decided["ns"]
    monkeypatch.setattr(auger, "propose_question", proposer_stub([FOLLOWUP_Q], []))
    monkeypatch.setattr(
        auger,
        "recall",
        lambda namespace, q, limit=5: (
            [
                {
                    "key": "/notes/somewhere-else",
                    "score": 0.9,
                    "content": "an answer, filed elsewhere",
                }
            ]
            if namespace == ns
            else []
        ),
    )
    monkeypatch.setattr(auger, "jev", gate_stub(0.88, []))

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert "UNLINKED" in out and "not asked" in out and "D-002" in out, out
    assert rows(ns, "question", "") == [], (
        "an unlinkable question was stored as if it were a link"
    )
    assert rows(ns, "edge", "") == []


@pytest.mark.jev
def test_feedback_live_proposes_gates_and_asks_a_real_question(decided: dict):
    """The live arm: the REAL large model proposes from the real decision, and the REAL gate judges
    the proposal. Only the outcome ACCEPTANCE is asserted here because the verdict is the model's:
    either a question row exists (asked, or refused-and-linked with the `satisfies` edge), or the
    run said out loud that the evidence named no decision. What must hold every time is that the
    decision was drilled and that the row was stored — never that a model agreed with a plan."""
    proposer_or_skip()
    jev_or_skip()
    ns, pid = decided["ns"], decided["pid"]

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    assert "proposed: 1" in out, f"the live proposer produced no question: {out}"
    assert "D-002" in out, out

    stored = rows(ns, "question", "qclass=eq.follow_up")
    if not stored:
        assert "UNLINKED" in out, (
            f"no question was stored and no reason was given (fail-closed must be loud): {out}"
        )
        return
    q = stored[0]
    assert q["text"].strip() and "?" in q["text"], q
    assert q["status"] in ("open", "linked"), q
    assert q["jev_checked_at"] and q["jev_already_answered"] >= 0, q
    assert opens_of(ns, q["id"]) == "D-002", (
        "the live question is not trackable back to the decision that raised it"
    )
    if q["status"] == "linked":
        sat = row(ns, "edge", f"kind=eq.satisfies&dst_id=eq.{q['id']}")
        assert sat["source"] == "gate" and sat["confidence"] >= auger.T_ANSWERED, sat
    else:
        assert [x["id"] for x in auger.askable_questions(ns, pid)] == [q["id"]]


def opens_of(ns: str, question_id: str) -> str:
    """The decision an `opens` edge names as the raiser of a question — "" when there is none."""
    for e in rows(ns, "edge", f"kind=eq.opens&dst_id=eq.{question_id}"):
        if e.get("src_kind") == "decision":
            return e["src_id"]
    return ""


# ---------------------------------------------------------------- the proposer and the governor
# Both of these are pure functions with no live dependency, and both are places where a wrong
# answer is cheap to make and expensive to find: a parser that accepts an ANSWER as a question
# stores a branch nobody asked for, and a governor that ignores the number it was given is not one.
def reply(content) -> dict:
    """A chat-completion reply in the shape `propose_question` parses."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_the_proposer_parser_takes_one_question_and_rejects_everything_else():
    assert (
        auger.question_from_reply(
            reply("What drains the staging table if the writer dies?")
        )
        == "What drains the staging table if the writer dies?"
    )
    # The decorations a large model adds unasked: numbering, a bullet, a "Q1:" label, quotes.
    assert (
        auger.question_from_reply(reply('1. "What drains the staging table?"'))
        == "What drains the staging table?"
    )
    assert (
        auger.question_from_reply(reply("Q2: Who owns the drain after delivery?"))
        == "Who owns the drain after delivery?"
    )
    assert (
        auger.question_from_reply(reply("\n\n- Is the drain retried?\n- Is it logged?"))
        == "Is the drain retried?"
    )  # one question, as asked for
    # Anything that is not a question is not a question — an answer, a list of options, a refusal.
    assert (
        auger.question_from_reply(reply("The staging table is drained by the writer."))
        == ""
    )
    assert (
        auger.question_from_reply(reply("Here are three candidates:\n- one\n- two"))
        == ""
    )
    assert auger.question_from_reply(reply("   ")) == ""
    assert (
        auger.question_from_reply(reply("x" * 500 + "?"))
        == ("x" * 500 + "?")[: auger.QUESTION_MAX]
    )
    # Malformed completions are "" and never an exception: the caller turns "" into a refusal.
    for bad in (
        None,
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": "not a list"},
    ):
        assert auger.question_from_reply(bad) == "", bad


def test_the_governor_refuses_a_ceiling_it_cannot_obey(monkeypatch):
    """The ceiling comes from the environment when it names one; a value that is not a
    non-negative integer is REFUSED, and the refusal names the variable."""
    monkeypatch.delenv(auger.BUDGET_ENV, raising=False)
    assert auger.feedback_budget() == auger.FEEDBACK_BUDGET
    monkeypatch.setenv(auger.BUDGET_ENV, "2")
    assert auger.feedback_budget() == 2
    monkeypatch.setenv(auger.BUDGET_ENV, " 0 ")
    assert auger.feedback_budget() == 0
    for bad in ("two", "1.5", "-1"):
        monkeypatch.setenv(auger.BUDGET_ENV, bad)
        code, msg, _out = run_cli_exit(["-n", "irrelevant", "feedback"])
        assert code == 1 and auger.BUDGET_ENV in msg, (bad, msg)
    # ...and the CLI flag is held to the same rule, without touching a namespace.
    code, msg, _out = run_cli_exit(["-n", "irrelevant", "feedback", "--budget", "-2"])
    assert code == 1 and "negative" in msg, msg


def test_feedback_with_nothing_thin_asks_nothing(project: dict, monkeypatch):
    """A project whose decisions are all above the threshold has no question to raise, and the run
    says so instead of inventing one."""
    ns = project["ns"]
    assert (
        answer(
            ns,
            did="D-001",
            domain="4.05",
            chosen="single SQLite file",
            options=["single SQLite file", "Postgres"],
            why_not="no service allowed",
            confidence=0.82,
        )[0]
        == 0
    )
    monkeypatch.setattr(
        auger, "propose_question", proposer_down("the proposer must not be called")
    )

    rc, out = run_cli(["-n", ns, "feedback", "--budget", "3"])
    assert rc == 0, out
    assert "(none" in out and "proposed: 0   asked: 0" in out, out
    assert rows(ns, "question", "") == []
    assert rows(ns, "edge", "") == []
    assert rows(ns, "escalation", "") == []


# ================================================================= verdict (AUG-003 / DESIGN R11)
# A dump is judged and the judgement is ON THE RECORD, with the reasons and the judge. The
# on-file arm is deterministic; the --ask-jev arm is `@pytest.mark.jev` — it is fail-closed on
# purpose, so a case that asserts a row must first prove the judge is reachable.
def test_a_good_verdict_round_trips_with_its_reasons(decided: dict):
    """--good records a row; --list shows it with the verdict, the judge and the reasons."""
    ns = decided["ns"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "verdict",
            "--good",
            "D-001=single SQLite file",
            "--reasons",
            "one box, no extra services",
        ]
    )
    assert rc == 0, out
    assert "recorded  (verdict good" in out, out
    assert "config judged: D-001=single SQLite file" in out, out

    rc, out = run_cli(["-n", ns, "verdict", "--list"])
    assert rc == 0, out
    assert "GOOD" in out and "by human" in out, out
    assert "config: D-001=single SQLite file" in out, out
    assert "reasons: one box, no extra services" in out, out

    # The row itself: what was STORED, not what was printed.
    [v] = rows(ns, "verdict", "")
    assert v["verdict"] == "good" and v["config_summary"] == "D-001=single SQLite file"
    assert v["reasons"] == "one box, no extra services" and v["judged_by"] == "human"
    assert v["project_id"] == decided["pid"] and v["id"].startswith("V-"), v


def test_a_bad_verdict_records_the_word_the_command_gave(ns: str):
    """--bad records BAD (with who judged it), and the closed set is refused BY NAME at the write."""
    auger.insert(
        ns,
        "project",
        {
            "id": "P-V",
            "name": "v",
            "seed": "",
            "core_statement": "",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    # A model id as the judge: the row carries who said it, not just that someone did.
    rc, out = run_cli(
        [
            "-n",
            ns,
            "verdict",
            "--bad",
            "D-001=Postgres",
            "--reasons",
            "no service allowed",
            "--judged-by",
            "gpt-5.6",
        ]
    )
    assert rc == 0, out
    assert "recorded  (verdict bad" in out and "judged_by gpt-5.6" in out, out
    [v] = rows(ns, "verdict", "")
    assert v["verdict"] == "bad" and v["judged_by"] == "gpt-5.6", v

    # The closed set (EDGE_KINDS' rule): a word outside VERDICT_WORDS is refused BY NAME,
    # and nothing is written.
    with pytest.raises(SystemExit) as ei:
        auger.record_verdict(ns, {"id": "V-999999", "verdict": "excellent"})
    assert "unknown verdict word 'excellent'" in str(ei.value), str(ei.value)
    assert "expected one of good, bad" in str(ei.value), str(ei.value)
    assert rows(ns, "verdict", "verdict=eq.excellent") == []


def test_mutually_exclusive_flags_refuse_and_write_nothing(ns: str):
    """Both --good and --bad (or neither) is a refusal, and no verdict row exists after."""
    for argv in (["--good", "X", "--bad", "Y", "--reasons", "r"], ["--reasons", "r"]):
        code, msg, out = run_cli_exit(["-n", ns, "verdict", *argv])
        assert code == 1, (argv, msg)
        assert "exactly one of --good/--bad" in msg, msg
        assert "nothing recorded" in msg or "nothing" in msg, msg
    assert rows(ns, "verdict", "") == []


def test_status_counts_verdicts_when_any_exist(ns: str):
    """The coverage line picks the register up (R11's tally), and stays silent when empty."""
    pid = "P-V"
    auger.insert(
        ns,
        "project",
        {
            "id": pid,
            "name": "v",
            "seed": "",
            "core_statement": "",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0 and "verdicts" not in out, out

    auger.record_verdict(
        ns,
        {
            "id": "V-000001",
            "project_id": pid,
            "config_summary": "cfg",
            "verdict": "good",
            "reasons": "r",
            "judged_by": "human",
            "source": "human",
            "note": "",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    auger.record_verdict(
        ns,
        {
            "id": "V-000002",
            "project_id": pid,
            "config_summary": "cfg",
            "verdict": "bad",
            "reasons": "r2",
            "judged_by": "jev",
            "confidence": 0.71,
            "source": "jev",
            "note": "",
            "created_at": "2026-01-01T00:01:00Z",
        },
    )
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "verdicts 2 (1 good, 1 bad)" in out, out


def test_ask_jev_judges_the_real_dump_and_records_the_model_verdict(
    decided: dict, monkeypatch
):
    """--ask-jev renders the DUMP (the artifact, not the summary), records judged_by jev."""
    ns = decided["ns"]
    seen = {}

    def stub(state, questions):
        seen["state"] = state
        return (
            {
                "answers": {"verdict": {"type": "noul", "noul": 0.93}},
                "usage": {"cost": 2e-05},
                "model": "stub-model",
            },
            None,
        )

    monkeypatch.setattr(auger, "jev", stub)
    rc, out = run_cli(["-n", ns, "verdict", "--ask-jev"])
    assert rc == 0, out
    assert "judged_by jev" in out and "noul 0.93" in out, out
    assert "ACTIVE CONFIGURATION:" in seen["state"], seen["state"]
    assert "ACTIVE CONFIGURATION: D-001=single SQLite file" in seen["state"], seen[
        "state"
    ]
    # The row's summary names the judged configuration, and the confidence is the model's.
    [v] = rows(ns, "verdict", "")
    assert v["verdict"] == "good" and v["judged_by"] == "jev"
    assert v["confidence"] == pytest.approx(0.93)
    assert "ACTIVE CONFIGURATION: D-001=single SQLite file" in v["config_summary"], v


def test_ask_jev_judges_a_hypothesis_from_config_specs(decided: dict, monkeypatch):
    """--config renders the HYPOTHETICAL dump: JEV judges the what-if, never the stored state."""
    ns = decided["ns"]
    seen = {}

    def stub(state, questions):
        seen["state"] = state
        return (
            {
                "answers": {"verdict": {"type": "noul", "noul": 0.2}},
                "usage": {"cost": 1e-05},
                "model": "stub",
            },
            None,
        )

    monkeypatch.setattr(auger, "jev", stub)
    rc, out = run_cli(["-n", ns, "verdict", "--ask-jev", "--config", "D-001=Postgres"])
    assert rc == 0, out
    assert "HYPOTHETICAL" in seen["state"], seen["state"]
    assert "D-001=Postgres" in seen["state"], seen["state"]
    [v] = rows(ns, "verdict", "")
    assert v["verdict"] == "bad", (
        v
    )  # noul 0.2 is below the threshold the module maps words by
    # The hypothesis changed NOTHING: the stored state is exactly what the record still holds.
    after = auger.select(ns, "option", "decision_id=eq.D-001")
    assert [o["active"] for o in sorted(after, key=lambda o: o["id"])] == [
        True,
        False,
    ], after


def test_ask_jev_records_nothing_when_the_judge_is_unreachable(
    decided: dict, monkeypatch
):
    """Fail-closed, the module's rule for every model call: no judge, NO ROW, non-zero exit."""
    ns = decided["ns"]
    monkeypatch.setattr(
        auger, "jev", lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401")
    )
    code, msg, out = run_cli_exit(["-n", ns, "verdict", "--ask-jev"])
    assert code == 1, msg
    assert "JEV unavailable" in msg and "nothing recorded" in msg, msg
    assert rows(ns, "verdict", "") == []


@pytest.mark.jev
def test_ask_jev_records_a_real_verdict_on_a_live_key(decided: dict):
    """The live arm: a real key records judged_by jev with the model's confidence."""
    jev_or_skip()
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "verdict", "--ask-jev"])
    assert rc == 0, out
    assert "judged_by jev" in out, out
    [v] = rows(ns, "verdict", "")
    assert v["judged_by"] == "jev" and isinstance(v.get("confidence"), (int, float)), v
    assert v["verdict"] in auger.VERDICT_WORDS, v


def test_render_dump_matches_the_dump_verb_structure(decided: dict):
    """One renderer: what --ask-jev judges is what `dump` prints, not a paraphrase of it.

    Structure, not bytes: the render stamps its `generated` moment, so two renders taken
    seconds apart differ on that one line and on nothing else.
    """
    ns = decided["ns"]
    _rc, printed = run_cli(["-n", ns, "dump"])
    rendered = auger.render_dump(ns, decided["pid"], []).splitlines()
    want = printed.splitlines()
    assert len(rendered) == len(want), (len(rendered), len(want))
    for got, exp in zip(rendered, want):
        if got.startswith("project ") and "generated " in got:
            assert exp.startswith("project ") and "generated " in exp, (got, exp)
        else:
            assert got == exp, (got, exp)
