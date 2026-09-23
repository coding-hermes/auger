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

## The bundle layer (2026-09-23 run — where the design meets the road)

The bundle model (SPEC-002) is careful on paper and the weakest layer in
practice. How the pieces actually connect — none of this is in docs/:

- **Membership is a table, populated by nobody.** `bundle`/`bundle_member`
  rows must be hand-POSTed to the table API; `bundle_member()` in code has
  three refusals (role closed-set, bundle exists, project in the FLEET
  scheduler DB) but no verb calls it (AUG-029).
- **A member's rows live in the namespace NAMED AFTER the member.**
  `member_namespace()` is the identity function (auger.py ~L865). Put two
  projects in one namespace and every cross-project walk silently sees
  nothing — no error, no warning. This cost the run a do-over.
- **The impact pass is silent by default.** `answer --scope bundle` walks
  siblings via JEV but prints nothing unless an AFFECTS/BREAKS edge is
  written; an "unchanged" verdict at ANY confidence is accepted. Proven by
  shim: a 0.24-confidence "unchanged" on an obvious algorithm swap (AUG-027).
  Function-level probe (importing auger.py, calling bundle_impact directly)
  DID write the edge at 0.50 on a re-run — the walk works; the thresholds
  and the report are the problem.
- **The moot cascade's trigger edge exists in no verb.** `propagate`'s rule
  walk fires on LOCAL `breaks` edges (dst_project absent). The impact pass
  always sets dst_project. Therefore only hand-POSTs or tests can trigger
  moot (AUG-028). The walk itself works: E-900001 (D-010 "the bench meter
  was mis-wired" breaks D-008) → `propagate` mooted Q-000001 with reason.
- **The gate walk needs an OPEN child of a CLOSED parent.** feedback always
  gates its own questions at ask time (stamps jev_checked_at), so plain
  `propagate` mostly skips; use `--recheck` for a real re-examination. The
  walk was verified live: Q-000006 (child of closed Q-000002) re-gated at
  noul 0.12, kept open, stamp updated honestly.
- **Parentless follow-ups are normal.** A decision recorded without
  `--question-id` produces follow-ups with NO derives_from parent — they are
  reachable but never gated by propagate. Only decisions that ANSWERED a
  question produce parented branches. Design consequence, not a bug.
- **No supersession.** Re-answering a contract area leaves the old decision
  active; five contradictory envelope decisions rendered as THE config.
  `verdict --ask-jev` called it BAD at noul 0.36/0.14 — the external model
  caught what the engine does not flag (AUG-030).

## How to verify claims about this tool (the 10-minute ladder)

1. `python3 auger.py --help` — the verb surface is the contract; diff it
   against the last sync record before anything else.
2. `bash tests/smoke.sh` — 15 real assertions, ~21s, leaves its namespace and
   prints the remove command.
3. `AUGER_CI=1 pytest` — 68 tests, ~5.5 min; CI runs the CI-safe subset.
4. For any "the data is wrong" claim: query the namespace directly
   (`GET /api/ns/<ns>/tables/<t>?select=...`) — auger is a thin client; the
   rows are the truth, the dump is a view.
