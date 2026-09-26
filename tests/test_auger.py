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

import http.client
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

import auger
from conftest import (
    CREATED,
    D001,
    D002,
    REPO_ROOT,
    SEED_TEXT,
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


def contradiction_entries(text: str) -> list[str]:
    """The entries of a `CONTRADICTIONS WITH THE RECORD` block, in render order.

    `[]` when the block is absent — so a test can assert on the EXACT set of contradictions a
    render owes its reader without depending on where in the dump the block sits, and a new
    entry cannot hide behind a substring match on the header.
    """
    lines = text.splitlines()
    if "CONTRADICTIONS WITH THE RECORD:" not in lines:
        return []
    entries = []
    for line in lines[lines.index("CONTRADICTIONS WITH THE RECORD:") + 1 :]:
        if not line.startswith("  - "):
            break
        entries.append(line[4:])
    return entries


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


def test_init_creates_a_namespace_with_live_tables(live_service: str):
    """`init` creates what it is pointed at; the resource API, not the stale registry list, is authoritative."""
    ns = ns_name()
    assert ns not in api_namespaces()
    try:
        rc, out = run_cli(["-n", ns, "init"])
        assert rc == 0
        assert f"namespace {ns}: created" in out, out
        status, body, _ = auger.db(f"/api/ns/{ns}/tables")
        assert status == 200, body
    finally:
        assert teardown_namespace(ns) == []


def test_init_tolerates_namespace_created_before_init(live_service: str):
    """A namespace created before `init` is existing even when the list endpoint is stale."""
    ns = new_bare_ns()
    try:
        rc, out = run_cli(["-n", ns, "init"])
        assert rc == 0
        assert f"namespace {ns}: existing" in out, out
        assert "declared: 14/14 tables" in out, out
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


# AUG-072: an empty seed has nothing to store or embed. DuckBrain's required-content
# gate (DB-GAP-058) 400s a POST /api/memories with content == "", so `start` without
# --seed must not call `remember` at all — while a non-empty seed must still store +
# embed exactly once, with the key the recall path looks for.
def test_start_with_empty_seed_never_calls_remember(monkeypatch, capsys):
    """`start` with no --seed inserts the project row and makes NO remember call."""
    calls: list[tuple] = []

    def fake_remember(ns: str, key: str, content: str, domain: str = "concept") -> dict:
        calls.append((ns, key, content))
        return {}

    ns = "auger-worker072-unit"  # never touched: remember and insert are stubbed
    pid = "P-EMPTYSEED"
    monkeypatch.setattr(auger, "remember", fake_remember)
    monkeypatch.setattr(
        auger, "insert", lambda ns_, table, rows: {"table": table, "row": rows}
    )

    rc, out = run_cli(["-n", ns, "start", "--id", pid])

    assert rc == 0, out
    assert calls == [], f"remember was called with an empty seed: {calls}"
    assert f"project {pid} in namespace {ns}" in out, out
    assert "seed stored (0 chars)" in out, out


def test_start_with_a_seed_stores_and_embeds_exactly_once(monkeypatch):
    """`start` with a non-empty seed calls remember once with the seed key + content."""
    calls: list[tuple] = []

    def fake_remember(ns: str, key: str, content: str, domain: str = "concept") -> dict:
        calls.append((ns, key, content))
        return {}

    ns = "auger-worker072-unit"  # never touched: remember and insert are stubbed
    pid = "P-SEEDED"
    monkeypatch.setattr(auger, "remember", fake_remember)
    monkeypatch.setattr(
        auger, "insert", lambda ns_, table, rows: {"table": table, "row": rows}
    )

    rc, out = run_cli(["-n", ns, "start", "--id", pid, "--seed", "hello seed"])

    assert rc == 0, out
    assert calls == [(ns, f"/auger/{pid}/seed", "hello seed")], calls
    assert f"project {pid} in namespace {ns}" in out, out
    assert "seed stored (10 chars) + embedded" in out, out


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


def test_answer_refuses_a_chosen_sentence_not_supplied_as_an_option(project: dict):
    """A mismatched chosen value is refused before either decision or option rows are written."""
    ns, pid = project["ns"], project["pid"]
    before = {
        "decision": rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc"),
        "option": rows(ns, "option", "order=id.asc"),
    }
    rc, message, out = run_cli_exit(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-055",
            "--chosen",
            "enable local caching",
            "--option",
            "enable local caching for every request",
            "--option",
            "write every request to disk",
            "--confidence",
            "0.8",
        ]
    )
    assert rc != 0
    assert "no such option" in message and "enable local caching" in message, message
    assert out == ""
    assert (
        rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc") == before["decision"]
    )
    assert rows(ns, "option", "order=id.asc") == before["option"]


def test_answer_resolves_option_token_and_dump_has_one_active_configuration(
    project: dict,
):
    """A bare option suffix is canonicalized before writes and remains consistent in both dumps."""
    ns = project["ns"]
    options = [
        "the system should cache requests in memory",
        "the system should write every request to disk",
        "the system should use a remote cache",
    ]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-055",
            "--chosen",
            "O2",
            "--option",
            options[0],
            "--option",
            options[1],
            "--option",
            options[2],
            "--confidence",
            "0.8",
        ]
    )
    assert rc == 0 and "1 of 3 active" in out, out

    decision = row(ns, "decision", "id=eq.D-055")
    assert decision["chosen"] == options[1]
    stored = rows(ns, "option", "decision_id=eq.D-055&order=id.asc")
    assert [o["label"] for o in stored] == options
    assert [o["active"] for o in stored] == [False, True, False], stored

    rc, current = run_cli(["-n", ns, "dump"])
    assert rc == 0
    assert f"ACTIVE CONFIGURATION: D-055={options[1]}" in current, current
    assert "CONTRADICTIONS WITH THE RECORD" not in current, current

    rc, hypothetical = run_cli(["-n", ns, "dump", "--config", "D-055=O2"])
    assert rc == 0
    assert f"ACTIVE CONFIGURATION: D-055={options[1]}" in hypothetical, hypothetical
    assert "CONTRADICTIONS WITH THE RECORD" not in hypothetical, hypothetical


def test_answer_without_options_remains_valid_and_reports_zero_active(project: dict):
    """The legacy no-option answer remains valid, but its success count is explicit."""
    ns = project["ns"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-056",
            "--chosen",
            "an unenumerated decision",
            "--confidence",
            "0.5",
        ]
    )
    assert rc == 0 and "0 of 0 active" in out, out
    assert row(ns, "decision", "id=eq.D-056")["chosen"] == "an unenumerated decision"
    assert rows(ns, "option", "decision_id=eq.D-056") == []


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


def test_bundle_add_cli_writes_the_named_bundle(ns: str):
    """A user can create a bundle through the public CLI, including its contract path."""
    rc, out = run_cli(
        [
            "-n",
            ns,
            "bundle",
            "add",
            "agent-ecosystem",
            "--contract",
            "crier/specs/AGENT-ECOSYSTEM.md",
        ]
    )
    assert rc == 0 and "agent-ecosystem" in out, out
    stored = row(ns, "bundle", "name=eq.agent-ecosystem")
    assert stored["id"] in out, out
    assert stored["contract"] == "crier/specs/AGENT-ECOSYSTEM.md"
    assert stored["status"] == "proposed"


def test_bundle_member_cli_validates_scheduler_projects(ns: str, monkeypatch, tmp_path):
    """The CLI stores known fleet projects and refuses unknown ones without writing."""
    scheduler = tmp_path / "scheduler.db"
    with sqlite3.connect(scheduler) as con:
        con.execute("create table projects (name text not null)")
        con.execute("insert into projects(name) values ('bunker')")
    monkeypatch.setenv(auger.SCHEDULER_DB_ENV, str(scheduler))
    monkeypatch.delenv(auger.ALLOW_SCRATCH_MEMBERS_ENV, raising=False)

    _, added = run_cli(["-n", ns, "bundle", "add", "agent-ecosystem"])
    bundle_id = added.split()[0]
    rc, out = run_cli(
        ["-n", ns, "bundle", "member", bundle_id, "bunker", "test-target"]
    )
    assert rc == 0 and bundle_id in out and "bunker" in out, out
    assert row(ns, "bundle_member", "project=eq.bunker")["role"] == "test-target"

    code, msg, _ = run_cli_exit(
        ["-n", ns, "bundle", "member", bundle_id, "scratch-app", "consumer"]
    )
    assert code != 0 and "scratch-app" in msg and "scheduler" in msg, msg
    assert rows(ns, "bundle_member", "project=eq.scratch-app") == []


def test_bundle_member_cli_scratch_escape_warns_and_writes(
    ns: str, monkeypatch, tmp_path
):
    """The explicit scratch escape bypasses only scheduler validation and is never silent."""
    missing = tmp_path / "missing" / "scheduler.db"
    monkeypatch.setenv(auger.SCHEDULER_DB_ENV, str(missing))
    monkeypatch.setenv(auger.ALLOW_SCRATCH_MEMBERS_ENV, "1")

    _, added = run_cli(["-n", ns, "bundle", "add", "scratch-pair"])
    bundle_id = added.split()[0]
    rc, out = run_cli(
        ["-n", ns, "bundle", "member", bundle_id, "scratch-app", "consumer"]
    )
    assert rc == 0, out
    assert "WARNING" in out and "AUGER_ALLOW_SCRATCH_MEMBERS=1" in out, out
    assert "scratch-app" in out and "namespace" in out, out
    stored = row(ns, "bundle_member", "project=eq.scratch-app")
    assert stored["bundle_id"] == bundle_id and stored["role"] == "consumer"


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


def test_answer_supersedes_marks_the_old_decision_and_records_one_edge(decided: dict):
    ns, pid = decided["ns"], decided["pid"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "blake3 envelope",
            "--option",
            "blake3 envelope",
            "--option",
            "sha256 envelope",
            "--supersedes",
            "D-001",
            "--supersedes-why",
            "the envelope format changed",
        ]
    )
    assert rc == 0 and "supersedes" in out and "status -> superseded" in out, out

    edges = rows(ns, "edge", f"project_id=eq.{pid}&order=id.asc")
    supersedes = [e for e in edges if e.get("kind") == "supersedes"]
    assert len(supersedes) == 1, supersedes
    edge = supersedes[0]
    assert (edge["src_kind"], edge["src_id"]) == ("decision", "D-003")
    assert (edge["dst_kind"], edge["dst_id"]) == ("decision", "D-001")
    assert edge["source"] == "human"
    assert edge["confidence"] == -1.0
    assert "dst_project" not in edge or not edge["dst_project"]
    assert edge["note"] == "D-003 supersedes D-001: the envelope format changed"
    assert row(ns, "decision", "id=eq.D-001")["status"] == auger.SUPERSEDED_STATUS


def test_answer_supersede_refusals_write_nothing(decided: dict):
    ns, pid = decided["ns"], decided["pid"]
    before = {
        "decision": rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc"),
        "option": rows(ns, "option", "order=id.asc"),
        "edge": rows(ns, "edge", "order=id.asc"),
    }
    base = ["-n", ns, "answer", "--id", "D-009", "--chosen", "new answer"]

    rc, msg, _ = run_cli_exit(base + ["--supersedes", "D-099"])
    assert rc != 0 and "D-099" in msg and "does not exist" in msg, msg
    rc, msg, _ = run_cli_exit(base + ["--supersedes", "D-009"])
    assert rc != 0 and "D-009" in msg and "itself" in msg, msg
    rc, msg, _ = run_cli_exit(
        base + ["--supersedes-why", "the old answer no longer applies"]
    )
    assert rc != 0 and "--supersedes" in msg and "target" in msg, msg

    after = {
        "decision": rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc"),
        "option": rows(ns, "option", "order=id.asc"),
        "edge": rows(ns, "edge", "order=id.asc"),
    }
    assert after == before, (
        "a refused supersession wrote decision, option, or edge rows"
    )


def test_answer_supersede_detection_warns_without_superseding(decided: dict):
    ns = decided["ns"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            D001["domain"],
            "--chosen",
            "another envelope",
            "--option",
            "another envelope",
            "--confidence",
            "0.7",
        ]
    )
    assert rc == 0
    assert (
        f"supersedes?  D-001 ({D001['domain']}, decided)" in out
        and "pass --supersedes D-001" in out
    ), out
    assert rows(ns, "edge", "kind=eq.supersedes") == []
    assert row(ns, "decision", "id=eq.D-003")["status"] == "decided"


def test_answer_supersede_refreshes_facets_without_rewriting_question(project: dict):
    ns, pid = project["ns"], project["pid"]
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "answered"))
    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")
    assert rc == 0, out

    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-002",
            "--domain",
            "4.06",
            "--chosen",
            "row locking",
            "--supersedes",
            "D-001",
            "--supersedes-why",
            "the answer was re-evaluated",
        ]
    )
    assert rc == 0, out
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered"
    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set())
    assert all(
        f["status"] == "closed"
        and f["closed_by"] == "D-002"
        and f["note"] == "superseded by D-002: the answer was re-evaluated"
        for f in facets
    ), facets


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


def test_status_supersede_chain_and_quiet_control(decided: dict):
    ns = decided["ns"]
    rc, before = run_cli(["-n", ns, "status"])
    assert rc == 0 and "supersession:" not in before, before

    rc, answer_out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "new envelope",
            "--supersedes",
            "D-001",
        ]
    )
    assert rc == 0, answer_out
    rc, after = run_cli(["-n", ns, "status"])
    assert rc == 0
    assert "supersession:" in after and "D-003 supersedes D-001" in after, after


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


def test_dump_excludes_superseded_decision_from_active_configuration(decided: dict):
    ns = decided["ns"]
    rc, answer_out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "blake3 envelope",
            "--option",
            "blake3 envelope",
            "--option",
            "sha256 envelope",
            "--supersedes",
            "D-001",
        ]
    )
    assert rc == 0, answer_out

    rc, out = run_cli(["-n", ns, "dump"])
    assert rc == 0
    assert "## D-001  (4.05)  conf 0.82  [SUPERSEDED by D-003]" in out, out
    assert "ACTIVE CONFIGURATION: D-002=staging table, D-003=blake3 envelope" in out, (
        out
    )
    assert "[x] D-001-O1" not in out
    assert "[ ] D-001-O1" in out

    rc, hypothetical = run_cli(["-n", ns, "dump", "--config", "D-001=O2"])
    assert rc == 0
    assert "CONTRADICTIONS WITH THE RECORD" in hypothetical, hypothetical
    assert "D-001: --config override is superseded by D-003" in hypothetical
    assert (
        "ACTIVE CONFIGURATION: D-002=staging table, D-003=blake3 envelope"
        in hypothetical
    )


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


def test_dump_config_bare_index_renders_hypothetical_without_mutating(decided: dict):
    """The documented bare option index binds the intended decision in the projection only."""
    ns = decided["ns"]
    before = option_flags(ns)

    rc, out = run_cli(["-n", ns, "dump", "--config", "D-001=O2"])
    assert rc == 0 and "mode: HYPOTHETICAL (nothing written)" in out, out
    assert "ACTIVE CONFIGURATION: D-001=Postgres, D-002=staging table" in out, out
    assert option_flags(ns) == before


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


def test_dump_config_refuses_an_unknown_option_without_rendering(decided: dict):
    rc, message, out = run_cli_exit(
        ["-n", decided["ns"], "dump", "--config", "D-002=nonexistent option"]
    )
    assert rc != 0
    assert "nonexistent option" in message
    assert out == ""
    assert "ACTIVE CONFIGURATION" not in out


def test_dump_config_refuses_an_unknown_decision_without_rendering(decided: dict):
    rc, message, out = run_cli_exit(
        ["-n", decided["ns"], "dump", "--config", "D-999=O2"]
    )
    assert rc != 0
    assert "D-999" in message
    assert out == ""
    assert "ACTIVE CONFIGURATION" not in out


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


def test_toggle_bare_index_refuses_ambiguous_matches_without_writing(decided: dict):
    ns = decided["ns"]
    before = option_flags(ns)
    rc, message, out = run_cli_exit(["-n", ns, "toggle", "--on", "O2"])
    assert rc != 0
    assert "ambiguous option" in message
    assert "D-001-O2" in message and "D-002-O2" in message
    assert "toggled:" not in out
    assert option_flags(ns) == before


def test_toggle_resolves_all_targets_before_writing(decided: dict):
    ns = decided["ns"]
    before = option_flags(ns)
    rc, message, out = run_cli_exit(
        ["-n", ns, "toggle", "--on", "NOPE", "--on", "D-001-O2"]
    )
    assert rc != 0
    assert "NOPE" in message
    assert "toggled:" not in out
    assert option_flags(ns) == before


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


# ============================================================ AUG-075: recall fails closed
# A dead substrate used to be indistinguishable from an empty store: `recall`
# collapsed every non-200 — including `_req`'s transport contract, st == 0 with an
# `error` body — into the same `[]` a genuinely empty namespace returns, so `check`
# answered "treat as a new question" with the evidence sitting in the store.
#
# Every case here mocks at the `db()` boundary, NOT at raw urllib: the no-bypass
# property being asserted is that the REAL recall/verb code runs all the way down
# to the substrate call, so a hand-rolled transport at a call site would not
# satisfy these tests.


def _substrate(st: int, body: dict):
    """A db() stand-in that always answers `st`/`body` — a dead or shaped substrate."""

    def fake_db(path, *args, **kwargs):
        return st, body, {}

    return fake_db


def test_recall_transport_failure_is_a_failure_not_an_empty_store(monkeypatch):
    """st == 0 is _req's unreachable-substrate contract; recall must raise, not return []."""
    monkeypatch.setattr(
        auger, "db", _substrate(0, {"error": "transport: connection refused"})
    )
    with pytest.raises(auger.SubstrateError) as ei:
        auger.recall("auger-075-dead", "any question", limit=3)
    msg = str(ei.value)
    assert "substrate" in msg and "transport: connection refused" in msg, msg


def test_recall_non_200_status_is_a_failure_not_an_empty_store(monkeypatch):
    """Any non-200 refusal raises; a 200 with no items is still the empty-store `[]`."""
    monkeypatch.setattr(auger, "db", _substrate(503, {"error": "unavailable"}))
    with pytest.raises(auger.SubstrateError) as ei:
        auger.recall("auger-075-dead", "any question", limit=3)
    assert "substrate" in str(ei.value) and "503" in str(ei.value), str(ei.value)

    # The other side of the line: a genuine 200 with nothing in it stays `[]`.
    monkeypatch.setattr(auger, "db", _substrate(200, {"items": []}))
    assert auger.recall("auger-075-dead", "any question", limit=3) == []


def test_check_fails_closed_when_the_substrate_is_dead(monkeypatch):
    """A dead store is never "no stored evidence matched — treat as a new question"."""
    monkeypatch.setattr(
        auger, "db", _substrate(0, {"error": "transport: connection refused"})
    )
    rc, out = run_cli(["-n", "auger-075-dead-ns", "check", "How is the record stored?"])
    assert rc == 1, out
    assert "substrate" in out, out
    assert "treat as a new question" not in out, out
    assert "no stored evidence matched" not in out, out


def test_check_on_a_genuinely_empty_store_keeps_the_new_question_answer(monkeypatch):
    """The fix must not swing the pendulum: an EMPTY store still reads rc=0."""
    monkeypatch.setattr(auger, "db", _substrate(200, {"items": []}))
    rc, out = run_cli(
        ["-n", "auger-075-empty-ns", "check", "How is the record stored?"]
    )
    assert rc == 0, out
    assert "no stored evidence matched — treat as a new question" in out, out
    assert "substrate" not in out, out


def test_recall_verb_fails_closed_when_the_store_is_unreachable(monkeypatch):
    """`recall` exits non-zero naming the substrate; a genuine empty keeps rc=0 and silence."""
    monkeypatch.setattr(
        auger, "db", _substrate(0, {"error": "transport: connection refused"})
    )
    rc, out = run_cli(["-n", "auger-075-dead-ns", "recall", "anything"])
    assert rc == 1, out
    assert "substrate" in out, out

    # The genuine empty result keeps its shape: rc 0, nothing printed.
    monkeypatch.setattr(auger, "db", _substrate(200, {"items": []}))
    rc, out = run_cli(["-n", "auger-075-empty-ns", "recall", "anything"])
    assert rc == 0 and out == "", (rc, out)


def test_the_verbs_stay_strict_on_a_missing_namespace(monkeypatch):
    """`missing_ok` is the sibling-walk's carve-out, never the user verbs' (AUG-075).

    A 404 from the memories endpoint means the NAMESPACE does not exist; pointing
    `recall` at one is a user error the verb must name, not silence to hide — so the
    default raises even though the same status reads as empty inside `recall_many`.
    """
    monkeypatch.setattr(
        auger, "db", _substrate(404, {"error": "Namespace 'gone' does not exist"})
    )
    with pytest.raises(auger.SubstrateError) as ei:
        auger.recall("gone", "any question", limit=3)
    assert "404" in str(ei.value), str(ei.value)


def test_recall_many_tolerates_a_missing_sibling_but_not_a_dead_store(monkeypatch):
    """SPEC-002 2.1 survives the fail-closed change, both directions (AUG-075)."""
    seen: list[str] = []

    def missing_sibling_db(path, *a, **kw):
        seen.append(path)
        ns = unquote(str(path).split("namespace=")[1].split("&")[0])
        if ns == "the-missing-one":
            return 404, {"error": "Namespace 'the-missing-one' does not exist"}, {}
        return 200, {"items": [{"key": "k", "score": 0.5, "content": "c"}]}, {}

    monkeypatch.setattr(auger, "db", missing_sibling_db)
    hits = auger.recall_many(["the-missing-one", "alive"], "q", limit=3)
    assert [(where, h["key"]) for where, h in hits] == [("alive", "k")], hits
    assert len(seen) == 2, seen  # the 404 did not stop the walk

    # A substrate that cannot ANSWER is a different thing entirely: it raises.
    monkeypatch.setattr(
        auger, "db", _substrate(0, {"error": "transport: connection refused"})
    )
    with pytest.raises(auger.SubstrateError):
        auger.recall_many(["the-missing-one"], "q", limit=3)


def test_gate_question_reports_a_dead_substrate_as_an_error(monkeypatch):
    """gate_question's err contract survives the recall fail-closed change (AUG-075).

    The gate walks sibling namespaces through `select_or_empty`, which tolerates a
    dead read as "no rows here", but its OWN recall must surface as `err` — never
    as the (None, "", "", None) "no stored evidence" verdict, which would gate
    nothing today and silently ask nothing tomorrow.
    """
    monkeypatch.setattr(
        auger, "db", _substrate(0, {"error": "transport: connection refused"})
    )
    verdict, dec_id, src_project, err = auger.gate_question(
        "auger-075-dead", "P-NONE", "Is anything true?"
    )
    assert verdict is None and dec_id == "" and src_project == "", (
        verdict,
        dec_id,
        src_project,
    )
    assert err and "substrate" in err, err


# ================================================================= AUG-078: recall marks superseded
# `answer --supersedes` leaves the replaced decision's evidence embedded — retrieval keeps finding
# the old story. The record already knows it was replaced (decision status) and by whom (the
# supersedes edge), so these pin the three renders: the successor-naming marker under the
# superseded hit's snippet, the equal-score tie-break (live first), and the bare marker when no
# successor edge resolves.


def test_recall_marks_a_superseded_decision_with_its_successor(decided: dict):
    """AUG-078: the replaced story stays retrievable, but it says so and names its successor.

    `answer --supersedes` flips the old row's status and writes the edge while its evidence
    stays embedded, so the raw ranking used to return both decisions at one score with no
    way to tell which answer was live. The marker goes directly under the superseded hit's
    snippet; the live successor renders exactly as before.
    """
    ns, pid = decided["ns"], decided["pid"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.05",
            "--chosen",
            "single SQLite file",
            "--option",
            "single SQLite file",
            "--option",
            "Postgres",
            "--why-not",
            "Postgres needs a service the seed forbids",
            "--confidence",
            "0.9",
            "--supersedes",
            "D-001",
            "--supersedes-why",
            "the envelope format changed",
        ]
    )
    assert rc == 0 and "status -> superseded" in out, out

    rc, out = run_cli(
        ["-n", ns, "recall", "which option did we choose for question meaning"]
    )
    assert rc == 0, out
    keys = [line.split("  ", 1)[1] for line in out.splitlines() if "  /auger/" in line]
    assert f"/auger/{pid}/D-001" in keys, out
    assert f"/auger/{pid}/D-003" in keys, out
    # The superseded hit's block: the line under its snippet is the marker naming the successor.
    d1 = out.split(f"/auger/{pid}/D-001\n", 1)[1].split("\n", 1)[1]
    assert d1.split("\n", 1)[0] == "     [superseded by D-003]", out
    # The live successor's block carries no marker.
    d3 = out.split(f"/auger/{pid}/D-003\n", 1)[1].split("\n", 1)[1]
    assert "superseded" not in d3.split("\n", 1)[0], out


def test_recall_orders_a_live_hit_before_a_superseded_one_at_equal_scores(
    project: dict, monkeypatch
):
    """AUG-078: one score, two answers — the live decision prints first.

    The tie cannot be built against the live store: identical evidence scores
    equally only until the namespace's git-native push settles, after which the
    index diverges the two (measured: 1 vs 0.9839). So the retrieval boundary is
    stubbed — the store's own tie order handed to the verb is superseded-first —
    while the supersession itself is REAL: the edge (successor -> replaced) plus
    the status flip `record_supersessions` performs, read back by cmd_recall from
    the namespace exactly as the answer verb leaves them.
    """
    ns, pid = project["ns"], project["pid"]
    evidence = "Question: 4.05. Decision D-001: we chose single SQLite file."
    auger.insert(
        ns,
        "decision",
        [
            {
                "id": "D-001",
                "project_id": pid,
                "domain": "4.05",
                "question_id": "",
                "chosen": "single SQLite file",
                "why_not": "",
                "reversal_cost": "",
                "confidence": 0.5,
                "status": auger.SUPERSEDED_STATUS,
                "evidence_key": f"/auger/{pid}/D-001",
            },
            {
                "id": "D-002",
                "project_id": pid,
                "domain": "4.05",
                "question_id": "",
                "chosen": "Postgres",
                "why_not": "",
                "reversal_cost": "",
                "confidence": 0.5,
                "status": "decided",
                "evidence_key": f"/auger/{pid}/D-002",
            },
        ],
    )
    auger.edge(
        ns,
        pid,
        "supersedes",
        "decision",
        "D-002",
        "decision",
        "D-001",
        note="D-002 supersedes D-001: the seed forbids a second service",
    )

    def tied_recall(namespace, q, limit=5, missing_ok=False):
        assert namespace == ns, namespace
        return [
            {"key": f"/auger/{pid}/D-001", "score": 0.99, "content": evidence},
            {"key": f"/auger/{pid}/D-002", "score": 0.99, "content": evidence},
        ]

    monkeypatch.setattr(auger, "recall", tied_recall)
    rc, out = run_cli(
        ["-n", ns, "recall", "which option did we choose for question meaning"]
    )
    assert rc == 0, out
    keys = [line.split("  ", 1)[1] for line in out.splitlines() if "  /auger/" in line]
    assert keys == [f"/auger/{pid}/D-002", f"/auger/{pid}/D-001"], (
        f"the superseded D-001 did not tie-break below the live D-002: {out}"
    )
    # The tie-break never suppresses the annotation: the dead story still says so.
    d1 = out.split(f"/auger/{pid}/D-001\n", 1)[1].split("\n", 1)[1]
    assert d1.split("\n", 1)[0] == "     [superseded by D-002]", out
    # The live hit's block carries no marker even when it arrived after the dead one.
    d2 = out.split(f"/auger/{pid}/D-002\n", 1)[1].split("\n", 1)[1]
    assert "superseded" not in d2.split("\n", 1)[0], out


def test_recall_degrades_to_a_bare_marker_without_a_successor_edge(project: dict):
    """AUG-078: a status flip with no supersedes edge marks "superseded" and names nobody.

    The drift `cmd_status` renders as "no supersedes edge" — a decision moved to superseded
    whose successor was never recorded — must not gain an invented id: the bare marker says
    the decision is dead and stops there.
    """
    ns, pid = project["ns"], project["pid"]
    auger.insert(
        ns,
        "decision",
        {
            "id": "D-001",
            "project_id": pid,
            "domain": "4.05",
            "question_id": "",
            "chosen": "single SQLite file",
            "why_not": "",
            "reversal_cost": "",
            "confidence": 0.5,
            "status": auger.SUPERSEDED_STATUS,
            "evidence_key": f"/auger/{pid}/D-001",
        },
    )
    auger.remember(
        ns,
        f"/auger/{pid}/D-001",
        "Question: 4.05. Decision D-001: we chose single SQLite file.",
    )
    rc, out = run_cli(
        ["-n", ns, "recall", "which option did we choose for question meaning"]
    )
    assert rc == 0, out
    assert f"/auger/{pid}/D-001" in out, out
    d1 = out.split(f"/auger/{pid}/D-001\n", 1)[1].split("\n", 1)[1]
    assert d1.split("\n", 1)[0] == "     [superseded]", out


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


# ------------------------------------------------- the default decision id (AUG-069)
# `answer` with no --id minted its id from a ROW COUNT: `len(select(ns, 'decision', project_filter))`
# + 1. DuckBrain's declared-table reads cap at 100 rows with no truncation signal (AUG-068), so the
# moment a namespace held a hundred decisions the mint stuck on D-101 — an id that already existed —
# and the uniqueness guard then refused EVERY answer: a permanent write lockout whose only escape was
# an explicit --id. The mint is now `next_id` (the page-safe highest-id reader whose docstring names
# the count as "the classic bug this deliberately does not have"), at the width the live ids use.
#
# The unit cases drive the REAL `answer` verb through `auger.main` against a stand-in HTTP layer, so
# the mint and the guard the lockout ran through are the production ones; only the transport and the
# model calls are stubbed. The stand-in CAPS its reads like the live API does, deliberately: a
# stand-in that answered honestly would pass a count mint and prove nothing.

#: The stand-in's page cap — AUG-068: a declared-table read returns at most this many rows.
API_PAGE = 100
MINT_NS = (
    "auger-mint-unit"  # never created: every call below is answered by the stand-in
)
MINT_PID = "P-MINT"


def query_pairs(query: str) -> dict[str, str]:
    """The flat key=value pairs of a DuckBrain read query, the way the server reads them."""
    pairs: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            pairs[key] = value
    return pairs


def stand_in_api(ids: list[str], table: str = "decision", queries: list | None = None):
    """A stand-in for one declared table: rows in, CAPPED pages out.

    `order=id.desc` sorts descending (the ids are zero-padded, so lexicographic IS numeric here),
    `limit=N` takes N, and an absent limit takes a full page — the AUG-068 shape that froze the count
    mint. `queries` records every read, so a test can assert WHICH read produced an id.
    """
    stored = [{"id": i, "project_id": MINT_PID} for i in ids]

    def _select(ns: str, wanted: str, query: str = "") -> list:  # noqa: ARG001 - mirrors select()
        if queries is not None:
            queries.append(query)
        if wanted != table:
            return []
        pairs = query_pairs(query)
        found = list(stored)
        for key in ("id", "project_id"):
            value = pairs.get(key, "")
            if value.startswith("eq."):
                found = [r for r in found if r[key] == value[3:]]
        found.sort(key=lambda r: r["id"], reverse=pairs.get("order") == "id.desc")
        return found[: int(pairs["limit"]) if "limit" in pairs else API_PAGE]

    return _select


def drive_answer(
    monkeypatch, ids: list[str], *extra: str
) -> tuple[str, list[dict], list[str]]:
    """Run the REAL `answer` verb against a stand-in decision table holding `ids`.

    Returns (stdout, the decision rows the verb wrote, every read query it made). Only the transport
    and the model calls are stubbed: `_project`, `insert_decision`, `insert`, `remember` and the
    bundle impact pass — never the mint, never the uniqueness guard.
    """
    written: list[dict] = []
    queries: list[str] = []
    monkeypatch.setattr(auger, "select", stand_in_api(ids, queries=queries))
    monkeypatch.setattr(
        auger, "_project", lambda ns, pid=None: {"id": MINT_PID, "name": "mint"}
    )
    monkeypatch.setattr(
        auger, "insert_decision", lambda ns, row: written.append(row) or ""
    )
    monkeypatch.setattr(auger, "insert", lambda ns, table, rows: {})
    monkeypatch.setattr(auger, "remember", lambda *a, **k: {})
    monkeypatch.setattr(
        auger,
        "bundle_impact",
        lambda ns, pid, row: {
            "scoped": False,
            "walked": [],
            "lines": [],
            "warnings": [],
        },
    )
    rc, out = run_cli(["-n", MINT_NS, "answer", "--chosen", "one option", *extra])
    assert rc == 0, out
    return out, written, queries


def test_answer_past_a_hundred_decisions_mints_from_the_highest_id(monkeypatch):
    """AUG-069: the 101st answer in a namespace is D-102 — not a stuck, already-stored D-101.

    The stand-in holds exactly the ids the dogfood namespace held (D-001..D-101) and caps its reads
    at 100 rows, so a count still reads 100 and mints the id that is taken.
    """
    ids = [f"D-{i:03d}" for i in range(1, 102)]
    out, written, queries = drive_answer(monkeypatch, ids)

    # The stand-in is not a polite fiction: it CAPS, which is the property that froze the mint.
    assert (
        len(auger.select(MINT_NS, "decision", f"project_id=eq.{MINT_PID}")) == API_PAGE
    )
    # ... so the expression this replaced (`len(...) + 1`) yields an id the store already holds, and
    # the guard below then refused the answer. Named here so the regression cannot return silently.
    assert f"D-{API_PAGE + 1:03d}" == "D-101" and "D-101" in ids

    assert out.startswith("D-102 recorded"), out
    assert [r["id"] for r in written] == ["D-102"], written
    # Criterion 1: the id came from the HIGHEST id, read page-safely — not from a count.
    assert queries[0] == "select=id&order=id.desc&limit=1", queries
    # and the uniqueness guard still ran against the requested id, after the mint
    assert "id=eq.D-102&select=id&limit=1" in queries, queries


def test_the_minted_id_keeps_the_three_digit_width_of_the_live_ids(monkeypatch):
    """AUG-069: D-101 -> D-102, NOT D-000102. `ID_WIDTH` is the six-digit edge/facet/bundle law and
    is deliberately not the decision law: every decision id in the real namespaces is three digits,
    and one table holding two widths sorts wrongly under the `order=id.desc` read the mint needs.
    """
    out, written, _ = drive_answer(monkeypatch, [f"D-{i:03d}" for i in range(1, 102)])
    assert auger.ID_WIDTH == 6, "the six-digit law this must not apply to decisions"
    assert written[0]["id"] == "D-102", written
    assert not out.startswith("D-000102"), out


def test_an_empty_project_still_mints_D_001(monkeypatch):
    """The first decision of a namespace is unchanged: D-001, three digits, from the same
    page-safe read — an empty table has no highest id to read, and that is not an error.
    """
    out, written, queries = drive_answer(monkeypatch, [])
    assert out.startswith("D-001 recorded"), out
    assert [r["id"] for r in written] == ["D-001"], written
    assert queries[0] == "select=id&order=id.desc&limit=1", queries


def test_an_explicit_id_is_still_used_verbatim_and_mints_nothing(monkeypatch):
    """Criterion 3: `--id` wins before any mint — and costs no highest-id read at all."""
    out, written, queries = drive_answer(monkeypatch, ["D-101"], "--id", "D-500")
    assert out.startswith("D-500 recorded"), out
    assert [r["id"] for r in written] == ["D-500"], written
    assert not any("order=id.desc" in q for q in queries), queries


def test_next_id_still_mints_the_six_digit_law_for_its_existing_callers(monkeypatch):
    """Criterion 3, one layer down: `width` defaults to `ID_WIDTH`, so E/F/B/BM/V/Q are unchanged."""
    monkeypatch.setattr(auger, "select", stand_in_api([], table="edge"))
    assert auger.next_id(MINT_NS, "edge", "E") == "E-000001"
    monkeypatch.setattr(auger, "select", stand_in_api(["E-000041"], table="edge"))
    assert auger.next_id(MINT_NS, "edge", "E") == "E-000042"
    monkeypatch.setattr(auger, "select", stand_in_api(["F-000041"], table="facet"))
    assert auger.next_id(MINT_NS, "facet", "F") == "F-000042"
    monkeypatch.setattr(auger, "select", stand_in_api(["B-000009"], table="bundle"))
    assert auger.next_id(MINT_NS, "bundle", "B") == "B-000010"


def test_next_id_pads_to_the_width_it_is_given_and_never_truncates(monkeypatch):
    """A width is a FLOOR: three digits for decisions, and a number past 999 keeps all its digits."""
    monkeypatch.setattr(auger, "select", stand_in_api(["D-998"]))
    assert auger.next_id(MINT_NS, "decision", "D", width=3) == "D-999"
    monkeypatch.setattr(auger, "select", stand_in_api(["D-999"]))
    assert auger.next_id(MINT_NS, "decision", "D", width=3) == "D-1000"


# ------------------------------------- the check-then-insert window (AUG-070)
# The mint reads the highest id, then the pre-insert check reads it again, then the INSERT lands —
# three steps another writer can move between. DuckBrain enforces NO uniqueness, so a lost race is
# either an answer lost to a refusal (the observed dominant shape) or two rows under one id. The
# cases below drive the REAL `answer` and the REAL `dump` against a stand-in SERVER stubbed at
# `db` — one layer under everything, so the mint, the guard, the retry loop and the warning block
# are the production ones — and the server reproduces the two race shapes exactly.

#: How many attempts the bounded re-mint is allowed. Asserted against the module constant so the
#: bound cannot silently loosen or tighten. The getattr keeps this block importable against a
#: PRE-AUG070 tree, so the RED proof below exercises the race itself instead of crashing collection.
AUG070_ATTEMPTS = getattr(auger, "AUG070_ID_ATTEMPTS", 3)


def race_decision_row(did: str, chosen: str) -> dict:
    """One decision row in the full shape `answer` sends."""
    return {
        "id": did,
        "project_id": MINT_PID,
        "domain": "",
        "question_id": "",
        "chosen": chosen,
        "why_not": "",
        "reversal_cost": "",
        "confidence": 0.5,
        "status": "decided",
        "evidence_key": f"/auger/{MINT_PID}/{did}",
    }


class RaceServer:
    """DuckBrain's declared-table SERVER with inserts — the transport the race needs.

    Reads serve from `tables` (eq-filters, order, limit, offset); POSTs append and return 201.
    Two race shapes, reproduced by the server rather than by mocking the guard:

      * `appear_on_query` — the first read whose path carries that substring finds `ghost_rows`
        already stored. Keyed on `id=eq.D-074` (the pre-insert check's read) this is shape (a):
        the sibling writer's insert landed between this writer's MINT read and its CHECK read,
        so the mint saw a free id and the check does not.
      * `duplicate_every_insert` — every decision POST is immediately stored twice: BOTH writers
        passed the same window (shape b taken to its pathological end, the only honest way to
        make every attempt collide from inside one process).
    """

    def __init__(
        self,
        tables: dict[str, list[dict]],
        ghost_rows: list[dict] | None = None,
        appear_on_query: str = "",
        duplicate_every_insert: bool = False,
    ):
        self.tables = {t: list(rows) for t, rows in tables.items()}
        self.ghost_rows = list(ghost_rows or [])
        self.appear_on_query = appear_on_query
        self.ghost_shown = False
        self.duplicate_every_insert = duplicate_every_insert
        self.requests: list[tuple[str, str]] = []

    def __call__(self, path, method="GET", body=None, *a, **k):  # noqa: ARG002 - mirrors db()
        self.requests.append((method, path))
        split = urlsplit(path)
        parts = split.path.split("/")  # /api/ns/<ns>/tables/<table>
        if method == "POST":
            table = unquote(parts[-1])
            rows = body if isinstance(body, list) else [body]
            self.tables.setdefault(table, []).extend(rows)
            if table == "decision" and self.duplicate_every_insert:
                self.tables[table].extend(rows)
            return 201, {}, {}
        if self.ghost_rows and not self.ghost_shown and self.appear_on_query in path:
            self.tables.setdefault("decision", []).extend(self.ghost_rows)
            self.ghost_shown = True
        if len(parts) < 3 or parts[-2] != "tables":
            return 404, {"error": f"not a declared-table path: {path}"}, {}
        stored = self.tables.get(unquote(parts[-1]))
        if stored is None:
            return 404, {"error": f"no such table: {parts[-1]}"}, {}
        pairs = dict(parse_qsl(split.query, keep_blank_values=True))
        found = list(stored)
        for key, value in pairs.items():
            if key in ("limit", "offset", "order", "select", "count"):
                continue
            if value.startswith("eq."):
                found = [r for r in found if str(r.get(key)) == value[3:]]
        for clause in reversed(str(pairs.get("order", "")).split(",")):
            col, _, direction = clause.strip().partition(".")
            if col and direction in ("asc", "desc"):
                found.sort(
                    key=lambda r: str(r.get(col) or ""), reverse=direction == "desc"
                )
        limit = int(pairs["limit"]) if "limit" in pairs else max(len(found), 1)
        offset = int(pairs.get("offset", 0))
        return 200, found[offset : offset + limit], {}


def race_stubs(monkeypatch) -> None:
    """Everything around the transport that `answer` touches but the race does not exercise."""
    monkeypatch.setattr(
        auger, "_project", lambda ns, pid=None: {"id": MINT_PID, "name": "race"}
    )
    monkeypatch.setattr(auger, "remember", lambda *a, **k: {})
    monkeypatch.setattr(
        auger,
        "bundle_impact",
        lambda ns, pid, row: {
            "scoped": False,
            "walked": [],
            "lines": [],
            "warnings": [],
        },
    )


def test_a_lost_race_remints_and_lands_the_answer_under_a_new_id(monkeypatch):
    """AUG-070 shape (a): the sibling's insert lands between our mint and our check.

    The mint reads a free D-074; the CHECK read finds D-074 stored (the ghost appears on exactly
    the `id=eq.D-074` query). The answer must be RE-MINTED to the live highest id + 1 (D-075) and
    stored ONCE under it — never lost to a refusal, and with nothing rewritten: the ghost row is
    the sibling's and stays untouched, the dead attempt's option ids are never stored, and the
    evidence key carries the id that was actually stored.
    """
    server = RaceServer(
        {"decision": [race_decision_row("D-073", "earlier")], "edge": []},
        ghost_rows=[race_decision_row("D-074", "the sibling got there first")],
        appear_on_query="id=eq.D-074",
    )
    monkeypatch.setattr(auger, "db", server)
    race_stubs(monkeypatch)

    rc, out = run_cli(
        [
            "-n",
            MINT_NS,
            "answer",
            "--chosen",
            "alpha",
            "--option",
            "alpha",
            "--option",
            "beta",
        ]
    )

    assert rc == 0, out
    assert out.startswith("D-075 recorded"), out
    decisions = [r["id"] for r in server.tables["decision"]]
    assert decisions == ["D-073", "D-074", "D-075"], decisions
    # the ghost is the SIBLING's row, stored once: this writer neither duplicated nor rewrote it
    assert sum(1 for r in server.tables["decision"] if r["id"] == "D-074") == 1
    stored = next(r for r in server.tables["decision"] if r["id"] == "D-075")
    assert stored["chosen"] == "alpha", stored
    assert stored["evidence_key"] == f"/auger/{MINT_PID}/D-075", stored
    # the dead attempt's D-074-O* ids are never stored: the retry re-mints BEFORE any row is built
    assert [o["id"] for o in server.tables.get("option", [])] == [
        "D-075-O1",
        "D-075-O2",
    ], server.tables.get("option")
    # exactly one decision POST landed: the retry re-minted before writing, not after
    assert (
        sum(1 for m, p in server.requests if m == "POST" and p.endswith("/decision"))
        == 1
    ), server.requests


def test_a_permanently_contended_answer_is_bounded_and_names_the_id_and_attempts(
    monkeypatch,
):
    """The bound: a namespace where EVERY minted id is already taken when the check runs.

    The pre-insert re-mint loop is bounded by `AUG070_ID_ATTEMPTS`: three attempts, each a re-mint
    from the live highest id, then a loud refusal naming the id and the attempt count — with
    NOTHING stored, because the bound fired before the first write. The mint cannot be allowed to
    spin on a store that answers "taken" forever.
    """
    posts: list[str] = []

    def always_taken(path, method="GET", body=None, *a, **k):
        """The mint's highest-id read is frozen at D-073 and every id=eq.<id> check says taken."""
        if method == "POST":
            posts.append(path)
            return 201, {}, {}
        if "order=id.desc" in path:
            return 200, [{"id": "D-073"}], {}  # the highest id never moves
        if "id=eq." in path and "select=id" in path:
            did = path.split("id=eq.")[1].split("&")[0]
            return 200, [{"id": did}], {}  # ... and every checked id is taken
        return 200, [], {}

    monkeypatch.setattr(auger, "db", always_taken)
    race_stubs(monkeypatch)

    rc, msg, _ = run_cli_exit(["-n", MINT_NS, "answer", "--chosen", "alpha"])

    assert rc != 0, msg
    assert "D-074" in msg, msg  # the id every attempt minted from the frozen highest
    assert f"re-checked {AUG070_ATTEMPTS} times" in msg, msg
    assert "NOTHING was stored" in msg, msg
    # bounded BEFORE the first write: the contention was at the mint, and no row was written
    assert posts == [], f"a bounded pre-insert refusal wrote: {posts!r}"


def test_an_explicit_duplicate_id_refusal_says_nothing_was_stored(monkeypatch):
    """The explicit `--id` path keeps its terminal refusal, but says what it guarantees.

    NOTHING was stored — the guarantee AUG-034 bought for the serial case, now in the message
    so a reader does not have to trust the code — and the message names the auto-minted path
    (no --id) as the one that re-mints and retries instead of refusing.
    """
    monkeypatch.setattr(
        auger,
        "db",
        RaceServer({"decision": [race_decision_row("D-034", "taken")], "edge": []}),
    )
    race_stubs(monkeypatch)

    rc, msg, _ = run_cli_exit(
        ["-n", MINT_NS, "answer", "--id", "D-034", "--chosen", "a retry must not land"]
    )

    assert rc != 0, msg
    assert "D-034" in msg and "already exists" in msg, msg
    assert "NOTHING was stored" in msg, msg
    assert "auto-minted" in msg and "--id" in msg, msg


def test_dump_names_duplicate_ids_rather_than_rendering_them_silently(monkeypatch):
    """A drifted store — two decisions under D-073, three options under D-073-O1 — is WARNED about
    by name and STILL rendered (a drifted record is still worth reading): the defect was one id
    silently rendering two different real choices with no signal at all.
    """
    dec = [
        race_decision_row("D-073", "postgres"),
        race_decision_row("D-073", "sqlite"),
        race_decision_row("D-074", "untouched"),
    ]
    opts = [
        {"id": "D-073-O1", "decision_id": "D-073", "label": "postgres", "active": True},
        {"id": "D-073-O1", "decision_id": "D-073", "label": "sqlite", "active": True},
        {"id": "D-073-O1", "decision_id": "D-073", "label": "neither", "active": False},
        {"id": "D-074-O1", "decision_id": "D-074", "label": "only", "active": True},
    ]
    monkeypatch.setattr(
        auger, "db", RaceServer({"decision": dec, "option": opts, "edge": []})
    )
    race_stubs(monkeypatch)

    rc, out = run_cli(["-n", MINT_NS, "dump", "--config", "D-073=postgres"])

    assert rc == 0, out
    assert "DUPLICATE IDS: D-073 x2, D-073-O1 x3" in out, out
    assert "concurrent writers collided; dedupe before trusting this config" in out, out
    # still readable: both rows render, both real choices visible, and the render COMPLETED
    assert out.count("## D-073") == 2, out
    assert "chosen   : postgres" in out and "chosen   : sqlite" in out, out
    assert "## D-074" in out, out
    assert "ACTIVE CONFIGURATION" in out, out


def test_the_decision_id_width_is_read_from_the_highest_id(monkeypatch):
    """The width read is the same page-safe read the mint uses, with the live three-digit default.

    A namespace already minted at `ID_WIDTH`, or holding one accidentally over-wide id, cannot widen
    the law for good: the value is capped at `ID_WIDTH`, and an id with no digits at all is the
    default rather than a crash.
    """
    queries: list[str] = []
    monkeypatch.setattr(auger, "select", stand_in_api([], queries=queries))
    assert auger.decision_id_width(MINT_NS) == auger.DECISION_ID_WIDTH == 3
    assert queries == ["select=id&order=id.desc&limit=1"], queries

    for ids, want in (
        (["D-101"], 3),
        (["D-000102"], 6),
        (["D-1234567"], 6),
        (["no-digits-here"], 3),
    ):
        monkeypatch.setattr(auger, "select", stand_in_api(ids))
        assert auger.decision_id_width(MINT_NS) == want, ids


def test_a_live_namespace_holding_a_hundred_and_one_decisions_keeps_answering(
    project: dict,
):
    """AUG-069 criterion 2, end to end against the real store: 101 stored decisions, then TWO answers
    with no --id. The mint ADVANCES (D-102, then D-103) at the three-digit width, where the count
    mint would have refused both with "refused: decision 'D-101' already exists".
    """
    ns, pid = project["ns"], project["pid"]
    auger.insert(
        ns,
        "decision",
        [
            {
                "id": f"D-{i:03d}",
                "project_id": pid,
                "domain": "",
                "question_id": "",
                "chosen": f"stored by the scale leg, {i}",
                "why_not": "",
                "reversal_cost": "",
                "confidence": 0.5,
                "status": "decided",
                "evidence_key": f"/auger/{pid}/D-{i:03d}",
            }
            for i in range(1, 102)
        ],
    )
    # The precondition of the freeze, asserted rather than assumed: ONE bare read is a page and no
    # more (AUG-068 — the reason a count capped at 100 and the mint froze there), while the paged
    # `select` the mint itself reads through now returns the whole table.
    st, page, _ = auger.db(auger.tbl(ns, "decision", f"project_id=eq.{pid}"))
    assert st == 200 and len(page) == API_PAGE
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 101

    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--chosen",
            "minted one",
            "--why-not",
            "n/a",
            "--confidence",
            "0.5",
        ]
    )
    assert rc == 0 and out.startswith("D-102 recorded"), out
    assert row(ns, "decision", "id=eq.D-102&limit=1")["chosen"] == "minted one"

    # and it is not a one-shot: the NEXT answer reads D-102 and mints D-103
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--chosen",
            "minted two",
            "--why-not",
            "n/a",
            "--confidence",
            "0.5",
        ]
    )
    assert rc == 0 and out.startswith("D-103 recorded"), out
    assert row(ns, "decision", "id=eq.D-103&limit=1")["chosen"] == "minted two"


# -------------------- the contradiction a mind-change leaves in the CURRENT dump (AUG-077)
# `toggle --on` a sibling of the recorded choice is the legitimate mind-change path, and it is honest
# at the moment it writes (it reports the sibling it switched off). The dump that follows renders
# `chosen` — the answer's label, canonicalized at write time — AND the toggled option as the one
# configuration, with no signal at all: the exact two-valued configuration AUG-070 named for
# duplicate ids, reachable through a path that never collides. The hypothetical render has always
# named it; the stored state now owes its reader the same block.
#
# The first four arms drive the REAL `dump` against a stand-in SERVER (the AUG-070 pattern above), so
# they run with no substrate and can build the shapes a live namespace cannot be asked for (a
# superseded decision whose option rows kept their `active` flags, a record whose `chosen` names no
# option at all). The three live arms below them cover the mind-change path a user actually takes,
# and pin the hypothetical render as unchanged.


def dump_decision(
    did: str,
    chosen: str,
    status: str = "decided",
    why_not: str = "no such service on one box",
) -> dict:
    """One decision row in the shape `dump` reads, with a reason on file that can be quoted."""
    return {**race_decision_row(did, chosen), "why_not": why_not, "status": status}


def dump_option(oid: str, label: str, active: bool) -> dict:
    """One option row; the decision it belongs to is the id's own prefix."""
    return {
        "id": oid,
        "decision_id": oid.rsplit("-", 1)[0],
        "label": label,
        "active": active,
    }


def stand_in_dump(monkeypatch, dec: list[dict], opts: list[dict]) -> None:
    """`dump` reads the project row, the decision rows, the option rows and the supersedes edges."""
    monkeypatch.setattr(
        auger, "db", RaceServer({"decision": dec, "option": opts, "edge": []})
    )
    race_stubs(monkeypatch)


def test_dump_current_mode_names_the_option_that_replaced_the_recorded_choice(
    monkeypatch,
):
    """The mind-change as data: `chosen` is O1's label, O2 is the row flagged active."""
    stand_in_dump(
        monkeypatch,
        [
            dump_decision("D-001", "single SQLite file"),
            dump_decision("D-002", "staging table"),
        ],
        [
            dump_option("D-001-O1", "single SQLite file", False),
            dump_option("D-001-O2", "Postgres", True),
            dump_option("D-002-O1", "staging table", True),
            dump_option("D-002-O2", "row locking", False),
        ],
    )

    rc, out = run_cli(["-n", MINT_NS, "dump"])

    assert rc == 0, out
    assert "mode: current stored state" in out, out
    assert contradiction_entries(out) == [
        "D-001: active D-001-O2 (Postgres) contradicts the recorded choice "
        "(single SQLite file) — reason on file: no such service on one box"
    ], out
    # Named, never hidden: the render COMPLETES with the selection it actually holds, so the reader
    # can see the two values the warning is about.
    assert "ACTIVE CONFIGURATION: D-001=Postgres, D-002=staging table" in out, out
    assert "     [x] D-001-O2" in out, out


def test_dump_current_mode_is_silent_when_the_active_option_is_the_recorded_choice(
    monkeypatch,
):
    """The clean control: agreement owes no entry at all — not the block, not a line."""
    stand_in_dump(
        monkeypatch,
        [
            dump_decision("D-001", "single SQLite file"),
            dump_decision("D-002", "row locking"),
        ],
        [
            dump_option("D-001-O1", "single SQLite file", True),
            dump_option("D-001-O2", "Postgres", False),
            dump_option("D-002-O1", "staging table", False),
            dump_option("D-002-O2", "row locking", True),
        ],
    )

    rc, out = run_cli(["-n", MINT_NS, "dump"])

    assert rc == 0, out
    assert "   chosen   : single SQLite file" in out, out  # the render happened
    assert "CONTRADICTIONS WITH THE RECORD" not in out, out
    assert contradiction_entries(out) == [], out


def test_dump_current_mode_names_a_recorded_choice_that_names_no_option(monkeypatch):
    """A `chosen` that is not any option row's label — the record and the configuration disagree,
    and the entry names the active option it disagrees with, quoting the reason on file."""
    stand_in_dump(
        monkeypatch,
        [dump_decision("D-001", "we will use one file on one box")],
        [
            dump_option("D-001-O1", "Postgres", True),
            dump_option("D-001-O2", "sqlite", False),
        ],
    )

    rc, out = run_cli(["-n", MINT_NS, "dump"])

    assert rc == 0, out
    assert contradiction_entries(out) == [
        "D-001: active D-001-O1 (Postgres) contradicts the recorded choice "
        "(we will use one file on one box) — reason on file: no such service on one box"
    ], out


def test_dump_current_mode_never_invents_a_contradiction_it_cannot_name(monkeypatch):
    """The shapes the pass declines to judge, each because it has no single option to name:

    * two options active — the activation warning owns it;
    * no option active — the same warning owns it;
    * nothing recorded as `chosen` — a record that claims nothing contradicts nothing;
    * superseded — it contributes nothing to the configuration, so nothing about it can
      contradict, even though its option rows keep the `active` flags they were written with.
    """
    stand_in_dump(
        monkeypatch,
        [
            dump_decision("D-001", "staging table"),  # two active
            dump_decision("D-002", "single SQLite file"),  # none active
            dump_decision("D-003", ""),  # nothing recorded
            dump_decision(
                "D-004", "single SQLite file", status=auger.SUPERSEDED_STATUS
            ),
        ],
        [
            dump_option("D-001-O1", "staging table", True),
            dump_option("D-001-O2", "row locking", True),
            dump_option("D-002-O1", "single SQLite file", False),
            dump_option("D-003-O1", "only option", True),
            dump_option("D-004-O1", "single SQLite file", False),
            dump_option("D-004-O2", "Postgres", True),
        ],
    )

    rc, out = run_cli(["-n", MINT_NS, "dump"])

    assert rc == 0, out
    assert "CONTRADICTIONS WITH THE RECORD" not in out, out
    assert contradiction_entries(out) == [], out
    # The two shapes it left alone are still reported, by the block that owns them.
    assert "  - D-001: 2 of 2 options active (D-001-O1, D-001-O2)" in out, out
    assert "  - D-002: 0 of 1 options active" in out, out
    assert "## D-004" in out and "[SUPERSEDED]" in out, out


def test_dump_in_current_mode_names_the_contradiction_a_toggle_created(decided: dict):
    """AUG-077 on the path a user takes: `toggle --on` the OTHER option of D-001, then dump.

    The toggle is honest and the toggled option IS the active configuration — what was missing is
    the signal that the recorded choice no longer is.
    """
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-001-O2"])
    assert rc == 0 and "one option per decision D-001" in out, out

    rc, current = run_cli(["-n", ns, "dump"])
    assert rc == 0, current
    assert "mode: current stored state" in current, current
    why_not = row(ns, "decision", "id=eq.D-001")[
        "why_not"
    ]  # read it, do not hardcode it
    assert why_not == D001["why_not"]
    assert contradiction_entries(current) == [
        "D-001: active D-001-O2 (Postgres) contradicts the recorded choice "
        f"(single SQLite file) — reason on file: {why_not}"
    ], current
    assert "ACTIVE CONFIGURATION: D-001=Postgres, D-002=staging table" in current, (
        current
    )


def test_dump_current_mode_goes_quiet_again_when_the_choice_is_toggled_back(
    decided: dict,
):
    """The block tracks the STATE, not the fact that a mind-change once happened."""
    ns = decided["ns"]
    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-001-O2"])
    assert rc == 0, out
    rc, drifted = run_cli(["-n", ns, "dump"])
    assert "CONTRADICTIONS WITH THE RECORD" in drifted, (
        drifted
    )  # the state this arm starts from

    rc, out = run_cli(["-n", ns, "toggle", "--on", "D-001-O1"])
    assert rc == 0, out

    rc, current = run_cli(["-n", ns, "dump"])
    assert rc == 0, current
    line = next(
        ln for ln in current.splitlines() if ln.startswith("ACTIVE CONFIGURATION:")
    )
    assert (
        line == "ACTIVE CONFIGURATION: D-001=single SQLite file, D-002=staging table"
    ), line
    assert contradiction_entries(current) == [], current


def test_dump_hypothetical_still_reports_only_its_own_contradiction(decided: dict):
    """The hypothesis keeps its contract: it names what the `--config` asks for and does NOT audit
    the stored drift it was not asked about — the current render owns that, and this pins both."""
    ns = decided["ns"]
    rc, out = run_cli(
        ["-n", ns, "toggle", "--on", "D-001-O2"]
    )  # stored state is now drifted
    assert rc == 0, out

    rc, hyp = run_cli(["-n", ns, "dump", "--config", "D-002=row locking"])
    assert rc == 0 and "mode: HYPOTHETICAL (nothing written)" in hyp, hyp
    assert contradiction_entries(hyp) == [
        "D-002: choosing row locking contradicts the recorded choice (staging table) "
        f"— reason on file: {D002['why_not']}"
    ], hyp
    # Non-overridden decisions still render the stored selection — the render is unchanged.
    assert "ACTIVE CONFIGURATION: D-001=Postgres, D-002=row locking" in hyp, hyp


# ------------------------------------- reads past one page of a declared table (AUG-068)
# DuckBrain's declared-table GET is PAGE-CAPPED and says nothing about it: a request with no `limit`
# returns at most `API_PAGE` rows, an explicit `limit` is honoured up to the server's hard cap, and the
# response carries neither a row count nor a `Content-Range` — the rows coming back SHORT of the limit
# asked for is the only truncation signal the API offers. `cmd_status` and `cmd_dump` read every
# decision and every option with a bare `select`, so a namespace past a hundred rows of either was
# reported short: measured on `auger-df7-scale`, 180 stored options read as "options 100" and D-031
# rendered two of its three options. Nothing was ever LOST — only silently under-reported, which is
# the worse failure for a reader who cannot tell a short table from a capped read.
#
# `select` now pages with an explicit `limit` + `offset` until a page comes back short of the size it
# asked for. The cases below drive the REAL `select` against a stand-in SERVER — stubbed at `db`, one
# layer UNDER the function under test, so the paging loop, its query building and its stop condition
# are the production ones — and the last one against the live store.

#: The server's own hard cap on an explicit `limit` (the live API clamps `limit=1000`).
SERVER_MAX_LIMIT = 1000


def decision_rows(count: int, prefix: str = "D") -> list[dict]:
    """`count` zero-padded rows in id order — the shape a table past a page holds."""
    return [{"id": f"{prefix}-{i:03d}"} for i in range(1, count + 1)]


class StandInServer:
    """A stand-in for DuckBrain's declared-table SERVER: rows in, PAGES out, cap included.

    Stubbed at `db` — one layer BELOW `select` — so the real paging loop is what the cases exercise.
    Fidelity is the point: a request with no `limit` gets `page` rows (the cap that produced this
    row), an explicit `limit` is honoured up to `SERVER_MAX_LIMIT` and then clamped, `offset` pages
    from there, `order=col.asc|desc` sorts, `col=eq.value` filters — and a read that cannot be
    answered (unknown table, or `fail_after` requests) comes back non-200 with no rows.
    A `select=` projection is ignored: auger reads whole rows.
    """

    def __init__(
        self,
        rows: dict[str, list[dict]],
        *,
        page: int = API_PAGE,
        fail_after: int | None = None,
    ):
        self.rows = rows
        self.page = page
        self.fail_after = fail_after
        self.requests: list[str] = []

    def __call__(self, path, method="GET", body=None, *a, **k):  # noqa: ARG002 - mirrors db()
        assert method == "GET", f"the stand-in serves reads only, got {method} {path}"
        self.requests.append(path)
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            return 500, {"error": "the store stopped answering mid-read"}, {}
        split = urlsplit(path)
        parts = split.path.split("/")  # /api/ns/<ns>/tables/<table>
        if len(parts) < 3 or parts[-2] != "tables":
            return 404, {"error": f"not a declared-table path: {path}"}, {}
        stored = self.rows.get(unquote(parts[-1]))
        if stored is None:
            return 404, {"error": f"no such table: {parts[-1]}"}, {}
        pairs = dict(parse_qsl(split.query, keep_blank_values=True))
        found = list(stored)
        for key, value in pairs.items():
            if key in ("limit", "offset", "order", "select", "count"):
                continue
            if value.startswith("eq."):
                found = [r for r in found if str(r.get(key)) == value[3:]]
        for clause in reversed(str(pairs.get("order", "")).split(",")):
            col, _, direction = clause.strip().partition(".")
            if col and direction in ("asc", "desc"):
                found.sort(
                    key=lambda r: str(r.get(col) or ""), reverse=direction == "desc"
                )
        limit = (
            min(int(pairs["limit"]), SERVER_MAX_LIMIT)
            if "limit" in pairs
            else self.page
        )
        offset = int(pairs.get("offset", 0))
        return 200, found[offset : offset + limit], {}


def test_the_module_page_size_is_the_measured_live_page_size():
    """AUG-068: `API_PAGE` is a MEASURED number — the stand-in caps at the same one the loop uses.

    A stand-in capped differently from the production page size would prove nothing about the loop
    (it could pass while the real 100-row cap still truncated), so the two names are tied here and
    the live case proves the real server serves exactly this many rows for a filterless read.
    """
    assert auger.API_PAGE == API_PAGE == 100


def test_select_reads_every_row_of_a_table_past_one_page(monkeypatch):
    """AUG-068 criterion 1: 130 stored rows come back as 130 rows, not as the server's first page.

    RED on the single-fetch `select`: it returned the first 100 rows with no error and no signal —
    exactly the silent under-count this row is about.
    """
    stored = decision_rows(130)
    server = StandInServer({"decision": stored})
    monkeypatch.setattr(auger, "db", server)

    got = auger.select(MINT_NS, "decision", "")
    assert len(got) == 130 == len(stored), (
        f"{len(got)} rows served for {len(stored)} stored"
    )
    assert [r["id"] for r in got] == [r["id"] for r in stored]
    # The cap the row measured, asserted rather than assumed: ONE bare GET returns a page and no
    # signal at all that the table holds more.
    st, page, _ = auger.db(auger.tbl(MINT_NS, "decision", ""))
    assert st == 200 and len(page) == API_PAGE, page


def test_the_pages_ask_with_limit_and_offset_until_a_page_comes_back_short(monkeypatch):
    """180 rows = 100 + 80, and the SECOND page is short of the size it asked for: the stop signal.

    The rows are asserted to be the whole table, in order and without repeats: offset paging that
    dropped or repeated a row would still "find more than 100".
    """
    stored = decision_rows(180, prefix="O")
    server = StandInServer({"option": stored})
    monkeypatch.setattr(auger, "db", server)

    got = auger.select(MINT_NS, "option", "")
    assert [r["id"] for r in got] == [r["id"] for r in stored]
    assert len({r["id"] for r in got}) == 180, "a paged read repeated a row"
    assert len(server.requests) == 2, server.requests
    assert server.requests[0].endswith("?limit=100"), server.requests[0]
    assert server.requests[1].endswith("?limit=100&offset=100"), server.requests[1]


def test_a_table_that_is_an_exact_multiple_of_the_page_still_terminates(monkeypatch):
    """200 rows: two full pages, then the empty page that proves the table ended.

    The case the "fewer than a page" stop condition exists for: two full pages in a row must not
    read as "there is nothing after 200", and must not loop.
    """
    stored = decision_rows(200, prefix="O")
    server = StandInServer({"option": stored})
    monkeypatch.setattr(auger, "db", server)

    assert len(auger.select(MINT_NS, "option", "")) == 200
    assert len(server.requests) == 3, server.requests
    assert server.requests[2].endswith("?limit=100&offset=200"), server.requests[2]


def test_a_caller_limit_stays_a_ceiling_and_still_costs_one_request(monkeypatch):
    """The page-safe mint shape (`order=id.desc&limit=1`) must not become a walk of the table.

    AUG-069's `next_id` reads ONE row per mint. Paging must not multiply that into a full read, and
    must not hand the caller more rows than it asked for.
    """
    stored = decision_rows(130)
    server = StandInServer({"decision": stored})
    monkeypatch.setattr(auger, "db", server)

    got = auger.select(MINT_NS, "decision", "select=id&order=id.desc&limit=1")
    assert [r["id"] for r in got] == ["D-130"], got
    assert len(server.requests) == 1, server.requests
    assert "limit=1" in server.requests[0] and "limit=100" not in server.requests[0]


def test_a_caller_limit_above_one_page_is_served_in_pages_up_to_that_limit(monkeypatch):
    """`limit=180` over a 180-row table is 180 rows in 2 requests — not 100, and not one request.

    The limit is a ceiling on the RESULT, not a page size: the loop asks for `min(API_PAGE,
    remaining)`, so the caller's own paging intent (the API honours `limit=180`, then a bare read
    would page from 100) is served in pages rather than trusted or discarded.
    """
    stored = decision_rows(180, prefix="O")
    server = StandInServer({"option": stored})
    monkeypatch.setattr(auger, "db", server)

    assert len(auger.select(MINT_NS, "option", "limit=180")) == 180
    assert len(server.requests) == 2, server.requests
    assert server.requests[1].endswith("?limit=80&offset=100"), server.requests[1]


def test_a_read_that_fails_mid_page_never_reads_as_a_short_table(monkeypatch):
    """A 500 on the SECOND page must not come back as "the table holds 100 rows".

    `select_or_empty` is the caller forbidden from inventing rows: its non-200 answer means "no rows
    here", so a partial page would be this row's silent under-count wearing a neighbourly face.
    `select` stays loud about the same read.
    """
    server = StandInServer({"option": decision_rows(180, prefix="O")}, fail_after=1)
    monkeypatch.setattr(auger, "db", server)
    assert auger.select_or_empty(MINT_NS, "option", "") == []

    with pytest.raises(SystemExit) as ei:
        auger.select(MINT_NS, "option", "")
    assert "select option failed (500)" in str(ei.value), str(ei.value)


def test_a_table_that_cannot_answer_is_still_nothing_for_select_or_empty(monkeypatch):
    """`select_or_empty`'s own contract, unchanged by paging: one unanswered read, no rows, no walk."""
    server = StandInServer({})
    monkeypatch.setattr(auger, "db", server)
    assert auger.select_or_empty(MINT_NS, "option", "") == []
    assert len(server.requests) == 1, server.requests


def test_the_page_safe_mint_reads_the_highest_id_through_the_real_paged_select(
    monkeypatch,
):
    """AUG-069 and AUG-068 together: the mint reads ONE page-safe row out of a capped 101-row table.

    The AUG-069 cases stub `select` itself, which cannot see the paging loop; this one stubs the
    server a layer lower, so the read the mint depends on is the real paged one — and it still costs
    exactly one request, which is the "no count-based mint" property.
    """
    stored = [{"id": f"D-{i:03d}", "project_id": MINT_PID} for i in range(1, 102)]
    server = StandInServer({"decision": stored})
    monkeypatch.setattr(auger, "db", server)

    assert len(auger.select(MINT_NS, "decision", f"project_id=eq.{MINT_PID}")) == 101

    server.requests.clear()
    assert auger.next_id(MINT_NS, "decision", "D", width=3) == "D-102"
    assert len(server.requests) == 1, server.requests
    assert server.requests[0].endswith("?select=id&order=id.desc&limit=1"), (
        server.requests[0]
    )

    server.requests.clear()
    assert auger.decision_id_width(MINT_NS) == 3
    assert len(server.requests) == 1, server.requests


def test_status_and_dump_report_a_namespace_holding_more_than_a_page(project: dict):
    """AUG-068 criterion 2, against the real store: the 180-option shape the row measured.

    60 decisions x 3 options = 180 option rows in one namespace. `status` must count all 180 (it read
    100 before this change) and `dump` must render all three of D-031's options (it rendered two).
    """
    ns, pid = project["ns"], project["pid"]
    decisions = 60
    options = 3
    auger.insert(
        ns,
        "decision",
        [
            {
                "id": f"D-{i:03d}",
                "project_id": pid,
                "domain": "",
                "question_id": "",
                "chosen": f"option one of decision {i}",
                "why_not": "recorded by the page-scale leg",
                "reversal_cost": "",
                "confidence": 0.5,
                "status": "decided",
                "evidence_key": f"/auger/{pid}/D-{i:03d}",
            }
            for i in range(1, decisions + 1)
        ],
    )
    auger.insert(
        ns,
        "option",
        [
            {
                "id": f"D-{i:03d}-O{n}",
                "decision_id": f"D-{i:03d}",
                "label": f"option {n} of decision {i}",
                "costs": "",
                "breaks": "",
                "active": n == 1,
            }
            for i in range(1, decisions + 1)
            for n in range(1, options + 1)
        ],
    )

    # The cap, proved live rather than assumed: a bare GET is ONE page and says nothing about it.
    st, page, _ = auger.db(auger.tbl(ns, "option", ""))
    assert st == 200, (st, page)
    assert len(page) == API_PAGE == auger.API_PAGE, (
        f"the live server served {len(page)} rows for a filterless read — either the server's page "
        "size moved off API_PAGE (the stand-in and the loop both key on it) or this leg stored fewer "
        "rows than it meant to"
    )
    # ... and the read the two verbs make now returns the whole table.
    assert len(auger.select(ns, "option", "")) == decisions * options

    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert f"decisions {decisions} | options {decisions * options} |" in out, out[:400]

    rc, out = run_cli(["-n", ns, "dump"])
    assert rc == 0, out
    split = out.split("\n## D-031", 1)
    assert len(split) == 2, "D-031 is missing from the dump"
    block = split[1].split("\n## ", 1)[0]
    for n in range(1, options + 1):
        assert f"D-031-O{n}" in block, (
            f"D-031's option {n} is missing from what the dump rendered:\n{block}"
        )


# ---------------------------------------------------------------- the walk's TRIGGER (AUG-028)
# The rule walk fires on a `breaks` edge whose dst is a DECISION with NO dst_project — a LOCAL
# invalidation. Every case above HAND-WRITES that edge with `auger.edge`, which is precisely what the
# dogfood run had to do (hand-POST edge E-900001) because no command could write one: the impact pass
# always sets dst_project (a sibling's contract breaking is an escalation for the SIBLING, SPEC-002
# 2.2). The three cases below drive the same cascade through the CLI ALONE — `answer --invalidates` is
# the write surface — and the first one asserts the shape the walk keys on rather than the walk.
def break_cli(
    ns: str, did: str, bad: str, *, why: str = "", chosen: str = ""
) -> tuple[int, str]:
    """`auger answer --invalidates` — the local break, written by the verb under test."""
    argv = [
        "-n",
        ns,
        "answer",
        "--id",
        did,
        "--domain",
        "9.02",
        "--chosen",
        chosen or "keep what the old meter read",
        "--option",
        "keep what the old meter read",
        "--option",
        "re-measure against a known source",
        "--why-not",
        "the old reading came off a mis-wired meter",
        "--confidence",
        "0.7",
        "--invalidates",
        bad,
    ]
    if why:
        argv += ["--invalidates-why", why]
    return run_cli(argv)


def test_answer_invalidates_writes_a_local_breaks_edge(project: dict, monkeypatch):
    """Criterion (a): the CLI puts an invalidation ON THE RECORD as the ONE edge kind the rule walk
    reads — `breaks`, dst a DECISION, and no `dst_project`.

    That absent project endpoint is the whole point (`edge_index`): an edge that names a project
    points at a SIBLING's decision, which is an escalation for that sibling and never this project's
    decision dying. The impact pass always sets it, which is why the cascade was unreachable by any
    verb until this flag existed.
    """
    ns, pid = project["ns"], project["pid"]
    assert record_decision(ns, "D-008", "the July bench meter read the peak")[0] == 0
    assert rows(ns, "edge", "") == [], "the control: no verb had written an edge yet"

    # The PAYLOAD, not only the served row: the declared `edge` table has the project columns, so a
    # read normalizes a row that never carried one to null. "No invented endpoint" is a claim about
    # the write, and this is where it can be checked.
    posted: list = []
    real_db = auger.db

    def capture(path, method="GET", body=None, *a, **k):
        if method == "POST" and path.endswith("/tables/edge"):
            posted.extend(body if isinstance(body, list) else [body])
        return real_db(path, method, body, *a, **k)

    monkeypatch.setattr(auger, "db", capture)
    rc, out = break_cli(
        ns,
        "D-009",
        "D-008",
        why="the bench meter was mis-wired, so D-008 measured nothing",
    )
    assert rc == 0, out

    # Exactly one edge: this answer named no question, so it wrote no `closes`.
    e = row(ns, "edge", "")
    assert len(posted) == 1, posted
    assert "dst_project" not in posted[0], (
        f"the write invented a project endpoint: {posted[0]!r}"
    )
    assert e["kind"] == "breaks", e
    assert (e["src_kind"], e["src_id"]) == ("decision", "D-009"), e
    assert (e["dst_kind"], e["dst_id"]) == ("decision", "D-008"), e
    # A person read the record and said so; no model gate is in this path, and the confidence is the
    # module's "asserted directly, not scored" value rather than a score nobody produced.
    assert e["source"] == "human", e
    assert e["confidence"] == -1.0, e
    assert not e.get("dst_project"), (
        f"a local break names a project endpoint ({e['dst_project']!r}) — the walk reads that as a "
        "SIBLING's decision breaking, so the cascade can never fire"
    )
    assert e["note"] == (
        "D-009 invalidates D-008: the bench meter was mis-wired, so D-008 measured nothing"
    ), e
    # The walk's OWN index — the call `propagate` makes — reads this edge as the local trigger.
    local = auger.edge_index(auger.graph_edges(ns, pid))["breaks"]
    assert [b["id"] for b in local] == [e["id"]]
    # A break RECORDS a claim: the invalidated decision keeps its row, its answer and its status.
    dead = row(ns, "decision", "id=eq.D-008")
    assert dead["chosen"] == "the July bench meter read the peak"
    assert dead["status"] == "decided"
    assert e["id"] in out, out


def test_answer_invalidates_refuses_a_target_it_cannot_record_locally(project: dict):
    """The flag's claim is that the DECISION IS ALREADY STORED. A break into nothing is the invented
    endpoint `edge` refuses by name, and naming the answer itself would moot the very question that
    answer just closed. Every refusal leaves the invocation UNWRITTEN — not even the answer lands —
    because the targets are known from the arguments and a half-applied invocation is not a refusal.
    """
    ns, pid = project["ns"], project["pid"]
    assert record_decision(ns, "D-008", "the July bench meter read the peak")[0] == 0
    before = rows(ns, "decision", f"project_id=eq.{pid}")
    base = ["-n", ns, "answer", "--id", "D-009", "--chosen", "re-measure"]

    rc, msg, _ = run_cli_exit(base + ["--invalidates", "D-099"])
    assert rc != 0 and "D-099" in msg and "does not exist" in msg, msg

    rc, msg, _ = run_cli_exit(base + ["--invalidates", "D-009"])
    assert rc != 0 and "itself" in msg, msg

    rc, msg, _ = run_cli_exit(base + ["--invalidates-why", "because it was mis-wired"])
    assert rc != 0 and "--invalidates" in msg, msg

    assert rows(ns, "decision", f"project_id=eq.{pid}") == before, (
        "a refused invocation stored a decision"
    )
    assert rows(ns, "edge", "") == [], "a refused invocation wrote an edge"


def test_answer_refuses_a_missing_question_before_decision_or_option_writes(
    project: dict, monkeypatch
):
    """A bad --question-id refuses the whole answer before either answer table is touched."""
    ns, pid = project["ns"], project["pid"]
    before_decisions = rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc")
    before_options = rows(ns, "option", "order=id.asc")
    writes: list[tuple[str, object]] = []
    real_db = auger.db

    def capture(path, method="GET", body=None, *args, **kwargs):
        if method == "POST" and path.endswith(("/tables/decision", "/tables/option")):
            writes.append((path, body))
        return real_db(path, method, body, *args, **kwargs)

    monkeypatch.setattr(auger, "db", capture)
    rc, msg, _ = run_cli_exit(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-034",
            "--question-id",
            "Q-999999",
            "--chosen",
            "do not persist",
            "--option",
            "do not persist",
        ]
    )

    assert rc != 0 and "Q-999999" in msg and "does not exist" in msg, msg
    assert writes == [], (
        f"a refused missing question attempted answer writes: {writes!r}"
    )
    assert rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc") == before_decisions
    assert rows(ns, "option", "order=id.asc") == before_options


def test_answer_refuses_a_duplicate_decision_id_before_any_writes(
    project: dict, monkeypatch
):
    """A retry with an existing decision ID refuses before rewriting either answer table."""
    ns, pid = project["ns"], project["pid"]
    assert answer_cli(ns, "D-034", "4.05", "the recorded answer")[0] == 0
    before_decisions = rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc")
    before_options = rows(ns, "option", "order=id.asc")
    writes: list[tuple[str, object]] = []
    real_db = auger.db

    def capture(path, method="GET", body=None, *args, **kwargs):
        if method == "POST" and path.endswith(("/tables/decision", "/tables/option")):
            writes.append((path, body))
        return real_db(path, method, body, *args, **kwargs)

    monkeypatch.setattr(auger, "db", capture)
    rc, msg, _ = run_cli_exit(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-034",
            "--chosen",
            "a retry must not land",
            "--option",
            "a retry must not land",
        ]
    )

    assert rc != 0 and "D-034" in msg and "already exists" in msg, msg
    assert writes == [], f"a duplicate decision attempted answer writes: {writes!r}"
    assert rows(ns, "decision", f"project_id=eq.{pid}&order=id.asc") == before_decisions
    assert rows(ns, "option", "order=id.asc") == before_options


def test_a_verb_written_local_break_moots_the_questions_the_dead_decision_answered(
    project: dict, monkeypatch
):
    """Criterion (b): the documented loop is reachable from the CLI ALONE. Every edge here is written
    by a VERB — BEAT 2's `closes` edge by `answer --question-id`, the local break by the flag above —
    where the dogfood run had to hand-POST an edge row to make the cascade fire."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.05, calls))
    store_questions(ns, pid, ("Q-000001", Q_WATCH, "open"))
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered"

    # CONTROL: the question is settled and no local break is on the record, so the walk moves nothing.
    rc, out = run_cli(["-n", ns, "propagate", "--no-gate"])
    assert rc == 0, out
    assert "moot: 0   reopened: 0" in out, out
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered", out

    rc, out = break_cli(ns, "D-002", "D-001", why="the meter was mis-wired")
    assert rc == 0, out
    eid = row(ns, "edge", "kind=eq.breaks")["id"]

    rc, out = run_cli(["-n", ns, "propagate"])
    assert rc == 0, out
    assert "moot: 1   reopened: 0" in out, out

    q = row(ns, "question", "id=eq.Q-000001")
    assert q["status"] == "moot", out
    assert q["text"] == Q_WATCH, "a moot question keeps its row — it is never deleted"
    reason = f"D-001 was invalidated by {eid}"
    assert auger.q_reason(ns, "Q-000001") == reason
    assert reason in out, out

    # The dead decision is not rewritten, a withdrawn question is not re-asked, and the rule walk
    # needs no model: this cascade is stored edges all the way down.
    assert row(ns, "decision", "id=eq.D-001")["chosen"] == "single SQLite file"
    assert auger.askable_questions(ns, pid) == []
    assert calls == [], f"the rule walk called JEV: {calls}"

    rc, out2 = run_cli(["-n", ns, "propagate", "--no-gate"])
    assert rc == 0 and "moot: 0" in out2, out2
    assert auger.q_reason(ns, "Q-000001") == reason, (
        "a second pass rewrote the reason a moot question already had"
    )


def test_a_verb_written_local_break_reopens_the_settled_children(
    project: dict, monkeypatch
):
    """The cascade the flag triggers is the WHOLE BEAT 4 walk, not its first hop: the questions the
    dead decision answered go moot, the settled questions below them REOPEN with the reason recorded
    (Q4), the walk reaches the grandchild, and a second pass changes nothing."""
    ns, pid = project["ns"], project["pid"]
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.05, calls))
    store_questions(
        ns,
        pid,
        ("Q-000001", Q_WATCH, "open"),
        ("Q-000002", Q_STORE, "linked"),
        ("Q-000003", Q_CRASH, "linked"),
    )
    assert answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")[0] == 0
    # No verb writes `derives_from` yet (that is BEAT 1), so the TREE is hand-built and only the
    # TRIGGER comes from the CLI — which is the property under test.
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

    rc, out = break_cli(ns, "D-002", "D-001", why="the meter was mis-wired")
    assert rc == 0, out
    cause = f"D-001 was invalidated by {row(ns, 'edge', 'kind=eq.breaks')['id']}"

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

    # Both reasons name the BREAKING EDGE — the one the VERB wrote, read off the stored row.
    child, grandchild = auger.q_reason(ns, "Q-000002"), auger.q_reason(ns, "Q-000003")
    assert "reopened" in child and "Q-000001" in child and cause in child, child
    assert (
        "reopened" in grandchild and "Q-000002" in grandchild and cause in grandchild
    ), grandchild
    assert child in out and grandchild in out, out
    # Both reopened children are askable again and neither was asked: the gate re-examined each once.
    askable = [q["id"] for q in auger.askable_questions(ns, pid)]
    assert askable == ["Q-000002", "Q-000003"], askable
    assert len(calls) == 1, calls

    rc, out2 = run_cli(["-n", ns, "propagate"])
    assert rc == 0 and "moot: 0   reopened: 0" in out2, out2


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


# ---------------------------------------------------------------- ask -> stores what it proposed (AUG-035)
# The README's loop is `ask` then `answer --question-id`, and until AUG-035 the first half wrote
# nothing: `ask` printed the question JEV picked and the `question` table stayed empty, so the
# documented next move refused the engine's own suggestion. These cases assert the ROWS and the loop,
# not the print.
Q_NEXT = "how_is_it_tested"  # what JEV's new_question choice carries — the criterion it picked
Q_NEXT_TEXT = "What test proves this works?"


def ask_stub(choice: str, conf, noul):
    """A JEV stub that answers `ask`'s four questions the way the live decisions model does."""

    def _stub(state, questions, *a, **k):
        answers = {
            "next_subject": {
                "type": "choice",
                "choice": "testability",
                "confidence": 0.70,
            },
            "new_question": {"type": "choice", "choice": choice, "confidence": conf},
            "completeness": {"type": "score", "score": 2, "confidence": 0.70},
        }
        if noul is not None:
            answers["already_answered"] = {"type": "noul", "noul": noul}
        return {"answers": answers, "usage": {"cost": 1e-05}, "model": "stub"}, None

    return _stub


def test_ask_stores_the_question_it_proposes_and_the_answer_then_closes_it(
    project: dict, monkeypatch
):
    """The loop, end to end on the rows: ask stores the proposal, answer --question-id closes it."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out

    # The print contract is intact and the one line AUG-035 adds says the question is now closable.
    assert f"next question: {Q_NEXT}  (0.73)" in out, out
    assert "genuinely new, ask it" in out, out
    assert f"stored question: Q-000001  {Q_NEXT_TEXT}" in out, out

    q = row(ns, "question", "id=eq.Q-000001")
    assert q["project_id"] == pid and q["text"] == Q_NEXT_TEXT, q
    assert q["text"] != Q_NEXT and q["text"].endswith("?"), q
    assert q["status"] == "open", q
    assert q["qclass"] == auger.ASK_CLASS, (
        q
    )  # the source marker: this one came from ask
    assert q["ring"] == 1 and q["domain"] == "", q
    # The JEV value the table declares a column for: the gate's answered-verdict, and when it was taken.
    assert q["jev_already_answered"] == pytest.approx(0.10), q
    assert q["jev_checked_at"], q
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 1

    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets
    assert all(f["status"] == "open" for f in facets), facets

    # The loop's central move: the command the README documents no longer refuses.
    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")
    assert rc == 0, out
    assert "closed Q-000001" in out, out
    assert row(ns, "decision", "id=eq.D-001")["question_id"] == "Q-000001"
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered"
    facets = rows(ns, "facet", "question_id=eq.Q-000001")
    assert all(f["status"] == "closed" and f["closed_by"] == "D-001" for f in facets), (
        facets
    )


def test_ask_stores_question_below_the_which_question_confidence_threshold(
    project: dict, monkeypatch
):
    """The new-question confidence ranks JEV's choice; it is not the answered-score gate."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.24, 0.10))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "stored question: Q-000001" in out, out
    assert "question not stored:" not in out, out

    q = row(ns, "question", "id=eq.Q-000001")
    assert q["project_id"] == pid, q
    assert q["qclass"] == auger.ASK_CLASS and q["text"] == Q_NEXT_TEXT, q
    assert q["text"] != Q_NEXT and q["text"].endswith("?"), q
    facets = rows(ns, "facet", "question_id=eq.Q-000001&order=id.asc")
    assert [f["facet"] for f in facets] == list(auger.facet_set()), facets


def test_ask_sends_the_six_exact_new_question_criteria_to_jev(
    project: dict, monkeypatch
):
    """The stored sentence mapping must not change the choice criteria sent over the JEV wire."""
    ns = project["ns"]
    seen = {}

    def capture(state, questions, *args, **kwargs):
        seen.update(questions["new_question"]["criteria"])
        return ask_stub(Q_NEXT, 0.24, 0.10)(state, questions, *args, **kwargs)

    monkeypatch.setattr(auger, "jev", capture)
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert seen == {
        "how_does_it_fail": "what happens when a component stops working",
        "what_does_it_break": "which existing decision this next choice would invalidate",
        "what_is_the_data": "what the data means, not where it is stored",
        "who_owns_it": "ownership and lifecycle after delivery",
        "how_is_it_tested": "the test that proves it works",
        "none": "nothing left worth asking",
    }, seen


def test_ask_unknown_criterion_is_fail_closed_and_names_the_slug(
    project: dict, monkeypatch
):
    """An unmapped JEV key cannot become a machine-readable question row."""
    ns, pid = project["ns"], project["pid"]
    unknown = "criterion_added_without_a_sentence"
    monkeypatch.setattr(auger, "jev", ask_stub(unknown, 0.24, 0.10))
    before = len(rows(ns, "question", f"project_id=eq.{pid}"))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert (
        f"question not stored: JEV proposed unknown question criterion '{unknown}'"
        in out
    ), out
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == before

    qid, why = auger.store_proposed_question(ns, pid, {"choice": unknown}, 0.10)
    assert qid == ""
    assert unknown in why
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == before


def test_ask_none_criterion_is_not_stored(project: dict, monkeypatch):
    """JEV's explicit no-more-questions choice remains a refusal, not a row."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub("none", 0.24, 0.10))
    before = len(rows(ns, "question", f"project_id=eq.{pid}"))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "question not stored: JEV proposed none: nothing left worth asking" in out, (
        out
    )
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == before


def test_ask_does_not_store_the_same_question_twice(project: dict, monkeypatch):
    """A second ask naming the same question says it is already open instead of storing it again."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0 and "stored question: Q-000001" in out, out

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "stored question:" not in out, out
    assert "question not stored: already open as Q-000001" in out, out
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 1
    assert len(rows(ns, "facet", "question_id=eq.Q-000001")) == len(auger.facet_set())


def test_ask_does_not_repropose_a_question_after_answer_closes_it(
    project: dict, monkeypatch
):
    """The fresh ask -> answer -> ask loop preserves the durable answered question identity."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0 and "stored question: Q-000001" in out, out
    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")
    assert rc == 0 and "closed Q-000001" in out, out

    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "stored question:" not in out, out
    assert "already closed/answered" in out and "Q-000001" in out, out
    assert "answered by D-001" in out, out
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 1
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 1
    assert row(ns, "question", "id=eq.Q-000001")["status"] == "answered"


def test_ask_stores_a_different_question_after_answer_closes_the_first(
    project: dict, monkeypatch
):
    """Closing one proposal does not suppress a different canonical proposal next round."""
    ns, pid = project["ns"], project["pid"]
    different = "how_does_it_fail"
    different_text = auger.ASK_NEW_QUESTION_CRITERIA[different]["sentence"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0 and "stored question: Q-000001" in out, out
    rc, out = answer_cli(ns, "D-001", "4.05", "single SQLite file", qid="Q-000001")
    assert rc == 0 and "closed Q-000001" in out, out

    monkeypatch.setattr(auger, "jev", ask_stub(different, 0.73, 0.10))
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert f"stored question: Q-000002  {different_text}" in out, out
    assert len(rows(ns, "question", f"project_id=eq.{pid}")) == 2
    assert len(rows(ns, "decision", f"project_id=eq.{pid}")) == 1
    assert row(ns, "question", "id=eq.Q-000002")["status"] == "open"


@pytest.mark.parametrize(
    "stub, want",
    [
        (ask_stub("", 0.24, 0.10), "JEV proposed no question"),
        (
            ask_stub(Q_NEXT, 0.24, auger.T_ANSWERED + 0.05),
            "the gate scores it already answered",
        ),
        (ask_stub(Q_NEXT, 0.24, None), "the gate returned no answered-verdict"),
    ],
)
def test_ask_stores_nothing_when_a_gate_refuses_the_proposal(
    project: dict, monkeypatch, stub, want
):
    """Fail-closed: every refusal leaves the `question` table exactly as empty as it was, and says so."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", stub)

    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert f"question not stored: {want}" in out, out
    assert "stored question:" not in out, out
    assert rows(ns, "question", f"project_id=eq.{pid}") == []
    assert rows(ns, "facet", f"project_id=eq.{pid}") == []


def test_ask_stays_a_report_when_the_store_itself_is_refused(
    project: dict, monkeypatch
):
    """A refused insert stores nothing, is printed with the transport's own message, and keeps rc 0."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))

    def refuse(*a, **k):
        raise SystemExit("insert question failed (400): boom")

    monkeypatch.setattr(auger, "insert", refuse)
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "question not stored: the question row was refused" in out, out
    assert rows(ns, "question", f"project_id=eq.{pid}") == []


def test_ask_reports_a_partial_store_instead_of_calling_it_clean(
    project: dict, monkeypatch
):
    """The row and its facets are two writes. A facet that fails after the row landed is SAID OUT LOUD.

    Nothing here can be undone — the module has no row delete — so the contract is that a partial
    store is reported as one, never printed as `stored question:` and left to look complete.
    """
    ns = project["ns"]
    monkeypatch.setattr(auger, "jev", ask_stub(Q_NEXT, 0.73, 0.10))
    real, last = auger.facet, auger.facet_set()[-1]

    def half(ns_: str, pid_: str, qid_: str, name: str, **k):
        if name == last:
            raise SystemExit("insert facet failed (400): boom")
        return real(ns_, pid_, qid_, name, **k)

    monkeypatch.setattr(auger, "facet", half)
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    assert "stored question: Q-000001" in out, out
    assert f"WARNING the question row landed but facet {last!r} did not" in out, out
    assert (
        len(rows(ns, "facet", "question_id=eq.Q-000001")) == len(auger.facet_set()) - 1
    )


def test_ask_stores_nothing_when_jev_is_unreachable(project: dict, monkeypatch):
    """The pre-existing fail-closed arm is unchanged: no verdict, no store, exit 1 and no row."""
    ns, pid = project["ns"], project["pid"]
    monkeypatch.setattr(
        auger, "jev", lambda *a, **k: (None, "all 6 JEV keys failed; last: HTTP 401")
    )
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 1, out
    assert "JEV unavailable" in out, out
    assert "stored question:" not in out and "question not stored" not in out, out
    assert rows(ns, "question", f"project_id=eq.{pid}") == []


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
    # The refusal is atomic: validating the named question happens before the answer rows are stored.
    assert rows(ns, "decision", "id=eq.D-001") == []


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
def test_patch_reporting_zero_updates_is_a_loud_failure(decided: dict, monkeypatch):
    """A zero-row PATCH is a refusal, and the stored flag remains unchanged."""
    ns = decided["ns"]
    before = option_flags(ns)
    monkeypatch.setattr(auger, "patch", lambda *a, **k: {"updated": 0})
    rc, message, out = run_cli_exit(
        ["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"]
    )
    assert rc != 0
    assert "patched 0 rows" in message
    assert "nothing changed" in message
    assert out == ""
    assert option_flags(ns) == before

    monkeypatch.undo()
    rc, out = run_cli(["-n", ns, "toggle", "--off", "D-001-O1", "--on", "D-001-O2"])
    assert rc == 0 and "D-001-O1->off (1)" in out, out
    assert option_flags(ns)["D-001-O1"] is False


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
    assert "nothing changed" in report, report  # the failure names the broken behaviour
    assert "3 passed" in report, report


# ================================================================= url encoding (AUG-061)
# A name with a space in it used to kill the whole verb BEFORE a request was sent: `http.client`
# validates the request line and raises `InvalidURL` for a raw space, so the traceback came from the
# client, not from DuckBrain. These cases pin the boundary where every URL is built — the path
# segments, the query VALUES (a spaced `project_id` is a first-class name here, not a filter to
# quote by hand), and the fact that the shapes the rest of this suite relies on did not move.
def validate_like_the_transport_does(path: str) -> None:
    """Put `path` through the SAME check that raised InvalidURL: `http.client.putrequest`.

    No socket is opened — `putrequest` validates the request line and nothing else, which is
    exactly the step the defect died in. Using the real validator (rather than, say, asserting
    "no space in the string") means a URL that our encoder lets through in a shape the client
    would still refuse cannot pass this test.
    """
    conn = http.client.HTTPConnection("127.0.0.1", 1)
    try:
        conn.putrequest("GET", path)
    finally:
        conn.close()


def test_the_validator_catches_the_pre_fix_shape_it_replaced():
    """The control: this is the URL the CLI built before the fix, and the client still refuses it.

    Without this, a `validate_like_the_transport_does` that silently did nothing would make every
    case below vacuous. The exact string is the one in the reported traceback.
    """
    with pytest.raises(http.client.InvalidURL):
        validate_like_the_transport_does(
            "/api/ns/ns with space/tables/project?order=created_at.desc&limit=1"
        )


def test_tbl_percent_encodes_spaced_path_segments():
    path = auger.tbl("ns with space", "Test Table")
    assert path == "/api/ns/ns%20with%20space/tables/Test%20Table", path
    validate_like_the_transport_does(path)


def test_tbl_encodes_a_spaced_filter_value_and_keeps_its_operator():
    path = auger.tbl("auger", "decision", "project_id=eq.Test Project&order=id.asc")
    assert (
        path
        == "/api/ns/auger/tables/decision?project_id=eq.Test%20Project&order=id.asc"
    ), path
    validate_like_the_transport_does(path)


def test_tbl_encodes_both_halves_at_once():
    """The reported case: a spaced namespace AND a spaced filter value in one URL."""
    path = auger.tbl("ns with space", "project", "id=eq.Test Project&limit=1")
    assert (
        path == "/api/ns/ns%20with%20space/tables/project?id=eq.Test%20Project&limit=1"
    ), path
    validate_like_the_transport_does(path)


def test_the_query_shapes_this_suite_depends_on_do_not_move():
    """Structural characters stay literal — an encoder that broke them would break every read.

    `,` is the one exception kept readable on purpose: postgrest uses it as a LIST separator, so
    `select=id,name` and `order=domain.asc,id.asc` must survive as written.
    """
    assert auger.tbl("auger", "project") == "/api/ns/auger/tables/project"
    assert auger.tbl("auger", "option", "") == "/api/ns/auger/tables/option"
    for query in (
        "order=id.asc",
        "select=id&order=id.desc&limit=1",
        "pk=eq.D-001",
        "project_id=eq.P-PYTEST&order=domain.asc,id.asc",
        "confidence=lt.0.6",
        "kind=eq.opens&src_id=eq.D-001",
        "order=created_at.desc,id.desc",
    ):
        assert (
            auger.tbl("auger", "decision", query)
            == f"/api/ns/auger/tables/decision?{query}"
        )


def test_reserved_characters_inside_a_value_cannot_reframe_the_url():
    """`?` and `#` in a value are DATA, not URL structure — a raw `#` would truncate the request.

    This is the same class of defect as the raw space, one step further along: the client would
    happily send a `#` and the server would see a different query than the caller wrote.
    """
    path = auger.tbl("auger", "decision", "id=eq.a?b#c")
    assert path == "/api/ns/auger/tables/decision?id=eq.a%3Fb%23c", path
    assert path.count("?") == 1 and "#" not in path, path
    validate_like_the_transport_does(path)


def test_the_ampersand_separator_is_still_structure():
    """`&` stays the parameter SEPARATOR: encoding it would fold every multi-parameter query into one.

    The honest limit of a query STRING as this module's interface: a value's own `&` has to be
    encoded by the caller, because by the time `tbl()` sees the string the two meanings are
    indistinguishable. No caller here builds such a value — every filter is an id, a status or a
    confidence — and this case pins the behaviour deliberately rather than by accident.
    """
    path = auger.tbl("auger", "decision", "id=eq.a&b")
    assert path == "/api/ns/auger/tables/decision?id=eq.a&b", path
    assert path.count("&") == 1, path


def test_ns_tables_path_encodes_the_namespace_it_names():
    assert auger.ns_tables_path("ns with space") == "/api/ns/ns%20with%20space/tables"
    assert auger.ns_tables_path("auger") == "/api/ns/auger/tables"


def test_a_spaced_namespace_path_is_answered_by_the_live_service(live_service: str):
    """The path half, live: the request REACHES DuckBrain, and the space comes back decoded.

    A name that exists and one that does not travel through the same decoder, so this case needs no
    namespace of its own — and it could not have one anyway: the API refuses to CREATE a name with a
    space (`VALIDATION_ERROR`, verified live), which is exactly why the reported defect was a
    read-side failure in the first place. 200 and 404 are both ANSWERS; `InvalidURL` never was.
    """
    name = "auger 061 does not exist"
    st, body, _ = auger.db(auger.ns_tables_path(name))
    assert st in (200, 404), body
    if st == 200:
        # The service resolved the request line back to a namespace NAME: `%20` was decoded.
        assert body["namespace"] == name, body


def test_recall_sends_an_encoded_namespace(monkeypatch):
    """The memories search quotes both halves of the query now; capture the URL it sends."""
    seen: dict = {}

    def fake_db(path, *a, **kw):
        seen["path"] = path
        return 200, {"items": []}, {}

    monkeypatch.setattr(auger, "db", fake_db)
    assert auger.recall("ns with space", "one query", limit=3) == []
    assert seen["path"] == (
        "/api/memories?namespace=ns%20with%20space&q=one%20query&limit=3"
    ), seen["path"]
    validate_like_the_transport_does(seen["path"])


def test_teardown_deletes_an_encoded_namespace(monkeypatch):
    """`delete_namespace` was the fifth hand-interpolated call site; it is encoded too."""
    seen: dict = {}

    def fake_db(path, *a, **kw):
        seen["path"] = path
        return 404, {"error": "gone"}, {}

    monkeypatch.setattr(auger, "db", fake_db)
    assert auger.delete_namespace("ns with space") == (404, {"error": "gone"})
    assert seen["path"] == "/api/namespaces/ns%20with%20space", seen["path"]
    validate_like_the_transport_does(seen["path"])


def test_a_spaced_project_id_round_trips_through_a_live_verb(ns: str, tmp_path):
    """The acceptance criterion, as a case: a project named with a space is ADDRESSABLE, not a crash.

    Both halves are live against DuckBrain: the WRITE stores the row under the spaced id, and the
    READ finds it by `id=eq.<spaced id>` — which only works because the encoded value is decoded by
    the server. `status` is deliberately not the feedback verb: the point is that ordinary verbs work.
    """
    pid = "Test Project"
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED_TEXT)

    rc, out = run_cli(
        [
            "-n",
            ns,
            "start",
            "--name",
            "spacedtest",
            "--id",
            pid,
            "--seed-file",
            str(seed),
        ]
    )
    assert rc == 0 and f"project {pid} in namespace {ns}" in out, out

    rc, status_out = run_cli(["-n", ns, "--project-id", pid, "status"])
    assert rc == 0, status_out
    assert f"project {pid} — spacedtest" in status_out, status_out

    # The stored row is the assertion: the spaced filter found THIS project, not the default one.
    stored = row(ns, "project", f"id=eq.{pid}")
    assert stored["name"] == "spacedtest", stored


def test_an_unknown_spaced_namespace_is_an_answer_not_a_traceback(live_service: str):
    """The reported command line, verbatim: it may not find a namespace, but it may not crash."""
    code, message, _out = run_cli_exit(
        ["--namespace", "ns with space", "--project-id", "Test Project", "feedback"]
    )
    assert code != 0, message
    assert "InvalidURL" not in message, message
    assert "control characters" not in message, message
    # A substrate answer either names the namespace it could not read, or says no project is stored.
    assert message.strip(), message
    assert "ns with space" in message or "no project row" in message, message


# ================================================================= teardown
def test_teardown_removes_the_namespace_directory(ns: str):
    """The teardown is proved here instead of being trusted: run it, then look."""
    from conftest import ns_path, teardown_namespace as take_down

    assert os.path.isdir(ns_path(ns))

    assert take_down(ns) == []
    assert not os.path.exists(ns_path(ns))

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

    def fake_recall(namespace, q, limit=5, missing_ok=False):
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
        lambda n, q, limit=5, missing_ok=False: (
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
            lambda n, q, limit=5, missing_ok=False: (
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


def test_the_impact_walk_prints_a_line_for_a_member_it_left_unchanged(
    ns: str, sibling_ns: str, monkeypatch
):
    """AUG-027(a): the walk PRINTS — one line per walked member, verdict word and score included.

    Silence is ambiguous: a reader cannot tell "the pass is not wired" from "the pass asked and
    decided nothing". The line is the pass's footprint, so it is printed for the outcome that
    writes nothing (and no longer only for the two that do).
    """
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=("D-007", "the envelope is one JSON object per message"),
    )
    monkeypatch.setattr(auger, "jev", impact_stub(0.0, [], confidence=0.8))  # unchanged

    rc, out = record_decision(ns, "D-016", "the envelope is NDJSON", scope="bundle")
    assert rc == 0, out
    assert f"impact: {sib} D-007 unchanged (score 0.00)" in out, out


def test_the_impact_walk_reports_a_member_it_walked_with_nothing_to_compare(
    ns: str, sibling_ns: str, monkeypatch
):
    """AUG-027(a), the other silence: the member WAS walked and holds no bundle-scoped decision."""
    _home, sib = bundled_pair(ns, sibling_ns, monkeypatch)  # sibling holds no decision
    calls: list = []
    monkeypatch.setattr(auger, "jev", impact_stub(0.0, calls, confidence=0.8))

    rc, out = record_decision(ns, "D-017", "the envelope is NDJSON", scope="bundle")
    assert rc == 0, out
    assert calls == [], "the model was asked about a decision the member does not hold"
    assert f"impact: {sib} walked, no bundle-scoped decision to compare" in out, out


def test_a_verdict_below_the_confidence_floor_is_unknown_and_fails_closed(
    ns: str, sibling_ns: str, monkeypatch
):
    """AUG-027(b): a low-confidence verdict is UNKNOWN — skipped, warned, and NOT honored.

    The module's own doctrine: an uncertain verdict is surfaced the way a transport error is
    surfaced. A model that answers "unchanged" at 0.24 confidence is saying it does not know, and
    an unknown verdict writes nothing — it never becomes a silent, final "unchanged".
    """
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=(
            "D-001",
            "the envelope hash is validated before a message is trusted",
        ),
    )
    calls: list = []
    monkeypatch.setattr(auger, "jev", impact_stub(0.24, calls, confidence=0.24))

    rc, out = record_decision(
        ns, "D-002", "the envelope hash is blake3, not sha256", scope="bundle"
    )
    assert rc == 0, out
    assert len(calls) == 1, f"the walk did not ask the model: {calls}"
    assert "WARNING" in out and str(auger.T_IMPACT_FLOOR) in out, out
    assert sib in out and "D-001" in out and "unknown" in out, out
    assert rows(ns, "edge", "") == [], (
        "an unknown verdict was honored: an edge was written"
    )
    assert rows(ns, "escalation", "") == []
    assert (
        row(sibling_ns, "decision", "id=eq.D-001")["chosen"]
        == "the envelope hash is validated before a message is trusted"
    )


def test_the_dogfood_d003_vs_d001_walk_prints_the_score_it_saw(
    ns: str, sibling_ns: str, monkeypatch
):
    """AUG-027(c): the dogfood pair — D-003 vs a sibling's D-001 at 0.24 — PRINTS the score.

    The live run this regression comes from returned in ~1s with no line at all, so the score the
    model gave for the walk was unrecoverable from the verb's own output. The score is the evidence
    a reader needs to see that the pass ran and what it read.
    """
    _home, sib = bundled_pair(
        ns,
        sibling_ns,
        monkeypatch,
        decision=(
            "D-001",
            "mcview validates the hash of an envelope before it trusts it",
        ),
    )
    monkeypatch.setattr(auger, "jev", impact_stub(0.24, [], confidence=0.24))

    rc, out = record_decision(
        ns,
        "D-003",
        "mccli swaps the envelope hash from sha256 to blake3",
        scope="bundle",
    )
    assert rc == 0, out
    assert f"impact: {sib} D-001 unknown (score 0.24)" in out, out


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
    # AUG-027(a): an unanswered member is still a walked member — the run shows it, and says there
    # is no score to show (the warning right below carries WHY).
    assert f"impact: {sib} D-007 unknown (no score)" in out, out
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

    def fake_recall(namespace, q, limit=5, missing_ok=False):
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

    def _stub(namespace, q, limit=5, missing_ok=False):  # noqa: ARG001 - mirrors recall's signature
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
        lambda namespace, q, limit=5, missing_ok=False: (
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


# ================================================================= the 44-domain grid (AUG-004)
# The grid lives with the method's skill (`~/.hermes/skills/.../02-domains/`), NOT in this repo,
# because a copy here would drift the moment the skill's grid changes. Every case that READS it
# therefore skips loudly on a host that does not have it, naming AUGER_DOMAIN_GRID — and every case
# that must run on any host builds the grid files it needs in `tmp_path`.
#
# Assertions are on the STORED ROWS wherever the claim is about storage (that is what the coverage
# numbers are derived from); they read stdout only where the claim IS about the report, which is
# the one claim that cannot be checked anywhere else: a domain with no row is reported ABSENT
# rather than silently omitted, and "silently omitted" is a property of the output.
GRID_NUMBERS = [f"4.{n:02d}" for n in range(1, 45)]
# The two header shapes the live grid uses, taken from it verbatim (4.20 for the inline one, 4.01
# for the key/value one). The cases below drive both so a shape change fails HERE, loudly, rather
# than silently defaulting a triage score somewhere.
GRID_INLINE_HEADER = "Triage: 3×3×3=27; floor 5; status CASCADED; terminating ring 5.\n"
GRID_KV_HEADER = (
    "status: NOT-REACHED\n"
    "triage: 2×1×3=6\n"
    "ring_floor: 2\n"
    "reason: the single-operator model is known\n"
    "owner: product owner\n"
    "trigger: before PRD acceptance\n"
    "containment_default: preserve what is recorded; reopen at the trigger.\n"
    "terminating_ring: null — domain was not opened; NOT-REACHED satisfies coverage.\n"
)


def grid_or_skip() -> list[dict]:
    """The method's grid as parsed, or a SKIP naming the path where the skill keeps it."""
    try:
        return auger.read_domain_grid()
    except SystemExit as exc:
        pytest.skip(f"the 44-domain grid is not on this host: {exc}")


def write_grid_file(directory: str, stem: str, header: str) -> str:
    """One grid file in `directory`, in the grid's own shape: heading, blank line, header."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{stem}.md")
    with open(path, "w") as fh:
        fh.write(f"# Domain {stem[:4]}\n\n{header}\n")
    return path


def seed_grid(ns: str) -> str:
    """`init --seed-domains` in this namespace. Returns its output; fails the test if it refused."""
    rc, out = run_cli(["-n", ns, "init", "--seed-domains"])
    assert rc == 0 and "domain grid:" in out, out
    return out


def coverage_lines(out: str) -> list[str]:
    """The per-domain coverage lines of a `status` run — one per domain, including the absent."""
    return [ln for ln in out.splitlines() if "terminating ring" in ln]


# ---------------------------------------------------------------- the parser (no live API needed)
def test_the_grid_source_parses_to_exactly_the_canonical_44():
    """Every live grid file, read: 44 numbers, 4.01-4.44, and a plausible score on each."""
    grid = grid_or_skip()
    assert [e["num"] for e in grid] == GRID_NUMBERS, [e["num"] for e in grid]
    for e in grid:
        assert e["name"] == e["name"].lower() and e["name"], e
        assert 1 <= e["triage"] <= 27, (
            e
        )  # blast radius × uncertainty × irreversibility, each 1-3
        assert e["ring_floor"] >= 2, e
        # the grid's ring ceiling is 8; None is the "not opened" value and is not a number at all
        assert e["terminating_ring"] is None or 1 <= e["terminating_ring"] <= 8, e


def test_the_parser_reads_both_header_shapes_the_grid_actually_uses(tmp_path):
    """The inline CASCADED header and the key/value NOT-REACHED header, field for field."""
    inline = auger.parse_domain_file(
        write_grid_file(str(tmp_path), "4.20-concurrency", GRID_INLINE_HEADER)
    )
    assert (inline["num"], inline["name"]) == ("4.20", "concurrency")
    assert (inline["triage"], inline["ring_floor"], inline["terminating_ring"]) == (
        27,
        5,
        5,
    )

    # the ⊕ header spells the floor `unconditional floor 5` and fills the same field (4.12's shape)
    uncond = auger.parse_domain_file(
        write_grid_file(
            str(tmp_path),
            "4.12-runbox-runtime",
            "Triage: 3×2×3=18; unconditional floor 5; status CASCADED; terminating ring 5.\n",
        )
    )
    assert (uncond["triage"], uncond["ring_floor"], uncond["terminating_ring"]) == (
        18,
        5,
        5,
    )

    kv = auger.parse_domain_file(
        write_grid_file(str(tmp_path), "4.01-product-people", GRID_KV_HEADER)
    )
    assert (kv["num"], kv["name"]) == ("4.01", "product-people")
    assert kv["triage"] == 6 and kv["ring_floor"] == 2
    assert kv["terminating_ring"] is None, (
        "an unopened domain's ring is None, not a number"
    )


def test_a_header_the_parser_cannot_read_is_refused_with_the_file_name(tmp_path):
    """Neither shape, so the parser stops — no default score, no zero ring, no silent skip."""
    path = write_grid_file(
        str(tmp_path),
        "4.07-cache",
        "Triage: high; ring floor: soon; terminating ring: eventually.\n",
    )
    with pytest.raises(SystemExit) as ei:
        auger.parse_domain_file(path)
    message = str(ei.value)
    assert "4.07-cache.md" in message and "triage" in message, message


def test_a_triage_line_whose_own_factors_do_not_multiply_is_refused(tmp_path):
    """2×1×2 is 4, and a grid line claiming 9 is corrupt: store neither number."""
    path = write_grid_file(
        str(tmp_path),
        "4.09-layout",
        "status: NOT-REACHED\ntriage: 2×1×2=9\nring_floor: 2\n"
        "terminating_ring: null — domain was not opened.\n",
    )
    with pytest.raises(SystemExit) as ei:
        auger.parse_domain_file(path)
    message = str(ei.value)
    assert "does not multiply" in message and "2×1×2=9" in message, message


def test_a_file_that_is_not_named_4_NN_slug_is_refused(tmp_path):
    """The file name IS the number and the slug (section 4: zero-padded, always)."""
    path = write_grid_file(str(tmp_path), "4.1-product-people", GRID_KV_HEADER)
    with pytest.raises(SystemExit) as ei:
        auger.parse_domain_file(path)
    assert "4.NN-<slug>.md" in str(ei.value), str(ei.value)


def test_a_grid_that_is_not_the_canonical_44_is_refused_by_name(tmp_path):
    """Two files is not 44: refused, with the numbers it is missing NAMED rather than assumed."""
    directory = str(tmp_path / "02-domains")
    write_grid_file(directory, "4.01-product-people", GRID_KV_HEADER)
    write_grid_file(directory, "4.02-user-model", GRID_KV_HEADER)
    with pytest.raises(SystemExit) as ei:
        auger.read_domain_grid(directory)
    message = str(ei.value)
    assert "4.03" in message and "44" in message and "missing" in message, message


# ---------------------------------------------------------------- seeding (live namespace)
def test_seeding_writes_the_44_domain_rows_with_the_grids_own_numbers(ns: str):
    """44 rows that round-trip, at both ends of the grid, with the numbers the FILES carry."""
    grid = grid_or_skip()
    seed_grid(ns)
    stored = rows(ns, "domain", "order=num.asc")
    assert len(stored) == 44, (
        f"expected the 44-domain grid, stored {len(stored)} row(s)"
    )
    assert [r["num"] for r in stored] == GRID_NUMBERS

    first = row(ns, "domain", "num=eq.4.01")
    assert (first["name"], first["triage"], first["ring_floor"]) == (
        "product-people",
        6,
        2,
    )
    assert first.get("terminating_ring") is None, (
        "4.01 was never opened: it has no ring"
    )
    assert first["status"] == auger.DOMAIN_SEED_STATUS == "NOT-REACHED"
    assert first["owner"] == "unassigned"
    assert first["trigger"] == "first question in domain"
    assert first["containment"] == "none"
    assert first["id"].startswith("DM-")
    # seeded by `init`, i.e. before any `start`: the namespace's grid, unbound to a project
    assert first["project_id"] == ""

    last = row(ns, "domain", "num=eq.4.44")
    assert (
        last["name"],
        last["triage"],
        last["ring_floor"],
        last["terminating_ring"],
    ) == ("meta-unknowns", 27, 5, 5)

    # field by field, against the files themselves: the seeder is a reader, and this proves it
    # did not carry a table of its own
    by_num = {e["num"]: e for e in grid}
    for r in stored:
        e = by_num[r["num"]]
        assert [r["triage"], r["ring_floor"], r.get("terminating_ring")] == [
            e["triage"],
            e["ring_floor"],
            e["terminating_ring"],
        ], r


def test_reseeding_the_grid_adds_no_second_copy(ns: str):
    """Idempotent by read-before-write: the second run SKIPS 44 and rewrites no id."""
    grid_or_skip()
    first_out = seed_grid(ns)
    before = {r["num"]: r["id"] for r in rows(ns, "domain", "")}
    assert len(before) == 44
    second_out = seed_grid(ns)
    after = {r["num"]: r["id"] for r in rows(ns, "domain", "")}
    assert len(after) == 44, (
        f"a re-seed duplicated the grid: {len(after)} row(s) stored"
    )
    assert after == before, (
        "a re-seed churned ids instead of skipping what was already stored"
    )
    assert "44 row(s) written, 0 already present" in first_out, first_out
    assert "0 row(s) written, 44 already present" in second_out, second_out


def test_the_grid_is_bound_to_the_project_that_already_exists(project: dict):
    """`init --seed-domains` AFTER `start` binds the rows; `status` reads that slice too."""
    grid_or_skip()
    seed_grid(project["ns"])
    stored = rows(project["ns"], "domain", "")
    assert len(stored) == 44
    assert {r["project_id"] for r in stored} == {project["pid"]}


# ---------------------------------------------------------------- coverage reporting
def test_status_names_every_absent_grid_domain_when_no_row_is_stored(project: dict):
    """The case the rule is FOR: an empty `domain` table is 44 ABSENT, each one NAMED."""
    grid = grid_or_skip()
    ns = project["ns"]
    assert rows(ns, "domain", "") == []
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "44 in the grid | 0 seeded | 44 absent" in out, out
    reported = coverage_lines(out)
    assert len(reported) == 44, (
        f"{len(reported)} domain line(s) for a 44-domain grid:\n{out}"
    )
    for e in grid:
        assert e["num"] in out, (
            f"{e['num']} was silently omitted from the coverage report"
        )
        line = next(ln for ln in reported if ln.strip().startswith(e["num"]))
        assert "ABSENT" in line, line
    assert "ABSENT (a grid domain with no stored" in out, out


def test_status_reports_coverage_and_the_terminating_ring_per_domain(decided: dict):
    """Seeded around two stored decisions: one line per domain, each carrying its ring."""
    grid = grid_or_skip()
    ns = decided["ns"]
    seed_grid(ns)
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "44 in the grid | 44 seeded | 0 absent" in out, out
    reported = coverage_lines(out)
    assert len(reported) == 44, f"{len(reported)} domain line(s):\n{out}"
    assert (
        "answered 2 (a decision or question row carries the num) | NOT-REACHED 42 (no such row)"
        in out
    ), out

    answered = next(ln for ln in reported if ln.strip().startswith("4.05"))
    assert "answered" in answered and "terminating ring 5" in answered, answered
    assert "decisions/questions 1" in answered, answered
    unopened = next(ln for ln in reported if ln.strip().startswith("4.01"))
    assert "NOT-REACHED" in unopened, unopened
    assert "terminating ring none (not opened)" in unopened, unopened

    # the report is a projection of the STORED rows, so those rows are the assertion
    stored = {r["num"]: r for r in rows(ns, "domain", "")}
    assert len(stored) == 44
    assert stored["4.05"]["terminating_ring"] == 5
    assert stored["4.01"].get("terminating_ring") is None
    by_num = {e["num"]: e for e in grid}
    for num, r in stored.items():
        assert r["triage"] == by_num[num]["triage"], (num, r)
        assert r["ring_floor"] == by_num[num]["ring_floor"], (num, r)
    # a domain holding an answer still carries the seed's own status word, and the line says so
    # rather than letting a reader believe the status column was updated by an answer
    assert stored["4.05"]["status"] == "NOT-REACHED"
    assert "(row status NOT-REACHED)" in answered, answered


def test_a_domain_whose_row_is_gone_is_reported_absent_not_skipped(project: dict):
    """One row moved off the grid: 4.05 prints ABSENT, the moved number prints off-grid."""
    grid_or_skip()
    ns = project["ns"]
    seed_grid(ns)
    [moving] = rows(ns, "domain", "num=eq.4.05")
    auger.patch(
        ns, "domain", moving["id"], {"num": "9.99", "name": "moved-off-the-grid"}
    )
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    assert "44 in the grid | 43 seeded | 1 absent" in out, out
    absent = next(ln for ln in coverage_lines(out) if ln.strip().startswith("4.05"))
    assert "ABSENT" in absent, absent
    assert "no stored `domain` row — absence, which is NOT" in out, out
    assert (
        "ABSENT (a grid domain with no stored `domain` row" in out and "4.05" in out
    ), out
    off = next(ln for ln in coverage_lines(out) if ln.strip().startswith("9.99"))
    assert "off-grid" in off, off
    assert "OFF-GRID rows" in out and "9.99" in out, out
    # and the moved row is still a real stored row: the report describes the table, it does not fix it
    assert row(ns, "domain", "num=eq.9.99")["id"] == moving["id"]


# ================================================================= the confidence sentinel (AUG-062)
# `answer` without --confidence stores the spec'd sentinel -1 ("asserted directly, not scored by
# a model" — the `edge` docstring). That value must STAY in the store; the defect is the LEAK:
# renders showed a bare -1 as if it were a measurement, and the feedback engine's `0 <= c` filter
# read "unmeasured" as "measured high", reporting a decision nobody scored as being at or above
# the threshold. Every case below proves the sentinel survives in the ROW and never reaches the
# READER. Deterministic throughout: the models are stubbed, the rows are re-read from DuckBrain.
def test_confidence_sentinel_stays_in_the_store_and_never_renders(project: dict):
    """AC1/AC5: a default-confidence answer keeps -1 in the decision row, but neither its own
    echo nor `dump` ever prints the sentinel — an unmeasured confidence renders as n/a."""
    ns = project["ns"]
    rc, out = run_cli(
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
            "--option",
            "single SQLite file",
            "--option",
            "Postgres",
            "--why-not",
            "the seed forbids a second service",
        ]
    )
    assert rc == 0, out
    # The sentinel is UNCHANGED in the store — the spec'd "asserted directly" value.
    assert row(ns, "decision", "id=eq.D-001")["confidence"] == pytest.approx(-1.0)
    # AC5: the echo says what was recorded, not the raw sentinel. (The echo embeds the
    # namespace name, which can itself contain "-1" — hex digit 1 after the prefix dash.)
    assert "-1" not in out.replace(ns, "<ns>"), out
    assert "confidence n/a" in out, out
    # AC1: `dump` renders the same decision as "conf n/a", never a bare -1/-1.0.
    rc, dump_out = run_cli(["-n", ns, "dump"])
    assert rc == 0, dump_out
    # The header embeds the raw namespace name (auger-pytest-<hex>), which can itself
    # contain "-1" (hex digit 1 after the prefix dash) — strip it before the leak scan.
    dump_body = dump_out.replace(ns, "<ns>")
    assert "-1" not in dump_body, dump_body
    assert "conf n/a" in dump_out, dump_out
    assert "## D-001  (4.05)  conf n/a" in dump_out, dump_out


def test_confidence_sentinel_decisions_are_thin_not_confident(
    decided: dict, monkeypatch
):
    """AC2: an unmeasured decision belongs in `feedback`'s thin list beside the measured-thin
    ones — `0 <= c` must not read the sentinel as a passing grade — and the follow-up it
    produces never quotes the sentinel into its edge note. Ordering stays deterministic:
    measured rows by confidence first, then the unmeasured ones by id."""
    ns, pid = decided["ns"], decided["pid"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "unmeasured",
            "--option",
            "unmeasured",
            "--option",
            "other",
            "--why-not",
            "the other one is worse",
        ]
    )
    assert rc == 0, out
    assert row(ns, "decision", "id=eq.D-003")["confidence"] == pytest.approx(-1.0)

    ids = [d["id"] for d in auger.thin_decisions(ns, pid)]
    assert ids == ["D-002", "D-003"], ids

    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    calls: list = []
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, calls))
    proposer_states: list = []
    scripted = [FOLLOWUP_Q, "Second question?"]

    def proposer_spy(state, *a, **k):
        proposer_states.append(state)
        return scripted.pop(0), ""

    monkeypatch.setattr(auger, "propose_question", proposer_spy)
    rc, out = run_cli(["-n", ns, "feedback", "--budget", "1"])
    assert rc == 0, out
    # The unmeasured decision is IN the thin list, rendered n/a — and the old false claim
    # ("every recorded decision is at or above the threshold") is gone.
    assert "  D-002  0.41  <- thinnest first" in out, out
    assert "  D-003  n/a  <- thinnest first" in out, out
    assert "(none" not in out, out
    # The proposer was fed the state for BOTH decisions; the unmeasured one reads n/a.
    assert any("confidence: 0.41" in s for s in proposer_states), proposer_states
    assert any("confidence: n/a" in s for s in proposer_states), proposer_states
    # Both decisions produced a follow-up (one asked, one budget-thin), and the sentinel
    # never reaches the `opens` edge notes.
    qrows = rows(ns, "question", "qclass=eq.follow_up&order=id.asc")
    assert len(qrows) == 2, qrows
    notes = {e["src_id"]: e["note"] for e in rows(ns, "edge", "kind=eq.opens")}
    assert notes["D-002"].startswith(
        "the confidence in D-002 is 0.41 (< 0.6), so the engine"
    ), notes
    assert notes["D-003"].startswith(
        "the confidence in D-003 is n/a (< 0.6), so the engine"
    ), notes


def test_confidence_sentinel_is_never_a_priority_judgment_below_the_floor(
    project: dict, monkeypatch
):
    """AC4: a MEASURED decision below the 0.30 floor is escalated as a priority judgment; an
    UNMEASURED one is thin and gets drilled, but is never claimed to be "-1.00 below the
    0.30 floor" — a weighing nobody scored is not a weighing that failed one."""
    ns, pid = project["ns"], project["pid"]
    for did, conf in (("D-002", ["--confidence", "0.20"]), ("D-003", [])):
        argv = [
            "-n",
            ns,
            "answer",
            "--id",
            did,
            "--domain",
            "4.06",
            "--chosen",
            f"pick {did}",
            "--option",
            f"pick {did}",
            "--option",
            "other",
            "--why-not",
            "worse",
            *conf,
        ]
        rc, out = run_cli(argv)
        assert rc == 0, out
    monkeypatch.setattr(auger, "recall", recall_hits(ns, pid, "D-002"))
    monkeypatch.setattr(auger, "jev", gate_stub(0.10, []))
    monkeypatch.setattr(
        auger, "propose_question", proposer_stub([FOLLOWUP_Q, "Second question?"], [])
    )
    rc, out = run_cli(["-n", ns, "feedback", "--budget", "2"])
    assert rc == 0, out
    esc = [e["question"] for e in rows(ns, "escalation", "order=id.asc")]
    # The measured row earns its escalation, with its real number.
    assert any(
        "priority judgment: D-002 has confidence 0.20, below the 0.3 floor" in t
        for t in esc
    ), esc
    # The unmeasured row is escalated for NOTHING, and no -1 claim exists on the record.
    assert not any("D-003" in t or "-1" in t for t in esc), esc
    # It is still drilled: thin is thin.
    opens = {e["src_id"] for e in rows(ns, "edge", "kind=eq.opens")}
    assert opens == {"D-002", "D-003"}, opens


def test_confidence_sentinel_is_excluded_from_status_aggregates(project: dict):
    """AC3: the min/mean/max line and the coverage-by-domain means exclude the sentinel —
    averaging a number nobody stated would print 0.22 here instead of 0.70 — while the
    drill list gains the unmeasured decisions as n/a."""
    ns = project["ns"]
    specs = (
        ("D-001", ["--confidence", "0.5"]),
        ("D-002", ["--confidence", "0.9"]),
        ("D-003", []),  # unmeasured: the sentinel
        ("D-004", ["--confidence", "0.7"]),
        ("D-005", []),  # unmeasured, alone in its domain
    )
    for did, extra in specs:
        argv = [
            "-n",
            ns,
            "answer",
            "--id",
            did,
            "--domain",
            "4.06" if did == "D-005" else "4.05",
            "--chosen",
            "x",
            "--option",
            "x",
            "--option",
            "y",
            "--why-not",
            "y loses",
            *extra,
        ]
        rc, out = run_cli(argv)
        assert rc == 0, out
    rc, out = run_cli(["-n", ns, "status"])
    assert rc == 0, out
    # (0.5 + 0.9 + 0.7) / 3 = 0.70 — the sentinels are excluded, not averaged in.
    assert "confidence: min 0.50  mean 0.70  max 0.90" in out, out
    cov = [
        ln
        for ln in out.splitlines()
        if ln.strip().startswith(("4.05", "4.06")) and "terminating ring" not in ln
    ]
    assert [ln.split()[:3] for ln in cov] == [
        ["4.05", "4", "0.70"],
        ["4.06", "1", "n/a"],
    ], cov
    # and the drill list: the unmeasured decisions appear as n/a, never as -1/-1.0.
    # The renderer matches the raw-confidence rendering every other site used (`:g`), so a
    # measured 0.5 reads 0.5 — the site-local `:.2f` is gone with the leak.
    assert "  D-003  n/a  x" in out, out
    assert "  D-005  n/a  x" in out, out
    assert "  D-001  0.5  x" in out, out


def test_confidence_sentinel_renders_n_a_in_ask_seats_and_verdict_list(
    decided: dict, monkeypatch
):
    """The remaining raw interpolations: `ask`'s seats list renders an unmeasured decision as
    conf n/a, and `verdict --list` keeps its dash for a verdict with no score at all (that
    column reads MODEL verdicts, which never carry the decision sentinel)."""
    ns = decided["ns"]
    rc, out = run_cli(
        [
            "-n",
            ns,
            "answer",
            "--id",
            "D-003",
            "--domain",
            "4.07",
            "--chosen",
            "unmeasured",
            "--option",
            "unmeasured",
            "--option",
            "other",
            "--why-not",
            "the other one is worse",
        ]
    )
    assert rc == 0, out

    seats_states: list = []

    def seats_jev_spy(state, questions, *a, **k):
        seats_states.append(state)
        return ask_stub("how_is_it_tested", 0.73, 0.10)(state, questions, *a, **k)

    monkeypatch.setattr(auger, "jev", seats_jev_spy)
    rc, out = run_cli(["-n", ns, "ask"])
    assert rc == 0, out
    # The seats list is the state `ask` FEEDS the model — assert it there, where the sentinel
    # would reach a consumer (the seats have no other stdout render to leak through).
    assert any(
        "- D-003 (conf n/a): unmeasured" in c
        and "- D-002 (conf 0.41): staging table" in c
        for c in seats_states
    ), seats_states

    rc, out = run_cli(["-n", ns, "verdict", "--good", "D-001=x", "--reasons", "r"])
    assert rc == 0, out
    rc, out = run_cli(["-n", ns, "verdict", "--list"])
    assert rc == 0, out
    assert "conf —" in out, out
    assert "-1" not in out, out
