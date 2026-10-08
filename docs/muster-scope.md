# AUG-065 — Muster scope decision

Status: decision recorded; this file is the scope contract for AUG-066 (OpenAPI spec) and AUG-067 (implementation).

## Decision: (a), a read-first HTTP API owned by auger

Expose an auger-owned, versioned HTTP API described by an OpenAPI 3.x document. The API represents auger's supported read operations and is the eventual home for explicitly gated write operations. The API is not a direct view of DuckBrain's database: consumers address auger concepts and auger controls which substrate operations occur. It is not a generic CLI-over-HTTP wrapper.

The measured premise in the original AUG-065 row is stale. `auger.py` already has `auger serve` (`cmd_serve`, currently around line 6310): it binds a read-only HTTP server for embedding search and substrate embedding status (`EMBEDDINGS_ROUTES`); it never writes a row. AUG-046 closed this earlier. This is a useful read-only beachhead, not the complete decision API: it does not expose auger's decision/question/register workflows, and MUST NOT be described as doing so. Do not widen or repurpose the existing server as a write API. AUG-067 should add a separately versioned API service, with a distinct default address/port, while retaining `auger serve` as-is.

The initial API/MCP exposure is read-only. Include useful read operations (status, dump without `--out`, export, recall, check, embedding search, and health/status) with typed request/response schemas. Operations that create, modify, or judge records are not exposed to unattended MCP clients in this initial release. They may be added only as explicit MCP-gated operations after the MCP host can require human approval and their contracts document the side effects. OpenAPI operation metadata must identify the safety class; absence of an approval gate means the mutating operation is omitted from the MCP-exposed contract, not merely labeled in prose.

The OpenAPI document is a generated artifact from the `auger.py` argparse/operation registry (one source of truth), not an independently maintained hand-written list. Generation must preserve stable operation IDs and include only the API methods actually implemented. The implementation must not claim that exposing an HTTP endpoint automatically makes it safe for MCP.

### Explicit non-goals

- Do not expose DuckBrain's generic table, namespace, or memory API as auger's API.
- Do not expose the entire CLI or invoke arbitrary command strings/arguments over HTTP.
- Do not change, broaden, or add writes to the existing `auger serve` routes.
- Do not make any writing verb agent-safe or unattended in this initial release.
- Do not put DuckBrain or JEV credentials in an OpenAPI document, URL, MCP argument, or response.
- Do not reimplement Muster; Muster consumes the generated OpenAPI document.

## Consumer configuration

The canonical local consumer uses the generated spec at the repository's absolute path and the new API's loopback base URL. This block is the exact `mcpServers` configuration to document and test (not the existing embedding-only `:8765` server):

```json
{
  "mcpServers": {
    "auger": {
      "command": "openapi-mcp",
      "args": [
        "--spec",
        "/home/kara/auger/docs/openapi.yaml",
        "--base-url",
        "http://127.0.0.1:8766"
      ]
    }
  }
}
```

`docs/openapi.yaml` is the agreed AUG-066 artifact path. The spec must use the API base URL `http://127.0.0.1:8766` in its server entry. `8766` is deliberately distinct from the existing embedding server's default port `8765` and DuckBrain's default `3000`. On another installation, substitute the checked-out repository's absolute spec path and the configured API base URL together; do not point the consumer at DuckBrain or pretend the `:8765` embedding service implements the full API.

## Operation boundary: MCP safety classification

An operation is read-only only if it does not write DuckBrain rows, namespace declarations, embedded memories, or local files. Calls to JEV or an embedding provider can incur external cost; they are read-only with respect to auger's records, but the API/MCP descriptions must disclose the external call and failure behavior. `dump --out` is excluded from HTTP because it writes a local file.

Classification of all 17 top-level CLI verbs (including subcommands/flags):

| Verb | Classification | Reason / MCP treatment |
|---|---|---|
| `init` | Mutating | Declares tables and can seed domain rows; gated, not in initial MCP surface. |
| `start` | Mutating | Creates a project row and seed memory; gated. |
| `bundle` | Mutating | `add`/`member` create bundle or membership rows; gated. |
| `ask` | Mutating | May store a question plus facets after JEV proposes it; gated. |
| `answer` | Mutating | Writes decision/options and related memory/graph effects; gated. |
| `check` | Read-only | Reads evidence and asks JEV; no record write. Disclose JEV call/cost and fail-closed result. |
| `status` | Read-only | Reads registers and reports coverage; safe for unattended MCP. |
| `toggle` | Mutating | Patches option active state; gated. |
| `dump` | Read-only in HTTP form | Rendering is read-only; HTTP omits `--out` so it cannot write a local file. |
| `export` | Read-only | Deterministic read of all registers; safe for unattended MCP. |
| `propagate` | Mutating | Changes question/facet/edge state and may invoke retrieval/JEV; gated. |
| `feedback` | Mutating | Generates and writes follow-up question/facet/edge/escalation records; gated. |
| `recall` | Read-only | Retrieves embedded memory; may call embedding/search providers and incur cost. |
| `serve` | Read-only | Existing embedding search and health/status routes; no row writes. Keep distinct from the new API. |
| `verdict` | Mixed | `--list` is read-only; recording a supplied or JEV-derived verdict writes a verdict row. Expose list only initially; gate all record forms. |
| `record` | Mutating | Every subcommand writes a register row; gated. |

The only agent-safe operations in the initial MCP surface are the read-only API operations above. The current API must have no MCP-visible write operation. When write operations are later considered, expose them through the MCP approval gate (human confirmation per invocation); never rely on an agent prompt, a tool description, or the fact that the service is local as the gate. Direct HTTP writes must also be absent until an independently enforced authorization/approval boundary exists. A future write-capable release is a new scope decision if the existing Muster approval contract cannot enforce this.

## Authentication and credential ownership

Muster's `openapi-mcp` consumer sends HTTP requests to auger. It does not itself authenticate to DuckBrain or JEV, and MUST NOT receive their secrets. The auger API process performs the upstream calls using the same host-side credential resolution that auger already implements:

- DuckBrain: `DUCKBRAIN_API_KEY` if set; otherwise first non-empty `~/.duckbrain/foreman-status.token`, then `~/.duckbrain/token`. Requests use the `x-api-key` header. `DUCKBRAIN_URL` selects the substrate URL (default `http://127.0.0.1:3000`).
- JEV/OpenRouter: `OPENROUTER_API_KEY`, then `OR_API_KEY`, then discovered `sk-or-v1-` keys from `~/.hermes/.env` in file order (deduplicated). The process must retain the existing fail-closed behavior when no usable key exists; no verdict may be fabricated or recorded after a failed judgment.

Run the API as the intended local OS user, with its HOME and narrowly scoped environment configured as for the CLI. Do not pass credential values in the `openapi-mcp` JSON block: its arguments are public process configuration. DuckBrain/JEV secrets stay server-side, are not returned in API responses or logs, and must not be forwarded from a model-controlled request. Initial server binding is loopback-only. Because the initial API has no write routes, it needs no broad credential delegation to MCP; adding mutations requires the explicit approval boundary described above and an API-side enforcement decision before those operations are implemented.

## Acceptance for AUG-066 and AUG-067

The following checks are required at the later rows' merged HEAD. Run from the auger repository root. Commands may be adapted only if the tool's installed CLI spelling differs; preserve every stated assertion and show the exact command/output in the evidence.

1. Generate and validate the spec against the actual CLI registry:

   ```bash
   python3 auger.py --help
   python3 auger.py export --help
   python3 -m pytest -q tests/test_openapi.py
   openapi-cli validate docs/openapi.yaml
   ```

   Assertions: generated spec parses as OpenAPI 3.x; every documented `operationId` is unique and maps to a registered implemented operation; required safe operation IDs are present; every mutating verb/branch listed in this document is absent from the initial MCP tool surface; no undocumented operation is generated; the spec `servers[0].url` equals `http://127.0.0.1:8766`; and its security/auth description contains no credential literal. A test that only checks the file exists or YAML parses is not sufficient.

2. Start the actual HTTP service, not a mock, and prove populated reads:

   ```bash
   python3 auger.py -n auger-muster-acceptance init
   python3 auger.py -n auger-muster-acceptance start --id P-MUSTER-ACCEPTANCE --name muster-acceptance --seed 'Muster adapter acceptance fixture'
   python3 auger_api.py --host 127.0.0.1 --port 8766 --namespace auger-muster-acceptance
   ```

   From a second terminal, request each implemented read route using `curl -fsS` and assert, with `jq -e`, that the response is valid JSON and includes the seeded project ID/text; assert `status`/`export` do not report an empty namespace, and assert recall/search returns the seeded evidence when indexing is available (otherwise require a clearly identified provider-unavailable response, never a false successful empty result). Also send `POST`, `PUT`, `PATCH`, and `DELETE` to every collection/resource route and assert `405` (or the documented fail-closed refusal), no row count changes, and no MCP write tool is advertised. A 200 health response alone is not acceptance.

   The command above names the intended executable boundary; AUG-067 may choose a different module/entrypoint, but must document and run its actual equivalent. Do not let an unimplemented placeholder command count as proof.

3. Exercise Muster itself against the same live process and fixture:

   ```bash
   openapi-cli --spec /home/kara/auger/docs/openapi.yaml --base-url http://127.0.0.1:8766 status
   openapi-mcp --spec /home/kara/auger/docs/openapi.yaml --base-url http://127.0.0.1:8766
   ```

   Assertions: `openapi-cli` exits zero and returns the populated fixture data, not merely help/schema output; `openapi-mcp` completes its MCP initialize/tool-list handshake; the listed tools include the required read operations and exclude every mutator (including `ask`, `verdict` write forms, and `record` subcommands); invoke at least one read tool and verify its result contains the fixture. Attempt each write-class tool by name and assert it is not listed/callable. If MUSTER's CLI syntax differs, use its actual documented command but retain these semantic assertions and capture `--help`/handshake evidence. Clean up only the isolated `auger-muster-acceptance` test namespace after assertions.

These tests are anti-vacuous: they require non-empty seeded data to traverse CLI → DuckBrain → auger HTTP → Muster client/MCP, and they prove negative write boundaries by checking both advertised tools and persisted row counts. A missing server, empty database, schema-only parse, or health-only ping cannot pass.

## Authority and remaining decisions

`~/muster/specs/PROTOCOLS.md` was not present at the recorded worktree path during this scope pass. The AUG-065 row's supplied protocol summary says OpenAPI 3.x is Muster's first adapter and gives the `mcpServers` shape; AUG-066 must verify current Muster operation-safety metadata and exact CLI options against the checked-out/current Muster design authority before freezing the generated contract. The product decision itself is closed: expose auger's own read-first API; keep DuckBrain substrate API private; leave `serve` as its existing embedding-only beachhead; do not expose writes unattended. If current Muster cannot enforce human confirmation for a write operation, writes remain absent from the MCP contract; they are not authorized by assumption.
