# AUG-067 live Muster E2E — 2026-10-10

Host-local integration proof for the read-first API. The temporary fixture namespace is `aug067-muster-acceptance-20261010`; its seed is deliberately non-sensitive. Attempt 1 created the fixture with the CLI; this rework reused its existing project, seed, and verdict rows for the live route and Muster checks, then deleted it over the DuckBrain API after the evidence run.

## Port selection / collision evidence

At implementation time `ss -ltnp` showed `127.0.0.1:8766` bound to `off-by-one` (PID 3704923). A current foreign-service probe returned `{"status":"ok","uptime":"74h7m37s"}` (the earlier AUG-067 attempt captured `{"status":"ok","uptime":"73h18m15s"}`); neither response has auger's `HealthStatus` shape. Port 8767 is also not available: Crier's `/home/kara/crier/Makefile` declares its default server port as `:8767`. Attempt 1's API log showed its relay subscribe request returned 404 and relay publish request returned 501, confirming Crier bus traffic reached the wrong listener; the attempt-1 process was stopped before this rework. `8768` was verified free before the API was launched. The single source of truth is `AUGER_API_DEFAULT_PORT`/`AUGER_API_BASE_URL` in `auger.py`; the generator imports that value. A live bind-conflict probe against the already-running service exited 1 with `auger API cannot bind 127.0.0.1:8768: [Errno 98] Address already in use`. `:8765` remains the existing embedding-only `auger serve` port.

## Fixture setup

Prior fixture setup command (from attempt 1; fixture reused for this rework):

```bash
python3 auger.py -n aug067-muster-acceptance-20261010 init && python3 auger.py -n aug067-muster-acceptance-20261010 start --id P-MUSTER-ACCEPTANCE --name muster-acceptance --seed 'Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.' && python3 auger.py -n aug067-muster-acceptance-20261010 verdict --good 'safe live acceptance fixture' --reasons 'Explicit temporary live-proof record for the AUG-067 read-only API integration.'
```

Raw output:

```text
namespace aug067-muster-acceptance-20261010: created
declared: 14/14 tables -> assumption, break, bundle, bundle_member, decision, domain, edge, escalation, facet, option, project, question, unknown, verdict
wrote declarations: project, question, decision, option, break, escalation, assumption, unknown, domain, edge, facet, bundle, bundle_member, verdict
project P-MUSTER-ACCEPTANCE in namespace aug067-muster-acceptance-20261010
seed stored (112 chars) + keyword-searchable only (embedding.healthy=false)
V-000001 recorded  (verdict good, judged_by human)
config judged: safe live acceptance fixture
reasons: Explicit temporary live-proof record for the AUG-067 read-only API integration.
```

## Service launch

The script `/tmp/aug067_start_api.sh` contains `cd /home/kara/auger` followed by `exec python3 auger.py api --host 127.0.0.1`. Exact launch command:

```bash
bash /tmp/aug067_start_api.sh > /tmp/aug067_api.log 2>&1
```

It was run in the background.

## Raw curl route probes

These are the exact successful commands and raw responses captured against the running service on port 8768. The namespace contains a real project row, a seed memory, and a verdict row; recall/search return the seed. `check` reports the actual current retrieval tier and JEV result.

```text
$ curl -fsS http://127.0.0.1:8768/health
{"tier": "semantic", "embedding_healthy": true, "provider": "openai", "model": "qwen/qwen3-embedding-8b"}

$ curl -fsS http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/status
{"namespace": "aug067-muster-acceptance-20261010", "project_id": "P-MUSTER-ACCEPTANCE", "name": "muster-acceptance", "status": "open", "counts": {"decisions": 0, "options": 0, "escalations": 0, "unknowns": 0, "domains": 0}, "confidence": {"min": 0, "mean": 0, "max": 0}, "pending_memory_warnings": [], "thin_decisions": []}

$ curl -fsS http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/dump
# muster-acceptance — configuration dump
project P-MUSTER-ACCEPTANCE · namespace aug067-muster-acceptance-20261010 · generated 2026-10-10T18:33:51.439521+00:00
mode: current stored state
seed: Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.

---
ACTIVE CONFIGURATION: (nothing active)


$ curl -fsS http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/export
# Auger spec export — namespace aug067-muster-acceptance-20261010

Read-only deterministic rendering of every declared table.
Scope: whole namespace (no rows are written).

## Projects (1 rows)
### P-MUSTER-ACCEPTANCE
- id: P-MUSTER-ACCEPTANCE
- name: muster-acceptance
- seed: Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.
- core_statement: —
- status: open
- created_at: 2026-10-10T17:50:23.408766+00:00

## Domains (0 rows)
### (none stored)

## Questions (0 rows)
### (none stored)

## Decisions (0 rows)
### (none stored)

## Options (0 rows)
### (none stored)

## Break records (0 rows)
### (none stored)

## Escalations (0 rows)
### (none stored)

## Assumptions (0 rows)
### (none stored)

## Unknowns (0 rows)
### (none stored)

## Graph edges (0 rows)
### (none stored)

## Question facets (0 rows)
### (none stored)

## Bundles (0 rows)
### (none stored)

## Bundle members (0 rows)
### (none stored)

## Verdicts (1 rows)
### V-000001
- id: V-000001
- project_id: P-MUSTER-ACCEPTANCE
- config_summary: safe live acceptance fixture
- verdict: good
- reasons: Explicit temporary live-proof record for the AUG-067 read-only API integration.
- judged_by: human
- confidence: —
- source: human
- note: —
- created_at: 2026-10-10T17:50:23.865667+00:00

$ curl -fsS "http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/recall?q=read-only+consumer&limit=2"
{"namespace": "aug067-muster-acceptance-20261010", "query": "read-only consumer", "limit": 2, "project": null, "tier": "lexical", "count": 1, "hits": [{"key": "/auger/P-MUSTER-ACCEPTANCE/seed", "score": 1, "content": "Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.", "timestamp": "2026-10-10T17:50:23.470Z"}]}

$ curl -fsS "http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/embeddings/search?q=read-only+consumer&limit=2"
{"namespace": "aug067-muster-acceptance-20261010", "query": "read-only consumer", "limit": 2, "project": null, "tier": "lexical", "embedding_healthy": false, "count": 1, "results": [{"key": "/auger/P-MUSTER-ACCEPTANCE/seed", "kind": "seed", "decision_id": null, "score": 1, "domain": "concept", "content": "Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.", "snippet": "/auger/P-MUSTER-ACCEPTANCE/seed Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data. {}", "timestamp": "2026-10-10T17:50:23.470Z"}]}

$ curl -fsS http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/verdicts
[{"id": "V-000001", "project_id": "P-MUSTER-ACCEPTANCE", "config_summary": "safe live acceptance fixture", "verdict": "good", "reasons": "Explicit temporary live-proof record for the AUG-067 read-only API integration.", "judged_by": "human", "confidence": null, "source": "human", "note": "", "created_at": "2026-10-10T17:50:23.865667+00:00"}]

$ curl -fsS -X POST -H 'Content-Type: application/json' -d '{"question":"Is this a read-only API acceptance fixture?","limit":2}' http://127.0.0.1:8768/api/ns/aug067-muster-acceptance-20261010/check
{"namespace": "aug067-muster-acceptance-20261010", "question": "Is this a read-only API acceptance fixture?", "tier": "lexical", "hits": [{"key": "/auger/P-MUSTER-ACCEPTANCE/seed", "score": 1, "content": "Safe AUG-067 live integration fixture: Muster API consumer path is read-only and must return typed project data.", "timestamp": "2026-10-10T17:50:23.470Z"}], "verdict": "ALREADY ANSWERED", "verdict_decided_by": "score", "jev_used": true, "jev_cost": "1.4532e-05"}
```

## Fail-closed negative probe

Exact command and live response for an unknown namespace:

```text
$ curl -sS -i http://127.0.0.1:8768/api/ns/aug067-not-a-real-namespace-404/status
HTTP/1.1 404 Not Found
Server: auger-api/0.2 Python/3.11.15
Date: Sat, 10 Oct 2026 17:59:32 GMT
Content-Type: application/json
Content-Length: 97

{"error": "namespace 'aug067-not-a-real-namespace-404' does not exist — run: auger init first"}
```

## Muster-generated typed CLI

Exact command:

```bash
PATH=/home/kara/wojons/muster/bin:$PATH openapi-cli generate /home/kara/auger/docs/openapi.yaml && PATH=/home/kara/wojons/muster/bin:$PATH openapi-cli get-namespace-status aug067-muster-acceptance-20261010 --base-url http://127.0.0.1:8768 --output json
```

Raw output:

```text
Loading spec from: /home/kara/auger/docs/openapi.yaml
✓ Parsed spec (version: 0.1.0)
✓ Generated 2 command groups
✓ Commands persisted to storage
✓ Registered command: api
✓ Registered command: health

✓ Success! Commands registered and persisted
Run 'openapi-cli --help' to see available commands
Commands will load automatically on next CLI start
{
  "confidence": {
    "max": 0,
    "mean": 0,
    "min": 0
  },
  "counts": {
    "decisions": 0,
    "domains": 0,
    "escalations": 0,
    "options": 0,
    "unknowns": 0
  },
  "name": "muster-acceptance",
  "namespace": "aug067-muster-acceptance-20261010",
  "pending_memory_warnings": [],
  "project_id": "P-MUSTER-ACCEPTANCE",
  "status": "open",
  "thin_decisions": []
}
13:35:12 INF HTTP response received status="200 OK" status_code=200
```

## Muster MCP stdio

The following small Python stdio client performs `initialize`, `notifications/initialized`, `tools/list`, and a `tools/call` for `getNamespaceStatus`. Exact command: `python3 /tmp/aug067_mcp_client.py` (the client invokes `/home/kara/wojons/muster/bin/openapi-mcp -spec /home/kara/auger/docs/openapi.yaml -base-url http://127.0.0.1:8768`).

Raw client output:

```text
INITIALIZE {"id": 1, "jsonrpc": "2.0", "result": {"capabilities": {"tools": {}}, "protocolVersion": "2024-11-05", "serverInfo": {"name": "openapi-mcp", "version": "v0.1.2-0.20261010113326-bdc44b27de8c"}}}
TOOLS_LIST {"count": 8, "names": ["getNamespaceDump", "searchNamespaceEmbeddings", "exportNamespace", "recallNamespace", "getNamespaceStatus", "listNamespaceVerdicts", "getHealth", "checkNamespaceEvidence"]}
TOOLS_CALL {"id": 3, "jsonrpc": "2.0", "result": {"content": [{"text": "{\"confidence\":{\"max\":0,\"mean\":0,\"min\":0},\"counts\":{\"decisions\":0,\"domains\":0,\"escalations\":0,\"options\":0,\"unknowns\":0},\"name\":\"muster-acceptance\",\"namespace\":\"aug067-muster-acceptance-20261010\",\"pending_memory_warnings\":[],\"project_id\":\"P-MUSTER-ACCEPTANCE\",\"status\":\"open\",\"thin_decisions\":[]}", "type": "text"}], "structuredContent": {"confidence": {"max": 0, "mean": 0, "min": 0}, "counts": {"decisions": 0, "domains": 0, "escalations": 0, "options": 0, "unknowns": 0}, "name": "muster-acceptance", "namespace": "aug067-muster-acceptance-20261010", "pending_memory_warnings": [], "project_id": "P-MUSTER-ACCEPTANCE", "status": "open", "thin_decisions": []}}}
```

## Agent safety boundary

`answer` and the recording forms of `verdict` are operations an unattended agent must not receive. Muster does not generate them: they are absent from the OpenAPI `paths`/`READ_REGISTRY` in `scripts/gen_openapi.py`. This is absence, not an `x-safety` label on a callable write tool. `tests/test_openapi_parity.py` (also run by the `openapi-parity` arm of `tests/gate.sh`) enforces the generated operation set and rejects mutating path segments/operations. The generated `checkNamespaceEvidence` is read-only but can incur JEV cost; a host that does not authorize that spend must exclude it through its MCP tool allowlist (the API contract itself does not grant a cost budget).

## Gate

Fast hermetic arm: `python3 -m pytest tests/test_openapi_parity.py tests/test_api_service.py -q` → 16 passed. Full gate used a scratch DuckBrain substrate at `http://127.0.0.1:3123` (`DUCKBRAIN_URL`, `DUCKBRAIN_API_KEY`, `AUGER_REQUIRE_LIVE=1`, `AUGER_GATE_PYTEST_BUDGET=2400s`); the scratch daemon was launched by `/tmp/aug067_scratch.sh` and its log is `/tmp/aug067_scratch.log`. Gate output is recorded in `/tmp/aug067_gate.log`; final result is added after completion. Fixture cleanup is performed with the API-backed `auger.delete_namespace` call (never `rm -rf`).
