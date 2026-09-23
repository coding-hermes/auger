# diagnostics — how auger is built, and why, as learned by using it

Accumulated diagnostic trail from real-use runs (this file grows per run; it
explains, it does not paste logs).

## Architecture in one breath

One Python file (`auger.py`, ~2100 lines, stdlib only — no pip install, ever).
State lives ENTIRELY in a DuckBrain namespace you name: 13 declared tables
(`tables/<t>.table.json` + `<t>.jsonl` rows) plus memory keys for the seed and
decision embeddings. "The dump is a view, not an artifact." JEV (typesafe/
jev-1.13 over OpenRouter) is the confidence oracle for `ask`/`check`; it fails
CLOSED — a transport error becomes an explicit UNKNOWN, never an optimistic
default.

## The load-bearing invariants (all proven by breaking them historically)

1. **A configuration is a selection across option rows.** Decisions keep their
   rejected alternatives as `option` rows; `toggle` flips `active`; `dump`
   renders the selection. Dogfood 2026-09-22 found this invariant currently
   unenforced (AUG-015: two active options for one decision render as both).
2. **Evidence must not contradict itself.** Only non-chosen options are
   "rejected"; violating this once scored a genuinely-answered question 0.11.
3. **`dump --config` never writes.** Verified twice byte-level (state before ==
   after, on-disk rows unchanged). If a report claims a config change, check
   whether it actually used `toggle`.
4. **Init is the repair tool.** It rewrites any declaration file that differs
   from `COLS` (idempotent, diff-detected). The 2026-09-22 edge-declaration
   drift (AUG-017) is fixed by exactly one `auger -n auger init`.

## Errors the project's own history already hit (know these, don't re-find them)

- **429 token-bucket (600 req/min) through the transport**: gate.sh's pytest +
  smoke burst trips DuckBrain's limiter; `cmd_init` once parsed the error body
  as a namespace list and reported success while leaking the namespace. Now:
  bounded retry honoring `retryAfter`/`Retry-After`, and init fails loudly on
  a non-200 list.
- **Leak-audit false positives**: the audit once scanned the whole
  `auger-pytest-*` prefix, so a CONCURRENT run's live namespace was reported
  as a leak by the judge. Scope by run-specific suffix.
- **Probe-shape vs account outage**: a JEV probe omitting the body's `model`
  field gets `400 invalid_type` from every good key — which reads exactly like
  all keys being dead. Mirror `JEV_MODEL` verbatim before scoring keys dead.
- **Git identity**: primary author = totalwindupflightsystems (the AI), co-author
  trailer via CODING_HERMES_CO_AUTHOR; never invert.

## Substrate coupling (the sharp edge new contributors hit)

auger's entire storage contract is DuckBrain's declared-tables HTTP API
(`/api/ns/<ns>/tables/...`). That API is NOT on duckbrain's public default
branch as of 2026-09-22 — it lives on `feat/native-s3` (which also carries the
per-port pidfile and the registry-invalidation fixes). Until the API lands on
duckbrain main, every fresh-user doc must pin the branch. The 2026-09-22
fresh-machine proof (bunker): default branch → init claims success, API sees
0/13 tables, start 404s; branch → full loop green.

## How to verify claims about this tool (the 10-minute ladder)

1. `python3 auger.py --help` — the verb surface is the contract; diff it
   against the last sync record before anything else.
2. `bash tests/smoke.sh` — 15 real assertions, ~21s, leaves its namespace and
   prints the remove command.
3. `AUGER_CI=1 pytest` — 68 tests, ~5.5 min; CI runs the CI-safe subset.
4. For any "the data is wrong" claim: query the namespace directly
   (`GET /api/ns/<ns>/tables/<t>?select=...`) — auger is a thin client; the
   rows are the truth, the dump is a view.

## Fresh-install leg (2026-09-23, bare Debian 13 user, node 22 preinstalled)

Three undocumented steps sat between a fresh user and a green loop. The substrate
itself was fine once they were taken (install + build ~76s, `auger`'s full loop
green in ~7s with zero pip/npm installs; the daemon boots in its
degraded-no-embeddings mode on an airgapped box, which is the correct state there,
not a failure). The gap was documentation, not code — so the three now have their
working commands in the README quickstart path.

- **`pnpm` missing, and both standard routes are EACCES for a plain user.**
  `corepack enable pnpm` fails `EACCES` (it symlinks into `/usr/bin`); `npm i -g
  pnpm` fails `EACCES` (it writes `/usr/lib`). Working: `npm config set prefix
  ~/.npm-global && export PATH=~/.npm-global/bin:$PATH && npm i -g pnpm` — ~5s,
  pnpm 12.4.2.
- **`DUCKBRAIN_DATA_DIR` must already exist, and the error lies about why.**
  `DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http` dies `ENOENT` on the
  pidfile write; the message names the pidfile, not the absent directory. Working:
  `mkdir -p ~/dd-data` first.
- **`AUGER_DOMAIN_GRID` is the only way through `init --seed-domains` on a box
  without the skill tree.** The default grid path is the skill's dogfood artifact
  under `~/.hermes/skills/`, and the refusal is loud and correct — `domain grid
  not found: <default path> — point AUGER_DOMAIN_GRID at the skill's
  references/dogfood-artifact/02-domains directory`. A fresh user without
  `spec-decomposition-matrix-tradeoff` must point `AUGER_DOMAIN_GRID` at any
  canonical 44-domain grid (`4.01`–`4.44`), or skip `--seed-domains`; the loud
  refusal is right — a seeded 43 is exactly the silent-domain failure the rule
  forbids.
