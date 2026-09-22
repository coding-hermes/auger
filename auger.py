#!/usr/bin/env python3
"""
auger — spec drilling for projects, backed by DuckBrain.

The loop: ask → answer → check → drill deeper. Every answer is a decision with a
reason, its rejected alternatives, and a calibrated confidence. DuckBrain holds the
rows; JEV (a decisions model) supplies the confidence and the "what next" pick;
DuckBrain's own embedding index supplies "have we already covered this?".

Substrate (verified live 2026-09-20, see docs/SUBSTRATE-VERIFICATION.md):
  DuckBrain  http://127.0.0.1:3000
    POST /api/namespaces                       create a namespace
    GET  /api/ns/<ns>/tables                   list declared tables
    GET  /api/ns/<ns>/tables/<t>?col=eq.v      PostgREST select (eq/ne/gt/gte/lt/lte/like/in)
    POST /api/ns/<ns>/tables/<t>               insert (object | array | NDJSON)
    PATCH /api/ns/<ns>/tables/<t>?pk=eq.v      update by primary key   <- the toggle
    POST /api/memories {key,domain,content}    write + embed
    GET  /api/memories?namespace=<ns>&q=...    semantic search (cosine + snippets)
  JEV  POST https://openrouter.ai/api/alpha/decisions  {state, questions{noul|choice|score}}

Hard-won rules encoded here:
  * JSON payloads go to the HTTP layer as BYTES FROM A FILE, never shell-interpolated
    (an apostrophe in "seed's rule" silently truncates a quoted curl body).
  * JEV is fail-closed: any transport error or malformed answer becomes an explicit
    UNKNOWN, never a silent optimistic default.
  * JEV key sets contain expired keys; failover across all of them, in order.
  * Thresholds live in THIS FILE, not in the model.
  * A 429 is BACKPRESSURE, not a failure: the transport retries it inline, bounded, honouring
    the server's own retryAfter / Retry-After. The destructive calls carry the larger budget,
    because giving up on a namespace DELETE leaks a registry row instead of losing a write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DB_URL = os.environ.get("DUCKBRAIN_URL", "http://127.0.0.1:3000")
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# ---------------------------------------------------------------- thresholds (code, not model)
T_ANSWERED = 0.55      # noul >= this  -> the question is already answered by stored evidence
T_CONFIDENT = 0.60     # decision confidence below this is surfaced as "needs drilling"
T_SUBJECT = 0.45       # choice confidence below this -> we do not trust the "next subject" pick
T_PRIORITY = 0.30      # decision confidence below this -> a PRIORITY JUDGMENT, which the skill
                       # reserves for the human. The engine records an escalation with a default
                       # and CONTINUES ON THE DEFAULT: it never blocks waiting for a person
                       # (SPEC-001 section 7, Q5; docs/ENGINE.md open question 5).

# The budget governor. The ceiling is a NUMBER OF QUESTIONS ASKED BY ONE RUN, and it is enforced
# by the program rather than trusted to an agent — that refusal is the whole reason this is
# software (docs/ENGINE.md E4: "the budget is enforced by the program, not the agent").
FEEDBACK_BUDGET = 3
BUDGET_ENV = "AUGER_QUESTION_BUDGET"

TOKEN_PATHS = [
    os.path.expanduser("~/.duckbrain/foreman-status.token"),
    os.path.expanduser("~/.duckbrain/token"),
]

# ---------------------------------------------------------------- the SDM data model
# One table per register of the spec-decomposition-matrix method. Declared to DuckBrain
# as namespaces/<ns>/tables/<name>.table.json  (DB-SUPA-3 registry).
COLS = {
    "project":     [("id","varchar"),("name","varchar"),("seed","varchar"),("core_statement","varchar"),
                    ("status","varchar"),("created_at","timestamp")],
    "question":    [("id","varchar"),("project_id","varchar"),("domain","varchar"),("text","varchar"),
                    ("ring","integer"),("qclass","varchar"),("status","varchar"),
                    ("jev_already_answered","double"),("jev_checked_at","varchar")],
    "decision":    [("id","varchar"),("project_id","varchar"),("domain","varchar"),("question_id","varchar"),
                    ("chosen","varchar"),("why_not","varchar"),("reversal_cost","varchar"),
                    ("confidence","double"),("status","varchar"),("evidence_key","varchar"),
                    # AUG-009 / SPEC-002 section 1: what the decision BINDS. project | bundle,
                    # empty = project — the value every row written before this column means.
                    ("scope","varchar")],
    "option":      [("id","varchar"),("decision_id","varchar"),("label","varchar"),("costs","varchar"),
                    ("breaks","varchar"),("active","boolean")],
    "break":       [("id","varchar"),("decision_id","varchar"),("breaks_what","varchar"),
                    ("consequence","varchar"),("applied","boolean")],
    "escalation":  [("id","varchar"),("project_id","varchar"),("question","varchar"),("options","varchar"),
                    ("default_action","varchar"),("risk","varchar"),("status","varchar")],
    "assumption":  [("id","varchar"),("project_id","varchar"),("text","varchar"),("falsifier","varchar"),
                    ("monitoring","varchar")],
    "unknown":     [("id","varchar"),("project_id","varchar"),("text","varchar"),("owner","varchar"),
                    ("trigger","varchar"),("containment","varchar")],
    "domain":      [("id","varchar"),("project_id","varchar"),("num","varchar"),("name","varchar"),
                    ("triage","integer"),("ring_floor","integer"),("terminating_ring","integer"),
                    ("status","varchar"),("owner","varchar"),("trigger","varchar"),("containment","varchar")],
    # --- the graph: the relationships the engine reasons over, not the nodes it stores (SPEC-001 2/3)
    "edge":        [("id","varchar"),("project_id","varchar"),("kind","varchar"),
                    ("src_kind","varchar"),("src_id","varchar"),("dst_kind","varchar"),("dst_id","varchar"),
                    # AUG-010 / SPEC-002 sections 2.1-2.2: which PROJECT an endpoint belongs to when
                    # that endpoint lives in a SIBLING's namespace — a bundle lets the gate link a
                    # sibling's answer and the impact pass point at a sibling's decision. Empty means
                    # "the endpoint is this namespace's", which is what every row written before this
                    # field means. Both travel as ABSENT KEYS when empty: see insert_edge.
                    ("src_project","varchar"),("dst_project","varchar"),
                    ("confidence","double"),("source","varchar"),("note","varchar"),
                    ("created_at","timestamp")],
    "facet":       [("id","varchar"),("project_id","varchar"),("question_id","varchar"),("facet","varchar"),
                    ("status","varchar"),("closed_by","varchar"),("note","varchar")],
    # --- the bundles: which projects must honour one contract (SPEC-002 section 1). Membership is
    # a TABLE and not a column, because a project can belong to several bundles at once.
    "bundle":      [("id","varchar"),("name","varchar"),("description","varchar"),
                    ("contract","varchar"),("status","varchar")],
    "bundle_member":[("id","varchar"),("bundle_id","varchar"),("project","varchar"),
                     ("role","varchar"),("note","varchar")],
}
PRIMARY = {t: "id" for t in COLS}

# ---------------------------------------------------------------- the graph's closed sets
# Edge kinds and sources are sets the ENGINE switches on, so they live in code. The facet set is
# the deliberate exception — SPEC-001 section 3 makes it DATA so a project can extend it.
EDGE_KINDS = ("opens", "closes", "satisfies", "affects", "breaks",
              "derives_from", "references", "blocks")
EDGE_SOURCES = ("gate", "impact", "human", "rule")
# The bundle model's closed sets (SPEC-002 section 1). `status` and `role` are sets the ENGINE
# switches on — a retired bundle is not walked, a test-target does not own the contract — so they
# live in code and are refused BY NAME when they are wrong: EDGE_KINDS' rule, applied to the two
# columns that carry a decision about behaviour rather than a label.
BUNDLE_STATUSES = ("proposed", "confirmed", "retired")
BUNDLE_ROLES = ("owner", "consumer", "test-target")
# What a decision BINDS (SPEC-002 section 1). Empty/absent means DEFAULT_SCOPE, which is why the
# default is a named constant: no reader re-derives it from a literal.
DECISION_SCOPES = ("project", "bundle")
DEFAULT_SCOPE = "project"
# src_kind/dst_kind -> the table that must ALREADY hold that id (this is the referential check).
NODE_TABLES = {"decision": "decision", "question": "question",
               "unknown": "unknown", "assumption": "assumption"}
# The DEFAULT facet set, not the only one: a project may extend it (SPEC-001 section 3, "the facet
# set is data, not code, so a project can extend it"). This is the INITIAL set; read it through
# facet_set() so an extension mechanism has exactly one place to land rather than a code path
# that hardcodes six strings no project can add to.
DEFAULT_FACETS = ("data", "failure", "ownership", "cost", "test", "who_else")
FACET_STATUSES = ("open", "closed")
ID_WIDTH = 6           # E-000001, F-000001 — fixed width, per the project's own id law


def facet_set() -> tuple:
    """The facets every new question gets one row per (SPEC-001 section 3)."""
    return DEFAULT_FACETS


def ns_dir(ns: str) -> str:
    return os.path.join(os.path.expanduser("~"), "duckbrain", "namespaces", ns)


# ---------------------------------------------------------------- the 429 budget
# DuckBrain's HTTP API is token-bucketed (measured: 600 requests/minute per IP) and answers
# 429 with the wait in the body (`retryAfter`) and/or in the `Retry-After` header. A 429 is
# BACKPRESSURE, not a failure, so `_req` absorbs it inline before any caller sees a status.
# The budget is per CALL, and it is deliberately not one number: giving up on a read costs a
# retry, while giving up on a namespace DELETE leaves a registry row behind for a namespace
# whose directory the caller is about to remove.
RETRIES = 4             # an ordinary call
TEARDOWN_RETRIES = 8    # a destructive call, where a give-up leaks state instead of losing work


# ---------------------------------------------------------------- transport
def _token() -> str:
    t = os.environ.get("DUCKBRAIN_API_KEY")
    if t:
        return t.strip()
    for p in TOKEN_PATHS:
        try:
            with open(p) as fh:
                v = fh.read().strip()
                if v:
                    return v
        except OSError:
            continue
    raise SystemExit("no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token")


def _req(url: str, method: str = "GET", body: dict | list | None = None,
         headers: dict | None = None, timeout: int = 45, retries: int = RETRIES):
    """Send JSON as BYTES. Never build a request body by string interpolation.

    A 429 is BACKPRESSURE, not a failure (proven: the pytest suite bursts enough
    calls to trip DuckBrain's limiter, and an unretried 429 loses the write). It
    is retried inline, honouring the server's own Retry-After, before the caller
    ever sees a status. A test suite that flakes on the substrate's rate limiter
    is a test suite nobody trusts.

    `retries` is the BOUND: one try plus at most this many retries, then the last
    429 is returned to the caller like any other status. A substrate that never
    recovers must not hang its caller forever, which is why there is no
    unbounded loop here.
    """
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                try:
                    return r.status, json.loads(raw) if raw else {}, dict(r.headers)
                except ValueError:
                    return r.status, raw, dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            if e.code == 429 and attempt < retries:
                wait = 1.0
                try:
                    wait = float(json.loads(raw).get("retryAfter", 1) or 1)
                except (ValueError, AttributeError):
                    pass
                hdr_wait = e.headers.get("Retry-After") if e.headers else None
                if hdr_wait:
                    try:
                        wait = max(wait, float(hdr_wait))
                    except ValueError:
                        pass
                attempt += 1
                time.sleep(min(wait, 10.0) * attempt)   # bounded linear backoff
                continue
            try:
                return e.code, json.loads(raw), dict(e.headers)
            except ValueError:
                return e.code, raw, dict(e.headers)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": f"transport: {e}"}, {}


def db(path: str, method: str = "GET", body=None, timeout: int = 45, retries: int = RETRIES):
    return _req(f"{DB_URL}{path}", method, body, {"x-api-key": _token()}, timeout, retries)


def tbl(ns: str, table: str, query: str = "") -> str:
    return f"/api/ns/{ns}/tables/{table}{('?' + query) if query else ''}"


def select(ns: str, table: str, query: str = "") -> list:
    st, body, _ = db(tbl(ns, table, query))
    if st != 200:
        raise SystemExit(f"select {table} failed ({st}): {body}")
    return body if isinstance(body, list) else []


def select_or_empty(ns: str, table: str, query: str = "") -> list:
    """`select`, but a namespace or table that cannot answer returns NOTHING instead of exiting.

    The bundle reads need this and they are why it exists. A SIBLING's namespace is foreign — it may
    not exist, may not have the tables declared, or may be unreachable — and a namespace declared
    before the bundle tables existed (AUG-009) serves a 400 for them. Every one of those means "no
    rows here", never a crash in the middle of the asking project's own verb and never a row invented
    to fill the gap. The asking project's OWN namespace keeps the loud `select`: a broken read there
    is a defect, not a neighbourly absence.
    """
    st, body, _ = db(tbl(ns, table, query))
    return body if st == 200 and isinstance(body, list) else []


def insert(ns: str, table: str, rows) -> dict:
    st, body, _ = db(tbl(ns, table), "POST", rows if isinstance(rows, list) else [rows])
    if st not in (200, 201):
        raise SystemExit(f"insert {table} failed ({st}): {body}")
    return body


def patch(ns: str, table: str, pk: str, values: dict) -> dict:
    st, body, _ = db(tbl(ns, table, f"pk=eq.{pk}"), "PATCH", values)
    if st != 200:
        raise SystemExit(f"patch {table} failed ({st}): {body}")
    return body


def remember(ns: str, key: str, content: str, domain: str = "concept") -> dict:
    st, body, _ = db("/api/memories", "POST",
                     {"key": key, "namespace": ns, "domain": domain, "content": content})
    if st not in (200, 201):
        raise SystemExit(f"remember {key} failed ({st}): {body}")
    return body


def delete_namespace(ns: str, retries: int = TEARDOWN_RETRIES) -> tuple[int, object]:
    """DELETE a namespace over the retrying transport. Returns (status, body).

    404 is NOT an error here: the desired end state is "gone", and a namespace that is
    already gone has reached it. Anything else is the caller's to report — this returns the
    status instead of raising, because the teardown path must go on and remove the directory
    rather than leave both paths half-done.

    Why the deletion has its own function, with its own budget, instead of an inline
    `db(...)` at the call site: it is the one call in the teardown path whose FAILURE LEAVES
    STATE BEHIND. A 429 on this DELETE aborts nothing visible — the registry row simply
    survives while the directory is removed — and the next reader then sees a namespace that
    does not exist (observed live: stale test entries in /api/namespaces whose directories
    were already gone, which is exactly the leak this function exists to prevent). So it runs
    on the same 429-retrying transport as every other call, honouring the server's own
    retryAfter / Retry-After, and it carries a LARGER bounded budget than an ordinary call:
    a read that gives up costs one retry, a delete that gives up leaks a row.
    """
    st, body, _ = db(f"/api/namespaces/{ns}", "DELETE", {"confirm": True}, retries=retries)
    return st, body


# ---------------------------------------------------------------- the graph write path
# DuckBrain stores what it is sent — it enforces nothing — so referential integrity is OURS.
# An edge is a claim about two nodes; an edge whose endpoint does not exist is not a claim about
# anything, and a silently stored orphan is worse than a refusal because it reads as evidence.
def next_id(ns: str, table: str, prefix: str) -> str:
    """The next fixed-width id for a table, derived from the HIGHEST existing id.

    Not from a row count: a count collides the moment a row is deleted (the classic bug this
    deliberately does not have). An empty table yields the first id, PREFIX-000001.
    """
    rows = select(ns, table, "select=id&order=id.desc&limit=1")
    if not rows or not rows[0].get("id"):
        return f"{prefix}-{'0' * (ID_WIDTH - 1)}1"
    m = re.search(r"(\d+)\s*$", str(rows[0]["id"]))
    return f"{prefix}-{int(m.group(1)) + 1:0{ID_WIDTH}d}" if m else f"{prefix}-{'0' * (ID_WIDTH - 1)}1"


def node_exists(ns: str, kind: str, node_id: str) -> bool:
    """Does a node of this kind actually exist? Unknown kinds are a hard error, not a pass."""
    table = NODE_TABLES.get(kind)
    if table is None:
        raise SystemExit(f"unknown node kind {kind!r}: expected one of {', '.join(sorted(NODE_TABLES))}")
    if not node_id:
        return False
    return bool(select(ns, table, f"id=eq.{node_id}&select=id&limit=1"))


def node_exists_in(ns: str, kind: str, node_id: str) -> bool:
    """`node_exists` against a namespace that may not exist — the SIBLING case of the same check.

    An edge is stored in ONE namespace, but its endpoint can live in another project's (SPEC-002
    2.1/2.2), so the referential check is made where that project's rows actually are. An unknown
    kind is still a hard error; a foreign namespace that cannot answer reads as "the node is not
    there", which REFUSES the edge rather than storing a claim about a row nobody can read.
    """
    if kind not in NODE_TABLES:
        raise SystemExit(f"unknown node kind {kind!r}: expected one of {', '.join(sorted(NODE_TABLES))}")
    if not node_id:
        return False
    return bool(select_or_empty(ns, NODE_TABLES[kind], f"id=eq.{node_id}&select=id&limit=1"))


def edge(ns: str, project_id: str, kind: str, src_kind: str, src_id: str,
         dst_kind: str, dst_id: str, *, confidence: float | None = None,
         source: str = "human", note: str = "", src_project: str = "", dst_project: str = "",
         warnings: list | None = None) -> dict:
    """Write one edge, refusing any edge whose endpoints are not already stored.

    `source` has no default-by-omission: an edge is born of a model gate, an impact pass, a
    person, or a rule, and an empty string would let a reader mistake an unknown origin for a
    recorded one. `confidence` is -1 — the spec'd "asserted directly, not scored by a model"
    value — whenever no model confidence is supplied.

    `src_project` / `dst_project` name the project an endpoint belongs to when that endpoint lives
    in a SIBLING's namespace (SPEC-002 sections 2.1-2.2: the gate links a sibling's answer, the
    impact pass points at a sibling's decision). Two consequences, both of them why this is a FIELD
    and not a sentence in `note`:

      * the referential check runs where that project's rows are, so a cross-namespace edge is
        VERIFIED instead of refused;
      * ids are per NAMESPACE — a sibling's D-007 and this project's D-007 are different decisions —
        so a row that did not name the project would read as a local link between two rows of this
        namespace, and the rule walk would treat a sibling's break as a local decision dying.

    Both travel as ABSENT KEYS when empty, so every edge written before this field sends exactly the
    payload it always sent (the same reason insert_decision gives: a namespace whose declaration
    predates a column refuses the key). `warnings` is an out-parameter for the insert's own degraded
    path, which is the one thing a caller must not lose.
    """
    if kind not in EDGE_KINDS:
        raise SystemExit(f"unknown edge kind {kind!r}: expected one of {', '.join(EDGE_KINDS)}")
    if source not in EDGE_SOURCES:
        raise SystemExit(f"unknown edge source {source!r}: expected one of {', '.join(EDGE_SOURCES)}")
    for role, k, i, project in (("src", src_kind, src_id, src_project),
                                ("dst", dst_kind, dst_id, dst_project)):
        where = member_namespace(project) if project else ns
        ok = node_exists(where, k, i) if where == ns else node_exists_in(where, k, i)
        if not ok:
            raise SystemExit(f"refused: {role} {k} {i!r} does not exist in table "
                             f"{NODE_TABLES.get(k, '?')}"
                             + (f" of project {project!r}" if project else "")
                             + " — an edge to a missing node is not stored")
    row = {"id": next_id(ns, "edge", "E"), "project_id": project_id, "kind": kind,
           "src_kind": src_kind, "src_id": src_id, "dst_kind": dst_kind, "dst_id": dst_id,
           "confidence": float(confidence) if confidence is not None else -1.0,
           "source": source, "note": note, "created_at": datetime.now(timezone.utc).isoformat()}
    for key, val in (("src_project", src_project), ("dst_project", dst_project)):
        if val:
            row[key] = val
    warning = insert_edge(ns, row)
    if warning and warnings is not None:
        warnings.append(warning)
    return row


UNDECLARED_PROJECT_COL = re.compile(r"unknown column\(s\).*?\b(?:src_project|dst_project)\b", re.I)


def insert_edge(ns: str, row: dict) -> str:
    """Insert an edge row, never losing a cross-project endpoint SILENTLY. Returns a WARNING.

    "" when nothing was downgraded. `insert` raises on a refusal, and that is right for almost every
    edge — but a namespace whose `edge` declaration predates `src_project` / `dst_project` cannot
    hold a cross-namespace link, and DuckBrain's declared-table registry caches a namespace's
    declarations IN PROCESS (the rule insert_decision states at length): the payload every existing
    namespace accepts has to stay available. So the specific 400 that names those columns retries
    without them, marks the row's own `note` with what was dropped, and hands the remedy back for the
    caller to print. The edge is still a true claim — a cross-project link recorded one field short
    of the ideal, saying so where it is stored.
    """
    st, body, _ = db(tbl(ns, "edge"), "POST", [row])
    if st in (200, 201):
        return ""
    if st == 400 and UNDECLARED_PROJECT_COL.search(json.dumps(body)):
        dropped = [k for k in ("src_project", "dst_project") if k in row]
        keep = {k: v for k, v in row.items() if k not in ("src_project", "dst_project")}
        keep["note"] = ((row.get("note") or "").rstrip()
                        + f" [stored without {'/'.join(dropped)}: this namespace's `edge` declaration "
                          f"predates them, so the project this edge crosses to is named here only]").strip()
        st2, body2, _ = db(tbl(ns, "edge"), "POST", [keep])
        if st2 in (200, 201):
            return (f"WARNING: this namespace's `edge` declaration predates {' and '.join(dropped)}, so "
                    f"{row['id']} is stored WITHOUT the project its cross-namespace endpoint belongs to "
                    f"(the note says so). Remedy: run `auger init` and restart the DuckBrain API so the "
                    f"declaration is re-read.")
        raise SystemExit(f"insert edge failed ({st2}): {body2}")
    raise SystemExit(f"insert edge failed ({st}): {body}")


def facet(ns: str, project_id: str, question_id: str, name: str, *, status: str = "open",
          closed_by: str = "", note: str = "") -> dict:
    """Write one facet row. The facet name is a label, not a gate — the set is extensible data."""
    if status not in FACET_STATUSES:
        raise SystemExit(f"unknown facet status {status!r}: expected one of {', '.join(FACET_STATUSES)}")
    if not node_exists(ns, "question", question_id):
        raise SystemExit(f"refused: question {question_id!r} does not exist — facet not stored")
    row = {"id": next_id(ns, "facet", "F"), "project_id": project_id, "question_id": question_id,
           "facet": name, "status": status, "closed_by": closed_by, "note": note}
    insert(ns, "facet", row)
    return row


# ---------------------------------------------------------------- the fleet a member must name
# A bundle member names a PROJECT, and the projects are the SCHEDULER's, not auger's: auger drills
# one namespace and keeps no project registry of its own. So membership is verified against the
# fleet's own table — READ-ONLY — and a member that names nothing the fleet has is refused. Same
# rule as `edge`: a stored row that names something that does not exist reads as evidence.
#
# READ-ONLY, always (mode=ro): a write here would land in the live scheduler's DB. And the DB is
# NOT required to run auger — on a host without one (a CI runner) membership cannot be verified,
# so it is refused with a message that says exactly that, never a raw sqlite3 exception.
SCHEDULER_DB_ENV = "AUGER_SCHEDULER_DB"
SCHEDULER_DB_CANDIDATES = (
    os.path.expanduser("~/coding-hermes-scheduler/coding-herms-scheduler/scheduler.db"),
    os.path.expanduser("~/.hermes/coding-hermes/scheduler.db"),
)


def _scheduler_projects(path: str) -> set | None:
    """The `projects` names in a scheduler DB read READ-ONLY — None when it cannot answer.

    None is "this DB cannot tell us", and it covers both a file that is not a scheduler DB (the
    repo-local path is a 0-byte file on the fleet host: an ABSENT answer, not an empty one) and a
    file that cannot be opened at all.
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        have = con.execute("select 1 from sqlite_master where type='table' and name='projects'").fetchone()
        if not have:
            return None
        return {str(r[0]) for r in con.execute("select name from projects")}
    except sqlite3.Error:
        return None
    finally:
        con.close()


def scheduler_db_path() -> str | None:
    """The fleet's scheduler DB, or None when this host has none that can answer.

    The env override is EXCLUSIVE when set: a caller that names a DB must not be answered from a
    different one. Otherwise the first candidate whose `projects` table can actually be read wins.
    """
    override = os.environ.get(SCHEDULER_DB_ENV)
    candidates = [os.path.expanduser(override)] if override else list(SCHEDULER_DB_CANDIDATES)
    for path in candidates:
        if path and os.path.exists(path) and _scheduler_projects(path) is not None:
            return path
    return None


def known_projects() -> set | None:
    """Every project the fleet has, or None when no scheduler DB can be read.

    An EMPTY SET is a real answer (the fleet has no such project); None is "unverifiable". The two
    lead to different messages, and neither is allowed to become a silently stored member.
    """
    path = scheduler_db_path()
    return None if path is None else _scheduler_projects(path)


def bundle(ns: str, name: str, *, description: str = "", contract: str = "",
           status: str = "proposed", bundle_id: str = "") -> dict:
    """Write one bundle row: projects that must honour one contract (SPEC-002 section 1).

    `status` is a closed set the engine switches on, so an unknown one is refused by name rather
    than stored. `contract` points at where the spanning spec lives TODAY (a path), or is empty
    when the bundle is real in the code and unwritten on paper — the honest shape of a bundle
    whose members share a contract nobody has written down.
    """
    if status not in BUNDLE_STATUSES:
        raise SystemExit(f"unknown bundle status {status!r}: expected one of {', '.join(BUNDLE_STATUSES)}")
    if not name:
        raise SystemExit("refused: a bundle needs a name — the row is the only place its members are readable")
    row = {"id": bundle_id or next_id(ns, "bundle", "B"), "name": name, "description": description,
           "contract": contract, "status": status}
    insert(ns, "bundle", row)
    return row


def bundle_exists(ns: str, bundle_id: str) -> bool:
    """Does this bundle row actually exist? A member of a bundle that does not is not stored."""
    if not bundle_id:
        return False
    return bool(select(ns, "bundle", f"id=eq.{bundle_id}&select=id&limit=1"))


def bundle_member(ns: str, bundle_id: str, project: str, role: str, *, note: str = "") -> dict:
    """Write one membership row: what a project owes or expects inside a bundle.

    Membership is a TABLE and not a column because a project can belong to several bundles
    (SPEC-002 section 1). Three refusals, all of them the rule `edge` already enforces on its
    endpoints — a row naming something that does not exist reads as a fact nobody can check:

      * `role` must be in the closed set the engine may switch on;
      * the bundle must already exist;
      * the project must be one the FLEET has (the scheduler's `projects` table, read-only). Where
        no scheduler DB can be read at all, membership cannot be verified and is refused rather
        than guessed.

    A second row for the same (bundle, project) is the same membership written twice, so it is
    refused too: the walk in SPEC-002 section 2 counts members, and a doubled member is a doubled
    blast radius.
    """
    if role not in BUNDLE_ROLES:
        raise SystemExit(f"unknown bundle role {role!r}: expected one of {', '.join(BUNDLE_ROLES)}")
    if not bundle_exists(ns, bundle_id):
        raise SystemExit(f"refused: bundle {bundle_id!r} does not exist — a member row for a "
                         f"missing bundle is not stored")
    projects = known_projects()
    if projects is None:
        override = os.environ.get(SCHEDULER_DB_ENV)
        looked = [os.path.expanduser(override)] if override else list(SCHEDULER_DB_CANDIDATES)
        raise SystemExit(f"refused: cannot verify project {project!r} — no readable scheduler DB "
                         f"(looked in {', '.join(looked)}). A bundle member names a project the "
                         f"fleet has, so membership is refused rather than guessed; point "
                         f"{SCHEDULER_DB_ENV} at a scheduler.db with a `projects` table.")
    if project not in projects:
        raise SystemExit(f"refused: project {project!r} has no row in the fleet's scheduler "
                         f"projects table ({scheduler_db_path()}) — a bundle member names a "
                         f"project the fleet has, and this name is not one")
    have = select(ns, "bundle_member",
                  f"bundle_id=eq.{bundle_id}&project=eq.{project}&select=id&limit=1")
    if have:
        raise SystemExit(f"refused: {project!r} is already a member of {bundle_id} (row "
                         f"{have[0]['id']}) — a second row is the same membership written twice")
    row = {"id": next_id(ns, "bundle_member", "BM"), "bundle_id": bundle_id, "project": project,
           "role": role, "note": note}
    insert(ns, "bundle_member", row)
    return row


def recall(ns: str, q: str, limit: int = 5) -> list:
    from urllib.parse import quote
    st, body, _ = db(f"/api/memories?namespace={ns}&q={quote(q)}&limit={limit}", timeout=60)
    if st != 200:
        return []
    return body.get("items", []) if isinstance(body, dict) else []


# ---------------------------------------------------------------- the bundle walk (SPEC-002 section 2)
# What a bundle makes REACHABLE. These readers are the only places a project name is turned into the
# namespace its rows live in, and that mapping is a convention rather than a fact auger stores: a
# project is ADDRESSED by the namespace it is pointed at (`auger init -n bunker`), and `start`
# defaults the project's own `name` to that namespace — so a member's name IS its namespace. It is
# one function so the convention has exactly one place to change.
#
# Membership is by project NAME because that is what the fleet's own table carries: bundle_member()
# verifies every row against the scheduler's `projects` table. Every read here goes through
# select_or_empty: a namespace declared before the bundle tables (AUG-009) or a sibling that does not
# exist has NO memberships, and that is not a reason to kill the asking project's verb.
def member_namespace(project: str) -> str:
    """The DuckBrain namespace a bundle MEMBER's rows live in — the project's own name."""
    return project


def project_name(ns: str, project_id: str) -> str:
    """The NAME of the project a pid belongs to in this namespace — "" when there is no such row.

    Deliberately not `_project`, which exits: this is read on paths (the gate, the impact pass) where
    a missing name must leave the verb running, because "no bundle membership is knowable" is a gap
    to report rather than a reason to fail the answer.
    """
    found = select_or_empty(ns, "project", f"id=eq.{project_id}&select=id,name&limit=1")
    return (found[0].get("name") or "") if found else ""


def bundles_of(ns: str, project: str) -> list:
    """The bundles this project is a member of, in id order — RETIRED ones excluded.

    A retired bundle's contract no longer binds anyone, so it is not walked: `status` is a closed set
    the engine switches on (BUNDLE_STATUSES), and this is where that switch is thrown.
    """
    if not project:
        return []
    ids: list = []
    for m in select_or_empty(ns, "bundle_member", f"project=eq.{project}&order=id.asc"):
        if m.get("bundle_id") and m["bundle_id"] not in ids:
            ids.append(m["bundle_id"])
    out = []
    for bid in ids:
        found = select_or_empty(ns, "bundle", f"id=eq.{bid}&select=id,name,status&limit=1")
        if found and found[0].get("status") != "retired":
            out.append(found[0])
    return out


def bundle_siblings(ns: str, project: str) -> list:
    """The OTHER member projects sharing an ACTIVE bundle with this one (SPEC-002 2.1/2.2).

    A project in several bundles is walked ONCE: the walk is over projects, and a doubled entry is a
    doubled blast radius — the rule bundle_member() already applies to its own rows. Ordered by
    bundle id and then by membership id, so two runs of the same bundle walk it the same way.
    """
    siblings: list = []
    for b in bundles_of(ns, project):
        for m in select_or_empty(ns, "bundle_member", f"bundle_id=eq.{b['id']}&order=id.asc"):
            name = m.get("project") or ""
            if name and name != project and name not in siblings:
                siblings.append(name)
    return siblings


def bundle_namespaces(ns: str, project: str) -> list:
    """[(namespace, project)] for the siblings to search — the HOME namespace is not in it.

    The home namespace is searched first and separately (its evidence is the project's own), so it is
    excluded here; a sibling whose namespace resolves to the home one is dropped rather than searched
    twice.
    """
    out: list = []
    for name in bundle_siblings(ns, project):
        where = member_namespace(name)
        if where and where != ns and (where, name) not in out:
            out.append((where, name))
    return out


def recall_many(places: list, q: str, limit: int = 5) -> list:
    """Retrieve from several namespaces; every hit comes back tagged with its namespace.

    Returns [(namespace, hit)] in the order the namespaces were given, each namespace's hits in the
    substrate's own order. SEQUENTIAL on purpose (SPEC-002 section 2.1's `recall_many`): DuckBrain's
    API is token-bucketed, so fanning out spends the same budget with more ways to fail, and a
    namespace that returns nothing — a sibling that does not exist, or holds no evidence — must
    contribute NOTHING rather than an invented neighbour.
    """
    out: list = []
    for where in places:
        for hit in recall(where, q, limit=limit):
            out.append((where, hit))
    return out


# ---------------------------------------------------------------- JEV
def _jev_keys() -> list:
    keys = []
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        with open(p, errors="replace") as fh:
            keys += re.findall(r"sk-or-v1-[A-Za-z0-9_-]{20,}", fh.read())
    for name in ("OPENROUTER_API_KEY", "OR_API_KEY"):
        if os.environ.get(name):
            keys.insert(0, os.environ[name])
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def jev(state: str, questions: dict):
    """Ask JEV. All questions ride ONE request for one flat cost. Fail-closed."""
    keys = _jev_keys()
    if not keys:
        return None, "no OpenRouter key found for JEV"
    last = "no attempt"
    for k in keys:
        st, body, _ = _req(JEV_URL, "POST", {"model": JEV_MODEL, "state": state, "questions": questions},
                           {"Authorization": f"Bearer {k}"}, timeout=90)
        if st == 200 and isinstance(body, dict) and "answers" in body:
            return body, None
        last = f"HTTP {st}: {str(body)[:180]}"
    return None, f"all {len(keys)} JEV keys failed; last: {last}"


def _noul(ans: dict, name: str):
    v = (ans.get(name) or {}).get("noul")
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------- the question PROPOSER (Q2)
# SPEC-001 BEAT 1 leaves exactly one thing open that the feedback engine needs: who PROPOSES the
# candidate questions. The recorded leaning — "large model proposes, JEV gates and ranks"
# (SPEC-001 section 7 Q2, docs/ENGINE.md open question 2) — is taken here as the working default.
#
# Why this is a SECOND model transport rather than another JEV question, which would be cheaper:
# JEV is a DECISIONS model. Its whole value is a calibrated noul/choice/score over a state it is
# given, and it does not write prose. Generation and judgement are different skills, and the
# split is the point: the large model generates ONE question, and JEV then GATES and RANKS it
# (see gate_question and feedback). The key set is the same one `jev` uses — one place to rotate.
#
# FAIL-CLOSED, like every other model call in this module: an unreachable proposer returns an
# error and NO question. A fabricated or half-parsed question is worse than a missing one,
# because the engine would ask it, answer it, and record it as a real branch.
PROPOSER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROPOSER_MODEL = "deepseek/deepseek-v3.2"
PROPOSER_ENV = "AUGER_PROPOSER_MODEL"
PROPOSER_SYSTEM = (
    "You propose the next question in a spec-drilling session. Reply with ONE question and "
    "nothing else: a single sentence ending in a question mark, no preamble, no numbering, no "
    "quotes, no explanation. It must be answerable for THIS project and must attack the specific "
    "decision whose confidence is too low to build on — not a general question about the subject. "
    "Never repeat a question that is already open.")
QUESTION_MAX = 400     # a proposed question longer than this is a document, not a question
#: A chat reply arrives with list markers, numbering or a "Question:" label often enough that
#: stripping them is part of parsing rather than a courtesy. The group REPEATS, because the shapes
#: stack: `1. "What drains the table?"` is numbering AND a quote before the question.
REPLY_PREFIX = re.compile(r"^(?:[-*•]|\d+\s*[.)]|q\d*\s*[:.)]|[\s\"'])+", re.I)


def proposer_model() -> str:
    """The model that proposes questions — env-overridable, resolved at CALL time.

    Read per call and not at import: a module constant captured at import cannot be changed by a
    caller (or a test) without re-importing, and the model is exactly the knob a run wants to set.
    """
    return (os.environ.get(PROPOSER_ENV) or "").strip() or PROPOSER_MODEL


def question_from_reply(resp) -> str:
    """The one question inside a chat completion — "" when the reply does not contain one.

    Every shape that is NOT a question reads as "": a missing/blank message, a reply with no
    question mark in it (an answer, a refusal, a preamble), and anything past the first non-empty
    line (a model that ignored "ONE question" has not proposed one).
    """
    if not isinstance(resp, dict):
        return ""
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = ((first.get("message") or {}).get("content") or "")
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        line = REPLY_PREFIX.sub("", raw.strip()).strip().rstrip('"').strip()
        if not line:
            continue
        return line[:QUESTION_MAX] if "?" in line else ""
    return ""


def propose_question(state: str) -> tuple[str, str]:
    """Ask the large model for ONE follow-up question. Returns (text, err) — never both.

    Failover across the whole key set, in order, exactly as `jev` does (the set contains expired
    keys). The first key that answers 200 with a usable question wins; anything else is remembered
    as the reason and reported, so a run says WHY no question was proposed.
    """
    keys = _jev_keys()
    if not keys:
        return "", "no OpenRouter key found for the question proposer"
    body = {"model": proposer_model(), "max_tokens": 220,
            "messages": [{"role": "system", "content": PROPOSER_SYSTEM},
                         {"role": "user", "content": state}]}
    last = "no attempt"
    for k in keys:
        st, resp, _ = _req(PROPOSER_URL, "POST", body, {"Authorization": f"Bearer {k}"}, timeout=90)
        if st == 200 and isinstance(resp, dict):
            text = question_from_reply(resp)
            if text:
                return text, ""
            last = f"HTTP {st}: the reply was not a question ({str(resp)[:160]})"
            continue
        last = f"HTTP {st}: {str(resp)[:180]}"
    return "", f"all {len(keys)} proposer keys failed; last: {last}"


# ---------------------------------------------------------------- PROPAGATE (SPEC-001 BEAT 4)
# Closure and moot cascades — docs/SPEC-001-graph.md section 4, BEAT 4. The engineering calls in
# that spec are settled and are implemented here, not re-litigated: Q4 — a closed branch reopens
# automatically when the answer that closed it is invalidated, with the reason recorded.
#
# Two walks, different in kind:
#   * the RULE walk — an invalidation (a `breaks` edge) moots the questions the dead decision
#     answered, and the change runs down the derives_from tree. No model is involved: a stored
#     edge is an assertion, and following it is arithmetic.
#   * the GATE walk — a question whose parent just closed is re-examined BEFORE it is asked. That
#     one costs a retrieval plus one JEV call, so it records its verdict on the question row
#     (`jev_already_answered` / `jev_checked_at` — the two columns that exist for exactly this)
#     and is not repeated until --recheck says to. A gate call that fails leaves the question
#     open: no answer is invented to make a walk look complete.
#
# WHERE A QUESTION'S REASON LIVES. The `question` table has no reason column, and the declared
# table registry caches declarations per namespace IN PROCESS, so appending a column to the
# declaration is NOT live for a namespace the server has already scanned (verified against the
# running service: a PATCH naming an undeclared column returns 400 VALIDATION_ERROR even after
# the declaration file itself is rewritten). So the reason is recorded where SPEC-001 section 3
# already puts a question's resolution — its facet rows, one per way of looking: closing (or
# reopening) them with `closed_by` and `note` records the new state and why it changed. q_reason()
# reads it back, so that storage decision sits behind one function.
QUESTION_STATES = ("open", "answered", "linked", "moot", "budget_thin")
#: The states in which a question is SETTLED — an answer exists. `moot` is deliberately not one:
#: a withdrawn question was never answered, so a question that blocks on it is still blocked.
SETTLED_STATES = ("answered", "linked")
#: The states in which a question is CLOSED — it is no longer waiting to be asked.
CLOSED_STATES = ("answered", "linked", "moot", "budget_thin")


def q_states(ns: str, project_id: str) -> dict:
    """Every question of the project, by id — the nodes both walks move."""
    return {q["id"]: q for q in select(ns, "question", f"project_id=eq.{project_id}&order=id.asc")}


def graph_edges(ns: str, project_id: str) -> list:
    return select(ns, "edge", f"project_id=eq.{project_id}&order=id.asc")


def edge_index(es: list) -> dict:
    """Index the edges the walks need, in the direction SPEC-001 section 2 defines them.

    derives_from: src is the question that exists only because dst was asked -> children[dst]=src.
    blocks:       src is the question that CANNOT be answered until dst is -> blockers[src]=dst.
    closes:       src is the answer, dst the question it is the answer to -> closed[src]=dst.
    breaks:       src is the answer that invalidates the dst decision.
                  A break whose `dst_project` is set points at a SIBLING's decision (SPEC-002 2.2):
                  that is an escalation for the sibling, not this project's decision dying, so it is
                  NOT indexed as a local invalidation — ids are per namespace, and a sibling's D-007
                  is not this project's D-007.
    """
    idx = {"children": {}, "blockers": {}, "closed": {}, "breaks": []}
    for e in es:
        k = e.get("kind")
        if k == "derives_from" and e.get("src_kind") == "question" and e.get("dst_kind") == "question":
            idx["children"].setdefault(e["dst_id"], []).append(e["src_id"])
        elif k == "blocks" and e.get("src_kind") == "question" and e.get("dst_kind") == "question":
            idx["blockers"].setdefault(e["src_id"], []).append(e["dst_id"])
        elif k == "closes" and e.get("src_kind") == "decision" and e.get("dst_kind") == "question":
            idx["closed"].setdefault(e["src_id"], []).append(e["dst_id"])
        elif k == "breaks" and e.get("dst_kind") == "decision" and not e.get("dst_project"):
            idx["breaks"].append(e)
    return idx


def questions_closed_by(ns: str, project_id: str, idx: dict) -> dict:
    """decision id -> the questions that decision is the answer to.

    TWO sources, because both are stored assertions of the same fact and either may be the only
    one present: the `closes` edge (SPEC-001 section 2) and `decision.question_id` (the field the
    CLI has always accepted and nothing has ever read back).
    """
    by = {d: set(v) for d, v in idx["closed"].items()}
    for d in select(ns, "decision", f"project_id=eq.{project_id}"):
        qid = (d.get("question_id") or "").strip()
        if qid:
            by.setdefault(d["id"], set()).add(qid)
    return {d: sorted(v) for d, v in by.items()}


def q_reason(ns: str, question_id: str) -> str:
    """Why this question is in the state it is in — "" when nothing is on record."""
    for f in select(ns, "facet", f"question_id=eq.{question_id}&order=id.asc"):
        if f.get("note"):
            return f["note"]
    return ""


def set_state(ns: str, project_id: str, question_id: str, status: str, reason: str,
              *, closed_by: str = "") -> dict:
    """Move one question to a state and RECORD WHY (SPEC-001 section 1: a question is never
    deleted, a moot one keeps its row and its reason).

    The reason goes on the question's facet rows — the rows SPEC-001 section 3 gives every
    question for exactly this — because the `question` table has no reason column and a
    declaration the running server has already scanned cannot gain one (see the block comment).
    A question with no facet rows gets the full set here, so a reason always has a home.
    The facets are written BEFORE the status: a failure leaves the question in its old state
    rather than in a new one with no explanation.
    """
    if status not in QUESTION_STATES:
        raise SystemExit(f"unknown question state {status!r}: expected one of {', '.join(QUESTION_STATES)}")
    fields = {"status": "open" if status == "open" else "closed",
              "closed_by": "" if status == "open" else closed_by,
              "note": reason}
    have = select(ns, "facet", f"question_id=eq.{question_id}&order=id.asc")
    if have:
        for f in have:
            patch(ns, "facet", f["id"], fields)
    else:
        first = int(next_id(ns, "facet", "F").split("-")[1])
        insert(ns, "facet", [{"id": f"F-{first + i:0{ID_WIDTH}d}", "project_id": project_id,
                              "question_id": question_id, "facet": name, **fields}
                             for i, name in enumerate(facet_set())])
    return patch(ns, "question", question_id, {"status": status})


def unresolved_blockers(idx: dict, qs: dict, question_id: str) -> list:
    """The questions this one cannot be answered until (SPEC-001: the blocks edge).

    A `moot` blocker does NOT unblock its dependents: a withdrawn question was never answered, so
    the dependency it created is still open. A blocker with no stored row is unresolved too — the
    conservative direction, since an edge that cannot be walked backwards must not read as done.
    """
    return [b for b in idx["blockers"].get(question_id, ())
            if (qs.get(b) or {}).get("status") not in SETTLED_STATES]


def askable_questions(ns: str, project_id: str) -> list:
    """Open questions nothing unresolved is blocking — the surface `ask` may show."""
    idx, qs = edge_index(graph_edges(ns, project_id)), q_states(ns, project_id)
    return [q for q in qs.values() if q.get("status") == "open" and not unresolved_blockers(idx, qs, q["id"])]


def blocked_questions(ns: str, project_id: str) -> list:
    """Open questions held back by an unresolved blocker — counted, never surfaced."""
    idx, qs = edge_index(graph_edges(ns, project_id)), q_states(ns, project_id)
    return [q for q in qs.values() if q.get("status") == "open" and unresolved_blockers(idx, qs, q["id"])]


def decision_named(key: str) -> str:
    """The decision id an evidence key names — "" when the key names none (no storage check).

    `answer` embeds each decision's evidence under /auger/<pid>/<did>, so the key is how a hit says
    WHICH decision it is evidence of. WHERE that decision is then looked up is the caller's business:
    this project's namespace (see decision_for_evidence) or a sibling's, which is what a bundle makes
    reachable (SPEC-002 section 2.1).
    """
    m = re.match(r"^/auger/[^/]+/([^/]+)$", key or "")
    return m.group(1) if m else ""


def decision_for_evidence(ns: str, project_id: str, key: str) -> str:
    """The decision a stored evidence key belongs to — "" when the key names none.

    Without a decision to name there is no `satisfies` edge to write, and an unnamed link is not a
    link (SPEC-001 section 1: `linked` means an EXISTING answer satisfies it), so the caller leaves
    the question open instead.
    """
    did = decision_named(key)
    return did if did and select(ns, "decision", f"id=eq.{did}&project_id=eq.{project_id}&select=id&limit=1") else ""


def gate_question(ns: str, project_id: str, text: str, limit: int = 5):
    """The gate: is this question already fully answered by evidence we already hold?

    Retrieval first — over this project's OWN namespace and then over every sibling namespace a
    bundle puts in reach (SPEC-002 section 2.1: the gate searches the whole bundle, which is what
    stops two projects re-deciding the same question and drifting apart) — then ONE JEV noul over the
    pooled evidence, so a bundle of six members still costs one model call. No stored evidence
    anywhere means no model call: nothing can answer it, so the verdict is None and the question
    stays open. A transport error is returned, never converted into a verdict (fail-closed).

    Returns (verdict, decision_id, src_project, err). `src_project` names the SIBLING whose namespace
    held the winning evidence, and is "" when the answer was this project's own; the caller puts it
    on the `satisfies` edge, so a reader can see the link crossed the boundary.
    """
    home = project_name(ns, project_id)
    places = [(ns, home)] + bundle_namespaces(ns, home)
    hits = recall_many([where for where, _ in places], text, limit=limit)
    if not hits:
        return None, "", "", None
    evidence = "\n".join(f"- {h.get('key')}: {h.get('content', '')[:400]}" for _, h in hits)
    ans, err = jev(f"QUESTION UNDER CONSIDERATION:\n{text}\n\nSTORED EVIDENCE:\n{evidence}",
                   {"already_answered": {"type": "noul",
                    "instructions": "Is the question under consideration ALREADY fully answered by the stored evidence?"}})
    if err:
        return None, "", "", err
    v = _noul(ans["answers"], "already_answered")
    if v is None or v < T_ANSWERED:
        return v, "", "", None
    # Highest score first — the same order as before bundles. What a hit came FROM decides where its
    # decision is looked for, and therefore which project the link ends up naming.
    for where, h in sorted(hits, key=lambda pair: -(pair[1].get("score") or 0)):
        key = h.get("key") or ""
        if where == ns:
            did, src = decision_for_evidence(ns, project_id, key), ""
        else:
            did = decision_named(key)
            # Read it back WHERE IT LIVES: a decision that cannot be read is not a link, and a link
            # to an unread row would be an invented one.
            if did and not select_or_empty(where, "decision", f"id=eq.{did}&select=id&limit=1"):
                did = ""
            src = next((p for w, p in places if w == where), "")
        if did:
            return v, did, src, None
    return v, "", "", None           # answered, but by evidence that names no decision node


def propagate(ns: str, project_id: str, *, gate: bool = True, recheck: bool = False) -> dict:
    """Run both walks once and report exactly what moved (SPEC-001 BEAT 4).

    The report is the verb's output AND the tests' handle on it:
      moot        [(question, reason)]                 the questions a dead decision answered
      reopened    [(question, reason)]                 the settled questions that went stale
      linked      [(question, decision, noul, project)] questions the gate satisfied from stored
                                                        evidence; `project` is the SIBLING whose
                                                        namespace held the answer, "" when it was
                                                        this project's own (SPEC-002 2.1)
      edges       [edge id]                            what the gate wrote
      warnings    [str]                                a write that had to be stored downgraded
      askable     [question id]                        open and unblocked after the walk
      blocked     [question id]                        open and held back by an unresolved blocker
      unattributed [question id]                       gate says answered, evidence names no decision
      ungated     [question id]                        left open because the gate was unreachable
      gate_error  str | None
    """
    qs = q_states(ns, project_id)
    idx = edge_index(graph_edges(ns, project_id))
    closed_by = questions_closed_by(ns, project_id, idx)
    rep = {"moot": [], "reopened": [], "linked": [], "edges": [], "warnings": [], "askable": [],
           "blocked": [], "unattributed": [], "ungated": [], "gate_error": None}

    # ---- the rule walk: invalidation -> moot, then down the derives_from tree
    seen, queue = set(), []
    for e in idx["breaks"]:
        dead, root = e["dst_id"], f"{e['dst_id']} was invalidated by {e['id']}"
        for qid in closed_by.get(dead, ()):
            q = qs.get(qid)
            if q is None or q["status"] == "moot":
                continue                      # already withdrawn, and it keeps the first reason
            set_state(ns, project_id, qid, "moot", root, closed_by=dead)
            qs[qid]["status"] = "moot"
            seen.add(qid)
            rep["moot"].append((qid, root))
            queue.append((qid, root))
    while queue:
        parent, root = queue.pop(0)
        for child in sorted(idx["children"].get(parent, ())):
            if child in seen:
                continue                      # a cycle in derives_from cannot loop the walk
            seen.add(child)
            q = qs.get(child)
            if q is None:
                continue
            if q["status"] in SETTLED_STATES:
                # The engine's Q4: a closed branch is not closed, it is STALE — stale is worse
                # than open because it silently reads as done. Reopen it and say why.
                reason = f"reopened: {parent} is stale ({root})"
                set_state(ns, project_id, child, "open", reason)
                qs[child]["status"] = "open"
                rep["reopened"].append((child, reason))
            # an already-open question is newly askable, a moot one keeps its reason, and a
            # budget_thin branch's ceiling has not moved: none of them are rewritten here.
            queue.append((child, root))

    # ---- the gate walk: children of closed questions, re-examined before they are asked
    pending = []
    for parent, kids in idx["children"].items():
        p = qs.get(parent)
        if p is None or p["status"] not in CLOSED_STATES:
            continue                          # the trigger is the parent CLOSING
        for child in sorted(kids):
            q = qs.get(child)
            if q is None or q["status"] != "open":
                continue
            if unresolved_blockers(idx, qs, child):
                continue                      # blocked questions are not gated, only counted
            if q.get("jev_checked_at") and not recheck:
                continue                      # the gate already examined this one
            pending.append(child)
    if gate and pending:
        for i, child in enumerate(pending):
            text = qs[child].get("text") or ""
            verdict, dec_id, src_project, err = gate_question(ns, project_id, text)
            if err:
                rep["gate_error"] = err
                rep["ungated"] = pending[i:]
                break
            checked_at = datetime.now(timezone.utc).isoformat()
            patch(ns, "question", child, {"jev_already_answered": -1.0 if verdict is None else verdict,
                                         "jev_checked_at": checked_at})
            qs[child]["jev_checked_at"] = checked_at
            if verdict is not None and verdict >= T_ANSWERED:
                if not dec_id:
                    rep["unattributed"].append(child)
                    continue
                # SPEC-002 2.1: when the winning evidence came from a SIBLING's namespace, the link
                # says so twice — on the edge (src_project, which also tells the referential check
                # where the decision actually lives) and in the question's own reason.
                row = edge(ns, project_id, "satisfies", "decision", dec_id, "question", child,
                           confidence=verdict, source="gate", src_project=src_project,
                           warnings=rep["warnings"],
                           note=f"the gate found this question already answered (noul {verdict:.2f})"
                                + (f" by {src_project}'s {dec_id}" if src_project else ""))
                set_state(ns, project_id, child, "linked",
                          f"linked by the gate to {dec_id} (noul {verdict:.2f})"
                          + (f" in {src_project}" if src_project else ""), closed_by=dec_id)
                qs[child]["status"] = "linked"
                rep["linked"].append((child, dec_id, verdict, src_project))
                rep["edges"].append(row["id"])

    rep["askable"] = [q["id"] for q in askable_questions(ns, project_id)]
    rep["blocked"] = [q["id"] for q in blocked_questions(ns, project_id)]
    return rep


def cmd_propagate(a):
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    rep = propagate(ns, pid, gate=not a.no_gate, recheck=a.recheck)
    print(f"project {pid}  |  {ns}")
    print(f"moot: {len(rep['moot'])}   reopened: {len(rep['reopened'])}   linked: {len(rep['linked'])}")
    for qid, reason in rep["moot"]:
        print(f"  moot       {qid}  {reason}")
    for qid, reason in rep["reopened"]:
        print(f"  reopened   {qid}  {reason}")
    for qid, dec_id, v, src in rep["linked"]:
        print(f"  linked     {qid}  -> {dec_id} (noul {v:.2f})"
              + (f"  from sibling {src} — the bundle was searched (SPEC-002 2.1)" if src else ""))
    for qid in rep["unattributed"]:
        print(f"  WARNING    {qid}: the gate says answered, but the evidence names no decision "
              f"— left open rather than linked to nothing")
    for w in rep["warnings"]:
        print(w)
    if rep["edges"]:
        print(f"edges written: {', '.join(rep['edges'])}")
    print(f"newly askable: {len(rep['askable'])}   blocked by an unresolved question: {len(rep['blocked'])}")
    texts = q_states(ns, pid)
    for qid in rep["askable"]:
        print(f"  askable    {qid}  {(texts.get(qid, {}).get('text') or '')[:90]}")
    if rep["gate_error"]:
        print(f"WARNING gate unavailable — {rep['gate_error']}")
        print(f"        {len(rep['ungated'])} question(s) left open and NOT linked (fail-closed).")
        return 1
    return 0


# ---------------------------------------------------------------- the FEEDBACK ENGINE (R12 / AUG-001)
# docs/DESIGN.md R12 and open decision 4, built over the graph AUG-007..AUG-011 landed. The engine
# proper: a low-confidence decision stops being a status line and becomes the NEXT QUESTION BATCH,
# bounded by a governor the program enforces.
#
# The design decisions this file records, all of them the recorded leanings of SPEC-001 section 7
# rather than new ones:
#
#   Q1 — gate everything (CONCLUDED). Every proposed question is put to JEV's already_answered
#        before it is asked. A gate costs ~$0.00003 and one retrieval; a duplicate question costs
#        the gate AND a full answer AND an impact pass AND a graph a reader has to untangle.
#   Q2 — a large model PROPOSES, JEV GATES AND RANKS (leaning). propose_question() is the
#        generation half; the ranking below is (confidence asc, gate noul asc), so the thinnest
#        decision is drilled first and, within it, the question the gate judged LEAST answered.
#   Q5 — runs unattended; it stops at a priority judgment, records an escalation with a default,
#        and CONTINUES ON THE DEFAULT (leaning). See T_PRIORITY and the escalation below.
#
# WHAT THE GOVERNOR IS AND IS NOT. The ceiling is the number of questions ONE RUN MAY ASK. It is
# not a cap on thinking: every candidate is still proposed and still GATED (that is what makes the
# next run cheap — the verdict is stored on the row), and a question that the ceiling stopped is
# recorded in state `budget_thin` with its text and its reason instead of being dropped. An honest
# shallow branch beats a silent one, so the run also writes ONE escalation row carrying the
# machine-checkable marker `budget-thin: true`.
#
# WHAT A RUN DOES, IN ORDER:
#   1. pick the thinnest decisions (lowest confidence first among RECORDED ones) that do not
#      already have an OPEN follow-up — a live branch is not drilled twice;
#   2. escalate any decision thin enough to be a priority judgment, with a default, and go on;
#   3. propose ONE question per decision (the large model), write the row, its facets and the
#      `opens` edge;
#   4. gate each: noul >= T_ANSWERED  -> REFUSED: the row is recorded `linked` to the decision that
#      already answers it, with a `satisfies` edge, and it is NEVER asked. Below the threshold ->
#      it is a genuine question;
#   5. ask in rank order while the ceiling lasts; the rest are recorded `budget_thin`;
#   6. return a report that is both what the verb prints and the tests' handle on the run.
#
# FAIL-CLOSED. An unreachable proposer produces NO question; an unreachable gate produces no ASK
# either — a question whose verdict is unknown is not asked, because asking it is exactly the
# duplicate the gate exists to prevent. Both cases are named in the report, the unproposed /
# ungated questions are printed with their text, and the verb exits non-zero.
FOLLOWUP_CLASS = "follow_up"     # the `qclass` a question opened by this engine carries


def feedback_budget() -> int:
    """The run ceiling, from the environment when it names one. A bad value is refused loudly.

    An empty/absent variable falls back to FEEDBACK_BUDGET; a value that is not a non-negative
    integer is a REFUSAL rather than a silent default, because a governor that quietly ignores the
    number it was given is not a governor.
    """
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return FEEDBACK_BUDGET
    try:
        n = int(raw)
    except ValueError:
        raise SystemExit(f"refused: {BUDGET_ENV}={raw!r} is not an integer")
    if n < 0:
        raise SystemExit(f"refused: {BUDGET_ENV}={raw!r} is negative — a ceiling cannot be")
    return n


def thin_decisions(ns: str, project_id: str) -> list:
    """The decisions that need drilling, THINNEST FIRST — criterion (a) of this row.

    RECORDED confidence only: `confidence` is -1 on a row whose confidence nobody stated, and
    "unknown" is not "low" — the same distinction `status` makes. Ordered by confidence and then
    by id, so two runs over an unchanged project drill it in the same order.
    """
    rows = select(ns, "decision", f"project_id=eq.{project_id}&order=confidence.asc,id.asc")
    return [d for d in rows
            if isinstance(d.get("confidence"), (int, float)) and 0 <= d["confidence"] < T_CONFIDENT]


def drilled_decisions(ns: str, project_id: str, qs: dict | None = None) -> dict:
    """decision id -> the OPEN follow-up an `opens` edge already gave it — the live branches.

    A decision with a live follow-up is not drilled again: the question is already on the board
    and awaiting an answer. A question in `budget_thin` deliberately does NOT count — it was never
    asked, so its decision is still owed a question, which is what makes the next run pick it up.
    """
    qs = q_states(ns, project_id) if qs is None else qs
    out: dict = {}
    for e in select(ns, "edge", f"project_id=eq.{project_id}&kind=eq.opens"):
        if e.get("src_kind") != "decision" or e.get("dst_kind") != "question":
            continue
        if (qs.get(e.get("dst_id")) or {}).get("status") == "open":
            out.setdefault(e["src_id"], e["dst_id"])
    return out


def decision_parent_question(ns: str, project_id: str, dec_row: dict) -> str:
    """The question this decision is the ANSWER to — "" when the record names none.

    TWO sources, the same pair `questions_closed_by` merges: `decision.question_id` (the field
    BEAT 2 populates) and a `closes` edge. Either may be the only one present. The id is returned
    only when that question actually exists: it becomes the `derives_from` endpoint, and an edge to
    a node nobody can read is refused by `edge()` — a refusal that would abort a whole run over a
    dangling field.
    """
    qid = (dec_row.get("question_id") or "").strip()
    if qid and node_exists(ns, "question", qid):
        return qid
    for e in select(ns, "edge", f"project_id=eq.{project_id}&kind=eq.closes&src_id=eq.{dec_row['id']}"):
        if e.get("src_kind") == "decision" and e.get("dst_kind") == "question" \
                and node_exists(ns, "question", e["dst_id"]):
            return e["dst_id"]
    return ""


def feedback_state(proj: dict, dec_row: dict, parent_text: str, open_questions: list) -> str:
    """Everything the proposer needs to write ONE specific question, and nothing else.

    Deliberately narrow. The proposer is told about the SEED (what the project is), the DECISION
    whose confidence is too low (what is thin), the question that decision answered (where the
    branch came from) and the questions already open (so it does not write a duplicate). It is not
    shown the stored evidence: the gate reads that, and a proposer fed the answer tends to restate
    it as a question.
    """
    lines = [f"PROJECT SEED:\n{(proj.get('seed') or '')[:800]}", "",
             "THE DECISION WHOSE CONFIDENCE IS TOO LOW TO BUILD ON:",
             f"{dec_row['id']} ({dec_row.get('domain') or 'no domain'}): {dec_row.get('chosen')}",
             f"confidence: {dec_row.get('confidence')}",
             f"reversal cost: {dec_row.get('reversal_cost') or 'not recorded'}",
             f"why the rejected alternatives lost: {dec_row.get('why_not') or 'not recorded'}"]
    if parent_text:
        lines += ["", f"THE QUESTION THIS DECISION ANSWERED: {parent_text}"]
    if open_questions:
        lines += ["", "QUESTIONS ALREADY OPEN IN THIS PROJECT (never repeat one):"]
        lines += [f"- {q['id']}: {(q.get('text') or '')[:140]}" for q in open_questions[:8]]
    return "\n".join(lines)


def record_followup(ns: str, project_id: str, dec_row: dict, text: str, status: str, *,
                    parent_qid: str = "", noul: float | None = None, checked_at: str = "",
                    reason: str = "", closed_by: str = "", warnings: list | None = None) -> dict:
    """Write ONE follow-up question row and its edges. Returns {"row":..., "edges":[...]}.

    The row travels with the gate's own verdict (`jev_already_answered` / `jev_checked_at` — the
    two columns that exist for exactly this, and the reason `propagate` will not re-gate it), the
    domain of the decision it drills, and `qclass=follow_up`, so a reader can tell an engine-raised
    question from one a person wrote. Its `ring` is the parent's ring + 1, or 1 at the top: the
    depth the branch is at, recorded rather than inferred from edge order.

    The edges are the branch's trackability, and they are the kinds SPEC-001 section 2 directs:
      * `opens`        — the ANSWER (the thin decision) raised this question;
      * `derives_from` — the question exists only because the parent question was asked, when the
                         decision answers one. This is what makes the new branch reachable by
                         PROPAGATE's walk instead of an orphan hanging off a decision.
    A non-`open` status is written THROUGH set_state, so the reason is on the question's facet rows
    (the only place this data model stores a question's reason — see the block comment above
    QUESTION_STATES) and the state change cannot happen without it.
    """
    qid = next_id(ns, "question", "Q")
    ring = 1
    if parent_qid:
        prows = select_or_empty(ns, "question", f"id=eq.{parent_qid}&select=ring&limit=1")
        ring = int(prows[0].get("ring") or 0) + 1 if prows else 1
    row = {"id": qid, "project_id": project_id, "domain": dec_row.get("domain") or "", "text": text,
           "ring": ring, "qclass": FOLLOWUP_CLASS, "status": "open",
           "jev_already_answered": -1.0 if noul is None else float(noul),
           "jev_checked_at": checked_at}
    insert(ns, "question", row)
    for name in facet_set():
        facet(ns, project_id, qid, name)
    edges = [edge(ns, project_id, "opens", "decision", dec_row["id"], "question", qid,
                  source="rule", warnings=warnings,
                  note=f"the confidence in {dec_row['id']} is {dec_row.get('confidence')} "
                       f"(< {T_CONFIDENT}), so the engine proposed a follow-up "
                       f"({proposer_model()})")["id"]]
    if parent_qid:
        edges.append(edge(ns, project_id, "derives_from", "question", qid, "question", parent_qid,
                          source="rule", warnings=warnings,
                          note=f"{qid} drills {dec_row['id']}, which answers {parent_qid}")["id"])
    if status != "open":
        set_state(ns, project_id, qid, status, reason, closed_by=closed_by)
    row["status"] = status
    return {"row": row, "edges": edges}


def feedback(ns: str, project_id: str, *, budget: int | None = None) -> dict:
    """One run of the feedback engine — the whole verb, and the tests' handle on it.

    The report:
      ceiling      int                                 the governor's ceiling for this run
      thin         [(decision, confidence)]            the decisions picked, thinnest first
      drilled      [(decision, question)]              left alone: an OPEN follow-up already exists
      proposed     [(decision, text)]                  what the large model wrote
      asked        [(question, decision, noul, text)]  opened and awaiting an answer
      refused      [(question, decision, noul, project)] the gate says ALREADY ANSWERED: linked,
                                                        never asked; `project` is the SIBLING whose
                                                        namespace held the answer, "" for our own
      budget_thin  [(question, decision, reason)]      recorded, NOT asked: the ceiling
      unattributed [(decision, text, noul)]            the gate says answered, the evidence names
                                                        no decision — not asked, not linked
      unproposed   [decision]                          no question was proposed for these
      ungated      [(decision, text)]                  the gate was unreachable: not asked
      escalations  [escalation id]                     priority judgments + the budget marker
      edges        [edge id]                           everything the run wrote
      askable      [question id]                       open and unblocked after the run
      warnings     [str]                               a degraded write, reported not swallowed
      proposer_error / gate_error  str | None
    """
    ceiling = feedback_budget() if budget is None else int(budget)
    if ceiling < 0:
        raise SystemExit(f"refused: a question ceiling of {ceiling} is negative — a ceiling cannot be")
    proj = _project(ns, project_id)
    pid = proj["id"]
    rep = {"ceiling": ceiling, "thin": [], "drilled": [], "proposed": [], "asked": [], "refused": [],
           "budget_thin": [], "unattributed": [], "unproposed": [], "ungated": [], "escalations": [],
           "edges": [], "askable": [], "warnings": [], "proposer_error": None, "gate_error": None}

    # ---- 1. the thinnest decisions, minus the ones already being drilled
    qs = q_states(ns, pid)
    live = drilled_decisions(ns, pid, qs)
    cand = []
    for d in thin_decisions(ns, pid):
        if d["id"] in live:
            rep["drilled"].append((d["id"], live[d["id"]]))
            continue
        cand.append(d)
        rep["thin"].append((d["id"], d.get("confidence")))
    if not cand:
        rep["askable"] = [q["id"] for q in askable_questions(ns, pid)]
        return rep

    # ---- 2. Q5: a decision this thin is a PRIORITY JUDGMENT, not a drilling problem. Escalate it
    # with a default and CONTINUE on the default — the run is unattended and never blocks.
    for d in cand:
        if isinstance(d.get("confidence"), (int, float)) and d["confidence"] < T_PRIORITY:
            esc = escalate(ns, pid,
                           question=f"priority judgment: {d['id']} has confidence "
                                    f"{d['confidence']:.2f}, below the {T_PRIORITY} floor — "
                                    f"{d.get('chosen')} rests on a weighing only you can make",
                           options=d["id"],
                           default_action=f"continue ON THE DEFAULT: the run drills {d['id']} "
                                          f"first and does not wait for an answer",
                           risk=f"every decision resting on {d['id']} inherits a confidence of "
                                f"{d['confidence']:.2f}")
            rep["escalations"].append(esc["id"])

    # ---- 3. propose ONE question per decision (the large model's half of Q2)
    open_qs = [q for q in qs.values() if q.get("status") == "open"]
    proposed = []
    for i, d in enumerate(cand):
        parent = decision_parent_question(ns, pid, d)
        parent_text = (qs.get(parent) or {}).get("text") or ""
        text, err = propose_question(feedback_state(proj, d, parent_text, open_qs))
        if err:
            rep["proposer_error"] = err
            rep["unproposed"] = [x["id"] for x in cand[i:]]
            break
        proposed.append((d, text, parent))
        rep["proposed"].append((d["id"], text))
    if not proposed:
        rep["askable"] = [q["id"] for q in askable_questions(ns, pid)]
        return rep

    # ---- 4. gate everything (Q1, concluded). An unknown verdict asks NOTHING.
    gated = []
    for i, (d, text, parent) in enumerate(proposed):
        verdict, dec_id, src_project, err = gate_question(ns, pid, text)
        if err:
            rep["gate_error"] = err
            rep["ungated"] = [(x["id"], t) for x, t, _ in proposed[i:]]
            break
        gated.append({"dec": d, "text": text, "parent": parent, "noul": verdict,
                      "dec_id": dec_id, "src_project": src_project,
                      "checked_at": datetime.now(timezone.utc).isoformat()})
    if not gated:
        rep["askable"] = [q["id"] for q in askable_questions(ns, pid)]
        return rep

    # ---- 5. rank (thinnest decision first, then the question the gate judged LEAST answered — a
    # noul of None means no stored evidence could answer it at all, which is the most unanswered
    # there is) and ask while the ceiling lasts; the rest are RECORDED, not dropped.
    ranked = sorted(gated, key=lambda g: (_conf(g["dec"]), -1.0 if g["noul"] is None else g["noul"],
                                          g["dec"]["id"]))
    left = ceiling
    for g in ranked:
        d, text, noul = g["dec"], g["text"], g["noul"]
        shared = {"parent_qid": g["parent"], "noul": noul, "checked_at": g["checked_at"],
                  "warnings": rep["warnings"]}
        if noul is not None and noul >= T_ANSWERED:
            if not g["dec_id"]:
                # Answered by evidence that names no decision: linking it would be an invented
                # link, and asking it would be the duplicate the gate exists to prevent. Left
                # unasked and unfiled, and SAID OUT LOUD (the text is printed) rather than stored
                # as a question the project would have to answer again.
                rep["unattributed"].append((d["id"], text, noul))
                rep["warnings"].append(
                    f"WARNING: the gate scores the proposed question for {d['id']} as already "
                    f"answered (noul {noul:.2f}) but the evidence names no decision — not asked, "
                    f"not linked, not stored.")
                continue
            where = f" in {g['src_project']}" if g["src_project"] else ""
            rec = record_followup(ns, pid, d, text, "linked",
                                  reason=f"refused: the gate found this already answered by "
                                         f"{g['dec_id']} (noul {noul:.2f}){where}", **shared,
                                  closed_by=g["dec_id"])
            sat = edge(ns, pid, "satisfies", "decision", g["dec_id"], "question", rec["row"]["id"],
                       confidence=noul, source="gate", src_project=g["src_project"],
                       warnings=rep["warnings"],
                       note=f"the gate found this question already answered by {g['dec_id']}"
                            f" (noul {noul:.2f}){where}")
            rec["edges"].append(sat["id"])
            rep["refused"].append((rec["row"]["id"], g["dec_id"], noul, g["src_project"]))
        elif left > 0:
            rec = record_followup(ns, pid, d, text, "open", **shared)
            left -= 1
            rep["asked"].append((rec["row"]["id"], d["id"], noul, text))
        else:
            rec = record_followup(
                ns, pid, d, text, "budget_thin",
                reason=f"budget-thin: the run's ceiling of {ceiling} question(s) was reached — "
                       f"recorded, NOT asked", **shared)
            rep["budget_thin"].append(
                (rec["row"]["id"], d["id"],
                 f"budget-thin: the run's ceiling of {ceiling} question(s) was reached — "
                 f"recorded, NOT asked"))
        rep["edges"] += rec["edges"]

    # ---- 6. the run-level marker: the ceiling WAS hit, and that is on the record too.
    if rep["budget_thin"]:
        esc = escalate(ns, pid,
                       question=f"budget-thin: true — the run hit its ceiling of {ceiling} "
                                f"question(s) with {len(rep['budget_thin'])} proposed question(s) "
                                f"recorded but NOT asked",
                       options=", ".join(q for q, _, _ in rep["budget_thin"]),
                       default_action="the questions keep their rows in state budget_thin and are "
                                      "asked on a later run",
                       risk="those branches stay thin until then: an honest shallow branch, never "
                            "a silent one")
        rep["escalations"].append(esc["id"])
    rep["askable"] = [q["id"] for q in askable_questions(ns, pid)]
    return rep


def _conf(dec_row: dict) -> float:
    """A decision's confidence as a SORT KEY: an unrecorded one sorts last, not first."""
    c = dec_row.get("confidence")
    return float(c) if isinstance(c, (int, float)) and c >= 0 else 2.0


def cmd_feedback(a):
    """Turn the thin decisions into the next question batch, bounded by the governor."""
    ns = a.namespace
    rep = feedback(ns, a.project_id, budget=a.budget)
    print(f"thin decisions (<{T_CONFIDENT}), needing a question of their own:")
    if not rep["thin"] and not rep["drilled"]:
        print("  (none — every recorded decision is at or above the threshold)")
    for did, conf in rep["thin"]:
        print(f"  {did}  {conf}  <- thinnest first")
    for did, qid in rep["drilled"]:
        print(f"  {did}  already drilled: {qid} is open and awaiting an answer")
    print(f"ceiling: {rep['ceiling']} question(s) this run   "
          f"proposed: {len(rep['proposed'])}   asked: {len(rep['asked'])}   "
          f"refused: {len(rep['refused'])}   budget-thin: {len(rep['budget_thin'])}")
    for qid, did, noul, text in rep["asked"]:
        print(f"  ASKED       {qid}  <- {did}  {text}")
    for qid, did, _reason in rep["budget_thin"]:
        print(f"  BUDGET-THIN {qid}  <- {did}  recorded, NOT asked (ceiling {rep['ceiling']})")
    for qid, did, noul, src in rep["refused"]:
        print(f"  REFUSED     {qid}  already answered by {did} (noul {noul:.2f})"
              + (f" in {src}" if src else "") + " — NOT asked")
    for did, text, noul in rep["unattributed"]:
        print(f"  UNLINKED    <- {did}  the gate says answered (noul {noul:.2f}) but the evidence "
              f"names no decision — not asked: {text}")
    for line in rep["warnings"]:
        print(line)
    if rep["escalations"]:
        print(f"escalations: {', '.join(rep['escalations'])}  "
              f"(questions only a person can weight, each with a default — the run continued on it)")
    for qid in rep["askable"]:
        print(f"  askable     {qid}")
    if rep["edges"]:
        print(f"edges written: {', '.join(rep['edges'])}")
    if rep["budget_thin"]:
        print(f"budget-thin: true — {len(rep['budget_thin'])} question(s) recorded but NOT asked "
              f"(ceiling {rep['ceiling']})")
    if rep["proposer_error"]:
        print(f"WARNING proposer unavailable — {rep['proposer_error']}")
        print(f"        {len(rep['unproposed'])} decision(s) left unproposed: "
              f"{', '.join(rep['unproposed'])}")
        print("        (fail-closed: no question is invented without the model.)")
        return 1
    if rep["gate_error"]:
        print(f"WARNING gate unavailable — {rep['gate_error']}")
        print(f"        {len(rep['ungated'])} proposed question(s) were NOT asked and NOT stored "
              f"(fail-closed):")
        for did, text in rep["ungated"]:
            print(f"          {did}  {text}")
        return 1
    return 0


# ---------------------------------------------------------------- verbs
def cmd_init(a):
    ns = a.namespace
    st, body, _ = db("/api/namespaces")
    # An error body is not a namespace list. Reading an error dict as a list used to
    # crash with `'str' object has no attribute 'get'` and, worse, made `init` report
    # success while nothing was declared (proven when a 429 landed here). Fail loudly
    # instead: the caller asked for a namespace that is now known NOT to exist.
    if st != 200:
        raise SystemExit(f"could not list namespaces ({st}): {body}")
    if isinstance(body, dict):
        body = body.get("namespaces", [])
    names = [n.get("name") for n in body if isinstance(n, dict)] if isinstance(body, list) else []
    created = False
    if ns not in names:
        st, body, _ = db("/api/namespaces", "POST", {"name": ns})
        if st not in (200, 201):
            raise SystemExit(f"could not create namespace {ns} ({st}): {body}")
        created = True
    # declare (idempotent) the SDM tables
    d = os.path.join(ns_dir(ns), "tables")
    os.makedirs(d, exist_ok=True)
    wrote = []
    for name, cols in COLS.items():
        decl = {"name": name, "format": "jsonl-objects", "primary": PRIMARY[name],
                "glob": f"tables/{name}.jsonl",
                "columns": [{"name": c, "type": t} for c, t in cols]}
        p = os.path.join(d, f"{name}.table.json")
        text = json.dumps(decl, indent=1)
        current = None
        if os.path.exists(p):
            with open(p) as f:
                current = f.read()
        if current != text:
            with open(p, "w") as f:
                f.write(text)
            wrote.append(name)
    st, live, _ = db(f"/api/ns/{ns}/tables")
    have = sorted(t["name"] for t in (live.get("tables", []) if isinstance(live, dict) else []))
    print(f"namespace {ns}: {'created' if created else 'existing'}")
    print(f"declared: {len(have)}/{len(COLS)} tables -> {', '.join(have)}")
    if wrote:
        print(f"wrote declarations: {', '.join(wrote)}")
    missing = sorted(set(COLS) - set(have))
    if missing:
        print(f"WARNING not visible to the API yet: {', '.join(missing)}")
    return 0


def _project(ns, pid=None):
    rows = select(ns, "project", "order=created_at.desc&limit=1")
    if pid:
        rows = [r for r in select(ns, "project", f"id=eq.{pid}")]
    if not rows:
        raise SystemExit("no project row — run: auger start --seed-file <f> [--name N]")
    return rows[0]


def cmd_start(a):
    ns = a.namespace
    pid = a.id or f"P-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    seed = a.seed or ""
    if a.seed_file:
        with open(a.seed_file, errors="replace") as f:
            seed = f.read()
    row = {"id": pid, "name": a.name or ns, "seed": seed, "core_statement": "",
           "status": "open", "created_at": datetime.now(timezone.utc).isoformat()}
    insert(ns, "project", row)
    remember(ns, f"/auger/{pid}/seed", seed)
    print(f"project {pid} in namespace {ns}")
    print(f"seed stored ({len(seed)} chars) + embedded")
    return 0


def cmd_ask(a):
    """Surface the next questions: JEV's pick, plus every low-confidence decision."""
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    seats = select(ns, "decision", f"project_id=eq.{pid}&confidence=lt.{T_CONFIDENT}&select=id,domain,chosen,confidence,status&order=confidence.asc")
    unknown = select(ns, "unknown", f"project_id=eq.{pid}")
    unans = select(ns, "question", f"project_id=eq.{pid}&status=eq.open")
    hits = recall(ns, p.get("seed", "") or "project spec", limit=6)
    evidence = "\n".join(f"- {h.get('key')}: {h.get('content','')[:300]}" for h in hits)
    decisions = "\n".join(f"- {d['id']} (conf {d['confidence']}): {d['chosen']}" for d in seats) or "(none yet)"
    state = (f"SEED:\n{p.get('seed','')}\n\nDECISIONS:\n{decisions}\n\n"
             f"OPEN QUESTIONS: {len(unans)}\nUNKNOWNS: {len(unknown)}\n\nRELATED EVIDENCE:\n{evidence}")
    qs = {
        "next_subject": {"type": "choice", "instructions": "Which subject of this system needs coverage next?",
            "criteria": {"none_needed": "evidence is sufficient for a v1",
                         "deployment_and_alerts": "deployment, boot survival, failure notification",
                         "retention_and_privacy": "retention horizons, privacy, data lifecycle",
                         "concurrency_and_scaling": "concurrency, load, growth beyond v1",
                         "testability": "how correctness will be proven"}},
        "new_question": {"type": "choice", "instructions": "Which single question should be asked next?",
            "criteria": {"how_does_it_fail": "what happens when a component stops working",
                         "what_does_it_break": "which existing decision this next choice would invalidate",
                         "what_is_the_data": "what the data means, not where it is stored",
                         "who_owns_it": "ownership and lifecycle after delivery",
                         "how_is_it_tested": "the test that proves it works",
                         "none": "nothing left worth asking"}},
        "already_answered": {"type": "noul", "instructions": "Is that question already fully answered by the evidence above?"},
        "completeness": {"type": "score", "instructions": "How complete is the design evidence for a buildable v1?",
            "criteria": ["nothing decided", "partial, major gaps", "mostly decided, minor gaps",
                         "decided enough to build", "complete and verified"]},
    }
    ans, err = jev(state, qs)
    print(f"project {pid}  |  {ns}")
    print(f"low-confidence decisions (<{T_CONFIDENT}): {len(seats)}   open questions: {len(unans)}   unknowns: {len(unknown)}")
    # The askable surface (SPEC-001 BEAT 4 + 5): a question ordered behind an unresolved blocker
    # is NOT surfaced here. Blocked questions are counted, never named — "not surfaced" is the
    # whole point of the edge, and a reader who can see the text of one has been shown it.
    ask = askable_questions(ns, pid)
    blocked = blocked_questions(ns, pid)
    print(f"askable (open, unblocked): {len(ask)}   blocked by an unresolved question: {len(blocked)}")
    for q in ask:
        print(f"  {q['id']}  {(q.get('text') or '')[:100]}")
    if err:
        print(f"\nJEV unavailable — {err}")
        print("(fail-closed: no next-question is invented without the model.)")
        return 1
    subj = ans["answers"].get("next_subject", {})
    nq = ans["answers"].get("new_question", {})
    comp = ans["answers"].get("completeness", {})
    already = _noul(ans["answers"], "already_answered")
    print(f"\ncompleteness : {comp.get('score')} / 4  (confidence {comp.get('confidence')})")
    print(f"next subject : {subj.get('choice')}  ({subj.get('confidence')})")
    if subj.get("confidence", 0) < T_SUBJECT:
        print(f"               ^ below threshold {T_SUBJECT} — treat as one option, not an answer")
    print(f"next question: {nq.get('choice')}  ({nq.get('confidence')})")
    print(f"already answered? {already}  ->", "ask something else" if (already or 0) >= T_ANSWERED
          else "genuinely new, ask it")
    print(f"\njev cost: {ans.get('usage',{}).get('cost')}  build: {ans.get('model')}")
    return 0


# ---------------------------------------------------------------- the decision's scope (AUG-009)
# SPEC-002 section 1 adds ONE field to `decision`: `scope` — what the decision BINDS. `project`
# (the default, and what every row written before the column means) binds only its own project.
# `bundle` binds every member of a bundle the project belongs to, which makes it a contract its
# siblings can BREAK rather than merely differ from.
#
# WHY THE DEFAULT TRAVELS AS AN ABSENT KEY. DuckBrain's declared-table registry caches a
# namespace's declarations IN PROCESS the first time it reads them (see the block comment above
# QUESTION_STATES; re-proven live 2026-09-21 for this column: a namespace read before this change
# is served a `decision` table with NO `scope` column, and an insert carrying one is refused with
# 400 VALIDATION_ERROR "Unknown column(s) in insert"). So the default path must send the payload
# every existing namespace already accepts — omitting the key when the scope is the default does
# exactly that, and a namespace declared before the column keeps working untouched. Where a caller
# DID ask for `bundle` and the column is not there, insert_decision() says so out loud rather than
# storing a contract that reads as a local choice.
UNDECLARED_SCOPE = re.compile(r"unknown column\(s\).*?\bscope\b", re.I)


def decision_scope(row: dict) -> str:
    """What a stored decision row binds — DEFAULT_SCOPE when nothing was recorded.

    One reader, because the default is a storage decision no caller should re-derive: a row
    written before the column existed, a row with an empty scope, and a row written explicitly
    `project` all mean the same thing, and this is the only place that says so.
    """
    return (row or {}).get("scope") or DEFAULT_SCOPE


def insert_decision(ns: str, row: dict) -> str:
    """Insert a decision row, never losing a non-default scope SILENTLY. Returns a WARNING.

    "" when nothing was downgraded. The two failure shapes are deliberately different: a 400 that
    names `scope` as an undeclared column is a namespace whose declaration predates the column, and
    dropping the key stores the decision the way that namespace can hold it — project-scoped, SAID
    OUT LOUD, with the remedy. Every other status is raised exactly as `insert` raises it: a failed
    write must never look like a stored row.
    """
    st, body, _ = db(tbl(ns, "decision"), "POST", [row])
    if st in (200, 201):
        return ""
    if "scope" in row and st == 400 and UNDECLARED_SCOPE.search(json.dumps(body)):
        st2, body2, _ = db(tbl(ns, "decision"), "POST",
                           [{k: v for k, v in row.items() if k != "scope"}])
        if st2 in (200, 201):
            return (f"WARNING: this namespace's `decision` declaration predates `scope` and the API "
                    f"is serving a cached declaration, so {row['id']} is stored {DEFAULT_SCOPE}-scoped, "
                    f"NOT bundle-scoped. Remedy: run `auger init` and restart the DuckBrain API so "
                    f"the declaration is re-read.")
        raise SystemExit(f"insert decision failed ({st2}): {body2}")
    raise SystemExit(f"insert decision failed ({st}): {body}")


# ---------------------------------------------------------------- the cross-boundary IMPACT pass
# SPEC-002 section 2.2. An answer to a BUNDLE-scoped decision additionally walks every member of the
# decision's bundle(s): each sibling's own bundle-scoped decisions are put to JEV, one question per
# decision, and the verdict is recorded as an edge naming the sibling. Two rules keep the walk finite
# and honest, and they are the two things this code must not lose:
#
#   * ONLY bundle-scoped decisions cross the boundary. A project-local decision never does — or every
#     answer reaches every project and the pass is quadratic again. The early return in bundle_impact
#     is that rule, and it is why an ordinary `auger answer` still costs nothing extra.
#   * a `breaks` edge into a sibling is an ESCALATION, not a silent change. The engine records the
#     break and surfaces it; it never rewrites another project's spec (the sibling's row is not
#     touched, and the escalation names it).
#
# The three outcomes are ORDERED, so they are one `score` — the type the engine already grades —
# rather than three separate questions: a bundle with four members and six shared contracts would
# otherwise cost eighteen model calls for one answer.
IMPACT_OUTCOMES = ("unchanged", "change", "invalidate")
T_IMPACT_CHANGE = 0.5        # score below this    -> the sibling's decision is untouched
T_IMPACT_INVALIDATE = 1.5    # score at/above this -> the sibling's contract is BROKEN


def escalate(ns: str, project_id: str, question: str, *, options: str = "", default_action: str = "",
             risk: str = "", status: str = "open") -> dict:
    """Record one escalation — a break the engine will not decide on its own (SPEC-002 2.2).

    The row lives in the ANSWERING project's namespace and carries that project's id, naming the
    sibling in its own text: the break is this answer's consequence, so it is recorded where the
    answer and the edge are. Nothing is written into the sibling's namespace — the same rule the
    pass follows for the break itself: auger RECORDS a break in a member's contract, it does not
    reach into that member's spec.
    """
    row = {"id": next_id(ns, "escalation", "ESC"), "project_id": project_id, "question": question,
           "options": options, "default_action": default_action, "risk": risk, "status": status}
    insert(ns, "escalation", row)
    return row


def bundle_impact_verdict(answer: dict, other: dict, project: str) -> tuple:
    """Ask JEV what ONE project's answer does to a SIBLING's bundle-scoped decision.

    Returns (kind, score, err) with kind in IMPACT_OUTCOMES — and "" on ANY error, because a
    malformed or unreachable model answer is UNKNOWN and never "unchanged": the caller must write no
    edge on a verdict it did not get. The thresholds are here, in code, like every other threshold in
    this module; the model supplies the score, never the meaning.
    """
    state = (f"AN ANSWER RECORDED IN PROJECT {answer.get('project_id')}:\n"
             f"{answer.get('id')}: {answer.get('chosen')}\n"
             f"reason its alternatives were rejected: {answer.get('why_not') or 'not stated'}\n\n"
             f"A BUNDLE-SCOPED DECISION OF A SIBLING PROJECT ({project}):\n"
             f"{other.get('id')}: {other.get('chosen')}\n"
             f"reason its alternatives were rejected: {other.get('why_not') or 'not stated'}\n\n"
             f"Both decisions bind every member of the bundle they share, so the answer above can "
             f"leave the sibling's decision standing, force it to change, or break it outright.")
    ans, err = jev(state, {"impact": {
        "type": "score",
        "instructions": (f"Does the answer above leave {project}'s decision {other.get('id')} "
                         f"unchanged, force it to change, or invalidate it?"),
        "criteria": ["unchanged: the sibling's decision still stands exactly as written",
                     "change: the sibling's decision must be re-decided to stay consistent",
                     "invalidate: the answer breaks the sibling's decision outright"]}})
    if err:
        return "", 0.0, err
    scored = (ans.get("answers") or {}).get("impact") or {}
    value = scored.get("score")
    if not isinstance(value, (int, float)):
        return "", 0.0, f"JEV returned no score for {other.get('id')} ({str(scored)[:120]})"
    score = float(value)
    if score < T_IMPACT_CHANGE:
        return IMPACT_OUTCOMES[0], score, ""
    if score < T_IMPACT_INVALIDATE:
        return IMPACT_OUTCOMES[1], score, ""
    return IMPACT_OUTCOMES[2], score, ""


def bundle_impact(ns: str, project_id: str, dec_row: dict) -> dict:
    """Walk the bundle of a BUNDLE-scoped answer and record what it does to each sibling (2.2).

    The report is both what the verb prints and the tests' handle on it:
      scoped      bool                   False when the decision does not cross the boundary
      walked      [project]              members whose bundle-scoped decisions were examined
      unchanged   [project]              members the model said are untouched
      affects     [(project, decision)]  a change, recorded as an `affects` edge
      breaks      [(project, decision)]  a break, recorded as a `breaks` edge + an escalation
      edges       [edge id]              what the pass wrote
      escalations [escalation id]        the breaks recorded
      lines       [str]                  the one-line surfacings to print
      warnings    [str]                  the model's failures and any degraded write
      skipped     [(project, reason)]    members left alone BECAUSE the verdict was unknown
    """
    rep = {"scoped": False, "walked": [], "unchanged": [], "affects": [], "breaks": [], "edges": [],
           "escalations": [], "lines": [], "warnings": [], "skipped": []}
    if decision_scope(dec_row) != "bundle":
        return rep                    # only a CONTRACT crosses the boundary — the finite rule
    rep["scoped"] = True
    home = project_name(ns, project_id)
    for project in bundle_siblings(ns, home):
        where = member_namespace(project)
        # A sibling's rows are read with the SAFE reader: a foreign namespace that cannot answer has
        # no decisions, and that is not this project's failure. The decision the ANSWER recorded is
        # never compared with itself, even if the sibling's namespace holds a row with its id.
        others = [d for d in select_or_empty(where, "decision", "scope=eq.bundle&order=id.asc")
                  if d.get("id") != dec_row.get("id")]
        rep["walked"].append(project)
        for other in others:
            kind, score, err = bundle_impact_verdict(dec_row, other, project)
            if err:
                # FAIL-CLOSED: an unknown verdict writes nothing, is surfaced, and never fails the
                # answer — the member is skipped, not guessed at.
                rep["skipped"].append((project, f"{other.get('id')}: {err}"))
                rep["warnings"].append(
                    f"WARNING: {project}'s {other.get('id')} was NOT compared with "
                    f"{dec_row.get('id')} — {err}. No edge is written on a verdict the model did "
                    f"not give (fail-closed).")
                continue
            if kind == IMPACT_OUTCOMES[0]:
                rep["unchanged"].append(project)
                continue
            shared = dict(confidence=score, source="impact", dst_project=project,
                          warnings=rep["warnings"])
            if kind == IMPACT_OUTCOMES[1]:
                wrote = edge(ns, project_id, "affects", "decision", dec_row["id"], "decision",
                             other["id"], note=f"{project}'s {other['id']} must be re-decided to "
                                               f"stay consistent with {dec_row['id']} "
                                               f"(score {score:.2f})", **shared)
                rep["affects"].append((project, other["id"]))
                rep["edges"].append(wrote["id"])
                rep["lines"].append(f"AFFECTS: answer {dec_row['id']} changes {project}'s "
                                    f"{other['id']} — recorded, not rewritten")
                continue
            wrote = edge(ns, project_id, "breaks", "decision", dec_row["id"], "decision", other["id"],
                         note=f"{project}'s {other['id']} is broken by {dec_row['id']} "
                              f"(score {score:.2f})", **shared)
            rep["breaks"].append((project, other["id"]))
            rep["edges"].append(wrote["id"])
            esc = escalate(
                ns, project_id,
                question=f"answer {dec_row['id']} breaks {project}'s {other['id']}",
                options=f"{project}/{other['id']}",
                default_action=f"recorded, not rewritten — {project}'s decision stands until "
                               f"{project} re-decides it",
                risk=f"the bundle contract is broken for {project}: {other.get('chosen')}")
            rep["escalations"].append(esc["id"])
            rep["lines"].append(f"ESCALATION: answer {dec_row['id']} breaks {project}'s "
                                f"{other['id']} — recorded, not rewritten")
    return rep


def cmd_answer(a):
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    scope = (a.scope or "").strip().lower()
    if scope and scope not in DECISION_SCOPES:
        raise SystemExit(f"unknown decision scope {a.scope!r}: expected one of {', '.join(DECISION_SCOPES)}")
    did = a.id or f"D-{len(select(ns,'decision',f'project_id=eq.{pid}'))+1:03d}"
    row = {"id": did, "project_id": pid, "domain": a.domain or "", "question_id": a.question_id or "",
           "chosen": a.chosen, "why_not": a.why_not or "", "reversal_cost": a.reversal_cost or "",
           "confidence": float(a.confidence if a.confidence is not None else -1),
           "status": a.status or "decided", "evidence_key": f"/auger/{pid}/{did}"}
    # `scope` is sent only when it is NOT the default: see insert_decision for why the default
    # travels as an absent key rather than as the word "project".
    if scope and scope != DEFAULT_SCOPE:
        row["scope"] = scope
    warning = insert_decision(ns, row)
    for i, opt in enumerate(a.option or []):
        insert(ns, "option", {"id": f"{did}-O{i+1}", "decision_id": did, "label": opt,
                              "costs": "", "breaks": "", "active": opt == a.chosen})
    # The embedded evidence must not contradict itself: the chosen option is CHOSEN, and the
    # rejected list is the other options. Writing all options as "rejected" (the first version
    # of this) produced evidence reading "chose X. Rejected: X, Y, Z" — which made the
    # already-answered check score a genuinely-answered question as unanswered.
    others = [o for o in (a.option or []) if o != a.chosen]
    remember(ns, row["evidence_key"],
             f"Question: {a.domain or ''} {a.question_id or ''}".strip()
             + f". Decision {did}: we chose {a.chosen}. "
             + (f"Rejected alternatives: {'; '.join(others)}. " if others else "No alternative was recorded. ")
             + f"Reason the alternatives were rejected: {a.why_not or 'not stated'}. "
             + f"Reversal cost: {row['reversal_cost'] or 'not stated'}.")
    # SPEC-002 section 2.2: an answer that is a CONTRACT walks its bundle. The hook sits here — after
    # the evidence is embedded and before this verb's own report — and it never changes the exit
    # code: a model that is down skips a member and says so, it does not undo the answer. The scope
    # read below is the scope that was STORED: a namespace that could not hold `bundle` (see
    # insert_decision) recorded a local decision, and a local decision crosses no boundary.
    stored_scope = DEFAULT_SCOPE if warning else decision_scope(row)
    impact = bundle_impact(ns, pid, {**row, "scope": stored_scope})
    print(f"{did} recorded  (confidence {row['confidence']}, {len(a.option or [])} options, "
          f"embedded, scope {decision_scope(row)})")
    for line in impact["lines"]:
        print(line)
    for line in impact["warnings"]:
        print(line)
    if warning:
        print(warning)
    return 0


def cmd_check(a):
    """Before asking: is this already answered by what we hold?"""
    ns = a.namespace
    hits = recall(ns, a.question, limit=a.limit)
    if not hits:
        print("no stored evidence matched — treat as a new question")
        return 0
    evidence = "\n".join(f"- {h.get('key')}: {h.get('content','')[:400]}" for h in hits)
    ans, err = jev(f"QUESTION UNDER CONSIDERATION:\n{a.question}\n\nSTORED EVIDENCE:\n{evidence}",
                   {"already_answered": {"type": "noul",
                     "instructions": "Is the question under consideration ALREADY fully answered by the stored evidence?"}})
    print(f"nearest stored rows ({len(hits)}):")
    for h in hits:
        print(f"  {h.get('score'):.3f}  {h.get('key')}")
    if err:
        print(f"\nJEV unavailable — {err}\n(fail-closed: UNKNOWN, not 'already answered'.)")
        return 1
    v = _noul(ans["answers"], "already_answered")
    verdict = "ALREADY ANSWERED" if (v or 0) >= T_ANSWERED else "NOT YET ANSWERED"
    print(f"\n{verdict}  (noul {v}, threshold {T_ANSWERED})")
    print(f"jev cost: {ans.get('usage',{}).get('cost')}")
    return 0


def cmd_status(a):
    ns = a.namespace
    p = _project(ns, a.project_id)
    pid = p["id"]
    dec = select(ns, "decision", f"project_id=eq.{pid}")
    opt = select(ns, "option", "")
    esc = select(ns, "escalation", f"project_id=eq.{pid}")
    unk = select(ns, "unknown", f"project_id=eq.{pid}")
    dom = select(ns, "domain", f"project_id=eq.{pid}")
    by_dom = {}
    for d in dec:
        by_dom.setdefault(d.get("domain") or "(none)", []).append(d)
    print(f"project {pid} — {p.get('name')}  [{p.get('status')}]")
    print(f"decisions {len(dec)} | options {len(opt)} | escalations {len(esc)} | unknowns {len(unk)} | domains {len(dom)}")
    if dec:
        confs = [d["confidence"] for d in dec if isinstance(d.get("confidence"), (int, float)) and d["confidence"] >= 0]
        if confs:
            print(f"confidence: min {min(confs):.2f}  mean {sum(confs)/len(confs):.2f}  max {max(confs):.2f}")
    if by_dom:
        print("\ncoverage by domain (decisions, mean confidence):")
        for k in sorted(by_dom):
            rows = by_dom[k]
            cs = [r["confidence"] for r in rows if isinstance(r.get("confidence"), (int, float)) and r["confidence"] >= 0]
            m = f"{sum(cs)/len(cs):.2f}" if cs else "n/a"
            flag = "  <- needs drilling" if (cs and sum(cs)/len(cs) < T_CONFIDENT) else ""
            print(f"  {k:10s} {len(rows):3d}  {m}{flag}")
    thin = [d for d in dec if isinstance(d.get("confidence"), (int, float)) and 0 <= d["confidence"] < T_CONFIDENT]
    if thin:
        print(f"\n{len(thin)} decision(s) below {T_CONFIDENT} — these are what `auger ask` will drill:")
        for d in sorted(thin, key=lambda x: x["confidence"]):
            print(f"  {d['id']}  {d['confidence']:.2f}  {d['chosen'][:70]}")
    # The selection invariant (AUG-015): the counts above cannot show WHICH options are active, so a
    # decision holding zero or two-or-more of them is named here. Warned, never fatal — the map this
    # verb exists to print is still the answer, and a record with a collision is still worth reading.
    activation_warnings = activation_warning_lines(dec, opt)
    if activation_warnings:
        print()
        print("\n".join(activation_warnings))
    return 0


# ------------------------------------------------ one option per decision (AUG-015 selection invariant)
# `option.active` is what makes a dump a CONFIGURATION rather than a union of everything anyone ever
# considered: the register exists so one decision resolves to ONE option. The substrate enforces
# nothing — `active` is a boolean column and the toggle was a raw PATCH — so the rule is kept at the
# two places that could break it or misreport it:
#
#   * `toggle --on` deactivates the target's siblings (the FLIP), because that verb writes the
#     selection. `--additive` is the deliberate escape hatch for hypotheses that need two live.
#   * `dump` and `status` NAME a decision whose active-option count is not one — zero OR two-or-more
#     — and carry on. A drifted record is still worth reading; a verb that refused to render would
#     hide the very contradiction the reader needs to see.
WARN_ACTIVATION = ("WARNING: decisions with != 1 active option "
                   "(a configuration SELECTS one option per decision):")


def options_by_decision(opts: list[dict]) -> dict[str, list[dict]]:
    """Every option row grouped under the decision it belongs to."""
    by_dec: dict[str, list[dict]] = {}
    for o in opts:
        by_dec.setdefault(o.get("decision_id") or "", []).append(o)
    return by_dec


def decision_options(by_dec: dict[str, list[dict]], oid: str) -> tuple[str, list[dict]]:
    """The decision `oid` belongs to and that decision's option rows; ("", []) when unregistered."""
    for did, cand in by_dec.items():
        if any(o["id"] == oid for o in cand):
            return did, cand
    return "", []


def activation_warning_lines(dec: list[dict], opts: list[dict],
                             live_by_dec: dict[str, set[str]] | None = None) -> list[str]:
    """The warning block for every decision whose active-option count is not exactly one.

    `live_by_dec` is the selection a caller is RENDERING — a `dump --config` hypothesis. Without it
    the stored `active` flags are the selection, which is what `status` reports. A decision with no
    option rows registered is skipped: there is nothing to select between, so nothing can disagree.
    """
    by_dec = options_by_decision(opts)
    lines = []
    for d in dec:
        cand = by_dec.get(d["id"], [])
        if not cand:
            continue
        if live_by_dec is None:
            live = {o["id"] for o in cand if o.get("active")}
        else:
            live = set(live_by_dec.get(d["id"]) or set())
        if len(live) == 1:
            continue
        if not live:
            lines.append(f"  - {d['id']}: 0 of {len(cand)} options active — this decision contributes "
                         f"NOTHING to the configuration")
        else:
            lines.append(f"  - {d['id']}: {len(live)} of {len(cand)} options active "
                         f"({', '.join(sorted(live))}) — one decision bound to contradictory choices")
    return [WARN_ACTIVATION] + lines if lines else []


def cmd_toggle(a):
    """Turn options on/off — the what-if switch: ONE option per decision by default.

    `--on` (and `--set ID=on`) turns the target's SIBLINGS off, so the decision keeps exactly one
    active option — the selection `dump` renders as THE configuration. Without it, `toggle --on`
    left the chosen option on as well and the dump printed one decision bound to two contradictory
    choices (observed 2026-09-22, AUG-015). `--additive` preserves that old behaviour for what-if
    work; `dump`/`status` then report the collision instead of rendering it as a configuration.
    """
    ns = a.namespace
    additive = bool(a.additive)
    by_dec = {} if additive else options_by_decision(select(ns, "option", "order=id.asc"))
    took = []
    touched: dict[str, bool] = {}   # the state THIS invocation set, per option

    def write(oid: str, state: bool) -> int:
        """PATCH one option's flag, and remember the write.

        The remembered state is why a call that flips several options of one decision still ends
        with one active option: a sibling is judged by the state this run gave it, never by the
        snapshot read before the first PATCH (`--on A --on B` would otherwise leave both live,
        because B was not active yet when the snapshot was taken).
        """
        n = patch(ns, "option", oid, {"active": state}).get("updated", 0)
        touched[oid] = state
        return n

    def flip(oid: str, state: bool) -> str:
        return f"{oid}->{'on' if state else 'off'} ({write(oid, state)})"

    def activate(oid: str) -> str:
        line = flip(oid, True)
        if additive:
            return line
        did, cand = decision_options(by_dec, oid)
        siblings = [o for o in cand if o["id"] != oid
                    and touched.get(o["id"], bool(o.get("active")))]
        if not siblings:
            return line
        # The flip is part of the write, so it is part of the report: the defect this fixes was a
        # configuration change nobody could see.
        offs = ", ".join(f"{o['id']} ({write(o['id'], False)})" for o in siblings)
        return f"{line}  [siblings off — one option per decision {did}: {offs}]"

    for oid, state in a.set or []:
        took.append(activate(oid) if state else flip(oid, False))
    for oid in (a.on or []):
        took.append(activate(oid))
    for oid in (a.off or []):
        took.append(flip(oid, False))
    print("toggled: " + (", ".join(took) if took else "(nothing — pass --on/--off/--set ID=on|off)"))
    return 0


def cmd_dump(a):
    """Render the system as it looks with a given option set.

    Two modes:
      dump                      -> the CURRENT active set (what is stored)
      dump --config D-001=O2    -> a hypothetical set (nothing is written), so you can ask
                                   "what does the system look like if we pick option 2 here
                                   and option 1 there" without disturbing the record.

    In EITHER mode a decision whose active options are not exactly one is WARNED about at the foot
    of the dump and still rendered: a record that drifted is exactly when the reader needs to see
    it (AUG-015).
    """
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    dec = select(ns, "decision", f"project_id=eq.{pid}&order=domain.asc,id.asc")
    opts = select(ns, "option", "order=id.asc")
    by_dec = options_by_decision(opts)

    # --config overrides: accept "D-001=O2" (option id) or "D-001=Postgres" (label)
    overrides, bad = {}, []
    for spec in (a.config or []):
        if "=" not in spec:
            bad.append(spec)
            continue
        d_id, val = spec.split("=", 1)
        d_id, val = d_id.strip(), val.strip()
        cand = by_dec.get(d_id, [])
        hit = next((o for o in cand if o["id"] == val), None) or \
              next((o for o in cand if o["label"].lower() == val.lower()), None)
        if hit:
            overrides.setdefault(d_id, set()).add(hit["id"])
        else:
            bad.append(f"{spec} (no such option for {d_id})")
    override_ids = set()
    for _s in overrides.values():
        override_ids |= _s
    hypothetical = bool(overrides)

    lines = []
    lines.append(f"# {p.get('name')} — configuration dump")
    lines.append(f"project {pid} · namespace {ns} · generated {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"mode: {'HYPOTHETICAL (nothing written)' if hypothetical else 'current stored state'}")
    lines.append(f"seed: {p.get('seed','')[:300]}")
    lines.append("")
    confs, shadows, live_by_dec = [], [], {}
    for d in dec:
        cand = by_dec.get(d["id"], [])
        if hypothetical:
            live_ids = overrides.get(d["id"]) or {o["id"] for o in cand if o.get("active")}
        else:
            live_ids = {o["id"] for o in cand if o.get("active")}
        # The selection this render TREATS as the configuration. A hypothesis is as capable of
        # binding one decision to two choices as the stored state is, so it is warned about too.
        live_by_dec[d["id"]] = set(live_ids)
        live = [o for o in cand if o["id"] in live_ids]
        for o in live:
            confs.append(f"{d['id']}={o['label']}")
        # a hypothesis that contradicts a recorded reason is worth naming, not hiding
        for o in live:
            if o["id"] in override_ids and o["label"] != d.get("chosen"):
                shadows.append(f"{d['id']}: choosing {o['label']} contradicts the recorded choice "
                               f"({d.get('chosen')}) — reason on file: {d.get('why_not') or 'none'}")
        lines.append(f"## {d['id']}  ({d.get('domain') or '—'})  conf {d.get('confidence')}")
        lines.append(f"   chosen   : {d.get('chosen')}")
        lines.append(f"   why not  : {d.get('why_not') or '(not recorded)'}")
        if cand:
            lines.append("   options  :")
            for o in cand:
                mark = "[x]" if o["id"] in live_ids else "[ ]"
                lines.append(f"     {mark} {o['id']}  {o['label']}  costs={o.get('costs') or '—'}  breaks={o.get('breaks') or '—'}")
        lines.append("")
    lines.append("---")
    lines.append(f"ACTIVE CONFIGURATION: {', '.join(confs) if confs else '(nothing active)'}")
    if shadows:
        lines.append("")
        lines.append("CONTRADICTIONS WITH THE RECORD:")
        for s in shadows:
            lines.append(f"  - {s}")
    lines += activation_warning_lines(dec, opts, live_by_dec)
    if bad:
        lines.append(f"WARNING: unparsed --config entries ignored: {', '.join(bad)}")
    out = "\n".join(lines)
    if a.out:
        with open(a.out, "w") as f:
            f.write(out)
        print(f"wrote {a.out}  ({len(out)} chars, {len(dec)} decisions, {len(confs)} choices)")
    else:
        print(out)
    return 0


def cmd_recall(a):
    for h in recall(a.namespace, a.query, a.limit):
        print(f"{h.get('score'):.3f}  {h.get('key')}\n     {h.get('content','')[:200]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="auger", description="spec drilling backed by DuckBrain")
    ap.add_argument("--namespace", "-n", default=os.environ.get("AUGER_NS", "auger"))
    ap.add_argument("--project-id", "-p", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create/verify the namespace and declare the SDM tables")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("start", help="register a project + store/embed its seed")
    s.add_argument("--name")
    s.add_argument("--id")
    s.add_argument("--seed")
    s.add_argument("--seed-file")
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("ask", help="surface the next questions (JEV) + low-confidence decisions")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("answer", help="record a decision with its rejected alternatives")
    s.add_argument("--id")
    s.add_argument("--chosen", required=True)
    s.add_argument("--option", action="append")
    s.add_argument("--why-not")
    s.add_argument("--domain")
    s.add_argument("--question-id")
    s.add_argument("--reversal-cost")
    s.add_argument("--confidence", type=float)
    s.add_argument("--status")
    s.add_argument("--scope", help=f"what this decision BINDS: {' | '.join(DECISION_SCOPES)} "
                                   f"(default {DEFAULT_SCOPE})")
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("check", help="is this question already answered by stored evidence?")
    s.add_argument("question")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("status", help="confidence map and coverage")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("toggle", help="turn options on/off (the what-if switch)")
    s.add_argument("--on", action="append")
    s.add_argument("--off", action="append")
    s.add_argument("--set", action="append", type=lambda v: (v.split("=")[0], v.split("=")[1].lower() in ("on", "true", "1")))
    s.add_argument("--additive", action="store_true",
                   help="do NOT deactivate the target's siblings: keep the old behaviour, where one "
                        "decision can hold several active options (dump/status then WARN about it)")
    s.set_defaults(fn=cmd_toggle)

    s = sub.add_parser("dump", help="render a configuration: current, or a --config hypothesis")
    s.add_argument("--out")
    s.add_argument("--config", action="append", metavar="D-001=O2",
                   help="hypothetical option set; repeatable. Nothing is written.")
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser("propagate", help="close/moot/reopen and re-gate (SPEC-001 BEAT 4)")
    s.add_argument("--no-gate", action="store_true",
                   help="rule walk only: moot, reopen and cascade, with no retrieval and no model call")
    s.add_argument("--recheck", action="store_true",
                   help="re-gate questions the gate has already examined (it records its verdict)")
    s.set_defaults(fn=cmd_propagate)

    s = sub.add_parser("feedback",
                       help="turn low-confidence decisions into the next question batch (AUG-001)")
    s.add_argument("--budget", type=int, default=None,
                   help=f"how many questions ONE run may ask (default {FEEDBACK_BUDGET}, "
                        f"env {BUDGET_ENV}); a question the ceiling stops is RECORDED, not dropped")
    s.set_defaults(fn=cmd_feedback)

    s = sub.add_parser("recall", help="semantic search over the namespace")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_recall)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
