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

- CLI: `python3 auger.py -n <namespace> <verb> ...` — 17 verbs: `init start
  bundle ask answer check status toggle dump export propagate feedback recall
  serve api verdict record`. The verb surface is a PUBLIC CONTRACT; renames are breaking.
  (`serve` runs the embedding store HTTP server; `api` serves the Muster OpenAPI contract on :8768.)
  (`export` renders the whole namespace as a deterministic spec — byte-identical
  across runs; `record` writes the registers; `bundle add|member` manages
  bundles — recipes below.)
- Storage: DuckBrain declared tables in namespace `<ns>` — 14 tables
  (project question decision option break escalation assumption unknown domain
  edge facet bundle bundle_member verdict). Rows live in the SUBSTRATE's
  namespace dir; the declaration files (`<ns>/tables/*.table.json`) are written
  by the CLI to a path IT resolves locally — `DUCKBRAIN_NAMESPACES_PATH`, else
  `~/duckbrain/namespaces` — NOT the path the substrate it is talking to uses.
  On a host with more than one DuckBrain, export
  `DUCKBRAIN_NAMESPACES_PATH=<the substrate's namespaces dir>` for the CLI too,
  or `init` writes into the OTHER instance's tree and exits rc=1 at 0/14
  (AUG-074-R2, measured 2026-10-23: 14 files created under the live instance).
- Install: `pip install .` / `-e .` needs a venv or pipx on Debian 12+ /
  Ubuntu 24.04 (PEP 668 `externally-managed-environment`; AUG-106-R2). The
  console script then lives in `<venv>/bin/auger`; stock boxes do not ship pipx.
- Tests: `bash tests/smoke.sh` (15 assertions, ~21s, leaves a namespace
  behind on purpose) · collection: `AUGER_CI=1 python3 -m pytest tests/ --collect-only -q`
  (345 tests collected at DOC-020 verification; rerun this command rather than relying on
  the snapshot) · execution: `AUGER_CI=1 python3 -m pytest tests/` (~5–19 min depending
  on host load — the gate budget is 1200s per AUG-081). The pytest
  suite targets the LIVE substrate at `DUCKBRAIN_URL` (default
  `127.0.0.1:3000`) and only teardown removes its namespaces, so a KILLED run
  (gate cap, QA cap) leaves a namespace + registry mapping behind and there is
  no sweep verb — on this host that had accumulated to 15,492 mappings and 645
  dirs / 1.18 GB (AUG-111). Point it at a scratch substrate for anything you
  expect to interrupt.

## Environment you need

- DuckBrain HTTP on `127.0.0.1:3000` (or your own `DUCKBRAIN_URL`). Substrate
proven live 2026-10-15 on `main` (dogfood run 16, 14/14 tables; see
docs/dogfood/2026-10-15-released-artifact-integration.md). The `main` branch
carries the declared-tables API — no branch pin required.
- Credentials: `DUCKBRAIN_API_KEY` env, else token files
  `~/.duckbrain/foreman-status.token` then `~/.duckbrain/token` — the README
  documents this since AUG-091 (fixed; re-verified live 2026-09-29 on a fresh
  box: file fallback alone worked, no env var needed). To mint:
  `node bin/duckbrain.js token` prints a 64-hex admin token on stdout only
  (with `--auth=apikey` the daemon enforces it; never paste it into notes or
  artifacts). `DUCKBRAIN_URL` overrides the base URL (documented in README
  §"Substrate port on shared hosts" at line ~171 — needed the moment :3000 is
  not yours); JEV needs an OpenRouter key
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
   deactivates its siblings by default; `--additive` opts back out. Use the
   FULL option id (`D-001-O2`) — a bare index token resolves when it matches
   exactly one option row; ambiguous tokens are refused rc=1 naming candidates;
   an unknown token prints "no such option" rc=1. The pre-AUG-036 no-op (bare
   index exits 0, patches nothing) is fixed — a subsequent dump showing "nothing
   active" is not explained by a silent no-op.
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
- ⚠️ `Prefer: count=exact` returns `X-Total-Count` on every table; the `?count=` QUERY
  parameter is silently ignored (no Content-Range). Use the header path for pagination.
- ⚠️ A bad-typed filter value (`confidence=gt.banana`) 500s (AUG-044).
- ⚠️ The embedding store (`memories`) IS reachable over HTTP: `auger serve` (AUG-046) publishes
  GET `/api/ns/<ns>/embeddings/search?q=…&limit=…` and `GET /api/embeddings/search?q=…`;
  `auger api` (AUG-067) does the same plus GET `/api/ns/<ns>/recall`.
  The start command: `python3 auger.py -n <ns> serve` or `python3 auger.py api`.
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
- **`--domain` takes one canonical grid number only (AUG-038 closed, commit
  5422123).** The accepted form is a zero-padded number in the inclusive range
  `4.01-4.44`, for example `--domain 4.05`; the number is then resolved to the
  grid name. Unknown or malformed values — including `4.05-data`, `4.05=data`,
  `data`, `4.9`, `4.99`, and `notanumber` — refuse rc=1 with "nothing was
  stored". The grid filename `4.05-data.md` and the refusal hint `(e.g.
  4.05=data)` are descriptive, not accepted CLI syntax: pass the number itself.
- **`status`'s coverage display preserves the grid's number/name pairs.** Its 44
  pairs are exactly the 44 grid file names (`4.05  data` ↔ `4.05-data.md`), so
  a number copied from `status` is correct.
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
- **`(row status NOT-REACHED)` beside an answered domain identifies a failed seed-row
  advance, not harmless display noise (AUG-059-R2, fixed 2026-10-27).** Auger
  records the decision/question evidence and then retires the seed row. Null-ring
  domains (35 of the 44, whose grid header reads `terminating_ring: null`) advance
  through a delete+insert rewrite because the substrate's PATCH refuses the stored
  null with 422. If even the rewrite fails, `answer` still exits 0 with the
  decision stored but surfaces one `WARNING domain <num> row <id>: ...` line naming
  the substrate's error — read that as a surfaced warning and drift signal: the
  evidence count is real, but the stored seed-row status is stale and the failed
  advance write needs investigation. Do not dismiss or distrust the parenthetical;
  use it together with the leading `answered` state, the `decisions/questions`
  count, and the WARNING line from the answer that produced it.
- **On a gridless namespace the coverage map is blind to your answers
  (AUG-112, filed 2026-10-27).** Without `init --seed-domains` the rows are
  absent, and `status` prints "answered 0" plus "44 absent" even when decisions
  carry domain numbers — `dump` shows them, the coverage block does not. The
  numbers are still stored; run `init --seed-domains` (it binds the 44 rows to
  the newest project in the namespace, and is idempotent per project) if you
  want the map to count them.
- **Confirmed FIXED in the current tree** (rows still pending on the board — check
  the code before believing them): `toggle --on O1` now resolves a unique bare index
  and writes it; an ambiguous token is refused rc=1 naming every candidate; a token
  matching nothing prints `no such option` rc=1 (AUG-036). `dump --config D-001=O1`
  now accepts the bare-index shorthand the help advertises (AUG-037). `status` now
  measures 199 ms, not the 2.2 s of AUG-047.
- **FIXED since these notes were written:** `answer --domain` no longer accepts
  any string — 4.99 and `notanumber` both refuse rc=1 with "nothing was stored"
  (AUG-038 closed, commit 5422123; re-verified live 2026-10-27).
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

## The `spec` verb family (XML corpora) and the spec-to-build loop (verified live 2026-11-23, main @ a8bf275)

`spec` is a SEPARATE surface from the DuckBrain loop. The CLI is 18 top-level
verbs: the 17 counted above (`init start bundle ask answer check status
toggle dump export propagate feedback recall serve api verdict record`) plus
`spec`, whose family carries 16 subcommands. `auger spec …` reads a directory
of XML spec documents, touches NO substrate, and writes nothing to the corpus.
The build handoff half is the flagship new surface (AUG-118 → AUG-124, rows
AUG-206/AUG-207 documented it); this section is the working recipe, verified
end to end on a scratch consumer repo. Full grammar and block taxonomy:
`docs/XML-SPEC-SCHEMA.md`; the authored corpus and its read recipes:
`docs/SPEC-CORPUS.md`.

### The loop in one breath (the digest chain)

Author a corpus → `task-packet` (packet + digest) → `plan-draft` (plan digest
= what an approval binds to) → `plan-approve --digest <sha>` (append-only
approval ledger) → `plan-export --board <dir>` (canonical board rows via
`boardctl`, approval checked BEFORE any write) → `judge-packet` (GitReins
packet binding board acceptance criteria verbatim) → build → `plan-reconcile`
(copied board evidence back, read-only). Nobody can approve a plan they are
not looking at: every digest in the chain must be CURRENT at the moment the
next step consumes it.

### All 16 subcommands (purpose — refusal behaviour)

1. `validate <root>` — corpus-wide verdict: schema version, stable IDs, typed
   edges, methods (XSD backstop when lxml is installed — a dev extra; a host
   without it refuses `xsd-processor-missing` instead of passing).
2. `list <root> [--id --kind --applies-to --status --version --as-of REF]` —
   TSV block listing (id, kind, status, version, source, applies-to); no
   selector lists the whole corpus; a selector matching nothing is exit 0 plus
   one `spec note (no-match)` line on STDERR, not an error.
3. `assemble <root>` (same selectors + `--format markdown|json --out DIR
   --no-deps --measure`) — matched blocks PLUS their depends-on/verifies
   closure, byte-reproducible; a selector-less run refuses `empty-selector`
   exit 2 (assembly is never a silent whole-corpus dump), while a selector
   that matches nothing emits a valid empty document.
4. `check <root>` — validate test obligations: tracked paths, selectors,
   migrations, fixtures (AUG-126 — the obligation validator, previously
   unreachable from the CLI); with `--diff-stdin`/`--diff-file` it grades a
   staged diff (cap `--max-diff-bytes`, default 8 MiB); an obligation `path=`
   not TRACKED in `--repo-root` is fail-closed `stale-test-ref`.
5. `bundle <root> <requirement>` — emit one requirement's verification bundle
   as JSON; a nonexistent requirement refuses `unknown-requirement`.
6. `render <root> --out DIR` — regenerate every Markdown/HTML view of the
   corpus (`SPEC-CORPUS-VIEW.*`); DIR must already exist and lie OUTSIDE the
   corpus root (`render-into-corpus` refusal; `--out` missing/unwritable
   refuses too); two runs over one corpus are byte-identical.
7. `align` — the staged-diff checker GitReins invokes: grade a patch against
   the corpus and the ch:trace doctrine, ONE JSON line. Takes NO positional
   ROOT (flag-only `--repo-root --spec-root --diff-stdin/--diff-file
   --protocol-version`); exit 0 pass or warn, 1 fail (advisory to the
   caller), 2 typed refusal (`unsupported-protocol-version`, a diff over the
   cap, no diff source, an unresolvable repository root, an invalid corpus).
8. `task-packet <root> --task ID` — ONE task's exact scope packet + sha256
   digest; corpus-wide, so a document that is not well-formed ANYWHERE refuses
   the whole run (a packet built from half a corpus is not the approved plan);
   a nonexistent id refuses `unknown-task`.
9. `task-export <root> --task ID --board DIR` — write ONE task packet to the
   canonical board through `boardctl`, which it uses for `create`/`show` ONLY
   (no scheduler dispatch, no completion, no board update). APPROVAL-BOUND:
   without a valid current plan digest boardctl is never invoked and both
   canonical files stay byte-identical (`unapproved-plan-digest` exit 2); an
   identical existing row is reported `unchanged`; a drifted row is a
   conflict, refused.
10. `context <root> (--task ID | --code PATH | --symbol NAME)` — ONLY the
    blocks governing the query: in-scope + declared closure, exclusions listed
    WITH reasons; never a whole-document dump. Zero or two selectors and a bad
    corpus refuse exit 2; an unscoped/ambiguous/inactive answer is a RESULT
    (exit 0) with an empty selection, not an error.
11. `plan-draft <root> --plan ID` — the versioned DRAFT plan for a set of
    tasks: ids, criteria, dependencies, anchors, expected evidence, and the
    plan digest (JSON, top-level `digest`) that an approval binds to;
    read-only.
12. `plan-approve <root> --plan ID (--approve | --reject) --digest SHA256
    --approver NAME [--ledger DIR] [--at TIMESTAMP]` — append ONE decision to
    the plan approval ledger (default `<spec root>/.auger/
    plan-approvals.jsonl`). The digest must be the plan's CURRENT digest —
    nobody approves a plan they are not looking at (`stale-plan-digest` exit
    2); the ledger is append-only and the LATEST record for a plan is the
    decision (a rejection is a new fact, never an edit). Pass `--at` or the
    record's `at` field stays empty (AUG-206: weakens audit traceability).
13. `plan-export <root> --plan ID --board DIR` — export every row of an
    APPROVED plan: `boardctl create` per new row, `update` when only
    title/acceptance text drifted, read back with `boardctl show` and compared
    field by field. Approval is checked BEFORE any boardctl call — an
    unapproved, rejected or stale plan cannot move a byte (`unapproved-plan-
    digest`/stale refusal, exit 2, nothing written). A row whose SCOPE drifted
    is a conflict, refused (an export never overwrites a row behind its
    owner). Fail-stop: first failing row ends `partial` exit 1 with a resume
    record — re-running the same approved plan completes it.
14. `judge-packet <root> --plan ID --row ID --board DIR [--trace MARKER]` —
    assemble the GitReins packet for one APPROVED board row: board id + id
    mapping, resolved ch:trace ids, exact test obligations, board acceptance
    criteria VERBATIM, and the approved digests. Refuses unless the ch:trace
    marker's ids resolve to THIS row, this task and its own obligations
    (offending ids named, no packet emitted; a marker missing its mandatory
    `row=` field → `missing-trace-field`). Omit `--trace` to use the marker
    `plan-export` bound into the row's `reasoning`.
15. `plan-reconcile <root> --plan ID --board DIR` — copy board status/commit/
    verdict evidence back into Auger, READ-ONLY (the only external command is
    `boardctl show`; the record says `writes: none`). A commit that does not
    exist in `--repo-root` and a verdict naming another commit are refused
    rather than recorded as if they bound; absent evidence carries
    `none:<reason>`.
16. `judge-handoff <root> --plan ID --row ID --board DIR --dispatch-cmd CMD` —
    the handoff: validate the binding FIRST (row, trace, scope, obligations,
    digests), then call the dispatch command exactly ONCE with the packet path
    appended. Without `--dispatch-cmd` it refuses `dispatch-unconfigured`
    exit 2 (auger never pretends to have started a judge — no dispatch, no
    partial handoff).

Every refusal across the 16 is TYPED and fail-closed: one `spec error (<code>)`
line on stderr, exit 2, nothing written. Time per verb: 0.12–0.23 s wall —
none of it is slow.

### Authoring the minimum corpus the loop needs

Grammar: namespace `urn:auger:spec:1`, root `<spec id=… version=…
schema-version="1.0">`; every block carries `id`, `kind` (always equals the
element name), `status`, `version`, and usually `applies-to`. One
requirement + one verification + one acceptance + one excluded requirement +
one task is the working minimum:

```xml
<spec xmlns="urn:auger:spec:1" id="DEMO-SPEC" version="1.0.0" schema-version="1.0">
  <requirement id="DEMO-REQ-1" kind="requirement" status="active" version="1.0.0"
               applies-to="demo.export" verification="test" verifies="DEMO-VER-1">
    The export writes one canonical row per record.
  </requirement>
  <verification id="DEMO-VER-1" kind="verification" status="active" version="1.0.0"
                applies-to="demo.export" method="test" type="test" runner="pytest"
                path="tests/test_export.py" selector="test_export_one_row_per_record"
                assertions="one row per record" />
  <acceptance id="DEMO-AC-1" kind="acceptance" status="active" version="1.0.0"
              applies-to="demo.export" verification="test" verifies="DEMO-REQ-1">
    Export produces one canonical row per record and leaves no partials.
  </acceptance>
  <requirement id="DEMO-REQ-9" kind="requirement" status="active" version="1.0.0"
               verification="inspection">A later wave owns the migration.</requirement>
  <task id="DEMO-TASK-1" kind="task" status="active" version="1.0.0"
        applies-to="demo.export" verification="test"
        plan="DEMO-PLAN" plan-version="1.0.0"
        in-scope="DEMO-REQ-1" acceptance="DEMO-AC-1" out-of-scope="DEMO-REQ-9">
    Export this task as one canonical board row.
    <exclusion ref="DEMO-REQ-9" reason="owned by the migration wave"/>
  </task>
</spec>
```

Rules that bite at authoring time:

- Every `<verification path=…>` must resolve to a file TRACKED in
  `--repo-root` (commit the repo, then check `git ls-files` BEFORE validating;
  the refusal is fail-closed `stale-test-ref`).
- The default repo root is `git rev-parse --show-toplevel` from the spec root;
  a corpus outside any git tree refuses `unresolvable-repo-root` with the
  remedy in the message — pass `--repo-root` explicitly.
- `runner=` is a registry id (`pytest`), never a shell string.
- Plan membership is authored ON the task (`plan=` + `plan-version=`), so a
  plan cannot grow by dropping a file into a directory.

### Driving the loop end to end (copy-paste with your ids)

```bash
# 0. a board for plan-export to write into (needs the boardctl binary):
boardctl init -C <board-dir> --project demo --namespace demo

# 1. the corpus verdicts
python3 auger.py spec validate specs                    # `1 document(s), 5 block(s), 5 reference(s) ok`

# 2. what governs the task (in-scope + closure, exclusions with reasons)
python3 auger.py spec context specs --task DEMO-TASK-1  # JSON: selected 4, dependencies 1

# 3. the task packet and its digest (bind an approval to THIS)
python3 auger.py spec task-packet specs --task DEMO-TASK-1   # JSON: {digest, packet}

# 4. draft the plan and grab its digest
DIGEST=$(python3 auger.py spec plan-draft specs --plan DEMO-PLAN | jq -r .digest)

# 5. approval bound to the current digest (wrong digest rc=2 stale-plan-digest)
python3 auger.py spec plan-approve specs --plan DEMO-PLAN --approve --digest $DIGEST --approver DemoAgent

# 6. export the approved plan to the board (read back per row; scope conflicts refused)
python3 auger.py spec plan-export specs --plan DEMO-PLAN --board <board-dir>

# 7. the judge packet (uses the ch:trace marker plan-export bound into the row;
#    writes specs/.auger/judge-packets/<plan>-<task>.json and prints {digest, packet, path})
python3 auger.py spec judge-packet specs --plan DEMO-PLAN --row DEMO-TASK-1 --board <board-dir>

# 8. after the build: board evidence back into auger (read-only)
python3 auger.py spec plan-reconcile specs --plan DEMO-PLAN --board <board-dir>
```

Refusals you can EXPECT while learning the loop (all rc=2, nothing written):
`invalid-plan-digest` (the --digest is not a 64-hex sha256), `stale-plan-digest`
(well-formed but approve an OLD plan — the digest chain's core guard),
`unapproved-plan-digest` (`task-export`/`plan-export` before approval),
`unknown-task`, `unknown-requirement` (`bundle`), `empty-selector`
(`assemble` with no selector), `render-into-corpus`, `as-of-unknown-ref`
(`list --as-of` a ref that does not resolve), `dispatch-unconfigured`
(`judge-handoff` with no judge command). A `judge-handoff --dispatch-cmd` run
starts the judge lifecycle exactly once; auger never dispatches a BOARD task
from that seam.

Pitfall: `spec align` takes NO positional ROOT (flag-only) even though its
`--help` prose says the `--spec-root` default is "the positional ROOT" (AUG-206).

