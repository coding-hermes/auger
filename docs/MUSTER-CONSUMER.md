# Consuming auger over the muster protocol (AUG-066)

[muster](https://github.com/wojons/muster) turns an OpenAPI 3.x document into a
typed CLI (`openapi-cli`), an MCP server (`openapi-mcp`), a daemon, and a Go
library. This repository publishes the contract muster consumes:
[`docs/openapi.yaml`](openapi.yaml) — a **generated artifact**, not a
hand-maintained list (see below), describing auger's read-first HTTP API.

Status: this file is the frozen AUG-066 contract and consumer wiring. The
service that implements it on `http://127.0.0.1:8766` is the AUG-067 build.
The port is deliberate: `8766` is distinct from the existing embedding-only
`auger serve` default (`8765`) and from DuckBrain's default (`3000`). Do not
point a consumer at `:8765` — that surface is embedding search and health
only, and it stays exactly as it is.

## The contract

- **Generated, one source of truth:** `scripts/gen_openapi.py` reads the
  facts the contract must not invent from `auger.py` (version, served route
  shapes, retrieval bounds) and emits the document. Regenerate with:

  ```bash
  python3 scripts/gen_openapi.py > docs/openapi.yaml
  ```

- **Parity is executable:** `tests/test_openapi_parity.py` (and the
  `openapi-parity` arm in `tests/gate.sh`) regenerate the spec and require
  byte identity with the committed file, then prove every `operationId` maps
  to a real, registered, read-only operation. If the code moves and the file
  is not regenerated, the gate fails — a hand-edited spec cannot pass.
- **Read-only by classification:** every operation carries
  `x-safety: read-only`. Operations that create, modify, or judge records
  (`init`, `start`, `bundle`, `ask`, `answer`, `toggle`, `propagate`,
  `feedback`, `record`, and `verdict`'s recording forms) are **absent from
  the contract entirely** — not labelled in prose, omitted — until an
  explicit approval boundary exists. This is the AUG-065 scope decision
  ([muster-scope.md](muster-scope.md)); `dump`'s file-writing `--out` form is
  excluded the same way, and no DuckBrain/JEV credential appears anywhere in
  the document.
- **External calls are disclosed, not hidden:** `check` calls the JEV
  decisions model and the retrieval operations query an embedding/search
  provider through the DuckBrain substrate; both can incur cost. They remain
  read-only with respect to auger's records, and each discloses this via
  `x-external-calls`.

## Consumer configuration

The exact `mcpServers` block (from the scope decision in
[muster-scope.md](muster-scope.md) — copy-pasteable as-is on this checkout):

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

On another installation, substitute the checked-out repository's absolute
spec path and the configured API base URL **together**. Never point the
consumer at DuckBrain itself; muster talks to auger, and auger alone decides
which substrate operations occur. The spec's own `servers[0].url` is the same
absolute `http://127.0.0.1:8766`, so muster can also dereference the document
standalone when `--base-url` is omitted.

## Quickstart (3 lines)

```bash
openapi-cli --spec /home/kara/auger/docs/openapi.yaml --base-url http://127.0.0.1:8766 status
openapi-mcp --spec /home/kara/auger/docs/openapi.yaml --base-url http://127.0.0.1:8766
```

Line 1 drives a generated read verb (`status`) and must return the
namespace's populated status data — not help or schema output; line 2 starts
the MCP server, whose listed tools are exactly the contract's read-only
operations (initialize + tool-list handshake, then attach it to any MCP
host). muster derives the typed verb names from the spec's stable
`operationId`s (`getNamespaceStatus`, `recallNamespace`, `listNamespaceVerdicts`,
...); the AUG-067 acceptance runs both binaries against the live service and
freezes the exact spellings in evidence.

No write tool exists to be careful with: attempting any mutator by name must
fail as "not listed / not callable", because the contract never declared one.
