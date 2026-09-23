---
name: auger-usage
description: How to USE auger (spec-drilling CLI backed by DuckBrain + JEV) — verbs, run commands, pitfalls, and the working substrate pin. Load this before touching auger or its namespace.
category: software-development
---

# auger — usage skill for agents

## What it is

`auger` drills a project's spec into a git-backed, DuckBrain-stored record of
DECISIONS: each decision keeps its rejected options and the reasons they lost,
scores confidence via JEV, and can render what the system would look like under
a different set of choices (`dump --config`). One file, `auger.py`, stdlib
only. Public repo: `coding-hermes/auger`.

## Entry points

- CLI: `python3 auger.py -n <namespace> <verb> ...` — 12 verbs: `init start
  ask answer check status toggle dump propagate feedback recall verdict`. The
  verb surface is a PUBLIC CONTRACT; renames are breaking.
- Storage: DuckBrain declared tables in namespace `<ns>` — 14 tables
  (project question decision option break escalation assumption unknown domain
  edge facet bundle bundle_member verdict). Files:
  `~/duckbrain/namespaces/<ns>/tables/`.
- Tests: `bash tests/smoke.sh` (15 assertions, ~21s, leaves a namespace
  behind on purpose) · `AUGER_CI=1 pytest` (68 tests, ~5.5 min).

## Environment you need

- DuckBrain HTTP on `127.0.0.1:3000` — **on the working branch
  `feat/native-s3` of wojons/duckbrain**; the public default branch has NO
  declared-tables API and auger's happy path 404s on it (AUG-016).
- Credentials: `DUCKBRAIN_API_KEY` env or `~/.duckbrain/foreman-status.token`
  — NOTE: a fresh DuckBrain boot creates NO token file (the substrate runs
  auth=none by default; any non-empty `DUCKBRAIN_API_KEY` value works, see
  pitfall 5; AUG-039 tracks the README drift);
  `DUCKBRAIN_URL` overrides the base URL; JEV needs an OpenRouter key in
  `~/.hermes/.env`.

## The right-way patterns

1. **Init first, always.** `init` is idempotent and REPAIRS drifted table
   declarations (rewrites files that differ from code's `COLS`). If a field
   "mysteriously" never appears in rows, run init, then re-check.
2. **`answer` records the WHOLE choice**: chosen option + every alternative +
   why-not. The options ARE the what-if engine; an answer with one option
   produces a dump with nothing to toggle.
3. **Never write namespace rows by editing JSONL files** — go through the
   HTTP table API (or the CLI). Direct edits desync the declared-table layer.
4. **`dump --config` is read-only by design.** To actually change the stored
   config: `toggle --on <option-id> --off <option-id>` (batch flips together —
   see pitfall 2).
5. **Scratch work goes in throwaway namespaces** (`auger-smoke-*` style), and
   you own the cleanup: `rm -rf ~/duckbrain/namespaces/<ns>` when done.

## Common pitfalls (each one bit a real run)

1. **Reading an error body as data.** `init` on a 429/401 body once "succeeded"
   with 0 tables. If `init` prints `declared: 0/14` or an odd count, STOP —
   the substrate is lying or unreachable; do not proceed to `start`.
2. **toggle has per-decision exclusivity now (AUG-015 fixed)**: one option ON
   deactivates its siblings by default; `--additive` opts back out. But use
   the FULL option id (`D-001-O2`) — a bare `O2` exits 0 with "toggled …
   (0)" and patches NOTHING (AUG-036). If a subsequent dump shows "nothing
   active", this no-op is why.
3. **JEV probe shape**: a probe missing the body's `model` field 400s on every
   key and looks like a total outage. Mirror `JEV_MODEL` from auger.py.
4. **ask/check take 5-7s**: that is the JEV network round trip. Fail-closed
   UNKNOWN output = JEV unavailable; report it, don't retry blindly.
5. **Fresh DuckBrain without auth**: start daemon plain (no `--auth=apikey`),
   set any non-empty `DUCKBRAIN_API_KEY`. With auth enabled, keys live in
   `~/.duckbrain/auth.json`.
6. **Shared-host pidfile clash**: `EACCES /tmp/duckbrain-http.pid` = another
   user's daemon; use a different port and `DUCKBRAIN_DATA_DIR`.

## The HTTP data layer (integrator surface, probed 2026-09-23)

Auger's rows are reachable WITHOUT auger: `GET/POST /api/ns/<ns>/tables/<t>`,
`PATCH/DELETE /api/ns/<ns>/tables/<t>?pk=eq.<id>`, filters `eq/neq/gt/gte/like`,
`order=<col>.asc|.desc`, `limit=`. Auth = `x-api-key`. Working recipes and the
sharp edges (all hit live, see docs/dogfood/2026-09-23-seam-http-integration.md):

- ✅ eq/gt/order/limit/LIKE reads, POST (201), DELETE by pk — all clean; a
  missing `pk` on PATCH/DELETE 400s with a precise message.
- ⚠️ PATCH can 200 WITHOUT writing (AUG-043): read the row back after any
  PATCH; never trust the status code.
- ⚠️ POST with no `id` is ACCEPTED (201) and stores `id: null` — unreachable
  afterward (AUG-045). Always send a full row.
- ⚠️ `count`/pagination params are silently ignored (no Content-Range);
  filter+count client-side (AUG-042/046 family).
- ⚠️ A bad-typed filter value (`confidence=gt.banana`) 500s (AUG-044).
- ⚠️ The embedding store (`memories`) has NO HTTP route — semantic search is
  only reachable through auger's `recall`/`check` verbs.
- ⚠️ "Versioned in git" (README/DESIGN) is currently FALSE: duckbrain
  gitignores `/namespaces/` (AUG-044). Treat the JSONL as local files.

## Quick sanity check (30 seconds)

```bash
python3 auger.py -n <ns> status        # instant; proves API + token + tables
python3 auger.py -n <ns> recall "any seed phrase"   # proves embeddings
```

- **The moot cascade has a verb now (AUG-028 merged 2026-09-23):**
  `answer --invalidates <decision-id> --invalidates-why "…"` records the
  LOCAL `breaks` edge that drives `propagate`'s rule walk (dst_project
  ABSENT = local; a sibling break still routes to an escalation). The old
  hand-POSTed edge shape below still describes the storage truth.
- **The registers have NO write verbs (AUG-019, confirmed twice by use).**
  `break`/`assumption`/`unknown`/`escalation` rows are declared and read
  (status counts unknowns; ask reads them) but no CLI command creates one —
  only `answer --invalidates` (edges, not rows) and the propagate impact
  pass (escalations) write anything. A seed that says "X is unknown" will
  still show `unknowns: 0` until a row is hand-POSTed.
- **`ask` persists the question it proposes now (AUG-035 merged 2026-09-23).**
  When JEV proposes a question above `T_SUBJECT` that the gate scores NEW,
  `ask` stores it — one `question` row (`status=open`, `qclass=ask_proposed`,
  the gate's noul in `jev_already_answered`) plus its `facet` rows — and
  prints `stored question: <id>`. `answer --question-id Q-…` on the line ask
  just printed therefore works, as the README's loop says. The store is
  fail-closed: a proposal below `T_SUBJECT`, one the gate scores answered, one
  with no verdict at all, and one whose text is already open all store NOTHING
  and print why. Questions still enter the store via `feedback` (thin
  decisions) when ask proposes no new one.
  FIELD REALITY (2026-09-23 4th dogfood): across five live `ask` calls every
  JEV proposal landed below T_SUBJECT (0.15-0.24 vs 0.45) — the seam did not
  fire once (AUG-041), and the proposal text is the criterion SLUG
  (`how_does_it_fail`), not a sentence (AUG-042). Do not build on `ask`
  storing anything yet; the proven question path is `feedback` → question row
  → `answer --question-id` (verified live: `closed Q-000001`).
- **`answer` validates its preconditions BEFORE it writes (AUG-034 merged
  2026-09-23).** A nonexistent `--question-id` (and a duplicate `--id`) now
  refuses with nothing stored, so the retry that used to duplicate a decision
  id is gone; decision ids are still not unique-keyed in the store itself.
- **`--domain` is unvalidated free text (AUG-038)** and `status`'s
  domain-coverage display is one-index-off — do not copy a domain number
  from `status`; look it up in the grid dir (`4.05-data.md` → `--domain
  4.05`).
- **`dump --config` full ids only (AUG-037):** the help's `D-001=O2`
  shorthand is discarded as "unparsed" at rc=0; write `D-001=D-001-O2`
  (option labels also accepted per verdict, per the note above).
- **Bundles and the graph (the 2026-09-23 surface)**

- **One namespace per member, named exactly the member** (`mccli`, `mcview`).
  This convention is code-only (`member_namespace`, auger.py ~L865); a
  different pairing silently disables every cross-project walk.
- **Bundle rows have no verb.** Create them via the table API (POST
  `/api/ns/<ns>/tables/bundle` and `/tables/bundle_member`) — see
  docs/dogfood/2026-09-23-graph-bundles-integration.md for the working
  recipe. AUG-029 tracks the missing verbs.
- **`answer --scope bundle` may be silent.** If the sibling walk found
  nothing to flag, the verb prints nothing (AUG-027). Do not assume the pass
  did not run; check the edge table.
- **`propagate` is cheap and mostly a no-op on feedback-born questions**
  (feedback pre-gates them); `--recheck` forces real re-examination.
- **Verdict shapes:** `verdict --good|--bad [--judged-by NAME]
  [--confidence X]` (on-file), `--ask-jev` (JEV judges the rendered dump,
  fail-closed), `--config D-001=O2` (hypothesis; values accept option ids OR
  labels; requires --ask-jev), `--list`. Cost ~$0.0001/call.
