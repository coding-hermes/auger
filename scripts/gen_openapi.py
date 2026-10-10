#!/usr/bin/env python3
"""Generate docs/openapi.yaml — the OpenAPI 3.x contract muster consumes (AUG-066).

One source of truth: this script IS auger's HTTP operation registry. It imports
`auger.py` and reads the facts the contract must not invent — the version, the
embedding route shapes, and the limit bounds — then emits a deterministic YAML
document (same code in, same bytes out; parity is gated in tests/gate.sh and
tests/test_openapi_parity.py).

The described surface is the scope decision frozen in docs/muster-scope.md
(AUG-065): a NEW, separately versioned, read-first HTTP API on
http://127.0.0.1:8768 — deliberately distinct from the existing embedding-only
`auger serve` on :8765, which stays untouched. Every operation carries
`x-safety: read-only`. Mutating verbs (init, start, bundle, ask, answer,
toggle, propagate, feedback, record, and verdict's recording forms) are absent
from this contract BY DESIGN: absence of an approval gate means the operation
is omitted, not merely labelled (scope doc, "Operation boundary").

Usage:
    python3 scripts/gen_openapi.py                # spec on stdout
    python3 scripts/gen_openapi.py > docs/openapi.yaml
    python3 scripts/gen_openapi.py --output docs/openapi.yaml

Stdlib + PyYAML only (pyyaml is available to the gate's python3; it parses the
emitted document as a final self-check before writing).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

#: The new API's base URL. Absolute on purpose: muster dereferences the spec
#: standalone when --base-url is omitted, and a relative servers url cannot be
#: dereferenced at all (AUG-065 scope doc, "Consumer configuration").
API_BASE_URL = auger.AUGER_API_BASE_URL

#: Contract version of THIS document (the frozen AUG-066 surface). It is the
#: contract's own semver, not auger's CLI version — which rides alongside as
#: `x-auger-cli-version` for traceability back to the code that generated it.
CONTRACT_VERSION = "0.1.0"

#: The namespace path parameter, shared by every /api/ns route.
NS_PARAM = {
    "name": "namespace",
    "in": "path",
    "required": True,
    "description": (
        "The DuckBrain namespace the rows live in — the name `auger -n` takes. "
        "auger addresses auger concepts; the DuckBrain substrate API itself is "
        "not exposed."
    ),
    "schema": {"type": "string", "minLength": 1},
}

#: Optional project context, the `-p/--project-id` of the matching CLI verb.
PROJECT_PARAM = {
    "name": "project_id",
    "in": "query",
    "required": False,
    "description": (
        "Project context (the CLI's -p/--project-id). When omitted the newest "
        "project row is used; a namespace holding several projects without an "
        "explicit id is refused with 409, exactly as the CLI refuses."
    ),
    "schema": {"type": "string"},
}

RECALL_LIMIT_PARAM = {
    "name": "limit",
    "in": "query",
    "required": False,
    "description": "Maximum hits to return (1.."
    + str(auger.EMBEDDINGS_MAX_LIMIT)
    + ").",
    "schema": {
        "type": "integer",
        "minimum": 1,
        "maximum": auger.EMBEDDINGS_MAX_LIMIT,
        "default": auger.EMBEDDINGS_DEFAULT_LIMIT,
    },
}

Q_PARAM = {
    "name": "q",
    "in": "query",
    "required": True,
    "description": "The query text. Blank queries are refused with 400.",
    "schema": {"type": "string", "minLength": 1},
}

PROJECT_QUERY_PARAM = {
    "name": "project",
    "in": "query",
    "required": False,
    "description": "Restrict hits to one project id (optional).",
    "schema": {"type": "string"},
}

#: The search route template — must keep matching the route `auger serve`
#: already implements (`auger.EMBEDDINGS_SEARCH_ROUTE`); the parity test
#: proves the template against that regex.
SEARCH_PATH = "/api/ns/{namespace}/embeddings/search"

ERROR_REF = {"$ref": "#/components/schemas/Error"}


def _json_response(description: str, schema: dict) -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }


def _text_response(description: str) -> dict:
    return {
        "description": description,
        "content": {"text/plain": {"schema": {"type": "string"}}},
    }


def _err(description: str) -> dict:
    return _json_response(description, ERROR_REF)


def _op(
    operation_id: str,
    summary: str,
    description: str,
    responses: dict,
    *,
    method: str = "get",
    external: list[str] | None = None,
    parameters: list[dict] | None = None,
    request_body: dict | None = None,
    cli: str | None = None,
) -> dict:
    """One registry entry. `x-safety` is mandatory on EVERY operation: muster
    and the parity test both read it, and an operation without the extension
    must not be generable at all."""
    op: dict = {
        "operationId": operation_id,
        "summary": summary,
        "description": description,
        "x-safety": "read-only",
    }
    if external:
        # Disclosed per the scope contract: these operations can incur
        # external cost; they remain read-only with respect to auger's rows.
        op["x-external-calls"] = external
    if parameters:
        op["parameters"] = parameters
    if request_body:
        op["requestBody"] = request_body
    op["responses"] = responses
    if cli:
        op["x-cli"] = cli
    return op


#: The exact read-only registry this script must emit, as (method, path,
#: operationId). The self-check compares the assembled document against this
#: list — an operation added without a registry line (or one drifting) fails
#: before a byte is written. `verdicts` here is the LIST form only; recording
#: a verdict is a mutating verb and stays out of both the registry and the doc.
READ_REGISTRY = (
    ("get", auger.EMBEDDINGS_HEALTH_ROUTE, "getHealth"),
    ("get", "/api/ns/{namespace}/status", "getNamespaceStatus"),
    ("get", "/api/ns/{namespace}/dump", "getNamespaceDump"),
    ("get", "/api/ns/{namespace}/export", "exportNamespace"),
    ("post", "/api/ns/{namespace}/check", "checkNamespaceEvidence"),
    ("get", "/api/ns/{namespace}/recall", "recallNamespace"),
    ("get", SEARCH_PATH, "searchNamespaceEmbeddings"),
    ("get", "/api/ns/{namespace}/verdicts", "listNamespaceVerdicts"),
)

#: CLI verbs that are mutating (scope doc classification table). A path SEGMENT
#: equal to one of these is a leak; the plural `verdicts` collection (the list
#: form) is deliberately not a segment match.
MUTATING_VERBS = (
    "init",
    "start",
    "bundle",
    "ask",
    "answer",
    "toggle",
    "propagate",
    "feedback",
    "record",
    "verdict",
)


def build_spec() -> dict:
    """Assemble the whole document. Order here is byte-order in the output."""
    health_route = auger.EMBEDDINGS_HEALTH_ROUTE
    paths = {
        health_route: {
            "get": _op(
                "getHealth",
                "Substrate embedding status",
                "The health/status read backing every retrieval tier label: "
                "whether the substrate's embedding leg is healthy, and which "
                "provider/model answered. Advisory by design — a degraded or "
                'unanswerable substrate is reported as `tier: "unknown"` with '
                "null fields, never as an HTTP error, because the label exists "
                "to remove ambiguity, not to gate reads on it.",
                {
                    "200": _json_response(
                        "The substrate's own statement about its embedding leg.",
                        {"$ref": "#/components/schemas/HealthStatus"},
                    )
                },
                cli="auger -n <namespace> status  # tier labels come from this read",
            )
        },
        "/api/ns/{namespace}/status": {
            "get": _op(
                "getNamespaceStatus",
                "Confidence map and coverage for one namespace",
                "The `status` verb as data: project identity, register row "
                "counts, the confidence summary, which decisions are thin "
                "(below the drill threshold or unmeasured), pending-memory "
                "warnings, and the 44-domain grid coverage. Reads only; "
                "nothing is written and no external model is called. Fails "
                "closed: a namespace with no project row is 404, a namespace "
                "with several and no explicit project_id is 409.",
                {
                    "200": _json_response(
                        "The confidence map and coverage report.",
                        {"$ref": "#/components/schemas/NamespaceStatus"},
                    ),
                    "404": _err(
                        "No project row in this namespace — run the "
                        "`start` verb from the CLI first."
                    ),
                    "409": _err(
                        "Several project rows and no explicit project_id: "
                        "implicit selection is refused, nothing is guessed."
                    ),
                    "502": _err("The DuckBrain substrate refused a read."),
                },
                parameters=[NS_PARAM, PROJECT_PARAM],
                cli="auger -n <namespace> status",
            )
        },
        "/api/ns/{namespace}/dump": {
            "get": _op(
                "getNamespaceDump",
                "Render the namespace's configuration",
                "The `dump` verb's render: the stored active configuration, or "
                "a hypothetical one under `config` overrides. Nothing is "
                "written in either mode. The CLI's `--out` FILE form is "
                "deliberately absent — writing a local file is not read-only "
                "(scope contract), so the HTTP surface renders to the response "
                "body only.",
                {
                    "200": _text_response(
                        "The rendered configuration document (text/plain)."
                    ),
                    "400": _err(
                        "A config entry is not a decision=option pair, or "
                        "names an unknown/ambiguous decision or option — "
                        "nothing is rendered."
                    ),
                    "404": _err("No project row in this namespace."),
                    "409": _err("Several project rows and no explicit project_id."),
                    "502": _err("The DuckBrain substrate refused a read."),
                },
                parameters=[
                    NS_PARAM,
                    PROJECT_PARAM,
                    {
                        "name": "config",
                        "in": "query",
                        "required": False,
                        "description": (
                            "Hypothetical decision=option override, repeatable "
                            "(the CLI's repeatable --config D-001=O2). Option "
                            "tokens accept a full id, label, or bare index; "
                            "malformed or ambiguous entries are refused before "
                            "anything is rendered."
                        ),
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                ],
                cli="auger -n <namespace> dump [--config D-001=O2 ...]",
            )
        },
        "/api/ns/{namespace}/export": {
            "get": _op(
                "exportNamespace",
                "Export every declared table as one deterministic document",
                "The `export` verb: a read-only, deterministic rendering of "
                "every declared table in the namespace (whole namespace by "
                "contract — the CLI's project context is not a filter here, "
                "and the HTTP surface exposes no filter parameter for it).",
                {
                    "200": _text_response(
                        "The namespace export document (text/plain)."
                    ),
                    "404": _err("The namespace does not exist."),
                    "502": _err("The DuckBrain substrate refused a read."),
                },
                parameters=[NS_PARAM],
                cli="auger -n <namespace> export",
            )
        },
        "/api/ns/{namespace}/check": {
            "post": _op(
                "checkNamespaceEvidence",
                "Is this question already answered by stored evidence?",
                "The `check` verb: retrieve the nearest stored evidence for a "
                "question, then ask JEV (the external decisions model) whether "
                "the evidence already answers it. READ-ONLY with respect to "
                "auger's records, but it makes an EXTERNAL model call and can "
                "incur cost — disclosed here by contract. Fail-closed twice "
                "over: a memory store that cannot be asked is 503 (never "
                "'treat as a new question'), and a JEV that cannot be reached "
                "yields verdict UNKNOWN with jev_used false — an unjudged "
                "check must never look like a judged one.",
                {
                    "200": _json_response(
                        "The retrieved evidence and the verdict over it.",
                        {"$ref": "#/components/schemas/CheckResult"},
                    ),
                    "400": _err("The question text is blank."),
                    "503": _err(
                        "The memory store could not be asked (fail-closed: "
                        "unaskable is not the same as empty)."
                    ),
                },
                method="post",
                external=["JEV decisions model (OpenRouter)"],
                parameters=[NS_PARAM],
                request_body={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/CheckRequest"}
                        }
                    },
                },
                cli='auger -n <namespace> check "<question>" [--limit N]',
            )
        },
        "/api/ns/{namespace}/recall": {
            "get": _op(
                "recallNamespace",
                "Semantic search over the namespace's memory",
                "The `recall` verb as data: ranked hits from the namespace's "
                "embedded memory, each labelled with the retrieval tier the "
                "substrate answered on (semantic vs lexical — the two scales "
                "are not comparable, so the tier travels with the scores). "
                "Read-only for auger's rows; it queries the embedding/search "
                "provider through the DuckBrain substrate and can incur "
                "external cost. Fail-closed: a store that cannot be asked is "
                "503 — 'unaskable' and 'empty' are never the same answer.",
                {
                    "200": _json_response(
                        "Ranked hits with the retrieval tier they were scored on.",
                        {"$ref": "#/components/schemas/RecallResult"},
                    ),
                    "400": _err("q is blank, or limit is not an integer in range."),
                    "503": _err("The memory store could not be asked (fail-closed)."),
                },
                external=["embedding/search provider via the DuckBrain substrate"],
                parameters=[NS_PARAM, Q_PARAM, RECALL_LIMIT_PARAM, PROJECT_QUERY_PARAM],
                cli='auger -n <namespace> recall "<query>" [--limit N]',
            )
        },
        SEARCH_PATH: {
            "get": _op(
                "searchNamespaceEmbeddings",
                "Embedding search with hit classification (the served route)",
                "The embedding search `auger serve` already publishes (AUG-046), "
                "addressed per namespace: ranked hits where each hit's stored "
                "key is classified — `seed` (the project seed), `decision` "
                "(with the decision id it is evidence of), or `memory` (another "
                "writer's row in a shared namespace, never guessed into an "
                "auger concept). Read-only for auger's rows; queries the "
                "embedding/search provider through the DuckBrain substrate and "
                "can incur external cost. A store that cannot be asked is 502 "
                "in the substrate's own words; a store that genuinely holds "
                "nothing is 200 with count 0.",
                {
                    "200": _json_response(
                        "Ranked, tier-labelled, classified hits.",
                        {"$ref": "#/components/schemas/EmbeddingSearchResult"},
                    ),
                    "400": _err("q is blank, or limit is not an integer in range."),
                    "502": _err(
                        "The substrate refused the search (fail-closed: "
                        "unaskable is not the same as empty)."
                    ),
                },
                external=["embedding/search provider via the DuckBrain substrate"],
                parameters=[NS_PARAM, Q_PARAM, RECALL_LIMIT_PARAM, PROJECT_QUERY_PARAM],
                cli=(
                    "curl '"
                    + API_BASE_URL
                    + SEARCH_PATH.replace("{namespace}", "<namespace>")
                    + "?q=<query>&limit=<n>'"
                ),
            )
        },
        "/api/ns/{namespace}/verdicts": {
            "get": _op(
                "listNamespaceVerdicts",
                "Every recorded verdict, newest first",
                "The register's reading half — the CLI's `verdict --list` as "
                "data. Only the LIST is exposed: recording a verdict (the "
                "CLI's --good/--bad/--ask-jev forms) writes a verdict row and "
                "is a mutating operation, so it is absent from this contract "
                "until an approval gate exists.",
                {
                    "200": _json_response(
                        "The verdict register, newest first (empty array when "
                        "nothing is recorded).",
                        {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/VerdictRow"},
                        },
                    ),
                    "502": _err("The DuckBrain substrate refused a read."),
                },
                parameters=[NS_PARAM],
                cli="auger -n <namespace> verdict --list",
            )
        },
    }

    schemas = {
        "HealthStatus": {
            "type": "object",
            "description": (
                "The substrate's advisory embedding status. Any field except "
                "`tier` can be null when the substrate did not answer."
            ),
            "required": ["tier", "embedding_healthy", "provider", "model"],
            "properties": {
                "tier": {
                    "type": "string",
                    "enum": ["semantic", "lexical", "unknown"],
                    "description": (
                        "Which ranker a scored read used: semantic (embedding "
                        "index), lexical (keyword fallback), or unknown (the "
                        "substrate's health read did not answer)."
                    ),
                },
                "embedding_healthy": {
                    "type": "boolean",
                    "nullable": True,
                },
                "provider": {"type": "string", "nullable": True},
                "model": {"type": "string", "nullable": True},
            },
        },
        "Counts": {
            "type": "object",
            "required": [
                "decisions",
                "options",
                "escalations",
                "unknowns",
                "domains",
            ],
            "properties": {
                name: {"type": "integer"}
                for name in (
                    "decisions",
                    "options",
                    "escalations",
                    "unknowns",
                    "domains",
                )
            },
        },
        "ConfidenceSummary": {
            "type": "object",
            "required": ["min", "mean", "max"],
            "properties": {
                "min": {"type": "number"},
                "mean": {"type": "number"},
                "max": {"type": "number"},
            },
        },
        "ThinDecision": {
            "type": "object",
            "description": (
                "A decision below the confidence threshold or unmeasured — "
                "what the `ask` verb would drill next."
            ),
            "required": ["id", "confidence", "chosen"],
            "properties": {
                "id": {"type": "string"},
                "confidence": {
                    "type": "number",
                    "nullable": True,
                    "description": "null when the decision is unmeasured.",
                },
                "chosen": {"type": "string"},
            },
        },
        "NamespaceStatus": {
            "type": "object",
            "required": [
                "namespace",
                "project_id",
                "name",
                "status",
                "counts",
            ],
            "properties": {
                "namespace": {"type": "string"},
                "project_id": {"type": "string"},
                "name": {"type": "string", "nullable": True},
                "status": {"type": "string", "nullable": True},
                "counts": {"$ref": "#/components/schemas/Counts"},
                "confidence": {"$ref": "#/components/schemas/ConfidenceSummary"},
                "pending_memory_warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Decisions whose memory is provisional: stored but "
                        "excluded from every aggregate until it lands."
                    ),
                },
                "thin_decisions": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ThinDecision"},
                },
            },
        },
        "CheckRequest": {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The question under consideration.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "default": auger.EMBEDDINGS_DEFAULT_LIMIT,
                    "description": "How much stored evidence to retrieve.",
                },
            },
        },
        "RecallHit": {
            "type": "object",
            "required": ["key", "score", "content"],
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "The store key — for auger's own rows "
                        "`/auger/<project>/<thing>`; anything else is another "
                        "writer's row."
                    ),
                },
                "score": {
                    "type": "number",
                    "nullable": True,
                    "description": "Similarity rank on the labelled tier's scale.",
                },
                "content": {"type": "string"},
                "timestamp": {"type": "string", "nullable": True},
            },
        },
        "RecallResult": {
            "type": "object",
            "required": [
                "namespace",
                "query",
                "limit",
                "project",
                "tier",
                "count",
                "hits",
            ],
            "properties": {
                "namespace": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "project": {"type": "string", "nullable": True},
                "tier": {
                    "type": "string",
                    "enum": ["semantic", "lexical", "unknown"],
                },
                "count": {"type": "integer"},
                "hits": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/RecallHit"},
                },
            },
        },
        "EmbeddingHit": {
            "type": "object",
            "required": [
                "key",
                "kind",
                "decision_id",
                "score",
                "domain",
                "content",
                "snippet",
                "timestamp",
            ],
            "properties": {
                "key": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["seed", "decision", "memory"],
                    "description": (
                        "Read from the key's own shape: the project seed "
                        "(`/auger/<project>/seed`), a decision's evidence "
                        "(`/auger/<project>/<decision-id>`), or another "
                        "writer's row (`memory`)."
                    ),
                },
                "decision_id": {
                    "type": "string",
                    "nullable": True,
                    "description": "Set exactly when kind is `decision`.",
                },
                "score": {"type": "number", "nullable": True},
                "domain": {"type": "string", "nullable": True},
                "content": {"type": "string", "nullable": True},
                "snippet": {"type": "string", "nullable": True},
                "timestamp": {"type": "string", "nullable": True},
            },
        },
        "EmbeddingSearchResult": {
            "type": "object",
            "required": [
                "namespace",
                "query",
                "limit",
                "project",
                "tier",
                "embedding_healthy",
                "count",
                "results",
            ],
            "properties": {
                "namespace": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "project": {"type": "string", "nullable": True},
                "tier": {
                    "type": "string",
                    "enum": ["semantic", "lexical", "unknown"],
                },
                "embedding_healthy": {"type": "boolean", "nullable": True},
                "count": {"type": "integer"},
                "results": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/EmbeddingHit"},
                },
            },
        },
        "CheckResult": {
            "type": "object",
            "required": [
                "namespace",
                "question",
                "tier",
                "hits",
                "verdict",
                "verdict_decided_by",
                "jev_used",
                "jev_cost",
            ],
            "properties": {
                "namespace": {"type": "string"},
                "question": {"type": "string"},
                "tier": {
                    "type": "string",
                    "enum": ["semantic", "lexical", "unknown"],
                },
                "hits": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/RecallHit"},
                },
                "verdict": {
                    "type": "string",
                    "enum": ["ALREADY ANSWERED", "NOT YET ANSWERED", "UNKNOWN"],
                    "description": (
                        "UNKNOWN is the fail-closed answer: JEV was "
                        "unreachable, and an unjudged check must never read "
                        "as a judged one."
                    ),
                },
                "verdict_decided_by": {
                    "type": "string",
                    "enum": ["score", "record", "fail-closed"],
                    "description": (
                        "Which decider produced the verdict: the JEV score, "
                        "the stored question record (inside the score-drift "
                        "band), or neither because JEV was unreachable."
                    ),
                },
                "jev_used": {"type": "boolean"},
                "jev_cost": {
                    "type": "string",
                    "nullable": True,
                    "description": "The external call's reported cost, when JEV answered.",
                },
            },
        },
        "VerdictRow": {
            "type": "object",
            "required": [
                "id",
                "project_id",
                "config_summary",
                "verdict",
                "judged_by",
                "created_at",
            ],
            "properties": {
                "id": {"type": "string"},
                "project_id": {"type": "string"},
                "config_summary": {"type": "string"},
                "verdict": {"type": "string", "enum": ["good", "bad"]},
                "reasons": {"type": "string"},
                "judged_by": {"type": "string"},
                "confidence": {"type": "number", "nullable": True},
                "source": {"type": "string"},
                "note": {"type": "string"},
                "created_at": {"type": "string"},
            },
        },
        "Error": {
            "type": "object",
            "required": ["error"],
            "properties": {
                "error": {
                    "type": "string",
                    "description": "What was refused or failed, in one line.",
                }
            },
        },
    }

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Auger read-first API",
            "version": CONTRACT_VERSION,
            "x-auger-cli-version": auger.AUGER_VERSION,
            "description": (
                "The versioned, read-first HTTP API auger owns and muster "
                "consumes (AUG-066, the frozen contract AUG-067 implements). "
                "Generated artifact — regenerate with "
                "`python3 scripts/gen_openapi.py > docs/openapi.yaml`; the "
                "parity gates (tests/gate.sh, tests/test_openapi_parity.py) "
                "fail on drift from the code.\n\n"
                "Scope boundary: every operation is read-only with respect to "
                "auger's records (`x-safety: read-only`). Operations that "
                "create, modify, or judge records are absent from this "
                "contract entirely — not labelled, omitted — until an "
                "explicit approval boundary exists (AUG-065 scope decision). "
                "The DuckBrain substrate's own API is not exposed: consumers "
                "address auger concepts, and auger controls which substrate "
                "operations occur.\n\n"
                "External calls: `check` calls the JEV decisions model, and "
                "the retrieval operations query an embedding/search provider "
                "through the DuckBrain substrate; both are disclosed per "
                "operation via `x-external-calls` and can incur cost. These "
                "operations remain read-only for auger's records.\n\n"
                "Credentials stay server-side: no API key, header scheme, or "
                "credential path is part of this contract. The service binds "
                "loopback only. The base URL is deliberately distinct from "
                "the existing embedding-only `auger serve` default "
                "(http://127.0.0.1:8765).\n\n"
                "Fail-closed semantics: a store that cannot be asked is an "
                "error (502/503), never an empty success; a JEV that cannot "
                "be reached is verdict UNKNOWN, never a fabricated answer."
            ),
        },
        "servers": [{"url": API_BASE_URL}],
        "x-safety-policy": "read-only",
        "paths": paths,
        "components": {"schemas": schemas},
    }
    _self_check(spec)
    return spec


def _self_check(spec: dict) -> None:
    """Refuse to emit a malformed or drifting contract, before a byte is written.

    Four gates: (1) the emitted (method, path, operationId) set equals the
    declared READ_REGISTRY exactly — no phantom operation, no missing one;
    (2) every operation carries operationId + responses + x-safety
    read-only; (3) no mutating CLI verb appears as a path segment; (4) the
    servers url equals the contracted base URL.
    """
    emitted = []
    seen: set[str] = set()
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post"):
                raise SystemExit(
                    f"self-check: {method} on {path} is outside the read set"
                )
            for field in ("operationId", "responses", "x-safety"):
                if field not in op:
                    raise SystemExit(f"self-check: {method} {path} lacks {field}")
            if op["x-safety"] != "read-only":
                raise SystemExit(
                    f"self-check: {method} {path} is not classified read-only"
                )
            oid = op["operationId"]
            if oid in seen:
                raise SystemExit(f"self-check: duplicate operationId {oid}")
            seen.add(oid)
            emitted.append((method, path, oid))
    if sorted(emitted) != sorted(READ_REGISTRY):
        missing = sorted(set(READ_REGISTRY) - set(emitted))
        phantom = sorted(set(emitted) - set(READ_REGISTRY))
        raise SystemExit(
            "self-check: operation registry drifted — "
            f"missing={missing} phantom={phantom}"
        )
    for path, _item in spec["paths"].items():
        for segment in path.split("/"):
            if segment in MUTATING_VERBS:
                raise SystemExit(
                    f"self-check: mutating verb {segment!r} leaked into path {path}"
                )
    if not emitted:
        raise SystemExit("self-check: the registry is empty")
    if spec["servers"][0]["url"] != API_BASE_URL:
        raise SystemExit("self-check: servers url drifted from the contract")


def render(spec: dict) -> str:
    header = "\n".join(
        [
            "# GENERATED FILE — do not hand-edit (AUG-066).",
            "# Source of truth: auger.py's operation registry, via scripts/gen_openapi.py.",
            "# Regenerate:  python3 scripts/gen_openapi.py > docs/openapi.yaml",
            "# Drift gate:  tests/test_openapi_parity.py + the openapi-parity arm in tests/gate.sh.",
            "# Scope:       docs/muster-scope.md (AUG-065) — read-only surface; no writes, ever,",
            "#              until an explicit approval boundary exists.",
        ]
    )
    try:
        import yaml
    except ImportError:  # pragma: no cover - the gate's python3 has pyyaml
        raise SystemExit(
            "PyYAML is required to emit the spec (pip install pyyaml)"
        ) from None
    body = yaml.safe_dump(
        spec,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return header + "\n" + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate docs/openapi.yaml from auger's operation registry"
    )
    ap.add_argument(
        "--output",
        help="write the spec here instead of stdout",
        default=None,
    )
    args = ap.parse_args(argv)
    text = render(build_spec())
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output} ({len(text.encode())} bytes)", file=sys.stderr)
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
