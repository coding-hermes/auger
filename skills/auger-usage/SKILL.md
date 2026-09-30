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

- CLI: `python3 auger.py -n <namespace> <verb> ...` — 15 verbs: `init start
  bundle ask answer check status toggle dump export propagate feedback recall
  verdict record`. The verb surface is a PUBLIC CONTRACT; renames are breaking.
  (`export` renders the whole namespace as a deterministic spec — byte-identical
  across runs; `record` writes the registers; `bundle add|member` manages
  bundles — recipes below.)
- Storage: DuckBrain declared tables in namespace `<ns>` — 14 tables
  (project question decision option break escalation assumption unknown domain
  edge facet bundle bundle_member verdict). Files:
  `~/duckbrain/namespaces/<ns>/tables/`.
- Tests: `bash tests/smoke.sh` (15 assertions, ~21s, leaves a namespace
  behind on purpose) · `AUGER_CI=1 pytest` (68 tests, ~5.5 min).

## Environment you need

- DuckBrain HTTP on `127.0.0.1:3000` (or your own `DUCKBRAIN_URL`). Substrate
  proven live 2026-09-29 on `feat/native-s3` @ 8f14f52 (fresh-box build 81s,
  `init` 14/14). The README now claims public `main` also carries the
  declared-tables API (since 42cea0a) — NOT yet verified by a dogfood run
  (our transfer leg lands on the pinned branch); if `init` 404s on `main`,
  fall back to the pinned branch (AUG-016 history).
- Credentials: `DUCKBRAIN_API_KEY` env, else token files
  `~/.duckbrain/foreman-status.token` then `~/.duckbrain/token` — the README
  documents this since AUG-091 (fixed; re-verified live 2026-09-29 on a fresh
  box: file fallback alone worked, no env var needed). To mint:
  `node bin/duckbrain.js token` prints a 64-hex admin token on stdout only
  (with `--auth=apikey` the daemon enforces it; never paste it into notes or
  artifacts). `DUCKBRAIN_URL` overrides the base URL (undocumented in the
  README — needed the moment :3000 is not yours); JEV needs an OpenRouter key
  in `~/.hermes/.env` — without it ask/check fail CLOSED and say so.

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
   see pitfall 2) for what-if flips, or `answer --supersedes D-XXX` for a
   formal reversal. Know the difference (pitfall 6).
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
5. **Two ways to change your mind, two different footprints** (run 9):
   `toggle` flips an option's active row only — the recorded choice and its
   memory row stay with the original answer, so current-mode `dump` then shows
   chosen-label O1 with active-checkbox O2 SILENTLY (the contradiction pass
   exists only in hypothetical mode, AUG-077), and `recall` keeps telling the
   old story. For a real reversal use `answer --supersedes D-XXX --chosen ...
   --supersedes-why ...` — it mints the successor, flips status, records the
   edge, refreshes facets. Even then, `recall` ranks the superseded decision
   EQUAL with its successor and unmarked (AUG-078) — trust `dump`/`check` over
   `recall` for "what did we choose" until that fix lands.
6. **Fresh DuckBrain without auth**: start daemon plain (no `--auth=apikey`),
   set any non-empty `DUCKBRAIN_API_KEY`. With auth enabled, keys live in
   `~/.duckbrain/auth.json`.
7. **Shared-host pidfile clash**: `EACCES /tmp/duckbrain-http.pid` = another
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
- **The registers have a WRITE verb now (AUG-019 closed; verified live
  2026-09-29, 6/6 probes, docs/dogfood/2026-09-29-run12-registers-bundle-export-integration.md).**
  `record break --decision D-XXX --breaks-what … --consequence … [--applied]`
  mints BR-NNNNNN — a nonexistent decision is refused rc=1 BEFORE id minting
  ("nothing was written" is true). `record assumption --text … --falsifier …
  [--monitoring …]` mints A-NNNNNN (--falsifier required at the parser).
  `record unknown --text … [--owner X] [--trigger Y] [--containment Z]`
  mints U-NNNNNN. `record option <label> --costs … --breaks …` fills the
  costs/breaks columns AUG-058 showed always-empty — now verified rendering
  in dump/export.
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
- **Bundles have verbs now: `bundle add <name> [--contract <path>]` and
  `bundle member <bundle-id> <project-name> <owner|consumer|test-target>`**
  (verified live 2026-09-29; run 2's hand-POSTed table-API recipe in
  docs/dogfood/2026-09-23-graph-bundles-integration.md still works but is no
  longer needed). Ids are minted (`B-000001`, `BM-000001`) — there is NO
  `--id` flag and no `bundle show` (read bundles via `export`/`dump`).
  Cross-repo members normally go through scheduler-project validation; the
  explicit escape `AUGER_ALLOW_SCRATCH_MEMBERS=1` prints a loud warning rather
  than passing silently. One namespace per member, named exactly the member.
- **`recall`/`check` scores change SCALE when the substrate's embedding
  provider is down (AUG-092, hit live 2026-09-29).** On a healthy substrate
  scores are cosine (~0.0-1.0). With embedding degraded, the substrate's
  lexical/BM25 tier answers and the SAME verbs print scores like 1.319 with
  no tier label — while `start`/`answer` still print "embedded". Do not
  compare scores across substrate health states, and check
  `GET /health → embedding.healthy` before trusting any similarity verdict.
- **`answer --scope bundle` may be silent.** If the sibling walk found
  nothing to flag, the verb prints nothing (AUG-027). Do not assume the pass
  did not run; check the edge table.
- **`propagate` is cheap and mostly a no-op on feedback-born questions**
  (feedback pre-gates them); `--recheck` forces real re-examination.
- **Verdict shapes:** `verdict --good|--bad [--judged-by NAME]
  [--confidence X]` (on-file), `--ask-jev` (JEV judges the rendered dump,
  fail-closed), `--config D-001=O2` (hypothesis; values accept option ids OR
  labels; requires --ask-jev), `--list`. Cost ~$0.0001/call.


## Pitfalls proven in real use (5th run, 2026-09-24 — the drill on a real decision)

- **`--chosen` must be byte-identical to one of the `--option` values, or you store
  an EMPTY configuration and get a success line (AUG-055).** The natural mistake:
  `--chosen` as a short name, `--option` as the full sentence. It exits 0 and prints
  `D-001 recorded`; only `dump` reveals `ACTIVE CONFIGURATION: (nothing active)`.
  Make the chosen text one of the options verbatim, or repair afterwards with
  `toggle --on <option-id>`. This also removes the spurious
  `CONTRADICTIONS WITH THE RECORD` line that `dump --config` prints when the
  recorded chosen text matches no option.
- **`--why-not` does NOT repeat (AUG-057).** Unlike `--option` (repeatable), passing
  `--why-not` twice silently keeps only the last reason — exit 0 either way. To keep
  two rejected reasons today, put them in one `--why-not` string; the schema has no
  per-option reason slot yet.
- **`ask` can re-propose the question your last `answer` just closed (AUG-056).**
  The gate scores similarity, not identity. If it happens, answering it creates a
  DUPLICATE decision with different id — prefer `check "<the question text>"` first,
  and treat a repeat as the gate's miss rather than a real new question.
- **On a shared host, never assume `127.0.0.1:3000` is yours (AUG-060).** Another
  user's daemon (or an orphan from a destroyed agent) can hold the port and will
  answer you — the tell is a 500 whose message names a home directory that is not
  your own. Boot your own substrate on a verified-free port and export
  `DUCKBRAIN_URL`; check with
  `(echo >/dev/tcp/127.0.0.1/<port>) 2>/dev/null && echo BUSY || echo FREE`.
- **Auth bootstrap is now documented and re-verified (AUG-091 closed;
  live on a fresh box 2026-09-29).** A fresh substrate has no token file;
  mint one with `node bin/duckbrain.js token` (prints the 64-hex token on
  stdout — never paste it into artifacts) and auger finds it via the
  documented fallback (`~/.duckbrain/foreman-status.token`, then
  `~/.duckbrain/token`); no env var needed. Starting the daemon with
  `--auth=apikey` makes it enforce tokens (a no-auth probe gets 401); bare
  boot runs auth=none where any non-empty `DUCKBRAIN_API_KEY` works.
- **On a gridless box, the README's `answer --domain 4.05` example refuses
  (AUG-093, hit live 2026-09-29).** The canonical 44-domain grid lives in a
  Hermes skill tree (`AUGER_DOMAIN_GRID` points at it). The refusal is
  precise and honest — omit `--domain` and the loop runs; domain rows are
  the coverage map, not a prerequisite.
- **`option.costs` / `option.breaks` USED to be always empty (AUG-058,
  closed): `record option <label> --costs … --breaks …` fills them now**
  (verified live 2026-09-29 — values render in dump/export). Older rows may
  still print «—»; read that as "not recorded", not "no cost".
- **`status`'s domain lines can contradict themselves (AUG-059).** A domain leading
  with "answered" can still end "(row status NOT-REACHED)"; trust the leading word
  and the decisions/questions count, not the parenthetical.
- **Confirmed FIXED in the current tree** (rows still pending on the board — check
  the code before believing them): `toggle --on O1` now resolves a unique bare index
  and writes it; an ambiguous token is refused rc=1 naming every candidate; a token
  matching nothing prints `no such option` rc=1 (AUG-036). `dump --config D-001=O1`
  now accepts the bare-index shorthand the help advertises (AUG-037). `status` now
  measures 199 ms, not the 2.2 s of AUG-047.
- **Still live:** `answer --domain` accepts any string (4.99, and even `notanumber`,
  both recorded exit 0) — AUG-038.
- **Every unpaginated read is silently capped at 100 rows (AUG-068).** The
  substrate returns exactly the first 100 rows when a table select carries no
  `limit` — 200 OK, no Content-Range, no truncation flag. Past 100 decisions in
  a project, `status` under-counts, `dump` drops real options, and nothing
  warns. Never trust either verb's numbers once `decisions` passes ~95; verify
  against the store with an explicit `limit=1000` select.
- **A project past 100 decisions cannot take a default-id answer AT ALL
  (AUG-069, P0).** The mint is count-based and caps with the page: every answer
  refuses with `decision 'D-101' already exists`, forever, single-writer
  included. Workaround: pass `--id D-<highest+1>` explicitly (find the highest
  via `?select=id&order=id.desc&limit=1`). Don't "retry harder" — the refusal
  is deterministic.
- **Never run two auger writers against one namespace (AUG-070).** They mint the
  same ids in the check-then-insert window and the store keeps duplicate
  decision/option rows; `dump --config` then renders one decision twice with
  two different real choices and no warning. If it happened, find duplicates
  with a per-id count over `?select=id&limit=1000` before trusting any dump.
- **`recall` slows with everything the project ever embedded (22 s warm at 61
  memories vs 3.9 s small).** At drill scale the "is this already answered"
  lookup is the slowest read in the tool; budget for it or batch questions.
