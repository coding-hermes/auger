# Security

## Reporting a vulnerability

Open a private security advisory on this repository, or contact the maintainers directly.
Please do not file a public issue for anything exploitable.

## What this tool touches

Auger is a client. It holds no data of its own and stores nothing in this repository:

- **DuckBrain** (HTTP, loopback) — reads and writes declared tables in a namespace you name.
  Auth is an `x-api-key` read from `~/.duckbrain/foreman-status.token` or `DUCKBRAIN_API_KEY`.
- **JEV** (`typesafe/jev-1.13` via OpenRouter) — sends project state as the model's `state`
  field and receives typed decisions. Key material is read from the environment or
  `~/.hermes/.env` and is **never** logged or written to a table.

## Operational notes

- The JEV key loop fails over across every key it finds, because expired keys are common. If
  every key fails, the tool **fails closed** — an explicit "unknown", never a silent optimistic
  default.
- `dump --config` renders a hypothetical and writes nothing. Anything that mutates a namespace
  is a `POST`/`PATCH` and is visible as such.
- Namespaces carry their own visibility. Publishing this repository reveals nothing about what
  a namespace contains.
