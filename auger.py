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
import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

DB_URL = os.environ.get("DUCKBRAIN_URL", "http://127.0.0.1:3000")
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# ---------------------------------------------------------------- thresholds (code, not model)
T_ANSWERED = (
    0.55  # noul >= this  -> the question is already answered by stored evidence
)
T_CONFIDENT = 0.60  # decision confidence below this is surfaced as "needs drilling"
T_SUBJECT = (
    0.45  # choice confidence below this -> we do not trust the "next subject" pick
)
T_PRIORITY = (
    0.30  # decision confidence below this -> a PRIORITY JUDGMENT, which the skill
)
# reserves for the human. The engine records an escalation with a default
# and CONTINUES ON THE DEFAULT: it never blocks waiting for a person
# (SPEC-001 section 7, Q5; docs/ENGINE.md open question 5).


# AUG-062. The -1 sentinel ("asserted directly, not scored by a model") lives in the STORE —
# every decision `answer` records without --confidence carries it, and that must not change. The
# defect was the LEAK: renders showed the bare -1 as if it were a measurement, and a `0 <= c`
# filter read "unmeasured" as "measured high". So: every confidence that reaches a READER goes
# through `_conf_render` (the sentinel renders as n/a), every AVERAGE goes through `_conf_values`
# (the sentinel is dropped, never averaged in), and the thin predicate treats unmeasured as the
# weakest evidence there is instead of as a passing grade.
def _conf_render(v) -> str:
    """A confidence as the READER sees it: the -1 sentinel renders as n/a, never a bare -1."""
    if isinstance(v, (int, float)) and v >= 0:
        return f"{v:g}"
    return "n/a"


def _conf_values(rows: list) -> list:
    """The MEASURED confidences of `rows`: the -1 sentinel is dropped, never averaged in."""
    return [
        float(r["confidence"])
        for r in rows
        if isinstance(r.get("confidence"), (int, float)) and r["confidence"] >= 0
    ]


def _is_unmeasured(dec_row: dict) -> bool:
    """True when a decision row's confidence is the -1 sentinel — nobody scored it."""
    c = dec_row.get("confidence")
    return not (isinstance(c, (int, float)) and c >= 0)


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
    "project": [
        ("id", "varchar"),
        ("name", "varchar"),
        ("seed", "varchar"),
        ("core_statement", "varchar"),
        ("status", "varchar"),
        ("created_at", "timestamp"),
    ],
    "question": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("domain", "varchar"),
        ("text", "varchar"),
        ("ring", "integer"),
        ("qclass", "varchar"),
        ("status", "varchar"),
        ("jev_already_answered", "double"),
        ("jev_checked_at", "varchar"),
    ],
    "decision": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("domain", "varchar"),
        ("question_id", "varchar"),
        ("chosen", "varchar"),
        ("why_not", "varchar"),
        ("reversal_cost", "varchar"),
        ("confidence", "double"),
        ("status", "varchar"),
        ("evidence_key", "varchar"),
        # AUG-009 / SPEC-002 section 1: what the decision BINDS. project | bundle,
        # empty = project — the value every row written before this column means.
        ("scope", "varchar"),
    ],
    "option": [
        ("id", "varchar"),
        ("decision_id", "varchar"),
        ("label", "varchar"),
        ("costs", "varchar"),
        ("breaks", "varchar"),
        ("active", "boolean"),
    ],
    "break": [
        ("id", "varchar"),
        ("decision_id", "varchar"),
        ("breaks_what", "varchar"),
        ("consequence", "varchar"),
        ("applied", "boolean"),
    ],
    "escalation": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("question", "varchar"),
        ("options", "varchar"),
        ("default_action", "varchar"),
        ("risk", "varchar"),
        ("status", "varchar"),
    ],
    "assumption": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("text", "varchar"),
        ("falsifier", "varchar"),
        ("monitoring", "varchar"),
    ],
    "unknown": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("text", "varchar"),
        ("owner", "varchar"),
        ("trigger", "varchar"),
        ("containment", "varchar"),
    ],
    "domain": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("num", "varchar"),
        ("name", "varchar"),
        ("triage", "integer"),
        ("ring_floor", "integer"),
        ("terminating_ring", "integer"),
        ("status", "varchar"),
        ("owner", "varchar"),
        ("trigger", "varchar"),
        ("containment", "varchar"),
    ],
    # --- the graph: the relationships the engine reasons over, not the nodes it stores (SPEC-001 2/3)
    "edge": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("kind", "varchar"),
        ("src_kind", "varchar"),
        ("src_id", "varchar"),
        ("dst_kind", "varchar"),
        ("dst_id", "varchar"),
        # AUG-010 / SPEC-002 sections 2.1-2.2: which PROJECT an endpoint belongs to when
        # that endpoint lives in a SIBLING's namespace — a bundle lets the gate link a
        # sibling's answer and the impact pass point at a sibling's decision. Empty means
        # "the endpoint is this namespace's", which is what every row written before this
        # field means. Both travel as ABSENT KEYS when empty: see insert_edge.
        ("src_project", "varchar"),
        ("dst_project", "varchar"),
        ("confidence", "double"),
        ("source", "varchar"),
        ("note", "varchar"),
        ("created_at", "timestamp"),
    ],
    "facet": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("question_id", "varchar"),
        ("facet", "varchar"),
        ("status", "varchar"),
        ("closed_by", "varchar"),
        ("note", "varchar"),
    ],
    # --- the bundles: which projects must honour one contract (SPEC-002 section 1). Membership is
    # a TABLE and not a column, because a project can belong to several bundles at once.
    "bundle": [
        ("id", "varchar"),
        ("name", "varchar"),
        ("description", "varchar"),
        ("contract", "varchar"),
        ("status", "varchar"),
    ],
    "bundle_member": [
        ("id", "varchar"),
        ("bundle_id", "varchar"),
        ("project", "varchar"),
        ("role", "varchar"),
        ("note", "varchar"),
    ],
    # --- the verdict register (R11): what a human or a model SAID about a dump, with the reasons.
    # The judged artifact is named by `config_summary` (the ACTIVE CONFIGURATION line, or the
    # --config spec string a hypothesis was judged by), so the row reads as evidence even when the
    # configuration it judged has since been toggled away.
    "verdict": [
        ("id", "varchar"),
        ("project_id", "varchar"),
        ("config_summary", "varchar"),
        ("verdict", "varchar"),
        ("reasons", "varchar"),
        ("judged_by", "varchar"),
        ("confidence", "double"),
        ("source", "varchar"),
        ("note", "varchar"),
        ("created_at", "timestamp"),
    ],
}
PRIMARY = {t: "id" for t in COLS}

# ---------------------------------------------------------------- the graph's closed sets
# Edge kinds and sources are sets the ENGINE switches on, so they live in code. The facet set is
# the deliberate exception — SPEC-001 section 3 makes it DATA so a project can extend it.
EDGE_KINDS = (
    "opens",
    "closes",
    "satisfies",
    "affects",
    "breaks",
    "derives_from",
    "references",
    "blocks",
    "supersedes",
)
EDGE_SOURCES = ("gate", "impact", "human", "rule")
SUPERSEDED_STATUS = "superseded"
# The bundle model's closed sets (SPEC-002 section 1). `status` and `role` are sets the ENGINE
# switches on — a retired bundle is not walked, a test-target does not own the contract — so they
# live in code and are refused BY NAME when they are wrong: EDGE_KINDS' rule, applied to the two
# columns that carry a decision about behaviour rather than a label.
BUNDLE_STATUSES = ("proposed", "confirmed", "retired")
BUNDLE_ROLES = ("owner", "consumer", "test-target")
# What a decision BINDS (SPEC-002 section 1). Empty/absent means DEFAULT_SCOPE, which is why the
# default is a named constant: no reader re-derives it from a literal.
DECISION_SCOPES = ("project", "bundle")
# What a VERDICT says (docs/DESIGN.md R11: "record good/bad with reasons"). A verdict is not a
# sentence — a reader greps it, a tally counts it, and a dump judged by ten words and one by two
# is not a judgement anyone can aggregate. Two words, closed in code like EDGE_KINDS, refused by
# name when wrong.
VERDICT_WORDS = ("good", "bad")
DEFAULT_SCOPE = "project"
# src_kind/dst_kind -> the table that must ALREADY hold that id (this is the referential check).
NODE_TABLES = {
    "decision": "decision",
    "question": "question",
    "unknown": "unknown",
    "assumption": "assumption",
}
# The DEFAULT facet set, not the only one: a project may extend it (SPEC-001 section 3, "the facet
# set is data, not code, so a project can extend it"). This is the INITIAL set; read it through
# facet_set() so an extension mechanism has exactly one place to land rather than a code path
# that hardcodes six strings no project can add to.
DEFAULT_FACETS = ("data", "failure", "ownership", "cost", "test", "who_else")
FACET_STATUSES = ("open", "closed")
ID_WIDTH = 6  # E-000001, F-000001 — fixed width, per the project's own id law


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
RETRIES = 4  # an ordinary call
TEARDOWN_RETRIES = (
    8  # a destructive call, where a give-up leaks state instead of losing work
)


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
    raise SystemExit(
        "no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token"
    )


def _req(
    url: str,
    method: str = "GET",
    body: dict | list | None = None,
    headers: dict | None = None,
    timeout: int = 45,
    retries: int = RETRIES,
):
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
                time.sleep(min(wait, 10.0) * attempt)  # bounded linear backoff
                continue
            try:
                return e.code, json.loads(raw), dict(e.headers)
            except ValueError:
                return e.code, raw, dict(e.headers)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, {"error": f"transport: {e}"}, {}


def db(
    path: str, method: str = "GET", body=None, timeout: int = 45, retries: int = RETRIES
):
    return _req(
        f"{DB_URL}{path}", method, body, {"x-api-key": _token()}, timeout, retries
    )


# ---------------------------------------------------------------- url building (AUG-061)
# EVERY request URL is built here, at one boundary, and every piece of caller text that reaches one
# is percent-encoded at that boundary. This is not cosmetic: `http.client` validates the finished
# URL and raises `InvalidURL` on a raw space (or any other control character) BEFORE the request is
# sent, so an unencoded name is not a wrong answer — it is a traceback that kills the whole verb.
# It kills it wherever the text lands: a path segment, a table name, or a filter VALUE.
#
# `quote`'s default `safe="/"` is wrong for BOTH halves of a URL. In a path it would let a namespace
# named `a/b` become two segments (a different table), so PATH segments and query KEYS use
# `safe=""`. In a query value a stray `&` or `=` would be read as STRUCTURE instead of data; only
# `,` stays readable, because postgrest uses it as a LIST separator (`select=id,name`,
# `order=a.asc,b.asc`, `in=(a,b)`) rather than as data.
_QUERY_VALUE_SAFE = ","


def url_seg(seg: object) -> str:
    """One URL PATH SEGMENT, percent-encoded — `safe=""` so `/` cannot split a segment."""
    return quote(str(seg), safe="")


def ns_tables_path(ns: str) -> str:
    """`/api/ns/<ns>/tables` — a namespace's own declaration endpoint.

    One function for the four call sites that used to interpolate `{ns}` by hand: a name that
    needs encoding needs it in all of them, and a fifth site added later should have nothing to
    copy by f-string.
    """
    return f"/api/ns/{url_seg(ns)}/tables"


def encode_query(query: str) -> str:
    """Percent-encode a PostgREST query string's keys and values, structure intact.

    The structure IS the data: `col=op.value&col2=op2.value2`. So the `&` separators and the FIRST
    `=` of each pair stay literal, the operator stays untouched (`.` is unreserved, so `eq.` /
    `lt.` survive `quote` unchanged), and only the value text is encoded. Choosing `safe=""` for
    the keys matters for the same reason as the path: a `&` smuggled into a key would invent a
    parameter.
    """
    if not query:
        return ""
    parts = []
    for part in query.split("&"):
        key, eq, value = part.partition("=")
        if not eq:
            parts.append(quote(part, safe=_QUERY_VALUE_SAFE))
            continue
        parts.append(f"{quote(key, safe='')}={quote(value, safe=_QUERY_VALUE_SAFE)}")
    return "&".join(parts)


def tbl(ns: str, table: str, query: str = "") -> str:
    base = f"/api/ns/{url_seg(ns)}/tables/{url_seg(table)}"
    return f"{base}?{encode_query(query)}" if query else base


#: How many rows ONE declared-table GET returns … and the reason `select` pages at all (AUG-068).
#: Measured on the live API: a read with no `limit` returns at most this many rows, an explicit
#: `limit` is honoured up to the server's own hard cap (1000), and the response carries NO row count
#: and no `Content-Range` — so equality between the rows returned and the limit asked for is the only
#: truncation signal a caller gets. A measured number, not a guess: the live suite asserts that a
#: real filterless read returns exactly this many rows.
API_PAGE = 100

#: How many page requests ONE read may make before it gives up loudly. A bounded loop needs a bound:
#: this is not the stop condition (a page SHORT of the size asked for is), it is the guard against a
#: server that ignores `offset` and would otherwise serve the same full page forever.
MAX_PAGES = 1000


def _query_pairs(query: str) -> dict:
    """The flat `key=value` pairs of a declared-table query, the way the server reads them."""
    pairs = {}
    for part in query.split("&"):
        key, eq, value = part.partition("=")
        if eq and key:
            pairs[key] = value
    return pairs


def _int_or_none(value) -> int | None:
    """A non-negative integer, or None for anything else — a `limit=all` is not a page size."""
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def _page_query(query: str, limit: int, offset: int) -> str:
    """`query` with OUR page `limit`/`offset` — the caller's own pairs kept.

    The caller's `limit`/`offset` are REPLACED, never sent alongside: two `limit=` pairs in one query
    is not a request the server can honour, and silently keeping whichever it picked would make the
    page size a guess. Everything else — filters, `select`, `order` — travels untouched, so the pages
    are the same query over the same rows. `offset=0` is left out: it is the server's own default, and
    the first request of a read then looks exactly like the single request it used to be.
    """
    kept = [
        part
        for part in query.split("&")
        if part and part.partition("=")[0] not in ("limit", "offset")
    ]
    kept.append(f"limit={limit}")
    if offset:
        kept.append(f"offset={offset}")
    return "&".join(kept)


def _select_paged(ns: str, table: str, query: str, strict: bool) -> list:
    """Every matching row, read in pages (AUG-068).

    A declared-table GET is page-capped and says NOTHING about it: with no `limit` the server returns
    `API_PAGE` rows, and a response is 200 with no row count and no `Content-Range` whether it holds
    one row or the first page of ten thousand. Equality between the rows returned and the limit asked
    for is therefore the ONLY truncation signal — so a caller either pages, or silently under-reports.
    `cmd_status` and `cmd_dump` read whole tables and did the latter: 180 stored options reported as
    "options 100", a decision rendering two of its three options.

    So a page is short of the size it asked for (the table ended) or this loop asks for the next one.
    `limit`-doubling would work too and is worse: the page size then depends on everything before it,
    and its stop condition depends on the server clamping an oversized limit it never promised to
    clamp. A fixed page plus an offset is bounded and deterministic.

    `strict` picks the only difference between the two public readers: an unanswered read RAISES for
    the caller's own namespace, or reads as "no rows here" for a sibling's (`select_or_empty`). That
    second case is all-or-nothing on purpose: a partially paged result would be exactly this row's
    silent under-count handed to the one caller that cannot distinguish it from an empty table.
    """
    pairs = _query_pairs(query)
    ceiling = _int_or_none(pairs.get("limit"))
    offset = _int_or_none(pairs.get("offset")) or 0
    rows: list = []
    for _page in range(MAX_PAGES):
        # The caller's limit is a ceiling on the RESULT, never the page size: `limit=1` (the mint's
        # page-safe read) must stay ONE request, and `limit=180` is served as pages up to 180.
        want = API_PAGE if ceiling is None else min(API_PAGE, ceiling - len(rows))
        if want <= 0:
            return rows
        st, body, _ = db(tbl(ns, table, _page_query(query, want, offset)))
        if st != 200:
            if strict:
                raise SystemExit(f"select {table} failed ({st}): {body}")
            return []
        page = body if isinstance(body, list) else []
        rows.extend(page)
        if len(page) < want:
            return rows
        if ceiling is not None and len(rows) >= ceiling:
            return rows
        offset += len(page)
    raise SystemExit(
        f"select {table} made {MAX_PAGES} full-page reads without reaching a short page: the server "
        "is not honouring `offset`, so paging cannot terminate. Refusing to return a partial read."
    )


def select(ns: str, table: str, query: str = "") -> list:
    """Every row the query matches — pages past the server's page cap instead of returning one (AUG-068).

    The caller's `limit`, when it passes one, still caps the result: `limit=1` is one row and one
    request, which is what the page-safe id reads (`next_id`, `decision_id_width`) depend on.
    """
    return _select_paged(ns, table, query, strict=True)


def select_or_empty(ns: str, table: str, query: str = "") -> list:
    """`select`, but a namespace or table that cannot answer returns NOTHING instead of exiting.

    The bundle reads need this and they are why it exists. A SIBLING's namespace is foreign — it may
    not exist, may not have the tables declared, or may be unreachable — and a namespace declared
    before the bundle tables existed (AUG-009) serves a 400 for them. Every one of those means "no
    rows here", never a crash in the middle of the asking project's own verb and never a row invented
    to fill the gap. The asking project's OWN namespace keeps the loud `select`: a broken read there
    is a defect, not a neighbourly absence.

    Paged like `select` (AUG-068), and a page that fails mid-read discards the pages before it: this
    reader's only answer to a read it could not finish is "no rows", and half a table would be a
    short table to every caller that trusts it.
    """
    return _select_paged(ns, table, query, strict=False)


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
    st, body, _ = db(
        "/api/memories",
        "POST",
        {"key": key, "namespace": ns, "domain": domain, "content": content},
    )
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
    st, body, _ = db(
        f"/api/namespaces/{url_seg(ns)}", "DELETE", {"confirm": True}, retries=retries
    )
    return st, body


# ---------------------------------------------------------------- the graph write path
# DuckBrain stores what it is sent — it enforces nothing — so referential integrity is OURS.
# An edge is a claim about two nodes; an edge whose endpoint does not exist is not a claim about
# anything, and a silently stored orphan is worse than a refusal because it reads as evidence.
def next_id(ns: str, table: str, prefix: str, width: int | None = None) -> str:
    """The next fixed-width id for a table, derived from the HIGHEST existing id.

    Not from a row count: a count collides the moment a row is deleted AND it stops advancing the
    moment the read is capped (the classic bugs this deliberately does not have). An empty table
    yields the first id, PREFIX-000001.

    `width` is the number of DIGITS to pad to and defaults to `ID_WIDTH`, the six-digit law every
    existing caller (`E`, `F`, `B`, `BM`, `V`, `Q`, `ESC`, ...) is already minted under: a caller
    that passes no width is byte-for-byte unchanged. A table that holds a DIFFERENT, narrower id
    law passes that width explicitly — decisions are three digits wide (D-007, D-101, see
    `decision_id_width`) and a six-digit row in that column would sort below every existing id
    under `order=id.desc`. The width is a FLOOR, never a truncation: a number needing more digits
    than `width` keeps all of them.
    """
    pad = ID_WIDTH if width is None else width
    rows = select(ns, table, "select=id&order=id.desc&limit=1")
    if not rows or not rows[0].get("id"):
        return f"{prefix}-{1:0{pad}d}"
    m = re.search(r"(\d+)\s*$", str(rows[0]["id"]))
    return f"{prefix}-{int(m.group(1)) + 1:0{pad}d}" if m else f"{prefix}-{1:0{pad}d}"


#: The width the LIVE decision ids are minted at: D-007, D-101 — three digits (AUG-069). `ID_WIDTH`
#: is the six-digit edge/facet/bundle law and is deliberately NOT this one: the ids in the real
#: namespaces are three digits, and one table holding two id widths sorts wrongly under the
#: lexicographic `order=id.desc` read that `next_id` mints from.
DECISION_ID_WIDTH = 3

#: AUG-070: how many times an auto-minted decision id is checked against the live store before the
#: answer gives up. The check-then-insert window cannot be closed from this side (DuckBrain enforces
#: no uniqueness), so a losing writer re-mints instead of refusing; the bound keeps a pathologically
#: contended namespace from spinning. Three attempts cover the observed two-writer race with room
#: for one more, and each attempt costs one `limit=1` read — the mint's own page-safe shape.
AUG070_ID_ATTEMPTS = 3


def decision_id_width(ns: str) -> int:
    """The digit width this namespace's decision ids are minted at.

    Read from the SAME page-safe row `next_id` mints from — the highest id, `order=id.desc&limit=1`
    — so the width can never be derived from a capped page of a bigger table. An empty table yields
    `DECISION_ID_WIDTH`, and a table wider than `ID_WIDTH` is capped there so one accidental row
    cannot mint an over-wide id law for the whole namespace.
    """
    rows = select(ns, "decision", "select=id&order=id.desc&limit=1")
    if not rows or not rows[0].get("id"):
        return DECISION_ID_WIDTH
    m = re.search(r"-(\d+)\s*$", str(rows[0]["id"]))
    return min(len(m.group(1)), ID_WIDTH) if m else DECISION_ID_WIDTH


def node_exists(ns: str, kind: str, node_id: str) -> bool:
    """Does a node of this kind actually exist? Unknown kinds are a hard error, not a pass."""
    table = NODE_TABLES.get(kind)
    if table is None:
        raise SystemExit(
            f"unknown node kind {kind!r}: expected one of {', '.join(sorted(NODE_TABLES))}"
        )
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
        raise SystemExit(
            f"unknown node kind {kind!r}: expected one of {', '.join(sorted(NODE_TABLES))}"
        )
    if not node_id:
        return False
    return bool(
        select_or_empty(ns, NODE_TABLES[kind], f"id=eq.{node_id}&select=id&limit=1")
    )


def edge(
    ns: str,
    project_id: str,
    kind: str,
    src_kind: str,
    src_id: str,
    dst_kind: str,
    dst_id: str,
    *,
    confidence: float | None = None,
    source: str = "human",
    note: str = "",
    src_project: str = "",
    dst_project: str = "",
    warnings: list | None = None,
) -> dict:
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
        raise SystemExit(
            f"unknown edge kind {kind!r}: expected one of {', '.join(EDGE_KINDS)}"
        )
    if source not in EDGE_SOURCES:
        raise SystemExit(
            f"unknown edge source {source!r}: expected one of {', '.join(EDGE_SOURCES)}"
        )
    for role, k, i, project in (
        ("src", src_kind, src_id, src_project),
        ("dst", dst_kind, dst_id, dst_project),
    ):
        where = member_namespace(project) if project else ns
        ok = node_exists(where, k, i) if where == ns else node_exists_in(where, k, i)
        if not ok:
            raise SystemExit(
                f"refused: {role} {k} {i!r} does not exist in table "
                f"{NODE_TABLES.get(k, '?')}"
                + (f" of project {project!r}" if project else "")
                + " — an edge to a missing node is not stored"
            )
    row = {
        "id": next_id(ns, "edge", "E"),
        "project_id": project_id,
        "kind": kind,
        "src_kind": src_kind,
        "src_id": src_id,
        "dst_kind": dst_kind,
        "dst_id": dst_id,
        "confidence": float(confidence) if confidence is not None else -1.0,
        "source": source,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    for key, val in (("src_project", src_project), ("dst_project", dst_project)):
        if val:
            row[key] = val
    warning = insert_edge(ns, row)
    if warning and warnings is not None:
        warnings.append(warning)
    return row


UNDECLARED_PROJECT_COL = re.compile(
    r"unknown column\(s\).*?\b(?:src_project|dst_project)\b", re.I
)


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
        keep["note"] = (
            (row.get("note") or "").rstrip()
            + f" [stored without {'/'.join(dropped)}: this namespace's `edge` declaration "
            f"predates them, so the project this edge crosses to is named here only]"
        ).strip()
        st2, body2, _ = db(tbl(ns, "edge"), "POST", [keep])
        if st2 in (200, 201):
            return (
                f"WARNING: this namespace's `edge` declaration predates {' and '.join(dropped)}, so "
                f"{row['id']} is stored WITHOUT the project its cross-namespace endpoint belongs to "
                f"(the note says so). Remedy: run `auger init` and restart the DuckBrain API so the "
                f"declaration is re-read."
            )
        raise SystemExit(f"insert edge failed ({st2}): {body2}")
    raise SystemExit(f"insert edge failed ({st}): {body}")


def facet(
    ns: str,
    project_id: str,
    question_id: str,
    name: str,
    *,
    status: str = "open",
    closed_by: str = "",
    note: str = "",
) -> dict:
    """Write one facet row. The facet name is a label, not a gate — the set is extensible data."""
    if status not in FACET_STATUSES:
        raise SystemExit(
            f"unknown facet status {status!r}: expected one of {', '.join(FACET_STATUSES)}"
        )
    if not node_exists(ns, "question", question_id):
        raise SystemExit(
            f"refused: question {question_id!r} does not exist — facet not stored"
        )
    row = {
        "id": next_id(ns, "facet", "F"),
        "project_id": project_id,
        "question_id": question_id,
        "facet": name,
        "status": status,
        "closed_by": closed_by,
        "note": note,
    }
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
ALLOW_SCRATCH_MEMBERS_ENV = "AUGER_ALLOW_SCRATCH_MEMBERS"
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
        have = con.execute(
            "select 1 from sqlite_master where type='table' and name='projects'"
        ).fetchone()
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
    candidates = (
        [os.path.expanduser(override)] if override else list(SCHEDULER_DB_CANDIDATES)
    )
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


def bundle(
    ns: str,
    name: str,
    *,
    description: str = "",
    contract: str = "",
    status: str = "proposed",
    bundle_id: str = "",
) -> dict:
    """Write one bundle row: projects that must honour one contract (SPEC-002 section 1).

    `status` is a closed set the engine switches on, so an unknown one is refused by name rather
    than stored. `contract` points at where the spanning spec lives TODAY (a path), or is empty
    when the bundle is real in the code and unwritten on paper — the honest shape of a bundle
    whose members share a contract nobody has written down.
    """
    if status not in BUNDLE_STATUSES:
        raise SystemExit(
            f"unknown bundle status {status!r}: expected one of {', '.join(BUNDLE_STATUSES)}"
        )
    if not name:
        raise SystemExit(
            "refused: a bundle needs a name — the row is the only place its members are readable"
        )
    row = {
        "id": bundle_id or next_id(ns, "bundle", "B"),
        "name": name,
        "description": description,
        "contract": contract,
        "status": status,
    }
    insert(ns, "bundle", row)
    return row


def bundle_exists(ns: str, bundle_id: str) -> bool:
    """Does this bundle row actually exist? A member of a bundle that does not is not stored."""
    if not bundle_id:
        return False
    return bool(select(ns, "bundle", f"id=eq.{bundle_id}&select=id&limit=1"))


def bundle_member(
    ns: str, bundle_id: str, project: str, role: str, *, note: str = ""
) -> dict:
    """Write one membership row: what a project owes or expects inside a bundle.

    Membership is a TABLE and not a column because a project can belong to several bundles
    (SPEC-002 section 1). Three refusals, all of them the rule `edge` already enforces on its
    endpoints — a row naming something that does not exist reads as a fact nobody can check:

      * `role` must be in the closed set the engine may switch on;
      * the bundle must already exist;
      * the project must be one the FLEET has (the scheduler's `projects` table, read-only). Where
        no scheduler DB can be read at all, membership cannot be verified and is refused rather
        than guessed. `AUGER_ALLOW_SCRATCH_MEMBERS=1` is the explicit standalone escape: it skips
        only this scheduler check and prints a warning naming the member namespace convention.

    A second row for the same (bundle, project) is the same membership written twice, so it is
    refused too: the walk in SPEC-002 section 2 counts members, and a doubled member is a doubled
    blast radius.
    """
    if role not in BUNDLE_ROLES:
        raise SystemExit(
            f"unknown bundle role {role!r}: expected one of {', '.join(BUNDLE_ROLES)}"
        )
    if not bundle_exists(ns, bundle_id):
        raise SystemExit(
            f"refused: bundle {bundle_id!r} does not exist — a member row for a "
            f"missing bundle is not stored"
        )
    allow_scratch = os.environ.get(ALLOW_SCRATCH_MEMBERS_ENV) == "1"
    if allow_scratch:
        print(
            f"WARNING: {ALLOW_SCRATCH_MEMBERS_ENV}=1 skips scheduler project validation for "
            f"{project!r}; this member's rows must live in namespace {member_namespace(project)!r}."
        )
    else:
        projects = known_projects()
        if projects is None:
            override = os.environ.get(SCHEDULER_DB_ENV)
            looked = (
                [os.path.expanduser(override)]
                if override
                else list(SCHEDULER_DB_CANDIDATES)
            )
            raise SystemExit(
                f"refused: cannot verify project {project!r} — no readable scheduler DB "
                f"(looked in {', '.join(looked)}). A bundle member names a project the "
                f"fleet has, so membership is refused rather than guessed; point "
                f"{SCHEDULER_DB_ENV} at a scheduler.db with a `projects` table."
            )
        if project not in projects:
            raise SystemExit(
                f"refused: project {project!r} has no row in the fleet's scheduler "
                f"projects table ({scheduler_db_path()}) — a bundle member names a "
                f"project the fleet has, and this name is not one"
            )
    have = select(
        ns,
        "bundle_member",
        f"bundle_id=eq.{bundle_id}&project=eq.{project}&select=id&limit=1",
    )
    if have:
        raise SystemExit(
            f"refused: {project!r} is already a member of {bundle_id} (row "
            f"{have[0]['id']}) — a second row is the same membership written twice"
        )
    row = {
        "id": next_id(ns, "bundle_member", "BM"),
        "bundle_id": bundle_id,
        "project": project,
        "role": role,
        "note": note,
    }
    insert(ns, "bundle_member", row)
    return row


class SubstrateError(SystemExit):
    """A memory-store call failed to ANSWER (AUG-075): transport dead, non-200.

    A SystemExit subclass so an uncaught raise is still the CLI's clean failure —
    the message on stderr, exit status 1, no traceback — and so `except SystemExit`
    handlers upstream keep working unchanged. Raising (instead of returning a
    sentinel) is what keeps every caller honest: a caller that forgets to handle a
    failed substrate read cannot silently continue with empty-handed data.
    """


def recall(ns: str, q: str, limit: int = 5, missing_ok: bool = False) -> list:
    """Semantic search over a namespace's embedded rows.

    AUG-075, fail closed: "the store has nothing" and "the store cannot be asked"
    are different answers, and only the first is `[]`. A transport failure arrives
    as st == 0 (the `_req` contract, untouched here) and any other refusal as its
    HTTP status; both raise `SubstrateError` naming the substrate's own words. An
    EMPTY store — a real 200 — still returns `[]`, so a caller that sees no rows
    may believe them.

    `missing_ok` is the ONE documented carve-out (SPEC-002 section 2.1): a
    namespace that does not exist — a bundle sibling never started — has no rows
    here, so its 404 reads as `[]` instead of an error. The verbs keep the default
    and stay strict: a user pointing `recall`/`check` at a missing namespace is
    told so, not handed silence.
    """
    st, body, _ = db(
        f"/api/memories?namespace={url_seg(ns)}&q={quote(q)}&limit={limit}", timeout=60
    )
    if st != 200:
        if missing_ok and st == 404:
            return []
        detail = body.get("error", body) if isinstance(body, dict) else body
        raise SubstrateError(
            f"substrate read failed for {ns!r} (status {st}): {detail} — a failed "
            "read is NOT an empty store"
        )
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
        found = select_or_empty(
            ns, "bundle", f"id=eq.{bid}&select=id,name,status&limit=1"
        )
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
        for m in select_or_empty(
            ns, "bundle_member", f"bundle_id=eq.{b['id']}&order=id.asc"
        ):
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
    contribute NOTHING rather than an invented neighbour. A sibling namespace that does not exist
    yet (a 404, AUG-075's `missing_ok` carve-out) is exactly that "nothing"; a substrate that
    cannot be ANSWERED still raises, because a dead store must not read as an empty one.
    """
    out: list = []
    for where in places:
        for hit in recall(where, q, limit=limit, missing_ok=True):
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
        st, body, _ = _req(
            JEV_URL,
            "POST",
            {"model": JEV_MODEL, "state": state, "questions": questions},
            {"Authorization": f"Bearer {k}"},
            timeout=90,
        )
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
    "Never repeat a question that is already open."
)
QUESTION_MAX = 400  # a proposed question longer than this is a document, not a question
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
    text = (first.get("message") or {}).get("content") or ""
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
    body = {
        "model": proposer_model(),
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": PROPOSER_SYSTEM},
            {"role": "user", "content": state},
        ],
    }
    last = "no attempt"
    for k in keys:
        st, resp, _ = _req(
            PROPOSER_URL, "POST", body, {"Authorization": f"Bearer {k}"}, timeout=90
        )
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
    return {
        q["id"]: q
        for q in select(ns, "question", f"project_id=eq.{project_id}&order=id.asc")
    }


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
        if (
            k == "derives_from"
            and e.get("src_kind") == "question"
            and e.get("dst_kind") == "question"
        ):
            idx["children"].setdefault(e["dst_id"], []).append(e["src_id"])
        elif (
            k == "blocks"
            and e.get("src_kind") == "question"
            and e.get("dst_kind") == "question"
        ):
            idx["blockers"].setdefault(e["src_id"], []).append(e["dst_id"])
        elif (
            k == "closes"
            and e.get("src_kind") == "decision"
            and e.get("dst_kind") == "question"
        ):
            idx["closed"].setdefault(e["src_id"], []).append(e["dst_id"])
        elif (
            k == "breaks"
            and e.get("dst_kind") == "decision"
            and not e.get("dst_project")
        ):
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


def set_state(
    ns: str,
    project_id: str,
    question_id: str,
    status: str,
    reason: str,
    *,
    closed_by: str = "",
) -> dict:
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
        raise SystemExit(
            f"unknown question state {status!r}: expected one of {', '.join(QUESTION_STATES)}"
        )
    fields = {
        "status": "open" if status == "open" else "closed",
        "closed_by": "" if status == "open" else closed_by,
        "note": reason,
    }
    have = select(ns, "facet", f"question_id=eq.{question_id}&order=id.asc")
    if have:
        for f in have:
            patch(ns, "facet", f["id"], fields)
    else:
        first = int(next_id(ns, "facet", "F").split("-")[1])
        insert(
            ns,
            "facet",
            [
                {
                    "id": f"F-{first + i:0{ID_WIDTH}d}",
                    "project_id": project_id,
                    "question_id": question_id,
                    "facet": name,
                    **fields,
                }
                for i, name in enumerate(facet_set())
            ],
        )
    return patch(ns, "question", question_id, {"status": status})


def close_question(
    ns: str,
    project_id: str,
    decision_id: str,
    question_id: str,
    *,
    warnings: list | None = None,
) -> dict:
    """SPEC-001 BEAT 2: record that ONE decision IS the answer to ONE question.

    Both writes the spec directs, and neither is optional. The `closes` edge is what
    PROPAGATE's walk reads to moot the questions a dead decision answered (`edge_index` indexes
    exactly this src_kind/dst_kind pair), and the state change is what takes the question off the
    askable surface. The reason travels THROUGH set_state, so it lands on the question's facet
    rows before the status flips and the change cannot happen without it — a closed question
    whose facets say nothing is one nobody can explain later.

    A question that is NOT STORED is refused BY NAME, the way `facet()` and `edge()` refuse a
    missing node. `--question-id` was accepted verbatim and never checked, so a typo used to store
    a link to nothing; a link to nothing reads as evidence that a question was answered.
    """
    if not node_exists(ns, "question", question_id):
        raise SystemExit(
            f"refused: question {question_id!r} does not exist — a decision cannot "
            f"close a question that is not stored"
        )
    wrote = edge(
        ns,
        project_id,
        "closes",
        "decision",
        decision_id,
        "question",
        question_id,
        source="rule",
        note=f"BEAT 2: decision {decision_id} is the answer to {question_id}",
        warnings=warnings,
    )
    set_state(
        ns,
        project_id,
        question_id,
        "answered",
        f"answered by {decision_id}",
        closed_by=decision_id,
    )
    return wrote


def declared_columns(ns: str, table: str) -> list:
    """The columns the SERVER declares for a table — [] when it cannot answer.

    Read from the API's own declaration rather than from COLS: what a reader is allowed to filter
    on is the running server's answer. A namespace declared before a column existed keeps serving
    the old shape (the whole reason `insert_decision` and `edge` send absent keys), and the API
    refuses a filter on a column it does not have.
    """
    st, body, _ = db(ns_tables_path(ns))
    if st != 200 or not isinstance(body, dict):
        return []
    for t in body.get("tables", []):
        if isinstance(t, dict) and t.get("name") == table:
            return [
                c.get("name") for c in (t.get("columns") or []) if isinstance(c, dict)
            ]
    return []


def project_questions(ns: str, project_id: str) -> list:
    """The project's stored question rows, whatever this namespace's `question` declaration holds.

    `question.project_id` is the direct filter and the one every namespace this engine declares
    serves. A declaration that predates the column cannot be asked for it, and the fallback is a
    real JOIN rather than a shrug: the question's own facet rows name both ids, so the project is
    resolved through rows that carry it instead of reading every question of a shared namespace as
    this project's.
    """
    cols = declared_columns(ns, "question")
    if cols and "project_id" not in cols:
        ids = {
            f.get("question_id")
            for f in select_or_empty(ns, "facet", f"project_id=eq.{project_id}")
        }
        return [
            q
            for q in select_or_empty(ns, "question", "order=id.asc")
            if q.get("id") in ids
        ]
    return select(ns, "question", f"project_id=eq.{project_id}&order=id.asc")


def branch_depth(questions: list, edges: list) -> int:
    """The longest question -> question chain in a project's stored graph — 0 with no questions.

    Depth follows DEPENDENCY, never arrival order (SPEC-001 section 5). A question that
    `derives_from` another sits one level below it, and a question that `blocks` on another cannot
    be asked until that one is answered, so it sits below it too. `opens` is deliberately NOT a
    depth edge: a decision -> question edge makes a question the child of an ANSWER, and the
    engine records the level it adds as a `derives_from` back to the question that answer closed
    (see `record_followup`) — counting the `opens` edge as well would count one branch twice. A
    question no other question derives from or blocks on is therefore a root, including one an
    answer raised directly.

    Longest-path over a topological order, so a cycle — the graph is written by several rules and
    nothing forbids one — leaves its nodes at ONE level instead of hanging the verb or inventing a
    length for a chain that never ends.
    """
    ids = {q.get("id") for q in questions if q.get("id")}
    kids: dict = {}
    indegree = dict.fromkeys(ids, 0)
    for e in edges:
        if e.get("src_kind") != "question" or e.get("dst_kind") != "question":
            continue
        if e.get("kind") not in ("derives_from", "blocks"):
            continue
        parent, child = e.get("dst_id"), e.get("src_id")
        if parent in ids and child in ids and child not in kids.get(parent, ()):
            kids.setdefault(parent, []).append(child)
            indegree[child] += 1
    depth = dict.fromkeys(ids, 1)
    queue = [q for q, n in indegree.items() if n == 0]
    while queue:
        parent = queue.pop(0)
        for child in kids.get(parent, ()):
            depth[child] = max(depth[child], depth[parent] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return max(depth.values(), default=0)


def unresolved_blockers(idx: dict, qs: dict, question_id: str) -> list:
    """The questions this one cannot be answered until (SPEC-001: the blocks edge).

    A `moot` blocker does NOT unblock its dependents: a withdrawn question was never answered, so
    the dependency it created is still open. A blocker with no stored row is unresolved too — the
    conservative direction, since an edge that cannot be walked backwards must not read as done.
    """
    return [
        b
        for b in idx["blockers"].get(question_id, ())
        if (qs.get(b) or {}).get("status") not in SETTLED_STATES
    ]


def askable_questions(ns: str, project_id: str) -> list:
    """Open questions nothing unresolved is blocking — the surface `ask` may show."""
    idx, qs = edge_index(graph_edges(ns, project_id)), q_states(ns, project_id)
    return [
        q
        for q in qs.values()
        if q.get("status") == "open" and not unresolved_blockers(idx, qs, q["id"])
    ]


def blocked_questions(ns: str, project_id: str) -> list:
    """Open questions held back by an unresolved blocker — counted, never surfaced."""
    idx, qs = edge_index(graph_edges(ns, project_id)), q_states(ns, project_id)
    return [
        q
        for q in qs.values()
        if q.get("status") == "open" and unresolved_blockers(idx, qs, q["id"])
    ]


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
    return (
        did
        if did
        and select(
            ns, "decision", f"id=eq.{did}&project_id=eq.{project_id}&select=id&limit=1"
        )
        else ""
    )


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
    try:
        hits = recall_many([where for where, _ in places], text, limit=limit)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        return None, "", "", str(exc)
    if not hits:
        return None, "", "", None
    evidence = "\n".join(
        f"- {h.get('key')}: {h.get('content', '')[:400]}" for _, h in hits
    )
    ans, err = jev(
        f"QUESTION UNDER CONSIDERATION:\n{text}\n\nSTORED EVIDENCE:\n{evidence}",
        {
            "already_answered": {
                "type": "noul",
                "instructions": "Is the question under consideration ALREADY fully answered by the stored evidence?",
            }
        },
    )
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
            if did and not select_or_empty(
                where, "decision", f"id=eq.{did}&select=id&limit=1"
            ):
                did = ""
            src = next((p for w, p in places if w == where), "")
        if did:
            return v, did, src, None
    return v, "", "", None  # answered, but by evidence that names no decision node


def propagate(
    ns: str, project_id: str, *, gate: bool = True, recheck: bool = False
) -> dict:
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
    rep = {
        "moot": [],
        "reopened": [],
        "linked": [],
        "edges": [],
        "warnings": [],
        "askable": [],
        "blocked": [],
        "unattributed": [],
        "ungated": [],
        "gate_error": None,
    }

    # ---- the rule walk: invalidation -> moot, then down the derives_from tree
    seen, queue = set(), []
    for e in idx["breaks"]:
        dead, root = e["dst_id"], f"{e['dst_id']} was invalidated by {e['id']}"
        for qid in closed_by.get(dead, ()):
            q = qs.get(qid)
            if q is None or q["status"] == "moot":
                continue  # already withdrawn, and it keeps the first reason
            set_state(ns, project_id, qid, "moot", root, closed_by=dead)
            qs[qid]["status"] = "moot"
            seen.add(qid)
            rep["moot"].append((qid, root))
            queue.append((qid, root))
    while queue:
        parent, root = queue.pop(0)
        for child in sorted(idx["children"].get(parent, ())):
            if child in seen:
                continue  # a cycle in derives_from cannot loop the walk
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
            continue  # the trigger is the parent CLOSING
        for child in sorted(kids):
            q = qs.get(child)
            if q is None or q["status"] != "open":
                continue
            if unresolved_blockers(idx, qs, child):
                continue  # blocked questions are not gated, only counted
            if q.get("jev_checked_at") and not recheck:
                continue  # the gate already examined this one
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
            patch(
                ns,
                "question",
                child,
                {
                    "jev_already_answered": -1.0 if verdict is None else verdict,
                    "jev_checked_at": checked_at,
                },
            )
            qs[child]["jev_checked_at"] = checked_at
            if verdict is not None and verdict >= T_ANSWERED:
                if not dec_id:
                    rep["unattributed"].append(child)
                    continue
                # SPEC-002 2.1: when the winning evidence came from a SIBLING's namespace, the link
                # says so twice — on the edge (src_project, which also tells the referential check
                # where the decision actually lives) and in the question's own reason.
                row = edge(
                    ns,
                    project_id,
                    "satisfies",
                    "decision",
                    dec_id,
                    "question",
                    child,
                    confidence=verdict,
                    source="gate",
                    src_project=src_project,
                    warnings=rep["warnings"],
                    note=f"the gate found this question already answered (noul {verdict:.2f})"
                    + (f" by {src_project}'s {dec_id}" if src_project else ""),
                )
                set_state(
                    ns,
                    project_id,
                    child,
                    "linked",
                    f"linked by the gate to {dec_id} (noul {verdict:.2f})"
                    + (f" in {src_project}" if src_project else ""),
                    closed_by=dec_id,
                )
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
    print(
        f"moot: {len(rep['moot'])}   reopened: {len(rep['reopened'])}   linked: {len(rep['linked'])}"
    )
    for qid, reason in rep["moot"]:
        print(f"  moot       {qid}  {reason}")
    for qid, reason in rep["reopened"]:
        print(f"  reopened   {qid}  {reason}")
    for qid, dec_id, v, src in rep["linked"]:
        print(
            f"  linked     {qid}  -> {dec_id} (noul {v:.2f})"
            + (
                f"  from sibling {src} — the bundle was searched (SPEC-002 2.1)"
                if src
                else ""
            )
        )
    for qid in rep["unattributed"]:
        print(
            f"  WARNING    {qid}: the gate says answered, but the evidence names no decision "
            f"— left open rather than linked to nothing"
        )
    for w in rep["warnings"]:
        print(w)
    if rep["edges"]:
        print(f"edges written: {', '.join(rep['edges'])}")
    print(
        f"newly askable: {len(rep['askable'])}   blocked by an unresolved question: {len(rep['blocked'])}"
    )
    texts = q_states(ns, pid)
    for qid in rep["askable"]:
        print(f"  askable    {qid}  {(texts.get(qid, {}).get('text') or '')[:90]}")
    if rep["gate_error"]:
        print(f"WARNING gate unavailable — {rep['gate_error']}")
        print(
            f"        {len(rep['ungated'])} question(s) left open and NOT linked (fail-closed)."
        )
        return 1
    return 0


# ------------------------------------------------- AUG-028: writing the walk's TRIGGER (BEAT 4)
# The rule walk above fires on exactly ONE shape: a `breaks` edge whose dst is a DECISION with no
# `dst_project` (edge_index). That shape had no writer. The impact pass always sets `dst_project` — a
# break reaching a sibling is an escalation for THAT sibling (SPEC-002 2.2), never this project's own
# decision dying — and `option.breaks` is free text that no walk reads. So the cascade SPEC-001 BEAT 4
# describes was reachable only by whoever hand-POSTed an edge row, which is exactly what this project's
# own tests did, and what the dogfood run had to do to prove the walk itself works.
#
# `answer --invalidates D-00X` is that writer, and the claim it records is a PERSON'S: "the decision I
# am recording now invalidates one already on the record" — the same assertion BEAT 3's impact pass
# makes through a model, made here by someone reading the record. It writes the local form and only
# that: no project endpoint is invented, no decision is rewritten, and resolution stays where the spec
# puts it (`propagate`). The write surfaces the trigger; it does not run the walk.
def local_break_targets(
    ns: str, project_id: str, decision_id: str, raw: list | None, why: str = ""
) -> list:
    """The decisions `--invalidates` names — refused by name when one of them cannot be one.

    Three refusals, all of them about the ONE claim this flag makes, that the decision is ALREADY
    STORED IN THIS PROJECT:

      * a decision that is not stored is the invented endpoint `edge` refuses — and "I invalidate a
        decision that has not been recorded yet" is not a claim the walk can act on;
      * a decision recorded by a SIBLING is not reachable from here at all (ids are per namespace), and
        writing it without `dst_project` would read as THIS project's D-007 dying;
      * an answer cannot invalidate ITSELF: the walk moots the questions the dead decision answered, so
        a self-break would withdraw the very question this answer just closed.

    A reason with no target is refused too. `--invalidates-why` describes a break; a reason that lands
    nowhere leaves the user believing something is on the record when nothing was written.

    Called before anything is stored, so a refusal costs no bundle-impact model call and leaves the
    whole invocation — the answer included — unwritten rather than half-applied.
    """
    targets, seen = [], set()
    for raw_id in raw or []:
        target = (raw_id or "").strip()
        if target and target not in seen:
            seen.add(target)
            targets.append(target)
    if why and not targets:
        raise SystemExit(
            "--invalidates-why names no --invalidates target — the reason belongs on a break "
            "edge, and no break was named"
        )
    for target in targets:
        if target == decision_id:
            raise SystemExit(
                f"refused: {decision_id} cannot invalidate itself — the rule walk would moot the "
                f"questions {decision_id} just answered"
            )
        if not select(
            ns,
            "decision",
            f"id=eq.{target}&project_id=eq.{project_id}&select=id&limit=1",
        ):
            raise SystemExit(
                f"refused: decision {target!r} does not exist in project {project_id} — a LOCAL "
                f"break names a decision this project already recorded. A break reaching a "
                f"sibling's decision is the bundle impact pass's edge, which carries dst_project "
                f"(SPEC-002 2.2)"
            )
    return targets


def record_local_breaks(
    ns: str, project_id: str, decision_id: str, targets: list, why: str = ""
) -> list:
    """Write one LOCAL `breaks` edge per target. Returns [(edge id, target)].

    Called after the answer is stored, because `edge` verifies BOTH of its endpoints are rows and the
    answer is one of them. `source="human"` and the default confidence (-1, "asserted directly, not
    scored by a model") are the same pair the impact pass's edges do NOT use: this one is a person's
    read of the record, not a model's. `dst_project` is deliberately never passed — that absent
    endpoint is what makes the edge local, and therefore what makes the rule walk read it as a
    decision of THIS project dying rather than a sibling's.
    """
    written = []
    for target in targets:
        note = f"{decision_id} invalidates {target}"
        if why:
            note += f": {why}"
        wrote = edge(
            ns,
            project_id,
            "breaks",
            "decision",
            decision_id,
            "decision",
            target,
            source="human",
            note=note,
        )
        written.append((wrote["id"], target))
    return written


def supersede_targets(
    ns: str, project_id: str, decision_id: str, raw: list | None, why: str = ""
) -> list:
    """Validate `--supersedes` targets before the answer writes anything."""
    targets, seen = [], set()
    for raw_id in raw or []:
        target = (raw_id or "").strip()
        if target and target not in seen:
            seen.add(target)
            targets.append(target)
    if why and not targets:
        raise SystemExit(
            "--supersedes-why names no --supersedes target — the reason belongs on a supersedes "
            "edge, and no supersession was named"
        )
    for target in targets:
        if target == decision_id:
            raise SystemExit(
                f"refused: {decision_id} cannot supersede itself — a new answer must replace "
                "an older decision"
            )
        if not select(
            ns,
            "decision",
            f"id=eq.{target}&project_id=eq.{project_id}&select=id&limit=1",
        ):
            raise SystemExit(
                f"refused: decision {target!r} does not exist in project {project_id} — "
                "a supersession names a decision this project already recorded"
            )
    return targets


def record_supersessions(
    ns: str,
    project_id: str,
    decision_id: str,
    targets: list,
    why: str,
) -> list:
    """Record supersession edges, status flips, and the old decisions' facet reasons."""
    written = []
    for target in targets:
        note = f"{decision_id} supersedes {target}"
        if why:
            note += f": {why}"
        wrote = edge(
            ns,
            project_id,
            "supersedes",
            "decision",
            decision_id,
            "decision",
            target,
            source="human",
            note=note,
        )
        patch(ns, "decision", target, {"status": SUPERSEDED_STATUS})
        idx = edge_index(graph_edges(ns, project_id))
        questions = questions_closed_by(ns, project_id, idx).get(target, [])
        reason = f"superseded by {decision_id}"
        if why:
            reason += f": {why}"
        fields = {"status": "closed", "closed_by": decision_id, "note": reason}
        for question_id in questions:
            have = select(ns, "facet", f"question_id=eq.{question_id}&order=id.asc")
            if have:
                for facet_row in have:
                    patch(ns, "facet", facet_row["id"], fields)
            else:
                first = int(next_id(ns, "facet", "F").split("-")[1])
                insert(
                    ns,
                    "facet",
                    [
                        {
                            "id": f"F-{first + i:0{ID_WIDTH}d}",
                            "project_id": project_id,
                            "question_id": question_id,
                            "facet": name,
                            **fields,
                        }
                        for i, name in enumerate(facet_set())
                    ],
                )
        written.append((wrote["id"], target, questions))
    return written


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
FOLLOWUP_CLASS = "follow_up"  # the `qclass` a question opened by this engine carries


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
        raise SystemExit(
            f"refused: {BUDGET_ENV}={raw!r} is negative — a ceiling cannot be"
        )
    return n


def thin_decisions(ns: str, project_id: str) -> list:
    """The decisions that need drilling, THINNEST FIRST — criterion (a) of this row.

    A decision is thin when its confidence is MEASURED and below `T_CONFIDENT`, or when it is
    UNMEASURED at all (the -1 sentinel `answer` stores for "asserted directly, not scored by a
    model" — AUG-062: an unmeasured decision is the thinnest evidence there is, not a passing
    grade, and hiding it from this list let `feedback` report every decision as confident while
    one had never been scored). Ordered measured-first by confidence, then the unmeasured ones
    by id, so two runs over an unchanged project drill it in the same order.
    """
    rows = select(
        ns, "decision", f"project_id=eq.{project_id}&order=confidence.asc,id.asc"
    )
    measured_thin = [
        d
        for d in rows
        if isinstance(d.get("confidence"), (int, float))
        and 0 <= d["confidence"] < T_CONFIDENT
    ]
    unmeasured = [d for d in rows if _is_unmeasured(d)]
    return measured_thin + sorted(unmeasured, key=lambda d: d["id"])


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
    for e in select(
        ns,
        "edge",
        f"project_id=eq.{project_id}&kind=eq.closes&src_id=eq.{dec_row['id']}",
    ):
        if (
            e.get("src_kind") == "decision"
            and e.get("dst_kind") == "question"
            and node_exists(ns, "question", e["dst_id"])
        ):
            return e["dst_id"]
    return ""


def feedback_state(
    proj: dict, dec_row: dict, parent_text: str, open_questions: list
) -> str:
    """Everything the proposer needs to write ONE specific question, and nothing else.

    Deliberately narrow. The proposer is told about the SEED (what the project is), the DECISION
    whose confidence is too low (what is thin), the question that decision answered (where the
    branch came from) and the questions already open (so it does not write a duplicate). It is not
    shown the stored evidence: the gate reads that, and a proposer fed the answer tends to restate
    it as a question.
    """
    lines = [
        f"PROJECT SEED:\n{(proj.get('seed') or '')[:800]}",
        "",
        "THE DECISION WHOSE CONFIDENCE IS TOO LOW TO BUILD ON:",
        f"{dec_row['id']} ({dec_row.get('domain') or 'no domain'}): {dec_row.get('chosen')}",
        f"confidence: {_conf_render(dec_row.get('confidence'))}",
        f"reversal cost: {dec_row.get('reversal_cost') or 'not recorded'}",
        f"why the rejected alternatives lost: {dec_row.get('why_not') or 'not recorded'}",
    ]
    if parent_text:
        lines += ["", f"THE QUESTION THIS DECISION ANSWERED: {parent_text}"]
    if open_questions:
        lines += ["", "QUESTIONS ALREADY OPEN IN THIS PROJECT (never repeat one):"]
        lines += [
            f"- {q['id']}: {(q.get('text') or '')[:140]}" for q in open_questions[:8]
        ]
    return "\n".join(lines)


def record_followup(
    ns: str,
    project_id: str,
    dec_row: dict,
    text: str,
    status: str,
    *,
    parent_qid: str = "",
    noul: float | None = None,
    checked_at: str = "",
    reason: str = "",
    closed_by: str = "",
    warnings: list | None = None,
) -> dict:
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
        prows = select_or_empty(
            ns, "question", f"id=eq.{parent_qid}&select=ring&limit=1"
        )
        ring = int(prows[0].get("ring") or 0) + 1 if prows else 1
    row = {
        "id": qid,
        "project_id": project_id,
        "domain": dec_row.get("domain") or "",
        "text": text,
        "ring": ring,
        "qclass": FOLLOWUP_CLASS,
        "status": "open",
        "jev_already_answered": -1.0 if noul is None else float(noul),
        "jev_checked_at": checked_at,
    }
    insert(ns, "question", row)
    for name in facet_set():
        facet(ns, project_id, qid, name)
    edges = [
        edge(
            ns,
            project_id,
            "opens",
            "decision",
            dec_row["id"],
            "question",
            qid,
            source="rule",
            warnings=warnings,
            note=f"the confidence in {dec_row['id']} is "
            f"{_conf_render(dec_row.get('confidence'))} "
            f"(< {T_CONFIDENT}), so the engine proposed a follow-up "
            f"({proposer_model()})",
        )["id"]
    ]
    if parent_qid:
        edges.append(
            edge(
                ns,
                project_id,
                "derives_from",
                "question",
                qid,
                "question",
                parent_qid,
                source="rule",
                warnings=warnings,
                note=f"{qid} drills {dec_row['id']}, which answers {parent_qid}",
            )["id"]
        )
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
        raise SystemExit(
            f"refused: a question ceiling of {ceiling} is negative — a ceiling cannot be"
        )
    proj = _project(ns, project_id)
    pid = proj["id"]
    rep = {
        "ceiling": ceiling,
        "thin": [],
        "drilled": [],
        "proposed": [],
        "asked": [],
        "refused": [],
        "budget_thin": [],
        "unattributed": [],
        "unproposed": [],
        "ungated": [],
        "escalations": [],
        "edges": [],
        "askable": [],
        "warnings": [],
        "proposer_error": None,
        "gate_error": None,
    }

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
    # AUG-062: the floor is a judgment about a MEASURED confidence. An unmeasured decision (the
    # -1 sentinel) is thin and is drilled below like any other, but it never FAILED the floor —
    # nobody scored it — so escalating "-1.00 below the 0.30 floor" would state a weighing that
    # was never made. The sentinel never reaches this branch.
    for d in cand:
        if not _is_unmeasured(d) and d["confidence"] < T_PRIORITY:
            esc = escalate(
                ns,
                pid,
                question=f"priority judgment: {d['id']} has confidence "
                f"{d['confidence']:.2f}, below the {T_PRIORITY} floor — "
                f"{d.get('chosen')} rests on a weighing only you can make",
                options=d["id"],
                default_action=f"continue ON THE DEFAULT: the run drills {d['id']} "
                f"first and does not wait for an answer",
                risk=f"every decision resting on {d['id']} inherits a confidence of "
                f"{d['confidence']:.2f}",
            )
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
        gated.append(
            {
                "dec": d,
                "text": text,
                "parent": parent,
                "noul": verdict,
                "dec_id": dec_id,
                "src_project": src_project,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if not gated:
        rep["askable"] = [q["id"] for q in askable_questions(ns, pid)]
        return rep

    # ---- 5. rank (thinnest decision first, then the question the gate judged LEAST answered — a
    # noul of None means no stored evidence could answer it at all, which is the most unanswered
    # there is) and ask while the ceiling lasts; the rest are RECORDED, not dropped.
    ranked = sorted(
        gated,
        key=lambda g: (
            _conf(g["dec"]),
            -1.0 if g["noul"] is None else g["noul"],
            g["dec"]["id"],
        ),
    )
    left = ceiling
    for g in ranked:
        d, text, noul = g["dec"], g["text"], g["noul"]
        shared = {
            "parent_qid": g["parent"],
            "noul": noul,
            "checked_at": g["checked_at"],
            "warnings": rep["warnings"],
        }
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
                    f"not linked, not stored."
                )
                continue
            where = f" in {g['src_project']}" if g["src_project"] else ""
            rec = record_followup(
                ns,
                pid,
                d,
                text,
                "linked",
                reason=f"refused: the gate found this already answered by "
                f"{g['dec_id']} (noul {noul:.2f}){where}",
                **shared,
                closed_by=g["dec_id"],
            )
            sat = edge(
                ns,
                pid,
                "satisfies",
                "decision",
                g["dec_id"],
                "question",
                rec["row"]["id"],
                confidence=noul,
                source="gate",
                src_project=g["src_project"],
                warnings=rep["warnings"],
                note=f"the gate found this question already answered by {g['dec_id']}"
                f" (noul {noul:.2f}){where}",
            )
            rec["edges"].append(sat["id"])
            rep["refused"].append(
                (rec["row"]["id"], g["dec_id"], noul, g["src_project"])
            )
        elif left > 0:
            rec = record_followup(ns, pid, d, text, "open", **shared)
            left -= 1
            rep["asked"].append((rec["row"]["id"], d["id"], noul, text))
        else:
            rec = record_followup(
                ns,
                pid,
                d,
                text,
                "budget_thin",
                reason=f"budget-thin: the run's ceiling of {ceiling} question(s) was reached — "
                f"recorded, NOT asked",
                **shared,
            )
            rep["budget_thin"].append(
                (
                    rec["row"]["id"],
                    d["id"],
                    f"budget-thin: the run's ceiling of {ceiling} question(s) was reached — "
                    f"recorded, NOT asked",
                )
            )
        rep["edges"] += rec["edges"]

    # ---- 6. the run-level marker: the ceiling WAS hit, and that is on the record too.
    if rep["budget_thin"]:
        esc = escalate(
            ns,
            pid,
            question=f"budget-thin: true — the run hit its ceiling of {ceiling} "
            f"question(s) with {len(rep['budget_thin'])} proposed question(s) "
            f"recorded but NOT asked",
            options=", ".join(q for q, _, _ in rep["budget_thin"]),
            default_action="the questions keep their rows in state budget_thin and are "
            "asked on a later run",
            risk="those branches stay thin until then: an honest shallow branch, never "
            "a silent one",
        )
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
        print(f"  {did}  {_conf_render(conf)}  <- thinnest first")
    for did, qid in rep["drilled"]:
        print(f"  {did}  already drilled: {qid} is open and awaiting an answer")
    print(
        f"ceiling: {rep['ceiling']} question(s) this run   "
        f"proposed: {len(rep['proposed'])}   asked: {len(rep['asked'])}   "
        f"refused: {len(rep['refused'])}   budget-thin: {len(rep['budget_thin'])}"
    )
    for qid, did, noul, text in rep["asked"]:
        print(f"  ASKED       {qid}  <- {did}  {text}")
    for qid, did, _reason in rep["budget_thin"]:
        print(
            f"  BUDGET-THIN {qid}  <- {did}  recorded, NOT asked (ceiling {rep['ceiling']})"
        )
    for qid, did, noul, src in rep["refused"]:
        print(
            f"  REFUSED     {qid}  already answered by {did} (noul {noul:.2f})"
            + (f" in {src}" if src else "")
            + " — NOT asked"
        )
    for did, text, noul in rep["unattributed"]:
        print(
            f"  UNLINKED    <- {did}  the gate says answered (noul {noul:.2f}) but the evidence "
            f"names no decision — not asked: {text}"
        )
    for line in rep["warnings"]:
        print(line)
    if rep["escalations"]:
        print(
            f"escalations: {', '.join(rep['escalations'])}  "
            f"(questions only a person can weight, each with a default — the run continued on it)"
        )
    for qid in rep["askable"]:
        print(f"  askable     {qid}")
    if rep["edges"]:
        print(f"edges written: {', '.join(rep['edges'])}")
    if rep["budget_thin"]:
        print(
            f"budget-thin: true — {len(rep['budget_thin'])} question(s) recorded but NOT asked "
            f"(ceiling {rep['ceiling']})"
        )
    if rep["proposer_error"]:
        print(f"WARNING proposer unavailable — {rep['proposer_error']}")
        print(
            f"        {len(rep['unproposed'])} decision(s) left unproposed: "
            f"{', '.join(rep['unproposed'])}"
        )
        print("        (fail-closed: no question is invented without the model.)")
        return 1
    if rep["gate_error"]:
        print(f"WARNING gate unavailable — {rep['gate_error']}")
        print(
            f"        {len(rep['ungated'])} proposed question(s) were NOT asked and NOT stored "
            f"(fail-closed):"
        )
        for did, text in rep["ungated"]:
            print(f"          {did}  {text}")
        return 1
    return 0


# ---------------------------------------------------------------- the 44-domain grid (AUG-004)
# The method's grid is EXACTLY 44 domains, and its coverage gate counts what a run reached against
# that canonical set (spec-decomposition-matrix-tradeoff, section 4 and the coverage gate in 7.6):
# a run that interrogates 43 domains and leaves the 44th silent FAILS, "because silence and 'this
# does not apply' are different claims". The `domain` table was declared and empty until this
# landed, so the program could make neither claim: it held no grid, `status` counted zero domains,
# and the 44-domain rule was enforced nowhere but in prose. Seeding the grid as rows is what turns
# that rule into something a program can fail.
#
# THE GRID IS READ, NEVER COPIED. The canonical files live with the skill, and a copy in this repo
# would drift the moment the skill's grid changes — a drifted copy is worse than no copy, because
# it would report 44 domains while the method defines others. So the seeder reads the 44 files,
# refuses a grid that is not exactly the canonical set (4.01-4.44), and refuses a file whose header
# it cannot parse: a guessed triage score is a fabricated one, and the whole point of these rows is
# that the numbers on them were read rather than asserted.
DOMAIN_GRID_ENV = "AUGER_DOMAIN_GRID"
DOMAIN_GRID_DIR = os.path.join(
    os.path.expanduser("~"),
    ".hermes",
    "skills",
    "software-development",
    "spec-decomposition-matrix-tradeoff",
    "references",
    "dogfood-artifact",
    "02-domains",
)
DOMAIN_GRID_SIZE = (
    44  # the method's rule, not a preference: 43 files is the named failure
)
DOMAIN_GRID_HEAD_LINES = (
    14  # both live headers sit in the first 3 lines; 14 leaves room to move
)
DOMAIN_ID_PREFIX = "DM"  # D- is the decision register; a domain row is not a decision
# Every seeded row's state, and the ONE reason it holds it. The `domain` table has no `reason`
# column (AUG-004 fixes COLS), and every seeded row is NOT-REACHED for the same reason — the
# program holds no answer in the domain yet — so the report STATES that sentence once instead of
# storing 44 copies of it. owner/trigger/containment are the same kind of default: the grid's own
# files name a design-run owner for some domains, but this program has assigned nobody, and a row
# that claimed an owner it never assigned would read as a commitment.
DOMAIN_SEED_STATUS = "NOT-REACHED"
DOMAIN_SEED_REASON = "not reached: no rows yet"
DOMAIN_SEED_OWNER = "unassigned"
DOMAIN_SEED_TRIGGER = "first question in domain"
DOMAIN_SEED_CONTAINMENT = "none"

# The file name IS the number and the name: `4.05-data.md` -> num 4.05, name data. Zero-padded to
# two digits, which the method requires precisely so a glob and a count are deterministic (unpadded
# `4.1` and padded `4.10` sort and match inconsistently).
DOMAIN_FILE_RE = re.compile(r"^(?P<num>\d+\.\d{2})-(?P<name>[a-z0-9][a-z0-9-]*)\.md$")
# TWO HEADER SHAPES live in the grid (all 44 read at AUG-004: 9 inline, 35 key/value), and each is
# matched BY NAME rather than by a loose "find some numbers on line 3" scan, because a header this
# half-reads is a score it invented:
#
#   * the inline one-liner of a domain the design run CASCADED (9 files):
#       Triage: 3×3×3=27; floor 5; status CASCADED; terminating ring 5.
#     `unconditional floor 5` instead of `floor 5` is the SAME header for a domain whose ring-5
#     floor is unconditional from the grid (the ⊕ marker; 4.12 in the live grid) — accepted, and it
#     fills the same field.
#   * the key/value block of a domain the run did NOT open (35 files):
#       status: NOT-REACHED / triage: 2×1×3=6 / ring_floor: 2 / ... / terminating_ring: null — ...
#     There `terminating_ring` is the WORD null, because the domain was never opened. That value
#     travels as an ABSENT key on the stored row: the substrate REFUSES a null in an integer column
#     (422, measured), and 0 would be a claim about a ring that was never reached.
DOMAIN_INLINE_HEADER_RE = re.compile(
    r"^Triage:\s*(?P<a>\d+)\s*[x×]\s*(?P<b>\d+)\s*[x×]\s*(?P<c>\d+)\s*=\s*(?P<score>\d+)\s*;"
    r"\s*(?:unconditional\s+)?floor\s+(?P<floor>\d+)\s*;\s*status\s+[A-Z][A-Z-]*\s*;\s*"
    r"terminating\s+ring\s+(?P<ring>\d+)\s*\.?\s*$",
    re.IGNORECASE,
)
DOMAIN_KV_TRIAGE_RE = re.compile(
    r"^triage:\s*(?P<a>\d+)\s*[x×]\s*(?P<b>\d+)\s*[x×]\s*(?P<c>\d+)\s*=\s*(?P<score>\d+)\s*$",
    re.IGNORECASE,
)
DOMAIN_KV_FLOOR_RE = re.compile(r"^ring_floor:\s*(?P<floor>\d+)", re.IGNORECASE)
DOMAIN_KV_RING_RE = re.compile(
    r"^terminating_ring:\s*(?P<ring>null|none|\d+)", re.IGNORECASE
)


def domain_grid_dir() -> str:
    """Where the method's 44 grid files are. `AUGER_DOMAIN_GRID` wins, for a host that moved them."""
    return os.environ.get(DOMAIN_GRID_ENV) or DOMAIN_GRID_DIR


def domain_numbers() -> list:
    """The canonical number set the coverage gate counts against: 4.01 … 4.44."""
    return [f"4.{n:02d}" for n in range(1, DOMAIN_GRID_SIZE + 1)]


def _triage_product(path: str, hit) -> int:
    """The triage score, after proving the header's own arithmetic. The product IS the score.

    `blast_radius × uncertainty × irreversibility`, each 1-3, integer, score >= 18 -> ring-5 floor.
    A header whose factors do not multiply to its stated score is a corrupt grid line, and taking
    the number on the right-hand side of it would store a score nothing else in the file supports.
    """
    a, b, c, score = (int(hit.group(k)) for k in ("a", "b", "c", "score"))
    if a * b * c != score:
        raise SystemExit(
            f"domain grid: {path} triage {a}×{b}×{c}={score} does not multiply — "
            "refusing to store a score the file's own factors contradict"
        )
    return score


def _grid_head_match(path: str, head: list, pattern, field: str):
    """The first header line this pattern reads, or a REFUSAL naming the file and the field."""
    for line in head:
        hit = pattern.match(line)
        if hit:
            return hit
    raise SystemExit(
        f"domain grid: {path} has no readable `{field}` header in its first "
        f"{DOMAIN_GRID_HEAD_LINES} lines — refusing to guess a grid number (build the grid's own "
        "file shape, or teach parse_domain_file the new shape)"
    )


def parse_domain_file(path: str) -> dict:
    """One grid file -> {num, name, triage, ring_floor, terminating_ring}.

    `terminating_ring` is None when the file records that the domain was never opened. That is a
    VALUE, not a missing one — "the domain was not reached" is exactly the distinction the coverage
    report exists to keep — and it is why the None is carried through instead of being flattened to
    a zero somewhere in the middle.

    A header neither live shape reads raises, naming the file and the field: the seeder stops rather
    than storing a guess.
    """
    m = DOMAIN_FILE_RE.match(os.path.basename(path))
    if not m:
        raise SystemExit(
            f"domain grid: {path} is not named 4.NN-<slug>.md — that name IS the domain's number "
            "and its slug (section 4: files are always zero-padded, 4.05-data.md)"
        )
    with open(path, errors="replace") as fh:
        head = [
            line.strip() for line in fh.read().splitlines()[:DOMAIN_GRID_HEAD_LINES]
        ]
    inline = None
    for line in head:
        inline = DOMAIN_INLINE_HEADER_RE.match(line)
        if inline:
            break
    if inline:
        triage = _triage_product(path, inline)
        ring_floor = int(inline.group("floor"))
        terminating_ring = int(inline.group("ring"))
    else:
        triage = _triage_product(
            path, _grid_head_match(path, head, DOMAIN_KV_TRIAGE_RE, "triage")
        )
        ring_floor = int(
            _grid_head_match(path, head, DOMAIN_KV_FLOOR_RE, "ring_floor").group(
                "floor"
            )
        )
        word = _grid_head_match(
            path, head, DOMAIN_KV_RING_RE, "terminating_ring"
        ).group("ring")
        terminating_ring = None if word.lower() in ("null", "none") else int(word)
    if not 1 <= triage <= 27:
        raise SystemExit(
            f"domain grid: {path} triage {triage} is outside 1-27 (3 × 3 × 3)"
        )
    if ring_floor < 1:
        raise SystemExit(
            f"domain grid: {path} ring_floor {ring_floor} is not a ring number"
        )
    return {
        "num": m.group("num"),
        "name": m.group("name"),
        "triage": triage,
        "ring_floor": ring_floor,
        "terminating_ring": terminating_ring,
    }


def read_domain_grid(grid_dir: str | None = None) -> list:
    """The method's 44 domains, parsed — refused unless they are EXACTLY the canonical set.

    The coverage gate's rule, enforced where it can fail a run instead of asserted in prose: a grid
    missing a domain, holding an unexpected one, or naming two files for one number is refused WITH
    THE NUMBERS NAMED, because a seeder that seats 43 domains and says nothing has reproduced
    exactly the silence the rule exists to forbid.
    """
    d = grid_dir or domain_grid_dir()
    if not os.path.isdir(d):
        raise SystemExit(
            f"domain grid not found: {d} — point {DOMAIN_GRID_ENV} at the skill's "
            "references/dogfood-artifact/02-domains directory"
        )
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    entries = [parse_domain_file(os.path.join(d, f)) for f in files]
    got = [e["num"] for e in entries]
    want = domain_numbers()
    missing = [n for n in want if n not in got]
    extra = sorted({n for n in got if n not in want})
    dupes = sorted({n for n in got if got.count(n) > 1})
    if missing or extra or dupes:
        raise SystemExit(
            f"domain grid {d} is not the canonical {DOMAIN_GRID_SIZE} domains "
            f"({want[0]}-{want[-1]}): {len(files)} file(s); missing {missing or 'none'}; "
            f"unexpected {extra or 'none'}; duplicated {dupes or 'none'}"
        )
    return entries


def _latest_project_id(ns: str) -> str:
    """The namespace's most recent project id, or "" when it has none yet.

    `init` is the verb that seeds the grid and also the verb that runs BEFORE `start`, so "no
    project yet" is the ordinary case rather than an error: the rows are then stored UNBOUND
    (`project_id` empty) and `domain_coverage` reads them for whichever project the namespace has.
    """
    rows = select(ns, "project", "order=created_at.desc&limit=1")
    return (rows[0].get("id") or "") if rows else ""


def seed_domains(ns: str, project_id: str = "", grid_dir: str | None = None) -> dict:
    """Seed the grid as `domain` rows for this project, idempotently. Returns the tally.

    IDEMPOTENT BY READ-BEFORE-WRITE, not by a unique index: the substrate enforces nothing, so the
    check is ours — every stored `domain` row is read ONCE and a number already present is skipped
    rather than written again. The read is the whole table and the project filter happens in PYTHON
    for a measured reason: the API cannot filter on an empty value (`project_id=eq.` matches
    nothing), and the rows a pre-`start` seeding writes carry exactly that empty value, so a
    query-shaped filter would have found no rows and re-seeded all 44 on every run.

    The 44 rows go in ONE insert (one request, not 44): three trip a rate limiter where one does
    not, and a half-seeded grid is worse than an unseeded one — it reads as a coverage result.
    """
    grid = read_domain_grid(grid_dir)
    bind = project_id or _latest_project_id(ns)
    have = set()
    for r in select(ns, "domain", ""):
        proj = r.get("project_id")
        if proj and proj != bind:
            continue  # a sibling project's slice of the same 44 numbers
        num = str(r.get("num") or "")
        if num:
            have.add(num)
    missing = [e for e in grid if e["num"] not in have]
    if not missing:
        return {"grid": len(grid), "written": 0, "present": len(grid), "ids": []}
    start = int(
        re.search(r"(\d+)\s*$", next_id(ns, "domain", DOMAIN_ID_PREFIX)).group(1)
    )
    rows = []
    for offset, entry in enumerate(missing):
        row = {
            "id": f"{DOMAIN_ID_PREFIX}-{start + offset:0{ID_WIDTH}d}",
            "project_id": bind,
            "num": entry["num"],
            "name": entry["name"],
            "triage": entry["triage"],
            "ring_floor": entry["ring_floor"],
            "status": DOMAIN_SEED_STATUS,
            "owner": DOMAIN_SEED_OWNER,
            "trigger": DOMAIN_SEED_TRIGGER,
            "containment": DOMAIN_SEED_CONTAINMENT,
        }
        if entry["terminating_ring"] is not None:
            row["terminating_ring"] = entry["terminating_ring"]
        rows.append(row)
    insert(ns, "domain", rows)
    return {
        "grid": len(grid),
        "written": len(rows),
        "present": len(grid) - len(rows),
        "ids": [r["id"] for r in rows],
    }


def domain_coverage(ns: str, project_id: str) -> dict:
    """Per-domain coverage for one project: the grid, the rows, and what the rows do NOT cover.

    ABSENCE IS A CLAIM, and this is where `status` earns the right to make it: a grid number with
    no stored `domain` row is carried as ABSENT and printed by NAME — never dropped, because "we
    have no row for 4.21" and "4.21 does not apply" are different sentences and only the first one
    is true. The expected set is READ (not remembered) so a grid that cannot be read is reported as
    unreadable instead of silently becoming 44 absences.

    A stored number the grid does not define is carried too: a coverage count that cannot see a row
    is not a count. "answered" is EVIDENCE, not a status word — a domain is answered when a decision
    or a question row carries its number, so the report never depends on a status write nobody
    performed.
    """
    mine: dict = {}
    nameless: list = []
    for r in select(ns, "domain", ""):
        proj = r.get("project_id")
        if proj and proj != project_id:
            continue  # a sibling project's slice of the same numbers
        num = str(r.get("num") or "")
        if not num:
            # A row no grid number can be matched to: it is not coverage of anything, and it is
            # named rather than dropped — a report that hides a row is the failure this block is
            # for, whichever end of it the row falls off.
            nameless.append(str(r.get("id") or "?"))
        elif num not in mine or proj == project_id:
            mine[num] = r  # a row BOUND to this project outranks an unbound one
    counts: dict = {}
    for rows_ in (
        select(ns, "decision", f"project_id=eq.{project_id}"),
        project_questions(ns, project_id),
    ):
        for r in rows_:
            num = str(r.get("domain") or "")
            if num:
                counts[num] = counts.get(num, 0) + 1
    grid, grid_error = [], ""
    try:
        grid = read_domain_grid()
    except (SystemExit, OSError) as exc:
        # A grid this host cannot read is a grid this report cannot judge absence against. OSError
        # is caught with the SystemExit because "the files are there but unreadable" is the same
        # answer to the same question: no expected set, so no absence claim.
        grid_error = str(exc)
    by_num = {e["num"]: e for e in grid}
    lines = []
    for num in sorted(set(by_num) | set(mine)):
        r, g = mine.get(num), by_num.get(num)
        lines.append(
            {
                "num": num,
                "name": (r or {}).get("name") or (g or {}).get("name") or "",
                "row": r,
                "grid": g,
                "evidence": counts.get(num, 0),
                "off_grid": g is None,
            }
        )
    answered = [
        ln["num"]
        for ln in lines
        if ln["row"] is not None and not ln["off_grid"] and ln["evidence"] > 0
    ]
    present = [ln for ln in lines if ln["row"] is not None]
    seeded = sum(1 for ln in present if not ln["off_grid"])
    return {
        "grid": len(grid),
        "grid_error": grid_error,
        "lines": lines,
        # rows present at all, then the grid's own slice of them: a number the grid does not define
        # is a stored row without being one of the 44, and the two counts say different things.
        "rows": len(present),
        "seeded": seeded,
        "absent": [ln["num"] for ln in lines if ln["row"] is None],
        "off_grid": [ln["num"] for ln in present if ln["off_grid"]],
        "answered": len(answered),
        "not_reached": seeded - len(answered),
        "nameless": nameless,
        # Rows whose STORED status is the seed's own word — the ones the default note describes.
        "seeded_status": sum(
            1
            for ln in present
            if str(ln["row"].get("status") or "") == DOMAIN_SEED_STATUS
        ),
    }


def domain_line(ln: dict) -> str:
    """One coverage line: the domain's state, the grid's numbers, and its terminating ring.

    The STATE is the derived coverage word — ABSENT (no row), `answered` (a decision or question
    row carries the number), or the stored row status when neither holds — and when the stored
    status disagrees with it, the line says so rather than choosing one and hiding the other: a row
    still reading NOT-REACHED while it holds an answer is a status nobody updated, which is a fact
    about the record and not something a report gets to smooth over.

    Three cases, kept apart on purpose. A SEEDED row prints the ring it holds — or
    `none (not opened)` when the grid recorded that the domain was never opened. An ABSENT domain
    prints ABSENT with the grid's numbers, because what is missing is the ROW, and naming what the
    grid expects while saying the row is gone is the whole point of the line. A number the grid does
    not define says so, so a stray row cannot pass for one of the 44.
    """
    r, g = ln["row"], ln["grid"]
    stored = str((r or {}).get("status") or "")
    if r is None:
        state = "ABSENT"
    elif ln["evidence"]:
        state = "answered"
    else:
        state = stored or DOMAIN_SEED_STATUS
    drift = f"  (row status {stored})" if stored and stored != state else ""
    ring = r.get("terminating_ring") if r else (g or {}).get("terminating_ring")
    ring_txt = (
        str(ring) if isinstance(ring, int) else ("none (not opened)" if r else "none")
    )
    triage, floor = (g or {}).get("triage"), (g or {}).get("ring_floor")
    grid_txt = (
        f"triage {triage:>3}  floor {floor}"
        if isinstance(triage, int) and isinstance(floor, int)
        else "triage  —  floor —"
    )
    return (
        f"  {ln['num']}  {str(ln['name'])[:22]:<22} {state:<12} {grid_txt}  "
        f"terminating ring {ring_txt}  decisions/questions {ln['evidence']}"
        + ("  (off-grid: not one of the 44)" if ln["off_grid"] else "")
        + drift
    )


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
    names = (
        [n.get("name") for n in body if isinstance(n, dict)]
        if isinstance(body, list)
        else []
    )
    created = False
    existing = ns in names
    if existing:
        # The registry list is a fast path, not the existence authority. A stale row can
        # name a namespace whose directory is gone, so confirm the namespace resource itself.
        verify_st, verify_body, _ = db(ns_tables_path(ns))
        if verify_st == 200:
            existing = True
        elif verify_st == 404:
            existing = False
        else:
            raise SystemExit(
                f"could not verify namespace {ns} ({verify_st}): {verify_body}"
            )
    if not existing:
        st, body, _ = db("/api/namespaces", "POST", {"name": ns})
        if st in (200, 201):
            created = True
        elif st == 409 or (
            isinstance(body, dict) and str(body.get("code", "")).upper() == "CONFLICT"
        ):
            # The create response is authoritative when the list is stale. Verify the
            # conflict names a namespace that is actually readable before continuing.
            verify_st, verify_body, _ = db(ns_tables_path(ns))
            if verify_st != 200:
                raise SystemExit(
                    f"could not create namespace {ns} ({st}): {body}; "
                    f"could not verify existing namespace ({verify_st}): {verify_body}"
                )
            existing = True
        else:
            raise SystemExit(f"could not create namespace {ns} ({st}): {body}")
    # declare (idempotent) the SDM tables
    d = os.path.join(ns_dir(ns), "tables")
    os.makedirs(d, exist_ok=True)
    wrote = []
    for name, cols in COLS.items():
        decl = {
            "name": name,
            "format": "jsonl-objects",
            "primary": PRIMARY[name],
            "glob": f"tables/{name}.jsonl",
            "columns": [{"name": c, "type": t} for c, t in cols],
        }
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
    st, live, _ = db(ns_tables_path(ns))
    have = sorted(
        t["name"] for t in (live.get("tables", []) if isinstance(live, dict) else [])
    )
    print(f"namespace {ns}: {'created' if created else 'existing'}")
    print(f"declared: {len(have)}/{len(COLS)} tables -> {', '.join(have)}")
    if wrote:
        print(f"wrote declarations: {', '.join(wrote)}")
    missing = sorted(set(COLS) - set(have))
    if missing:
        print(f"WARNING not visible to the API yet: {', '.join(missing)}")
    if a.seed_domains:
        # The one row-writing arm of `init`, and it belongs here because `init` owns namespace
        # setup: the 44-domain grid is the shape of the namespace, not an answer about a project.
        # Without the flag `init` writes no rows at all — the property its VERBS.md entry states —
        # so this is an extension a caller asks for, never a side effect of declaring tables.
        tally = seed_domains(ns)
        print(
            f"domain grid: {tally['grid']} domains -> {tally['written']} row(s) written, "
            f"{tally['present']} already present"
        )
        if tally["written"]:
            print(
                f"  each new row starts {DOMAIN_SEED_STATUS}: owner {DOMAIN_SEED_OWNER!r}, "
                f"trigger {DOMAIN_SEED_TRIGGER!r}, containment {DOMAIN_SEED_CONTAINMENT!r} "
                f"('{DOMAIN_SEED_REASON}' — the `domain` table has no reason column)"
            )
            print(f"  ids: {tally['ids'][0]} … {tally['ids'][-1]}")
        print(
            f"  grid source: {domain_grid_dir()} (read, never copied — a copy would drift)"
        )
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
    row = {
        "id": pid,
        "name": a.name or ns,
        "seed": seed,
        "core_statement": "",
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    insert(ns, "project", row)
    if seed:
        # An empty seed has nothing to store or embed: POSTing it makes DuckBrain's
        # required-content gate (DB-GAP-058) 400 the call and abort a fresh start.
        remember(ns, f"/auger/{pid}/seed", seed)
    print(f"project {pid} in namespace {ns}")
    print(f"seed stored ({len(seed)} chars) + embedded")
    return 0


def cmd_bundle_add(a):
    """Create one proposed bundle through the public CLI."""
    row = bundle(a.namespace, a.name, contract=a.contract or "")
    print(f"{row['id']} bundle {row['name']!r} added (status {row['status']})")
    if row["contract"]:
        print(f"contract: {row['contract']}")
    return 0


def cmd_bundle_member(a):
    """Add one project to a bundle, with scheduler validation unless explicitly escaped."""
    row = bundle_member(a.namespace, a.bundle, a.project, a.role)
    print(
        f"{row['id']} member {row['project']!r} added to {row['bundle_id']} "
        f"as {row['role']}"
    )
    return 0


# ---------------------------------------------------------------- the ask -> store seam (AUG-035)
# The README's loop is `ask` -> `answer --question-id`: ask names the question worth drilling and the
# user records the decision that answers it. Until this seam existed the proposal lived ONLY as a
# printed line — the `question` table stayed empty after a call that named a next question, so the
# loop's central move (`answer --question-id Q-...`) refused the engine's own suggestion, and no verb
# a README reader can run could store a question at all. `feedback` was the only writer of question
# rows, and it drills THIN DECISIONS (< T_CONFIDENT): a project whose decisions are all confident
# could not put one question on the record.
#
# What is stored is the proposal the user just read, plus the two JEV values the `question` table
# DECLARES columns for:
#   * `jev_already_answered` — the gate's noul for exactly this question, which is the field's own
#     meaning and the reason `propagate` does not re-gate a row carrying it. NOT the new_question
#     choice's confidence: that number answers "how sure is JEV about WHICH question to ask", and a
#     reader taking it for an answered-score would see a 0.73 proposal as an answered question.
#   * `jev_checked_at` — when that verdict was taken.
# Nothing else is invented. No `opens` edge: the proposal names no decision it came from, and `edge`
# refuses a claim about a node that is not stored rather than storing an orphan. No `domain` either:
# JEV picks a SUBJECT, and which of the 44 grid domains it lands in is not something this verb knows.
ASK_CLASS = "ask_proposed"  # the `qclass` a question proposed by `ask` carries (its source marker)

# One source of truth for the JEV wire descriptions and the human-readable question that `ask`
# records. JEV answers this `choice` question with one of these keys, so storing the key itself
# would leave the question table with a machine identifier instead of a question a user can answer.
ASK_NEW_QUESTION_CRITERIA = {
    "how_does_it_fail": {
        "description": "what happens when a component stops working",
        "sentence": "What happens when a component stops working?",
    },
    "what_does_it_break": {
        "description": "which existing decision this next choice would invalidate",
        "sentence": "Which existing decision would this next choice invalidate?",
    },
    "what_is_the_data": {
        "description": "what the data means, not where it is stored",
        "sentence": "What does the data mean, as opposed to where it is stored?",
    },
    "who_owns_it": {
        "description": "ownership and lifecycle after delivery",
        "sentence": "Who owns this, and what is its lifecycle after delivery?",
    },
    "how_is_it_tested": {
        "description": "the test that proves it works",
        "sentence": "What test proves this works?",
    },
    "none": {
        "description": "nothing left worth asking",
        "sentence": None,
    },
}


def store_proposed_question(
    ns: str, project_id: str, nq: dict, noul
) -> tuple[str, str]:
    """Persist the question `ask` proposed. Returns (qid, why); an empty qid means nothing was stored.

    Why this is a function and not four lines inside `cmd_ask`: every reason NOT to store has to be
    provable on its own — a question the gate already answered, a question already open, a refused
    insert — and each of them must leave the store untouched.

    FAIL-CLOSED, in three parts:
      * nothing is written until every gate has passed, so a failure before the first write returns
        "" with the reason and the `question` table keeps exactly the rows it had;
      * an absent verdict is NOT a "genuinely new" one: a noul the model did not return refuses the
        store, because a question whose answered-score nobody knows is a question nobody scored;
      * the row and its facets are separate writes, so a failure AFTER the row landed is returned as
        a PARTIAL store (both a qid and a reason) instead of being reported as a clean one.
    """
    slug = str((nq or {}).get("choice") or "").strip()
    if not slug:
        return "", "JEV proposed no question"
    criterion = ASK_NEW_QUESTION_CRITERIA.get(slug)
    if criterion is None:
        return "", f"JEV proposed unknown question criterion {slug!r}"
    if slug == "none":
        return "", "JEV proposed none: nothing left worth asking"
    # `new_question` confidence says how sure JEV is about WHICH question to ask, not whether the
    # question is answered. The answered-score below is the safety gate, so a useful low-confidence
    # proposal must still reach storage rather than making this seam unreachable in live use.
    if noul is None:
        return "", "the gate returned no answered-verdict for it"
    if float(noul) >= T_ANSWERED:
        return "", f"the gate scores it already answered (noul {float(noul)})"
    try:
        project_questions = select(
            ns, "question", f"project_id=eq.{project_id}&order=id.asc"
        )
    except SystemExit as exc:
        return "", f"the project's stored questions could not be read — {exc}"
    text = criterion["sentence"]
    want = text.casefold()
    for q in project_questions:
        if str(q.get("text") or "").strip().casefold() != want:
            continue
        if q.get("status") == "open":
            return "", f"already open as {q.get('id')}"
        # Only a settled question that carries a durable decision closure is a duplicate. In
        # particular, do not suppress moot/budget-thin rows: propagation may reopen or otherwise
        # revisit those states, so a matching proposal remains a legitimate new question.
        if q.get("status") not in SETTLED_STATES:
            continue
        facets = select(ns, "facet", f"question_id=eq.{q.get('id')}&order=id.asc")
        closure = next(
            (
                f
                for f in facets
                if f.get("status") == "closed" and str(f.get("closed_by") or "").strip()
            ),
            None,
        )
        if closure:
            reason = str(closure.get("note") or "").strip()
            detail = f": {reason}" if reason else ""
            return (
                "",
                f"already closed/answered as {q.get('id')} by "
                f"{closure['closed_by']}{detail}",
            )
    qid = next_id(ns, "question", "Q")
    row = {
        "id": qid,
        "project_id": project_id,
        "domain": "",
        "text": text,
        "ring": 1,
        "qclass": ASK_CLASS,
        "status": "open",
        "jev_already_answered": float(noul),
        "jev_checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        insert(ns, "question", row)
    except SystemExit as exc:
        return "", f"the question row was refused — {exc}"
    for name in facet_set():
        try:
            facet(ns, project_id, qid, name)
        except SystemExit as exc:
            return qid, f"the question row landed but facet {name!r} did not — {exc}"
    return qid, ""


def cmd_ask(a):
    """Surface the next questions: JEV's pick, plus every low-confidence decision."""
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    seats = select(
        ns,
        "decision",
        f"project_id=eq.{pid}&confidence=lt.{T_CONFIDENT}&select=id,domain,chosen,confidence,status&order=confidence.asc",
    )
    unknown = select(ns, "unknown", f"project_id=eq.{pid}")
    unans = select(ns, "question", f"project_id=eq.{pid}&status=eq.open")
    hits = []
    try:
        hits = recall(ns, p.get("seed", "") or "project spec", limit=6)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        print(f"substrate unavailable — cannot ask: {exc}")
        print(
            "fail-closed: no next-question is invented while the memory store "
            "cannot be read."
        )
        return 1
    evidence = "\n".join(
        f"- {h.get('key')}: {h.get('content', '')[:300]}" for h in hits
    )
    decisions = (
        "\n".join(
            f"- {d['id']} (conf {_conf_render(d.get('confidence'))}): {d['chosen']}"
            for d in seats
        )
        or "(none yet)"
    )
    state = (
        f"SEED:\n{p.get('seed', '')}\n\nDECISIONS:\n{decisions}\n\n"
        f"OPEN QUESTIONS: {len(unans)}\nUNKNOWNS: {len(unknown)}\n\nRELATED EVIDENCE:\n{evidence}"
    )
    qs = {
        "next_subject": {
            "type": "choice",
            "instructions": "Which subject of this system needs coverage next?",
            "criteria": {
                "none_needed": "evidence is sufficient for a v1",
                "deployment_and_alerts": "deployment, boot survival, failure notification",
                "retention_and_privacy": "retention horizons, privacy, data lifecycle",
                "concurrency_and_scaling": "concurrency, load, growth beyond v1",
                "testability": "how correctness will be proven",
            },
        },
        "new_question": {
            "type": "choice",
            "instructions": "Which single question should be asked next?",
            "criteria": {
                slug: criterion["description"]
                for slug, criterion in ASK_NEW_QUESTION_CRITERIA.items()
            },
        },
        "already_answered": {
            "type": "noul",
            "instructions": "Is that question already fully answered by the evidence above?",
        },
        "completeness": {
            "type": "score",
            "instructions": "How complete is the design evidence for a buildable v1?",
            "criteria": [
                "nothing decided",
                "partial, major gaps",
                "mostly decided, minor gaps",
                "decided enough to build",
                "complete and verified",
            ],
        },
    }
    ans, err = jev(state, qs)
    print(f"project {pid}  |  {ns}")
    print(
        f"low-confidence decisions (<{T_CONFIDENT}): {len(seats)}   open questions: {len(unans)}   unknowns: {len(unknown)}"
    )
    # The askable surface (SPEC-001 BEAT 4 + 5): a question ordered behind an unresolved blocker
    # is NOT surfaced here. Blocked questions are counted, never named — "not surfaced" is the
    # whole point of the edge, and a reader who can see the text of one has been shown it.
    ask = askable_questions(ns, pid)
    blocked = blocked_questions(ns, pid)
    print(
        f"askable (open, unblocked): {len(ask)}   blocked by an unresolved question: {len(blocked)}"
    )
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
    print(
        f"\ncompleteness : {comp.get('score')} / 4  (confidence {comp.get('confidence')})"
    )
    print(f"next subject : {subj.get('choice')}  ({subj.get('confidence')})")
    if subj.get("confidence", 0) < T_SUBJECT:
        print(
            f"               ^ below threshold {T_SUBJECT} — treat as one option, not an answer"
        )
    print(f"next question: {nq.get('choice')}  ({nq.get('confidence')})")
    print(
        f"already answered? {already}  ->",
        "ask something else"
        if (already or 0) >= T_ANSWERED
        else "genuinely new, ask it",
    )
    # AUG-035: the proposal becomes a ROW here, which is the whole difference between a report and a
    # question a reader can close. One added line either way — the question is on the record, or the
    # reason it is not (fail-closed: no half-stored question, and no exit code of its own).
    stored_qid, store_why = store_proposed_question(ns, pid, nq, already)
    if stored_qid:
        stored_text = ASK_NEW_QUESTION_CRITERIA[nq["choice"].strip()]["sentence"]
        print(f"stored question: {stored_qid}  {stored_text}")
        if store_why:
            print(f"  WARNING {store_why}")
    else:
        print(f"question not stored: {store_why}")
    print(f"\njev cost: {ans.get('usage', {}).get('cost')}  build: {ans.get('model')}")
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
        st2, body2, _ = db(
            tbl(ns, "decision"),
            "POST",
            [{k: v for k, v in row.items() if k != "scope"}],
        )
        if st2 in (200, 201):
            return (
                f"WARNING: this namespace's `decision` declaration predates `scope` and the API "
                f"is serving a cached declaration, so {row['id']} is stored {DEFAULT_SCOPE}-scoped, "
                f"NOT bundle-scoped. Remedy: run `auger init` and restart the DuckBrain API so "
                f"the declaration is re-read."
            )
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
T_IMPACT_CHANGE = 0.5  # score below this    -> the sibling's decision is untouched
T_IMPACT_INVALIDATE = 1.5  # score at/above this -> the sibling's contract is BROKEN
# The floor the first version of this pass did not have: a SCORE is the model's read, and the
# CONFIDENCE beside it is how much that read is worth. A verdict the model states at 0.24 confidence
# is the model saying it does not know, and honoring that as "unchanged" is the one outcome nobody
# can re-read later — nothing is written and nothing is said (the dogfood that found this: a
# bundle-scoped answer "unchanged" at 0.24 left the walk invisible AND the decision unexamined).
# 0.4 sits between T_IMPACT_CHANGE and that observed 0.24: this pass may only conclude something the
# model is more sure of than the change threshold it is being compared against.
T_IMPACT_FLOOR = (
    0.4  # confidence below this -> the verdict is UNKNOWN (skipped + WARNING)
)


def escalate(
    ns: str,
    project_id: str,
    question: str,
    *,
    options: str = "",
    default_action: str = "",
    risk: str = "",
    status: str = "open",
) -> dict:
    """Record one escalation — a break the engine will not decide on its own (SPEC-002 2.2).

    The row lives in the ANSWERING project's namespace and carries that project's id, naming the
    sibling in its own text: the break is this answer's consequence, so it is recorded where the
    answer and the edge are. Nothing is written into the sibling's namespace — the same rule the
    pass follows for the break itself: auger RECORDS a break in a member's contract, it does not
    reach into that member's spec.
    """
    row = {
        "id": next_id(ns, "escalation", "ESC"),
        "project_id": project_id,
        "question": question,
        "options": options,
        "default_action": default_action,
        "risk": risk,
        "status": status,
    }
    insert(ns, "escalation", row)
    return row


def record_verdict(ns: str, row: dict) -> dict:
    """Store one verdict row, refusing an out-of-set word BY NAME (the EDGE_KINDS rule).

    The word is what readers grep and tallies count, so it is closed in code (VERDICT_WORDS) and
    the refusal happens at the WRITE — the one place every path to a row passes through, whichever
    caller built it. A verdict is never degraded or re-written: either it is stored or the write
    fails loudly, because a verdict nobody can read is a judgement nobody made.
    """
    word = str(row.get("verdict", "")).lower()
    if word not in VERDICT_WORDS:
        raise SystemExit(
            f"unknown verdict word {row.get('verdict')!r}: "
            f"expected one of {', '.join(VERDICT_WORDS)}"
        )
    insert(ns, "verdict", row)
    return row


def bundle_impact_verdict(answer: dict, other: dict, project: str) -> tuple:
    """Ask JEV what ONE project's answer does to a SIBLING's bundle-scoped decision.

    Returns (kind, score, err) with kind in IMPACT_OUTCOMES — and "" on ANY error, because a
    malformed or unreachable model answer is UNKNOWN and never "unchanged": the caller must write no
    edge on a verdict it did not get. A verdict stated BELOW `T_IMPACT_FLOOR` confidence is the same
    refusal — the model saying it does not know is not a decision, so it is returned as an error and
    skipped rather than honored silently (SPEC-002 2.2 fail-closed). `score` is the model's own score
    whenever there was one — INCLUDING on a refusal, because the caller still has to say what it saw
    — and -1.0 when the model gave no score at all (the module's "nobody stated it" value, as an
    unrecorded decision's confidence is -1). The thresholds are here, in code, like every other
    threshold in this module; the model supplies the score, never the meaning.
    """
    state = (
        f"AN ANSWER RECORDED IN PROJECT {answer.get('project_id')}:\n"
        f"{answer.get('id')}: {answer.get('chosen')}\n"
        f"reason its alternatives were rejected: {answer.get('why_not') or 'not stated'}\n\n"
        f"A BUNDLE-SCOPED DECISION OF A SIBLING PROJECT ({project}):\n"
        f"{other.get('id')}: {other.get('chosen')}\n"
        f"reason its alternatives were rejected: {other.get('why_not') or 'not stated'}\n\n"
        f"Both decisions bind every member of the bundle they share, so the answer above can "
        f"leave the sibling's decision standing, force it to change, or break it outright."
    )
    ans, err = jev(
        state,
        {
            "impact": {
                "type": "score",
                "instructions": (
                    f"Does the answer above leave {project}'s decision {other.get('id')} "
                    f"unchanged, force it to change, or invalidate it?"
                ),
                "criteria": [
                    "unchanged: the sibling's decision still stands exactly as written",
                    "change: the sibling's decision must be re-decided to stay consistent",
                    "invalidate: the answer breaks the sibling's decision outright",
                ],
            }
        },
    )
    if err:
        return "", -1.0, err
    scored = (ans.get("answers") or {}).get("impact") or {}
    value = scored.get("score")
    if not isinstance(value, (int, float)):
        return (
            "",
            -1.0,
            f"JEV returned no score for {other.get('id')} ({str(scored)[:120]})",
        )
    score = float(value)
    # THE FLOOR (T_IMPACT_FLOOR). Below it the model is telling us it does not know, and "does not
    # know" must never be read as "unchanged" — that is the one verdict which writes nothing and says
    # nothing, so it is indistinguishable from a pass that never ran. It is refused the way an
    # unreachable model is refused: skipped, warned, no edge. A confidence the model did not state at
    # all is refused too — a verdict of unknown strength is not a verdict, and the fail-closed side
    # is the only side that can be corrected later.
    conf = scored.get("confidence")
    if not isinstance(conf, (int, float)) or float(conf) < T_IMPACT_FLOOR:
        conf_txt = (
            f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "not stated"
        )
        return (
            "",
            score,
            f"JEV's verdict on {other.get('id')} is score {score:.2f} at confidence {conf_txt}, "
            f"below the {T_IMPACT_FLOOR} confidence floor — UNKNOWN, not {IMPACT_OUTCOMES[0]}",
        )
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
      lines       [str]                  the one-line surfacings: one per walked member (its
                                         decision, the verdict word and the score), plus each edge
                                         written when the verdict was change or invalidate
      warnings    [str]                  the model's failures and any degraded write
      skipped     [(project, reason)]    members left alone BECAUSE the verdict was unknown
    """
    rep = {
        "scoped": False,
        "walked": [],
        "unchanged": [],
        "affects": [],
        "breaks": [],
        "edges": [],
        "escalations": [],
        "lines": [],
        "warnings": [],
        "skipped": [],
    }
    if decision_scope(dec_row) != "bundle":
        return rep  # only a CONTRACT crosses the boundary — the finite rule
    rep["scoped"] = True
    home = project_name(ns, project_id)
    for project in bundle_siblings(ns, home):
        where = member_namespace(project)
        # A sibling's rows are read with the SAFE reader: a foreign namespace that cannot answer has
        # no decisions, and that is not this project's failure. The decision the ANSWER recorded is
        # never compared with itself, even if the sibling's namespace holds a row with its id.
        others = [
            d
            for d in select_or_empty(where, "decision", "scope=eq.bundle&order=id.asc")
            if d.get("id") != dec_row.get("id")
        ]
        rep["walked"].append(project)
        if not others:
            # A member holding no bundle-scoped decision was still WALKED, and the run says so:
            # "nothing here to compare" and "the pass never ran" must not read the same to a user.
            rep["lines"].append(
                f"impact: {project} walked, no bundle-scoped decision to compare"
            )
        for other in others:
            kind, score, err = bundle_impact_verdict(dec_row, other, project)
            if err:
                # FAIL-CLOSED: an unknown verdict writes nothing, is surfaced, and never fails the
                # answer — the member is skipped, not guessed at.
                rep["skipped"].append((project, f"{other.get('id')}: {err}"))
                rep["warnings"].append(
                    f"WARNING: {project}'s {other.get('id')} was NOT compared with "
                    f"{dec_row.get('id')} — {err}. No edge is written on a verdict the model did "
                    f"not give (fail-closed)."
                )
                # The line is printed even here: the walk DID ask about this decision, and the score
                # the model gave is what the reader needs to judge the skip. -1.0 is "no score"
                # (the model answered nothing to score), never "score 0".
                got = f"score {score:.2f}" if score >= 0 else "no score"
                rep["lines"].append(
                    f"impact: {project} {other.get('id')} unknown ({got})"
                )
                continue
            # The footprint of the walk itself: which member, which decision, the verdict word and
            # the score it was read from. Printed for EVERY outcome — the two that write an edge
            # get their own line right below, and the one that writes nothing (unchanged) is
            # exactly the outcome that used to leave no trace at all.
            rep["lines"].append(
                f"impact: {project} {other['id']} {kind} (score {score:.2f})"
            )
            if kind == IMPACT_OUTCOMES[0]:
                rep["unchanged"].append(project)
                continue
            shared = dict(
                confidence=score,
                source="impact",
                dst_project=project,
                warnings=rep["warnings"],
            )
            if kind == IMPACT_OUTCOMES[1]:
                wrote = edge(
                    ns,
                    project_id,
                    "affects",
                    "decision",
                    dec_row["id"],
                    "decision",
                    other["id"],
                    note=f"{project}'s {other['id']} must be re-decided to "
                    f"stay consistent with {dec_row['id']} "
                    f"(score {score:.2f})",
                    **shared,
                )
                rep["affects"].append((project, other["id"]))
                rep["edges"].append(wrote["id"])
                rep["lines"].append(
                    f"AFFECTS: answer {dec_row['id']} changes {project}'s "
                    f"{other['id']} — recorded, not rewritten"
                )
                continue
            wrote = edge(
                ns,
                project_id,
                "breaks",
                "decision",
                dec_row["id"],
                "decision",
                other["id"],
                note=f"{project}'s {other['id']} is broken by {dec_row['id']} "
                f"(score {score:.2f})",
                **shared,
            )
            rep["breaks"].append((project, other["id"]))
            rep["edges"].append(wrote["id"])
            esc = escalate(
                ns,
                project_id,
                question=f"answer {dec_row['id']} breaks {project}'s {other['id']}",
                options=f"{project}/{other['id']}",
                default_action=f"recorded, not rewritten — {project}'s decision stands until "
                f"{project} re-decides it",
                risk=f"the bundle contract is broken for {project}: {other.get('chosen')}",
            )
            rep["escalations"].append(esc["id"])
            rep["lines"].append(
                f"ESCALATION: answer {dec_row['id']} breaks {project}'s "
                f"{other['id']} — recorded, not rewritten"
            )
    return rep


def cmd_answer(a):
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    scope = (a.scope or "").strip().lower()
    if scope and scope not in DECISION_SCOPES:
        raise SystemExit(
            f"unknown decision scope {a.scope!r}: expected one of {', '.join(DECISION_SCOPES)}"
        )
    # AUG-069: the default id is derived from the HIGHEST stored decision id, never from a row
    # count. `len(select(...)) + 1` was the count: DuckBrain's declared-table selects cap at 100
    # rows with no truncation signal (AUG-068), so past the 100th decision the mint stuck at D-101
    # and the `node_exists` guard below then refused EVERY `answer` — a permanent write lockout
    # whose only escape was an explicit --id. `next_id` does the page-safe read (limit=1, highest
    # first) and `decision_id_width` keeps the live three-digit width (D-101 -> D-102), because the
    # ids in the real namespaces are three digits and a six-digit row would sort below them all.
    # The id is namespace-scoped, so the read is too: a sibling project's rows are exactly what a
    # per-project count could not see.
    did = a.id or next_id(ns, "decision", "D", width=decision_id_width(ns))
    # Refuse every precondition that can be decided from the requested IDs before inserting the
    # decision or any of its options. `close_question` still defends its own public contract below,
    # but discovering a missing question there is too late for `answer`: the answer rows already
    # exist by then. The same ordering makes an explicit retry idempotently loud instead of allowing
    # duplicate decision and option IDs into stores that do not enforce primary-key uniqueness.
    qid = (a.question_id or "").strip()
    if not a.id:
        # AUG-070: the mint read happens-before other writers' writes, so the id `next_id` derived
        # can already be stored by the time this process checks — or by the time its own INSERT
        # lands. DuckBrain enforces NO uniqueness (the comment above `next_id` says it: referential
        # integrity is OURS), so a losing writer's duty is to RE-MINT from the live highest id and
        # retry, never to exit with the answer lost. Bounded: the mint cannot be allowed to spin on
        # a namespace that holds the id before the mint even runs (the serial case below), and
        # three attempts cover the observed two-writer race with room for one more. Everything that
        # captured the previous did — the option ids, the evidence key, the row itself — is
        # recomputed inside the loop, because ids are embedded in rows, not carried by reference.
        for attempt in range(1, AUG070_ID_ATTEMPTS + 1):
            if not node_exists(ns, "decision", did):
                break
            did = next_id(ns, "decision", "D", width=decision_id_width(ns))
        else:
            raise SystemExit(
                f"refused: the auto-minted decision id {did!r} is taken — re-minted and re-checked "
                f"{AUG070_ID_ATTEMPTS} times against the live highest id and every attempt collided "
                f"(concurrent writers); NOTHING was stored. Remedy: retry `answer` once the other "
                f"writer settles, or pass an explicit --id."
            )
    elif node_exists(ns, "decision", did):
        # The explicit-id path keeps the terminal refusal (a caller that NAMED the id must decide
        # what to do about the collision), but the message now says what the refusal actually
        # guarantees: nothing was written (AUG-034's atomic-refusal doctrine, stated in the message
        # so a reader does not have to trust the code), and that the auto-minted path is the one
        # that survives a concurrent writer.
        raise SystemExit(
            f"refused: decision {did!r} already exists — answer decision IDs must be unique, and "
            f"NOTHING was stored (no decision, no options, no evidence, no edges). The "
            f"auto-minted path (no --id) re-mints from the live highest id and retries instead of "
            f"refusing; pass no --id to take that path."
        )
    if qid and not node_exists(ns, "question", qid):
        raise SystemExit(
            f"refused: question {qid!r} does not exist — a decision cannot "
            f"close a question that is not stored"
        )
    # AUG-028: the LOCAL break this answer records (SPEC-001 BEAT 4's rule-walk trigger). Validated
    # here, before anything is stored: the flag's claim is that the target is ALREADY on the record,
    # and a refusal must leave no half-applied invocation behind.
    break_targets = local_break_targets(
        ns, pid, did, a.invalidates, a.invalidates_why or ""
    )
    supersede_target_ids = supersede_targets(
        ns, pid, did, a.supersedes, a.supersedes_why or ""
    )
    # Resolve --chosen against the supplied options before the decision write. Reuse the same
    # token grammar as dump/toggle (full id, label, or bare suffix), but resolve provisional rows so
    # a refusal cannot leave a decision with zero active options. The stored choice is canonicalized
    # to the option label; otherwise a token such as O2 would look contradictory in dump --config.
    # (AUG-070: this build sits AFTER the bounded re-mint above, so the ids baked into the option
    # rows, the evidence key and the break/supersede payloads are the id that survived the check.
    # A collision from here on is the residual insert-insert window, read back below — re-running
    # this build for a new id could not help, because the row under the OLD id is already stored
    # and deleting it is not this verb's business.)
    option_rows = [
        {
            "id": f"{did}-O{i + 1}",
            "decision_id": did,
            "label": opt,
            "costs": "",
            "breaks": "",
        }
        for i, opt in enumerate(a.option or [])
    ]
    resolved_option = (
        resolve_option(option_rows, a.chosen, did) if option_rows else None
    )
    chosen = resolved_option["label"] if resolved_option else a.chosen
    row = {
        "id": did,
        "project_id": pid,
        "domain": a.domain or "",
        "question_id": a.question_id or "",
        "chosen": chosen,
        "why_not": a.why_not or "",
        "reversal_cost": a.reversal_cost or "",
        "confidence": float(a.confidence if a.confidence is not None else -1),
        "status": a.status or "decided",
        "evidence_key": f"/auger/{pid}/{did}",
    }
    # `scope` is sent only when it is NOT the default: see insert_decision for why the default
    # travels as an absent key rather than as the word "project".
    if scope and scope != DEFAULT_SCOPE:
        row["scope"] = scope
    warning = insert_decision(ns, row)
    if not a.id:
        # AUG-070, the residual insert-insert window: the pre-insert check can pass for BOTH
        # writers and both inserts then land — DuckBrain enforces NO uniqueness. The read-back is
        # BOUNDED (`limit=2`: one row is ours, two is ours plus a sibling's) and DELETES NOTHING —
        # deleting is a human decision about whose answer survives. The residue is surfaced in the
        # same DUPLICATE IDS grammar `dump` renders, so the drift is named at write time AND on
        # every later read of the config.
        readback = select(ns, "decision", f"id=eq.{did}&select=id&limit=2")
        if len(readback) > 1:
            print(
                f"{WARN_DUPLICATE_IDS}: {did} x{len(readback)}+ — concurrent writers collided; "
                f"dedupe before trusting this config"
            )
    for option in option_rows:
        option["active"] = bool(
            resolved_option and option["id"] == resolved_option["id"]
        )
        insert(ns, "option", option)
    # The embedded evidence must not contradict itself: the chosen option is CHOSEN, and the
    # rejected list is the other options. Writing all options as "rejected" (the first version
    # of this) produced evidence reading "chose X. Rejected: X, Y, Z" — which made the
    # already-answered check score a genuinely-answered question as unanswered.
    others = [
        option["label"]
        for option in option_rows
        if not resolved_option or option["id"] != resolved_option["id"]
    ]
    remember(
        ns,
        row["evidence_key"],
        f"Question: {a.domain or ''} {a.question_id or ''}".strip()
        + f". Decision {did}: we chose {row['chosen']}. "
        + (
            f"Rejected alternatives: {'; '.join(others)}. "
            if others
            else "No alternative was recorded. "
        )
        + f"Reason the alternatives were rejected: {a.why_not or 'not stated'}. "
        + f"Reversal cost: {row['reversal_cost'] or 'not stated'}.",
    )
    # SPEC-002 section 2.2: an answer that is a CONTRACT walks its bundle. The hook sits here — after
    # the evidence is embedded and before this verb's own report — and it never changes the exit
    # code: a model that is down skips a member and says so, it does not undo the answer. The scope
    # read below is the scope that was STORED: a namespace that could not hold `bundle` (see
    # insert_decision) recorded a local decision, and a local decision crosses no boundary.
    # SPEC-001 BEAT 2 — the half of ANSWER that was missing: the record NAMED the question it
    # answers and nothing carried that claim into the graph, so the question stayed open and
    # PROPAGATE had no `closes` edge to walk. Nothing here runs without --question-id: a decision
    # that names no question closes no question, and this verb then behaves exactly as it did
    # before the hook existed. The hook sits AFTER the decision is stored and BEFORE the bundle
    # impact pass, because the refusal it can raise is about the LINK: the answer itself is never
    # undone (the same doctrine the impact pass states below — a model that is down does not
    # unrecord an answer).
    closed, beat2_warnings = "", []
    if qid:
        close_question(ns, pid, did, qid, warnings=beat2_warnings)
        closed = f", closed {qid}"
    # AUG-028: the trigger, written now that the answer is a row — `edge` verifies BOTH endpoints, and
    # the answer is one of them. It sits BEFORE the bundle impact pass for the same reason the BEAT 2
    # hook does: the impact pass is the one step here that spends a model call, and a refusal must not
    # be discovered after it.
    breaks = record_local_breaks(ns, pid, did, break_targets, a.invalidates_why or "")
    supersessions = record_supersessions(
        ns, pid, did, supersede_target_ids, a.supersedes_why or ""
    )
    stored_scope = DEFAULT_SCOPE if warning else decision_scope(row)
    impact = bundle_impact(ns, pid, {**row, "scope": stored_scope})
    active_count = 1 if resolved_option else 0
    print(
        f"{did} recorded  (confidence {_conf_render(row['confidence'])}, "
        f"{active_count} of {len(option_rows)} active, "
        f"embedded, scope {decision_scope(row)}{closed})"
    )
    # The local break, surfaced in the same grammar `propagate` reports its walk in (AUG-028): the
    # trigger is a claim about a decision that is now DEAD, and the user needs to see both that it is
    # on the record and what reads it. Without a line here the flag would be silent about the one
    # thing it exists for — the same defect AUG-027 fixed for the bundle impact pass.
    for eid, target in breaks:
        print(
            f"  breaks     {eid}  {did} invalidates {target} — local (no project endpoint)"
        )
    if breaks:
        print(
            f"  trigger    `auger propagate` is the walk this edge fires: it moots the questions "
            f"{'/'.join(b for _, b in breaks)} answered and reopens their stale children"
        )
    for eid, target, questions in supersessions:
        print(
            f"  supersedes {eid}  {did} supersedes {target} — status -> {SUPERSEDED_STATUS}"
        )
        if questions:
            print(
                f"  facets    {target}: refreshed {len(questions)} question(s) with "
                f"closed_by {did}"
            )
    # The walk's own report (AUG-027): `impact["lines"]` carries one line per member the pass
    # WALKED — its decision, the verdict word and the score — so that an 'unchanged' member and a
    # skipped one are both visible in the verb's output. Without that the answer to a run where
    # nothing was affected is indistinguishable from a run where the pass is not wired at all.
    if impact["scoped"] and not impact["walked"]:
        print(
            f"impact: nothing walked — no other project shares an active bundle with "
            f"{p.get('name') or pid}"
        )
    for line in impact["lines"]:
        print(line)
    for line in impact["warnings"] + beat2_warnings:
        print(line)
    if warning:
        print(warning)
    domain = (a.domain or "").strip()
    if domain:
        candidates = [
            d
            for d in select(ns, "decision", f"project_id=eq.{pid}&order=id.asc")
            if d.get("id") != did
            and (d.get("domain") or "").strip() == domain
            and d.get("status") != SUPERSEDED_STATUS
        ]
        shown = candidates[:3]
        for candidate in shown:
            print(
                f"  supersedes?  {candidate['id']} ({domain}, {candidate.get('status') or 'decided'}) "
                f"is in this domain — pass --supersedes {candidate['id']} to record it"
            )
        if len(candidates) > len(shown):
            print(f"  supersedes?  (+{len(candidates) - len(shown)} more)")
    return 0


def cmd_check(a):
    """Before asking: is this already answered by what we hold?

    AUG-075, fail closed: a substrate that cannot be asked is an error with exit 1,
    never "no stored evidence matched — treat as a new question" — that sentence is
    reserved for a store that was asked and genuinely holds nothing.
    """
    ns = a.namespace
    try:
        hits = recall(ns, a.question, limit=a.limit)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        print(f"substrate unavailable — cannot check: {exc}")
        print(
            "fail-closed: a memory store that cannot be read is NOT the same as an "
            "empty one — no 'new question' answer is invented."
        )
        return 1
    if not hits:
        print("no stored evidence matched — treat as a new question")
        return 0
    evidence = "\n".join(
        f"- {h.get('key')}: {h.get('content', '')[:400]}" for h in hits
    )
    ans, err = jev(
        f"QUESTION UNDER CONSIDERATION:\n{a.question}\n\nSTORED EVIDENCE:\n{evidence}",
        {
            "already_answered": {
                "type": "noul",
                "instructions": "Is the question under consideration ALREADY fully answered by the stored evidence?",
            }
        },
    )
    print(f"nearest stored rows ({len(hits)}):")
    for h in hits:
        print(f"  {h.get('score'):.3f}  {h.get('key')}")
    if err:
        print(
            f"\nJEV unavailable — {err}\n(fail-closed: UNKNOWN, not 'already answered'.)"
        )
        return 1
    v = _noul(ans["answers"], "already_answered")
    verdict = "ALREADY ANSWERED" if (v or 0) >= T_ANSWERED else "NOT YET ANSWERED"
    print(f"\n{verdict}  (noul {v}, threshold {T_ANSWERED})")
    print(f"jev cost: {ans.get('usage', {}).get('cost')}")
    return 0


def cmd_status(a):
    ns = a.namespace
    p = _project(ns, a.project_id)
    pid = p["id"]
    dec = select(ns, "decision", f"project_id=eq.{pid}")
    opt = select(ns, "option", "")
    esc = select(ns, "escalation", f"project_id=eq.{pid}")
    unk = select(ns, "unknown", f"project_id=eq.{pid}")
    cov = domain_coverage(ns, pid)
    by_dom = {}
    for d in dec:
        by_dom.setdefault(d.get("domain") or "(none)", []).append(d)
    print(f"project {pid} — {p.get('name')}  [{p.get('status')}]")
    print(
        f"decisions {len(dec)} | options {len(opt)} | escalations {len(esc)} | unknowns {len(unk)} | domains {cov['rows']}"
    )
    if dec:
        # AUG-062: the sentinel is a NON-measurement, so it is excluded from every aggregate —
        # min/mean/max describe the decisions somebody actually scored.
        confs = _conf_values(dec)
        if confs:
            print(
                f"confidence: min {min(confs):.2f}  mean {sum(confs) / len(confs):.2f}  max {max(confs):.2f}"
            )
    if by_dom:
        print("\ncoverage by domain (decisions, mean confidence):")
        for k in sorted(by_dom):
            rows = by_dom[k]
            cs = _conf_values(rows)
            m = f"{sum(cs) / len(cs):.2f}" if cs else "n/a"
            flag = (
                "  <- needs drilling"
                if (cs and sum(cs) / len(cs) < T_CONFIDENT)
                else ""
            )
            print(f"  {k:10s} {len(rows):3d}  {m}{flag}")
    # AUG-004, the GRID half of coverage — and it is a different question from the block above.
    # That one groups the decisions by the domain string they carry; this one walks the method's
    # own 44-domain grid and says what each domain HOLDS and which grid domains have NO ROW AT ALL
    # (section 4's coverage gate: "silence and 'this does not apply' are different claims"). It
    # prints even when nothing is stored, which is the case it exists for — a report that listed
    # only the domains it happened to have would be the silent-43 failure in miniature. Nothing is
    # omitted and nothing is invented: a domain with no row is named ABSENT, and a grid that cannot
    # be read makes `status` say it cannot judge absence rather than print 44 of them.
    if cov["grid_error"]:
        print(f"\ndomain grid coverage: WARNING {cov['grid_error']}")
        print(
            "  absence CANNOT be claimed from here: with no grid there is no expected set for a\n"
            "  domain to be absent AGAINST. The rows below are what is stored; nothing is claimed\n"
            "  about the rest."
        )
    else:
        print(
            f"\ndomain grid coverage ({cov['grid']} in the grid | {cov['seeded']} seeded | "
            f"{len(cov['absent'])} absent):"
        )
        print(
            f"  answered {cov['answered']} (a decision or question row carries the num) | "
            f"NOT-REACHED {cov['not_reached']} (no such row)"
        )
    for ln in cov["lines"]:
        print(domain_line(ln))
    if not cov["lines"]:
        print("  (no domain row is stored in this namespace)")
    if cov["absent"]:
        print(
            "  ABSENT (a grid domain with no stored `domain` row — absence, which is NOT the "
            f"same claim as 'does not apply'): {', '.join(cov['absent'])}"
        )
    if cov["seeded_status"]:
        print(
            f"  {cov['seeded_status']} row(s) still carry the seed's status {DOMAIN_SEED_STATUS} "
            f"— owner {DOMAIN_SEED_OWNER!r}, trigger {DOMAIN_SEED_TRIGGER!r}, containment "
            f"{DOMAIN_SEED_CONTAINMENT!r}, reason '{DOMAIN_SEED_REASON}' (`domain` has no reason "
            "column)"
        )
    if cov["off_grid"]:
        print(
            "  OFF-GRID rows (a num the 44-domain grid does not define — it counts for nothing "
            f"in the gate): {', '.join(cov['off_grid'])}"
        )
    if cov["nameless"]:
        print(
            "  ROWS WITH NO `num` (they cannot be matched to the grid at all): "
            f"{', '.join(cov['nameless'])}"
        )
    # AUG-062: the drill list reads the same predicate `feedback` reads (thin_decisions' twin) —
    # an unmeasured decision is thin, not confident, and renders n/a, never the bare sentinel.
    thin = [
        d
        for d in dec
        if (
            isinstance(d.get("confidence"), (int, float))
            and 0 <= d["confidence"] < T_CONFIDENT
        )
        or _is_unmeasured(d)
    ]
    if thin:
        print(
            f"\n{len(thin)} decision(s) below {T_CONFIDENT} — these are what `auger ask` will drill:"
        )
        for d in sorted(thin, key=lambda x: _conf(x)):
            print(
                f"  {d['id']}  {_conf_render(d.get('confidence'))}  {d['chosen'][:70]}"
            )
    # The selection invariant (AUG-015): the counts above cannot show WHICH options are active, so a
    # decision holding zero or two-or-more of them is named here. Warned, never fatal — the map this
    # verb exists to print is still the answer, and a record with a collision is still worth reading.
    activation_warnings = activation_warning_lines(dec, opt)
    if activation_warnings:
        print()
        print("\n".join(activation_warnings))

    supersession_edges = [
        e
        for e in graph_edges(ns, pid)
        if e.get("kind") == "supersedes"
        and e.get("src_kind") == "decision"
        and e.get("dst_kind") == "decision"
    ]
    superseded = [d for d in dec if d.get("status") == SUPERSEDED_STATUS]
    if supersession_edges or superseded:
        print("\nsupersession:")
        for e in supersession_edges:
            print(f"  {e['src_id']} supersedes {e['dst_id']}")
        edged_old = {e["dst_id"] for e in supersession_edges}
        for d in superseded:
            if d["id"] not in edged_old:
                print(f"  marked superseded: {d['id']} (no supersedes edge)")
        dec_by_id = {d["id"]: d for d in dec}
        opts_by_dec = options_by_decision(opt)
        for e in supersession_edges:
            old = dec_by_id.get(e["dst_id"])
            if old and old.get("status") == "decided":
                if any(o.get("active") for o in opts_by_dec.get(old["id"], [])):
                    print(
                        f"  UNRESOLVED: {old['id']} is still decided with an active option "
                        f"despite supersession by {e['src_id']}"
                    )
        closed = questions_closed_by(ns, pid, edge_index(graph_edges(ns, pid)))
        for old in superseded:
            for question_id in closed.get(old["id"], []):
                questions = select(ns, "question", f"id=eq.{question_id}&limit=1")
                if questions and questions[0].get("status") in SETTLED_STATES:
                    print(
                        f"  residue (expected): question {question_id} remains "
                        f"{questions[0]['status']} although answering decision {old['id']} "
                        "is superseded"
                    )

    # SPEC-001 BEAT 2, the reading half: how much branch is still open, and how deep the stored
    # dependency chain runs. Both numbers come off the ROWS — the question statuses and the
    # question->question edges — never off the prose in this report, so a project with no question
    # table rows prints nothing extra at all (this verb's output for a question-less project is
    # exactly what it always was).
    questions = project_questions(ns, pid)
    if questions:
        opened = [q for q in questions if q.get("status") == "open"]
        print(
            f"\nbranches: {len(opened)} open | max depth "
            f"{branch_depth(questions, graph_edges(ns, pid))}"
        )
    # R11's register in the coverage line: a verdict recorded and never shown is a judgement
    # nobody can find. Nothing extra prints when none are on file (the same quiet rule the
    # branch block above follows for a question-less project).
    verdicts = select_or_empty(ns, "verdict", f"project_id=eq.{pid}")
    if verdicts:
        good = sum(1 for v in verdicts if v.get("verdict") == "good")
        print(
            f"\nverdicts {len(verdicts)} ({good} good, {len(verdicts) - good} bad)"
            " — auger verdict --list for the rows"
        )
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
WARN_ACTIVATION = (
    "WARNING: decisions with != 1 active option "
    "(a configuration SELECTS one option per decision):"
)


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


def option_matches(
    opts: list[dict], token: str, decision_id: str | None = None
) -> list[dict]:
    """Every option row a token could name, best form first.

    A token may be a full option id, a label, or the bare index at the end of an id. When
    a decision is supplied, matching is scoped to that decision so a short index cannot
    accidentally select a sibling from another decision.
    """
    tok = token.strip()
    low = tok.lower()
    cand = [
        o for o in opts if decision_id is None or o.get("decision_id") == decision_id
    ]
    for test in (
        lambda o: o.get("id") == tok,
        lambda o: (o.get("label") or "").lower() == low,
        lambda o: (o.get("id") or "").rsplit("-", 1)[-1].lower() == low,
    ):
        hits = [o for o in cand if test(o)]
        if hits:
            return hits
    return []


def resolve_option(
    opts: list[dict], token: str, decision_id: str | None = None
) -> dict:
    """Resolve to exactly one option row, or refuse by name before any write/render."""
    hits = option_matches(opts, token, decision_id)
    if len(hits) == 1:
        return hits[0]
    where = f" for {decision_id}" if decision_id else ""
    if not hits:
        raise SystemExit(
            f"no such option{where}: {token!r} — use the full option id "
            f"(like D-00X-OY) or a label; nothing was written"
        )
    ids = ", ".join(sorted(o["id"] for o in hits))
    raise SystemExit(
        f"ambiguous option{where}: {token!r} matches {ids} — use the full "
        f"option id; nothing was written"
    )


def activation_warning_lines(
    dec: list[dict], opts: list[dict], live_by_dec: dict[str, set[str]] | None = None
) -> list[str]:
    """The warning block for every decision whose active-option count is not exactly one.

    `live_by_dec` is the selection a caller is RENDERING — a `dump --config` hypothesis. Without it
    the stored `active` flags are the selection, which is what `status` reports. A decision with no
    option rows registered is skipped: there is nothing to select between, so nothing can disagree.
    """
    by_dec = options_by_decision(opts)
    lines = []
    for d in dec:
        if d.get("status") == SUPERSEDED_STATUS:
            continue
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
            lines.append(
                f"  - {d['id']}: 0 of {len(cand)} options active — this decision contributes "
                f"NOTHING to the configuration"
            )
        else:
            lines.append(
                f"  - {d['id']}: {len(live)} of {len(cand)} options active "
                f"({', '.join(sorted(live))}) — one decision bound to contradictory choices"
            )
    return [WARN_ACTIVATION] + lines if lines else []


#: AUG-070: the header of the duplicate-id warning block. Two writers can pass the same
#: check-then-insert window and both insert (DuckBrain enforces NO uniqueness — referential
#: integrity is ours), leaving two decisions under one id and options minted over each other.
#: The consequence that matters to a READER is that one id renders two different real choices,
#: so the warning names the ids and says what to do about it — and dump keeps rendering, because
#: a drifted record is still worth reading (the doctrine at `activation_warning_lines` above).
WARN_DUPLICATE_IDS = "WARNING: DUPLICATE IDS"


def duplicate_id_warning_lines(dec: list[dict], opts: list[dict]) -> list[str]:
    """The warning block for ids the store holds MORE THAN ONCE of (AUG-070).

    `dec` and `opts` are the rows `dump` already read: a duplicate is a row-count-per-id fact
    about exactly those lists, so no extra read is spent. Sorted by id, then count: the same
    drifted namespace names the same drift on every render, and a reader cross-checking the
    warning against a raw table read finds the same list.
    """
    dec_counts = Counter(d.get("id") or "" for d in dec)
    opt_counts = Counter(o.get("id") or "" for o in opts)
    parts = [
        f"{id_} x{n}"
        for id_, n in sorted(dec_counts.items()) + sorted(opt_counts.items())
        if n > 1
    ]
    if not parts:
        return []
    return [
        f"{WARN_DUPLICATE_IDS}: {', '.join(parts)} — concurrent writers collided; "
        f"dedupe before trusting this config"
    ]


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
    opts = select(ns, "option", "order=id.asc")
    by_dec = options_by_decision(opts)
    took = []
    touched: dict[str, bool] = {}  # the state THIS invocation set, per option

    def write(oid: str, state: bool) -> int:
        """PATCH one option's flag, and remember the write.

        The remembered state is why a call that flips several options of one decision still ends
        with one active option: a sibling is judged by the state this run gave it, never by the
        snapshot read before the first PATCH (`--on A --on B` would otherwise leave both live,
        because B was not active yet when the snapshot was taken). A zero-row patch is a hard
        failure: a successful-looking toggle must never be a silent no-op.
        """
        n = patch(ns, "option", oid, {"active": state}).get("updated", 0)
        if n != 1:
            raise SystemExit(
                f"toggle {oid}: patched {n} rows (expected 1) — nothing changed; "
                f"the option id named no row"
            )
        touched[oid] = state
        return n

    def flip(oid: str, state: bool) -> str:
        return f"{oid}->{'on' if state else 'off'} ({write(oid, state)})"

    def activate(oid: str) -> str:
        line = flip(oid, True)
        if additive:
            return line
        did, cand = decision_options(by_dec, oid)
        siblings = [
            o
            for o in cand
            if o["id"] != oid and touched.get(o["id"], bool(o.get("active")))
        ]
        if not siblings:
            return line
        # The flip is part of the write, so it is part of the report: the defect this fixes was a
        # configuration change nobody could see.
        offs = ", ".join(f"{o['id']} ({write(o['id'], False)})" for o in siblings)
        return f"{line}  [siblings off — one option per decision {did}: {offs}]"

    targets = []
    for oid, state in a.set or []:
        targets.append((resolve_option(opts, oid)["id"], state))
    for oid in a.on or []:
        targets.append((resolve_option(opts, oid)["id"], True))
    for oid in a.off or []:
        targets.append((resolve_option(opts, oid)["id"], False))
    for oid, state in targets:
        took.append(activate(oid) if state else flip(oid, False))
    print(
        "toggled: "
        + (", ".join(took) if took else "(nothing — pass --on/--off/--set ID=on|off)")
    )
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
    superseded_by = {}
    for e in graph_edges(ns, pid):
        if (
            e.get("kind") == "supersedes"
            and e.get("src_kind") == "decision"
            and e.get("dst_kind") == "decision"
        ):
            superseded_by.setdefault(e["dst_id"], []).append(e["src_id"])

    # --config overrides: accept a full option id, label, or bare index, but refuse every
    # malformed/unknown/ambiguous entry before rendering so a current dump cannot masquerade as a
    # hypothesis.
    dec_ids = {d["id"] for d in dec}
    overrides = {}
    for spec in a.config or []:
        if "=" not in spec:
            raise SystemExit(
                f"--config {spec!r} is not a decision=option pair (expected e.g. "
                f"D-001=O2); nothing rendered"
            )
        d_id, val = (part.strip() for part in spec.split("=", 1))
        if d_id not in dec_ids:
            raise SystemExit(
                f"no such decision for --config: {d_id!r} — use a decision id "
                f"shown by 'dump'; nothing rendered"
            )
        overrides.setdefault(d_id, set()).add(
            resolve_option(by_dec.get(d_id, []), val, d_id)["id"]
        )
    override_ids = set()
    for _s in overrides.values():
        override_ids |= _s
    hypothetical = bool(overrides)

    lines = []
    lines.append(f"# {p.get('name')} — configuration dump")
    lines.append(
        f"project {pid} · namespace {ns} · generated {datetime.now(timezone.utc).isoformat()}"
    )
    lines.append(
        f"mode: {'HYPOTHETICAL (nothing written)' if hypothetical else 'current stored state'}"
    )
    lines.append(f"seed: {p.get('seed', '')[:300]}")
    lines.append("")
    confs, shadows, live_by_dec = [], [], {}
    for d in dec:
        cand = by_dec.get(d["id"], [])
        superseders = superseded_by.get(d["id"], [])
        if d.get("status") == SUPERSEDED_STATUS or superseders:
            live_ids = set()
            if d["id"] in overrides:
                names = ", ".join(superseders) if superseders else "another decision"
                shadows.append(
                    f"{d['id']}: --config override is superseded by {names} and is not "
                    "part of the active configuration"
                )
        elif hypothetical:
            live_ids = overrides.get(d["id"]) or {
                o["id"] for o in cand if o.get("active")
            }
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
                shadows.append(
                    f"{d['id']}: choosing {o['label']} contradicts the recorded choice "
                    f"({d.get('chosen')}) — reason on file: {d.get('why_not') or 'none'}"
                )
        lines.append(
            f"## {d['id']}  ({d.get('domain') or '—'})  conf {_conf_render(d.get('confidence'))}"
            + (
                f"  [SUPERSEDED by {', '.join(superseders)}]"
                if superseders
                else "  [SUPERSEDED]"
                if d.get("status") == SUPERSEDED_STATUS
                else ""
            )
        )
        lines.append(f"   chosen   : {d.get('chosen')}")
        lines.append(f"   why not  : {d.get('why_not') or '(not recorded)'}")
        if cand:
            lines.append("   options  :")
            for o in cand:
                mark = "[x]" if o["id"] in live_ids else "[ ]"
                lines.append(
                    f"     {mark} {o['id']}  {o['label']}  costs={o.get('costs') or '—'}  breaks={o.get('breaks') or '—'}"
                )
        lines.append("")
    lines.append("---")
    lines.append(
        f"ACTIVE CONFIGURATION: {', '.join(confs) if confs else '(nothing active)'}"
    )
    if shadows:
        lines.append("")
        lines.append("CONTRADICTIONS WITH THE RECORD:")
        for s in shadows:
            lines.append(f"  - {s}")
    lines += activation_warning_lines(dec, opts, live_by_dec)
    # AUG-070: ids the store holds more than once of — the residue of two writers passing the same
    # check-then-insert window. Warned about by name and STILL rendered: a drifted record is still
    # worth reading, and silently rendering one id as two different real choices is the defect.
    lines += duplicate_id_warning_lines(dec, opts)
    out = "\n".join(lines)
    if a.out:
        with open(a.out, "w") as f:
            f.write(out)
        print(
            f"wrote {a.out}  ({len(out)} chars, {len(dec)} decisions, {len(confs)} choices)"
        )
    else:
        print(out)
    return 0


def cmd_recall(a):
    """AUG-075, fail closed: a store that cannot be asked is an error, not an empty list."""
    try:
        hits = recall(a.namespace, a.query, a.limit)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        print(f"substrate unavailable — cannot recall: {exc}")
        print(
            "fail-closed: a memory store that cannot be read is NOT the same as an "
            "empty one."
        )
        return 1
    for h in hits:
        print(
            f"{h.get('score'):.3f}  {h.get('key')}\n     {h.get('content', '')[:200]}"
        )
    return 0


def render_dump(
    ns: str, project_id: str | None, overrides_spec: list[str] | None = None
) -> str:
    """Render a configuration as text: the stored state, or the --config overrides as a hypothesis.

    `cmd_dump` prints this; `verdict --ask-jev` puts it in front of the model. One renderer, so a
    verdict judges the artifact the reader sees, never a paraphrase of it (AUG-003).
    """
    a_dump = argparse.Namespace(
        namespace=ns, project_id=project_id, out=None, config=overrides_spec or []
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_dump(a_dump)
    return buf.getvalue()


def cmd_verdict(a):
    """Judge a configuration: good or bad, with the reasons, on the record (DESIGN R11).

    Two shapes, one row shape:
      verdict --good|--bad <config string> --reasons ...   a verdict ON FILE (human or another
                                                           model judged it elsewhere)
      verdict --ask-jev [--config D-001=O2 ...]            JEV judges the DUMP ITSELF — rendered
                                                           exactly as `dump` renders it, so the
                                                           model judges the real artifact
    --ask-jev is fail-closed: no key, no score, NO ROW — a verdict with no judge behind it reads
    as evidence it is not, and a silently stored one is worse than a refused one.
    """
    ns = a.namespace
    if a.good and a.bad:
        raise SystemExit(
            "verdict takes exactly one of --good/--bad, not both — nothing recorded"
        )
    if not a.ask_jev and a.good is None and a.bad is None:
        raise SystemExit(
            "verdict needs exactly one of --good/--bad (got none) — pass one, or "
            "pass --ask-jev to let JEV judge the dump; nothing recorded"
        )
    if a.config and not a.ask_jev:
        raise SystemExit(
            "--config names a hypothetical dump, which only --ask-jev can judge — "
            "a verdict on file judges a configuration string, not a render"
        )
    word = "good" if a.good is not None else "bad" if a.bad is not None else None

    if a.ask_jev:
        dump = render_dump(ns, a.project_id, a.config or [])
        # The summary the row stores: the ACTIVE CONFIGURATION line of the render, so the list
        # names WHAT was judged without a re-render.
        conf_lines = [
            line
            for line in dump.splitlines()
            if line.startswith("ACTIVE CONFIGURATION:")
        ]
        summary = conf_lines[0] if conf_lines else dump
        ans, err = jev(
            dump,
            {
                "verdict": {
                    "type": "noul",
                    "instructions": (
                        "Is this configuration GOOD (coherent, buildable, consistent with "
                        "its recorded reasons) or BAD (contradictory, unbuildable, contradicts "
                        "what its decisions say)? Higher noul = more confident it is GOOD."
                    ),
                }
            },
        )
        if err:
            raise SystemExit(
                f"JEV unavailable — {err}\n(fail-closed: nothing recorded — an "
                "unjudged verdict must never look like a recorded one.)"
            )
        v = _noul(ans["answers"], "verdict")
        if v is None:
            raise SystemExit(
                f"JEV returned no noul for the verdict ({str(ans)[:200]}) — "
                "nothing recorded"
            )
        # An explicit human word wins (the human saw the same dump and said so); no word, the
        # model's score chooses through the module's own threshold.
        if word is None:
            word = "good" if (v or 0) >= T_ANSWERED else "bad"
        pid = _project(ns, a.project_id)["id"]
        row = {
            "id": next_id(ns, "verdict", "V"),
            "project_id": pid,
            "config_summary": summary,
            "verdict": word,
            "reasons": a.reasons or "",
            "judged_by": "jev",
            "confidence": v,
            "source": f"jev:{ans.get('model', JEV_MODEL)}",
            "note": a.note or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record_verdict(ns, row)
        print(
            f"{row['id']} recorded  (verdict {word}, judged_by jev, noul {v}, "
            f"cost {ans.get('usage', {}).get('cost')})"
        )
        print(f"config judged: {row['config_summary']}")
        if row["reasons"]:
            print(f"reasons: {row['reasons']}")
        return 0

    pid = _project(ns, a.project_id)["id"]
    row = {
        "id": next_id(ns, "verdict", "V"),
        "project_id": pid,
        "config_summary": (a.good or a.bad) or "",
        "verdict": word,
        "reasons": a.reasons or "",
        "judged_by": a.judged_by or "human",
        # A human verdict carries no model score: null is the declared-but-absent value,
        # and it travels as an ABSENT KEY (the namespace writer would reject a JSON null
        # for a `double` column).
        "source": a.judged_by or "human",
        "note": a.note or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if a.confidence is not None:
        row["confidence"] = float(a.confidence)
    record_verdict(ns, row)
    print(f"{row['id']} recorded  (verdict {word}, judged_by {row['judged_by']})")
    print(f"config judged: {row['config_summary']}")
    if row["reasons"]:
        print(f"reasons: {row['reasons']}")
    return 0


def cmd_verdict_list(a):
    """Every verdict on file, newest first — the register's reading half."""
    ns = a.namespace
    rows = select(ns, "verdict", "order=created_at.desc,id.desc")
    if not rows:
        print(
            "no verdicts recorded — auger verdict --good|--bad <config> --reasons ..."
        )
        return 0
    for r in rows:
        conf = (
            f"{r['confidence']:.2f}"
            if isinstance(r.get("confidence"), (int, float))
            else "—"
        )
        print(
            f"{r['id']}  {str(r.get('verdict', '')).upper():4s}  "
            f"by {r.get('judged_by') or '—'}  conf {conf}  {str(r.get('created_at', ''))[:19]}"
        )
        print(f"    config: {r.get('config_summary') or '—'}")
        if r.get("reasons"):
            print(f"    reasons: {r['reasons']}")
    return 0


def cmd_verdict_dispatch(a):
    return cmd_verdict_list(a) if a.list else cmd_verdict(a)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="auger", description="spec drilling backed by DuckBrain"
    )
    ap.add_argument("--namespace", "-n", default=os.environ.get("AUGER_NS", "auger"))
    ap.add_argument("--project-id", "-p", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser(
        "init", help="create/verify the namespace and declare the SDM tables"
    )
    s.add_argument(
        "--seed-domains",
        action="store_true",
        help="also seed the 44-domain grid as `domain` rows (idempotent): every row starts "
        "NOT-REACHED with no owner, so `status` can report coverage and name what is absent",
    )
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("start", help="register a project + store/embed its seed")
    s.add_argument("--name")
    s.add_argument("--id")
    s.add_argument("--seed")
    s.add_argument("--seed-file")
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("bundle", help="create bundles and add their project members")
    bundle_sub = s.add_subparsers(dest="bundle_cmd", required=True)

    b = bundle_sub.add_parser("add", help="create a proposed bundle")
    b.add_argument("name")
    b.add_argument("--contract", help="path to the spanning contract, if one exists")
    b.set_defaults(fn=cmd_bundle_add)

    b = bundle_sub.add_parser(
        "member",
        help="add a scheduler project (or explicit scratch project) to a bundle",
    )
    b.add_argument("bundle", help="bundle id, for example B-000001")
    b.add_argument(
        "project", help="project name; its rows live in the same-named namespace"
    )
    b.add_argument("role", help=f"one of: {', '.join(BUNDLE_ROLES)}")
    b.set_defaults(fn=cmd_bundle_member)

    s = sub.add_parser(
        "ask", help="surface the next questions (JEV) + low-confidence decisions"
    )
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser(
        "answer", help="record a decision with its rejected alternatives"
    )
    s.add_argument("--id")
    s.add_argument("--chosen", required=True)
    s.add_argument("--option", action="append")
    s.add_argument("--why-not")
    s.add_argument("--domain")
    s.add_argument("--question-id")
    s.add_argument("--reversal-cost")
    s.add_argument("--confidence", type=float)
    s.add_argument("--status")
    s.add_argument(
        "--scope",
        help=f"what this decision BINDS: {' | '.join(DECISION_SCOPES)} "
        f"(default {DEFAULT_SCOPE})",
    )
    s.add_argument(
        "--invalidates",
        action="append",
        metavar="D-00X",
        help="record a LOCAL breaks edge — SPEC-001 BEAT 4's rule-walk trigger: the answer being "
        "recorded invalidates that decision, which must already be on this project's record. "
        "Nothing is rewritten; `propagate` then moots the questions the invalidated decision "
        "answered and reopens their stale children. Repeatable.",
    )
    s.add_argument(
        "--invalidates-why",
        "--why",
        dest="invalidates_why",
        metavar="WHY",
        help="why the invalidated decision is dead; recorded on the break edge's note "
        "(`--why` is the same option). Only with --invalidates.",
    )
    s.add_argument(
        "--supersedes",
        action="append",
        metavar="D-00X",
        help="record that this answer replaces a prior decision; repeatable",
    )
    s.add_argument(
        "--supersedes-why",
        dest="supersedes_why",
        metavar="WHY",
        help="why the prior decision is superseded; only with --supersedes",
    )
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser(
        "check", help="is this question already answered by stored evidence?"
    )
    s.add_argument("question")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("status", help="confidence map and coverage")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("toggle", help="turn options on/off (the what-if switch)")
    option_token_help = (
        "option token: full id (D-001-O2), label, or bare index (O2); "
        "unresolvable or ambiguous tokens are refused without writing"
    )
    s.add_argument("--on", action="append", metavar="OPTION", help=option_token_help)
    s.add_argument("--off", action="append", metavar="OPTION", help=option_token_help)
    s.add_argument(
        "--set",
        action="append",
        metavar="OPTION=STATE",
        type=lambda v: (
            v.split("=")[0],
            v.split("=")[1].lower() in ("on", "true", "1"),
        ),
        help=(
            "set an option token (full id, label, or bare index) on/off; "
            "unresolvable or ambiguous tokens are refused without writing"
        ),
    )
    s.add_argument(
        "--additive",
        action="store_true",
        help="do NOT deactivate the target's siblings: keep the old behaviour, where one "
        "decision can hold several active options (dump/status then WARN about it)",
    )
    s.set_defaults(fn=cmd_toggle)

    s = sub.add_parser(
        "dump", help="render a configuration: current, or a --config hypothesis"
    )
    s.add_argument("--out")
    s.add_argument(
        "--config",
        action="append",
        metavar="D-001=OPTION",
        help=(
            "hypothetical decision=option set; OPTION is a full id, label, or bare index; "
            "unresolvable or ambiguous entries are refused before anything is rendered"
        ),
    )
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser(
        "propagate", help="close/moot/reopen and re-gate (SPEC-001 BEAT 4)"
    )
    s.add_argument(
        "--no-gate",
        action="store_true",
        help="rule walk only: moot, reopen and cascade, with no retrieval and no model call",
    )
    s.add_argument(
        "--recheck",
        action="store_true",
        help="re-gate questions the gate has already examined (it records its verdict)",
    )
    s.set_defaults(fn=cmd_propagate)

    s = sub.add_parser(
        "feedback",
        help="turn low-confidence decisions into the next question batch (AUG-001)",
    )
    s.add_argument(
        "--budget",
        type=int,
        default=None,
        help=f"how many questions ONE run may ask (default {FEEDBACK_BUDGET}, "
        f"env {BUDGET_ENV}); a question the ceiling stops is RECORDED, not dropped",
    )
    s.set_defaults(fn=cmd_feedback)

    s = sub.add_parser("recall", help="semantic search over the namespace")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_recall)

    s = sub.add_parser(
        "verdict", help="record good/bad on a configuration, with reasons (R11)"
    )
    s.add_argument(
        "--good",
        nargs="?",
        const="",
        metavar="CONFIG",
        help="record a GOOD verdict; CONFIG is the configuration string that was "
        "judged (exactly one of --good/--bad; bare --good with --ask-jev "
        "lets the model's score choose the word)",
    )
    s.add_argument(
        "--bad",
        nargs="?",
        const="",
        metavar="CONFIG",
        help="record a BAD verdict; CONFIG is the configuration string that was "
        "judged (exactly one of --good/--bad)",
    )
    s.add_argument("--reasons")
    s.add_argument(
        "--ask-jev",
        action="store_true",
        help="ask JEV to judge the dump first (fail-closed: no key, no record)",
    )
    s.add_argument(
        "--config",
        action="append",
        metavar="D-001=O2",
        help="judge this hypothetical option set (with --ask-jev); repeatable",
    )
    s.add_argument(
        "--judged-by", help="who judged it (default human; --ask-jev forces jev)"
    )
    s.add_argument("--confidence", type=float)
    s.add_argument("--note")
    s.add_argument(
        "--list", action="store_true", help="show every recorded verdict, newest first"
    )
    s.set_defaults(fn=cmd_verdict_dispatch)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
