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
    POST /api/memories {key,domain,content}    write + embed (embedding is a NO-OP with no healthy provider)
    GET  /api/memories?namespace=<ns>&q=...    hybrid search: the embedding leg fused with the keyword leg
                                               (RRF) on a healthy substrate; the KEYWORD-ONLY fallback
                                               answers with raw (unbounded) BM25 when it is not — AUG-092
    GET  /health                               embedding.healthy — the substrate's own statement about
                                               which of those two answered
  JEV  POST https://openrouter.ai/api/alpha/decisions  {state, questions{noul|choice|score}}

auger's OWN surface — `auger serve`, for an external agent with no shell (AUG-046):
  GET  /api/ns/<ns>/embeddings/search?q=<q>&limit=<n>[&project=<id>]  ranked embedding hits, JSON
  GET  /api/embeddings/search?q=...                                   the same, on `-n`'s namespace
  GET  /health                                                        the substrate's embedding status
Every hit carries the kind of row its key names (`decision` / `seed` / `memory`) and the tier the
substrate's /health reports, so a caller never compares a similarity with a raw BM25 score.

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
import copy
import hashlib
import importlib
import io
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

AUGER_VERSION = "0.3"
AUGER_API_DEFAULT_PORT = 8768
AUGER_API_BASE_URL = f"http://127.0.0.1:{AUGER_API_DEFAULT_PORT}"
DEFAULT_DUCKBRAIN_URL = "http://127.0.0.1:3000"
DB_URL = os.environ.get("DUCKBRAIN_URL", DEFAULT_DUCKBRAIN_URL)
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# ---------------------------------------------------------------- thresholds (code, not model)
T_ANSWERED = (
    0.55  # noul >= this  -> the question is already answered by stored evidence
)
# AUG-086. A noul is a MODEL score, and the same evidence does not come back identical: run 10
# read ALREADY ANSWERED at noul 0.55 and, three seconds later on the same question, NOT YET
# ANSWERED at noul 0.53. No threshold on a score with that much noise can be deterministic, so
# within this band of the threshold the score does NOT decide — the STORE does (check_verdict).
# Outside the band the score decides, exactly as it always has.
T_ANSWERED_BAND = (
    0.03  # |noul - T_ANSWERED| < this -> the stored question decides, not the score
)
# AUG-063. `check` measured 6.5-9.7s warm — the JEV round-trip dominates, and most of those
# round-trips are spent on evidence the retrieval already handed back as a near-verbatim restatement
# (or nothing like one). This LOCAL lexical-cosine prefilter (term-frequency vectors, stdlib `re` +
# `math` only — no embeddings are available locally) triages the pooled evidence BEFORE the model is
# asked, and the conservative policy is load-bearing: only an unambiguous top hit (>= COS_HIT) or an
# unambiguous total miss (<= COS_MISS with nothing near it) skips JEV; everything in between — which
# on short texts is most pairs — falls through to the model UNCHANGED. The gate's verdict-quality
# baseline (AUG-086's 0.95 hit / 0.03 miss discrimination) is never traded for latency.
# ch:trace row=AUG-063 test=tests/test_auger.py::test_gate_question_skips_jev_on_an_obvious_hit evidence=~/.hermes/state/aug063-evidence/focused.log witness=none:worktree-branch-merged-by-foreman
COS_HIT = 0.85  # best lexical cosine >= this -> an obvious HIT: the query restates the evidence
COS_MISS = 0.10  # best lexical cosine <= this -> an obvious MISS: no evidence is remotely about it
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
        ("created_at", "varchar"),
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
        ("proposed_by_model", "varchar"),
        ("scored_by_model", "varchar"),
        ("generation_cost", "double"),
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
        ("created_at", "varchar"),
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
        ("created_at", "varchar"),
    ],
}
PRIMARY = {t: "id" for t in COLS}

# One explicit export order: it is a readable spec, not an incidental rendering of dict insertion
# order. Every declared table is present even when the namespace holds no rows.
EXPORT_TABLES = (
    "project",
    "domain",
    "question",
    "decision",
    "option",
    "break",
    "escalation",
    "assumption",
    "unknown",
    "edge",
    "facet",
    "bundle",
    "bundle_member",
    "verdict",
)
EXPORT_HEADINGS = {
    "project": "Projects",
    "domain": "Domains",
    "question": "Questions",
    "decision": "Decisions",
    "option": "Options",
    "break": "Break records",
    "escalation": "Escalations",
    "assumption": "Assumptions",
    "unknown": "Unknowns",
    "edge": "Graph edges",
    "facet": "Question facets",
    "bundle": "Bundles",
    "bundle_member": "Bundle members",
    "verdict": "Verdicts",
}

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
# Answer writes use this transient status while the matching memory entry is being embedded. DuckBrain
# has no multi-table transaction, so a provisional row is the durable commit point: readers never treat
# it as a normal decision, and a retry can finish the same id instead of minting another answer.
PENDING_MEMORY_STATUS = "pending_memory"


def pending_memory_status(
    final_status: str,
    scope: str | None = None,
    invalidates: list[str] | tuple[str, ...] = (),
    supersedes: list[str] | tuple[str, ...] = (),
) -> str:
    """Encode all answer arguments needed to repair a provisional decision.

    The status column is the only decision field available for a durable in-progress marker. Keep the
    legacy-readable prefix, then percent-encode the requested final status, scope, and graph targets so
    a retry cannot silently change the side effects that the interrupted answer was going to perform.
    """
    fields = (
        final_status,
        scope or DEFAULT_SCOPE,
        ",".join(invalidates),
        ",".join(supersedes),
    )
    return f"{PENDING_MEMORY_STATUS}:" + ":".join(
        quote(str(field), safe="") for field in fields
    )


def is_pending_memory(row: dict) -> bool:
    """Whether a decision row is waiting for its evidence memory to be committed."""
    return str((row or {}).get("status") or "").startswith(f"{PENDING_MEMORY_STATUS}:")


def pending_metadata(row: dict) -> dict:
    """Decode repair metadata, accepting the first provisional format for upgrade safety."""
    status = str((row or {}).get("status") or "")
    prefix = f"{PENDING_MEMORY_STATUS}:"
    if not status.startswith(prefix):
        return {}
    fields = status[len(prefix) :].split(":")
    decoded = [unquote(field) for field in fields]
    if len(decoded) >= 4:
        return {
            "final_status": decoded[0],
            "scope": decoded[1] or DEFAULT_SCOPE,
            "invalidates": decoded[2].split(",") if decoded[2] else [],
            "supersedes": decoded[3].split(",") if decoded[3] else [],
        }
    # Rows created by the first partial worker encoded only the final status. Such a row is
    # repairable with the original answer fields, but never with newly requested graph side effects.
    return {
        "final_status": decoded[0] if decoded else "decided",
        "scope": decision_scope(row),
        "invalidates": [],
        "supersedes": [],
    }


def pending_final_status(row: dict) -> str:
    """Recover the final status requested by an answer that stopped before memory was written."""
    return pending_metadata(row).get("final_status", "decided")


def pending_requested_scope(row: dict) -> str:
    """Recover the scope requested before a provisional decision was stored."""
    return pending_metadata(row).get("scope", decision_scope(row))


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
WHY_NOT_PREFIX = "per-option-v1:"
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


# ---------------------------------------------------------------- the namespaces base (AUG-074)
# The base directory is the SUBSTRATE's config, not this repo's assumption: DuckBrain honors
# DUCKBRAIN_NAMESPACES_PATH for its namespaces directory, so a substrate running from a
# non-default path made the legacy guess write declaration files into a tree the API never
# reads (proven 2026-09-25: 0/14 declared files under ~, substrate serving /tmp/...).
# First choice is DUCKBRAIN_NAMESPACES_PATH when it is set and non-empty; the substrate's
# data directory is the next deterministic location, then the legacy default for the plain
# default substrate. An isolated DUCKBRAIN_URL without either path knob is refused by `init`
# before it can create declarations in the legacy tree.
NAMESPACES_PATH_ENV = "DUCKBRAIN_NAMESPACES_PATH"
DATA_DIR_ENV = "DUCKBRAIN_DATA_DIR"


def namespaces_base() -> str:
    """Resolve the local declaration tree using the substrate's path precedence."""
    base = os.environ.get(NAMESPACES_PATH_ENV, "").strip()
    if base:
        return base
    data_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    if data_dir:
        return os.path.join(data_dir, "namespaces")
    return os.path.join(os.path.expanduser("~"), "duckbrain", "namespaces")


def ns_dir(ns: str) -> str:
    return os.path.join(namespaces_base(), ns)


def _init_ns_dir(ns: str) -> str:
    """Resolve `init`'s declaration tree, refusing an unpinned isolated substrate."""
    if (
        not os.environ.get(NAMESPACES_PATH_ENV, "").strip()
        and not os.environ.get(DATA_DIR_ENV, "").strip()
        and DB_URL.rstrip("/") != DEFAULT_DUCKBRAIN_URL
    ):
        raise SystemExit(
            f"cannot resolve namespace declarations for isolated substrate {DB_URL!r}; "
            f"set {NAMESPACES_PATH_ENV} or {DATA_DIR_ENV} before running init "
            "(refusing the legacy default namespaces tree)"
        )
    return ns_dir(ns)


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
                # AUG-085: a 404 NOT_FOUND body is a namespace (or its declared table) that does
                # not exist — the one refusal a caller's OWN namespace hits, by typo or before
                # `auger init`. The raw dict repr is a diagnostic for a substrate defect, not for
                # this; every other non-200 stays loud and detailed below.
                if (
                    st == 404
                    and isinstance(body, dict)
                    and (
                        body.get("code") == "NOT_FOUND"
                        or "not found in namespace" in str(body.get("error", ""))
                    )
                ):
                    raise SystemExit(
                        f"namespace '{ns}' does not exist — run: auger init first"
                    )
                if st == 0:
                    # AUG-101: `_req`'s st == 0 IS the unreachable contract — the transport
                    # died, so there is no responder and no body to paraphrase. Handing that
                    # back as `select project failed (0): {'error': 'transport: ...'}` made one
                    # dead store read differently in `status` than in `recall`; the refusal is
                    # now the shared one every strict read raises. A NON-zero status keeps the
                    # raw detailed message below: there the store DID answer, and its body is
                    # the diagnostic (AUG-085).
                    raise substrate_read_failure(ns, f"select {table}", st, body)
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


#: The substrate's wording when a PATCH is refused not because of the PAYLOAD but because the row it
#: read back carries an explicit JSON null in a declared column (GATE-RED-002, AUG-059-R2).
_NULL_REFUSAL = "got null"


def _refused_a_stored_null(status: int, body) -> bool:
    """True when this PATCH response refuses the row's STORED null, not the values sent.

    Measured live against the running substrate (GATE-RED-002, 2026-10-28, throwaway namespace):
    the declaration the API serves for `question` types all twelve columns and carries no
    nullability; an INSERT that OMITS `proposed_by_model`/`scored_by_model`/`generation_cost` is
    answered 201 and the omitted columns are materialized as explicit `null`s IN THE STORED LINE;
    a PATCH whose body names only `jev_already_answered`/`jev_checked_at` is then answered
    `422 UNPROCESSABLE_ENTITY: Column 'proposed_by_model' (varchar) requires a string, got null`,
    because the substrate validates the MERGED row. No payload can repair that: re-sending the
    null is refused too, and omitting a key cannot take it out of a line that already has it.

    So the refusal is about the row image, never about the payload — which is what makes the
    rewrite fallback below both safe (the payload was legal) and necessary (nothing else writes).
    """
    if status != 422 or not isinstance(body, dict):
        return False
    if str(body.get("code") or "").upper() != "UNPROCESSABLE_ENTITY":
        return False
    return _NULL_REFUSAL in str(body.get("error") or "")


def patch_or_rewrite(ns: str, table: str, pk: str, values: dict) -> dict:
    """`patch`, and when the substrate refuses the row's stored nulls, rewrite the row instead.

    Every partial PATCH of a row some writer stored with a null-valued declared column 422s
    (`_refused_a_stored_null` explains the measurement). The row still has to move: `question`'s
    `jev_already_answered`/`jev_checked_at` are what PROPAGATE's gate records, and a question
    stored by `ask`, by `insert_question` or by any older version of this file carries exactly
    those nulls. So the refusal falls back to the AUG-059-R2 write shape: DELETE the row and
    INSERT the same image back with the change applied and every null-valued key DROPPED from the
    body (an explicit null is refused at INSERT too, and the omission re-materializes as null —
    the stored shape is byte-equivalent to what the original write produced).

    What this deliberately does NOT do is FABRICATE a value for the nullable column to satisfy the
    validator: a blank `proposed_by_model` or a `0.0` generation cost would be a claim nobody made
    about a row this verb was only trying to mark as gate-checked. Omitting the key states nothing.

    Ordering is delete-then-insert, for AUG-059-R2's reason: the reverse lets one DELETE take both
    copies of a duplicated primary key. If the delete landed and the insert did not, the ORIGINAL
    row is put back before the failure is raised — the update may fail, the row may not be lost.
    """
    st, body, _ = db(tbl(ns, table, f"pk=eq.{pk}"), "PATCH", values)
    if st == 200:
        return body
    if not _refused_a_stored_null(st, body):
        raise SystemExit(f"patch {table} failed ({st}): {body}")
    return _rewrite_row_via_pk(ns, table, pk, values, body)


def _rewrite_row_via_pk(ns: str, table: str, pk: str, values: dict, refusal) -> dict:
    """The AUG-059-R2 rewrite behind `patch_or_rewrite`, generalised off the `domain` table.

    The row's CURRENT image is read back first and is what the replacement is built from, so a
    column this call does not name keeps the value it had (or stays null, by being dropped). A
    rewrite that cannot be built or that fails reports the substrate's own refusal plus what it
    managed to do, never a bare status.
    """
    key = PRIMARY.get(table, "id")
    try:
        matched = [
            r for r in select(ns, table, f"{key}=eq.{pk}") if str(r.get(key)) == str(pk)
        ]
    except SystemExit as read_exc:
        raise SystemExit(
            f"patch {table} failed (422): {refusal}; the row could not be read back for the "
            f"rewrite ({read_exc})"
        )
    if not matched:
        raise SystemExit(
            f"patch {table} failed (422): {refusal}; no row {pk} to rewrite"
        )
    original = {k: v for k, v in matched[0].items() if v is not None}
    replacement = dict(original)
    replacement.update({k: v for k, v in values.items() if v is not None})
    try:
        delete_row(ns, table, pk)
    except SystemExit as delete_exc:
        raise SystemExit(
            f"patch {table} failed (422): {refusal}; the rewrite could not delete {pk} "
            f"({delete_exc}) — the row is untouched"
        )
    try:
        insert(ns, table, replacement)
    except SystemExit as insert_exc:
        try:
            insert(ns, table, original)
            note = "the original row was restored unchanged"
        except SystemExit as restore_exc:
            note = (
                f"the row itself could not be restored ({' '.join(str(restore_exc).split())})"
                " — it is GONE, and its facets and edges now name a row that no longer exists"
            )
        raise SystemExit(
            f"patch {table} failed (422): {refusal}; the replacement row was refused "
            f"({insert_exc}) — {note}"
        )
    return {"updated": 1}


def delete_row(ns: str, table: str, pk: str) -> dict:
    """DELETE the row(s) carrying this primary key. AUG-059-R2: the rewrite half of the
    domain advance needs the substrate's one other row-level write, because a stored null
    in a validated column puts the row beyond PATCH's reach entirely (see
    advance_domain_status)."""
    st, body, _ = db(tbl(ns, table, f"pk=eq.{pk}"), "DELETE")
    if st not in (200, 204):
        raise SystemExit(f"delete {table} failed ({st}): {body}")
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

    AUG-107: only rows whose id matches the declared law ``^<prefix>-\\d+$$`` are mint sources. A
    lexicographically-higher stored id outside the law (a hand-stored `D-CACHE-EMBED`, a
    `D-BAND-1.0`) is not part of this register and reading it as the top poisoned the mint into
    re-deriving the already-taken first id. The read widens once, bounded by `ID_LAW_SCAN_ROWS`,
    only when the `limit=1` top row fails the law — the trade-off is stated there.
    """
    pad = ID_WIDTH if width is None else width
    _rows, top = _highest_id_under_the_law(ns, table, prefix)
    if top is None:
        return f"{prefix}-{1:0{pad}d}"
    m = re.search(r"(\d+)\s*$", top)
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

#: AUG-107: when the mint's page-safe `limit=1` top row does not match the declared id law
#: (`^<prefix>-\d+$`), the read widens ONCE to this many rows — still ordered `id.desc` — to find
#: the highest id that IS part of the law. The bound is the honest version of a trade-off the
#: `next_id` docstring has always warned about: a capped page can hide the real highest id, and
#: any wider read re-admits that risk in exchange for immunity to out-of-law rows. Thirty-two
#: covers the observed poison shape (a handful of hand-stored rows above the numeric register)
#: dozens of times over while staying a bounded, single extra request on a path that only runs
#: when a malformed row is actually present — the numeric-only namespace keeps its one-row read.
ID_LAW_SCAN_ROWS = 32

#: The shape an id must match to be a mint source — the law `next_id`'s docstring has always
#: declared (`PREFIX-<digits>`). Anything else stored in an id column (a hand-stored
#: `D-CACHE-EMBED`, a `D-BAND-1.0` with a non-numeric tail) is not part of the register the
#: mint advances, and reading it as one poisons the mint (AUG-107).
_ID_LAW_TEMPLATE = r"^{prefix}-\d+$"


def _highest_id_under_the_law(
    ns: str, table: str, prefix: str
) -> tuple[list, str | None]:
    """The mint's read: the highest stored id that matches the declared id law.

    The hot path is the page-safe single-row read (`select=id&order=id.desc&limit=1`); when the
    top row fails the law it is a malformed/foreign row, NOT the register's top — so the read
    widens once, bounded by `ID_LAW_SCAN_ROWS`, and the first law-matching id wins. Returns the
    rows the winning read served and the winning id (None when the bounded page holds no legal
    id at all). The page-safety trade-off is stated on `ID_LAW_SCAN_ROWS`: a capped page can
    hide the real highest id, and this read only pays that risk when a malformed row forced it.
    """
    rows = select(ns, table, "select=id&order=id.desc&limit=1")
    law = re.compile(_ID_LAW_TEMPLATE.format(prefix=re.escape(prefix)))
    if rows and rows[0].get("id") and law.match(str(rows[0]["id"])):
        return rows, str(rows[0]["id"])
    if not (rows and rows[0].get("id")):
        # An empty read is the empty-table contract, not a malformed row: no widening.
        return rows, None
    rows = select(ns, table, f"select=id&order=id.desc&limit={ID_LAW_SCAN_ROWS}")
    for row in rows:
        rid = str(row.get("id") or "")
        if law.match(rid):
            return rows, rid
    return rows, None


def _out_of_law_ids(rows: list, prefix: str) -> list[str]:
    """The ids in `rows` that are NOT part of the declared id law — the AUG-107 poison shape."""
    law = re.compile(_ID_LAW_TEMPLATE.format(prefix=re.escape(prefix)))
    return [
        rid
        for rid in (str(r.get("id") or "") for r in rows)
        if rid and not law.match(rid)
    ]


def decision_id_width(ns: str) -> int:
    """The digit width this namespace's decision ids are minted at.

    Read from the SAME id the mint mints from — the highest id that matches the declared law,
    `order=id.desc` (AUG-107) — so the width can never be derived from a capped page of a bigger
    table, and never from a malformed row that is not part of the id law at all: a poison top row
    (`D-CACHE-EMBED`) used to fall back to `DECISION_ID_WIDTH` while the live register below it
    was minted wider. An empty table yields `DECISION_ID_WIDTH`, and a table wider than
    `ID_WIDTH` is capped there so one accidental row cannot mint an over-wide id law for the
    whole namespace.
    """
    top = _highest_id_under_the_law(ns, "decision", "D")[1]
    if top is None:
        return DECISION_ID_WIDTH
    m = re.search(r"-(\d+)\s*$", top)
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


#: The ONE sentence every strict read prints when the store could not be ASKED (AUG-101).
#:
#: `recall`/`check`/`ask` have carried it since AUG-075; every verb that reads a table through
#: `select` — `status`, `dump`, `feedback`, `export` — printed the substrate's raw transport dict
#: instead, so ONE dead substrate produced two different refusals depending on which verb asked
#: (dogfood run 15, 2026-10-12). The exit code was 1 either way once the shell plumbing was right
#: (the finding's rc=0 was `cmd | tail -2; echo $?` reading TAIL's status), but the WORDS are the
#: contract a scripting consumer keys on, and `select project failed (0): {'error': 'transport:
#: ...'}` is a diagnostic, not a refusal. The diagnostic is KEPT — the sentence is appended to it,
#: never printed instead of it.
FAIL_CLOSED_SENTENCE = "a failed read is NOT an empty store"


def substrate_read_failure(ns: str, what: str, st: int, body) -> SubstrateError:
    """The refusal EVERY strict read raises when the substrate did not answer (AUG-101).

    One builder, so `select` and `recall` cannot drift apart again: `what` names the read that
    failed (`select project`, `substrate read`), `ns` says where it was asked, and the substrate's
    own words are carried verbatim — the reader still needs to know WHICH read died and WHAT the
    store said. Returns the error rather than raising it so the call site stays an explicit
    `raise` (the no-bypass property AUG-075 relies on).
    """
    detail = body.get("error", body) if isinstance(body, dict) else body
    return SubstrateError(
        f"{what} failed for {ns!r} (status {st}): {detail} — {FAIL_CLOSED_SENTENCE}"
    )


# ---------------------------------------------------------------- retrieval tiers (AUG-092)
# A `?q=` search is answered by ONE of three rankers, and all three report their number in the same
# `items[].score` field (DuckBrain `src/mcp/tools/recall.ts` + `src/search/fusion.ts`):
#   * embedding providers healthy      -> the embedding leg answers (fused with the keyword leg
#     through RRF when the FTS sidecar exists): a bounded similarity / normalized scale
#   * providers unhealthy or missing   -> the keyword-only fallback answers: RAW BM25, NOT bounded
#     by 1.0
# The response never says which ranker ran, so a score on its own is ambiguous. Dogfood run 12
# (2026-09-29) recorded `recall` 1.319 and `check` 0.477 on a substrate whose /health said
# embedding.healthy=false — numbers cosine cannot produce — while every run against a healthy
# substrate recorded the ~0.99x scale, and a reader comparing the two had nothing to tell them
# apart. auger now reads the substrate's OWN /health and LABELS the tier it believes answered, so
# two runs are never silently compared as if they were one scale.
TIER_SEMANTIC = "semantic"
TIER_LEXICAL = "lexical"
TIER_UNKNOWN = "unknown"
HEALTH_PATH = "/health"
HEALTH_TIMEOUT = 10


def embedding_status(timeout: int = HEALTH_TIMEOUT) -> dict:
    """The substrate's own statement about its embedding leg, as FIELDS (AUG-046).

    `retrieval_tier` is this read reduced to one word; the HTTP route needs the same read as data —
    it reports the tier AND the provider that answered — so the read happens here ONCE and both
    callers share one decision about which ranker is left.

    `embedding.healthy` is the substrate's own statement about its embedding providers, and it is
    what decides whether the keyword-only fallback is the ranker left. The read is ADVISORY — a
    label on a result, never a precondition for one — so every way it can fail to answer (a
    transport error, `_req`'s st == 0; a status that is neither of /health's two answers; no token
    for `db`; a body without the field) reports TIER_UNKNOWN with `healthy` None, which is NOT the
    substrate saying its embedding leg is down. A tier invented from a health probe that did not
    answer would put back exactly the ambiguity this label exists to remove.

    The status check accepts 200 AND 503, never 200 alone: DuckBrain's health handler answers
    `503` whenever it is degraded (GAP-030, `src/cli/http.ts`:
    `res.status(degraded ? 503 : 200)`), and degraded is `!embedding.healthy || keys_error` —
    so the embedding-down substrate this label exists for answers **503 with
    `embedding.healthy=false` in the body**, and a keys-store failure answers 503 with the
    embedding leg still healthy. The field — not the status — decides the tier; an HTTP-200-only
    reader would label the run-12 substrate "unknown" and never report the lexical tier at all.
    """
    unknown = {"tier": TIER_UNKNOWN, "healthy": None, "provider": None, "model": None}
    try:
        st, body, _ = db(HEALTH_PATH, timeout=timeout)
    except SystemExit:  # no token: `db` cannot even build the request
        return dict(unknown)
    if st not in (200, 503) or not isinstance(body, dict):
        return dict(unknown)
    embedding = body.get("embedding")
    if not isinstance(embedding, dict) or not isinstance(
        embedding.get("healthy"), bool
    ):
        return dict(unknown)
    healthy = embedding["healthy"]
    provider, model = embedding.get("provider"), embedding.get("model")
    return {
        "tier": TIER_SEMANTIC if healthy else TIER_LEXICAL,
        "healthy": healthy,
        "provider": provider if isinstance(provider, str) else None,
        "model": model if isinstance(model, str) else None,
    }


def retrieval_tier(timeout: int = HEALTH_TIMEOUT) -> str:
    """The retrieval tier the substrate will answer `?q=` with, read from its /health (AUG-092).

    The read itself — which /health answers count, and why — lives in `embedding_status`, which the
    HTTP route shares; this is that read reduced to the one word the CLI prints above its scores.
    """
    return embedding_status(timeout)["tier"]


def tier_note(tier: str) -> str:
    """One line naming the retrieval tier behind the scores printed under it (AUG-092)."""
    if tier == TIER_LEXICAL:
        return (
            f"retrieval tier: {TIER_LEXICAL} (embedding.healthy=false — the substrate's "
            "keyword/BM25 ranker answered; these scores are NOT cosine similarity and are "
            "not bounded by 1.0)"
        )
    if tier == TIER_SEMANTIC:
        return f"retrieval tier: {TIER_SEMANTIC} (embedding.healthy=true)"
    return (
        f"retrieval tier: {TIER_UNKNOWN} (the substrate's /health did not report "
        "embedding.healthy — which ranker answered cannot be attributed)"
    )


def embed_claim(tier: str) -> str:
    """The write verbs' claim about a row's embedding, keyed on the substrate's /health (AUG-092).

    `start`/`answer` printed "embedded" unconditionally, so a substrate carrying
    embedding.healthy=false — no provider reachable, the write path's own embed attempt a no-op —
    still reported the row as embedded. The claim is the SUBSTRATE's to make: it is asserted only
    when /health says the embedding provider is healthy, and a degraded substrate reports the true,
    weaker state instead. TIER_UNKNOWN keeps the historical wording and marks it UNVERIFIED — a
    /health that could not be READ is not the substrate saying its provider is down, and the two
    are never conflated.
    """
    if tier == TIER_LEXICAL:
        return "keyword-searchable only (embedding.healthy=false)"
    if tier == TIER_UNKNOWN:
        return (
            "embedded (unverified: substrate /health did not report embedding.healthy)"
        )
    return "embedded"


def recall(
    ns: str,
    q: str,
    limit: int = 5,
    missing_ok: bool = False,
    project_id: str | None = None,
) -> list:
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

    When `project_id` is present, its `/auger/<project>/` key prefix is sent to
    the substrate in the same request as the semantic query. That constrains the
    candidate set before ranking and limiting; the result filter is a fail-closed
    backstop if a substrate ever returns a key outside the requested prefix.
    """
    project_prefix = f"/auger/{project_id}/" if project_id is not None else None
    prefix_query = (
        f"&prefix={quote(project_prefix, safe=_QUERY_VALUE_SAFE)}"
        if project_prefix
        else ""
    )
    # AUG-032, cold-start honesty: the FIRST query of a process lifetime pays the
    # substrate's embedding provider round-trip for the query itself, which can
    # sit ~20s reading like a hang. Say so on stderr BEFORE the call; stdout
    # stays clean because recall's output is the product.
    print(
        "embedding query... (first recall in a process pays the provider "
        "round-trip, ~20s)",
        file=sys.stderr,
        flush=True,
    )
    st, body, _ = db(
        f"/api/memories?namespace={url_seg(ns)}{prefix_query}&q={quote(q)}&limit={limit}",
        timeout=60,
    )
    if st != 200:
        if missing_ok and st == 404:
            return []
        # AUG-101: the SAME builder `select` raises from — one dead store, one refusal, whoever
        # asked. `recall`'s message is unchanged in shape (it is the one `substrate_read_failure`
        # was named after), so `check`/`ask`/`recall` keep the sentence they have had since
        # AUG-075 and the table verbs now join them instead of printing a raw dict.
        raise substrate_read_failure(ns, "substrate read", st, body)
    items = body.get("items", []) if isinstance(body, dict) else []
    if project_prefix:
        return [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("key", "")).startswith(project_prefix)
        ]
    return items


# ---------------------------------------------------------------- the embedding store over HTTP (AUG-046)
# The README promises the spec is "queryable over HTTP, and searchable by embedding", and only the ROW
# half of that was true for an integrator: the embedding index answered inside auger's own verbs
# (`recall`, `check`) and nowhere an external agent could call it (dogfood 2026-09-23, 4th run —
# `/api/ns/<ns>/memories` -> 404 ROUTE_NOT_FOUND, and `memories` is not one of the declared tables).
# `auger serve` publishes the SAME retrieval those verbs use — DuckBrain's `/api/memories?q=` over its
# embedding index — as JSON, carrying the two things this file adds to a raw store read: the tier
# label (AUG-092, so a caller never compares a bounded similarity with a raw BM25 number) and the
# key -> row classification the store's own keys already encode (`/auger/<project>/<thing>`).
EMBEDDINGS_SEARCH_ROUTE = re.compile(r"^/api/ns/(?P<ns>[^/]+)/embeddings/search$")
EMBEDDINGS_ALIAS_PATH = (
    "/api/embeddings/search"  # the same search, on the server's --namespace
)
EMBEDDINGS_HEALTH_ROUTE = (
    "/health"  # the same fact as HEALTH_PATH, on auger's own socket
)
EMBEDDINGS_DEFAULT_LIMIT = 5
EMBEDDINGS_MAX_LIMIT = (
    1000  # the store's own hard page cap; a bigger `limit` is refused, not clamped
)
SERVE_DEFAULT_HOST = "127.0.0.1"
SERVE_DEFAULT_PORT = 8765
#: The route list a caller is handed when it asks for one that does not exist (and printed at boot).
EMBEDDINGS_ROUTES = (
    "/api/ns/<namespace>/embeddings/search?q=<query>&limit=<n>[&project=<id>]",
    "/api/embeddings/search?q=<query>&limit=<n>[&project=<id>]  (the server's --namespace)",
    "/health",
)


def embedding_hit_kind(key: str) -> tuple:
    """(kind, decision id) for one stored key, read from the key's own shape.

    auger writes exactly two shapes into the memory store: the project seed under
    `/auger/<project>/seed`, and each decision's evidence under `/auger/<project>/<decision-id>`
    (`remember` in `cmd_start` / `cmd_answer`). The key is therefore how a hit says WHICH decision it
    is evidence of, which is the question an external agent actually asks. Anything else is another
    writer's row in a shared namespace — a plain `memory`, never guessed at. Pure parse: a search
    must not become one declared-table read per hit.
    """
    m = re.match(r"^/auger/(?P<project>[^/]+)/(?P<tail>[^/]+)$", key or "")
    if not m:
        return "memory", ""
    tail = m.group("tail")
    # `/auger/<project>/seed` also matches the shape above, and is the ONE key that names no decision.
    return ("seed", "") if tail == "seed" else ("decision", tail)


def embedding_search(
    ns: str,
    q: str,
    limit: int = EMBEDDINGS_DEFAULT_LIMIT,
    project_id: str | None = None,
) -> dict:
    """The ranked, tier-labelled answer an external agent receives over HTTP (AUG-046).

    One read of the substrate's tier, one query of the store, and NO secondary per-hit reads: the
    caller dereferences a `decision_id` through the declared-table API when it wants the row, because
    a route that fanned out per hit would turn one search into `limit + 1` requests against a
    token-bucketed API. FAIL CLOSED (AUG-075): a store that cannot be asked raises `SubstrateError`
    (the handler answers 502), while a store that genuinely holds nothing answers 200 with `count` 0 —
    "unaskable" and "empty" are never the same answer here, exactly as in `recall`.
    """
    status = embedding_status()
    hits = recall(ns, q, limit, project_id=project_id)
    results = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        key = str(hit.get("key") or "")
        kind, decision_id = embedding_hit_kind(key)
        results.append(
            {
                "key": key,
                "kind": kind,
                "decision_id": decision_id or None,
                "score": hit.get("score"),
                "domain": hit.get("domain"),
                "content": hit.get("content"),
                "snippet": hit.get("snippet"),
                "timestamp": hit.get("timestamp"),
            }
        )
    # RANKED, whatever order the store returned: the caller asked for the most similar rows first. A
    # score that is not a number sorts last rather than poisoning the comparison, and `sort` is stable,
    # so equal scores keep the store's own order.
    results.sort(
        key=lambda r: (
            r["score"] if isinstance(r["score"], (int, float)) else float("-inf")
        ),
        reverse=True,
    )
    return {
        "namespace": ns,
        "query": q,
        "limit": limit,
        "project": project_id,
        "tier": status["tier"],
        "embedding_healthy": status["healthy"],
        "count": len(results),
        "results": results,
    }


class EmbeddingsHTTPHandler(BaseHTTPRequestHandler):
    """The route's request handler: GET-only, JSON-only, and never a traceback on the wire.

    `protocol_version` is HTTP/1.1 and EVERY response — refusals included — carries a Content-Length,
    because a 1.1 response without one leaves the client waiting for a body that never comes. Nothing
    is written outside `_send_json`, which is what makes that guarantee hold for the error paths too.
    """

    server_version = f"auger/{AUGER_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(
        self, fmt, *args
    ):  # one line per request, on stderr, with the peer named
        print(
            f"{self.address_string()} [{self.log_date_time_string()}] {fmt % args}",
            file=sys.stderr,
            flush=True,
        )

    def do_GET(self):
        try:
            self._route()
        except (
            SubstrateError
        ) as exc:  # precise: the store refused, in the substrate's own words
            self._send_json(502, {"error": str(exc)})
        except (
            SystemExit
        ) as exc:  # auger's own fail-closed exit (no token to ask the store with)
            self._send_json(503, {"error": str(exc) or "auger exited"})
        except Exception as exc:  # never leak a traceback into a closed socket
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_HEAD(self):
        """HEAD answers like GET with no body: a monitor probing /health must not be answered with
        BaseHTTPRequestHandler's HTML `501 Unsupported method` from a JSON surface."""
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_POST(self):
        self._send_json(
            405,
            {
                "error": "only GET is served: this surface reads the embedding store",
                "allow": "GET",
            },
            {"Allow": "GET"},
        )

    do_PUT = do_PATCH = do_DELETE = do_POST

    def _route(self):
        parts = urlsplit(self.path)
        params = parse_qs(parts.query, keep_blank_values=True)
        m = EMBEDDINGS_SEARCH_ROUTE.match(parts.path)
        if m:
            # The path segment arrives percent-ENCODED (`urlsplit` does not decode), and the store's
            # namespace is the decoded name; `recall` re-encodes it exactly once when it builds the
            # upstream request (AUG-061's one boundary), so a spaced name must be decoded HERE or the
            # store is asked for the literal `a%20b`.
            return self._search(unquote(m.group("ns")), params)
        if parts.path == EMBEDDINGS_ALIAS_PATH:
            return self._search(self.server.namespace, params)
        if parts.path == EMBEDDINGS_HEALTH_ROUTE:
            status = embedding_status()
            return self._send_json(
                200,
                {
                    "tier": status["tier"],
                    "embedding_healthy": status["healthy"],
                    "provider": status["provider"],
                    "model": status["model"],
                },
            )
        # Discovery is part of the fix: a caller who guessed the path is told the real ones.
        return self._send_json(
            404,
            {
                "error": f"no such route: {parts.path}",
                "routes": list(EMBEDDINGS_ROUTES),
            },
        )

    def _search(self, ns: str, params: dict):
        q = (params.get("q") or [""])[0].strip()
        if not q:
            return self._send_json(
                400, {"error": f"q is required: {EMBEDDINGS_ROUTES[0]}"}
            )
        raw_limit = (params.get("limit") or [str(EMBEDDINGS_DEFAULT_LIMIT)])[0].strip()
        try:
            limit = int(raw_limit)
        except ValueError:
            return self._send_json(
                400, {"error": f"limit must be an integer, got {raw_limit!r}"}
            )
        if not 1 <= limit <= EMBEDDINGS_MAX_LIMIT:
            return self._send_json(
                400,
                {
                    "error": f"limit must be between 1 and {EMBEDDINGS_MAX_LIMIT}, "
                    f"got {limit}"
                },
            )
        project = (params.get("project") or [""])[0].strip() or None
        self._send_json(200, embedding_search(ns, q, limit, project_id=project))

    def _send_json(self, status: int, payload: dict, headers: dict | None = None):
        raw = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not getattr(self, "_head_only", False):
                self.wfile.write(raw)
        except OSError:
            # The caller hung up mid-response: there is nothing left to say, and a traceback from a
            # client that is already gone is noise, not a failure of the route.
            pass


class EmbeddingsHTTPServer(ThreadingHTTPServer):
    """The embedding route's server: one thread per request, and the alias route's namespace.

    `daemon_threads` matters — a client that opens a connection and never finishes it must not pin
    the process (or a test's teardown) on a request thread that will never return.
    `allow_reuse_address` keeps a restart after Ctrl-C from failing on TIME_WAIT.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler_class, namespace: str):
        self.namespace = namespace
        super().__init__(address, handler_class)


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


def candidate_questions_from_reply(resp, limit: int) -> list[str]:
    """Parse up to `limit` independently numbered/bulleted question lines from a completion."""
    if not isinstance(resp, dict) or limit <= 0:
        return []
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = (first.get("message") or {}).get("content") or ""
    if not isinstance(text, str):
        return []
    out = []
    for raw in text.splitlines():
        line = REPLY_PREFIX.sub("", raw.strip()).strip().strip('"').strip()
        if not line or "?" not in line:
            continue
        line = line[:QUESTION_MAX]
        out.append(line)
        if len(out) >= limit:
            break
    return out


def generate_candidate_questions(
    state: str, count: int
) -> tuple[list[str], float | None, str]:
    """Ask the configured large model for a candidate batch; return (questions, cost, error).

    The proposer uses the same model/key selection as `feedback`; generation cost is taken only
    from the provider's reported `usage.cost`, never estimated or fabricated.
    """
    keys = _jev_keys()
    if not keys:
        return [], None, "no OpenRouter key found for the question proposer"
    count = max(1, int(count))
    body = {
        "model": proposer_model(),
        "max_tokens": min(1000, 180 * count),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Propose exactly {count} distinct, specific, answerable project questions. "
                    "Return one question per line, numbered, and nothing else. Questions must "
                    "address the supplied thin decisions and project context; do not answer them."
                ),
            },
            {"role": "user", "content": state},
        ],
    }
    last = "no attempt"
    for key in keys:
        st, resp, _ = _req(
            PROPOSER_URL, "POST", body, {"Authorization": f"Bearer {key}"}, timeout=90
        )
        if st == 200 and isinstance(resp, dict):
            questions = candidate_questions_from_reply(resp, count)
            if questions:
                usage = resp.get("usage") or {}
                cost = usage.get("cost")
                cost = float(cost) if isinstance(cost, (int, float)) else None
                return questions, cost, ""
            last = f"HTTP {st}: the reply contained no parseable questions"
            continue
        last = f"HTTP {st}: {str(resp)[:180]}"
    return [], None, f"all {len(keys)} proposer keys failed; last: {last}"


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
    # An empty project id asks for the WHOLE namespace (the export render is namespace-scoped):
    # `project_id=eq.` matches nothing and would silently blind the caller.
    query = (
        "order=id.asc" if not project_id else f"project_id=eq.{project_id}&order=id.asc"
    )
    return select(ns, "edge", query)


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


def breaks_by_decision(edges: list[dict]) -> dict[str, list[dict]]:
    """Index local decision invalidations for read-side renderers.

    The write path deliberately leaves the target decision and its option rows untouched. A
    renderer therefore needs the edge table to distinguish an invalidated decision from a live one.
    Cross-project breaks are excluded just like ``edge_index``: their target belongs to a sibling
    namespace and is not a local decision in this render.
    """
    return {
        decision_id: targeted
        for decision_id, targeted in _group_break_edges(edges).items()
    }


def _group_break_edges(edges: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for e in edges:
        if (
            e.get("kind") == "breaks"
            and e.get("dst_kind") == "decision"
            and not e.get("dst_project")
        ):
            grouped.setdefault(e["dst_id"], []).append(e)
    return grouped


def invalidation_marker(edge_row: dict) -> str:
    """The stable reader-facing annotation for one breaks edge."""
    return f"INVALIDATED by {edge_row['src_id']} ({edge_row['id']})"


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

    The status write is `patch_or_rewrite` with the status and NOTHING else. Before GATE-RED-002
    this body also sent `proposed_by_model=""`, `scored_by_model=""` and `generation_cost=0.0` to
    get past the substrate's merged-row validation, which OVERWROTE the provenance every stored
    question carries (which model proposed it, which scored it, what generating it cost) with
    blanks the moment a question closed — and 400'd outright on a namespace whose `question`
    declaration predates those columns. The nulls that made the validator complain are handled
    where they are actually created (see `patch_or_rewrite`), so a row keeps its real values and
    the ones nobody set stay unset.
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
    return patch_or_rewrite(
        ns,
        "question",
        question_id,
        {"status": status},
    )


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


def supersession_for(ns: str, decision_id: str) -> tuple[bool, str]:
    """Whether a decision is superseded, and the successor that replaced it — "" names nobody.

    The record carries a supersession in two places: the decision row's `status`
    (SUPERSEDED_STATUS) and the supersedes edge — `src` the successor, `dst` the replaced
    decision, the direction record_supersessions writes. A status with no edge (the drift
    `cmd_status` renders as "no supersedes edge") reports superseded with NO successor: an
    unrecorded one is never invented. Both reads go through `select_or_empty` on purpose: this
    lookup decorates a recall that already succeeded — AUG-075's fail-closed stays at the
    retrieval — and a namespace whose tables were never declared (`auger init` never ran) holds
    no decisions at all, which is "no rows here", not a reason the verb stops rendering its hits.
    """
    found = select_or_empty(
        ns, "decision", f"id=eq.{decision_id}&select=status&limit=1"
    )
    if not found or found[0].get("status") != SUPERSEDED_STATUS:
        return False, ""
    for e in select_or_empty(
        ns,
        "edge",
        f"kind=eq.supersedes&dst_id=eq.{decision_id}&select=src_id&order=id.asc",
    ):
        if e.get("src_id"):
            return True, e["src_id"]
    return True, ""


def _tf_vector(text: str) -> dict:
    """Term frequencies of a text — the cheap local vector the lexical cosine lives on (AUG-063).

    `\\w+` runs on the lowercased text, so identifiers like `Postgres` and `postgres` collide into one
    term; that is wanted, since the gate's question texts and evidence contents are prose. No stop
    list, no stemming, no idf: this triages, it does not rank. Nothing here promises linguistic
    quality, only a stable cheap signal.
    """
    vec: dict = {}
    for term in re.findall(r"\w+", (text or "").lower()):
        vec[term] = vec.get(term, 0) + 1
    return vec


def _lexical_cosine(query: str, content: str) -> float:
    """Pure cosine over term-frequency vectors — 1.0 identical, 0.0 disjoint, never negative.

    Empty or punctuated-only sides (`\\w+` found nothing) score 0.0: an unmeasurable pair is a MISS
    signal only when COS_MISS is satisfied, and the MISS shortcut keeps its second condition, so an
    empty vector can never alone send the gate to a verdict (AUG-063's conservative policy).
    """
    a, b = _tf_vector(query), _tf_vector(content)
    if not a or not b:
        return 0.0
    dot = sum(n * b.get(term, 0) for term, n in a.items())
    na = math.sqrt(sum(n * n for n in a.values()))
    nb = math.sqrt(sum(n * n for n in b.values()))
    return dot / (na * nb) if dot > 0 and na and nb else 0.0


def gate_question(ns: str, project_id: str, text: str, limit: int = 5):
    """The gate: is this question already fully answered by evidence we already hold?

    Retrieval first — over this project's OWN namespace and then over every sibling namespace a
    bundle puts in reach (SPEC-002 section 2.1: the gate searches the whole bundle, which is what
    stops two projects re-deciding the same question and drifting apart) — then ONE JEV noul over the
    pooled evidence, so a bundle of six members still costs one model call. No stored evidence
    anywhere means no model call: nothing can answer it, so the verdict is None and the question
    stays open. A transport error is returned, never converted into a verdict (fail-closed).

    AUG-063: a LOCAL lexical cosine (term-frequency vectors, no embeddings — stdlib only) triages
    the pooled hits BEFORE the model is spent. The retrieval `score` is the substrate's BM25/keyword
    rank, an unbounded scale that cannot be compared with a similarity, so it re-ranks nothing here;
    the cosine is computed fresh over the TEXTS. Only the two unambiguous ends shortcut: a best
    cosine >= COS_HIT is an obvious hit (the query restates the evidence) and a best cosine <=
    COS_MISS an obvious miss; the wide gray zone between — where short texts are noisy and the
    model's judgement is worth its latency — falls through to the JEV path EXACTLY as before, and
    so does anything the triage cannot classify. The verdict-quality baseline is never regressed by
    this verb: when in doubt, ask the model.

    Returns (verdict, decision_id, src_project, err). `src_project` names the SIBLING whose namespace
    held the winning evidence, and is "" when the answer was this project's own; the caller puts it
    on the `satisfies` edge, so a reader can see the link crossed the boundary. On the COS_HIT
    shortcut the verdict is still T_ANSWERED — the same value the JEV path would have produced —
    so the downstream >= T_ANSWERED branches and their printouts are unchanged.
    """
    home = project_name(ns, project_id)
    places = [(ns, home)] + bundle_namespaces(ns, home)
    try:
        hits = recall_many([where for where, _ in places], text, limit=limit)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        return None, "", "", str(exc)
    if not hits:
        return None, "", "", None
    # ---- the AUG-063 prefilter: local lexical cosine over the query vs each hit's content.
    # `hits` is non-empty here and `max` over floats cannot raise, so the triage itself never takes
    # the gate down — anything it cannot classify falls through to the model.
    best_cos = max(_lexical_cosine(text, h.get("content", "")) for _, h in hits)
    if best_cos >= COS_HIT:
        # An obvious hit: the query near-verbatim restates stored evidence. Same answer the JEV
        # path returns for it — T_ANSWERED — with the decision link resolved exactly as that path
        # resolves it, and NO model call spent proving what the texts already say.
        v = T_ANSWERED
        for where, h in sorted(hits, key=lambda pair: -(pair[1].get("score") or 0)):
            key = h.get("key") or ""
            if where == ns:
                did, src = decision_for_evidence(ns, project_id, key), ""
            else:
                did = decision_named(key)
                # Read it back WHERE IT LIVES: a decision that cannot be read is not a link, and a
                # link to an unread row would be an invented one.
                if did and not select_or_empty(
                    where, "decision", f"id=eq.{did}&select=id&limit=1"
                ):
                    did = ""
                src = next((p for w, p in places if w == where), "")
            if did:
                return v, did, src, None
        return v, "", "", None  # answered, but by evidence that names no decision node
    if best_cos <= COS_MISS:
        # An obvious miss: nothing in the pooled evidence is remotely about this question. The same
        # (None, "", "", None) the JEV path returns for a sub-threshold noul — the question stays
        # open — but with NO model call spent on a pair the texts already separate.
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
            # `patch_or_rewrite`: a question stored by `ask`/`insert_question` carries the
            # substrate-materialized nulls in proposed_by_model/scored_by_model/generation_cost,
            # and its merged-row validation refuses any partial PATCH of that row (GATE-RED-002).
            # The gate's record must still land, so the refusal falls back to the rewrite.
            patch_or_rewrite(
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
        if not is_pending_memory(d)
        and isinstance(d.get("confidence"), (int, float))
        and 0 <= d["confidence"] < T_CONFIDENT
    ]
    unmeasured = [d for d in rows if not is_pending_memory(d) and _is_unmeasured(d)]
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
    # AUG-059: the question carries the decision's domain, so it retires the seed word too —
    # normally a no-op (the parent decision already advanced the row), but the advance keeps
    # the row and the evidence count in agreement however the question arrived. AUG-059-R2:
    # a refused advance rides the warnings channel, never the exit code.
    advance = advance_domain_status(ns, project_id, row["domain"])
    if advance["reached"] and warnings is not None:
        warnings.append(f"domain {row['domain']} -> {advance['reached']}")
    if advance["warning"] and warnings is not None:
        warnings.append(advance["warning"])
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
# AUG-059: the status a decision or question landing in the domain ADVANCES the seed word to.
# It is deliberately a reach word, not "answered": the coverage map's "answered" counts
# evidence (rows carrying the num), while a row's own column says whether the seed's
# not-reached sentence still describes it — one word each, and the advance keeps them from
# contradicting each other on the same line. Anything already past NOT-REACHED (a grid header's
# CASCADED, a word a person wrote) is never dragged back to it.
DOMAIN_REACHED_STATUS = "REACHED"
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


def resolve_answer_domain(raw: str) -> tuple[str, str]:
    """Validate an `answer --domain` value against the canonical grid (AUG-038).

    Returns (canonical_num, grid_name). Refuses — before anything is written — a value
    that is not one of the grid's numbers: the grid read here is THE one source of truth
    (no second table), so an unknown or malformed value can only mean a mistyped one,
    and silently storing it would fake a coverage claim the store cannot see.

    The refusal prints an example, and EVERY example it prints must be a value this same
    command accepts (AUG-115): the message used to demonstrate `4.05=<name>` — the exact
    shape it had just refused — so a reader who copied the example stayed stuck.
    """
    value = (raw or "").strip()
    try:
        grid = read_domain_grid()
    except (SystemExit, OSError) as exc:
        raise SystemExit(
            f"refused: --domain {raw!r} cannot be validated — the canonical domain grid "
            f"is unreadable, and no domain may be stored unverified: {exc}"
        ) from exc
    by_num = {e["num"]: e for e in grid}
    entry = by_num.get(value)
    if entry is None:
        allowed = f"{grid[0]['num']}-{grid[-1]['num']} ({len(grid)} domains)"
        example = grid[4] if len(grid) > 4 else grid[0]
        raise SystemExit(
            f"refused: unknown domain {raw!r} — --domain takes one bare canonical grid "
            f"number, {allowed}, with nothing appended to it (no name, no '=', no '-'); "
            f"nothing was stored. Accepted: --domain {example['num']} "
            f"({example['num']} is the {example['name']} domain)"
        )
    return entry["num"], entry["name"]


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

    A number that ALREADY carries evidence (a decision or question row with that domain) seeds at
    REACHED, not at the seed word (AUG-059): the row is born in agreement with the coverage map
    instead of one answer stale — `bind` may have answered into the grid before anyone seeded it.
    The evidence read is the same shape `domain_coverage` counts from, and a read that fails
    leaves every row at the seed word rather than guessing.
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
    reached: set = set()
    try:
        for rows_ in (
            select(ns, "decision", f"project_id=eq.{bind}&select=domain"),
            select(ns, "question", f"project_id=eq.{bind}&select=domain"),
        ):
            for r in rows_:
                num = str(r.get("domain") or "")
                if num:
                    reached.add(num)
    except SystemExit:
        reached = set()  # no evidence read, no advance claim — the seed word stands
    missing = [e for e in grid if e["num"] not in have]
    if not missing:
        return {
            "grid": len(grid),
            "written": 0,
            "present": len(grid),
            "ids": [],
            "reached": 0,
        }
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
            "status": (
                DOMAIN_REACHED_STATUS if entry["num"] in reached else DOMAIN_SEED_STATUS
            ),
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
        "reached": sum(1 for r in rows if r["status"] == DOMAIN_REACHED_STATUS),
    }


def _advance_warning(row: dict, *errors: object) -> str:
    """The ONE line a refused domain advance earns (AUG-059-R2): the domain number, the row
    id, and the substrate's own error(s), whitespace-folded so the line stays one line."""
    detail = "; ".join(" ".join(str(e).split()) for e in errors)
    return (
        f"WARNING domain {row.get('num') or '?'} row {row.get('id') or '?'}: the seed status "
        f"advance ({DOMAIN_SEED_STATUS} -> {DOMAIN_REACHED_STATUS}) was refused — {detail}. "
        "The answer itself is stored; the domain row keeps its stored status, and `status` "
        "keeps naming the drift until the advance lands."
    )


def _advance_via_rewrite(ns: str, row: dict, patch_exc: SystemExit) -> dict:
    """Advance a seeded domain row the substrate's PATCH cannot touch (AUG-059-R2).

    The substrate validates the MERGED row on every PATCH, and its own INSERT normalization
    materializes an omitted column as an explicit JSON null in the stored line — so a seeded
    row whose grid header reads `terminating_ring: null` (35 of the 44) carries that null on
    disk, and EVERY PATCH on it 422s on the integer column no matter what the payload says
    (measured live against the running substrate 2026-10-27: omitting the key changes nothing
    because the stored line carries the null, and re-sending the null is refused outright).
    No PATCH payload can repair such a row, the column's integer type is the schema contract
    and stays, and the row's ring must not be FABRICATED to satisfy a validator — so the one
    write shape left is a read-modify-write through the primary key: DELETE the stored line,
    INSERT the same image with the advance applied and every null-valued key DROPPED from the
    body (an explicit null is refused at INSERT too; the omission re-materializes as null,
    leaving the stored shape byte-equivalent to what seeding wrote).

    Ordering is delete-then-insert because the reverse is not safe: a duplicate primary key
    would make the DELETE take BOTH copies. If the delete landed but the insert did not, the
    ORIGINAL row is put back before the failure is reported — the advance may fail, the row
    may not be silently lost.
    """
    quiet = {"reached": "", "warning": ""}
    replacement = {k: v for k, v in row.items() if v is not None}
    replacement["status"] = DOMAIN_REACHED_STATUS
    try:
        delete_row(ns, "domain", str(row["id"]))
    except SystemExit as delete_exc:
        # the row is untouched — nothing to restore, just name both refusals
        quiet["warning"] = _advance_warning(row, patch_exc, delete_exc)
        return quiet
    try:
        insert(ns, "domain", replacement)
    except SystemExit as insert_exc:
        try:
            insert(ns, "domain", {k: v for k, v in row.items() if v is not None})
            note = "the original row was restored unchanged"
        except SystemExit as restore_exc:
            note = (
                f"the row itself could not be restored ({' '.join(str(restore_exc).split())})"
                " — it is GONE, and `status` renders the domain ABSENT until it is re-seeded"
            )
        quiet["warning"] = _advance_warning(row, patch_exc, f"{insert_exc} — {note}")
        return quiet
    return {"reached": DOMAIN_REACHED_STATUS, "warning": ""}


def advance_domain_status(ns: str, project_id: str, num: str) -> dict:
    """Retire the seed word on one domain row: NOT-REACHED -> REACHED (AUG-059).

    The coverage line derives `answered` from the decision/question rows carrying the num, but
    prints the row's OWN status column beside it — and before this advance nothing ever moved
    that column, so a domain that held an answer read `answered ... (row status NOT-REACHED)`
    and contradicted itself on one line. The WRITE verb is where the column moves (`status`
    never writes), because the seed word's own sentence — "not reached: no rows yet" — stopped
    being true the moment a row carrying the num landed.

    The row is chosen the way `domain_coverage` reads them (the API cannot filter on an empty
    value, and a pre-`start` seed leaves the rows unbound): the slice is read whole, a row
    bound to this project outranks an unbound one, and a sibling project's row is skipped.
    The read takes the row's WHOLE image, not the three keys the guard needs, because the
    rewrite path below re-writes the row and must not invent the fields it carries.

    The guard is equality with the seed word, and it does double duty: a row already past
    NOT-REACHED (a grid header's CASCADED, a word a person wrote) is never dragged back to
    REACHED, and a second decision in the same domain is idempotent. No failure here is
    fatal: an answer must land even where the grid was never seeded or the row could not be
    written. But since AUG-059-R2 a REFUSED advance is not silent either — the substrate
    rejects every update of a null-ring row (its INSERT normalization stores the omitted ring
    as an explicit null its PATCH then refuses), so when the plain PATCH 422s the advance
    falls back to a delete+insert rewrite of the same row image, and when even that fails the
    caller prints ONE WARNING line naming the domain, the row id and the substrate's error.
    The answer still exits 0 with the decision stored, and `status`'s drift note names the
    same row on the next render.

    Returns {"reached": <the new status when the row moved, "" when there was nothing to
    move>, "warning": <"" or the one WARNING line for a refused advance>}, so the calling
    verb can say one honest line about what changed — or about what refused to.
    """
    quiet = {"reached": "", "warning": ""}
    target = (num or "").strip()
    if not target:
        return quiet
    try:
        rows = select(ns, "domain", f"num=eq.{target}")
    except SystemExit:
        # no read, so no row to name — a stuck row still surfaces in `status`'s drift note
        return quiet
    row = next(
        (r for r in rows if r.get("project_id") == project_id),
        next((r for r in rows if not r.get("project_id")), None),
    )
    if row is None or str(row.get("status") or "") != DOMAIN_SEED_STATUS:
        return quiet
    try:
        patch(ns, "domain", row["id"], {"status": DOMAIN_REACHED_STATUS})
    except SystemExit as patch_exc:
        return _advance_via_rewrite(ns, row, patch_exc)
    return {"reached": DOMAIN_REACHED_STATUS, "warning": ""}


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
    performed. And evidence does NOT require a `domain` row (AUG-112): the README blesses `init`
    without `--seed-domains`, so a number can be answered while the coverage map has no row for it —
    such a number is counted in `answered`, named in `no_row_evidence`, and kept OUT of `absent`,
    because "we hold an answer for 4.05" and "we have no row for 4.05" cannot both be the sentence
    the report prints. Its line is never dropped either: the union below includes the numbers the
    evidence carries, so a grid that cannot be read still cannot hide what was decided.
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
    # The union carries the EVIDENCE numbers as well as the grid's and the rows': a decision
    # carrying a number the grid cannot be read for (or a number no row was ever seeded for)
    # must still get a line, or the report silently drops the one thing it was asked about.
    for num in sorted(set(by_num) | set(mine) | set(counts)):
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
    # AUG-112: the row is NOT a precondition of the answer. Counting only rows-present evidence
    # reported `answered 0` on the gridless path while a decision carried 4.05 — the report
    # contradicting the store, and `dump`, about the same row.
    answered = [ln["num"] for ln in lines if ln["evidence"] > 0 and not ln["off_grid"]]
    # The numbers that carry evidence with NO `domain` row: their own class, because such a line
    # is neither ABSENT (something IS recorded) nor a seeded row (the row was never written).
    no_row_evidence = [
        ln["num"] for ln in lines if ln["row"] is None and ln["evidence"] > 0
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
        # ABSENT keeps its narrow meaning: a grid number with no row AND no evidence. A number
        # that holds evidence is reported as answered instead of being counted absent.
        "absent": [
            ln["num"] for ln in lines if ln["row"] is None and not ln["evidence"]
        ],
        "off_grid": [ln["num"] for ln in present if ln["off_grid"]],
        "answered": len(answered),
        # Seeded rows that hold NO evidence — subtracted over ROWS, not `seeded - answered`: an
        # answered number with no row (the gridless path) is not part of the seeded slice, and the
        # old subtraction went NEGATIVE for it.
        "not_reached": sum(
            1 for ln in present if not ln["off_grid"] and not ln["evidence"]
        ),
        # Evidence-bearing numbers whose `domain` row is absent, named so the report says which
        # covered domain has no row for the coverage map.
        "no_row_evidence": no_row_evidence,
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
    status GENUINELY disagrees with it, the line says so rather than choosing one and hiding the
    other: a row still reading NOT-REACHED while it holds an answer is a status nobody updated,
    which is a fact about the record and not something a report gets to smooth over (AUG-059;
    `answer` advances the row precisely so this note is never that contradiction). REACHED with
    evidence is the pair the advance CREATES — the same agreement `answered` states, so it is
    rendered as the plain word, not narrated back with a parenthetical.

    Three cases, kept apart on purpose. A SEEDED row prints the ring it holds — or
    `none (not opened)` when the grid recorded that the domain was never opened. An ABSENT domain
    prints ABSENT with the grid's numbers, because what is missing is the ROW, and naming what the
    grid expects while saying the row is gone is the whole point of the line. A number the grid does
    not define says so, so a stray row cannot pass for one of the 44.

    A FOURTH case, because two of the three would each be half wrong (AUG-112): a number that
    carries evidence with no `domain` row. It prints `answered` — the evidence IS the answer — and
    says in the same line that the row is missing, so neither claim hides behind the other.
    """
    r, g = ln["row"], ln["grid"]
    stored = str((r or {}).get("status") or "")
    no_row_evidence = r is None and bool(ln["evidence"])
    if r is None:
        state = "answered" if no_row_evidence else "ABSENT"
    elif ln["evidence"]:
        state = "answered"
    else:
        state = stored or DOMAIN_SEED_STATUS
    # the note is for DISAGREEMENT only: the advance's own REACHED-with-evidence pair agrees,
    # and a seed-status row without evidence needs no echo of its own status word either
    agrees = bool(ln["evidence"]) and stored == DOMAIN_REACHED_STATUS
    drift = (
        f"  (row status {stored})" if stored and stored != state and not agrees else ""
    )
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
        + (
            "  (no `domain` row: the evidence is counted answered above; the coverage map"
            " has no row for this num)"
            if no_row_evidence
            else ""
        )
        + drift
    )


# ---------------------------------------------------------------- verbs
def cmd_init(a):
    ns = a.namespace
    # Resolve the declaration root before the namespace POST. A custom substrate URL without
    # a matching local path must never fall through to ~/duckbrain/namespaces.
    d = os.path.join(_init_ns_dir(ns), "tables")
    namespace_path = os.path.dirname(d)
    tables_dir_existed = os.path.isdir(d)
    namespace_dir_existed = os.path.isdir(namespace_path)
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
    os.makedirs(d, exist_ok=True)
    changed = []
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
            changed.append((p, current))
            wrote.append(name)
    st, live, _ = db(ns_tables_path(ns))
    have = sorted(
        t["name"] for t in (live.get("tables", []) if isinstance(live, dict) else [])
    )
    print(f"namespace {ns}: {'created' if created else 'existing'}")
    print(f"declared: {len(have)}/{len(COLS)} tables -> {', '.join(have)}")
    if not have:
        # A full miss means the API did not read the tree we just touched. Restore every file
        # changed by this invocation before refusing, so a wrong-root configuration cannot
        # leave declarations behind for a later run to mistake for substrate state.
        for p, previous in reversed(changed):
            if previous is None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(p)
            else:
                with open(p, "w") as f:
                    f.write(previous)
        if not tables_dir_existed:
            with contextlib.suppress(OSError):
                os.rmdir(d)
        if not namespace_dir_existed:
            with contextlib.suppress(OSError):
                os.rmdir(namespace_path)
        if wrote:
            print(f"rolled back declarations: {', '.join(wrote)}")
        raise SystemExit(
            f"declared: 0/{len(COLS)} tables — nothing visible to the API under {d}; "
            f"namespaces base resolved from {NAMESPACES_PATH_ENV}"
        )
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
            if tally["reached"]:
                # AUG-059: the evidence the project already held seeded those rows REACHED
                print(
                    f"  {tally['reached']} new row(s) start {DOMAIN_REACHED_STATUS} — the "
                    f"project already holds decision/question rows carrying those numbers"
                )
            print(
                f"  each other new row starts {DOMAIN_SEED_STATUS}: owner "
                f"{DOMAIN_SEED_OWNER!r}, trigger {DOMAIN_SEED_TRIGGER!r}, containment "
                f"{DOMAIN_SEED_CONTAINMENT!r} ('{DOMAIN_SEED_REASON}' — the `domain` table "
                f"has no reason column)"
            )
            print(f"  ids: {tally['ids'][0]} … {tally['ids'][-1]}")
        print(
            f"  grid source: {domain_grid_dir()} (read, never copied — a copy would drift)"
        )
    return 0


def _project(ns, pid=None):
    if pid:
        rows = select(ns, "project", f"id=eq.{pid}")
        if not rows:
            raise SystemExit(
                "no project row — run: auger start --seed-file <f> [--name N]"
            )
        return rows[0]

    rows = select(ns, "project", "order=created_at.desc")
    if not rows:
        raise SystemExit("no project row — run: auger start --seed-file <f> [--name N]")
    if len(rows) > 1:
        current = rows[0]
        raise SystemExit(
            f"refused: namespace {ns!r} has multiple projects; implicit selection would choose "
            f"newest project {current['id']} ({current.get('name', '')!r}). "
            "Pass -p/--project-id to choose explicitly; nothing was written"
        )
    return rows[0]


def cmd_start(a):
    ns = a.namespace
    pid = a.id or f"P-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    if a.project_id is not None:
        # The top-level --project-id/-p resolves a project row for verbs that READ one;
        # `start` never consumes it — it takes --id or mints its own. Name the flag
        # instead of silently swallowing it (AUG-074: `start -p X` used to run as if
        # the flag had never been given).
        action = f"using --id {pid}" if a.id else f"minting {pid}"
        print(f"note: -p/--project-id is not honored by start; {action}")
    seed = a.seed or ""
    if a.seed_file:
        with open(a.seed_file, errors="replace") as f:
            seed = f.read()
    name = a.name or ns
    existing = select(ns, "project", f"name=eq.{name}&limit=1")
    if existing:
        raise SystemExit(
            f"refused: project name {name!r} already exists as {existing[0]['id']}; "
            "nothing was written"
        )
    row = {
        "id": pid,
        "name": name,
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
    # AUG-092: the "embedded" claim belongs to the substrate, not to us — read its /health and
    # report the state it actually asserts, so a degraded substrate never reports a row as embedded.
    print(f"seed stored ({len(seed)} chars) + {embed_claim(retrieval_tier())}")
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
    ns: str,
    project_id: str,
    nq: dict,
    noul,
    *,
    text: str = "",
    proposed_by_model: str = "",
    scored_by_model: str = "",
    generation_cost: float | None = None,
    status: str = "open",
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
    if status not in QUESTION_STATES:
        return "", f"unknown question status {status!r}"
    slug = str((nq or {}).get("choice") or "").strip()
    generated_text = bool(text)
    if text:
        text = str(text).strip()
        if not text or "?" not in text:
            return "", "proposed text is not a question"
    else:
        if not slug:
            return "", "JEV proposed no question"
        criterion = ASK_NEW_QUESTION_CRITERIA.get(slug)
        if criterion is None:
            return "", f"JEV proposed unknown question criterion {slug!r}"
        if slug == "none":
            return "", "JEV proposed none: nothing left worth asking"
        text = criterion["sentence"]
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
    # For generated candidates JEV is the duplicate gate. Do not second-guess its calibrated
    # verdict with local text equality; retain the legacy exact-text guard for the criterion path.
    if not generated_text:
        want = text.casefold()
        for q in project_questions:
            if str(q.get("text") or "").strip().casefold() != want:
                continue
            if q.get("status") == "open":
                return "", f"already open as {q.get('id')}"
            if q.get("status") not in SETTLED_STATES:
                continue
            facets = select(ns, "facet", f"question_id=eq.{q.get('id')}&order=id.asc")
            closure = next(
                (
                    f
                    for f in facets
                    if f.get("status") == "closed"
                    and str(f.get("closed_by") or "").strip()
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
        "status": status,
        "jev_already_answered": float(noul),
        "jev_checked_at": datetime.now(timezone.utc).isoformat(),
        "proposed_by_model": proposed_by_model,
        "scored_by_model": scored_by_model,
    }
    if generation_cost is not None:
        row["generation_cost"] = generation_cost
    try:
        insert(ns, "question", row)
    except SystemExit as exc:
        return "", f"the question row was refused — {exc}"
    for name in facet_set():
        try:
            facet(
                ns,
                project_id,
                qid,
                name,
                status="closed" if status == "budget_thin" else "open",
                note="budget-thin: not asked within this run's ceiling"
                if status == "budget_thin"
                else "",
            )
        except SystemExit as exc:
            return qid, f"the question row landed but facet {name!r} did not — {exc}"
    return qid, ""


def _ask_generated_candidates(ns: str, pid: str, state: str, seats: list) -> int | None:
    """Generate, JEV-gate and rank a candidate batch; None means use the legacy ask path."""
    if not seats:
        return None
    ceiling = feedback_budget()
    # One candidate of headroom: the run BREAKS at the ceiling (AUG-095) instead of recording the
    # overflow, so the extra candidate is a margin, never a budget_thin row.
    count = min(max(1, ceiling + 1), max(1, len(seats) * 2))
    candidates, generation_cost, generation_error = generate_candidate_questions(
        state, count
    )
    candidates = candidates[:count]
    if not candidates:
        print(
            f"question generator unavailable — {generation_error}; using JEV's single-subject fallback"
        )
        return None

    candidate_ids = [f"candidate_{i + 1}" for i in range(len(candidates))]
    questions = {
        "next_subject": {
            "type": "choice",
            "instructions": "Which subject needs coverage next?",
            "criteria": {
                "none_needed": "evidence is sufficient for a v1",
                "deployment_and_alerts": "deployment, boot survival, failure notification",
                "retention_and_privacy": "retention horizons, privacy, data lifecycle",
                "concurrency_and_scaling": "concurrency, load, growth beyond v1",
                "testability": "how correctness will be proven",
            },
        },
        "next_question": {
            "type": "choice",
            "instructions": (
                "Rank the candidate questions; choose the most valuable next question. "
                "Reject any candidate that repeats or paraphrases a stored open question."
            ),
            "criteria": {candidate_ids[i]: text for i, text in enumerate(candidates)},
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
    for candidate_id in candidate_ids:
        questions[f"{candidate_id}_already_answered"] = {
            "type": "noul",
            "instructions": (
                f"Is candidate {candidate_id} already fully answered by the supplied evidence?"
            ),
        }
    ans, err = jev(state, questions)
    if err or not isinstance(ans, dict):
        print(
            f"JEV unavailable for generated candidates — {err}; using JEV's single-subject fallback"
        )
        return None

    answers = ans.get("answers") or {}
    selected = str((answers.get("next_question") or {}).get("choice") or "")
    order = list(range(len(candidates)))
    if selected in candidate_ids:
        rank = candidate_ids.index(selected)
        order.remove(rank)
        order.insert(0, rank)
    model = proposer_model()
    print(f"generated candidates: {len(candidates)}  proposed by: {model}")
    print(f"completeness: {(answers.get('completeness') or {}).get('score')} / 4")
    asked = 0
    hit_ceiling = False
    for idx in order:
        candidate_id = candidate_ids[idx]
        text = candidates[idx]
        already = _noul(answers, f"{candidate_id}_already_answered")
        if already is None:
            print(
                f"  UNGATED {candidate_id}: {text} (JEV returned no already_answered verdict)"
            )
            continue
        if already >= T_ANSWERED:
            print(
                f"  DROPPED {candidate_id}: already answered (JEV noul {already:.2f}) — {text}"
            )
            continue
        # AUG-095: the ceiling bounds ASKING, exactly as in `feedback`. Once it is reached the
        # run STOPS — the over-ceiling candidates are generated, not owed, so they are left
        # unrecorded (a later run regenerates them) instead of being piled up as budget_thin rows
        # the user never asked for and never sees a stop signal about.
        if asked >= ceiling:
            hit_ceiling = True
            break
        qid, why = store_proposed_question(
            ns,
            pid,
            {},
            already,
            text=text,
            proposed_by_model=model,
            scored_by_model=JEV_MODEL,
            generation_cost=generation_cost,
            status="open",
        )
        if not qid:
            print(f"  NOT STORED {candidate_id}: {why} — {text}")
            continue
        asked += 1
        print(
            f"  ASKED {qid}  {text}  [proposed={model}; scored={JEV_MODEL}; generation_cost={generation_cost}]"
        )
        if why:
            print(f"    WARNING {why}")
    if hit_ceiling:
        print(
            f"  STOPPED at the ceiling: {ceiling} question(s) asked this run — the remaining "
            f"candidate(s) were left unasked and not recorded"
        )
        escalate(
            ns,
            pid,
            question=(
                f"budget-thin: true — the run hit its ceiling of {ceiling} question(s) and "
                f"stopped; the over-ceiling candidate(s) were left unasked and NOT recorded "
                f"(a later run regenerates them)"
            ),
            options="",
            default_action="the run asked its ceiling and stopped; a later run regenerates the rest",
            risk="the over-ceiling candidates stay unasked until a later run regenerates them",
        )
    print(f"ceiling: {ceiling}  asked: {asked}")
    if hit_ceiling:
        print("budget-thin: true")
    return 0


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
    # AUG-095: a decision that already has an OPEN follow-up (an `opens` edge to an unanswered
    # question) is not a candidate for re-drilling — asking again would pile a second open
    # question on the same decision. `feedback` already skips these and says so out loud; `ask`
    # must not re-drill them silently.
    drilled = drilled_decisions(ns, pid)
    if drilled:
        undrilled = []
        for d in seats:
            if d["id"] in drilled:
                print(
                    f"  {d['id']}  already drilled: {drilled[d['id']]} is open and awaiting an answer"
                )
            else:
                undrilled.append(d)
        seats = undrilled
    unknown = select(ns, "unknown", f"project_id=eq.{pid}")
    unans = select(ns, "question", f"project_id=eq.{pid}&status=eq.open&order=id.asc")
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
    stored_open_questions = (
        "\n".join(
            f"- {q.get('id', 'unknown')}: {' '.join(str(q.get('text') or '').split())[:100]}"
            for q in unans[:20]
        )
        or "(none)"
    )
    state = (
        f"SEED:\n{p.get('seed', '')}\n\nDECISIONS:\n{decisions}\n\n"
        f"OPEN QUESTIONS: {len(unans)}\n"
        f"STORED OPEN QUESTION TEXTS (up to 20; each truncated to 100 characters; do not "
        f"propose these again or paraphrase them):\n"
        f"{stored_open_questions}\n"
        f"UNKNOWNS: {len(unknown)}\n\nRELATED EVIDENCE:\n{evidence}"
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
    # The generated-candidate path runs FIRST and owns the ask outright when it can serve one:
    # it generates the batch, has JEV rank/gate it and stores the winner, and it prints its own
    # completeness. Only when it declines (no seats, no candidates, or JEV unreachable for the
    # batch) is the legacy single-subject question below needed.
    #
    # AUG-110 speculatively started BOTH (this call and the legacy `jev(state, qs)`) in a thread
    # pool to overlap two remote waits — but the legacy answer was then DISCARDED whenever the
    # candidate path answered, so the common path paid JEV twice for one question (GATE-RED-002:
    # the stored-question-context test pins exactly one JEV call and saw two). A duplicate paid
    # model call is not worth the overlap it buys on a path that only runs when generation fails,
    # so the speculation is gone: the fallback pays its own round trip, and nothing is asked twice.
    generated_rc = _ask_generated_candidates(ns, pid, state, seats)
    if generated_rc is not None:
        return generated_rc
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
    stored_qid, store_why = store_proposed_question(
        ns,
        pid,
        nq,
        already,
        proposed_by_model=ans.get("model") or JEV_MODEL,
        scored_by_model=JEV_MODEL,
    )
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


def finalize_decision(
    ns: str, did: str, status: str, scope: str = DEFAULT_SCOPE
) -> None:
    """Set a provisional decision's FINAL status without inventing a scope.

    AUG-009's contract covers this UPDATE too, and the two substrates force two different shapes:

    * a namespace that DECLARES `scope` cannot take the key's OMISSION — the declared-table layer
      materialises a missing column as JSON null and then refuses to write it (measured live
      2026-10-23: 422 UNPROCESSABLE_ENTITY "Column 'scope' (varchar) requires a string, got null in
      table 'decision'"). So the default travels as the EMPTY value, which `decision_scope` and
      SPEC-002 both read as the default (`project | bundle (-1/empty = project)`) — never as the
      default's NAME, which made every default-scope row indistinguishable from an explicitly
      project-scoped one and returned `scope: "project"` to every reader (GATE-RED-001).
    * a namespace whose declaration PREDATES the column answers 400 VALIDATION_ERROR "Unknown
      column(s) in PATCH body: scope" instead — the same split insert_decision handles — and is
      finalized with the key left off entirely, because there is no column to satisfy.
    """
    values = {"status": status, "scope": "" if scope == DEFAULT_SCOPE else scope}
    try:
        patch(ns, "decision", did, values)
        return
    except SystemExit as exc:
        if not UNDECLARED_SCOPE.search(str(exc)):
            raise
    # A namespace whose declaration predates the column has nothing to satisfy — the same downgrade
    # insert_decision makes at INSERT time — so it is finalized with the key left off entirely.
    patch(ns, "decision", did, {"status": status})


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
    # A project may belong to several bundles, and bundle-scoped decisions do not carry a
    # bundle_id. Resolve the shared bundle(s) from membership: a sibling reachable only through a
    # different bundle has a different contract and must not be invalidated by this answer.
    home_bundle_ids = {b["id"] for b in bundles_of(ns, home) if b.get("id")}
    for project in bundle_siblings(ns, home):
        member_rows = select_or_empty(
            ns, "bundle_member", f"project=eq.{project}&order=id.asc"
        )
        if not home_bundle_ids.intersection(
            m.get("bundle_id") for m in member_rows if m.get("bundle_id")
        ):
            continue
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


def pending_answer_candidates(ns: str, project_id: str) -> list[dict]:
    """Return unfinished answers for one project, regardless of the requested final status."""
    return [
        decision
        for decision in select(
            ns, "decision", f"project_id=eq.{project_id}&order=id.asc"
        )
        if is_pending_memory(decision)
    ]


def pending_answer_matches(
    ns: str,
    pending: dict,
    expected: dict,
    option_labels: list[str],
    invalidates: list[str],
    supersedes: list[str],
) -> bool:
    """Match a retry to its provisional answer without adding another active decision."""
    for field in (
        "project_id",
        "domain",
        "question_id",
        "chosen",
        "why_not",
        "reversal_cost",
        "confidence",
    ):
        if pending.get(field) != expected.get(field):
            return False
    if pending_requested_scope(pending) != (expected.get("scope") or DEFAULT_SCOPE):
        return False
    metadata = pending_metadata(pending)
    if metadata.get("invalidates", []) != invalidates:
        return False
    if metadata.get("supersedes", []) != supersedes:
        return False
    if pending_final_status(pending) != expected["final_status"]:
        return False
    stored_options = select(
        ns, "option", f"decision_id=eq.{pending['id']}&order=id.asc"
    )
    stored_labels = [option.get("label") for option in stored_options]
    # A substrate death can happen while the option rows are being inserted too. The writer is
    # ordered, so a retry may see an empty or prefix subset; validate every row that did land and
    # let the retry insert only the missing suffix. An arbitrary subset or an extra row is refused
    # rather than silently changing the logical answer.
    return stored_labels == option_labels[: len(stored_labels)]


def encode_why_not(
    raw: str | list[str] | None, option_rows: list[dict], chosen: dict | None
) -> str:
    """Keep legacy reasons scalar, but preserve each repeated reason with its rejected option."""
    values = raw if isinstance(raw, list) else [raw] if raw is not None else []
    if not values:
        return ""
    rejected = [
        option for option in option_rows if not chosen or option["id"] != chosen["id"]
    ]
    if len(values) == 1:
        return values[0]
    if len(values) != len(rejected):
        raise SystemExit(
            f"refused: --why-not was repeated {len(values)} times, but this answer has "
            f"{len(rejected)} rejected options; provide exactly one reason per rejected "
            "option (in option order), or pass --why-not once for a shared reason; "
            "nothing was written"
        )
    return WHY_NOT_PREFIX + json.dumps(
        [
            {"option_id": option["id"], "label": option["label"], "reason": reason}
            for option, reason in zip(rejected, values)
        ],
        separators=(",", ":"),
    )


def why_not_records(value: str | None, options: list[dict]) -> list[dict]:
    """Decode the structured repeated-reason form, using current option labels for rendering."""
    if not isinstance(value, str) or not value.startswith(WHY_NOT_PREFIX):
        return []
    try:
        encoded = json.loads(value[len(WHY_NOT_PREFIX) :])
    except (TypeError, ValueError):
        return []
    if not isinstance(encoded, list):
        return []
    by_id = {option.get("id"): option for option in options}
    records = []
    for item in encoded:
        if not isinstance(item, dict) or not item.get("option_id"):
            return []
        option = by_id.get(item["option_id"], item)
        records.append(
            {
                "option_id": item["option_id"],
                "label": option.get("label") or item.get("label") or "",
                "reason": item.get("reason") or "",
            }
        )
    return records


def why_not_display(value: str | None, options: list[dict]) -> str:
    """Render one stored reason, or every structured reason with its option identity."""
    records = why_not_records(value, options)
    if not records:
        return value or "none"
    return "; ".join(
        f"{record['option_id']} ({record['label']}) — {record['reason']}"
        for record in records
    )


def why_not_dump_lines(value: str | None, options: list[dict]) -> list[str]:
    """Render per-option reasons as indented dump lines; legacy values keep their old shape."""
    records = why_not_records(value, options)
    if not records:
        return [f"   why not  : {value or '(not recorded)'}"]
    return [
        "   why not  :",
        *[
            f"     {record['option_id']} ({record['label']}) — {record['reason']}"
            for record in records
        ],
    ]


def cmd_answer(a):
    if a.confidence is not None and (
        not math.isfinite(a.confidence) or not 0 <= a.confidence <= 1
    ):
        raise SystemExit(
            f"refused: confidence must be between 0 and 1 inclusive; "
            f"nothing was stored (received {a.confidence!r})"
        )
    ns, pid = a.namespace, a.project_id
    p = _project(ns, pid)
    pid = p["id"]
    scope = (a.scope or "").strip().lower()
    if scope and scope not in DECISION_SCOPES:
        raise SystemExit(
            f"unknown decision scope {a.scope!r}: expected one of {', '.join(DECISION_SCOPES)}"
        )
    # AUG-038, fail closed BEFORE any decision/options/evidence write: a domain that is not one
    # of the canonical grid's numbers is refused here, while refusing is still free. The empty
    # value keeps its existing contract (a decision may name no domain at all).
    domain_name = ""
    if (a.domain or "").strip():
        a.domain, domain_name = resolve_answer_domain(a.domain)
    # The id is namespace-scoped, so the read is too: a sibling project's rows are exactly what a
    # per-project count could not see. An unfinished answer is different: it owns its id and is the
    # only retry target, whether the caller repeats --id or lets the CLI find the one pending answer.
    pending = None
    repairing = False
    if a.id:
        pending_rows = pending_answer_candidates(ns, pid)
        pending = next((d for d in pending_rows if d.get("id") == a.id), None)
        if pending is not None:
            did = pending["id"]
            repairing = True
        else:
            did = a.id
    else:
        # Keep the established auto-mint read first. The pending scan follows it so the hot path
        # retains its page-safe highest-id query and its existing query/order contract.
        did = next_id(ns, "decision", "D", width=decision_id_width(ns))
        pending_rows = pending_answer_candidates(ns, pid)
        if pending_rows:
            if len(pending_rows) != 1:
                raise SystemExit(
                    "refused: more than one answer is pending memory repair in this project; "
                    "pass --id for the specific provisional decision before recording another answer"
                )
            pending = pending_rows[0]
            did = pending["id"]
            repairing = True
    # Refuse every precondition that can be decided from the requested IDs before inserting the
    # decision or any of its options. `close_question` still defends its own public contract below,
    # but discovering a missing question there is too late for `answer`: the answer rows already
    # exist by then. The same ordering makes an explicit retry idempotently loud instead of allowing
    # duplicate decision and option IDs into stores that do not enforce primary-key uniqueness.
    qid = (a.question_id or "").strip()
    if not repairing and not a.id:
        # AUG-070: the mint read happens-before other writers' writes, so the id `next_id` derived
        # can already be stored by the time this process checks — or by the time its own INSERT
        # lands. DuckBrain enforces NO uniqueness, so a losing writer's duty is to RE-MINT from the
        # live highest id and retry, never to exit with the answer lost.
        for attempt in range(1, AUG070_ID_ATTEMPTS + 1):
            if not node_exists(ns, "decision", did):
                break
            did = next_id(ns, "decision", "D", width=decision_id_width(ns))
        else:
            # AUG-107: a re-mint that keeps re-deriving the SAME taken id is the signature of a
            # stored id outside the register's law, not of contention. The mint now SKIPS such
            # rows when it derives its id, so a namespace holding them still refuses here only
            # when the legal ids themselves are taken — the malformed rows are then the first
            # thing to clean up. Name that first; a genuine two-writer race is still possible
            # and keeps its own cause below, scoped to the collision wording the bound retains.
            poison_rows = select(
                ns,
                "decision",
                f"select=id&order=id.desc&limit={ID_LAW_SCAN_ROWS}",
            )
            poison_ids = _out_of_law_ids(poison_rows, "D")
            if poison_ids:
                raise SystemExit(
                    f"refused: the auto-minted decision id {did!r} is taken — re-minted and "
                    f"re-checked {AUG070_ID_ATTEMPTS} times against the live highest id and every "
                    f"attempt collided; NOTHING was stored. Likely cause: this namespace stores "
                    f"malformed decision ids outside the register's id law `D-<digits>` "
                    f"({', '.join(poison_ids)}); the mint ignores them when deriving ids, but a "
                    f"namespace full of out-of-law rows (or rows crowding the scan window) can "
                    f"still leave every legal id taken. Remedy: delete or rename those rows, or "
                    f"pass an explicit --id. A second, less likely cause is concurrent writers: "
                    f"if another writer really is minting here, retry `answer` once it settles."
                )
            raise SystemExit(
                f"refused: the auto-minted decision id {did!r} is taken — re-minted and re-checked "
                f"{AUG070_ID_ATTEMPTS} times against the live highest id and every attempt collided "
                f"(concurrent writers); NOTHING was stored. Remedy: retry `answer` once the other "
                f"writer settles, or pass an explicit --id."
            )
    elif not repairing and a.id and node_exists(ns, "decision", did):
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
    why_not_value = encode_why_not(a.why_not, option_rows, resolved_option)
    final_status = a.status or "decided"
    row = {
        "id": did,
        "project_id": pid,
        "domain": a.domain or "",
        "question_id": a.question_id or "",
        "chosen": chosen,
        "why_not": why_not_value,
        "reversal_cost": a.reversal_cost or "",
        "confidence": float(a.confidence if a.confidence is not None else -1),
        "status": final_status,
        "evidence_key": f"/auger/{pid}/{did}",
    }
    # `scope` is sent only when it is NOT the default: see insert_decision for why the default
    # travels as an absent key rather than as the word "project".
    if scope and scope != DEFAULT_SCOPE:
        row["scope"] = scope
    expected = {**row, "final_status": final_status}
    if repairing:
        if not pending_answer_matches(
            ns,
            pending,
            expected,
            list(a.option or []),
            break_targets,
            supersede_target_ids,
        ):
            raise SystemExit(
                f"refused: provisional decision {did!r} belongs to a different answer; "
                "retry with the original arguments or choose a new answer explicitly"
            )
        warning = ""
    else:
        provisional = {
            **row,
            "status": pending_memory_status(
                final_status,
                scope,
                break_targets,
                supersede_target_ids,
            ),
        }
        warning = insert_decision(ns, provisional)
    if not a.id and not repairing:
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
    existing_options = (
        {
            option["id"]: option
            for option in select(ns, "option", f"decision_id=eq.{did}&order=id.asc")
        }
        if repairing
        else {}
    )
    for option in option_rows:
        option["active"] = bool(
            resolved_option and option["id"] == resolved_option["id"]
        )
        if option["id"] not in existing_options:
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
    try:
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
            + f"Reason the alternatives were rejected: {why_not_display(why_not_value, option_rows)}. "
            + f"Reversal cost: {row['reversal_cost'] or 'not stated'}.",
        )
    except SystemExit as exc:
        raise SystemExit(
            f"answer {did} remains provisional until its memory is written; "
            f"retry the same answer to repair it: {exc}"
        ) from exc
    # A legacy namespace may have downgraded a requested bundle scope because its cached declaration
    # lacks the column. On repair, preserve the scope that was actually stored rather than re-running
    # the impact pass as if the requested scope had landed.
    stored_scope = (
        decision_scope(pending)
        if repairing
        else DEFAULT_SCOPE
        if warning
        else decision_scope(row)
    )
    # AUG-009's contract covers BOTH writes; finalize_decision owns the shape of this UPDATE (the
    # default travels as the empty value, a non-default scope still says its name out loud, and a
    # namespace declared before the column is finalized without the key). See its docstring for the
    # two substrate answers this was measured against.
    try:
        finalize_decision(ns, did, final_status, stored_scope)
    except SystemExit as exc:
        raise SystemExit(
            f"answer {did} remains provisional after memory was written; "
            f"retry the same answer to finalize it: {exc}"
        ) from exc
    row["status"] = final_status
    if stored_scope == DEFAULT_SCOPE:
        row.pop("scope", None)
    else:
        row["scope"] = stored_scope
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
    # AUG-059: a decision carrying a grid domain number is the event the seed word was waiting
    # for — "not reached: no rows yet" stopped being true when this row landed. The row's own
    # status advances here (NOT-REACHED -> REACHED, never regressing a row already past the
    # seed word), so `status`'s coverage line stops contradicting itself. The advance is
    # never fatal: an answer lands even where the grid was never seeded. AUG-059-R2: it is
    # never SILENT either — a refused advance prints one WARNING line below naming the
    # domain, the row and the substrate's error, and the drift note names it on the next
    # render.
    advance = advance_domain_status(ns, pid, row["domain"])
    impact = bundle_impact(ns, pid, {**row, "scope": stored_scope})
    active_count = 1 if resolved_option else 0
    domain_note = f"domain {row['domain']} = {domain_name}, " if domain_name else ""
    # AUG-092: "embedded" is the substrate's claim to make — a substrate whose /health carries
    # embedding.healthy=false stored this row lexically and says so instead.
    print(
        f"{did} recorded  ({domain_note}"
        f"confidence {_conf_render(row['confidence'])}, "
        f"{active_count} of {len(option_rows)} active, "
        f"{embed_claim(retrieval_tier())}, scope {decision_scope(row)}{closed})"
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
    # AUG-059: a decision carrying a grid domain number is the event the seed word was waiting
    # for — "not reached: no rows yet" stopped being true when this row landed. The row's own
    # status advanced with the answer (NOT-REACHED -> REACHED, never regressing a row already
    # past the seed word), so `status`'s coverage line stops contradicting itself. The advance
    # itself is never fatal: an answer lands even where the grid was never seeded. AUG-059-R2:
    # a REFUSED advance is not silent — one WARNING line names the domain, the row id and the
    # substrate's own error, and the drift note names the row left behind on the next render.
    if advance["reached"]:
        print(
            f"  domain     {row['domain']} -> {advance['reached']} (seed status retired)"
        )
    if advance["warning"]:
        print(f"  {advance['warning']}")
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


# ---------------------------------------------------------------- AUG-086: the boundary band
# Run 10 (docs/dogfood/2026-09-26-error-surface-integration.md): `check` on a just-closed Q-000001
# printed ALREADY ANSWERED (noul 0.55, threshold 0.55); three seconds later the SAME question
# printed NOT YET ANSWERED (noul 0.53, threshold 0.55). A user told "genuinely new, ask it" about a
# question they answered a minute ago stops trusting the gate, so the verdict inside the band is
# read off the STORE — a stored question whose text matches — and not off the score. Every input
# to that decision is a stored row, so identical evidence gives identical verdicts.
def question_text_key(text) -> str:
    """The identity of a question's TEXT: case- and surrounding-whitespace-insensitive."""
    return str(text or "").strip().casefold()


def settled_question_with_text(ns: str, text: str) -> str:
    """The id of a question in `ns` whose text is this one and which is SETTLED — "" for none.

    SETTLED_STATES, not CLOSED_STATES: `moot` and `budget_thin` are closed without ever being
    answered (a withdrawn question was never answered, so a blocker on it is still unresolved —
    see `unresolved_blockers`), and neither may make `check` call a question "already answered".

    The read is `select_or_empty` on purpose: a namespace whose `question` table was never
    declared holds no settled question — "no rows here", not an error — and the direction that
    leaves the verdict on is NOT YET ANSWERED, the same one an empty store has always given. The
    substrate's own unreachability is already fail-closed upstream (AUG-075, `recall`).
    """
    want = question_text_key(text)
    for q in select_or_empty(ns, "question", "order=id.asc"):
        if (
            q.get("status") in SETTLED_STATES
            and question_text_key(q.get("text")) == want
        ):
            return str(q.get("id") or "")
    return ""


def in_answered_band(noul) -> bool:
    """Whether a score sits too close to T_ANSWERED for the score itself to decide the verdict.

    Half-open at the top (`< T_ANSWERED + T_ANSWERED_BAND`): the upper edge belongs to the score,
    so a noul that clears the threshold by the full band is ALREADY ANSWERED without consulting
    the store, and the band can only ever move a verdict toward the record's reading.
    """
    if noul is None:
        return False
    v = float(noul)
    return T_ANSWERED - T_ANSWERED_BAND <= v < T_ANSWERED + T_ANSWERED_BAND


def check_verdict(noul, settled_match: bool) -> str:
    """`check`'s verdict for one JEV score (AUG-086).

    Outside the band the SCORE decides, exactly as it did before this change. Inside it the score
    is not a distinction the model can actually make, so the RECORD decides: `settled_match` —
    whether a stored question with this text is settled (`settled_question_with_text`) — is the
    whole input. Identical rows therefore give identical verdicts however the score drifts inside
    the band, including the exactly-at-threshold tie, which reads ALREADY ANSWERED when the record
    holds the question.

    A missing score (`None`) is not a verdict at all: it is not in the band and lands on
    NOT YET ANSWERED, the direction this verb has always taken for it.
    """
    v = 0.0 if noul is None else float(noul)
    if v >= T_ANSWERED + T_ANSWERED_BAND:
        return "ALREADY ANSWERED"
    if v < T_ANSWERED - T_ANSWERED_BAND:
        return "NOT YET ANSWERED"
    return "ALREADY ANSWERED" if settled_match else "NOT YET ANSWERED"


def check_band_note(matched_id: str) -> str:
    """The line `check` prints under a verdict the band decided — it names what decided it."""
    what = (
        f"settled question {matched_id} matches this text"
        if matched_id
        else "no settled question matches this text"
    )
    return (
        f"  inside the +/-{T_ANSWERED_BAND} band around the threshold: {what} — "
        "the record decides here, not the score"
    )


def cmd_check(a):
    """Before asking: is this already answered by what we hold?

    AUG-075, fail closed: a substrate that cannot be asked is an error with exit 1,
    never "no stored evidence matched — treat as a new question" — that sentence is
    reserved for a store that was asked and genuinely holds nothing.

    AUG-092: the scores of the rows this prints are on ONE of two scales depending on
    the substrate's embedding health (see `tier_note`), so the tier is read from /health
    BEFORE the retrieval and named with the rows, whenever there are rows to attribute.

    AUG-086: when the model's noul lands within `T_ANSWERED_BAND` of the threshold the score
    cannot decide the verdict — it flipped between two checks of identical evidence — so the
    stored `question` rows do, and the line under the verdict says so. See `check_verdict`.
    """
    ns = a.namespace
    tier = retrieval_tier()
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
    print(tier_note(tier))  # AUG-092: which ranker's scale the scores below are on
    for h in hits:
        print(f"  {h.get('score'):.3f}  {h.get('key')}")
    if err:
        print(
            f"\nJEV unavailable — {err}\n(fail-closed: UNKNOWN, not 'already answered'.)"
        )
        return 1
    v = _noul(ans["answers"], "already_answered")
    band = in_answered_band(
        v
    )  # AUG-086: inside the band the record decides, not the score
    matched = settled_question_with_text(ns, a.question) if band else ""
    verdict = check_verdict(v, bool(matched))
    print(f"\n{verdict}  (noul {v}, threshold {T_ANSWERED})")
    if band:
        print(check_band_note(matched))
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
    # AUG-076: a provisional decision is durable but not yet configuration, so its confidence is
    # withheld from every aggregate until its memory lands — the same exclusion `feedback`'s thin
    # list and `dump`'s ACTIVE CONFIGURATION line apply. The row itself stays counted above: it IS
    # stored, and the pending warning names it.
    active_dec = [d for d in dec if not is_pending_memory(d)]
    by_dom = {}
    for d in active_dec:
        by_dom.setdefault(d.get("domain") or "(none)", []).append(d)
    print(f"project {pid} — {p.get('name')}  [{p.get('status')}]")
    # AUG-100: an invalidated decision stays STORED (AUG-076 keeps every stored row in the
    # count), so the main count subtracts only the invalidated rows, and the invalidated set
    # is reported by name below — a reader must see WHICH decision died, not just how many.
    edges = graph_edges(ns, pid)
    break_idx = breaks_by_decision(edges)
    invalidated_dec_ids = set(break_idx.keys())
    invalidated_rows = [d for d in dec if d["id"] in invalidated_dec_ids]
    invalidated_count = len(invalidated_rows)
    print(
        f"decisions {len(dec) - invalidated_count} | invalidated {invalidated_count} | "
        f"options {len(opt)} | escalations {len(esc)} | unknowns {len(unk)} | domains {cov['rows']}"
    )
    if invalidated_count:
        print(f"invalidated_count={invalidated_count}")
        for d in invalidated_rows:
            for e in break_idx[d["id"]]:
                print(f"{d['id']}: {invalidation_marker(e)}")
    pending_lines = pending_memory_warning_lines(dec)
    if pending_lines:
        print("\n" + "\n".join(pending_lines))
    if active_dec:
        # AUG-062: the sentinel is a NON-measurement, so it is excluded from every aggregate —
        # min/mean/max describe the decisions somebody actually scored.
        confs = _conf_values(active_dec)
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
        # AUG-114: the parenthetical must describe the counter, and the counter is
        # `not_reached` — seeded, on-grid rows whose num NO decision or question carries. It
        # said "(no such row)", which is the ABSENT claim (`absent`, printed in the header
        # above): a run with 44 seeded rows printed "44 seeded | 0 absent" on one line and
        # "NOT-REACHED 44 (no such row)" on the next. The gloss now names what the counter
        # names — a row that IS stored ("seeded") and holds no evidence — so the summary
        # agrees with the per-domain lines below it and with the drift line beneath them.
        print(
            f"  answered {cov['answered']} (a decision or question row carries the num) | "
            f"NOT-REACHED {cov['not_reached']} "
            "(a seeded row with no decision or question row carrying the num)"
        )
    for ln in cov["lines"]:
        print(domain_line(ln))
    if not cov["lines"]:
        print("  (no domain row is stored in this namespace)")
    # AUG-112: the numbers that hold evidence with NO `domain` row — the README's gridless path.
    # Named as its own class so nothing the user has decided disappears from the report, and so a
    # reader can tell "answered, but the coverage map has no row" from "answered and mapped".
    if cov["no_row_evidence"]:
        print(
            "  EVIDENCE WITHOUT A `domain` ROW (a decision or question row carries the num, so "
            "these are counted in `answered` above — but no `domain` row was ever seeded for "
            f"them, so the coverage map cannot report them as rows): {', '.join(cov['no_row_evidence'])}"
        )
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
        if not is_pending_memory(d)
        and (
            isinstance(d.get("confidence"), (int, float))
            and 0 <= d["confidence"] < T_CONFIDENT
            or _is_unmeasured(d)
        )
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


def pending_memory_warning_lines(dec: list[dict]) -> list[str]:
    """Name decisions that are repairable but not ready to count as active configuration."""
    pending = [d for d in dec if is_pending_memory(d)]
    if not pending:
        return []
    lines = ["WARNING: PENDING MEMORY REPAIR (not part of the active configuration):"]
    lines.extend(
        f"  {d['id']}  requested status {pending_final_status(d)} — "
        "retry the original answer arguments to finalize it"
        for d in pending
    )
    return lines


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
        if d.get("status") == SUPERSEDED_STATUS or is_pending_memory(d):
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


#: AUG-077: the CURRENT render's contradiction pass. `toggle --on` a sibling of the recorded choice
#: is the legitimate mind-change path, and it leaves `chosen` (the answer's label, canonicalized at
#: write time) on file while a DIFFERENT option row is the one flagged active — the render then
#: presents both as one configuration, and the duplicate-id warning (AUG-070) cannot see it because
#: the two ids differ. The `--config` hypothesis has always named this; the stored state now does.
def chosen_active_contradiction_lines(
    dec: list[dict], opts: list[dict], live_by_dec: dict[str, set[str]]
) -> list[str]:
    """The `CONTRADICTIONS WITH THE RECORD` entries a CURRENT dump owes its reader (AUG-077).

    One entry per decision whose RECORDED choice is not the option that decision's stored flags
    select. `live_by_dec` is the selection the render treats as the configuration, so a superseded
    decision — which contributes nothing to it — is never reported: it reaches this pass with no live
    option, and nothing outside the configuration can contradict it. A decision is skipped too when
      * its selection is not exactly ONE option — 0 or 2+ is what the activation warning names, and
        there is no single active option to contrast the record against;
      * nothing is recorded as `chosen` — a record that claims nothing contradicts nothing;
      * the recorded choice names MORE than one option row (duplicate ids again) — naming one of them
        would be a guess.
    The entry names the decision, the recorded choice, the option that is actually active, and quotes
    the reason on file — the same four facts, in the same grammar, as the hypothetical entry above.
    """
    by_dec = options_by_decision(opts)
    lines = []
    for d in dec:
        if is_pending_memory(d):
            continue
        live = set(live_by_dec.get(d["id"]) or set())
        cand = by_dec.get(d["id"], [])
        if len(live) != 1 or not cand:
            continue
        recorded = (d.get("chosen") or "").strip()
        if not recorded:
            continue
        # The same token grammar `dump`/`toggle`/`answer` accept, so a row written before `chosen`
        # was canonicalized to the option label is still read as the choice it names.
        named = option_matches(cand, recorded, d["id"])
        if len(named) == 1 and named[0]["id"] in live:
            continue  # the record and the selection AGREE — a clean state owes no entry
        if len(named) > 1:
            continue  # ambiguous: which row the record means is the duplicate-id block's business
        active = next((o for o in cand if o["id"] in live), None)
        if active is None:
            continue  # a selection this pass cannot attribute: report nothing, guess nothing
        lines.append(
            f"{d['id']}: active {active['id']} ({active['label']}) contradicts the recorded "
            f"choice ({recorded}) — reason on file: "
            f"{why_not_display(d.get('why_not'), by_dec.get(d['id'], []))}"
        )
    return lines


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


def _export_value(value: object) -> str:
    """A stable, readable scalar for the namespace export (empty is explicit, never omitted)."""
    if value is None or value == "":
        text = "—"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    else:
        text = str(value)
    return text.replace("\n", "\n    ")


def render_export(ns: str, project_id: str | None = None) -> str:
    """Render every declared table in one deterministic, read-only namespace document.

    `-p/--project-id` remains the CLI's optional project context, but cannot filter this
    command: the export contract is the whole namespace, including cross-project bundle and
    graph rows. Reads go through `select`, so page-sized API responses cannot truncate the spec.
    """
    lines = [
        f"# Auger spec export — namespace {ns}",
        "",
        "Read-only deterministic rendering of every declared table.",
        "Scope: whole namespace (no rows are written).",
    ]
    if project_id:
        lines.append(f"Project context: {project_id} (not a filter).")
    # AUG-100: index breaks edges for rendering (always, export is namespace-scoped)
    edges = graph_edges(ns, "")
    break_idx = breaks_by_decision(edges)
    for table in EXPORT_TABLES:
        rows = select(ns, table, "order=id.asc")
        lines.extend(("", f"## {EXPORT_HEADINGS[table]} ({len(rows)} rows)"))
        if not rows:
            lines.append("### (none stored)")
            continue
        fields = [name for name, _kind in COLS[table]]
        for row in rows:
            # AUG-100: render breaks-edge invalidation on decisions
            if table == "decision" and row.get("id") in break_idx:
                markers = [invalidation_marker(e) for e in break_idx[row["id"]]]
                lines.extend(f"- {marker}" for marker in markers)
            lines.append(f"### {row.get('id') or '(missing id)'}")
            invalidated_option = (
                table == "option" and row.get("decision_id") in break_idx
            )
            for field in fields:
                # AUG-100: an option of an invalidated decision is not live — the stored value
                # stays untouched on disk, but the render must not report it as active.
                if invalidated_option and field == "active":
                    lines.append("- active: false  # INVALIDATED by a breaks edge")
                    continue
                lines.append(f"- {field}: {_export_value(row.get(field))}")
    return "\n".join(lines)


def cmd_export(a):
    profile = getattr(a, "profile", "default") or "default"
    if profile == "nasa":
        print(render_export_nasa(a.namespace, a.project_id))
    else:
        print(render_export(a.namespace, a.project_id))
    return 0


def render_export_nasa(ns: str, project_id: str | None = None) -> str:
    """Render the same rows in the NASA requirement grammar (docs/playbook/NASA-SE-PLAYBOOK.md).

    Additive by design: the default `export` contract is untouched, so no consumer breaks.
    A decision becomes a requirement, its options are the alternatives, `why_not` is the
    rationale, a verdict is the verification, and an `unknown` row is the TBD register entry
    that must carry an owner and a closing trigger.
    """
    lines: list[str] = [
        f"# Spec (NASA profile) — namespace {ns}",
        "",
        "Requirement grammar: one statement per decision, a stable id, a rationale, the",
        "alternatives considered, and an explicit verification method. Unresolved items carry",
        "an owner and a closing trigger.",
        "",
        "Read-only deterministic rendering. `auger export --profile nasa` writes nothing.",
    ]
    if project_id:
        lines.append(f"Project context: {project_id} (not a filter).")

    decisions = select(ns, "decision", "order=id.asc")
    options = select(ns, "option", "order=id.asc")
    unknowns = select(ns, "unknown", "order=id.asc")
    assumptions = select(ns, "assumption", "order=id.asc")
    verdicts = select(ns, "verdict", "order=id.asc") if "verdict" in COLS else []

    options_by_decision: dict[str, list[dict]] = {}
    for opt in options:
        options_by_decision.setdefault(str(opt.get("decision_id") or ""), []).append(
            opt
        )
    verdicts_by_target: dict[str, list[dict]] = {}
    for verdict in verdicts:
        for key in ("decision_id", "target_id", "subject_id"):
            if verdict.get(key):
                verdicts_by_target.setdefault(str(verdict[key]), []).append(verdict)
                break

    lines += ["", f"## Requirements ({len(decisions)} decisions)"]
    if not decisions:
        lines.append("### (none stored)")
    for decision in decisions:
        did = str(decision.get("id") or "(missing id)")
        opts = options_by_decision.get(did, [])
        chosen = str(decision.get("chosen") or "")
        label = next(
            (
                str(o.get("label"))
                for o in opts
                if str(o.get("id")) == chosen or str(o.get("label")) == chosen
            ),
            chosen,
        )
        judged = verdicts_by_target.get(did, [])
        # D = demonstration (an independent judgement observed the configuration);
        # I = inspection (the record itself is the evidence). Never claim T without a test row.
        method = "D" if judged else "I"
        lines += [
            "",
            f"### REQ-{did}",
            f"- statement: {label or '(undecided)'}",
            f"- id: REQ-{did}  [V: {method}]",
            f"- status: {decision.get('status') or 'active'}  confidence: {decision.get('confidence')}",
            f"- rationale: {decision.get('why_not') or '(none recorded)'}",
        ]
        if decision.get("reversal_cost"):
            lines.append(f"- reversal cost: {decision['reversal_cost']}")
        if opts:
            lines.append("- alternatives:")
            for opt in opts:
                mark = "active" if opt.get("active") else "inactive"
                brk = f" — breaks: {opt['breaks']}" if opt.get("breaks") else ""
                lines.append(f"  - {opt.get('id')} {opt.get('label')} [{mark}]{brk}")
        for verdict in judged:
            reason = verdict.get("reasons") or verdict.get("reason") or ""
            lines.append(
                f"- verified by: {verdict.get('id')} score={verdict.get('score')} {reason}".rstrip()
            )

    lines += ["", f"## Unresolved — TBD register ({len(unknowns)})"]
    if not unknowns:
        lines.append("### (none stored)")
    for unknown in unknowns:
        lines.append(
            f"- TBD-{unknown.get('id')} {unknown.get('text')}"
            f" (owner: {unknown.get('owner') or 'unassigned'};"
            f" close by: {unknown.get('trigger') or 'unscheduled'})"
        )

    lines += ["", f"## Assumptions ({len(assumptions)})"]
    if not assumptions:
        lines.append("### (none stored)")
    for assumption in assumptions:
        lines.append(
            f"- ASM-{assumption.get('id')} {assumption.get('text')}"
            f" (falsifier: {assumption.get('falsifier') or '(none recorded)'})"
        )
    return "\n".join(lines) + "\n"


def cmd_dump(a):
    """Render the system as it looks with a given option set.

    Two modes:
      dump                      -> the CURRENT active set (what is stored)
      dump --config D-001=O2    -> a hypothetical set (nothing is written), so you can ask
                                   "what does the system look like if we pick option 2 here
                                   and option 1 there" without disturbing the record.

    In EITHER mode a decision whose active options are not exactly one is WARNED about at the foot
    of the dump and still rendered: a record that drifted is exactly when the reader needs to see
    it (AUG-015). In CURRENT mode a decision whose recorded choice is no longer its active option is
    named in the CONTRADICTIONS WITH THE RECORD block too (AUG-077) — the block a `--config`
    hypothesis has always carried — and still rendered, because the legacy `--config A=B` flow is not
    the only way a record and its configuration come apart: `toggle --on` a sibling does it with
    nothing colliding.
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
    # AUG-100: index breaks edges so invalidations surface in the render
    break_idx = breaks_by_decision(graph_edges(ns, pid))

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
    confs, shadows, live_by_dec, pending_lines = [], [], {}, []
    for d in dec:
        cand = by_dec.get(d["id"], [])
        superseders = superseded_by.get(d["id"], [])
        if is_pending_memory(d):
            live_ids = set()
            pending_lines.append(
                f"{d['id']}: pending memory repair — requested status "
                f"{pending_final_status(d)}; not part of the active configuration"
            )
        elif d.get("status") == SUPERSEDED_STATUS or superseders:
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
                    f"({d.get('chosen')}) — reason on file: "
                    f"{why_not_display(d.get('why_not'), cand)}"
                )
        # AUG-100: surface breaks-edge invalidations on the decision header
        break_markers = []
        for edge_row in break_idx.get(d["id"], []):
            break_markers.append(invalidation_marker(edge_row))
        lines.append(
            f"## {d['id']}  ({d.get('domain') or '—'})  conf {_conf_render(d.get('confidence'))}"
            + (
                f"  [PENDING MEMORY REPAIR — requested status {pending_final_status(d)}]"
                if is_pending_memory(d)
                else (
                    f"  [SUPERSEDED by {', '.join(superseders)}]"
                    if superseders
                    else "  [SUPERSEDED]"
                    if d.get("status") == SUPERSEDED_STATUS
                    else (f"  [{'; '.join(break_markers)}]" if break_markers else "")
                )
            )
        )
        lines.append(f"   chosen   : {d.get('chosen')}")
        lines.extend(why_not_dump_lines(d.get("why_not"), cand))
        if cand:
            lines.append("   options  :")
            for o in cand:
                mark = "[x]" if o["id"] in live_ids else "[ ]"
                lines.append(
                    f"     {mark} {o['id']}  {o['label']}  costs={o.get('costs') or '—'}  breaks={o.get('breaks') or '—'}"
                )
        lines.append("")
    # AUG-077: the stored state can contradict the record without any id colliding, and a reader
    # asking what the system looks like NOW is owed that as loudly as a reader asking about a
    # hypothesis. A `--config` render judges the hypothesis — the pass above is ITS contract and is
    # left exactly as it was; the drift it was not asked about is not restated there. Appending to
    # the same list keeps ONE block, in decision order, at the foot of the dump.
    if not hypothetical:
        shadows += chosen_active_contradiction_lines(dec, opts, live_by_dec)
    lines.append("---")
    lines.append(
        f"ACTIVE CONFIGURATION: {', '.join(confs) if confs else '(nothing active)'}"
    )
    if pending_lines:
        lines.append("")
        lines.append("PENDING MEMORY REPAIR:")
        lines.extend(f"  - {line}" for line in pending_lines)
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


#: The snippet's continuation indent — the marker line under a superseded hit sits at the same
#: column, so the block still reads as one hit.
RECALL_SNIPPET_INDENT = "     "
#: How a superseded hit says so (AUG-078): with its successor when the supersedes edge names one,
#: bare when the status alone was moved — a successor that was never recorded is never invented.
SUPERSEDED_MARKER = RECALL_SNIPPET_INDENT + "[superseded by {}]"
SUPERSEDED_MARKER_NO_SUCCESSOR = RECALL_SNIPPET_INDENT + "[superseded]"


def cmd_recall(a):
    """AUG-075, fail closed: a store that cannot be asked is an error, not an empty list.

    AUG-092: the scores printed below are on ONE of two scales depending on the substrate's
    embedding health (see `tier_note`), so the tier is read from /health BEFORE the retrieval and
    named once above the scores, whenever there is a score to attribute.
    """
    tier = retrieval_tier()
    try:
        hits = recall(a.namespace, a.query, a.limit, project_id=a.project_id)
    except SubstrateError as exc:  # AUG-075: the store could not be asked
        print(f"substrate unavailable — cannot recall: {exc}")
        print(
            "fail-closed: a memory store that cannot be read is NOT the same as an "
            "empty one."
        )
        return 1
    # AUG-078: the record knows which answers died (`answer --supersedes` flips the decision's
    # status and writes the supersedes edge), so a superseded hit is marked — naming its
    # successor, which also breaks ties at equal scores live-first. Read per hit: recall returns
    # at most the requested limit, so the reads stay small.
    marks = {}
    for hit in hits:
        did = decision_named(hit.get("key", ""))
        if did and did not in marks:
            marks[did] = supersession_for(a.namespace, did)
    hits = sorted(
        hits,
        key=lambda h: (
            -(h.get("score") or 0),
            1 if marks.get(decision_named(h.get("key", "")), (False, ""))[0] else 0,
        ),
    )
    # AUG-092: the label rides with the scores, and only where there is a score to attribute — a
    # genuine empty result keeps the verb's contract exactly (rc 0, nothing printed).
    if hits:
        print(tier_note(tier))
    for h in hits:
        superseded, successor = marks.get(decision_named(h.get("key", "")), (False, ""))
        marker = (
            SUPERSEDED_MARKER.format(successor)
            if superseded and successor
            else (SUPERSEDED_MARKER_NO_SUCCESSOR if superseded else "")
        )
        print(
            f"{h.get('score'):.3f}  {h.get('key')}\n"
            f"{RECALL_SNIPPET_INDENT}{h.get('content', '')[:200]}"
            + (f"\n{marker}" if marker else "")
        )
    return 0


def cmd_serve(a):
    """Serve the embedding store over HTTP — the route an external agent can call (AUG-046).

    Read-only and long-lived: it publishes `embedding_search` plus the substrate's own embedding
    status, and never writes a row. `--port 0` binds an ephemeral port and the banner names it, which
    is how a caller behind a proxy (or a test) learns the address without guessing.
    """
    try:
        server = EmbeddingsHTTPServer(
            (a.host, a.port), EmbeddingsHTTPHandler, namespace=a.namespace
        )
    except OSError as exc:
        print(f"cannot serve on {a.host}:{a.port}: {exc}")
        return 1
    host, port = server.server_address[0], server.server_address[1]
    print(f"auger {AUGER_VERSION} embedding HTTP surface: http://{host}:{port}")
    for route in EMBEDDINGS_ROUTES:
        print(f"  GET {route}")
    print(
        f"substrate: {DB_URL} — default namespace: {a.namespace} (Ctrl-C stops; reads only)"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping (interrupt)")
    finally:
        server.server_close()
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


# ---------------------------------------------------------------- the register write paths (AUG-019)
# The `break`, `assumption` and `unknown` tables (and `option.costs` / `option.breaks`) were
# DECLARED and READ by `status`/`dump`/`export`, but no verb could write them. `record` is the
# operator's write path. DuckBrain enforces nothing, so every relationship is validated HERE,
# before any row is written: a refused run stores nothing at all.


def _required_text(value: object, flag: str, what: str) -> str:
    """A non-empty register field, refused by name — argparse's `required` sees presence, not content."""
    text = (value or "").strip()
    if not text:
        raise SystemExit(
            f"refused: {flag} must not be empty — a {what} row with no {flag.lstrip('-')} "
            "is not a record; nothing was written"
        )
    return text


def cmd_record_break(a):
    """Record what a decision BREAKS and what it changes — the `break` register's write path.

    The target decision must already be on THIS project's record: a break row names a decision,
    and one pointing at a decision that is not stored is an orphan claim (the same referential
    doctrine the edge write path states). Validated before the id is minted, so a refusal leaves
    no row behind.
    """
    ns = a.namespace
    pid = _project(ns, a.project_id)["id"]
    did = (a.decision or "").strip()
    breaks_what = _required_text(a.breaks_what, "--breaks-what", "break")
    consequence = _required_text(a.consequence, "--consequence", "break")
    if not select(ns, "decision", f"id=eq.{did}&project_id=eq.{pid}&select=id&limit=1"):
        raise SystemExit(
            f"refused: decision {did!r} does not exist in project {pid} — a break record names "
            "a decision this project already recorded; nothing was written"
        )
    row = {
        "id": next_id(ns, "break", "BR"),
        "decision_id": did,
        "breaks_what": breaks_what,
        "consequence": consequence,
        "applied": bool(a.applied),
    }
    insert(ns, "break", row)
    print(
        f"{row['id']} recorded  (break on {did}, applied {str(row['applied']).lower()})"
    )
    print(f"  breaks_what: {breaks_what}")
    print(f"  consequence: {consequence}")
    return 0


def cmd_record_assumption(a):
    """Record what the project is resting on — the `assumption` register's write path.

    A falsifier is required: an assumption that cannot name what would prove it wrong is not an
    assumption, it is a wish (DESIGN: "what we are resting on, with a falsifier").
    """
    ns = a.namespace
    pid = _project(ns, a.project_id)["id"]
    text = _required_text(a.text, "--text", "assumption")
    falsifier = _required_text(a.falsifier, "--falsifier", "assumption")
    row = {
        "id": next_id(ns, "assumption", "A"),
        "project_id": pid,
        "text": text,
        "falsifier": falsifier,
        "monitoring": (a.monitoring or "").strip(),
    }
    insert(ns, "assumption", row)
    print(f"{row['id']} recorded  (assumption on {pid})")
    print(f"  text: {text}")
    print(f"  falsifier: {falsifier}")
    if row["monitoring"]:
        print(f"  monitoring: {row['monitoring']}")
    return 0


def cmd_record_unknown(a):
    """Record what the project knows it does not know — the `unknown` register's write path."""
    ns = a.namespace
    pid = _project(ns, a.project_id)["id"]
    text = _required_text(a.text, "--text", "unknown")
    row = {
        "id": next_id(ns, "unknown", "U"),
        "project_id": pid,
        "text": text,
        "owner": (a.owner or "").strip(),
        "trigger": (a.trigger or "").strip(),
        "containment": (a.containment or "").strip(),
    }
    insert(ns, "unknown", row)
    print(f"{row['id']} recorded  (unknown on {pid})")
    print(f"  text: {text}")
    for field in ("owner", "trigger", "containment"):
        if row[field]:
            print(f"  {field}: {row[field]}")
    return 0


def cmd_record_option(a):
    """Record an option's `costs` / `breaks` text — the declared columns' only write path.

    The option rows themselves come from `answer`; this subcommand PATCHES the two columns
    `answer` leaves empty. The token grammar is `toggle`'s (full id, label, or bare index), an
    unresolvable or ambiguous token is refused before any PATCH, and a call whose values are
    already stored is refused as the no-op it is — a successful-looking record must never be a
    silent zero-row write.
    """
    ns = a.namespace
    pid = _project(ns, a.project_id)["id"]
    opts = select(ns, "option", "order=id.asc")
    opt = resolve_option(opts, a.option)
    # The option must belong to THIS project's record: option rows carry no project_id, so the
    # check goes through the decision they hang off — recording costs on a sibling project's
    # option would write a claim this project's record cannot vouch for.
    if not select(
        ns,
        "decision",
        f"id=eq.{opt['decision_id']}&project_id=eq.{pid}&select=id&limit=1",
    ):
        raise SystemExit(
            f"refused: option {opt['id']!r} belongs to decision {opt['decision_id']!r}, which "
            f"is not on project {pid}'s record; nothing was written"
        )
    updates = {}
    if a.costs is not None:
        updates["costs"] = a.costs
    if a.breaks is not None:
        updates["breaks"] = a.breaks
    if not updates:
        raise SystemExit(
            "refused: record option needs --costs and/or --breaks (got none) — "
            "nothing was written"
        )
    unchanged = [k for k, v in updates.items() if (opt.get(k) or "") == v]
    if len(unchanged) == len(updates):
        raise SystemExit(
            f"refused: {opt['id']} already holds exactly those {', '.join(sorted(updates))} "
            "value(s) — a record that changes nothing is refused, not reported as written"
        )
    n = patch(ns, "option", opt["id"], updates).get("updated", 0)
    if n != 1:
        raise SystemExit(
            f"record option {opt['id']}: patched {n} rows (expected 1) — nothing changed; "
            "the option id named no row"
        )
    changed = ", ".join(f"{k} -> {updates[k]!r}" for k in sorted(updates))
    print(f"{opt['id']} recorded  ({changed})")
    return 0


"""Read-only HTTP adapter for auger's frozen AUG-066 contract."""


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _namespace(ns: str) -> None:
    try:
        if not select(ns, "project", "select=id&limit=1"):
            raise APIError(404, f"unknown namespace {ns!r}")
    except APIError:
        raise
    except SystemExit as exc:
        message = str(exc)
        if "does not exist" in message or "unknown namespace" in message:
            raise APIError(404, message) from exc
        raise APIError(503, message or "substrate unavailable") from exc
    except Exception as exc:
        raise APIError(502, f"substrate read failed: {exc}") from exc


def namespace_status(ns: str, project_id: str | None) -> dict:
    try:
        p = _project(ns, project_id)
    except SubstrateError as exc:
        # AUG-101: "the store could not be asked" is NOT "this namespace has no such project".
        # A SubstrateError is a SystemExit subclass, so without this branch the translation below
        # answered 404 — `run: auger init first` — for a substrate that is simply DOWN, telling a
        # caller to re-init a namespace that is fine. 502 is the code this route's own OpenAPI
        # entry already documents for a substrate failure ("The DuckBrain substrate refused a
        # read"), so the mapping moves to the code the contract promised instead of inventing one.
        raise APIError(502, str(exc)) from exc
    except SystemExit as exc:
        message = str(exc)
        raise APIError(409 if "multiple projects" in message else 404, message) from exc
    pid = p["id"]
    decisions = select(ns, "decision", f"project_id=eq.{pid}")
    options = select(ns, "option", "")
    escalations = select(ns, "escalation", f"project_id=eq.{pid}")
    unknowns = select(ns, "unknown", f"project_id=eq.{pid}")
    coverage = domain_coverage(ns, pid)
    active = [d for d in decisions if not is_pending_memory(d)]
    confidence = _conf_values(active)
    thin = [
        {"id": d["id"], "confidence": _conf(d), "chosen": d.get("chosen", "")}
        for d in decisions
        if not is_pending_memory(d)
        and (
            _is_unmeasured(d)
            or (
                isinstance(d.get("confidence"), (int, float))
                and d["confidence"] < T_CONFIDENT
            )
        )
    ]
    return {
        "namespace": ns,
        "project_id": pid,
        "name": p.get("name"),
        "status": p.get("status"),
        "counts": {
            "decisions": len(decisions),
            "options": len(options),
            "escalations": len(escalations),
            "unknowns": len(unknowns),
            "domains": coverage["rows"],
        },
        "confidence": {
            "min": min(confidence),
            "mean": sum(confidence) / len(confidence),
            "max": max(confidence),
        }
        if confidence
        else {"min": 0, "mean": 0, "max": 0},
        "pending_memory_warnings": [
            str(x) for x in pending_memory_warning_lines(decisions)
        ],
        "thin_decisions": thin,
    }


class APIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"auger-api/{AUGER_VERSION}"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", file=sys.stderr, flush=True)

    def _send(self, status: int, body, content_type="application/json"):
        raw = (
            body.encode()
            if isinstance(body, str)
            else json.dumps(body, ensure_ascii=False).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            self._route()
        except APIError as exc:
            self._send(exc.status, {"error": str(exc)})
        except SystemExit as exc:
            self._send(503, {"error": str(exc) or "substrate unavailable"})
        except Exception as exc:
            self._send(
                502, {"error": f"substrate read failed: {type(exc).__name__}: {exc}"}
            )

    def do_POST(self):
        self._route_post()

    def do_PUT(self):
        self._send(405, {"error": "method not allowed"})

    do_PATCH = do_DELETE = do_PUT

    def _route(self):
        parts = urlsplit(self.path)
        args = parse_qs(parts.query, keep_blank_values=True)
        path = parts.path
        if path == "/health":
            try:
                stat = embedding_status()
            except Exception:
                stat = {
                    "tier": "unknown",
                    "healthy": None,
                    "provider": None,
                    "model": None,
                }
            return self._send(
                200,
                {
                    "tier": stat["tier"],
                    "embedding_healthy": stat["healthy"],
                    "provider": stat["provider"],
                    "model": stat["model"],
                },
            )
        bits = path.split("/")
        if len(bits) not in (5, 6) or bits[1:3] != ["api", "ns"]:
            return self._send(404, {"error": f"no such route: {path}"})
        ns, op = unquote(bits[3]), bits[4]
        _namespace(ns)
        pid = (args.get("project_id") or [None])[0]
        if op == "status" and len(bits) == 5:
            return self._send(200, namespace_status(ns, pid))
        if op == "dump" and len(bits) == 5:
            try:
                text = render_dump(ns, pid, args.get("config", []))
            except SystemExit as exc:
                raise APIError(
                    400 if "--config" in str(exc) else 404, str(exc)
                ) from exc
            return self._send(200, text, "text/plain; charset=utf-8")
        if op == "export" and len(bits) == 5:
            return self._send(200, render_export(ns), "text/plain; charset=utf-8")
        if op == "recall" and len(bits) == 5:
            q = (args.get("q") or [""])[0].strip()
            limit = self._limit(args)
            if not q:
                raise APIError(400, "q is required")
            tier = embedding_status()["tier"]
            hits = recall(ns, q, limit, project_id=(args.get("project") or [None])[0])
            rows = [
                {
                    "key": h.get("key", ""),
                    "score": h.get("score"),
                    "content": h.get("content", ""),
                    "timestamp": h.get("timestamp"),
                }
                for h in hits
                if isinstance(h, dict)
            ]
            return self._send(
                200,
                {
                    "namespace": ns,
                    "query": q,
                    "limit": limit,
                    "project": (args.get("project") or [None])[0],
                    "tier": tier,
                    "count": len(rows),
                    "hits": rows,
                },
            )
        if op == "embeddings" and len(bits) == 6 and bits[5] == "search":
            q = (args.get("q") or [""])[0].strip()
            if not q:
                raise APIError(400, "q is required")
            return self._send(
                200,
                embedding_search(
                    ns,
                    q,
                    self._limit(args),
                    project_id=(args.get("project") or [None])[0],
                ),
            )
        if op == "verdicts" and len(bits) == 5:
            return self._send(
                200, select(ns, "verdict", "order=created_at.desc,id.desc")
            )
        return self._send(404, {"error": f"no such route: {path}"})

    @staticmethod
    def _limit(args):
        try:
            limit = int((args.get("limit") or [str(EMBEDDINGS_DEFAULT_LIMIT)])[0])
        except ValueError as exc:
            raise APIError(400, "limit must be an integer") from exc
        if not 1 <= limit <= EMBEDDINGS_MAX_LIMIT:
            raise APIError(400, "limit outside allowed range")
        return limit

    def _route_post(self):
        parts = urlsplit(self.path)
        bits = parts.path.split("/")
        if len(bits) != 5 or bits[1:3] != ["api", "ns"] or bits[4] != "check":
            return self._send(404, {"error": f"no such route: {parts.path}"})
        ns = unquote(bits[3])
        try:
            _namespace(ns)
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                raise APIError(400, "request body too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            question = str(data.get("question", "")).strip()
            if not question:
                raise APIError(400, "question is required")
            limit = max(1, min(1000, int(data.get("limit", 5))))
            tier = embedding_status()["tier"]
            hits = recall(ns, question, limit)
            records = [
                {
                    "key": h.get("key", ""),
                    "score": h.get("score"),
                    "content": h.get("content", ""),
                    "timestamp": h.get("timestamp"),
                }
                for h in hits
                if isinstance(h, dict)
            ]
            state = (
                f"QUESTION UNDER CONSIDERATION: {question}\\nSTORED EVIDENCE: "
                + "\\n".join(
                    f"- {h.get('key')}: {h.get('content', '')[:400]}" for h in records
                )
            )
            answers, err = jev(
                state,
                {
                    "already_answered": {
                        "type": "noul",
                        "instructions": "Is the supplied evidence already answering the question? Higher noul means yes.",
                    }
                },
            )
            val = (
                _noul((answers or {}).get("answers", {}), "already_answered")
                if not err
                else None
            )
            band = val is not None and in_answered_band(val)
            matched = settled_question_with_text(ns, question) if band else ""
            verdict = (
                check_verdict(val, bool(matched)) if val is not None else "UNKNOWN"
            )
            decided_by = (
                "record"
                if band and matched
                else "fail-closed"
                if val is None
                else "score"
            )
            cost = ((answers or {}).get("usage") or {}).get("cost")
            return self._send(
                200,
                {
                    "namespace": ns,
                    "question": question,
                    "tier": tier,
                    "hits": records,
                    "verdict": verdict,
                    "verdict_decided_by": decided_by,
                    "jev_used": bool(not err and answers is not None),
                    "jev_cost": str(cost) if cost is not None else None,
                },
            )
        except APIError as exc:
            self._send(exc.status, {"error": str(exc)})
        except SystemExit as exc:
            self._send(503, {"error": str(exc) or "substrate unavailable"})
        except Exception as exc:
            self._send(
                503, {"error": f"substrate unavailable: {type(exc).__name__}: {exc}"}
            )


class APIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host="127.0.0.1", port=AUGER_API_DEFAULT_PORT):
    try:
        server = APIServer((host, port), APIHandler)
    except OSError as exc:
        raise SystemExit(f"auger API cannot bind {host}:{port}: {exc}") from exc
    print(
        f"auger read-first API: http://{server.server_address[0]}:{server.server_address[1]}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_api(a):
    return serve(a.host, a.port)


# ---------------------------------------------------------------- XML spec graph (AUG-118)
# XML specifications are authored as versioned documents; specs/schema/auger-spec-1.0.xsd is the
# normative grammar and this code is its executable half. Every block carries a stable
# repository-unique `id` plus typed metadata (`kind`, `status`, `version`, `applies-to`, verification
# method) and typed reference edges. `assemble` selects by id/attributes and emits the matched
# blocks PLUS their explicit dependency closure, in corpus order, citing the declaring file for
# every block. Design record and rationale: docs/SPEC-003-xml-specs.md.
#
# Additive by construction: nothing here is reachable from `export`/`render_export`, so the Markdown
# export contract is byte-identical to before — tests/test_xml_specs.py pins that with a golden.
# ch:trace row=AUG-118 spec=docs/SPEC-003-xml-specs.md#assembly-and-selection
# ch:trace row=AUG-123 spec=docs/prd/xml-spec-graph.md#R15 test=tests/test_verification_graph.py doc=docs/XML-SPEC-SCHEMA.md

SPEC_SCHEMA_VERSION = (
    "1.0"  # the version this code emits; SPEC_SCHEMA_VERSIONS is what it accepts
)
SPEC_SCHEMA_VERSIONS = ("1.0",)
SPEC_XML_NAMESPACE = "urn:auger:spec:1"
# `test` is a block kind, not an obligation the schema merely tolerates: the obligation validator
# has always enforced the <test> surface (SPEC_BLOCK_TAGS), so before AUG-133 an authored corpus
# could not write the shape the validator reads. One element set, both halves (AUG-133).
# `task` (AUG-121) is a block too: a task IS a small spec for one exact request, so it carries
# stable identity, lifecycle, method and typed scope edges like every other authored block. Making
# it a second storage shape (a fenced YAML block in a board row, say) is exactly the drift the XML
# graph exists to remove.
SPEC_BLOCK_KINDS = (
    "section",
    "requirement",
    "acceptance",
    "verification",
    "test",
    "task",
)
SPEC_STATUSES = ("active", "deprecated", "superseded")
SPEC_VERIFICATION_METHODS = ("test", "demo", "analysis", "inspection")
# Typed ID edges. EVERY one must resolve; only the closure attributes are followed by assembly,
# so `refs`/`implements`/`supersedes` stay navigable and never silently expand a bundle.
#
# A TASK'S SCOPE IS A SET OF TYPED EDGES (AUG-121), not a prose list: `in-scope` is the contract the
# task implements, `out-of-scope` is the explicit set of blocks it does NOT touch (each carrying a
# reason through `<exclusion>`), `acceptance` names the criteria that decide it, and `acknowledges`
# is the deliberate act that lets a NON-ACTIVE block into scope. They are separate attributes
# because their meanings differ and a caller has to be able to tell them apart; sharing `refs`
# would erase the distinction the packet exists to carry.
SPEC_TASK_EDGE_ATTRS = ("in-scope", "out-of-scope", "acceptance", "acknowledges")
SPEC_EDGE_ATTRS = (
    "depends-on",
    "verifies",
    "refs",
    "implements",
    "supersedes",
    *SPEC_TASK_EDGE_ATTRS,
)
SPEC_FORWARD_CLOSURE_ATTRS = ("depends-on",)
# A block that VERIFIES a selected block belongs to that block's explicit dependency closure: a
# requirement without its acceptance criteria and verification plan is not the selected contract.
SPEC_INVERSE_CLOSURE_ATTRS = ("verifies",)
# The block kinds that can BE an obligation of a requirement (AUG-144): a `<verification>` plan,
# a `<test>` that points at a runner/selector/path, or an `<acceptance>` criterion. All three name
# their requirement through `verifies=`, which is the inverse edge `bundle` used to ignore.
SPEC_OBLIGATION_KINDS = ("acceptance", "verification", "test")
# Blocks whose verification METHOD is mandatory metadata (`verification=`); a <verification> block
# carries the same closed set under `method=`. A task states the method that proves it (AUG-121):
# "how would we know this task is done" is part of the contract, not a footnote on the board row.
SPEC_REQUIRES_METHOD = ("requirement", "acceptance", "task")
# These patterns are the AUTHORING patterns. They are deliberately dialect-neutral — plain
# capturing groups, no `(?:`, no lookahead, no Python-only escapes — because the same strings ship
# in specs/schema/auger-spec-1.0.xsd, and an XSD 1.0 processor rejects a Python-dialect facet
# outright ("not a valid regular expression"). Before AUG-125 the `(?:` form made the published
# schema unloadable by every XSD 1.0 processor while this code kept accepting the same language.
# The facets carry the SAME strings minus the `^`/`$` anchors: an XSD pattern is implicitly
# anchored over the whole lexical value, and `^`/`$` are ORDINARY CHARACTERS in XSD 1.0, so
# keeping them there would require a literal caret. tests/test_xsd_conformance.py pins the
# language of both engines against the same samples.
SPEC_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$")
SPEC_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SPEC_APPLIES_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")
# AUG-121: an approved plan digest is the sha256 of the canonical task packet, lower-case hex. The
# approval is bound to the DIGEST, never to the task id: a plan that changed after review must not
# export just because its name did not.
SPEC_TASK_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
# The fields a generated task marker carries. `row` and `spec` must resolve to the canonical task
# and its exact XML ids; `evidence`/`verdict`/`commit` are absent at export time and therefore
# carry `none:<reason>` — a bare `none` is refused (AUG-121 AC5, PRD R10). `witness` (AUG-119 AC6)
# is the independent observer of the claim: the doctrine's own closeout markers carry it
# (`witness=none:self-verified`), and a field a reader cannot see is a field no marker may name —
# so it is part of the PARSER's vocabulary, not only of the renderer's.
SPEC_TASK_MARKER_FIELDS = (
    "row",
    "spec",
    "test",
    "evidence",
    "verdict",
    "commit",
    "witness",
)
# A marker written before any board row exists, and the marker bound INTO the row the exporter then
# reads back. Two values, because "nothing has witnessed this yet" and "the canonical row was read
# back" are different claims and a reader must be able to tell them apart.
SPEC_TASK_DRAFT_WITNESS = "none:not-exported"
SPEC_TASK_EXPORT_WITNESS = "boardctl:show-readback"
# A plan's approval ledger (AUG-119). Append-only: the LATEST record for a plan is the decision, so
# a rejection is a new fact rather than an edit of history, and a plan whose approval predates its
# current digest is stale instead of silently still-approved.
SPEC_PLAN_LEDGER_NAME = "plan-approvals.jsonl"
SPEC_PLAN_DECISIONS = ("approved", "rejected")
# The lifecycle states a GitReins packet accepts on a spec/test reference (AUG-124). Mirrors the
# consumer's `_ACTIVE_STATES`: a packet Auger calls valid must be one the adapter will not refuse.
SPEC_PACKET_ACTIVE_STATES = ("active", "current", "approved")
SPEC_PACKET_VERSION = "1.0"
# AUG-122: the closed set of outcomes a context query can answer with. Anything other than `ok`
# carries a `reason` string, because "no result" and "the tool had nothing to say" must never look
# the same to a caller.
SPEC_CONTEXT_STATUSES = ("ok", "inactive", "unscoped", "ambiguous")
# The only external command the task exporter runs. It is a NAME so a caller (and a test) can point
# it at a wrapper without the exporter growing a second transport.
BOARDCTL_BINARY = "boardctl"
SPEC_ID_MAX_LEN = 80
SPEC_FORBIDDEN_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# AUG-146: the ONE document-level failure a selection-scoped verb tolerates. A half-written file
# is a fact about THAT file, never evidence about any other document, and refusing
# `spec list --id X` because a neighbouring work-in-progress file is not well-formed makes
# incremental authoring impossible (PRD AC-14: a selection reads no unselected document BODY).
# Every other read-time refusal — wrong namespace, wrong root element, unsupported schema version,
# a DOCTYPE/ENTITY declaration — stays loud on EVERY verb: those are policy violations, not works
# in progress, and a corpus that states a rule auger refuses is not a corpus to select from.
SPEC_TOLERATED_UNREADABLE = ("invalid-xml",)
# The normative grammar, and the processor that loads it. `specs/schema/auger-spec-1.0.xsd` is the
# published schema. TWO layouts can hold it (AUG-129):
#
#   * a SOURCE CHECKOUT — `specs/schema/...` beside auger.py, which is where contributors and the
#     gate always ran it from;
#   * an INSTALLED auger — the wheel has no package directory (`py-modules = ["auger"]`), so the
#     identical file travels as a `data_files` entry and setuptools installs it under the
#     interpreter's `data` scheme: `<prefix>/share/auger/specs/schema/auger-spec-1.0.xsd`
#     (inside the wheel: `auger-<version>.data/data/share/auger/specs/schema/`).
#
# `spec_schema_candidates()` lists both, in that order, and `_spec_load_schema` refuses with
# `xsd-schema-missing` when NONE of them holds a schema: shipping the file must never turn a
# missing grammar into a verdict.
SPEC_SCHEMA_NAME = "auger-spec-1.0.xsd"
SPEC_SCHEMA_RELPATH = Path("specs") / "schema" / SPEC_SCHEMA_NAME
# Installed data lives off the `data` scheme, not off the module directory (AUG-129).
SPEC_SCHEMA_INSTALL_RELPATH = Path("share") / "auger" / SPEC_SCHEMA_RELPATH
SPEC_SCHEMA_PATH = Path(__file__).resolve().parent / SPEC_SCHEMA_RELPATH
# The example corpus the wheel ships beside the grammar, so a fresh install has something real to
# validate without a git checkout (AUG-129 AC2).
SPEC_EXAMPLES_INSTALL_RELPATH = Path("share") / "auger" / "specs" / "examples"
# The XSD processor is a `dev`/`auger[xsd]` extra (owner decision D-1): auger stays stdlib-only at
# RUNTIME (`dependencies = []`), so lxml is imported lazily and its absence is a typed refusal.
# The seam is a module NAME so tests can point it at a module that does not exist.
SPEC_XSD_PROCESSOR_MODULE = "lxml.etree"


class SpecError(ValueError):
    """A typed XML specification refusal: `code` names the rule, `detail` names the evidence."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _spec_local(tag: str) -> str:
    """The element's local name, namespace stripped (stdlib ElementTree keeps `{ns}tag`)."""
    return tag.rsplit("}", 1)[-1]


def _spec_namespace(tag: str) -> str:
    """The element's namespace, or '' for an unnamespaced document."""
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _spec_text(element) -> str:
    """Normalized body text: full prose for a leaf, the block's own intro for a section."""
    nested = [child for child in element if _spec_local(child.tag) in SPEC_BLOCK_KINDS]
    if nested:
        return " ".join((element.text or "").split())
    return " ".join("".join(element.itertext()).split())


def _spec_read_document(path: Path, root: Path):
    """Parse one XML document, refusing entity declarations before any element is built."""
    rel = path.relative_to(root).as_posix()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SpecError("unreadable-document", f"{rel}: {exc}") from None
    if SPEC_FORBIDDEN_DECL_RE.search(raw.decode("utf-8", "replace")):
        raise SpecError(
            "external-entity-forbidden",
            f"{rel}: DOCTYPE/ENTITY declarations are refused "
            "(no entity expansion, no network fetch)",
        )
    try:
        element = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SpecError("invalid-xml", f"{rel}: {exc}") from None
    namespace = _spec_namespace(element.tag)
    if namespace and namespace != SPEC_XML_NAMESPACE:
        raise SpecError(
            "unsupported-namespace",
            f"{rel}: {namespace} (supported: {SPEC_XML_NAMESPACE})",
        )
    local = _spec_local(element.tag)
    if local != "spec":
        raise SpecError("invalid-root", f"{rel}: <{local}> (expected <spec>)")
    declared = element.get("schema-version")
    if declared not in SPEC_SCHEMA_VERSIONS:
        found = "absent" if declared is None else declared
        raise SpecError(
            "unsupported-schema-version",
            f"{rel}: schema-version {found} "
            f"(supported: {', '.join(SPEC_SCHEMA_VERSIONS)})",
        )
    if (
        not (element.get("id") or "").strip()
        or not (element.get("version") or "").strip()
    ):
        raise SpecError(
            "missing-document-metadata", f"{rel}: <spec> requires id and version"
        )
    return element


def _spec_discover_documents(root: Path) -> tuple:
    """Every readable `*.xml` file under the corpus root, plus the ones REFUSED (AUG-148).

    One discovery rule for both loaders (`load_spec_corpus` and `_xml_spec_documents`): before
    AUG-148 each of them rglob'd independently, and the out-of-root rule was enforced on the
    `<include src>` mechanism only — so `<include src="/etc/hostname"/>` was refused
    (`remote-reference-rejected`) while `linked.xml -> /etc/hostname` sitting in the corpus was
    read and parsed. The rule now lives at the ONE point where a document enters the corpus.

    A path whose RESOLVED target is outside the root is refused, whether it is a symlinked file or
    a file reached through a symlinked directory (both `resolve()` out). A symlink that resolves
    INSIDE the root is an ordinary document. The refusal carries paths only — never a byte of the
    target's content — and its code is `symlink-outside-root`; a dangling `*.xml` symlink is
    `unreadable-document` rather than a silent disappearance.

    Returns (paths, refusals) with refusals shaped like the loader's `skipped` entries: the strict
    callers turn the first one into a typed refusal, the selection-scoped ones count it.
    """
    paths: list = []
    refusals: list = []
    for path in sorted(root.rglob("*.xml")):
        rel = path.relative_to(root).as_posix()
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            refusals.append(
                {
                    "file": rel,
                    "code": "unreadable-document",
                    "detail": f"{rel}: the symlink target does not exist",
                }
            )
            continue
        if not resolved.is_file():
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            refusals.append(
                {
                    "file": rel,
                    "code": "symlink-outside-root",
                    "detail": (
                        f"{rel}: the symlink resolves to {resolved}, outside the corpus "
                        f"root {root} (only local documents inside the root are read)"
                    ),
                }
            )
            continue
        paths.append(path)
    return paths, refusals


def _spec_read_documents(
    paths, root: Path, *, tolerate_unreadable: bool, skipped: list
) -> dict:
    """Parse every discovered document, optionally SKIPPING the not-well-formed ones (AUG-146).

    The tolerance is deliberately one code wide (`SPEC_TOLERATED_UNREADABLE`) and per DOCUMENT,
    never per corpus: a skipped file is recorded in `skipped` (file, code, detail) so the caller
    can both print the count and put it in the JSON counter, and the same corpus handed to a
    corpus-wide verb still refuses, naming that file. Skipping is not forgiving-by-default: the
    strict path (`tolerate_unreadable=False`) re-raises the very same typed error it always did.
    """
    documents: dict = {}
    for path in paths:
        try:
            documents[path.relative_to(root).as_posix()] = _spec_read_document(
                path, root
            )
        except SpecError as exc:
            if not (tolerate_unreadable and exc.code in SPEC_TOLERATED_UNREADABLE):
                raise
            skipped.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "code": exc.code,
                    "detail": exc.detail,
                }
            )
    return documents


def _spec_load_processor():
    """The XSD processor module, or a typed refusal (AUG-133 / owner decision D-1).

    auger is deliberately stdlib-only at RUNTIME (`pyproject.toml`: `dependencies = []`), so the
    processor lives in the `dev` and `auger[xsd]` extras and is imported HERE, lazily. The trade is
    only honest while its absence is loud: no processor means no verdict, never a quiet pass.
    """
    try:
        return importlib.import_module(SPEC_XSD_PROCESSOR_MODULE)
    except ImportError:
        raise SpecError(
            "xsd-processor-missing",
            f"{SPEC_XSD_PROCESSOR_MODULE} is not installed, so the published grammar cannot be "
            "loaded and no verdict is possible (install the `auger[xsd]` extra, or the `dev` "
            "extra: `pip install -e '.[dev]'`)",
        ) from None


def _spec_data_roots() -> tuple:
    """The `data`-scheme install roots this interpreter can be served from (AUG-129).

    A wheel's `data_files` land under the install scheme, so the root depends on HOW auger was
    installed, not on where `auger.py` sits: a venv's `data` root is the venv prefix, a system
    install's is the prefix, and a `pip install --user` puts the same files under the user scheme.
    Both are candidates; a platform whose sysconfig lacks one simply contributes nothing (the
    module-adjacent checkout path in `SPEC_SCHEMA_PATH` is always tried first).
    """
    roots: list = []
    schemes = [None]
    try:
        schemes.append(sysconfig.get_preferred_scheme("user"))
    except (AttributeError, KeyError):  # pragma: no cover - older/odd sysconfig
        pass
    for scheme in schemes:
        try:
            root = (
                sysconfig.get_path("data", scheme)
                if scheme
                else sysconfig.get_path("data")
            )
        except (KeyError, TypeError):  # pragma: no cover - scheme not on this platform
            continue
        if root:
            roots.append(Path(root))
    return tuple(roots)


def spec_schema_candidates() -> tuple:
    """Every documented location the published grammar can live in, in resolution order.

    Read `SPEC_SCHEMA_PATH` at CALL time (AUG-129) so a caller — or a test — that points that
    module attribute at a fixture wins the way it always did: tests/test_xsd_conformance.py
    relies on it to grade a mutated grammar.
    """
    candidates = [Path(SPEC_SCHEMA_PATH)]
    candidates.extend(root / SPEC_SCHEMA_INSTALL_RELPATH for root in _spec_data_roots())
    ordered: list = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return tuple(ordered)


def spec_schema_path(path=None) -> Path:
    """The grammar the next verdict loads, or a typed `xsd-schema-missing` refusal.

    An explicit `path` is honoured exactly (a caller that names a file gets that file, or the
    refusal), and asked-for-nowhere falls back to the candidate list. Naming EVERY candidate in
    the refusal is the point: "no schema" has to be actionable on a host where the installed data
    went somewhere the user can check.
    """
    candidates = (Path(path),) if path is not None else spec_schema_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SpecError(
        "xsd-schema-missing",
        f"no published grammar at any of: {', '.join(str(c) for c in candidates)} "
        "(a source checkout carries it at specs/schema/, an installed auger at "
        "share/auger/specs/schema/ — reinstall the wheel, or install the `auger[xsd]` extra and "
        "run from a checkout)",
    )


def _spec_load_schema(path=None):
    """Load the shipped XSD with a real XSD 1.0 processor, or refuse with a typed error.

    Three failures are distinguished, because they need different fixes: no processor
    (`xsd-processor-missing`), no schema file anywhere (`xsd-schema-missing`), and a schema the
    processor rejects (`xsd-schema-unloadable`, e.g. a Python-dialect pattern).
    """
    etree = _spec_load_processor()
    target = spec_schema_path(path)
    try:
        return etree.XMLSchema(etree.parse(str(target)))
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError) as exc:
        # libxml2's log entries already carry the file and line; only fall back to prefixing the
        # path when the processor gave no log (a bare XMLSyntaxError, for instance).
        detail = "; ".join(str(entry) for entry in getattr(exc, "error_log", [])[:3])
        raise SpecError(
            "xsd-schema-unloadable",
            detail or f"{target}: {exc}",
        ) from None


def _spec_schema_violations(schema, raw: bytes, rel: str) -> list[str]:
    """The schema's complaints about one document, as actionable one-line messages.

    The parser is configured to match the loader's security posture — no entity expansion, no DTD
    loading, no network — so a schema check can never become the path that resolves an entity the
    loader refused. `schema.error_log` is per validation run, so it is snapshotted here.
    """
    etree = _spec_load_processor()
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        document = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as exc:
        return [f"{rel}: the XSD processor cannot parse this document: {exc}"]
    try:
        valid = schema.validate(document)
    except etree.XMLSchemaValidateError as exc:
        # An entity reference the parser refused to resolve (never resolved: no entity expansion,
        # no DTD load) leaves a tree the processor cannot validate. That is a refusal, not a pass.
        return [f"{rel}: the XSD processor cannot validate this document: {exc}"]
    if valid:
        return []
    return [
        f"{rel}:{entry.line}: {entry.message}" for entry in list(schema.error_log)[:5]
    ] or [f"{rel}: does not conform to {SPEC_SCHEMA_NAME}"]


def _spec_assert_schema_conformance(schema, paths, root: Path) -> None:
    """Refuse any document the published grammar rejects, naming the file and the violation.

    This runs AFTER the executable rules, deliberately: the code's checks are the more specific
    diagnostic (a `kind-mismatch` names the attribute pair; the schema would only say "invalid"),
    and the schema is the normative BACKSTOP for everything the code would otherwise let through —
    an undeclared attribute, an unknown element, a value outside a facet. Both are refusals; the
    ordering only decides which evidence the author sees first.
    """
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SpecError("unreadable-document", f"{rel}: {exc}") from None
        problems = _spec_schema_violations(schema, raw, rel)
        if problems:
            extra = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            raise SpecError(
                "schema-invalid",
                f"{problems[0]}{extra} ({SPEC_SCHEMA_NAME})",
            )


def _spec_include_target(element, rel: str, root: Path) -> str:
    """Resolve an <include src> to a repository-relative path inside the spec root."""
    src = (element.get("src") or "").strip()
    if not src:
        raise SpecError("invalid-include", f"{rel}: <include> requires src")
    if "://" in src or src.startswith("/") or ".." in Path(src).parts:
        raise SpecError(
            "remote-reference-rejected",
            f"{rel}: include src {src!r} "
            "(only repository-relative local files are allowed)",
        )
    target = (root / src).resolve()
    try:
        target_rel = target.relative_to(root).as_posix()
    except ValueError:
        raise SpecError(
            "include-outside-root", f"{rel}: include src {src!r} escapes {root}"
        ) from None
    if not target.is_file():
        raise SpecError(
            "unresolved-include", f"{rel}: include src {src!r} is not a file"
        )
    return target_rel


def _spec_include_map(documents: dict, root: Path) -> dict:
    """Every document's include targets, with include cycles refused before any traversal."""
    include_map = {}
    for rel in sorted(documents):
        targets = []
        for element in documents[rel].iter():
            if _spec_local(element.tag) == "include":
                targets.append(_spec_include_target(element, rel, root))
        include_map[rel] = tuple(targets)
    state: dict[str, str] = {}

    def visit(rel: str, path: list) -> None:
        mark = state.get(rel)
        if mark == "done":
            return
        if mark == "open":
            start = path.index(rel)
            raise SpecError("include-cycle", " -> ".join(path[start:] + [rel]))
        state[rel] = "open"
        for target in include_map.get(rel, ()):
            visit(target, path + [rel])
        state[rel] = "done"

    for rel in sorted(documents):
        visit(rel, [])
    return include_map


def _spec_block(element, rel: str, order: int) -> dict:
    """Validate one block's stable identity and typed metadata, and index its edges."""
    kind = _spec_local(element.tag)
    block_id = (element.get("id") or "").strip()
    if not block_id:
        raise SpecError("missing-id", f"{rel}: <{kind}> has no id")
    if len(block_id) > SPEC_ID_MAX_LEN or not SPEC_ID_RE.match(block_id):
        raise SpecError(
            "invalid-id",
            f"{rel}: {block_id!r} (expected UPPER-CASE alphanumerics with at least one "
            f"dash, at most {SPEC_ID_MAX_LEN} chars)",
        )
    declared_kind = (element.get("kind") or "").strip()
    if not declared_kind:
        raise SpecError("missing-kind", f"{rel}: {block_id} has no kind attribute")
    if declared_kind != kind:
        raise SpecError(
            "kind-mismatch", f"{rel}: {block_id} kind={declared_kind!r} vs <{kind}>"
        )
    status = (element.get("status") or "").strip()
    if status not in SPEC_STATUSES:
        found = status or "absent"
        raise SpecError(
            "invalid-status",
            f"{rel}: {block_id} status={found!r} (one of: {', '.join(SPEC_STATUSES)})",
        )
    version = (element.get("version") or "").strip()
    if not SPEC_VERSION_RE.match(version):
        found = version or "absent"
        raise SpecError(
            "invalid-version",
            f"{rel}: {block_id} version={found!r} (expected X.Y.Z)",
        )
    applies_to = tuple((element.get("applies-to") or "").split())
    for token in applies_to:
        if not SPEC_APPLIES_RE.match(token):
            raise SpecError(
                "invalid-applies-to",
                f"{rel}: {block_id} applies-to={token!r} "
                "(expected dotted tokens like auger.export)",
            )
    method = ""
    if kind in SPEC_REQUIRES_METHOD:
        method = (element.get("verification") or "").strip()
        attribute = "verification"
    elif kind == "verification":
        method = (element.get("method") or "").strip()
        attribute = "method"
    if kind in SPEC_REQUIRES_METHOD or kind == "verification":
        if method not in SPEC_VERIFICATION_METHODS:
            found = method or "absent"
            raise SpecError(
                "invalid-verification-method",
                f"{rel}: {block_id} {attribute}={found!r} "
                f"(one of: {', '.join(SPEC_VERIFICATION_METHODS)})",
            )
    refs = {}
    for attribute in SPEC_EDGE_ATTRS:
        targets = tuple((element.get(attribute) or "").split())
        if targets:
            refs[attribute] = targets
    # AUG-123/AUG-121: the machine-checkable obligation a verification/test block names, and the
    # task-only metadata. Both are READ here (not in a second parser) so the published block record
    # is the one place a verb, a renderer and a packet learn what a block carries.
    obligation = {}
    if kind in ("verification", "test"):
        obligation = {
            "runner": (element.get("runner") or "").strip(),
            "path": (element.get("path") or "").strip(),
            "selector": (element.get("selector") or "").strip(),
        }
    plan_digest = ""
    scope_version = ""
    plan_id = ""
    plan_version = ""
    exclusions: tuple = ()
    if kind == "task":
        plan_digest = (element.get("plan-digest") or "").strip()
        if plan_digest and not SPEC_TASK_DIGEST_RE.match(plan_digest):
            raise SpecError(
                "invalid-plan-digest",
                f"{rel}: {block_id} plan-digest={plan_digest[:16]!r} (expected 64 lowercase hex "
                "characters — the sha256 of the canonical task packet)",
            )
        scope_version = (element.get("scope-version") or "").strip()
        if scope_version and not SPEC_VERSION_RE.match(scope_version):
            raise SpecError(
                "invalid-version",
                f"{rel}: {block_id} scope-version={scope_version!r} (expected X.Y.Z)",
            )
        # AUG-119: PLAN MEMBERSHIP is authored, not inferred from a directory or a filename. A plan
        # is many tasks, so the task is the one place that knows which plan it belongs to; the
        # version rides beside it so a plan that changed shape cannot be approved under the old
        # label (the plan builder refuses a mixed plan-version).
        plan_id = (element.get("plan") or "").strip()
        if plan_id and not SPEC_ID_RE.match(plan_id):
            raise SpecError(
                "invalid-plan-id",
                f"{rel}: {block_id} plan={plan_id!r} (expected an UPPER-CASE block-style id)",
            )
        plan_version = (element.get("plan-version") or "").strip()
        if plan_version and not SPEC_VERSION_RE.match(plan_version):
            raise SpecError(
                "invalid-version",
                f"{rel}: {block_id} plan-version={plan_version!r} (expected X.Y.Z)",
            )
        entries = []
        for child in element:
            if _spec_local(child.tag) != "exclusion":
                continue
            entries.append(
                {
                    "ref": (child.get("ref") or "").strip(),
                    "reason": " ".join((child.get("reason") or "").split()),
                }
            )
        exclusions = tuple(entries)
    else:
        # The grammar admits <exclusion> in any block's content (an XSD 1.0 extension of a mixed
        # type cannot add an element particle), so the CODE is what keeps the shape honest: an
        # exclusion outside a task means nothing and would otherwise be silently ignored.
        for child in element:
            if _spec_local(child.tag) == "exclusion":
                raise SpecError(
                    "unexpected-exclusion",
                    f"{rel}: {block_id} <exclusion> is only meaningful inside a <task> block "
                    "(it explains why a block is out of a TASK's scope)",
                )
    return {
        "id": block_id,
        "kind": kind,
        "status": status,
        "version": version,
        "applies_to": applies_to,
        "method": method,
        "refs": refs,
        "obligation": obligation,
        "plan_digest": plan_digest,
        "scope_version": scope_version,
        "plan": plan_id,
        "plan_version": plan_version,
        "exclusions": exclusions,
        "text": _spec_text(element),
        "source": f"{rel}#{block_id}",
        "source_file": rel,
        "order": order,
    }


def _spec_collect(
    element,
    rel: str,
    root: Path,
    documents: dict,
    stack: list,
    out: list,
    spliced: dict,
) -> None:
    """Walk a document in order, splicing included fragments at their <include> site.

    A fragment may be spliced at exactly ONE site in a corpus: reuse across documents is by
    explicit ID reference (`depends-on`/`verifies`), not by including the same block twice.
    A second splice is refused instead of silently duplicating or silently skipping blocks.
    """
    for child in element:
        local = _spec_local(child.tag)
        if local == "include":
            target = _spec_include_target(child, rel, root)
            if target not in documents:
                raise SpecError(
                    "unresolved-include", f"{rel}: {target} was not indexed"
                )
            origin = spliced.get(target)
            if origin is not None:
                raise SpecError(
                    "duplicate-include",
                    f"{rel} includes {target}, already spliced into {origin} "
                    "(reuse a block by ID reference instead of including it twice)",
                )
            spliced[target] = rel
            _spec_collect(
                documents[target],
                target,
                root,
                documents,
                stack + [target],
                out,
                spliced,
            )
            continue
        if local not in SPEC_BLOCK_KINDS:
            raise SpecError(
                "unknown-element",
                f"{rel}: <{local}> is not a spec block "
                f"(one of: {', '.join(SPEC_BLOCK_KINDS)}, include)",
            )
        out.append(_spec_block(child, rel, len(out)))


def _spec_refuse_reference_cycles(by_id: dict) -> None:
    """Refuse any cycle over the typed reference graph, naming the path."""
    edges = {
        block_id: sorted(
            target
            for attribute in SPEC_EDGE_ATTRS
            for target in block["refs"].get(attribute, ())
        )
        for block_id, block in by_id.items()
    }
    state: dict[str, str] = {}
    for start in sorted(edges):
        if state.get(start) == "done":
            continue
        state[start] = "open"
        path = [start]
        stack = [(start, iter(edges[start]))]
        while stack:
            node, pending = stack[-1]
            target = next(pending, None)
            if target is None:
                state[node] = "done"
                stack.pop()
                path.pop()
                continue
            mark = state.get(target)
            if mark == "open":
                begin = path.index(target)
                raise SpecError("reference-cycle", " -> ".join(path[begin:] + [target]))
            if mark == "done":
                continue
            state[target] = "open"
            path.append(target)
            stack.append((target, iter(edges[target])))


def _spec_validate_blocks(blocks: list) -> dict:
    """Repository-wide ID and edge constraints. Returns the id -> block index."""
    by_id: dict = {}
    for block in blocks:
        previous = by_id.get(block["id"])
        if previous is not None:
            owners = [
                candidate["id"]
                for candidate in blocks
                if candidate["kind"] == "requirement"
                and block["id"] in candidate["refs"].get("verifies", ())
            ]
            owner_detail = f"; referenced by {', '.join(owners)}" if owners else ""
            raise SpecError(
                "duplicate-id",
                f"{block['id']} declared in {previous['source_file']} "
                f"and {block['source_file']}{owner_detail}",
            )
        by_id[block["id"]] = block
    for block in blocks:
        for attribute, targets in block["refs"].items():
            for target in targets:
                found = by_id.get(target)
                if found is None:
                    raise SpecError(
                        "unresolved-reference",
                        f"{block['id']} {attribute}={target} (no block with that id)",
                    )
                if target == block["id"]:
                    raise SpecError(
                        "reference-cycle",
                        f"{block['id']} {attribute}={target} (self reference)",
                    )
                if attribute == "verifies":
                    valid_target = (
                        found["kind"] == "requirement"
                        if block["kind"] != "requirement"
                        else found["kind"] in {"verification", "test"}
                    )
                    if not valid_target:
                        raise SpecError(
                            "invalid-reference-type",
                            f"{block['id']} verifies={target} but {target} is a "
                            f"{found['kind']}",
                        )
    _spec_refuse_reference_cycles(by_id)
    _spec_validate_tasks(blocks, by_id)
    return by_id


def _spec_validate_tasks(blocks: list, by_id: dict) -> None:
    """The AUG-121 task-scope contract: a task is an EXACT scope, not a prose intention.

    Every rule here exists because the loose shape fails silently in a way the board cannot see.
    A duplicate ref is an author's copy-paste (the packet would carry the block twice). A block in
    both lists is a contradiction. An acceptance ref that is not an `<acceptance>` block means the
    criteria the task is judged on are not criteria. A non-active block in scope without an
    acknowledgment pulls RETIRED text into a fresh task. An exclusion with no reason is
    indistinguishable from an oversight — and the reason is the only thing that stops the next
    agent from re-litigating the exclusion. A cross-version ref silently mixes two contract
    versions in one "exact mini-spec". Each is its own code, naming the task AND the block, because
    a finding an author cannot act on is another round trip.
    """
    for block in blocks:
        if block["kind"] != "task":
            continue
        task_id = block["id"]
        in_scope = block["refs"].get("in-scope", ())
        out_scope = block["refs"].get("out-of-scope", ())
        accepted = block["refs"].get("acceptance", ())
        acknowledged = block["refs"].get("acknowledges", ())
        if not in_scope:
            raise SpecError(
                "task-scope-missing",
                f"{task_id}: no in-scope references — a task is a scope over existing blocks, and "
                "a task that names none cannot be dispatched against a contract",
            )
        for attribute, values in (
            ("in-scope", in_scope),
            ("out-of-scope", out_scope),
            ("acceptance", accepted),
            ("acknowledges", acknowledged),
        ):
            duplicated = sorted({value for value in values if values.count(value) > 1})
            if duplicated:
                raise SpecError(
                    "task-duplicate-reference",
                    f"{task_id} {attribute}={','.join(duplicated)} (listed more than once)",
                )
        conflict = sorted(set(in_scope) & set(out_scope))
        if conflict:
            raise SpecError(
                "task-scope-conflict",
                f"{task_id} lists {','.join(conflict)} as BOTH in-scope and out-of-scope",
            )
        for ref in accepted:
            target = by_id[ref]
            if target["kind"] != "acceptance":
                raise SpecError(
                    "task-invalid-acceptance-ref",
                    f"{task_id} acceptance={ref} but {ref} is a {target['kind']} "
                    "(the criteria a task is judged on must be <acceptance> blocks)",
                )
        for ref in (*in_scope, *accepted):
            target = by_id[ref]
            if target["status"] != "active" and ref not in acknowledged:
                raise SpecError(
                    "inactive-reference-unacknowledged",
                    f"{task_id} references {ref} status={target['status']} without "
                    f"acknowledges={ref} (a retired block must be named as a deliberate inclusion)",
                )
        exclusions: dict = {}
        for entry in block["exclusions"]:
            ref = entry["ref"]
            if not ref:
                raise SpecError(
                    "missing-exclusion-ref",
                    f"{task_id}: an <exclusion> element carries no ref",
                )
            if ref in exclusions:
                raise SpecError(
                    "task-duplicate-reference",
                    f"{task_id} <exclusion ref={ref}> is declared more than once",
                )
            if not entry["reason"]:
                raise SpecError(
                    "missing-exclusion-reason",
                    f"{task_id} exclusion {ref} carries no reason (why it is OUT of scope is "
                    "the half of the contract a later agent reads first)",
                )
            exclusions[ref] = entry
        unreasoned = [ref for ref in out_scope if ref not in exclusions]
        if unreasoned:
            raise SpecError(
                "missing-exclusion-reason",
                f"{task_id} out-of-scope={','.join(unreasoned)} with no "
                "<exclusion ref=... reason=...> naming why it is excluded",
            )
        stray = [ref for ref in exclusions if ref not in out_scope]
        if stray:
            raise SpecError(
                "unexpected-exclusion",
                f"{task_id} exclusion {','.join(sorted(stray))} is not in out-of-scope "
                "(an exclusion for a block the task never mentions is a contradiction)",
            )
        scope_version = block.get("scope_version") or ""
        if scope_version:
            for attribute in ("in-scope", "acceptance", "out-of-scope"):
                for ref in block["refs"].get(attribute, ()):
                    found = by_id[ref]
                    if found["version"] != scope_version:
                        raise SpecError(
                            "cross-version-reference",
                            f"{task_id} {attribute}={ref} version={found['version']} but the task "
                            f"scope is pinned to {scope_version}",
                        )


def load_spec_corpus(
    spec_root, *, tolerate_unreadable: bool = False, skipped: list | None = None
) -> tuple:
    """Parse and validate every XML document under a spec root.

    Returns (root, documents_by_relpath, blocks_in_corpus_order). Any integrity failure raises a
    typed SpecError BEFORE a caller can render, so a refusal never yields a partial document.

    `tolerate_unreadable` is the AUG-146 seam, and it belongs to the SELECTION-scoped verbs
    (`list`, `assemble`) only: a document that is not well-formed as XML is then skipped, counted
    in `skipped` (a list the caller passes in), and the remaining corpus is still indexed — one
    work-in-progress file no longer takes every query offline (PRD AC-14). Everything else stays
    corpus-wide and loud: the schema is still loaded, every PARSED document is still checked
    against it (`schema-invalid`), include cycles and dangling includes still refuse, and the
    reference graph still has to resolve whole — a reference into a skipped document cannot be
    proved, so it refuses rather than passing silently. Corpus-wide verbs (`validate`, `check`,
    `bundle`) never pass this flag, so the malformed file is named and the exit code is 2.


    The verdict has THREE layers, in this order, and every CLI verb goes through all three:

    1. the published grammar must be loadable — `specs/schema/auger-spec-1.0.xsd` through a real
       XSD 1.0 processor, which is a `dev`/`auger[xsd]` extra (owner decision D-1). No processor,
       no schema file, or a grammar the processor cannot load is a typed refusal, never a pass
       (`xsd-processor-missing`, `xsd-schema-missing`, `xsd-schema-unloadable`);
    2. the executable rules below (root, namespace, schema version, block grammar, typed edges);
    3. conformance of every document to the schema (`schema-invalid`).

    Layer 3 is the backstop, so it runs LAST: the code's rules give the more specific diagnostic,
    and the schema catches everything the code would otherwise let through — which is what made
    this gate missing before AUG-125/AUG-133, when the schema was loadable by nothing and loaded by
    nothing while `spec validate` printed "ok" for documents the grammar rejects.
    """
    root = Path(spec_root).expanduser()
    if not root.is_dir():
        raise SpecError("missing-spec-root", f"{spec_root} is not a directory")
    root = root.resolve()
    paths, refusals = _spec_discover_documents(root)
    if not paths:
        if refusals:
            raise SpecError(refusals[0]["code"], refusals[0]["detail"])
        raise SpecError("empty-corpus", f"no *.xml documents under {root}")
    if refusals and not tolerate_unreadable:
        raise SpecError(refusals[0]["code"], refusals[0]["detail"])
    schema = _spec_load_schema()
    unreadable = [] if skipped is None else skipped
    unreadable.extend(refusals)
    documents = _spec_read_documents(
        paths, root, tolerate_unreadable=tolerate_unreadable, skipped=unreadable
    )
    if not documents:
        # Every document was skipped: a selection over an empty index would answer "no-match" for
        # a reason the author cannot see, so name the real one instead.
        first = unreadable[0]
        raise SpecError(first["code"], first["detail"])
    include_map = _spec_include_map(documents, root)
    included = {target for targets in include_map.values() for target in targets}
    blocks: list = []
    spliced: dict = {}
    for rel in sorted(documents):
        if rel in included:
            continue  # a fragment: its blocks are assembled at their <include> site
        _spec_collect(documents[rel], rel, root, documents, [rel], blocks, spliced)
    if not blocks:
        raise SpecError(
            "no-root-document", f"{root}: every document is included by another"
        )
    _spec_validate_blocks(blocks)
    # Layer 3 covers every document that was READ: a skipped (not-well-formed) file has no tree to
    # check, and a corpus-wide verb never gets here with one, because it refuses at read time.
    _spec_assert_schema_conformance(
        schema,
        [path for path in paths if path.relative_to(root).as_posix() in documents],
        root,
    )
    return root, documents, blocks


def _spec_describe_selection(ids, kinds, applies_to, statuses, versions) -> str:
    pairs = (
        ("id", ids),
        ("kind", kinds),
        ("applies-to", applies_to),
        ("status", statuses),
        ("version", versions),
    )
    parts = [f"{label}={value}" for label, values in pairs for value in values]
    return ", ".join(parts)


def _spec_matches(block: dict, ids, kinds, applies_to, statuses, versions) -> bool:
    """Attribute matching: AND across criteria, OR within one criterion's repeated values."""
    if ids and block["id"] not in ids:
        return False
    if kinds and block["kind"] not in kinds:
        return False
    if applies_to and not (set(applies_to) & set(block["applies_to"])):
        return False
    if statuses and block["status"] not in statuses:
        return False
    if versions and block["version"] not in versions:
        return False
    return True


def select_spec_blocks(
    blocks: list,
    *,
    ids=(),
    kinds=(),
    applies_to=(),
    statuses=(),
    versions=(),
    closure: bool = True,
    empty_ok: bool = False,
    forward_edges=SPEC_FORWARD_CLOSURE_ATTRS,
) -> tuple:
    """Select by id/attributes and expand ONLY the explicit dependency closure.

    Closure edges: forward `depends-on`, plus INVERSE `verifies` (a block that declares it verifies
    a selected block is part of that block's explicit closure — its acceptance criterion or
    verification plan). Every other typed edge must resolve but never expands a bundle.
    Returns (selection_text, ordered records covering matched + dependency blocks).

    `forward_edges` (AUG-122) is how the CONTEXT query follows `verifies` forward as well: a
    requirement's declared verification plan is part of the contract it is judged against, so a
    context bundle that stopped at the requirement would hide the test that proves it. The default
    stays `SPEC_FORWARD_CLOSURE_ATTRS`, so assembly semantics are unchanged for every existing
    caller.

    `empty_ok` splits the two shapes a caller must be able to tell apart (AUG-149):

      * no selector at all is always `empty-selector` — assembly is never a silent whole-corpus
        dump, whatever `empty_ok` says;
      * a selector that MATCHES NOTHING is `no-match` by default (strict, for Python callers who
        want a refusal), and with `empty_ok=True` it returns `(selection, [])` so the CLI can
        report an empty selection as a report instead of an error. The CLI prints a named notice
        for that shape, because "your selector matched nothing" and "the tool had nothing to say"
        must not look the same to a script.
    """
    ids = tuple(ids)
    kinds = tuple(kinds)
    applies_to = tuple(applies_to)
    statuses = tuple(statuses)
    versions = tuple(versions)
    if not (ids or kinds or applies_to or statuses or versions):
        raise SpecError(
            "empty-selector",
            "pass at least one of --id/--kind/--applies-to/--status/--version "
            "(assembly is never a silent whole-corpus dump)",
        )
    selection = _spec_describe_selection(ids, kinds, applies_to, statuses, versions)
    matched = [
        block
        for block in blocks
        if _spec_matches(block, ids, kinds, applies_to, statuses, versions)
    ]
    if not matched:
        if not empty_ok:
            raise SpecError("no-match", f"{selection} matched no block")
        return selection, []

    by_id = {block["id"]: block for block in blocks}
    inverse: dict = {}
    for block in blocks:
        for attribute in SPEC_INVERSE_CLOSURE_ATTRS:
            for target in block["refs"].get(attribute, ()):
                inverse.setdefault(target, []).append((block["id"], attribute))
    chosen = {block["id"]: {"why": "matched", "via": None} for block in matched}
    if closure:
        queue = deque(block["id"] for block in matched)
        while queue:
            current = queue.popleft()
            edges = [
                (target, attribute)
                for attribute in forward_edges
                for target in by_id[current]["refs"].get(attribute, ())
            ]
            edges.extend(inverse.get(current, ()))
            for target, attribute in edges:
                if target in chosen:
                    continue
                chosen[target] = {"why": "dependency", "via": f"{current} {attribute}"}
                queue.append(target)
    ordered = sorted(chosen, key=lambda block_id: by_id[block_id]["order"])
    records = [{"block": by_id[block_id], **chosen[block_id]} for block_id in ordered]
    return selection, records


def render_spec_assembly(
    records: list,
    *,
    selection: str,
    fmt: str = "markdown",
    schema_version: str = SPEC_SCHEMA_VERSION,
    corpus: dict | None = None,
) -> str:
    """Render a selected bundle. Deterministic: same inputs, byte-identical output.

    `corpus` (AUG-146) is the measurement of the selection, not a decoration: the JSON view
    carries `documents_discovered` / `documents_parsed` / `documents_unreadable` /
    `documents_selected`, so AC-14 ("a selection reads no unselected document body") is a number
    a test can read instead of a claim a docstring makes. It is JSON-only: the Markdown document
    is a byte-pinned golden and its shape belongs to the assembly contract, not to loader health.
    """
    matched = sum(1 for record in records if record["why"] == "matched")
    counts = {
        "matched": matched,
        "dependencies": len(records) - matched,
        "total": len(records),
    }
    if fmt == "json":
        payload = {
            "schema_version": schema_version,
            "selection": selection,
            "counts": counts,
            "blocks": [
                {
                    "id": record["block"]["id"],
                    "kind": record["block"]["kind"],
                    "status": record["block"]["status"],
                    "version": record["block"]["version"],
                    "applies_to": list(record["block"]["applies_to"]),
                    "method": record["block"]["method"],
                    "refs": {
                        key: list(value)
                        for key, value in record["block"]["refs"].items()
                    },
                    "source": record["block"]["source"],
                    "selection": record["why"],
                    "via": record["via"],
                    "text": record["block"]["text"],
                }
                for record in records
            ],
        }
        if corpus is not None:
            payload["corpus"] = dict(corpus)
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if fmt != "markdown":
        raise SpecError("unsupported-format", f"{fmt!r} (markdown or json)")
    lines = [
        f"# Auger spec assembly — {counts['total']} block(s)",
        "",
        f"selector: {selection}",
        f"matched: {counts['matched']} · dependencies: {counts['dependencies']} "
        f"· total: {counts['total']}",
        f"schema: {schema_version}",
    ]
    for record in records:
        block = record["block"]
        lines.extend(
            [
                "",
                f"## {block['id']} · {block['kind']} · {block['status']} "
                f"· v{block['version']}",
                f"- source: {block['source']}",
            ]
        )
        if block["applies_to"]:
            lines.append(f"- applies-to: {' '.join(block['applies_to'])}")
        if block["method"]:
            label = (
                "verification" if block["kind"] in SPEC_REQUIRES_METHOD else "method"
            )
            lines.append(f"- {label}: {block['method']}")
        for attribute in SPEC_EDGE_ATTRS:
            targets = block["refs"].get(attribute)
            if targets:
                lines.append(f"- {attribute}: {' '.join(targets)}")
        if record["why"] == "matched":
            lines.append("- selection: matched")
        else:
            lines.append(f"- selection: dependency (via {record['via']})")
        if block["text"]:
            lines.extend(["", block["text"]])
    return "\n".join(lines) + "\n"


def _spec_write_atomic(path, text: str) -> None:
    """Write `text` to `path` via a sibling temp file + rename, so no partial file is ever left.

    AUG-145: every failure here is a TYPED `SpecError`, because `--out` was the one path in the
    `spec` surface that answered a caller with a raw Python stack and exit 1 — `FileNotFoundError`
    from `tempfile.mkstemp` for a missing parent directory, `IsADirectoryError` from `os.replace`
    for a target that is a directory — instead of the `spec error (<code>)` / rc 2 contract every
    other refusal keeps.

    The parent directory is NOT created. `spec` creates nothing when a run refuses, and silently
    materialising a directory tree is a write the caller never asked for; a missing parent is
    refused by name (`missing-out-directory`) so the caller can decide. Nothing is left behind on
    any path: the temp file is unlinked before the refusal is raised.
    """
    target = Path(path).expanduser()
    directory = target.parent if str(target.parent) else Path(".")
    if target.is_dir():
        raise SpecError(
            "invalid-out-path",
            f"{target} is a directory; --out names a file (nothing was written)",
        )
    if not directory.is_dir():
        raise SpecError(
            "missing-out-directory",
            f"{directory} is not a directory; create it first or pass --out into an "
            "existing one (nothing was written)",
        )
    try:
        handle_fd, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(directory)
        )
    except OSError as exc:
        raise SpecError(
            "unwritable-out",
            f"{directory}: {exc.strerror or exc} (nothing was written)",
        ) from exc
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise SpecError(
            "unwritable-out", f"{target}: {exc.strerror or exc} (nothing was written)"
        ) from exc
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


# ----------------------------- task scope contract + one-query context (AUG-121 / AUG-122) -------
# A task is a SMALL SPEC for one exact request (owner clarification 2026-10-30, PRD R14). The two
# verbs here are the two halves of that claim:
#
#   `spec task-packet` — turn one authored `<task>` block into the exact packet: the task's own
#                        identity, the in-scope blocks, the acceptance criteria, and the excluded
#                        blocks WITH the reason each was excluded. Deterministic bytes, and a
#                        sha256 digest bound to the whole packet so an approval cannot drift.
#   `spec task-export` — write that packet to the canonical board through `boardctl`, and ONLY
#                        after the plan digest matches the approved one. A refused export invokes
#                        no boardctl at all, so the two canonical JSONL files cannot move.
#   `spec context`     — the ONE query an agent runs when it reaches a task or a code location:
#                        the task, its in-scope blocks and their closure, the explicit exclusions,
#                        or (for code) the block a `ch:trace spec=` reference names. Never a
#                        whole-document or whole-corpus dump.
#
# Deliberate non-goals, because they are how this surface would rot: no scheduler dispatch, no task
# completion, no board UPDATE (a re-detected change is a new approval, not a silent write), no
# second transport, no LLM, no semantic search. `boardctl` is the sole writer and a `create` plus a
# read-back is the whole external surface.


def _spec_task_block(blocks: list, task_id: str) -> dict:
    """The `<task>` block named by `task_id`, or a typed refusal (never a silent miss)."""
    for block in blocks:
        if block["id"] == task_id and block["kind"] == "task":
            return block
    raise SpecError(
        "unknown-task",
        f"{task_id} is not a task block in this corpus (a task packet is built from a <task> "
        "block, never from a board row's prose)",
    )


def _task_packet_entry(block: dict, edge: str) -> dict:
    """One typed reference in a task packet: identity, lifecycle, provenance and the edge."""
    return {
        "id": block["id"],
        "kind": block["kind"],
        "status": block["status"],
        "version": block["version"],
        "source": block["source"],
        "method": block["method"],
        "applies_to": list(block["applies_to"]),
        "path": (block.get("obligation") or {}).get("path", ""),
        "selector": (block.get("obligation") or {}).get("selector", ""),
        "text": block["text"],
        "edge": edge,
    }


def task_packet(blocks: list, task_id: str) -> dict:
    """The AUG-121 packet: exact scope, exact exclusions with reasons, nothing inferred.

    The packet is DATA and it is complete in the sense that matters: every id it names is a block
    the same corpus validated, every entry carries that block's source/version/status, and the
    exclusion list carries the author's reason. It deliberately does NOT expand the graph closure —
    the closure is the CONTEXT query's job (`spec context`), so a packet stays a statement of what
    was approved rather than a snapshot of how the graph happened to look that day.
    """
    task = _spec_task_block(blocks, task_id)
    by_id = {block["id"]: block for block in blocks}
    packet = {
        "task": task["id"],
        "source": task["source"],
        "version": task["version"],
        "status": task["status"],
        "method": task["method"],
        "applies_to": list(task["applies_to"]),
        "scope_version": task.get("scope_version") or None,
        "in_scope": [
            _task_packet_entry(by_id[ref], "in-scope")
            for ref in task["refs"].get("in-scope", ())
        ],
        "acceptance": [
            _task_packet_entry(by_id[ref], "acceptance")
            for ref in task["refs"].get("acceptance", ())
        ],
        "out_of_scope": [
            {
                **_task_packet_entry(by_id[entry["ref"]], "out-of-scope"),
                "reason": entry["reason"],
            }
            for entry in task["exclusions"]
        ],
        "acknowledges": list(task["refs"].get("acknowledges", ())),
        "prose": task["text"],
    }
    return packet


def task_packet_digest(packet: dict) -> str:
    """sha256 of the CANONICAL packet — the thing an approval is bound to (AUG-121 AC3/AC4).

    Canonical means: sorted keys, no insignificant whitespace, UTF-8. Two runs over the same corpus
    produce the same digest, so "the approved plan" is a value an operator can compare, and any
    edit to scope, reason or acceptance text changes it.
    """
    canonical = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_approved_plan(packet: dict, declared) -> str:
    """The approved digest, or a typed refusal — called BEFORE any boardctl invocation.

    Three shapes, three codes, because they need different fixes: no approval at all
    (`unapproved-plan-digest`), an approval that is not a digest (`invalid-plan-digest`), and an
    approval for a DIFFERENT plan (`stale-plan-digest`, naming both digests).
    """
    digest = task_packet_digest(packet)
    if declared is None or not str(declared).strip():
        raise SpecError(
            "unapproved-plan-digest",
            f"{packet['task']}: no approved plan digest (pass --plan-digest or declare "
            f"plan-digest= on the task block); the plan currently hashes to {digest}",
        )
    declared = str(declared).strip()
    if not SPEC_TASK_DIGEST_RE.match(declared):
        raise SpecError(
            "invalid-plan-digest",
            f"{packet['task']}: {declared!r} is not a sha256 digest (64 lowercase hex characters)",
        )
    if declared != digest:
        raise SpecError(
            "stale-plan-digest",
            f"{packet['task']}: approved plan {declared} does not match the current plan "
            f"{digest} — the scope changed after approval, so re-approve the new digest",
        )
    return digest


def _task_marker_value(key: str, value: str) -> str:
    """One marker value: no whitespace (a marker is whitespace-separated key=value pairs)."""
    if not value or any(character.isspace() for character in value):
        raise SpecError(
            "invalid-trace-value",
            f"{key}={value!r} must be a single token (an absent value is none:<reason>)",
        )
    return value


def render_task_trace_marker(
    packet: dict,
    *,
    test=None,
    evidence=None,
    verdict=None,
    commit=None,
    witness=None,
) -> str:
    """The `ch:trace` marker for an exported task (AUG-121 AC5, AUG-119 AC6, PRD R10).

    `row` is the canonical task id and `spec` is the task block's own `file.xml#ID` source, so both
    resolve to the exact XML block rather than to a document heading. A field with nothing behind it
    yet carries `none:<reason>` — never a bare `none`, and never a green-looking placeholder.

    `witness` comes LAST so the marker a draft produced is a prefix of the marker its exported row
    carries: a reader comparing the two sees one field differ (who witnessed it), not two markers
    that share no text.
    """
    marker = "ch:trace " + " ".join(
        f"{key}={_task_marker_value(key, value)}"
        for key, value in (
            ("row", packet["task"]),
            ("spec", packet["source"]),
            ("test", test or "none:no-test-declared"),
            ("evidence", evidence or "none:no-evidence-recorded"),
            ("verdict", verdict or "none:not-judged"),
            ("commit", commit or "none:not-implemented"),
            ("witness", witness or SPEC_TASK_DRAFT_WITNESS),
        )
    )
    return marker


def _task_obligation_test(blocks: list, packet: dict) -> str:
    """The machine-checkable test the packet's closure names, as `path::selector`, or none:<reason>.

    Reads the closure through the ONE selection primitive (with `verifies` followed forward, since a
    requirement's declared verification plan is part of the contract, not a reverse-only edge), so
    the marker cannot name a test the context bundle would not contain.
    """
    seeds = [entry["id"] for entry in packet["in_scope"] + packet["acceptance"]]
    _selection, records = select_spec_blocks(
        blocks,
        ids=seeds,
        closure=True,
        forward_edges=("depends-on", "verifies"),
    )
    for record in records:
        obligation = record["block"].get("obligation") or {}
        if obligation.get("path"):
            selector = obligation.get("selector") or ""
            return (
                f"{obligation['path']}::{selector}" if selector else obligation["path"]
            )
    return "none:no-test-declared"


def parse_trace_marker(text: str) -> dict:
    """Parse one `ch:trace` marker into its fields, refusing a marker that proves nothing.

    A marker is a whitespace-separated list of `key=value` tokens on the `ch:trace` line. The rules
    are the ones the PRD's risk list implies: `row` is mandatory, every key must be a declared
    marker field (a typo'd field is silently ignored by every reader, which is worse than a crash),
    no field may repeat, and an absent value must state WHY (`none:<reason>`) instead of leaving a
    blank that reads as "fine".
    """
    line = None
    for candidate in text.splitlines():
        if SPEC_TRACE_MARKER_RE.search(candidate):
            line = candidate
            break
    if line is None:
        raise SpecError(
            "missing-trace-marker", "no `ch:trace` marker in the given text"
        )
    body = SPEC_TRACE_MARKER_RE.search(line).group(1)
    fields: dict = {}
    for token in body.split():
        key, separator, value = token.partition("=")
        if not separator or not key:
            raise SpecError(
                "malformed-trace-marker", f"{token!r} is not key=value ({line.strip()})"
            )
        if not SPEC_TRACE_KEY_RE.fullmatch(key):
            raise SpecError(
                "malformed-trace-marker", f"{key!r} is not a marker field name"
            )
        if key not in SPEC_TRACE_FIELDS:
            raise SpecError(
                "unknown-trace-field",
                f"{key!r} is not a ch:trace field (one of: {', '.join(SPEC_TRACE_FIELDS)})",
            )
        if key in fields:
            raise SpecError("duplicate-trace-field", f"{key}= appears more than once")
        fields[key] = value
    if not fields.get("row"):
        raise SpecError(
            "missing-trace-field", "ch:trace carries no row= (the one mandatory field)"
        )
    for key, value in fields.items():
        if value in ("", "none"):
            raise SpecError(
                "unexplained-absent-field",
                f"{key}=" + value + " gives no reason — write none:<reason>",
            )
    return fields


def resolve_trace_marker(text, blocks: list) -> dict:
    """Resolve a marker's `row`/`spec` against the corpus, and expand the task's scope if it is one.

    `spec` accepts the two shapes the fleet writes: a bare block id, or the block's `file.xml#ID`
    source. A value that names a file the block is not authored in is a STALE reference, not a hit.
    """
    fields = parse_trace_marker(text) if isinstance(text, str) else dict(text)
    by_id = {block["id"]: block for block in blocks}
    row = fields["row"]
    if row not in by_id:
        raise SpecError("unknown-trace-row", f"row={row} names no block in this corpus")
    reference = fields.get("spec", "")
    if not reference or reference.startswith("none:"):
        raise SpecError(
            "missing-trace-field",
            "spec= is absent or explicitly none: — nothing to resolve",
        )
    resolved: list = []
    missing: list = []
    foreign: list = []
    for value in reference.split(","):
        file_part, separator, fragment = value.partition("#")
        block_id = fragment if separator else value
        block = by_id.get(block_id)
        if block is None:
            missing.append(value)
        elif separator and block["source_file"] != file_part:
            foreign.append(value)
        elif block_id not in resolved:
            resolved.append(block_id)
    if missing:
        raise SpecError(
            "unknown-spec-id", f"spec={','.join(missing)} names no block in this corpus"
        )
    if foreign:
        raise SpecError(
            "stale-spec-ref",
            f"spec={','.join(foreign)} names a document the block is not authored in",
        )
    scope: list = []
    for block_id in resolved:
        if by_id[block_id]["kind"] == "task":
            packet = task_packet(blocks, block_id)
            if row != block_id:
                raise SpecError(
                    "trace-row-mismatch",
                    f"row={row} but spec= names the task {block_id} — a marker points at ONE task",
                )
            scope = [
                *packet["in_scope"],
                *packet["acceptance"],
                *packet["out_of_scope"],
            ]
    return {
        "fields": fields,
        "row": row,
        "blocks": resolved,
        "missing": missing,
        "scope": scope,
    }


def _boardctl(boardctl_path, *argv: str) -> tuple:
    """Run boardctl once. The ONLY external command this surface issues (AUG-121 AC6)."""
    command = boardctl_path or BOARDCTL_BINARY
    try:
        done = subprocess.run(
            [str(command), *argv], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise SpecError(
            "boardctl-missing",
            f"{command}: {exc.strerror or exc} — the canonical board is written by boardctl only; "
            "install coding-hermes-tools and re-run",
        ) from None
    return done.returncode, done.stdout, done.stderr


def _board_readback(boardctl_path, board, row_id: str):
    """`boardctl show <id>` as a dict, or None when the row does not exist yet."""
    code, out, err = _boardctl(boardctl_path, "-C", str(board), "show", row_id)
    if code != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SpecError(
            "board-readback-unreadable",
            f"boardctl show {row_id} printed no JSON ({exc}): {out.strip()[:200]}",
        ) from None


def _task_row_title(packet: dict) -> str:
    """A short, deterministic row title built from the task's own prose."""
    sentence = packet["prose"].split(".")[0].strip() or packet["task"]
    title = f"[scoped task] {packet['task']}: {sentence}"
    return title[:120]


def task_board_row(packet: dict) -> dict:
    """The canonical row body: existing supported fields only, byte-for-byte reproducible.

    `capability_tags` carries the machine-readable scope (`<edge>:<ID>@<version>`, in packet order)
    and `reasoning` carries the canonical packet JSON plus the generated marker. `depends_on` is
    deliberately NOT used: boardctl resolves it against real task ids and refuses a create that
    names spec blocks, so writing scope there would silently break the export.
    """
    tags = [
        *(f"in-scope:{entry['id']}@{entry['version']}" for entry in packet["in_scope"]),
        *(
            f"acceptance:{entry['id']}@{entry['version']}"
            for entry in packet["acceptance"]
        ),
        *(
            f"out-of-scope:{entry['id']}@{entry['version']}"
            for entry in packet["out_of_scope"]
        ),
    ]
    canonical = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        "title": _task_row_title(packet),
        "capability_tags": tags,
        "acceptance_criteria": "\n".join(
            f"{entry['id']}: {entry['text']}" for entry in packet["acceptance"]
        ),
        "reasoning": canonical,
    }


def export_task_packet(
    spec_root,
    task_id: str,
    *,
    board,
    plan_digest=None,
    boardctl=None,
    repo_root=None,
) -> dict:
    """Export ONE approved task packet to the canonical board through boardctl (AUG-121).

    Order is the contract: load the corpus (a corpus-wide verdict, so a half-written neighbour is
    refused rather than exported around), build the packet, verify the APPROVED DIGEST, and only
    then touch boardctl. A refused approval therefore cannot move a single byte of `tasks.jsonl` or
    `events.jsonl`, which is what AC4 measures.

    An existing row with byte-identical scope and reasoning is reported as `unchanged` and NOT
    re-created (one row per task id); an existing row that DIFFERS is a conflict and is refused —
    updating a canonical row needs its own approval-bound path, not a side effect of this one.
    """
    _root, _documents, blocks = load_spec_corpus(spec_root)
    packet = task_packet(blocks, task_id)
    block = _spec_task_block(blocks, task_id)
    declared = (
        plan_digest if plan_digest is not None else (block.get("plan_digest") or None)
    )
    digest = require_approved_plan(packet, declared)
    marker = render_task_trace_marker(
        packet, test=_task_obligation_test(blocks, packet)
    )
    row = task_board_row(packet)
    reasoning = f"{row['reasoning']}\n{marker}"

    def outcome(status: str, readback) -> dict:
        return {
            "status": status,
            "row": task_id,
            "digest": digest,
            "source": packet["source"],
            "in_scope": [entry["id"] for entry in packet["in_scope"]],
            "out_of_scope": [entry["id"] for entry in packet["out_of_scope"]],
            "trace": marker,
            "readback": {
                "id": readback.get("id"),
                "status": readback.get("status"),
                "capability_tags": readback.get("capability_tags"),
                "reasoning": readback.get("reasoning"),
                "acceptance_criteria": readback.get("acceptance_criteria"),
            },
        }

    existing = _board_readback(boardctl, board, task_id)
    if existing is not None:
        unchanged = (
            existing.get("capability_tags") == row["capability_tags"]
            and existing.get("reasoning") == reasoning
        )
        if not unchanged:
            raise SpecError(
                "board-row-conflict",
                f"{task_id} already exists on {board} with a different scope or reasoning — an "
                "update is its own approval-bound operation, not a side effect of an export",
            )
        return outcome("unchanged", existing)

    argv = [
        "-C",
        str(board),
        "create",
        "--id",
        task_id,
        "--title",
        row["title"],
        "--status",
        "pending",
        "--capability-tags",
        ",".join(row["capability_tags"]),
        "--reasoning",
        reasoning,
    ]
    if row["acceptance_criteria"]:
        argv.extend(["--acceptance-criteria", row["acceptance_criteria"]])
    code, out, err = _boardctl(boardctl, *argv)
    if code != 0:
        raise SpecError(
            "board-create-refused",
            f"boardctl create exited {code}: {(err or out).strip()[:400]}",
        )
    readback = _board_readback(boardctl, board, task_id)
    if readback is None:
        raise SpecError(
            "board-readback-missing",
            f"boardctl created {task_id} but `show` cannot read it back — the write is unproven",
        )
    if readback.get("reasoning") != reasoning:
        raise SpecError(
            "board-readback-mismatch",
            f"the row read back from {board} does not match the approved packet byte for byte "
            f"(expected {len(reasoning)} characters, got "
            f"{len(readback.get('reasoning') or '')})",
        )
    if readback.get("capability_tags") != row["capability_tags"]:
        raise SpecError(
            "board-readback-mismatch",
            f"the row's scope tags read back as {readback.get('capability_tags')!r}, expected "
            f"{row['capability_tags']!r}",
        )
    return outcome("exported", readback)


# ---------------------------------------------------------------- approved plans (AUG-119)
# A plan is the reviewed unit ABOVE a task: many tasks, one version, one digest. The SG7 seam
# (`spec task-packet` / `spec task-export`) answers "what is this ONE task, and may it be written";
# this seam answers "is THIS SET of tasks approved, write them all, and tell me what landed".
#
# Three properties are load-bearing, and each one is refused loudly rather than defaulted:
#
#   * APPROVAL IS A RECORD, not a flag. The digest an operator approved is written to an
#     append-only ledger with the plan id, the plan VERSION and the approver, and the LATEST record
#     for a plan is the decision — so a rejection is a new fact instead of an edit of history. Any
#     edit to the corpus changes the digest, which makes the old approval STALE rather than
#     silently still valid.
#   * THE BOARD IS WRITTEN BY boardctl ONLY: `create` for a row that does not exist, `update` for
#     the fields boardctl supports rewriting (title, acceptance criteria) when they drift.
#     Everything else is a conflict — an export never overwrites a row's scope behind its owner.
#   * RECONCILIATION IS READ-ONLY. Board status/commit/verdict evidence is copied INTO Auger with
#     the witness named and `writes: none`; a commit or a verdict that does not BIND is refused
#     rather than recorded as though it had.
def spec_block_digest(block: dict) -> str:
    """sha256 of one block's canonical record — the digest a packet reference must carry.

    Canonical means sorted keys, no insignificant whitespace, and no absolute path: the block's
    `source_file` is corpus-relative, so the same corpus hashed in two checkouts produces the same
    digests. The edges are part of the hash on purpose — a requirement whose `verifies=` target
    changed is not the requirement that was approved.
    """
    payload = {
        "id": block["id"],
        "kind": block["kind"],
        "status": block["status"],
        "version": block["version"],
        "method": block.get("method") or "",
        "text": block["text"],
        "source_file": block["source_file"],
        "obligation": dict(block.get("obligation") or {}),
        "refs": {
            key: list(value) for key, value in sorted((block.get("refs") or {}).items())
        },
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_scope_block(block: dict) -> dict:
    """One reference in a plan/packet: identity, lifecycle, provenance and its digest."""
    return {
        "id": block["id"],
        "kind": block["kind"],
        "status": block["status"],
        "version": block["version"],
        "source": block["source"],
        "digest": spec_block_digest(block),
    }


def _plan_test_obligations(blocks: list, packet: dict, task_id: str) -> tuple:
    """(evidence, test_refs) for one task, read from the task's own closure — never from a glob.

    The closure is expanded through the ONE selection primitive with `depends-on` and `verifies`
    followed forward, so the tests a plan names are the tests its contract declares. A verification
    block that names a runner GitReins cannot run, or a path with no selector, is refused HERE:
    a packet carrying it would be a fail-closed Tier-1 result on the consumer side, which is the
    worst place to discover an authoring mistake.
    """
    seeds = [entry["id"] for entry in packet["in_scope"] + packet["acceptance"]]
    _selection, records = select_spec_blocks(
        blocks,
        ids=seeds,
        closure=True,
        forward_edges=("depends-on", "verifies"),
    )
    evidence: list = []
    test_refs: list = []
    for record in records:
        block = record["block"]
        if block["kind"] not in ("verification", "test"):
            continue
        obligation = block.get("obligation") or {}
        path = (obligation.get("path") or "").strip()
        if not path:
            continue
        runner = (obligation.get("runner") or "").strip()
        if runner and runner != "pytest":
            raise SpecError(
                "unsupported-test-runner",
                f"{task_id}: {block['id']} names runner={runner!r}; the judge harness runs pytest, "
                "so a plan naming another runner produces a packet the consumer refuses",
            )
        selector = (obligation.get("selector") or "").strip()
        if not selector:
            raise SpecError(
                "missing-test-selector",
                f"{task_id}: {block['id']} names path={path} with no selector — a test reference "
                "without a selector cannot be run, so it is not evidence",
            )
        evidence.append(
            {
                "id": block["id"],
                "kind": block["kind"],
                "method": block.get("method") or "",
                "runner": "pytest",
                "path": path,
                "selector": selector,
            }
        )
        test_refs.append(
            {
                "id": block["id"],
                "kind": block["kind"],
                "status": block["status"],
                "version": block["version"],
                "source": block["source"],
                "digest": spec_block_digest(block),
                "runner": "pytest",
                "path": path,
                "selector": selector,
            }
        )
    return evidence, test_refs


def _plan_task_entry(blocks: list, task: dict, plan_id: str) -> dict:
    """One task's plan entry: identity, criteria, dependencies, anchors, evidence, packet, digest."""
    packet = task_packet(blocks, task["id"])
    by_id = {block["id"]: block for block in blocks}
    evidence, test_refs = _plan_test_obligations(blocks, packet, task["id"])

    scope_blocks: list = []
    seen: set = set()
    for item in (*packet["in_scope"], *packet["acceptance"]):
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        scope_blocks.append(_plan_scope_block(by_id[item["id"]]))

    anchors = [
        {
            "id": packet["task"],
            "source": packet["source"],
            "file": task["source_file"],
        }
    ]
    known = {anchor["id"] for anchor in anchors}
    for item in (*packet["in_scope"], *packet["acceptance"]):
        if item["id"] in known:
            continue
        known.add(item["id"])
        anchors.append(
            {
                "id": item["id"],
                "source": item["source"],
                "file": item["source"].split("#")[0],
            }
        )

    return {
        "task": packet["task"],
        "source": packet["source"],
        "status": packet["status"],
        "version": packet["version"],
        "method": packet["method"],
        "applies_to": list(packet["applies_to"]),
        "plan": plan_id,
        "plan_version": task.get("plan_version") or "",
        # Task-to-task edges, read from the corpus' own `depends-on`; the plan is exported in this
        # list's order, so a plan that needs an order states it rather than relying on file order.
        "dependencies": list(task["refs"].get("depends-on", ())),
        "in_scope": [
            _plan_scope_block(by_id[item["id"]]) for item in packet["in_scope"]
        ],
        "out_of_scope": [
            {**_plan_scope_block(by_id[item["id"]]), "reason": item["reason"]}
            for item in packet["out_of_scope"]
        ],
        "acceptance": [
            {
                **_plan_scope_block(by_id[item["id"]]),
                "text": item["text"],
            }
            for item in packet["acceptance"]
        ],
        "anchors": anchors,
        "evidence": evidence,
        "spec_blocks": scope_blocks,
        "test_refs": test_refs,
        "packet": packet,
        "packet_digest": task_packet_digest(packet),
        "trace": render_task_trace_marker(
            packet,
            test=_task_obligation_test(blocks, packet),
            witness=SPEC_TASK_EXPORT_WITNESS,
        ),
    }


def plan_digest(plan: dict) -> str:
    """sha256 of the canonical plan, EXCLUDING its own digest field.

    The digest therefore lives INSIDE the document it describes without being self-referential, and
    two drafts of the same corpus agree on it regardless of which checkout they were built in.
    """
    payload = {key: value for key, value in plan.items() if key != "digest"}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_spec_plan(spec_root, plan_id: str, *, loaded=None) -> dict:
    """The DRAFT plan for `plan_id`: every task carrying `plan=<plan_id>`, with one version+digest.

    Membership is authored on the task (`plan=` / `plan-version=`), never inferred from a filename,
    so a plan cannot silently grow by dropping a file into a directory. A plan whose members
    disagree on the version is refused: half of it would be from a review the approval never saw.
    The task order is the corpus order, which is deterministic; `dependencies` states any order the
    plan actually requires.
    """
    root, documents, blocks = load_spec_corpus(spec_root) if loaded is None else loaded
    members = [
        block
        for block in blocks
        if block["kind"] == "task" and (block.get("plan") or "") == plan_id
    ]
    if not members:
        raise SpecError(
            "unknown-plan",
            f"{plan_id}: no <task> block in {root} carries plan={plan_id} — plan membership is "
            "authored on the task (`plan=` + `plan-version=`), never inferred from a directory",
        )
    versions = sorted({member.get("plan_version") or "" for member in members})
    if len(versions) > 1:
        shown = ", ".join(version or "absent" for version in versions)
        raise SpecError(
            "plan-version-conflict",
            f"{plan_id}: its tasks disagree on plan-version ({shown}) — one plan has ONE version, "
            "because an approval is bound to a version and a digest together",
        )
    version = versions[0]
    if not version:
        raise SpecError(
            "missing-plan-version",
            f"{plan_id}: its tasks carry no plan-version= — a plan is versioned (the approval "
            "names the version as well as the digest), and an unversioned plan cannot be approved",
        )
    sources: dict = {}
    for rel in sorted(documents):
        try:
            sources[rel] = hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
        except OSError as exc:
            raise SpecError(
                "unreadable-spec-source", f"{rel}: {exc.strerror or exc}"
            ) from None
    tasks = [_plan_task_entry(blocks, member, plan_id) for member in members]
    plan = {
        "plan": plan_id,
        "version": version,
        "sources": sources,
        "tasks": tasks,
        "counts": {
            "tasks": len(tasks),
            "in_scope": sum(len(entry["in_scope"]) for entry in tasks),
            "out_of_scope": sum(len(entry["out_of_scope"]) for entry in tasks),
            "test_refs": sum(len(entry["test_refs"]) for entry in tasks),
            "dependencies": sum(len(entry["dependencies"]) for entry in tasks),
        },
    }
    plan["digest"] = plan_digest(plan)
    return plan


# ---------------------------------------------------------------- the approval ledger
def plan_ledger_dir(ledger=None, spec_root=None) -> Path:
    """The directory holding a plan's approvals: explicit, else `<spec root>/.auger`."""
    if ledger is not None and str(ledger).strip():
        return Path(ledger).expanduser()
    if spec_root is None:
        raise SpecError(
            "missing-ledger",
            "no approval ledger given and no spec root to derive one from; pass --ledger <dir>",
        )
    return Path(spec_root).expanduser() / ".auger"


def plan_approval_file(ledger=None, *, spec_root=None) -> Path:
    return plan_ledger_dir(ledger, spec_root) / SPEC_PLAN_LEDGER_NAME


def _plan_approval_records(path: Path) -> list:
    """Every record in the ledger, validated — a corrupt line is a REFUSAL, never a skipped line.

    Skipping an unreadable record could resurrect the decision before it, which is how a rejected
    plan quietly becomes approved again.
    """
    if not path.is_file():
        return []
    records: list = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SpecError(
                "invalid-approval-record", f"{path}:{number}: not JSON ({exc})"
            ) from None
        if not isinstance(payload, dict):
            raise SpecError(
                "invalid-approval-record",
                f"{path}:{number}: a record must be a JSON object",
            )
        plan = str(payload.get("plan") or "")
        version = str(payload.get("version") or "")
        digest = str(payload.get("digest") or "")
        decision = str(payload.get("decision") or "")
        approver = str(payload.get("approver") or "")
        problems = []
        if not plan:
            problems.append("plan")
        if not SPEC_VERSION_RE.match(version):
            problems.append(f"version={version or 'absent'}")
        if not SPEC_TASK_DIGEST_RE.match(digest):
            problems.append(f"digest={digest[:16] or 'absent'}")
        if decision not in SPEC_PLAN_DECISIONS:
            problems.append(f"decision={decision or 'absent'}")
        if not approver.strip():
            problems.append("approver")
        if problems:
            raise SpecError(
                "invalid-approval-record",
                f"{path}:{number}: {', '.join(problems)} "
                f"(expected plan, X.Y.Z version, a 64-hex digest, a decision in "
                f"{', '.join(SPEC_PLAN_DECISIONS)} and a named approver)",
            )
        records.append(
            {
                "plan": plan,
                "version": version,
                "digest": digest,
                "decision": decision,
                "approver": approver,
                "at": str(payload.get("at") or ""),
            }
        )
    return records


def read_plan_approval(ledger=None, plan_id: str = "", *, spec_root=None):
    """The LATEST record for `plan_id` (the decision), or None when the plan was never decided."""
    records = [
        record
        for record in _plan_approval_records(
            plan_approval_file(ledger, spec_root=spec_root)
        )
        if record["plan"] == plan_id
    ]
    return records[-1] if records else None


def record_plan_approval(
    ledger,
    plan: dict,
    *,
    digest,
    decision: str,
    approver: str,
    at: str = "",
    spec_root=None,
) -> dict:
    """Append one decision, BOUND to the plan's current version and digest.

    The digest is checked against the plan as it exists NOW, so an operator cannot approve a digest
    they are not looking at (the stale case names both values), and the record is read back before
    it is reported as recorded — an approval nobody can read is not an approval.
    """
    if decision not in SPEC_PLAN_DECISIONS:
        raise SpecError(
            "invalid-approval-decision",
            f"{decision!r} is not a decision (one of: {', '.join(SPEC_PLAN_DECISIONS)})",
        )
    declared = str(digest or "").strip()
    if not SPEC_TASK_DIGEST_RE.match(declared):
        raise SpecError(
            "invalid-plan-digest",
            f"{declared!r} is not a sha256 digest (64 lowercase hex characters)",
        )
    if declared != plan["digest"]:
        raise SpecError(
            "stale-plan-digest",
            f"{plan['plan']}: approved digest {declared} does not match the current plan "
            f"{plan['digest']} — approve the plan you are actually looking at",
        )
    if not str(approver or "").strip():
        raise SpecError(
            "missing-approver",
            f"{plan['plan']}: an approval must name who decided (an anonymous approval is not a "
            "decision anyone can be held to)",
        )
    record = {
        "plan": plan["plan"],
        "version": plan["version"],
        "digest": declared,
        "decision": decision,
        "approver": str(approver).strip(),
        "at": str(at or ""),
    }
    path = plan_approval_file(ledger, spec_root=spec_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        raise SpecError(
            "approval-ledger-unwritable", f"{path}: {exc.strerror or exc}"
        ) from None
    stored = read_plan_approval(ledger, plan["plan"], spec_root=spec_root)
    if stored != record:
        raise SpecError(
            "approval-ledger-unverified",
            f"{path}: the record written back does not match the decision ({stored!r})",
        )
    return record


def require_plan_approval(
    plan: dict, ledger=None, *, spec_root=None, declared=None
) -> dict:
    """The approval record bound to this plan version+digest, or a typed refusal.

    Four shapes, four codes, because they need four different fixes: no decision at all
    (`unapproved-plan-digest`), a decision that was a rejection (`plan-rejected`), an approval for a
    digest the plan no longer has (`stale-plan-digest`, naming both), and an approval for another
    VERSION of the plan (`stale-plan-version`). Called BEFORE any boardctl invocation, so a refused
    approval cannot move a byte of `tasks.jsonl` or `events.jsonl`.
    """
    current = plan["digest"]
    if declared is not None and str(declared).strip():
        candidate = str(declared).strip()
        if not SPEC_TASK_DIGEST_RE.match(candidate):
            raise SpecError(
                "invalid-plan-digest",
                f"{plan['plan']}: {candidate!r} is not a sha256 digest (64 lowercase hex characters)",
            )
        if candidate != current:
            raise SpecError(
                "stale-plan-digest",
                f"{plan['plan']}: declared digest {candidate} does not match the current plan "
                f"{current}",
            )
    record = read_plan_approval(ledger, plan["plan"], spec_root=spec_root)
    if record is None:
        raise SpecError(
            "unapproved-plan-digest",
            f"{plan['plan']}: no approval record for this plan (the plan currently hashes to "
            f"{current}); record one with `spec plan-approve --plan {plan['plan']} "
            f"--digest {current} --approve --approver <name>`",
        )
    if record["decision"] == "rejected":
        raise SpecError(
            "plan-rejected",
            f"{plan['plan']}: the plan was REJECTED by {record['approver']}"
            f"{' at ' + record['at'] if record['at'] else ''} — an export needs a later approval",
        )
    if record["digest"] != current:
        raise SpecError(
            "stale-plan-digest",
            f"{plan['plan']}: approved plan {record['digest']} does not match the current plan "
            f"{current} — the plan changed after approval, so re-approve the new digest",
        )
    if record["version"] != plan["version"]:
        raise SpecError(
            "stale-plan-version",
            f"{plan['plan']}: approved version {record['version']} does not match the current "
            f"version {plan['version']} — re-approve the version that is being exported",
        )
    return record


# ---------------------------------------------------------------- plan export
def _plan_row_payload(entry: dict) -> tuple:
    """(row fields, reasoning) for one plan task, built from the APPROVED packet."""
    row = task_board_row(entry["packet"])
    return row, f"{row['reasoning']}\n{entry['trace']}"


def _readback_summary(readback: dict) -> dict:
    return {
        "id": readback.get("id"),
        "status": readback.get("status"),
        "capability_tags": readback.get("capability_tags"),
        "reasoning": readback.get("reasoning"),
        "acceptance_criteria": readback.get("acceptance_criteria"),
        "title": readback.get("title"),
    }


def _verify_plan_row(entry: dict, row: dict, reasoning: str, readback: dict) -> None:
    """The read-back must EQUAL the approved input, field by field, or the write is unproven."""
    task_id = entry["task"]
    if readback.get("id") != task_id:
        raise SpecError(
            "board-readback-mismatch",
            f"{task_id}: boardctl read back {readback.get('id')!r} — the row's id is not the "
            "approved task id",
        )
    if (readback.get("reasoning") or "") != reasoning:
        raise SpecError(
            "board-readback-mismatch",
            f"{task_id}: the row's reasoning does not match the approved packet byte for byte "
            f"(expected {len(reasoning)} characters, got "
            f"{len(readback.get('reasoning') or '')})",
        )
    if readback.get("capability_tags") != row["capability_tags"]:
        raise SpecError(
            "board-readback-mismatch",
            f"{task_id}: the row's scope tags read back as "
            f"{readback.get('capability_tags')!r}, expected {row['capability_tags']!r}",
        )
    if (readback.get("acceptance_criteria") or "") != (
        row["acceptance_criteria"] or ""
    ):
        raise SpecError(
            "board-readback-mismatch",
            f"{task_id}: the row's acceptance criteria do not equal the approved criteria",
        )


def _export_plan_task_row(boardctl, board, entry: dict) -> dict:
    """Create, repair or confirm ONE canonical row — through boardctl's supported operations only.

    Three outcomes, and nothing else: `exported` (the row did not exist), `updated` (it existed with
    the approved scope but a drifted title/acceptance text, repaired through `boardctl update`), and
    `unchanged` (it already equals the approved input). A row whose SCOPE differs is a conflict: the
    exporter refuses rather than rewriting a row its owner may already be working.
    """
    row, reasoning = _plan_row_payload(entry)
    task_id = entry["task"]
    existing = _board_readback(boardctl, board, task_id)

    def outcome(status: str, readback: dict) -> dict:
        return {
            "task": task_id,
            "status": status,
            "row": task_id,
            "source": entry["source"],
            "readback": _readback_summary(readback),
        }

    if existing is None:
        argv = [
            "-C",
            str(board),
            "create",
            "--id",
            task_id,
            "--title",
            row["title"],
            "--status",
            "pending",
            "--capability-tags",
            ",".join(row["capability_tags"]),
            "--reasoning",
            reasoning,
        ]
        if row["acceptance_criteria"]:
            argv.extend(["--acceptance-criteria", row["acceptance_criteria"]])
        code, out, err = _boardctl(boardctl, *argv)
        if code != 0:
            raise SpecError(
                "board-create-refused",
                f"{task_id}: boardctl create exited {code}: {(err or out).strip()[:400]}",
            )
        readback = _board_readback(boardctl, board, task_id)
        if readback is None:
            raise SpecError(
                "board-readback-missing",
                f"{task_id}: boardctl created the row but `show` cannot read it back — the write "
                "is unproven",
            )
        _verify_plan_row(entry, row, reasoning, readback)
        return outcome("exported", readback)

    same_scope = (
        existing.get("capability_tags") == row["capability_tags"]
        and (existing.get("reasoning") or "") == reasoning
    )
    if not same_scope:
        raise SpecError(
            "board-row-conflict",
            f"{task_id} already exists on {board} with a different SCOPE (capability tags or "
            "approved packet). `boardctl update` cannot rewrite scope, and an export never "
            "overwrites a row behind its owner — retire the row explicitly, or approve a plan "
            "that matches it",
        )
    if existing.get("title") == row["title"] and (
        existing.get("acceptance_criteria") or ""
    ) == (row["acceptance_criteria"] or ""):
        return outcome("unchanged", existing)

    code, out, err = _boardctl(
        boardctl,
        "-C",
        str(board),
        "update",
        task_id,
        "--title",
        row["title"],
        "--acceptance-criteria",
        row["acceptance_criteria"],
    )
    if code != 0:
        raise SpecError(
            "board-update-refused",
            f"{task_id}: boardctl update exited {code}: {(err or out).strip()[:400]}",
        )
    readback = _board_readback(boardctl, board, task_id)
    if readback is None:
        raise SpecError(
            "board-readback-missing",
            f"{task_id}: boardctl updated the row but `show` cannot read it back",
        )
    _verify_plan_row(entry, row, reasoning, readback)
    return outcome("updated", readback)


def export_spec_plan(
    spec_root,
    plan_id: str,
    *,
    board,
    ledger=None,
    boardctl=None,
    declared=None,
    out=None,
) -> dict:
    """Write every row of an APPROVED plan; stop at the first failure and record what landed.

    Fail-stop, not fail-silent: a half-written plan reported as "exported" is the failure mode this
    row exists to prevent, so the run reports `partial`, exits non-zero, keeps the outcome of every
    row it DID write and names the failure with boardctl's own words. The recovery record names the
    resume path, and resuming is simply re-running the same approved plan: rows already written read
    back `unchanged`, so nothing is created twice.
    """
    plan = build_spec_plan(spec_root, plan_id)
    approval = require_plan_approval(
        plan, ledger, spec_root=spec_root, declared=declared
    )
    rows: list = []
    failed = None
    for entry in plan["tasks"]:
        try:
            rows.append(_export_plan_task_row(boardctl, board, entry))
        except SpecError as exc:
            failed = {"task": entry["task"], "code": exc.code, "detail": exc.detail}
            break
    if failed is not None:
        status = "partial"
    elif all(row["status"] == "unchanged" for row in rows):
        status = "unchanged"
    else:
        status = "exported"
    record = {
        "status": status,
        "plan": plan["plan"],
        "version": plan["version"],
        "digest": plan["digest"],
        "approver": approval["approver"],
        "rows": rows,
        "failed": failed,
        "counts": {
            "tasks": len(plan["tasks"]),
            "exported": sum(1 for row in rows if row["status"] == "exported"),
            "updated": sum(1 for row in rows if row["status"] == "updated"),
            "unchanged": sum(1 for row in rows if row["status"] == "unchanged"),
        },
        "completed": [row["task"] for row in rows],
        "resume": (
            "re-run `spec plan-export` with the same plan and digest: rows already exported read "
            "back as `unchanged` and are not re-created, and the failed row is retried"
            if failed is not None
            else "none:complete"
        ),
        "writes": "boardctl",
    }
    if out is not None:
        _spec_write_atomic(
            out, json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
    return record


# ---------------------------------------------------------------- reconciliation (read-only)
def _git_tracks(root, path: str) -> bool:
    """Is `path` tracked in the repository at `root`? (`git ls-files`, the consumer's own rule.)"""
    if not path or os.path.isabs(path) or ".." in path.split(os.sep):
        return False
    if root is None:
        return False
    absolute = os.path.realpath(os.path.join(str(root), path))
    if not os.path.isfile(absolute):
        return False
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", path],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


def _git_object_exists(root, commit: str) -> bool:
    """Is `commit` a real commit in the repository at `root`? (Not merely a plausible string.)"""
    done = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


def _reconcile_repository_root(spec_root, repo_root):
    try:
        return _spec_repository_root(spec_root, repo_root)
    except SpecValidationError as exc:
        raise SpecError("unresolvable-repo-root", str(exc)) from exc


def reconcile_spec_plan(
    spec_root,
    plan_id: str,
    *,
    board,
    boardctl=None,
    repo_root=None,
    verdict_root=None,
    out=None,
) -> dict:
    """Copy board status/commit/verdict evidence INTO Auger, without writing the board.

    Read-only by construction: the only external command is `boardctl show`, and the record says
    `writes: none` so a reader can tell this apart from an export. Evidence that does not BIND is
    refused rather than recorded: a commit must exist in the repository it is claimed against, and a
    verdict must describe the SAME commit the row records. Every absent field carries a
    `none:<reason>` — "pending" must never be readable as "passed".
    """
    plan = build_spec_plan(spec_root, plan_id)
    resolved_root = None
    rows: list = []
    for entry in plan["tasks"]:
        task_id = entry["task"]
        row = _board_readback(boardctl, board, task_id)
        if row is None:
            rows.append(
                {
                    "task": task_id,
                    "board_status": "absent",
                    "commit": "none:not-on-board",
                    "guard": "none:not-on-board",
                    "ci": "none:not-on-board",
                    "verdict": "none:not-judged",
                    "witness": "boardctl:show",
                    "plan_digest": plan["digest"],
                }
            )
            continue
        commit = str(row.get("commit_hash") or "")
        if commit:
            if resolved_root is None:
                resolved_root = _reconcile_repository_root(spec_root, repo_root)
            if not _git_object_exists(resolved_root, commit):
                raise SpecError(
                    "unresolved-commit",
                    f"{task_id}: the canonical row records commit {commit}, which does not exist "
                    f"in {resolved_root} — a commit that cannot be resolved is not evidence",
                )
        verdict = "none:not-judged"
        if verdict_root is not None:
            path = Path(verdict_root) / f"{task_id}.json"
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise SpecError(
                        "invalid-verdict-record", f"{path}: not JSON ({exc})"
                    ) from None
                if not isinstance(payload, dict) or not payload.get("status"):
                    raise SpecError(
                        "invalid-verdict-record",
                        f"{path}: a verdict record needs a `status` (pass|fail|...)",
                    )
                verdict = str(payload["status"])
                verdict_commit = str(payload.get("commit") or "")
                if verdict_commit and commit and verdict_commit != commit:
                    raise SpecError(
                        "verdict-commit-mismatch",
                        f"{task_id}: the verdict names commit {verdict_commit} but the board row "
                        f"records {commit} — the verdict is about a different tree",
                    )
        rows.append(
            {
                "task": task_id,
                "board_status": str(row.get("status") or ""),
                "commit": commit or "none:not-recorded",
                "guard": str(row.get("guard_result") or "") or "none:not-recorded",
                "ci": str(row.get("ci_result") or "") or "none:not-recorded",
                "verdict": verdict,
                "worker_summary": str(row.get("worker_summary") or "")
                or "none:not-recorded",
                "witness": "boardctl:show",
                "plan_digest": plan["digest"],
            }
        )
    record = {
        "plan": plan["plan"],
        "version": plan["version"],
        "digest": plan["digest"],
        "rows": rows,
        "counts": {
            "tasks": len(rows),
            "complete": sum(1 for row in rows if row["board_status"] == "complete"),
            "pending": sum(1 for row in rows if row["board_status"] == "pending"),
            "absent": sum(1 for row in rows if row["board_status"] == "absent"),
        },
        "writes": "none",
        "witness": "boardctl:show",
    }
    if out is not None:
        _spec_write_atomic(
            out, json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
    return record


# ---------------------------------------------------------------- the GitReins packet (AUG-124)
# The consumer is `engine/auger_packet.py` in the GitReins repository, and it fails CLOSED: its
# `TaskPacketAdapter._unresolved` names every reference it cannot resolve and a packet that fails
# there never reaches a judge. `validate_judge_packet` is the mirror of that rule set, so the same
# matrix the live probe runs through the REAL adapter is also checked on every gate run — the
# probe proves compatibility today, the mirror keeps it true tomorrow.
SPEC_PACKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
SPEC_PACKET_SELECTOR_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$"
)


def _packet_ref_status_ok(ref: dict) -> bool:
    return str(ref.get("status") or "").lower() in SPEC_PACKET_ACTIVE_STATES


def _packet_test_ref_ok(ref: dict, repo_root) -> bool:
    ref_id = str(ref.get("id") or "")
    path = str(ref.get("path") or "")
    selector = str(ref.get("selector") or "")
    runner = ref.get("runner")
    valid_runner = runner == "pytest" or (
        isinstance(runner, list)
        and len(runner) >= 2
        and runner[-2:] == ["-m", "pytest"]
    )
    if not valid_runner:
        return False
    if not SPEC_PACKET_ID_RE.match(ref_id) or not SPEC_PACKET_SELECTOR_RE.match(
        selector
    ):
        return False
    if not path or os.path.isabs(path) or ".." in path.split(os.sep):
        return False
    if repo_root is None:
        return True
    return _git_tracks(repo_root, path)


def validate_judge_packet(
    packet: dict, board_task: dict | None = None, *, repo_root=None
) -> list:
    """The unresolved ids a GitReins judge would refuse, in the consumer's own vocabulary.

    Empty means "the adapter's `_unresolved` should find nothing"; non-empty is the same id list its
    `PacketValidationError` would carry. `repo_root` enables the tracked-path half: without a
    repository a test path can only be checked for SHAPE, so the handoff always passes one.
    """
    unresolved: list = []
    board_id = (board_task or {}).get("id") if isinstance(board_task, dict) else None
    if isinstance(board_task, dict) and "acceptance_criteria" not in board_task:
        unresolved.append(f"board:{board_id or 'unknown'}:acceptance_criteria")
    packet_board_id = packet.get("board_id")
    if not isinstance(packet_board_id, str) or not packet_board_id:
        unresolved.append("board:id")
    mapping = packet.get("id_mapping")
    mapped_board_id = mapping.get("board_id") if isinstance(mapping, dict) else None
    if board_id and packet_board_id != board_id and mapped_board_id != board_id:
        unresolved.extend([str(board_id), str(packet_board_id or "packet:board_id")])

    trace = packet.get("ch_trace")
    if not isinstance(trace, dict):
        unresolved.append("ch:trace")
        trace = {}
    if board_id and trace.get("row") != board_id:
        unresolved.append(str(trace.get("row") or f"ch:trace:row:{board_id}"))

    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    in_scope = [str(item) for item in (scope.get("in_scope") or []) if item]
    out_of_scope = {str(item) for item in (scope.get("out_of_scope") or []) if item}
    if not in_scope:
        unresolved.append("scope:in_scope")

    spec_blocks: dict = {}
    raw_blocks = (
        packet.get("spec_blocks") if isinstance(packet.get("spec_blocks"), list) else []
    )
    for item in raw_blocks:
        if not isinstance(item, dict) or not item.get("id"):
            unresolved.append("packet:reference:id")
            continue
        if str(item["id"]) in spec_blocks:
            unresolved.append(str(item["id"]))
            continue
        spec_blocks[str(item["id"])] = item
    test_refs: dict = {}
    raw_tests = (
        packet.get("test_refs") if isinstance(packet.get("test_refs"), list) else []
    )
    for item in raw_tests:
        if not isinstance(item, dict) or not item.get("id"):
            unresolved.append("packet:reference:id")
            continue
        if str(item["id"]) in test_refs:
            unresolved.append(str(item["id"]))
            continue
        test_refs[str(item["id"])] = item

    for ref_id in in_scope:
        if ref_id in out_of_scope or ref_id not in spec_blocks:
            unresolved.append(ref_id)
            continue
        if not _packet_ref_status_ok(spec_blocks[ref_id]):
            unresolved.append(ref_id)
        if not spec_blocks[ref_id].get("digest"):
            unresolved.append(ref_id)

    trace_specs = [str(item) for item in (trace.get("spec") or []) if item]
    if isinstance(trace.get("spec"), str):
        trace_specs = [trace["spec"]]
    if not trace_specs:
        unresolved.append("ch:trace:spec")
    for ref_id in trace_specs:
        if (
            ref_id not in in_scope
            or ref_id in out_of_scope
            or ref_id not in spec_blocks
        ):
            unresolved.append(ref_id)

    trace_tests = [str(item) for item in (trace.get("test") or []) if item]
    if isinstance(trace.get("test"), str):
        trace_tests = [trace["test"]]
    if test_refs and not trace_tests:
        unresolved.append("ch:trace:test")
    for ref_id in trace_tests:
        if ref_id in out_of_scope or ref_id not in test_refs:
            unresolved.append(ref_id)

    for ref_id, ref in test_refs.items():
        if not _packet_ref_status_ok(ref):
            unresolved.append(ref_id)
        if not ref.get("digest"):
            unresolved.append(ref_id)
        if not _packet_test_ref_ok(ref, repo_root):
            unresolved.append(ref_id)

    if not isinstance(packet.get("digests"), dict) or not packet.get("digests"):
        unresolved.append("packet:digests")
    return list(dict.fromkeys(unresolved))


def _row_trace_marker(reasoning: str) -> str:
    """The `ch:trace` line a canonical row carries, or a refusal (a row without one is unbound)."""
    for line in (reasoning or "").splitlines():
        if SPEC_TRACE_MARKER_RE.search(line):
            return line.strip()
    raise SpecError(
        "missing-trace-marker",
        "the canonical row carries no `ch:trace` marker, so no row/spec/test binding can be "
        "proved — export the row from an approved plan before handing it to a judge",
    )


def _require_marker_binding(
    marker: str, entry: dict, row_id: str, blocks: list
) -> dict:
    """Validate a marker against the APPROVED task: one row, that task, its own obligations.

    Five refusals, each naming the offending value: the marker points at another row
    (`trace-row-mismatch`), names an id the corpus does not have (`unknown-spec-id`, raised by the
    resolver), names a block authored in another file (`stale-spec-ref`), names a block that is OUT
    of this task's scope (`trace-spec-out-of-scope`), or names a test that is not one of the task's
    obligations (`trace-test-not-obligation`). No judge starts on any of them.
    """
    fields = parse_trace_marker(marker)
    if fields["row"] != row_id:
        raise SpecError(
            "trace-row-mismatch",
            f"row={fields['row']} but the canonical board row is {row_id} — a marker points at "
            "exactly ONE row, and this one is not it",
        )
    resolved = resolve_trace_marker(marker, blocks)
    task_id = entry["task"]
    if resolved["blocks"] != [task_id]:
        out_scope = {item["id"] for item in entry["out_of_scope"]}
        stray = [block_id for block_id in resolved["blocks"] if block_id != task_id]
        if stray and all(block_id in out_scope for block_id in stray):
            raise SpecError(
                "trace-spec-out-of-scope",
                f"spec={','.join(stray)} names a block that is OUT of {task_id}'s approved scope — "
                "the handoff would judge a contract this task does not own",
            )
        raise SpecError(
            "trace-spec-not-task",
            f"spec={fields.get('spec')} resolves to {','.join(resolved['blocks']) or '<nothing>'}, "
            f"not to the approved task {task_id}",
        )
    targets = {
        f"{ref['path']}::{ref['selector']}"
        for ref in entry["test_refs"]
        if ref.get("selector")
    } | {ref["path"] for ref in entry["test_refs"]}
    value = str(fields.get("test") or "")
    if value.startswith("none:"):
        if targets:
            raise SpecError(
                "trace-test-missing",
                f"{task_id} has approved test obligations ({', '.join(sorted(targets))}) but the "
                f"marker says test={value} — a marker that names no obligation proves nothing",
            )
    else:
        named = {token for token in value.split(",") if token}
        if not named <= targets:
            raise SpecError(
                "trace-test-not-obligation",
                f"test={value} names no obligation of {task_id} (approved obligations: "
                f"{', '.join(sorted(targets)) or 'none'})",
            )
    return fields


def build_judge_packet(
    spec_root,
    plan_id: str,
    row_id: str,
    *,
    board,
    ledger=None,
    boardctl=None,
    repo_root=None,
    trace=None,
    worker_summary=None,
) -> dict:
    """Assemble the packet `engine.auger_packet.TaskPacketAdapter` accepts, from the APPROVED plan.

    Nothing here re-derives scope from the raw XML at handoff time: the plan is rebuilt only to be
    checked against the approval (a changed corpus is `stale-plan-digest`), and every scope ref,
    obligation and digest the packet carries comes out of that approved plan. The canonical row is
    re-read, and it must still carry the approved packet and the approved acceptance criteria — a
    row edited after export is refused, not blessed.
    """
    loaded = load_spec_corpus(spec_root)
    plan = build_spec_plan(spec_root, plan_id, loaded=loaded)
    blocks = loaded[2]
    require_plan_approval(plan, ledger, spec_root=spec_root)
    entry = next(
        (item for item in plan["tasks"] if item["task"] == row_id),
        None,
    )
    if entry is None:
        raise SpecError(
            "unknown-plan-task",
            f"{row_id} is not a task in plan {plan['plan']} (its tasks: "
            f"{', '.join(item['task'] for item in plan['tasks'])})",
        )
    row = _board_readback(boardctl, board, row_id)
    if row is None:
        raise SpecError(
            "board-row-missing",
            f"{row_id} has no canonical row on {board} — a judge packet binds to an APPROVED "
            "board task, so export the plan first",
        )
    canonical = json.dumps(
        entry["packet"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if canonical not in (row.get("reasoning") or ""):
        raise SpecError(
            "board-row-not-approved-packet",
            f"{row_id}: the canonical row does not carry the approved packet byte for byte — the "
            "row was written by another plan or edited after export",
        )
    expected_criteria = "\n".join(
        f"{item['id']}: {item['text']}" for item in entry["acceptance"]
    )
    if (row.get("acceptance_criteria") or "") != expected_criteria:
        raise SpecError(
            "board-acceptance-drift",
            f"{row_id}: the row's acceptance criteria differ from the approved criteria "
            f"(expected {expected_criteria!r})",
        )

    marker = _row_trace_marker(row.get("reasoning") or "") if trace is None else trace
    _require_marker_binding(marker, entry, row_id, blocks)

    packet = {
        "packet_version": SPEC_PACKET_VERSION,
        "board_id": row_id,
        "id_mapping": {
            "board_id": row_id,
            "gitreins_task_id": row_id,
            "auger_task_id": entry["task"],
        },
        "task": entry["task"],
        "plan": {
            "plan": plan["plan"],
            "version": plan["version"],
            "digest": plan["digest"],
        },
        # The consumer indexes these by ID, so the trace carries ids — the row marker's path-shaped
        # `test=` value is resolved to the obligation id HERE, once, instead of by the judge.
        "ch_trace": {
            "row": row_id,
            "spec": [item["id"] for item in entry["in_scope"]],
            "test": [item["id"] for item in entry["test_refs"]],
        },
        "scope": {
            "in_scope": [item["id"] for item in entry["in_scope"]],
            "out_of_scope": [item["id"] for item in entry["out_of_scope"]],
        },
        "acceptance_criteria": row.get("acceptance_criteria") or "",
        "board_acceptance_criteria": row.get("acceptance_criteria") or "",
        "acceptance": [
            {"id": item["id"], "text": item["text"]} for item in entry["acceptance"]
        ],
        "spec_blocks": copy.deepcopy(entry["spec_blocks"]),
        "test_refs": copy.deepcopy(entry["test_refs"]),
        "digests": {
            "plan": plan["digest"],
            "packet": entry["packet_digest"],
            **{
                ref["id"]: ref["digest"]
                for ref in [*entry["spec_blocks"], *entry["test_refs"]]
            },
        },
        "sources": dict(plan["sources"]),
        "worker_summary": worker_summary,
    }
    if repo_root is not None:
        for ref in packet["test_refs"]:
            if not _git_tracks(repo_root, ref["path"]):
                raise SpecError(
                    "untracked-test-ref",
                    f"{entry['task']}: the obligation {ref['id']} names {ref['path']}, which is "
                    f"not tracked in {repo_root} — a judge cannot run a path `git ls-files` does "
                    "not resolve, so the packet is refused here rather than failed closed there",
                )
    unresolved = validate_judge_packet(packet, row, repo_root=repo_root)
    if unresolved:
        raise SpecError(
            "packet-invalid",
            f"{row_id}: unresolved Auger packet references: {', '.join(unresolved)}",
        )
    return packet


def judge_packet_verdict(packet: dict, results, *, worker_summary=None) -> dict:
    """The verdict a judge would reach from the packet's obligations and their RUN results.

    A worker summary is context, never authority: it is copied out with `trust: untrusted` and has
    no path into the verdict, so a summary claiming green cannot turn a red obligation, or a missing
    one, into a pass. Every non-pass carries the obligation id AND the reason it was not proved.
    """
    by_id: dict = {}
    for result in results or []:
        if isinstance(result, dict) and result.get("id"):
            by_id[str(result["id"])] = result
    citations: list = []
    for ref in packet.get("test_refs") or []:
        ref_id = str(ref["id"])
        target = f"{ref['path']}::{ref.get('selector', '')}"
        result = by_id.get(ref_id)
        if result is None:
            citations.append(
                {
                    "id": ref_id,
                    "reason": "missing-test-obligation",
                    "target": target,
                }
            )
            continue
        if not result.get("passed"):
            citations.append(
                {
                    "id": ref_id,
                    "reason": "failed-test-obligation",
                    "target": target,
                    "exit_code": result.get("exit_code"),
                }
            )
    if not (packet.get("test_refs") or []):
        citations.append(
            {
                "id": "packet:test_refs",
                "reason": "no-test-obligation",
                "target": "",
            }
        )
    return {
        "status": "fail" if citations else "pass",
        "citations": citations,
        "obligations": [str(ref["id"]) for ref in packet.get("test_refs") or []],
        "test_results": [
            dict(result) for result in (results or []) if isinstance(result, dict)
        ],
        "worker_summary": {
            "value": worker_summary,
            "trust": "untrusted",
            "authoritative": False,
        },
    }


def judge_packet_path(
    ledger, plan_id: str, row_id: str, *, spec_root=None, out=None
) -> Path:
    """Where a handoff's packet is written: `--out`, else `<ledger>/judge-packets/<plan>-<row>.json`."""
    if out is not None:
        return Path(out).expanduser()
    directory = plan_ledger_dir(ledger, spec_root) / "judge-packets"
    return directory / f"{plan_id}-{row_id}.json"


def write_judge_packet(
    packet: dict, *, ledger, plan_id: str, row_id: str, spec_root=None, out=None
) -> Path:
    """Write the packet atomically and return its path (deterministic bytes: repeat = identical)."""
    path = judge_packet_path(ledger, plan_id, row_id, spec_root=spec_root, out=out)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "packet": packet,
        "digest": packet["digests"]["plan"],
        "board_id": packet["board_id"],
        "acceptance_criteria": packet["acceptance_criteria"],
    }
    _spec_write_atomic(
        path, json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return path


def start_judge_handoff(
    spec_root,
    plan_id: str,
    row_id: str,
    *,
    board,
    ledger=None,
    boardctl=None,
    repo_root=None,
    trace=None,
    dispatch=None,
    out=None,
    worker_summary=None,
) -> dict:
    """Hand an APPROVED board row to the GitReins lifecycle — or refuse and start nothing.

    Order is the contract, and it is the whole of AC-19: the packet (and therefore the row, the
    spec binding and every obligation) is validated FIRST; only then is the dispatch seam called,
    exactly once, with the board acceptance criteria verbatim. `dispatch=None` refuses — Auger never
    invents a judge invocation, and a caller that has not wired one gets a named refusal instead of
    a silent no-op.
    """
    packet = build_judge_packet(
        spec_root,
        plan_id,
        row_id,
        board=board,
        ledger=ledger,
        boardctl=boardctl,
        repo_root=repo_root,
        trace=trace,
        worker_summary=worker_summary,
    )
    if dispatch is None:
        raise SpecError(
            "dispatch-unconfigured",
            f"{row_id}: no judge dispatch is configured; pass a lifecycle entry point (or "
            "`--dispatch-cmd <command>`) — a handoff that silently starts nothing is worse than a "
            "handoff that refuses",
        )
    row = _board_readback(boardctl, board, row_id)
    path = write_judge_packet(
        packet,
        ledger=ledger,
        plan_id=plan_id,
        row_id=row_id,
        spec_root=spec_root,
        out=out,
    )
    context = {
        "packet": packet,
        "board_task": row,
        "board_id": row_id,
        "acceptance_criteria": packet["acceptance_criteria"],
        "packet_path": str(path),
    }
    handed = dispatch(context)
    return {
        "status": "dispatched",
        "row": row_id,
        "invocations": 1,
        "digest": packet["digests"]["plan"],
        "packet_digest": packet["digests"]["packet"],
        "acceptance_criteria": packet["acceptance_criteria"],
        "packet_path": str(path),
        "dispatch": handed if isinstance(handed, dict) else {"result": handed},
    }


def run_dispatch_command(command, packet_path) -> dict:
    """Run an operator-supplied judge command with the packet path appended (the CLI's seam)."""
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise SpecError("invalid-dispatch-command", f"{command!r}: {exc}") from None
    if not words:
        raise SpecError(
            "invalid-dispatch-command", "an empty dispatch command names nothing"
        )
    done = subprocess.run(
        [*words, str(packet_path)], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise SpecError(
            "dispatch-refused",
            f"the dispatch command exited {done.returncode}: {(done.stderr or done.stdout).strip()[:400]}",
        )
    return {
        "command": [*words, str(packet_path)],
        "exit_code": done.returncode,
        "stdout": done.stdout.strip()[:2000],
    }


def cmd_spec_plan_draft(a) -> int:
    """`spec plan-draft`: the versioned draft plan, its task list and its digest."""
    plan = build_spec_plan(a.root, a.plan)
    print(json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def cmd_spec_plan_approve(a) -> int:
    """`spec plan-approve`: record an approval (or a rejection) bound to version + digest."""
    plan = build_spec_plan(a.root, a.plan)
    decision = "approved" if a.approve else "rejected"
    record = record_plan_approval(
        a.ledger,
        plan,
        digest=a.digest,
        decision=decision,
        approver=a.approver,
        at=a.at or "",
        spec_root=a.root,
    )
    print(
        json.dumps({"status": "recorded", "record": record}, indent=2, sort_keys=True)
    )
    return 0


def cmd_spec_plan_export(a) -> int:
    """`spec plan-export`: write every approved row; exit 1 when the run is PARTIAL."""
    result = export_spec_plan(
        a.root,
        a.plan,
        board=a.board,
        ledger=a.ledger,
        boardctl=a.boardctl,
        declared=getattr(a, "plan_digest", None),
        out=getattr(a, "out", None),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if result["status"] == "partial" else 0


def cmd_spec_plan_reconcile(a) -> int:
    """`spec plan-reconcile`: copy board evidence into Auger; never write the board."""
    record = reconcile_spec_plan(
        a.root,
        a.plan,
        board=a.board,
        boardctl=a.boardctl,
        repo_root=getattr(a, "repo_root", None),
        verdict_root=getattr(a, "verdict_root", None),
        out=getattr(a, "out", None),
    )
    print(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def cmd_spec_judge_packet(a) -> int:
    """`spec judge-packet`: the GitReins packet for one approved row, its digest and its path."""
    repo_root = getattr(a, "repo_root", None) or None
    resolved = _reconcile_repository_root(a.root, repo_root) if repo_root else None
    packet = build_judge_packet(
        a.root,
        a.plan,
        a.row,
        board=a.board,
        ledger=a.ledger,
        boardctl=a.boardctl,
        repo_root=resolved,
        trace=getattr(a, "trace", None),
        worker_summary=getattr(a, "worker_summary", None),
    )
    path = write_judge_packet(
        packet,
        ledger=a.ledger,
        plan_id=a.plan,
        row_id=a.row,
        spec_root=a.root,
        out=getattr(a, "out", None),
    )
    print(
        json.dumps(
            {
                "packet": packet,
                "digest": packet["digests"]["plan"],
                "path": str(path),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_spec_judge_handoff(a) -> int:
    """`spec judge-handoff`: validate the binding, then start the lifecycle ONCE (or refuse)."""
    repo_root = getattr(a, "repo_root", None) or None
    resolved = _reconcile_repository_root(a.root, repo_root) if repo_root else None
    dispatch = None
    command = getattr(a, "dispatch_cmd", None)
    if command:
        dispatch = lambda context: run_dispatch_command(  # noqa: E731 - one-line seam
            command, context["packet_path"]
        )
    result = start_judge_handoff(
        a.root,
        a.plan,
        a.row,
        board=a.board,
        ledger=a.ledger,
        boardctl=a.boardctl,
        repo_root=resolved,
        trace=getattr(a, "trace", None),
        dispatch=dispatch,
        out=getattr(a, "out", None),
        worker_summary=getattr(a, "worker_summary", None),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- one-query context (AUG-122)
#: A `spec=` token inside a marker line: any non-space value, so both the bare block id and the
#: `file.xml#ID` source form are read. `SPEC_TRACE_ANNOTATION_RE` only matches the bare-id form,
#: which would silently drop every source-shaped citation.
SPEC_CONTEXT_SPEC_TOKEN_RE = re.compile(r"\bspec=([^\s]+)")


def _context_rel(path: Path, base: Path) -> str:
    """A path relative to `base` when it is inside it — an absolute tmp path in the response would
    make the golden non-reproducible and would leak the caller's layout."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def _context_entry(block: dict, edge: str) -> dict:
    """One selected block: everything a reader needs to act without opening the corpus."""
    return {
        "id": block["id"],
        "kind": block["kind"],
        "status": block["status"],
        "version": block["version"],
        "applies_to": list(block["applies_to"]),
        "method": block["method"],
        "source": block["source"],
        "edge": edge,
    }


def _context_selection(blocks: list, ids, edges: dict) -> list:
    """Expand the explicit closure for `ids`, keeping the edge that put each block in the bundle.

    `verifies` is followed FORWARD as well as inversely here: a requirement's declared verification
    plan is part of the contract the task is judged against (PRD R15), not a reverse-only edge, so
    leaving it out would hand an agent the requirement and hide the test that proves it.
    """
    seeds = [block_id for block_id in ids if block_id]
    if not seeds:
        return []
    _selection, records = select_spec_blocks(
        blocks,
        ids=seeds,
        closure=True,
        forward_edges=("depends-on", "verifies"),
    )
    entries = []
    for record in records:
        block_id = record["block"]["id"]
        if record["why"] == "matched":
            edge = edges.get(block_id, "matched")
        else:
            via = record["via"] or ""
            edge = via.split()[-1] if via else "dependency"
        entries.append(_context_entry(record["block"], edge))
    return entries


def _context_trace_refs(text: str) -> tuple:
    """(marker count, `spec=` values) for every ch:trace line in a source file, in file order.

    A line counts as a marker only when it carries `row=` — the doctrine's one mandatory field. That
    is not pedantry: this parser reads WHOLE FILES, so a docstring that merely mentions `ch:trace
    spec=` (as auger.py's own does) would otherwise contribute a phantom `spec=` value and turn a
    real query into an `unknown-spec-id` refusal.
    """
    markers = 0
    refs: list = []
    for line in text.splitlines():
        if not SPEC_TRACE_MARKER_RE.search(line):
            continue
        if not re.search(r"\brow=\S", line):
            continue
        markers += 1
        for match in SPEC_CONTEXT_SPEC_TOKEN_RE.finditer(line):
            for value in match.group(1).split(","):
                if value:
                    refs.append(value)
    return markers, refs


def _context_block_id(value: str, by_id: dict):
    """The block a `spec=` value names: a bare id, or `file.xml#ID` resolved to its source file."""
    file_part, separator, fragment = value.partition("#")
    block_id = fragment if separator else value
    block = by_id.get(block_id)
    if block is None:
        return None
    if separator and block["source_file"] != file_part:
        return None
    return block_id


def _context_is_block_id(value: str) -> bool:
    """Does this `spec=` value CLAIM to be a block id (as opposed to a doc/path citation)?

    The distinction matters for the diagnostic: a citation like `docs/SPEC-003.md#assembly` is a
    legitimate trace that names no XML block (`spec-names-no-block`), while `AUG-REQ-404` claims an
    id the corpus does not have (`unknown-spec-id`) — a different defect and a different fix.
    """
    fragment = value.partition("#")[2] or value
    return SPEC_ID_RE.match(fragment) is not None


def _context_source_file(raw: str, roots) -> Path | None:
    """Resolve `--code` to a real file: absolute as given, relative against the stated roots."""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve() if candidate.is_file() else None
    for base in roots:
        path = Path(base) / candidate
        if path.is_file():
            return path.resolve()
    return None


def _context_symbol_files(search_root: Path, symbol: str) -> list:
    """Every `*.py` under `search_root` that DEFINES `symbol` (exact name, never a substring).

    A substring match would make the symbol query useless on a real tree, where `export_row` and
    `other_export_row` sit beside each other; the definition is read from the AST, so a mention in a
    comment or a string is not a definition.
    """
    import ast

    hits: list = []
    for path in sorted(search_root.rglob("*.py")):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        names.update(
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        )
        if symbol in names:
            hits.append(path)
    return hits


def _context_corpus_bytes(root: Path, documents: dict) -> int:
    """The bytes of the documents this query actually read (measured, not estimated)."""
    total = 0
    for rel in documents:
        try:
            total += (root / rel).stat().st_size
        except OSError:  # pragma: no cover - the loader already read the file
            continue
    return total


def build_spec_context(
    spec_root,
    *,
    task=None,
    code=None,
    symbol=None,
    search_root=None,
    repo_root=None,
) -> dict:
    """The AUG-122 answer, as data: what governs this task / this code location, and what does not.

    Exactly one selector may be given. The result is a dict with a closed `status`
    (`ok|inactive|unscoped|ambiguous`), a `reason` whenever the status is not `ok`, the selected
    blocks with their provenance and edge, the explicit exclusions with reasons, and measured
    counts. There is no branch that returns the corpus: an unscoped query returns an empty
    selection and a named reason.
    """
    selectors = [
        name
        for name, value in (("task", task), ("code", code), ("symbol", symbol))
        if value
    ]
    if len(selectors) != 1:
        raise SpecError(
            "context-selector",
            "pass exactly one of --task/--code/--symbol (a context query is never a whole-corpus "
            "dump)",
        )
    root, documents, blocks = load_spec_corpus(
        spec_root, tolerate_unreadable=True, skipped=[]
    )
    by_id = {block["id"]: block for block in blocks}
    corpus_root = Path(spec_root).expanduser().resolve()
    input_bytes = _context_corpus_bytes(corpus_root, documents)
    payload = {
        "query": {"kind": selectors[0], "value": str(task or code or symbol)},
        "status": "ok",
        "reason": "",
        "task": None,
        "selected": [],
        "excluded": [],
        "candidates": [],
        "scan": {"files": [], "spec_refs": [], "traces": 0},
        "counts": {"selected": 0, "dependencies": 0, "input_bytes": input_bytes},
    }

    if task is not None:
        block = by_id.get(task)
        if block is None or block["kind"] != "task":
            payload["status"] = "unscoped"
            payload["reason"] = "unknown-task"
            return payload
        packet = task_packet(blocks, task)
        entry = _context_entry(block, "task")
        payload["task"] = {**entry, "prose": block["text"]}
        edges = {
            **{item["id"]: "in-scope" for item in packet["in_scope"]},
            **{item["id"]: "acceptance" for item in packet["acceptance"]},
        }
        seeds = [entry["id"] for entry in packet["in_scope"] + packet["acceptance"]]
        payload["selected"] = [entry, *_context_selection(blocks, seeds, edges)]
        payload["excluded"] = [
            {
                "id": item["id"],
                "status": item["status"],
                "version": item["version"],
                "source": item["source"],
                "reason": item["reason"],
            }
            for item in packet["out_of_scope"]
        ]
        if block["status"] != "active":
            payload["status"] = "inactive"
            payload["reason"] = "block-inactive"
    else:
        roots = [Path(repo_root)] if repo_root else [corpus_root, Path.cwd()]
        files: list = []
        if code is not None:
            path = _context_source_file(code, roots)
            if path is not None:
                files = [path]
        else:
            search = Path(search_root) if search_root else roots[0]
            files = _context_symbol_files(search, symbol)
            if not files:
                payload["status"] = "unscoped"
                payload["reason"] = "unknown-symbol"
                return payload
        if not files:
            payload["status"] = "unscoped"
            payload["reason"] = "path-not-found"
            return payload
        base = (
            Path(search_root)
            if (symbol is not None and search_root)
            else (Path(repo_root) if repo_root else Path.cwd())
        )
        payload["scan"]["files"] = [_context_rel(path, base) for path in files]
        for path in files:
            try:
                input_bytes += path.stat().st_size
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            markers, refs = _context_trace_refs(text)
            payload["scan"]["traces"] += markers
            payload["scan"]["spec_refs"].extend(
                value for value in refs if value not in payload["scan"]["spec_refs"]
            )
        payload["counts"]["input_bytes"] = input_bytes
        if payload["scan"]["traces"] == 0:
            payload["status"] = "unscoped"
            payload["reason"] = "no-trace"
            return payload
        if not payload["scan"]["spec_refs"]:
            payload["status"] = "unscoped"
            payload["reason"] = "trace-without-spec"
            return payload
        resolved: dict = {}
        unknown_ids: list = []
        no_block: list = []
        for value in payload["scan"]["spec_refs"]:
            block_id = _context_block_id(value, by_id)
            if block_id is not None:
                resolved[value] = block_id
            elif _context_is_block_id(value):
                unknown_ids.append(value)
            else:
                no_block.append(value)
        if unknown_ids:
            payload["status"] = "unscoped"
            payload["reason"] = "unknown-spec-id"
            payload["candidates"] = sorted(set(unknown_ids))
            return payload
        if not resolved:
            # A trace that cites only documents/paths names no XML block. That is an explicit
            # `unscoped` answer, not a broken lookup and above all not a corpus dump.
            payload["status"] = "unscoped"
            payload["reason"] = "spec-names-no-block"
            payload["scan"]["unresolved"] = no_block
            return payload
        unique = sorted(set(resolved.values()))
        if len(unique) > 1:
            payload["status"] = "ambiguous"
            payload["reason"] = "ambiguous-match"
            payload["candidates"] = unique
            return payload
        block = by_id[unique[0]]
        payload["selected"] = _context_selection(
            blocks, [block["id"]], {block["id"]: "spec"}
        )
        if block["status"] != "active":
            payload["status"] = "inactive"
            payload["reason"] = "block-inactive"

    dependencies = sum(
        1
        for entry in payload["selected"]
        if entry["edge"] not in ("task", "in-scope", "acceptance")
    )
    payload["counts"].update(
        {"selected": len(payload["selected"]), "dependencies": dependencies}
    )
    return payload


def render_spec_context(payload: dict) -> str:
    """Serialize a context answer with a MEASURED `output_bytes` — proved against the real bytes.

    `output_bytes` is self-referential (the value is inside the document it measures), so it is
    solved as a FIXED POINT: render with a candidate, measure, re-render until the measurement and
    the value agree. It converges in a couple of iterations because the serialization is stable, and
    the result is exact rather than approximate — `counts.output_bytes` equals the length of the
    bytes this function returns, which is what makes the number checkable at all.
    """
    counts = dict(payload.get("counts") or {})
    value = 0
    text = ""
    for _attempt in range(8):
        counts["output_bytes"] = value
        text = (
            json.dumps(
                {**payload, "counts": counts},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )
        measured = len(text.encode("utf-8"))
        if measured == value:
            return text
        value = measured
    return text


def cmd_spec_task_packet(a) -> int:
    """`spec task-packet`: the exact packet, its approved digest, and the generated marker."""
    _root, _documents, blocks = load_spec_corpus(a.root)
    packet = task_packet(blocks, a.task)
    print(
        json.dumps(
            {
                "packet": packet,
                "digest": task_packet_digest(packet),
                "trace": render_task_trace_marker(
                    packet, test=_task_obligation_test(blocks, packet)
                ),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_spec_task_export(a) -> int:
    """`spec task-export`: write ONE approved packet to the canonical board through boardctl."""
    result = export_task_packet(
        a.root,
        a.task,
        board=a.board,
        plan_digest=a.plan_digest,
        boardctl=a.boardctl,
        repo_root=getattr(a, "repo_root", None),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def cmd_spec_context(a) -> int:
    """`spec context`: one query, minimal context, measured bytes, no writes."""
    payload = build_spec_context(
        a.root,
        task=a.task,
        code=a.code,
        symbol=a.symbol,
        search_root=getattr(a, "search_root", None),
        repo_root=getattr(a, "repo_root", None),
    )
    sys.stdout.write(render_spec_context(payload))
    return 0


# ----------------------------- staged-diff spec alignment and ch:trace check ---------------------
# AUG-120 / AUG-135: the checker GitReins already invokes.
#
# GitReins' half of this seam is BUILT and shipped (engine.spec_alignment.run_checker): it appends
# `--repo-root <workdir> --spec-root <root> --protocol-version <n> --diff-stdin`, feeds the exact
# staged patch, and reads the LAST JSON LINE of stdout, which must carry `protocol`, `version` and
# `status` in `pass|warn|fail|block`. Measured consequences, each encoded below:
#
#   * the payload is ONE line. The adapter `json.loads`es each stdout line separately and keeps the
#     last dict it can parse, so a pretty-printed object parses as nothing;
#   * `version` is the CHECKER PROTOCOL version, never AUGER_VERSION: GitReins compares it against
#     `min_checker_version` (default 1.0.0) and this product's own version is 0.3, which the
#     handshake refuses ("checker version 0.3 is below minimum 1.0.0");
#   * a protocol version this checker does not speak is a TYPED refusal on stderr with exit 2 and no
#     payload at all — a caller must never be able to read a verdict out of a contract we did not
#     agree to speak;
#   * the run is bounded: one capped stdin read, no subprocess chain, no thread, so a refusal cannot
#     leave a process behind that outlives the hook's decision.
#
# The judgement is STRUCTURAL and citeable, never semantic. It reports which corpus blocks scope a
# changed path, whether the change carries a ch:trace marker, whether every value that marker needed
# is present with an explicit `none:<reason>` when absent, whether its pointers resolve in THIS
# repository, and whether it cites a section the corpus retired. "This comment says X, the prose says
# not-X" is not mechanically decidable and this checker does not claim it.
SPEC_CHECK_PROTOCOL = "1"
SPEC_CHECK_PROTOCOLS = ("1",)
SPEC_CHECK_VERSION = "1.0.0"
SPEC_CHECK_MAX_DIFF_BYTES = 8 * 1024 * 1024
# The eleven-field closeout marker (PRD R10, docs/prd/xml-spec-graph.md), and the six fields the
# doctrine names at minimum: `row` is mandatory and can never be absent; the other five must be
# PRESENT, carrying a value or an explicit `none:<reason>`.
SPEC_TRACE_FIELDS = (
    "row",
    "spec",
    "wave",
    "test",
    "doc",
    "prompt",
    "evidence",
    "witness",
    "verdict",
    "commit",
    "memory",
)
SPEC_TRACE_MANDATORY = ("row",)
SPEC_TRACE_CONTRACT = ("row", "spec", "test", "evidence", "verdict", "commit")
SPEC_TRACE_MARKER_RE = re.compile(r"ch:trace\b(.*)$")
# `spec=<ID>` written anywhere else — a comment, a docstring, a doc line. The id shape is the
# corpus' own, so this catches an annotation that names a block the corpus does not have.
SPEC_TRACE_ANNOTATION_RE = re.compile(r"\bspec=([A-Z][A-Z0-9]*(-[A-Z0-9]+)+)\b")
# Explicit absence must carry its reason (`none:pending-judge`), never a bare `none`.
SPEC_TRACE_NONE_RE = re.compile(r"^none:\S")
SPEC_TRACE_KEY_RE = re.compile(r"[a-z][a-z0-9_-]*")


def _spec_check_path(raw: str) -> str:
    """A diff header path as a repository-relative path (`b/x` -> `x`, `/dev/null` -> '')."""
    value = (raw or "").strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            value = value[1:-1].encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            value = value[1:-1]
    if value == "/dev/null":
        return ""
    return value[2:] if value.startswith(("a/", "b/")) else value


def _spec_check_diff(diff_text: str) -> dict:
    """Parse a unified diff into per-file added/removed lines WITH their new-file line numbers.

    Line numbers are the evidence a caller can act on, so they come from the hunk header instead of
    being counted from the top of the file. A file appears as soon as its `diff --git` header does,
    so a binary change (which has no `+++` line) is still a changed file rather than invisible.
    """
    files: dict = {}
    order: list = []
    current = ""
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            rest = raw[len("diff --git ") :]
            split = rest.find(" b/")
            head = _spec_check_path(rest[split + 1 :] if split != -1 else rest)
            current = head
            if current and current not in files:
                files[current] = {"added": [], "removed": [], "binary": False}
                order.append(current)
            in_hunk = False
            continue
        if raw.startswith("--- "):
            in_hunk = False
            continue
        if raw.startswith("+++ "):
            named = _spec_check_path(raw[4:])
            if named:
                current = named
            continue
        if raw.startswith("@@"):
            header = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            new_line = int(header.group(1)) if header else 0
            in_hunk = True
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            if current in files:
                files[current]["binary"] = True
            continue
        if not current or not in_hunk or current not in files:
            continue
        if raw.startswith("+"):
            files[current]["added"].append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            files[current]["removed"].append((new_line, raw[1:]))
        elif not raw.startswith("\\"):
            new_line += 1
    return {"files": files, "order": order}


def _spec_check_citations(documents: dict) -> dict:
    """Block id -> the repository paths its own attributes cite (path=/migration=/fixture=).

    Read from the parsed trees rather than from `_spec_block`'s record so the published block record
    (and the assembled JSON built from it) keeps exactly the shape it had.
    """
    citations: dict = {}
    for rel in sorted(documents):
        for element in documents[rel].iter():
            block_id = (element.get("id") or "").strip()
            if not block_id:
                continue
            for attribute in ("path", "migration", "fixture"):
                value = (element.get(attribute) or "").strip()
                if value:
                    citations.setdefault(block_id, set()).add(value)
    return citations


def _spec_check_covers(cited: str, changed: str) -> bool:
    """Does one citation cover a changed path (same file, or a directory prefix)?"""
    if cited == changed:
        return True
    return cited.endswith("/") and changed.startswith(cited)


def _spec_check_markers(diff: dict) -> list:
    """Every `ch:trace` marker among the ADDED lines, with its file and new-file line number."""
    markers: list = []
    for path in diff["order"]:
        for line_no, text in diff["files"][path]["added"]:
            match = SPEC_TRACE_MARKER_RE.search(text)
            if not match:
                continue
            fields: dict = {}
            unknown: list = []
            for token in match.group(1).split():
                if "=" not in token:
                    # Prose after the marker is not part of it: the marker is the leading run of
                    # key=value tokens, exactly as a reader (and a grep) sees it.
                    break
                key, _, value = token.partition("=")
                key = key.strip()
                if not SPEC_TRACE_KEY_RE.fullmatch(key):
                    break
                if key in SPEC_TRACE_FIELDS:
                    fields[key] = value.strip()
                else:
                    unknown.append((key, value.strip()))
            if not fields and not unknown:
                # The line MENTIONS the marker without claiming one ("the ch:trace marker must
                # carry row="). It is prose, not a malformed marker: calling it malformed turned
                # every explanatory comment into a contract violation.
                continue
            markers.append(
                {
                    "file": path,
                    "line": line_no,
                    "fields": fields,
                    "unknown": unknown,
                    "malformed": ""
                    if fields
                    else "the marker carries no ch:trace field, only unrecognized keys",
                }
            )
    return markers


def _spec_check_annotations(diff: dict) -> list:
    """`spec=<ID>` citations written outside a ch:trace marker (a comment, a doc line)."""
    found: list = []
    for path in diff["order"]:
        for line_no, text in diff["files"][path]["added"]:
            if SPEC_TRACE_MARKER_RE.search(text):
                continue  # the marker's own spec= is validated as a marker field
            for match in SPEC_TRACE_ANNOTATION_RE.finditer(text):
                found.append({"file": path, "line": line_no, "id": match.group(1)})
    return found


def _spec_check_slug(value: str) -> str:
    """A heading reduced to the shape an anchor uses: lowercase words joined by dashes."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _spec_check_anchor_matches(text: str, anchor: str) -> bool:
    """Does an anchor land in this document? A heading slug, or the token in the body."""
    wanted = _spec_check_slug(anchor)
    if not wanted:
        return False
    for line in text.splitlines():
        stripped = line.lstrip()
        if (
            stripped.startswith("#")
            and _spec_check_slug(stripped.lstrip("#")) == wanted
        ):
            return True
    return re.search(rf"\b{re.escape(anchor)}\b", text) is not None


def _spec_check_resolve_spec(value, blocks_by_id: dict, repo_root: Path) -> tuple:
    """Resolve one `spec=` value: (block id or None, refusal reason or '').

    Three shapes are accepted, and each has to LAND somewhere: a block id in this corpus, a
    repository path that exists, or a `path#anchor` whose anchor resolves inside that document.
    """
    if value in blocks_by_id:
        return value, ""
    if "#" in value:
        relative, _, anchor = value.partition("#")
        target = (repo_root / relative).resolve()
        if not target.is_relative_to(repo_root):
            return None, f"{value}: resolves outside the repository root"
        if not target.is_file():
            return None, f"{value}: {relative} does not exist in this repository"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return (
                None,
                f"{value}: {relative} could not be read ({exc.strerror or exc})",
            )
        if not _spec_check_anchor_matches(text, anchor):
            return None, f"{value}: {relative} carries no heading or token {anchor!r}"
        return None, ""
    if "/" in value or "." in value:
        target = (repo_root / value).resolve()
        if target.is_relative_to(repo_root) and target.is_file():
            return None, ""
        return None, f"{value}: no such path in this repository"
    return (
        None,
        f"{value}: neither a block id in this corpus nor a path in this repository",
    )


def _spec_check_resolve_test(value, repo_root: Path) -> tuple:
    """Resolve one `test=` value (`path` or `path::selector`): (ok, refusal reason)."""
    import ast

    relative, _, selector = value.partition("::")
    if not relative:
        return False, f"{value}: names no path"
    target = (repo_root / relative).resolve()
    if not target.is_relative_to(repo_root):
        return False, f"{value}: {relative} resolves outside the repository root"
    if not target.is_file():
        return False, f"{value}: {relative} does not exist in this repository"
    if not selector:
        return True, ""
    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except (SyntaxError, OSError) as exc:
        return False, f"{value}: {relative} could not be parsed ({exc})"
    wanted = selector.split("::")[-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == wanted:
                return True, ""
    return False, f"{value}: {relative} defines no {wanted!r}"


def _spec_check_commit_resolves(value: str, repo_root: Path) -> bool:
    """Is this commit an object in THIS repository (a verdict must not name a foreign commit)?"""
    probe = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{value}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _spec_check_board_rows(repo_root: Path) -> set:
    """Row ids from the repository's own JSONL board, or an empty set when it has none."""
    board = repo_root / "tasks.jsonl"
    rows: set = set()
    try:
        if not board.is_file() or board.stat().st_size > SPEC_CHECK_MAX_DIFF_BYTES:
            return rows
        text = board.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows.add(row["id"])
    return rows


def _spec_check_repo_root(spec_root, explicit) -> Path:
    """The repository the diff, test pointers and commit ids are resolved against."""
    try:
        return _spec_repository_root(spec_root, explicit)
    except SpecValidationError as exc:
        detail = str(exc)
        prefix = "unresolvable-repo-root: "
        raise SpecError(
            "unresolvable-repo-root",
            detail[len(prefix) :] if detail.startswith(prefix) else detail,
        ) from exc


def _spec_finding(code: str, severity: str, why: str, evidence: str) -> dict:
    """One machine-readable finding: rule, severity, a concrete reason and a source location."""
    return {"code": code, "severity": severity, "why": why, "evidence": evidence}


def align_staged_change(
    spec_root,
    *,
    diff_text: str,
    repository_root=None,
    protocol_version=None,
    max_diff_bytes=None,
) -> dict:
    """Grade one staged diff against the spec corpus and the ch:trace doctrine (AUG-120).

    Reads only: no file, spec or board is written, and the result is a deterministic document. The
    caller (the `spec align` verb) owns the protocol handshake; this function re-checks it so a
    Python caller cannot skip it either.
    """
    requested = (
        SPEC_CHECK_PROTOCOL if protocol_version is None else str(protocol_version)
    )
    if requested not in SPEC_CHECK_PROTOCOLS:
        raise SpecError(
            "unsupported-protocol-version",
            f"requested {requested!r}; this checker speaks {', '.join(SPEC_CHECK_PROTOCOLS)} "
            f"(checker version {SPEC_CHECK_VERSION})",
        )
    limit = SPEC_CHECK_MAX_DIFF_BYTES if max_diff_bytes is None else int(max_diff_bytes)
    raw = diff_text.encode("utf-8", "replace")
    if len(raw) > limit:
        raise SpecError(
            "diff-too-large",
            f"the staged diff is {len(raw)} bytes, over the {limit}-byte cap "
            "(--max-diff-bytes); the check is bounded and refuses rather than buffering without "
            "limit",
        )

    root, documents, blocks = load_spec_corpus(spec_root)
    repo_root = _spec_check_repo_root(root, repository_root)

    diff = _spec_check_diff(diff_text)
    changed = list(diff["order"])
    added = sum(len(diff["files"][path]["added"]) for path in changed)
    removed = sum(len(diff["files"][path]["removed"]) for path in changed)
    binary = sorted(path for path in changed if diff["files"][path]["binary"])

    blocks_by_id = {block["id"]: block for block in blocks}
    citations = _spec_check_citations(documents)

    related: dict = {}
    for block_id, cited_paths in citations.items():
        for changed_path in changed:
            if any(_spec_check_covers(cited, changed_path) for cited in cited_paths):
                matched = sorted(
                    cited
                    for cited in cited_paths
                    if _spec_check_covers(cited, changed_path)
                )[0]
                related[block_id] = matched
                break
    # A corpus document that IS the change scopes the blocks it defines.
    for block in blocks:
        if block["source_file"] in changed:
            related.setdefault(block["id"], block["source_file"])

    markers = _spec_check_markers(diff)
    annotations = _spec_check_annotations(diff)
    board_rows = _spec_check_board_rows(repo_root) if markers else set()

    findings: list = []

    def add(code: str, severity: str, why: str, evidence: str) -> None:
        """Append a finding once. Two rules naming the same location and reason are one finding."""
        finding = _spec_finding(code, severity, why, evidence)
        if finding not in findings:
            findings.append(finding)

    scoped = bool(markers or annotations or related)

    if not changed:
        add(
            "empty-diff",
            "info",
            "the staged diff changes no file, so there is nothing to align",
            "diff:none",
        )

    declared: dict = {}
    unresolved: list = []

    for marker in markers:
        where = f"{marker['file']}:{marker['line']}"
        fields = marker["fields"]
        if marker["malformed"]:
            add("trace-malformed", "fail", marker["malformed"], where)
            continue
        for name in SPEC_TRACE_MANDATORY:
            if name not in fields:
                add(
                    "trace-missing-row" if name == "row" else "trace-missing-field",
                    "fail",
                    f"the ch:trace marker has no {name}= field; {name}= is mandatory on every "
                    "marker (docs/prd/xml-spec-graph.md, the eleven-field closeout contract)",
                    where,
                )
        for name in SPEC_TRACE_CONTRACT:
            if name not in fields:
                if name not in SPEC_TRACE_MANDATORY:
                    add(
                        "trace-missing-field",
                        "fail",
                        f"the marker has no {name}= field: absent links must be EXPLICIT "
                        "(none:<reason>), never omitted (docs/prd/xml-spec-graph.md R10)",
                        where,
                    )
                continue
            v = fields[name].strip()
            if not v:
                add(
                    "trace-absent-value",
                    "fail",
                    f"{name}= is present but empty; an absent link must carry an explicit "
                    f"{name}=none:<reason>",
                    where,
                )
            elif v == "none":
                add(
                    "trace-absent-value",
                    "fail",
                    f"{name}=none states an absence without a reason; write {name}=none:<reason>",
                    where,
                )
        for key, value in marker["unknown"]:
            add(
                "trace-unknown-field",
                "warn",
                f"{key}={value} is not one of the eleven ch:trace fields "
                f"({', '.join(SPEC_TRACE_FIELDS)}); a marker field outside the doctrine is "
                "resolved by nothing",
                where,
            )
        for value in (fields.get("evidence") or "").split(","):
            value = value.strip()
            if not value or SPEC_TRACE_NONE_RE.match(value):
                continue
            if "/" not in value and "." not in value and SPEC_ID_RE.match(value):
                add(
                    "trace-evidence-is-row",
                    "fail",
                    f"evidence={value} is an identifier, not an artifact: evidence must be a "
                    "path, command transcript or witness, never another row or block "
                    "(docs/prd/xml-spec-graph.md R10)",
                    where,
                )
        for value in (fields.get("spec") or "").split(","):
            value = value.strip()
            if not value or SPEC_TRACE_NONE_RE.match(value):
                continue
            block_id, reason = _spec_check_resolve_spec(value, blocks_by_id, repo_root)
            if reason:
                add(
                    "trace-spec-unresolved",
                    "fail",
                    f"spec={value} does not resolve: {reason}",
                    where,
                )
                unresolved.append(value)
            elif block_id:
                declared[block_id] = where
        for value in (fields.get("test") or "").split(","):
            value = value.strip()
            if not value or SPEC_TRACE_NONE_RE.match(value):
                continue
            ok, reason = _spec_check_resolve_test(value, repo_root)
            if not ok:
                add(
                    "trace-test-unresolved",
                    "fail",
                    f"test={value} does not resolve: {reason}",
                    where,
                )
        verdict = (fields.get("verdict") or "").strip()
        commit = (fields.get("commit") or "").strip()
        judged = bool(verdict) and not SPEC_TRACE_NONE_RE.match(verdict)
        bound = bool(commit) and not SPEC_TRACE_NONE_RE.match(commit)
        if judged and not bound:
            add(
                "trace-verdict-unbound",
                "fail",
                f"verdict={verdict} judges a commit, but commit={commit or 'absent'} names none: "
                "a verdict must be bound to the commit it was reached on",
                where,
            )
        if bound:
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
                add(
                    "trace-commit-unknown",
                    "fail",
                    f"commit={commit} is not an abbreviated or full object name",
                    where,
                )
            elif not _spec_check_commit_resolves(commit, repo_root):
                add(
                    "trace-commit-unknown",
                    "fail",
                    f"commit={commit} is not a commit in this repository: a verdict bound to a "
                    "foreign commit proves nothing about this tree",
                    where,
                )
        for value in (fields.get("row") or "").split(","):
            value = value.strip()
            if value and board_rows and value not in board_rows:
                add(
                    "trace-row-unknown",
                    "warn",
                    f"row={value} is not a row in this repository's tasks.jsonl; a trace that "
                    "names no board row cannot be closed by one",
                    where,
                )

    for annotation in annotations:
        block_id = annotation["id"]
        where = f"{annotation['file']}:{annotation['line']}"
        if block_id not in blocks_by_id:
            add(
                "spec-annotation-unresolved",
                "fail",
                f"the change cites spec={block_id}, which this corpus does not define",
                where,
            )
            unresolved.append(block_id)
        else:
            declared[block_id] = where

    for block_id, where in declared.items():
        block = blocks_by_id[block_id]
        if block["status"] != "active":
            add(
                "spec-section-superseded",
                "fail",
                f"the change cites {block_id}, whose corpus status is {block['status']} "
                f"({block['source']}); an annotation that contradicts the section's lifecycle "
                "does not describe the current contract",
                f"{block['source']} (cited at {where})",
            )

    if related and declared and not (set(declared) & set(related)):
        first_changed = sorted(changed)[0]
        cited = ", ".join(sorted(declared))
        scoping = ", ".join(sorted(related))
        add(
            "spec-section-mismatch",
            "warn",
            f"the change cites {cited}, but {first_changed} is scoped by {scoping}: the marker "
            "names a section that does not govern this file",
            f"{first_changed}:1",
        )

    if changed and not scoped:
        add(
            "unscoped-change",
            "warn",
            "no corpus block scopes the changed path(s) and the change cites no spec block "
            f"({', '.join(sorted(changed))}); the result is UNSCOPED, not a pass — the check "
            "cannot say this change matches any specification",
            f"{sorted(changed)[0]}:1",
        )

    if scoped and not markers:
        scoping = ", ".join(sorted(related)) or "the change's own annotations"
        for changed_path in sorted(changed):
            add(
                "trace-missing",
                "fail",
                f"no ch:trace marker in the staged change, yet {scoping} scopes "
                f"{changed_path}: a scoped change must carry its trace",
                f"{changed_path}:1",
            )

    referenced_blocks = []
    for block_id in sorted(set(declared) | set(related)):
        block = blocks_by_id[block_id]
        if block_id in declared:
            why = "cited by the staged change"
        else:
            why = f"cites {related[block_id]}"
        referenced_blocks.append(
            {
                "id": block_id,
                "kind": block["kind"],
                "status": block["status"],
                "source": block["source"],
                "why": why,
            }
        )

    findings.sort(
        key=lambda finding: (finding["code"], finding["evidence"], finding["why"])
    )
    counts = {
        "fail": sum(1 for finding in findings if finding["severity"] == "fail"),
        "warn": sum(1 for finding in findings if finding["severity"] == "warn"),
        "info": sum(1 for finding in findings if finding["severity"] == "info"),
    }
    if counts["fail"]:
        status = "fail"
    elif counts["warn"]:
        status = "warn"
    else:
        status = "pass"
    scope = "empty" if not changed else ("scoped" if scoped else "unscoped")
    return {
        "protocol": requested,
        "version": SPEC_CHECK_VERSION,
        "checker": "auger",
        "status": status,
        "scope": scope,
        "repo_root": str(repo_root),
        "spec_root": str(root),
        "diff": {
            "changed_files": sorted(changed),
            "files": len(changed),
            "added_lines": added,
            "removed_lines": removed,
            "binary_files": binary,
        },
        "referenced": {
            "blocks": referenced_blocks,
            "unresolved": sorted(set(unresolved)),
            "paths": sorted(changed),
        },
        "findings": findings,
        "counts": counts,
    }


def cmd_spec_align(a):
    """`auger spec align` — the staged-diff checker GitReins invokes (AUG-120 / AUG-135).

    One typed refusal shape, the same one every other `spec` verb uses: `spec error (<code>)` on
    stderr and exit 2, nothing on stdout.
    """
    try:
        return _spec_align_dispatch(a)
    except SpecError as exc:
        print(f"spec error ({exc.code}): {exc.detail}", file=sys.stderr)
        raise SystemExit(2) from None


def _spec_align_dispatch(a):
    """Read the diff, align it, print ONE JSON line. Exit 0 pass/warn, 1 fail, 2 a typed refusal.

    The refusal happens BEFORE stdin is read when the handshake cannot be honoured, so a version we
    do not speak costs nothing and prints no verdict a caller could mistake for a result.
    """
    requested = str(
        getattr(a, "protocol_version", SPEC_CHECK_PROTOCOL) or SPEC_CHECK_PROTOCOL
    )
    if requested not in SPEC_CHECK_PROTOCOLS:
        raise SpecError(
            "unsupported-protocol-version",
            f"requested {requested!r}; this checker speaks {', '.join(SPEC_CHECK_PROTOCOLS)} "
            f"(checker version {SPEC_CHECK_VERSION})",
        )
    diff_file = getattr(a, "diff_file", None)
    from_stdin = bool(getattr(a, "diff_stdin", False))
    if diff_file and from_stdin:
        raise SpecError(
            "ambiguous-diff-source",
            "pass exactly one of --diff-stdin and --diff-file",
        )
    if not diff_file and not from_stdin:
        raise SpecError(
            "missing-diff-source",
            "pass --diff-stdin (the GitReins shape) or --diff-file PATH",
        )
    limit = getattr(a, "max_diff_bytes", None) or SPEC_CHECK_MAX_DIFF_BYTES
    if diff_file:
        try:
            raw = Path(diff_file).read_bytes()
        except OSError as exc:
            raise SpecError(
                "unreadable-diff-file", f"{diff_file}: {exc.strerror or exc}"
            ) from exc
        if len(raw) > limit:
            raise SpecError(
                "diff-too-large",
                f"{diff_file} is {len(raw)} bytes, over the {limit}-byte cap (--max-diff-bytes)",
            )
        diff_text = raw.decode("utf-8", "replace")
    else:
        buffered = getattr(sys.stdin, "buffer", None)
        raw = (
            buffered.read(limit + 1)
            if buffered is not None
            else sys.stdin.read().encode()
        )
        if len(raw) > limit:
            raise SpecError(
                "diff-too-large",
                f"stdin exceeds the {limit}-byte cap (--max-diff-bytes); the check is bounded "
                "and refuses rather than buffering without limit",
            )
        diff_text = raw.decode("utf-8", "replace")

    spec_root = getattr(a, "spec_root", None) or getattr(a, "root", None)
    if not spec_root:
        repository = getattr(a, "repo_root", None)
        if not repository:
            raise SpecError(
                "missing-spec-root",
                "pass --spec-root DIR (or --repo-root DIR to default it to <repo>/specs)",
            )
        spec_root = str(Path(repository) / "specs")
    payload = align_staged_change(
        spec_root,
        diff_text=diff_text,
        repository_root=getattr(a, "repo_root", None),
        protocol_version=requested,
        max_diff_bytes=limit,
    )
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    return 1 if payload["status"] == "fail" else 0


def cmd_spec(a):
    """`auger spec validate|list|assemble` — the XML spec authoring surface (AUG-118)."""
    try:
        return _spec_dispatch(a)
    except SpecError as exc:
        print(f"spec error ({exc.code}): {exc.detail}", file=sys.stderr)
        raise SystemExit(2) from None


def _spec_report_unreadable(unreadable: list) -> None:
    """Tell the author, on stderr, exactly what a selection-scoped verb had to skip (AUG-146).

    stderr, not stdout: the selection is the answer and stdout stays parseable (a JSON assembly is
    still `json.loads`-able). The count is the point — a corpus that silently lost a document is
    the defect, and a corpus-wide `spec validate` still refuses the very same file.
    """
    if not unreadable:
        return
    print(
        f"spec: skipped {len(unreadable)} unreadable document(s) — this verb is "
        "selection-scoped; `auger spec validate` refuses the corpus",
        file=sys.stderr,
    )
    for entry in unreadable:
        print(f"spec:   {entry['detail']} [{entry['code']}]", file=sys.stderr)


def _spec_corpus_counts(documents: dict, unreadable: list, records: list) -> dict:
    """The selection measured against the corpus it was read from (AUG-146, AC-14).

    `documents_selected` counts documents that actually CONTRIBUTED an emitted block, so the gap
    between it and `documents_parsed` is visible in the product instead of inferred by a reader.
    """
    return {
        "documents_discovered": len(documents) + len(unreadable),
        "documents_parsed": len(documents),
        "documents_unreadable": len(unreadable),
        "documents_selected": len(
            {record["block"]["source_file"] for record in records}
        ),
    }


def _spec_dispatch(a):
    command = getattr(a, "spec_cmd", "validate")
    # AUG-121/AUG-122: the task-scope contract and the one-query context have their own entry
    # points. They are NOT variants of `assemble`: a task packet is a statement of what was
    # approved (no closure), and a context answer carries exclusions with reasons plus measured
    # bytes. Routing them through the assembly renderer would make three different answers share
    # one shape, which is how a "minimal context" turns back into a dump.
    if command == "task-packet":
        return cmd_spec_task_packet(a)
    if command == "task-export":
        return cmd_spec_task_export(a)
    if command == "context":
        return cmd_spec_context(a)
    # AUG-119/AUG-124: the PLAN seam above the task packet, and the GitReins handoff. Like the
    # task verbs they are not variants of `assemble`: they write through boardctl (or hand a packet
    # to a judge) and their answers are records, not rendered selections.
    if command == "plan-draft":
        return cmd_spec_plan_draft(a)
    if command == "plan-approve":
        return cmd_spec_plan_approve(a)
    if command == "plan-export":
        return cmd_spec_plan_export(a)
    if command == "plan-reconcile":
        return cmd_spec_plan_reconcile(a)
    if command == "judge-packet":
        return cmd_spec_judge_packet(a)
    if command == "judge-handoff":
        return cmd_spec_judge_handoff(a)
    if command == "check" and (
        getattr(a, "diff_stdin", False) or getattr(a, "diff_file", None)
    ):
        # AUG-135: `spec check` is the verb the board row's own text names, so it carries the
        # checker too — SAME implementation (`_spec_align_dispatch`), same one-line payload, same
        # exit codes. Two command strings, one verdict; the boards cannot disagree about what the
        # checker is because both of them run this code.
        return _spec_align_dispatch(a)
    # AUG-146: `list` and `assemble` select, they do not adjudicate the corpus — a neighbouring
    # file that is still being written is skipped and counted instead of taking every query
    # offline. `validate`/`check`/`bundle` are corpus-wide verdicts and never get the tolerance.
    selection_scoped = command in ("list", "assemble")
    unreadable: list = []
    root, documents, blocks = load_spec_corpus(
        a.root, tolerate_unreadable=selection_scoped, skipped=unreadable
    )
    _spec_report_unreadable(unreadable)
    # AUG-127: the repository the obligations resolve in is EXPLICIT, never auger's own
    # directory. `--repo-root` names it; without the flag the validator resolves
    # `git rev-parse --show-toplevel` from the spec root and refuses
    # (`unresolvable-repo-root`) rather than falling back to `Path(__file__).parent`.
    repository_root = getattr(a, "repo_root", None)
    if command in ("validate", "check"):
        # AUG-132/AUG-134: `validate` is not a counting verb. BOTH verbs run the one
        # verdict — the corpus loader above (root shape, namespace, schema version,
        # block grammar, typed edges) plus the obligation pass over the SAME documents,
        # loaded once and handed over. Before this, `check` was the only verb that
        # resolved a test path and `validate` the only verb that accepted a namespaced
        # root, so one corpus could be valid and invalid at once depending on which verb
        # a caller happened to type (and a corpus naming tests/does-not-exist.py
        # validated "ok" with exit 0).
        try:
            nodes = validate_xml_specs(
                a.root, loaded=(root, documents), repository_root=repository_root
            )
        except SpecValidationError as exc:
            raise SpecError("invalid-spec-graph", str(exc)) from exc
        if command == "check":
            print(
                json.dumps({"status": "valid", "nodes": sorted(nodes)}, sort_keys=True)
            )
            return 0
        references = sum(
            len(targets) for block in blocks for targets in block["refs"].values()
        )
        print(
            f"spec: {len(documents)} document(s), {len(blocks)} block(s), "
            f"{references} reference(s) ok"
        )
        return 0
    if command == "bundle":
        # AUG-126: same story for assemble_xml_requirement — reachable from Python,
        # unbound on the CLI.
        try:
            bundle = assemble_xml_requirement(
                a.root, a.requirement, repository_root=repository_root
            )
        except SpecUnknownRequirement as exc:
            # AUG-144: a requirement this corpus does not have is its own code. Printing it as
            # `invalid-spec-graph` dressed a caller's typo up as a broken corpus, and made the
            # one diagnostic that must be unambiguous indistinguishable at a glance.
            raise SpecError("unknown-requirement", str(exc)) from exc
        except SpecValidationError as exc:
            raise SpecError("invalid-spec-graph", str(exc)) from exc
        if not bundle["verifications"]:
            # AUG-144: `{"verifications": []}` is a real answer for a requirement with no
            # obligations, so the JSON stays (backward compatible) and the human/diagnostic path
            # names WHY it is empty. An empty array alone cannot be told from a broken lookup.
            print(
                f"spec note (no-obligations): {bundle['id']} names no obligation and no block "
                "names it — an empty bundle is a report, not a failed lookup",
                file=sys.stderr,
            )
        print(json.dumps(bundle, sort_keys=True, separators=(",", ":")))
        return 0
    ids = tuple(getattr(a, "id", None) or ())
    kinds = tuple(getattr(a, "kind", None) or ())
    applies_to = tuple(getattr(a, "applies_to", None) or ())
    statuses = tuple(getattr(a, "status", None) or ())
    versions = tuple(getattr(a, "version", None) or ())
    selected = bool(ids or kinds or applies_to or statuses or versions)
    if command == "list":
        rows = 0
        for block in blocks:
            if not _spec_matches(block, ids, kinds, applies_to, statuses, versions):
                continue
            scopes = " ".join(block["applies_to"]) or "-"
            print(
                f"{block['id']}\t{block['kind']}\t{block['status']}\t"
                f"{block['version']}\t{block['source']}\t{scopes}"
            )
            rows += 1
        if selected and not rows:
            # AUG-149: `list` with NO selector is the whole-corpus listing it documents; a
            # SELECTOR that matches nothing is a different answer, and an empty stdout alone made
            # it indistinguishable from a tool that had nothing to say. The notice goes to stderr
            # so stdout stays parseable (`--format json` has the same contract).
            _spec_no_match_notice(
                _spec_describe_selection(ids, kinds, applies_to, statuses, versions)
            )
        return 0
    selection, records = select_spec_blocks(
        blocks,
        ids=ids,
        kinds=kinds,
        applies_to=applies_to,
        statuses=statuses,
        versions=versions,
        closure=not getattr(a, "no_deps", False),
        empty_ok=True,
    )
    if not records:
        _spec_no_match_notice(selection)
    text = render_spec_assembly(
        records,
        selection=selection,
        fmt=a.format,
        corpus=_spec_corpus_counts(documents, unreadable, records),
    )
    out = getattr(a, "out", None)
    if out:
        _spec_write_atomic(out, text)
        print(f"wrote {len(records)} block(s) to {out}")
    else:
        sys.stdout.write(text)
    return 0


def _spec_no_match_notice(selection: str) -> None:
    """One line on stderr: a selector matched nothing, and that is a report, not a failure.

    AUG-149: the empty-selector case was already refused, so the two shapes had different
    contracts — refusing "no selector" while answering "selector matched nothing" with silence.
    A caller scripting the CLI could not tell "your selector matched nothing" from "the tool had
    nothing to say" without interpreting emptiness. stdout stays whatever the verb renders.
    """
    print(
        f"spec note (no-match): {selection} matched no block "
        "(empty selection, exit 0 — not a failure)",
        file=sys.stderr,
    )


def _add_spec_repo_root(parser) -> None:
    """The `--repo-root` flag every verb that resolves an obligation shares (AUG-127).

    Only the verbs that actually resolve paths carry it: `list` and `assemble` select
    blocks and never touch a test path, and a flag they ignored would be a declared
    surface that does nothing.
    """
    parser.add_argument(
        "--repo-root",
        metavar="DIR",
        help=(
            "repository the corpus' test/migration/fixture paths are relative to; must be "
            "a git work tree's top level. Default: `git rev-parse --show-toplevel` from "
            "the spec root (refused when that resolves to nothing)"
        ),
    )


def _add_spec_checker_flags(parser) -> None:
    """The staged-diff checker flags, shared by `spec align` and `spec check --diff-stdin`.

    One definition, because the two command strings an operator (or a board row) may name must
    accept the SAME arguments: AUG-135's row text simulates the caller as
    `... --diff-stdin check` while the documentation names `auger spec align`. Both verbs route
    into `_spec_align_dispatch`, so the payload cannot differ either.
    """
    parser.add_argument(
        "--spec-root",
        metavar="DIR",
        help="spec corpus root; default: the positional ROOT, else <repo-root>/specs",
    )
    parser.add_argument(
        "--protocol-version",
        default=SPEC_CHECK_PROTOCOL,
        help="the checker protocol the caller speaks (this checker speaks "
        f"{', '.join(SPEC_CHECK_PROTOCOLS)}; default {SPEC_CHECK_PROTOCOL})",
    )
    parser.add_argument(
        "--diff-stdin",
        action="store_true",
        help="read the staged unified diff from stdin — the shape GitReins sends; turns this "
        "verb into the staged-diff checker",
    )
    parser.add_argument(
        "--diff-file",
        metavar="PATH",
        help="read the diff from a file instead of stdin (tests and replays)",
    )
    parser.add_argument(
        "--max-diff-bytes",
        type=int,
        default=SPEC_CHECK_MAX_DIFF_BYTES,
        metavar="N",
        help=f"refuse a diff larger than N bytes instead of buffering without limit "
        f"(default {SPEC_CHECK_MAX_DIFF_BYTES})",
    )


def _add_spec_selectors(parser) -> None:
    """The selector flags `spec list` and `spec assemble` share (AND across, OR within)."""
    parser.add_argument(
        "--id", action="append", metavar="BLOCK-ID", help="select this exact block id"
    )
    parser.add_argument(
        "--kind",
        action="append",
        choices=SPEC_BLOCK_KINDS,
        help="select this block kind",
    )
    parser.add_argument(
        "--applies-to",
        action="append",
        metavar="SCOPE",
        help="select blocks that apply to this scope token (for example auger.export)",
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=SPEC_STATUSES,
        help="select this lifecycle status",
    )
    parser.add_argument(
        "--version",
        action="append",
        metavar="X.Y.Z",
        help="select this block version attribute",
    )


# ---------------------------------------------------------------- XML spec graph (AUG-118/AUG-123)


class SpecValidationError(ValueError):
    """A typed XML specification graph integrity failure."""


class SpecUnknownRequirement(SpecValidationError):
    """A `bundle` request named an id that is not a requirement block (AUG-144).

    Its own type because it is its own CODE: "you asked for a requirement this corpus does not
    have" is a caller's typo, not a broken graph, and the CLI must not dress one up as the other.
    """


SPEC_RUNNERS = {"pytest": (sys.executable, "-m", "pytest")}
SPEC_BLOCK_TAGS = {"requirement", "verification", "test"}


def _xml_spec_documents(spec_root):
    """Read every document under a spec root through the ONE root/shape loader.

    AUG-134: this function used to parse each document itself and compare the bare
    `document.tag` to "spec", while `load_spec_corpus` matched the Clark-notation
    `{urn:auger:spec:1}spec`. On the same directory `spec validate` therefore accepted a
    corpus that `spec check` refused as `invalid-root` — one corpus, two verdicts. Two
    parsers of one corpus always drift, so there is now one: `_spec_read_document` owns
    the root element, the namespace, the schema version and the DOCTYPE/ENTITY refusal,
    and its typed SpecError is re-raised here under this validator's error type so both
    verbs print the same diagnostic and exit with the same code.
    """
    root = Path(spec_root).expanduser()
    if not root.is_dir():
        raise SpecValidationError(f"missing-spec-root: {spec_root} is not a directory")
    root = root.resolve()
    paths, refusals = _spec_discover_documents(root)
    if not paths:
        if refusals:
            raise SpecValidationError(f"{refusals[0]['code']}: {refusals[0]['detail']}")
        raise SpecValidationError(f"empty-corpus: no *.xml documents under {root}")
    if refusals:
        # AUG-148: this loader is the corpus-wide one too, so an out-of-root symlink is a typed
        # refusal here, never a silent omission.
        raise SpecValidationError(f"{refusals[0]['code']}: {refusals[0]['detail']}")
    documents = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            documents[rel] = _spec_read_document(path, root)
        except SpecError as exc:
            raise SpecValidationError(f"{exc.code}: {exc.detail}") from None
    return root, documents


def _git_toplevel(directory):
    """`git rev-parse --show-toplevel` for `directory`, or None when it is no work tree.

    Run with `-C`, never from the caller's cwd: the answer must be a function of the
    directory handed in, not of wherever the process happens to be standing.
    """
    probe = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    shown = probe.stdout.strip()
    if not shown:
        return None
    path = Path(shown)
    return path.resolve() if path.is_dir() else None


def _spec_repository_root(spec_root, explicit=None):
    """The repository a corpus' `path`/`migration`/`fixture` values are relative to.

    AUG-127: this used to be `Path(__file__).resolve().parent` — the directory of the
    INSTALLED MODULE. So every obligation was resolved, `git ls-files`-checked and
    AST-verified inside auger's own checkout: a consumer repository naming its own
    committed test (`tests/test_marker.py`, present and tracked) was refused
    `stale-test-ref ... (resolved <auger>/tests/test_marker.py)`, and a corpus whose
    obligation happened to match a file in auger's tree passed for the wrong reason. A
    repository can never satisfy a root it does not control.

    The root is chosen EXPLICITLY, in this order, and there is no fallback to auger:

      * `explicit` — the CLI's `--repo-root` (or `repository_root=` from Python). It must
        BE a work tree's top level, so "repository-relative" has exactly one meaning;
      * otherwise `git rev-parse --show-toplevel` resolved FROM THE SPEC ROOT, which is
        what a corpus committed inside the repository it describes gets for free.

    Neither resolving is NO VERDICT (`unresolvable-repo-root`), never a guess — quietly
    guessing is how the pre-fix code graded every corpus against the wrong tree.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if not candidate.is_dir():
            raise SpecValidationError(
                f"unresolvable-repo-root: {explicit} is not a directory; --repo-root must "
                "name the repository the corpus' test, migration and fixture paths are "
                "relative to"
            )
        resolved = candidate.resolve()
        top = _git_toplevel(resolved)
        if top is None:
            raise SpecValidationError(
                f"unresolvable-repo-root: {resolved} is not a git work tree; --repo-root "
                "must name a repository root, because a test path is only proved tracked "
                "by `git ls-files` inside one"
            )
        if top != resolved:
            raise SpecValidationError(
                f"unresolvable-repo-root: --repo-root {resolved} is not a repository root "
                f"(the work tree's top level is {top})"
            )
        return resolved
    top = _git_toplevel(Path(spec_root))
    if top is None:
        raise SpecValidationError(
            f"unresolvable-repo-root: {spec_root} is not inside a git work tree and no "
            "--repo-root was given; pass --repo-root <repository> so test, migration and "
            "fixture paths can be resolved and proved tracked there"
        )
    return top


def validate_xml_specs(spec_root, *, loaded=None, repository_root=None):
    """Validate graph IDs, typed links and tracked test references under a spec root.

    `loaded` is the `(root, documents)` pair already produced for this run, with
    `documents` keyed by repository-relative path — the same shape `load_spec_corpus`
    returns, so the CLI can hand either loader's output over. The CLI loads a corpus
    ONCE and passes the same documents to the block checks and to this obligation pass,
    so the two verbs cannot see two different corpora (AUG-134) and a corpus cannot
    change under the validator mid-verdict. Call it without `loaded` and the documents
    are read here through that same loader.

    `repository_root` is the repository the obligations resolve in (AUG-127). Omitted, it
    is `git rev-parse --show-toplevel` from the SPEC ROOT — never auger's own directory;
    see `_spec_repository_root`.
    """
    import ast

    if loaded is None:
        root, documents = _xml_spec_documents(spec_root)
    else:
        root, documents = loaded
    repository_root = _spec_repository_root(root, repository_root)
    nodes = {}
    sources = {}
    for rel, document in documents.items():
        for node in document.iter():
            tag = _spec_local(node.tag)
            if tag not in SPEC_BLOCK_TAGS:
                continue
            node_id = node.get("id")
            if not node_id:
                raise SpecValidationError(f"missing-id: {rel}: <{tag}>")
            if node_id in nodes:
                raise SpecValidationError(f"duplicate-id: {rel}: {node_id}")
            nodes[node_id] = node
            sources[node_id] = rel
    for node_id, node in nodes.items():
        tag = _spec_local(node.tag)
        rel = sources[node_id]
        if tag == "requirement":
            refs = (node.get("verifies") or "").split()
            refs.extend(
                (child.get("ref") or "")
                for child in node
                if _spec_local(child.tag) == "verification-ref"
            )
            refs = [ref for ref in refs if ref]
            # AUG-134: a requirement is verifiable either by naming verification refs
            # (the typed test/verification graph) or by declaring a verification METHOD
            # (`verification=`), which the authoring grammar already requires on every
            # requirement block. Keying this rule on refs alone refused every corpus
            # written in the authoring grammar — the shipped fixture corpus included — so
            # `check` and `validate` could never be one verdict.
            if not refs and not (node.get("verification") or "").strip():
                raise SpecValidationError(
                    f"missing-verification-ref: {rel}: {node_id} "
                    "(no verifies= ref and no verification= method)"
                )
            for ref in refs:
                if ref not in nodes:
                    raise SpecValidationError(
                        f"dangling-test-ref: {rel}: {node_id} -> {ref}"
                    )
                if _spec_local(nodes[ref].tag) not in {"verification", "test"}:
                    raise SpecValidationError(
                        f"wrong-ref-type: {rel}: {node_id} -> {ref}"
                    )
        if tag not in {"verification", "test"}:
            continue
        target = node.get("verifies")
        if target and (
            target not in nodes or _spec_local(nodes[target].tag) != "requirement"
        ):
            raise SpecValidationError(
                f"dangling-requirement-ref: {rel}: {node_id} -> {target}"
            )
        for attribute in ("type", "method"):
            declared = (node.get(attribute) or "").strip()
            # The obligation validator has always accepted `manual` where the authoring
            # grammar names the same intent `demo` (SPEC_VERIFICATION_METHODS). Both mean
            # "not automated", so the union is accepted instead of refusing a shipped
            # shape over a vocabulary split (AUG-134); the loader above still enforces
            # the authored enum for any corpus that reaches the CLI.
            if declared and declared not in SPEC_VERIFICATION_METHODS + ("manual",):
                raise SpecValidationError(
                    f"invalid-verification-type: {rel}: {node_id} "
                    f"{attribute}={declared}"
                )
        # AUG-132: a machine-checkable test claim is a node that points AT a test — a
        # `<test>` block, or any block carrying a runner/selector/path. A block that
        # only declares type/method="test" states a METHOD, not a location: the
        # authoring grammar uses exactly that shape for verification plans, and treating
        # it as a broken pointer would refuse the shipped fixture corpus.
        pointer = any(
            (node.get(attribute) or "").strip()
            for attribute in ("runner", "selector", "path")
        )
        if tag != "test" and not pointer:
            continue
        runner = (node.get("runner") or "").strip()
        selector = (node.get("selector") or "").strip()
        test_path = (node.get("path") or "").strip()
        if runner not in SPEC_RUNNERS or not selector or not test_path:
            raise SpecValidationError(
                f"invalid-test-ref: {rel}: {node_id} "
                "(needs runner=<registered runner>, selector= and path=)"
            )
        resolved = (repository_root / test_path).resolve()
        # Escaping the root and pointing at a missing file are SEPARATE refusals: "this
        # path leaves the repository" is not a stale test, and a caller scripting on the
        # code has to be able to tell them apart. Both fail closed (AUG-127).
        if not resolved.is_relative_to(repository_root):
            raise SpecValidationError(
                f"escaping-test-ref: {rel}: {node_id}: {test_path} "
                f"(resolved {resolved}, outside {repository_root})"
            )
        if not resolved.is_file():
            raise SpecValidationError(
                f"stale-test-ref: {rel}: {node_id}: {test_path} (resolved {resolved})"
            )
        relative_test_path = resolved.relative_to(repository_root).as_posix()
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative_test_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if tracked.returncode != 0 or tracked.stdout.strip() != relative_test_path:
            raise SpecValidationError(
                f"untracked-test-ref: {rel}: {node_id}: {test_path}"
            )
        try:
            tree = ast.parse(
                resolved.read_text(encoding="utf-8"), filename=str(resolved)
            )
        except (SyntaxError, OSError) as exc:
            raise SpecValidationError(
                f"stale-test-ref: {rel}: {node_id}: {test_path} ({exc})"
            ) from exc
        if not any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == selector
            for item in ast.walk(tree)
        ):
            # Name the file that was parsed, not just the selector: with the root now coming
            # from the caller (AUG-127) "which file did you look in?" is the first question a
            # consumer asks, and the answer is not derivable from the path alone.
            raise SpecValidationError(
                f"stale-test-ref: {rel}: {node_id}: selector {selector} in {resolved}"
            )
        migration = node.get("migration")
        if migration:
            migration_path = (repository_root / migration).resolve()
            if (
                not migration_path.is_relative_to(repository_root)
                or not migration_path.is_file()
            ):
                raise SpecValidationError(
                    f"stale-migration-ref: {rel}: {node_id}: {migration}"
                )
            fixture = node.get("fixture", "")
            fixture_path = (repository_root / fixture).resolve()
            if (
                not fixture_path.is_relative_to(repository_root)
                or not fixture_path.is_file()
            ):
                raise SpecValidationError(
                    f"stale-fixture-ref: {rel}: {node_id}: {fixture}"
                )
            if not node.get("assertions", "").strip():
                raise SpecValidationError(
                    f"incomplete-migration-obligation: {rel}: {node_id}"
                )
    return nodes


def _spec_source_index(documents: dict) -> tuple:
    """Every authored block element by id, with its position in source order.

    Position is (document path, document order) over the SAME documents one loader produced, so
    two bundles of one corpus order their obligations identically. Blocks are indexed by id only
    once: a fragment spliced into a document is still authored in exactly one file.
    """
    elements: dict = {}
    order: dict = {}
    for rel in sorted(documents):
        for element in documents[rel].iter():
            if _spec_local(element.tag) not in SPEC_BLOCK_KINDS:
                continue
            block_id = (element.get("id") or "").strip()
            if not block_id or block_id in elements:
                continue
            elements[block_id] = element
            order[block_id] = len(order)
    return elements, order


def _spec_obligation_ids(
    elements: dict, order: dict, requirement_id: str, requirement
) -> list:
    """Every block that is an obligation OF `requirement_id`, once each, in source order.

    AUG-144: BOTH directions of the `verifies` edge are real. A requirement may NAME its
    obligations (`verifies=` attribute, `<verification-ref>` children) and an obligation may
    NAME the requirement — the shape every document in the shipped corpus uses, the shape
    `spec check` lists, and the shape `assemble` expands as inverse closure. `bundle` read only
    the forward direction, so a requirement whose obligations name IT answered
    `{"verifications": []}`: an empty array that is indistinguishable from "this requirement has
    no obligations", which is the same class of lie as a green check with nothing behind it.

    The inverse scan runs over the authored BLOCKS, not over the obligation validator's node map:
    that map indexes requirement/verification/test only, so scanning it silently dropped the
    `<acceptance>` criteria that verify a requirement — the very blocks `assemble` includes in
    the same inverse closure.

    Both directions declared for one pair is a `reference-cycle` and is refused by the corpus
    loader before this runs, so the union here never hides a cycle — it only dedupes the same
    edge spelled twice (attribute and `<verification-ref>`).
    """
    declared = requirement.get("verifies", "").split()
    declared.extend(
        child.get("ref", "")
        for child in requirement
        if _spec_local(child.tag) == "verification-ref"
    )
    inverse = [
        block_id
        for block_id, element in elements.items()
        if _spec_local(element.tag) in SPEC_OBLIGATION_KINDS
        and requirement_id in (element.get("verifies") or "").split()
    ]
    collected: dict = {}
    for ref in (*declared, *inverse):
        ref = (ref or "").strip()
        if ref and ref in elements and ref not in collected:
            collected[ref] = order.get(ref, len(order))
    return sorted(collected, key=lambda block_id: (collected[block_id], block_id))


def assemble_xml_requirement(spec_root, requirement_id, *, repository_root=None):
    """Return the requirement and its linked verification obligations as data only.

    Obligations are collected from BOTH directions of the `verifies` edge (AUG-144) — forward
    refs the requirement declares, and blocks that declare they verify the requirement — deduped
    by id and ordered by source position. The payload always carries `id` and `verifications`
    (backward compatible) plus `status`/`count`, so an empty bundle is a NAMED report
    (`no-obligations`) rather than a blank array a caller has to interpret. An id that is not a
    requirement raises `SpecUnknownRequirement`, the distinct code the CLI prints.
    """
    root, documents = _xml_spec_documents(spec_root)
    nodes = validate_xml_specs(
        root, loaded=(root, documents), repository_root=repository_root
    )
    requirement = nodes.get(requirement_id)
    # AUG-134 left two bare-tag reads here (`requirement.tag != "requirement"` and
    # `findall("verification-ref")`), which only work for a document with NO namespace — and the
    # XSD requires `urn:auger:spec:1` on every element, so a namespaced corpus (the only kind the
    # grammar accepts) could reach neither branch. Compare local names instead.
    if requirement is None or _spec_local(requirement.tag) != "requirement":
        raise SpecUnknownRequirement(
            f"{requirement_id} is not a requirement block in this corpus"
        )
    elements, order = _spec_source_index(documents)
    obligations = []
    for ref in _spec_obligation_ids(elements, order, requirement_id, requirement):
        node = elements[ref]
        runner = node.get("runner")
        obligations.append(
            {
                "id": ref,
                "type": node.get("type", "test"),
                "path": node.get("path"),
                "selector": node.get("selector"),
                "runner": list(SPEC_RUNNERS[runner]) if runner else None,
                "fixture": node.get("fixture"),
                "migration": node.get("migration"),
                "assertions": node.get("assertions"),
            }
        )
    return {
        "id": requirement_id,
        "status": "ok" if obligations else "no-obligations",
        "count": len(obligations),
        "verifications": obligations,
    }


def cmd_spec_validate(args):
    nodes = validate_xml_specs(args.root)
    print(json.dumps({"status": "valid", "nodes": sorted(nodes)}, sort_keys=True))
    return 0


def cmd_spec_assemble(args):
    bundle = assemble_xml_requirement(args.root, args.requirement)
    print(json.dumps(bundle, sort_keys=True, separators=(",", ":")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="auger", description="spec drilling backed by DuckBrain"
    )
    ap.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s v{AUGER_VERSION}",
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
    s.add_argument("--why-not", action="append", metavar="REASON")
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
        "export",
        help="render every namespace table as one deterministic, read-only spec",
    )
    s.add_argument(
        "--profile",
        choices=["default", "nasa"],
        default="default",
        help=(
            "rendering profile; `nasa` emits the requirement grammar from "
            "docs/playbook/NASA-SE-PLAYBOOK.md (one requirement per decision, alternatives, "
            "rationale, [V: T|D|A|I] verification method, TBD register). Default output is "
            "unchanged"
        ),
    )
    s.set_defaults(fn=cmd_export)

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
        "serve",
        help="serve the embedding store over HTTP (semantic search for external agents)",
    )
    s.add_argument(
        "--host",
        default=SERVE_DEFAULT_HOST,
        help=f"bind address (default {SERVE_DEFAULT_HOST}: reachable only from this host)",
    )
    s.add_argument(
        "--port",
        type=int,
        default=SERVE_DEFAULT_PORT,
        help=f"TCP port (default {SERVE_DEFAULT_PORT}; 0 binds an ephemeral one, and the "
        "banner names it)",
    )
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("api", help="serve auger's read-first HTTP API (AUG-067)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=AUGER_API_DEFAULT_PORT)
    s.set_defaults(fn=cmd_api)

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

    s = sub.add_parser(
        "record",
        help="write the declared registers: break, assumption, unknown, option costs/breaks (AUG-019)",
    )
    record_sub = s.add_subparsers(dest="record_cmd", required=True)

    r = record_sub.add_parser(
        "break", help="record what a stored decision breaks, and the consequence"
    )
    r.add_argument(
        "--decision",
        required=True,
        metavar="D-00X",
        help="the decision this break names; must already be on this project's record",
    )
    r.add_argument("--breaks-what", required=True, help="what the decision breaks")
    r.add_argument("--consequence", required=True, help="what that break changes")
    r.add_argument(
        "--applied",
        action="store_true",
        help="the break has been applied, not just recorded",
    )
    r.set_defaults(fn=cmd_record_break)

    r = record_sub.add_parser(
        "assumption", help="record what the project is resting on, with its falsifier"
    )
    r.add_argument("--text", required=True, help="the assumption itself")
    r.add_argument(
        "--falsifier",
        required=True,
        help="what would prove it wrong (required: an unfalsifiable assumption is a wish)",
    )
    r.add_argument("--monitoring", help="how the assumption is watched")
    r.set_defaults(fn=cmd_record_assumption)

    r = record_sub.add_parser(
        "unknown", help="record what the project knows it does not know"
    )
    r.add_argument("--text", required=True, help="the known unknown")
    r.add_argument("--owner", help="who owns resolving it")
    r.add_argument("--trigger", help="what makes it need an answer")
    r.add_argument("--containment", help="what bounds it meanwhile")
    r.set_defaults(fn=cmd_record_unknown)

    r = record_sub.add_parser(
        "option", help="record an option's costs/breaks text (PATCHes the option row)"
    )
    r.add_argument(
        "option",
        metavar="OPTION",
        help="option token: full id (D-001-O2), label, or bare index (O2); "
        "unresolvable or ambiguous tokens are refused without writing",
    )
    r.add_argument("--costs", help="what choosing this option costs")
    r.add_argument("--breaks", help="what choosing this option breaks")
    r.set_defaults(fn=cmd_record_option)

    s = sub.add_parser(
        "spec",
        help="validate and assemble XML specifications (AUG-118: IDs, typed metadata, "
        "attribute selection, cross-file assembly)",
        # AUG-147: the corpus model is a DECISION, so it is stated where the author reads it.
        # Without this sentence, "an <include> does not change what is selectable" gets re-filed
        # as a defect every time a new reader meets it.
        description=(
            "Author, validate, select and assemble an XML spec corpus.\n\n"
            "The corpus is a FLAT corpus: every *.xml under ROOT contributes blocks and is\n"
            "selectable, whether or not another document includes it. <include src> is an\n"
            "assertion-only edge — it splices that fragment's blocks at the include site (so it\n"
            "decides their ORDER) and it must resolve inside ROOT without a cycle, but it never\n"
            "adds or removes a document from the corpus.\n\n"
            "`validate`, `check` and `bundle` are corpus-wide verdicts; `list` and `assemble`\n"
            "are selection-scoped and skip (counted, on stderr) a document that is not\n"
            "well-formed XML."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    spec_sub = s.add_subparsers(dest="spec_cmd", required=True)

    v = spec_sub.add_parser(
        "validate",
        help="validate a spec corpus: schema version, stable IDs, typed edges, methods",
    )
    v.add_argument("root", help="spec root directory; every *.xml below it is indexed")
    _add_spec_repo_root(v)
    v.set_defaults(fn=cmd_spec)

    lst = spec_sub.add_parser(
        "list",
        help="list blocks with their kind, status, version and source citation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes:\n"
            "  no selector           0, the whole corpus is listed (this verb's default)\n"
            "  selector, no match    0, no rows on stdout, and a\n"
            "                        'spec note (no-match): ...' line on stderr\n"
            "  selector, matches     0\n"
        ),
    )
    lst.add_argument("root")
    _add_spec_selectors(lst)
    lst.set_defaults(fn=cmd_spec)

    asm = spec_sub.add_parser(
        "assemble",
        help="select blocks by ID/attributes and assemble them deterministically "
        "(matched blocks plus their explicit dependency closure)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes:\n"
            "  no selector           2, 'spec error (empty-selector)' — assembly is never a\n"
            "                        silent whole-corpus dump\n"
            "  selector, no match    0, an empty selection is emitted (valid document with\n"
            "                        counts 0) plus a 'spec note (no-match): ...' line on stderr\n"
            "  selector, matches     0\n"
        ),
    )
    asm.add_argument("root")
    _add_spec_selectors(asm)
    asm.add_argument(
        "--no-deps",
        action="store_true",
        help="emit only the matched blocks (no depends-on/verifies closure)",
    )
    asm.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default markdown)",
    )
    asm.add_argument(
        "--out",
        help="write the assembly here atomically; the parent directory must already exist "
        "(nothing is created when the run refuses)",
    )
    asm.set_defaults(fn=cmd_spec)

    chk = spec_sub.add_parser(
        "check",
        help="validate test obligations: tracked paths, selectors, migrations, fixtures "
        "(AUG-126: the obligation validator, previously unreachable from the CLI)",
    )
    chk.add_argument(
        "root",
        nargs="?",
        help="spec root directory; every *.xml below it is validated. Optional when "
        "--spec-root is given or the verb runs as the staged-diff checker (--diff-stdin)",
    )
    _add_spec_repo_root(chk)
    _add_spec_checker_flags(chk)
    chk.set_defaults(fn=cmd_spec)

    bnd = spec_sub.add_parser(
        "bundle",
        help="emit one requirement's verification bundle as JSON "
        "(AUG-126: previously unreachable from the CLI)",
    )
    bnd.add_argument("root")
    bnd.add_argument("requirement", help="requirement id to assemble")
    _add_spec_repo_root(bnd)
    bnd.set_defaults(fn=cmd_spec)

    aln = spec_sub.add_parser(
        "align",
        help="the staged-diff checker GitReins invokes: grade a patch against the corpus and "
        "the ch:trace doctrine, and emit one machine-readable JSON line (AUG-120/AUG-135)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read a staged unified diff and report which spec blocks scope it, whether it carries "
            "a ch:trace marker whose values resolve, and whether the sections it cites are still "
            "active. Reads only: nothing under the repository is written.\n\n"
            "The caller shape is GitReins' (engine.spec_alignment): --repo-root, --spec-root, "
            "--protocol-version and --diff-stdin. The payload is ONE JSON line carrying protocol, "
            "version, status (pass|warn|fail), scope (scoped|unscoped|empty), referenced "
            "ids/blocks and findings; a protocol version this checker does not speak is refused "
            "with a typed error on stderr and no payload at all."
        ),
        epilog=(
            "exit codes:\n"
            "  0  pass, or warn (advisory: the caller applies its own policy)\n"
            "  1  fail — at least one finding has severity fail\n"
            "  2  a typed refusal (spec error (<code>): unsupported protocol version, a diff over "
            "the cap, no diff source, an unresolvable repository root, an invalid corpus)\n"
        ),
    )
    _add_spec_repo_root(aln)
    _add_spec_checker_flags(aln)
    aln.set_defaults(fn=cmd_spec_align)

    tpk = spec_sub.add_parser(
        "task-packet",
        help="emit ONE task's exact scope packet, its approved digest and its ch:trace marker "
        "(AUG-121)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Build the packet for one authored <task> block: stable identity, lifecycle and "
            "verification method, the in-scope blocks, the acceptance criteria, and the excluded "
            "blocks WITH the reason each was excluded. Deterministic bytes; `digest` is the sha256 "
            "of the canonical packet and is what an approval is bound to.\n\n"
            "This is corpus-wide: a document that is not well-formed refuses the whole run, "
            "because a packet built from half a corpus is not the approved plan."
        ),
    )
    tpk.add_argument("root", help="spec root directory")
    tpk.add_argument("--task", required=True, metavar="ID", help="the <task> block id")
    tpk.set_defaults(fn=cmd_spec)

    tex = spec_sub.add_parser(
        "task-export",
        help="write ONE approved task packet to the canonical board through boardctl (AUG-121)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Export one task packet to the canonical JSONL board. The export is APPROVAL-BOUND: "
            "the plan digest must match the packet's current digest (`--plan-digest`, else the "
            "task block's own plan-digest=). With no approval, or a stale one, boardctl is not "
            "invoked at all and both canonical files stay byte-identical.\n\n"
            "The exporter issues boardctl `create` and `show` only: no scheduler dispatch, no task "
            "completion, no board update. An existing row with identical scope is reported as "
            "`unchanged`; a differing row is a conflict and is refused."
        ),
        epilog=(
            "exit codes:\n"
            "  0  exported, or unchanged (one row per task id)\n"
            "  2  a typed refusal (unapproved/stale/invalid plan digest, no task block, a bad "
            "corpus, a board conflict); nothing was written\n"
        ),
    )
    tex.add_argument("root", help="spec root directory")
    tex.add_argument("--task", required=True, metavar="ID", help="the <task> block id")
    tex.add_argument(
        "--board", required=True, metavar="DIR", help="board repo or board dir"
    )
    tex.add_argument(
        "--plan-digest",
        metavar="SHA256",
        help="the approved plan digest (default: the task block's plan-digest= attribute)",
    )
    tex.add_argument(
        "--boardctl",
        metavar="PATH",
        help=f"the boardctl binary to run (default: {BOARDCTL_BINARY} on PATH)",
    )
    tex.set_defaults(fn=cmd_spec)

    ctx = spec_sub.add_parser(
        "context",
        help="one query: the minimal XML context for a task, a code path or a symbol (AUG-122)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Answer with ONLY the blocks that govern the query: the task and its in-scope blocks "
            "plus their declared closure, or the block a `ch:trace spec=` reference names. "
            "Out-of-scope blocks are listed explicitly WITH their exclusion reasons.\n\n"
            "Never a whole-document or whole-corpus dump: a query with no trace, an unknown "
            "symbol, an inactive block or an ambiguous match answers with a structured "
            "unscoped/inactive/ambiguous result and an empty selection. The response reports "
            "selected count, dependency count, input bytes and output bytes."
        ),
        epilog=(
            "exit codes:\n"
            "  0  an answer (including an unscoped/ambiguous/inactive answer — that is a result)\n"
            "  2  a typed refusal (no selector, two selectors, a bad corpus)\n"
        ),
    )
    ctx.add_argument("root", help="spec root directory")
    ctx.add_argument("--task", metavar="ID", help="resolve a <task> block to its scope")
    ctx.add_argument(
        "--code", metavar="PATH", help="resolve a source file's ch:trace reference"
    )
    ctx.add_argument(
        "--symbol",
        metavar="NAME",
        help="resolve the source file that DEFINES this symbol",
    )
    ctx.add_argument(
        "--search-root",
        metavar="DIR",
        help="where --symbol looks for definitions (default: --repo-root, else the spec root)",
    )
    _add_spec_repo_root(ctx)
    ctx.set_defaults(fn=cmd_spec)

    pln = spec_sub.add_parser(
        "plan-draft",
        help="emit the versioned DRAFT plan for a set of tasks: ids, criteria, dependencies, "
        "anchors, expected evidence and the plan digest (AUG-119)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "A plan is many tasks with ONE version and ONE digest. Membership is authored on the "
            "task (`plan=` + `plan-version=`), so a plan cannot grow by dropping a file into a "
            "directory. The digest is the sha256 of the canonical plan and is what an approval is "
            "bound to; `spec plan-export` refuses to write a board row without it.\n\n"
            "Read-only: drafting a plan touches nothing."
        ),
    )
    pln.add_argument("root", help="spec root directory")
    pln.add_argument(
        "--plan", required=True, metavar="ID", help="the plan id tasks carry in `plan=`"
    )
    pln.set_defaults(fn=cmd_spec)

    pap = spec_sub.add_parser(
        "plan-approve",
        help="record an approval (or a rejection) BOUND to a plan's version and digest (AUG-119)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Append one decision to the plan approval ledger (default: `<spec root>/.auger/"
            "plan-approvals.jsonl`). The digest must be the plan's CURRENT digest, so nobody can "
            "approve a plan they are not looking at. The ledger is append-only and the LATEST "
            "record for a plan is the decision: a rejection is a new fact, never an edit.\n\n"
            "Exit codes:\n"
            "  0  the decision was recorded and read back\n"
            "  2  a typed refusal (stale/invalid digest, missing approver, unwritable ledger)\n"
        ),
    )
    pap.add_argument("root", help="spec root directory")
    pap.add_argument("--plan", required=True, metavar="ID", help="the plan id")
    decision = pap.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true", help="approve this digest")
    decision.add_argument(
        "--reject", dest="approve", action="store_false", help="reject it"
    )
    pap.add_argument(
        "--digest",
        required=True,
        metavar="SHA256",
        help="the plan digest being decided on",
    )
    pap.add_argument(
        "--approver", required=True, metavar="NAME", help="who is deciding"
    )
    pap.add_argument(
        "--ledger",
        metavar="DIR",
        help="approval ledger directory (default: <spec root>/.auger)",
    )
    pap.add_argument(
        "--at",
        metavar="TIMESTAMP",
        help="when the decision was made (recorded verbatim)",
    )
    pap.set_defaults(fn=cmd_spec)

    pex = spec_sub.add_parser(
        "plan-export",
        help="write every row of an APPROVED plan through boardctl, read them all back (AUG-119)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Export a whole approved plan. The approval is checked BEFORE any boardctl call, so an "
            "unapproved, rejected or stale plan cannot move a byte of the canonical board.\n\n"
            "Per row: `boardctl create` when the row does not exist, `boardctl update` when only the "
            "title/acceptance text drifted, `unchanged` when the row already equals the approved "
            "input. Every write is read back with `boardctl show` and compared field by field. A row "
            "whose SCOPE differs is a conflict and is refused: an export never overwrites a row "
            "behind its owner.\n\n"
            "Fail-stop: the first failing row ends the run with status `partial`, exit 1, the "
            "outcome of every row that DID land, the boardctl error, and a recovery record naming "
            "the resume path (re-running the same approved plan completes it).\n\n"
            "Exit codes:\n"
            "  0  exported, or unchanged\n"
            "  1  PARTIAL — at least one row landed, one failed; nothing was reported as complete\n"
            "  2  a typed refusal (no approval / rejected / stale / invalid digest, a bad corpus, a "
            "scope conflict); nothing was written\n"
        ),
    )
    pex.add_argument("root", help="spec root directory")
    pex.add_argument("--plan", required=True, metavar="ID", help="the plan id")
    pex.add_argument(
        "--board", required=True, metavar="DIR", help="board repo or board dir"
    )
    pex.add_argument("--ledger", metavar="DIR", help="approval ledger directory")
    pex.add_argument(
        "--plan-digest",
        metavar="SHA256",
        help="an out-of-band approval digest to ALSO require (must equal the ledger's and the plan's)",
    )
    pex.add_argument(
        "--boardctl",
        metavar="PATH",
        help=f"the boardctl binary (default: {BOARDCTL_BINARY})",
    )
    pex.add_argument("--out", metavar="PATH", help="write the run record here (atomic)")
    pex.set_defaults(fn=cmd_spec)

    prc = spec_sub.add_parser(
        "plan-reconcile",
        help="copy board status/commit/verdict evidence into Auger, read-only (AUG-119)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Reconcile a plan's canonical rows back into Auger. READ-ONLY: the only external command "
            "is `boardctl show`, the record says `writes: none`, and no board byte moves. A commit "
            "that does not exist in `--repo-root` and a verdict naming another commit are refused "
            "rather than recorded as if they bound; absent evidence carries `none:<reason>`.\n\n"
            "Exit codes:\n"
            "  0  a reconciliation record was produced\n"
            "  2  a typed refusal (unresolved commit, verdict/commit mismatch, bad corpus)\n"
        ),
    )
    prc.add_argument("root", help="spec root directory")
    prc.add_argument("--plan", required=True, metavar="ID", help="the plan id")
    prc.add_argument(
        "--board", required=True, metavar="DIR", help="board repo or board dir"
    )
    prc.add_argument(
        "--boardctl",
        metavar="PATH",
        help=f"the boardctl binary (default: {BOARDCTL_BINARY})",
    )
    _add_spec_repo_root(prc)
    prc.add_argument(
        "--verdict-root",
        metavar="DIR",
        help="directory of <row id>.json verdict records to bind (optional)",
    )
    prc.add_argument("--out", metavar="PATH", help="write the record here (atomic)")
    prc.set_defaults(fn=cmd_spec)

    jpk = spec_sub.add_parser(
        "judge-packet",
        help="assemble the GitReins packet for one APPROVED board row (AUG-124)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Produce the packet `engine.auger_packet.TaskPacketAdapter` consumes, from the APPROVED "
            "plan: the board id (plus an explicit id mapping), the resolved `ch:trace` ids, the in/out "
            "of-scope references, the exact test obligations, the board acceptance criteria VERBATIM, "
            "and the digests that were approved. The canonical row must still carry the approved "
            "packet, and the marker must resolve to this row, this task and its own obligations — "
            "anything else is refused with the offending ids named, and no packet is emitted.\n\n"
            "Exit codes:\n"
            "  0  the packet was written (path on stdout)\n"
            "  2  a typed refusal naming the invalid row/spec/test ids\n"
        ),
    )
    jpk.add_argument("root", help="spec root directory")
    jpk.add_argument("--plan", required=True, metavar="ID", help="the plan id")
    jpk.add_argument(
        "--row", required=True, metavar="ID", help="the canonical board row id"
    )
    jpk.add_argument(
        "--board", required=True, metavar="DIR", help="board repo or board dir"
    )
    jpk.add_argument("--ledger", metavar="DIR", help="approval ledger directory")
    jpk.add_argument(
        "--boardctl",
        metavar="PATH",
        help=f"the boardctl binary (default: {BOARDCTL_BINARY})",
    )
    _add_spec_repo_root(jpk)
    jpk.add_argument(
        "--trace",
        metavar="MARKER",
        help="validate THIS ch:trace marker instead of the one bound into the approved row",
    )
    jpk.add_argument(
        "--worker-summary",
        metavar="TEXT",
        help="the worker's own summary, carried as untrusted context",
    )
    jpk.add_argument("--out", metavar="PATH", help="write the packet here (atomic)")
    jpk.set_defaults(fn=cmd_spec)

    jhd = spec_sub.add_parser(
        "judge-handoff",
        help="validate the boardctl→GitReins binding and start the lifecycle ONCE, or refuse "
        "(AUG-124)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "The handoff. The packet is validated FIRST (row, trace, scope, obligations, digests); an "
            "invalid binding refuses with the offending ids and starts NOTHING. A valid binding calls "
            "the dispatch command exactly once, with the packet path appended, carrying the board's "
            "acceptance criteria verbatim. Without `--dispatch-cmd` the verb REFUSES "
            "(`dispatch-unconfigured`) instead of pretending to have started a judge.\n\n"
            "Auger never dispatches a BOARD task from here: this seam hands a packet to a judge and "
            "writes only the packet file.\n\n"
            "Exit codes:\n"
            "  0  the lifecycle was started exactly once\n"
            "  2  a typed refusal — no dispatch, no judge, no partial handoff\n"
        ),
    )
    jhd.add_argument("root", help="spec root directory")
    jhd.add_argument("--plan", required=True, metavar="ID", help="the plan id")
    jhd.add_argument(
        "--row", required=True, metavar="ID", help="the canonical board row id"
    )
    jhd.add_argument(
        "--board", required=True, metavar="DIR", help="board repo or board dir"
    )
    jhd.add_argument("--ledger", metavar="DIR", help="approval ledger directory")
    jhd.add_argument(
        "--boardctl",
        metavar="PATH",
        help=f"the boardctl binary (default: {BOARDCTL_BINARY})",
    )
    _add_spec_repo_root(jhd)
    jhd.add_argument(
        "--trace",
        metavar="MARKER",
        help="validate THIS ch:trace marker instead of the one bound into the approved row",
    )
    jhd.add_argument(
        "--worker-summary",
        metavar="TEXT",
        help="the worker's own summary, carried as untrusted context",
    )
    jhd.add_argument(
        "--dispatch-cmd",
        metavar="COMMAND",
        help="the judge lifecycle command; the packet path is appended as its last argument",
    )
    jhd.add_argument("--out", metavar="PATH", help="write the packet here (atomic)")
    jhd.set_defaults(fn=cmd_spec)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
