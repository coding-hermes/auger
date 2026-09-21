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
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
                    ("confidence","double"),("status","varchar"),("evidence_key","varchar")],
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
                    ("confidence","double"),("source","varchar"),("note","varchar"),
                    ("created_at","timestamp")],
    "facet":       [("id","varchar"),("project_id","varchar"),("question_id","varchar"),("facet","varchar"),
                    ("status","varchar"),("closed_by","varchar"),("note","varchar")],
}
PRIMARY = {t: "id" for t in COLS}

# ---------------------------------------------------------------- the graph's closed sets
# Edge kinds and sources are sets the ENGINE switches on, so they live in code. The facet set is
# the deliberate exception — SPEC-001 section 3 makes it DATA so a project can extend it.
EDGE_KINDS = ("opens", "closes", "satisfies", "affects", "breaks",
              "derives_from", "references", "blocks")
EDGE_SOURCES = ("gate", "impact", "human", "rule")
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
         headers: dict | None = None, timeout: int = 45, retries: int = 4):
    """Send JSON as BYTES. Never build a request body by string interpolation.

    A 429 is BACKPRESSURE, not a failure (proven: the pytest suite bursts enough
    calls to trip DuckBrain's limiter, and an unretried 429 loses the write). It
    is retried inline, honouring the server's own Retry-After, before the caller
    ever sees a status. A test suite that flakes on the substrate's rate limiter
    is a test suite nobody trusts.
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


def db(path: str, method: str = "GET", body=None, timeout: int = 45):
    return _req(f"{DB_URL}{path}", method, body, {"x-api-key": _token()}, timeout)


def tbl(ns: str, table: str, query: str = "") -> str:
    return f"/api/ns/{ns}/tables/{table}{('?' + query) if query else ''}"


def select(ns: str, table: str, query: str = "") -> list:
    st, body, _ = db(tbl(ns, table, query))
    if st != 200:
        raise SystemExit(f"select {table} failed ({st}): {body}")
    return body if isinstance(body, list) else []


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


def edge(ns: str, project_id: str, kind: str, src_kind: str, src_id: str,
         dst_kind: str, dst_id: str, *, confidence: float | None = None,
         source: str = "human", note: str = "") -> dict:
    """Write one edge, refusing any edge whose endpoints are not already stored.

    `source` has no default-by-omission: an edge is born of a model gate, an impact pass, a
    person, or a rule, and an empty string would let a reader mistake an unknown origin for a
    recorded one. `confidence` is -1 — the spec'd "asserted directly, not scored by a model"
    value — whenever no model confidence is supplied.
    """
    if kind not in EDGE_KINDS:
        raise SystemExit(f"unknown edge kind {kind!r}: expected one of {', '.join(EDGE_KINDS)}")
    if source not in EDGE_SOURCES:
        raise SystemExit(f"unknown edge source {source!r}: expected one of {', '.join(EDGE_SOURCES)}")
    for role, k, i in (("src", src_kind, src_id), ("dst", dst_kind, dst_id)):
        if not node_exists(ns, k, i):
            raise SystemExit(f"refused: {role} {k} {i!r} does not exist in table "
                             f"{NODE_TABLES.get(k, '?')} — an edge to a missing node is not stored")
    row = {"id": next_id(ns, "edge", "E"), "project_id": project_id, "kind": kind,
           "src_kind": src_kind, "src_id": src_id, "dst_kind": dst_kind, "dst_id": dst_id,
           "confidence": float(confidence) if confidence is not None else -1.0,
           "source": source, "note": note, "created_at": datetime.now(timezone.utc).isoformat()}
    insert(ns, "edge", row)
    return row


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


def recall(ns: str, q: str, limit: int = 5) -> list:
    from urllib.parse import quote
    st, body, _ = db(f"/api/memories?namespace={ns}&q={quote(q)}&limit={limit}", timeout=60)
    if st != 200:
        return []
    return body.get("items", []) if isinstance(body, dict) else []


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
        elif k == "breaks" and e.get("dst_kind") == "decision":
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


def decision_for_evidence(ns: str, project_id: str, key: str) -> str:
    """The decision a stored evidence key belongs to — "" when the key names none.

    `answer` embeds each decision's evidence under /auger/<pid>/<did>, so the gate can name WHICH
    existing answer satisfies a question. Without a decision to name there is no `satisfies` edge
    to write, and an unnamed link is not a link (SPEC-001 section 1: `linked` means an EXISTING
    answer satisfies it), so the caller leaves the question open instead.
    """
    m = re.match(r"^/auger/[^/]+/([^/]+)$", key or "")
    if not m:
        return ""
    did = m.group(1)
    return did if select(ns, "decision", f"id=eq.{did}&project_id=eq.{project_id}&select=id&limit=1") else ""


def gate_question(ns: str, project_id: str, text: str, limit: int = 5):
    """The gate: is this question already fully answered by evidence we already hold?

    Retrieval first, then ONE JEV noul. Returns (verdict, decision_id, err). No stored evidence
    means no model call — nothing can answer it, so the verdict is None and the question stays
    open. A transport error is returned, never converted into a verdict (fail-closed).
    """
    hits = recall(ns, text, limit=limit)
    if not hits:
        return None, "", None
    evidence = "\n".join(f"- {h.get('key')}: {h.get('content', '')[:400]}" for h in hits)
    ans, err = jev(f"QUESTION UNDER CONSIDERATION:\n{text}\n\nSTORED EVIDENCE:\n{evidence}",
                   {"already_answered": {"type": "noul",
                    "instructions": "Is the question under consideration ALREADY fully answered by the stored evidence?"}})
    if err:
        return None, "", err
    v = _noul(ans["answers"], "already_answered")
    if v is None or v < T_ANSWERED:
        return v, "", None
    for h in sorted(hits, key=lambda h: -(h.get("score") or 0)):
        did = decision_for_evidence(ns, project_id, h.get("key") or "")
        if did:
            return v, did, None
    return v, "", None           # answered, but by evidence that names no decision node


def propagate(ns: str, project_id: str, *, gate: bool = True, recheck: bool = False) -> dict:
    """Run both walks once and report exactly what moved (SPEC-001 BEAT 4).

    The report is the verb's output AND the tests' handle on it:
      moot        [(question, reason)]            the questions a dead decision answered
      reopened    [(question, reason)]            the settled questions that went stale
      linked      [(question, decision, noul)]    questions the gate satisfied from stored evidence
      edges       [edge id]                       what the gate wrote
      askable     [question id]                   open and unblocked after the walk
      blocked     [question id]                   open and held back by an unresolved blocker
      unattributed [question id]                  gate says answered, evidence names no decision
      ungated     [question id]                   left open because the gate was unreachable
      gate_error  str | None
    """
    qs = q_states(ns, project_id)
    idx = edge_index(graph_edges(ns, project_id))
    closed_by = questions_closed_by(ns, project_id, idx)
    rep = {"moot": [], "reopened": [], "linked": [], "edges": [], "askable": [], "blocked": [],
           "unattributed": [], "ungated": [], "gate_error": None}

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
            verdict, dec_id, err = gate_question(ns, project_id, text)
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
                row = edge(ns, project_id, "satisfies", "decision", dec_id, "question", child,
                           confidence=verdict, source="gate",
                           note=f"the gate found this question already answered (noul {verdict:.2f})")
                set_state(ns, project_id, child, "linked",
                          f"linked by the gate to {dec_id} (noul {verdict:.2f})", closed_by=dec_id)
                qs[child]["status"] = "linked"
                rep["linked"].append((child, dec_id, verdict))
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
    for qid, dec_id, v in rep["linked"]:
        print(f"  linked     {qid}  -> {dec_id} (noul {v:.2f})")
    for qid in rep["unattributed"]:
        print(f"  WARNING    {qid}: the gate says answered, but the evidence names no decision "
              f"— left open rather than linked to nothing")
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
        if not os.path.exists(p) or open(p).read() != text:
            open(p, "w").write(text)
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
    seed = open(a.seed_file, errors="replace").read() if a.seed_file else (a.seed or "")
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


def cmd_answer(a):
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    did = a.id or f"D-{len(select(ns,'decision',f'project_id=eq.{pid}'))+1:03d}"
    row = {"id": did, "project_id": pid, "domain": a.domain or "", "question_id": a.question_id or "",
           "chosen": a.chosen, "why_not": a.why_not or "", "reversal_cost": a.reversal_cost or "",
           "confidence": float(a.confidence if a.confidence is not None else -1),
           "status": a.status or "decided", "evidence_key": f"/auger/{pid}/{did}"}
    insert(ns, "decision", row)
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
    print(f"{did} recorded  (confidence {row['confidence']}, {len(a.option or [])} options, embedded)")
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
    return 0


def cmd_toggle(a):
    ns = a.namespace
    took = []
    for oid, state in a.set or []:
        r = patch(ns, "option", oid, {"active": state})
        took.append(f"{oid}->{'on' if state else 'off'} ({r.get('updated',0)})")
    for oid in (a.on or []):
        took.append(f"{oid}->on ({patch(ns,'option',oid,{'active':True}).get('updated',0)})")
    for oid in (a.off or []):
        took.append(f"{oid}->off ({patch(ns,'option',oid,{'active':False}).get('updated',0)})")
    print("toggled: " + (", ".join(took) if took else "(nothing — pass --on/--off/--set ID=on|off)"))
    return 0


def cmd_dump(a):
    """Render the system as it looks with a given option set.

    Two modes:
      dump                      -> the CURRENT active set (what is stored)
      dump --config D-001=O2    -> a hypothetical set (nothing is written), so you can ask
                                   "what does the system look like if we pick option 2 here
                                   and option 1 there" without disturbing the record.
    """
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    dec = select(ns, "decision", f"project_id=eq.{pid}&order=domain.asc,id.asc")
    opts = select(ns, "option", "order=id.asc")
    by_dec = {}
    for o in opts:
        by_dec.setdefault(o["decision_id"], []).append(o)

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
    confs, unresolved, shadows = [], [], []
    for d in dec:
        cand = by_dec.get(d["id"], [])
        if hypothetical:
            live_ids = overrides.get(d["id"]) or {o["id"] for o in cand if o.get("active")}
        else:
            live_ids = {o["id"] for o in cand if o.get("active")}
        live = [o for o in cand if o["id"] in live_ids]
        if not live and cand:
            unresolved.append(d["id"])
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
    if unresolved:
        lines.append(f"WARNING: decisions with options but NONE active: {', '.join(unresolved)}")
    if bad:
        lines.append(f"WARNING: unparsed --config entries ignored: {', '.join(bad)}")
    out = "\n".join(lines)
    if a.out:
        open(a.out, "w").write(out)
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

    s = sub.add_parser("recall", help="semantic search over the namespace")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_recall)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
