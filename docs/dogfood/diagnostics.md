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

## Registers + seam run (2026-09-23, third run — report:
docs/dogfood/2026-09-23-registers-seam-integration.md)

How the write surface actually behaves, as learned by driving a real drill
(N100 backup policy, scratch namespace `auger-df-registers`):

- **A decision insert precedes its own validation.** `cmd_answer` stores the
  decision + options, THEN `close_question()` refuses a nonexistent
  `--question-id`. The refusal is by design (the spec is right about why) —
  but the order is wrong: a "refused" exit must mean nothing was stored.
  Because decision ids are not unique-keyed, the natural retry then created a
  duplicate id (dump rendered it twice; every derived count ×4).
- **`ask` was a proposal, not a write (AUG-035; run 2026-09-23).** Nothing it
  printed was persisted (the `question` table stayed empty after a call that
  named a next question). The only question-writer was `feedback` (thin
  decisions only), so the README's own loop — ask, then answer
  `--question-id` — could not complete for a not-yet-stored question. Anyone
  reading `ask`'s output as "the engine recorded the next question" was being
  set up for the non-atomic refusal above. Closed by AUG-035: `ask` now stores
  the question it proposes (one `question` row with `qclass=ask_proposed` plus
  its `facet` rows, printed as `stored question: <id>`), and `answer` checks
  `--question-id` before it writes anything.
- **Two CLI contracts lie in their own help text.** `toggle --on O2` (bare
  id) exits 0 with "toggled … (0)" and patches zero rows; `dump --config
  D-001=O2` (the form `--help` shows) is discarded as unparsed. The full
  id forms work; the discrepancy is discoverable only by diffing behavior
  against the help string.
- **`--domain` is free text.** `4.99` was accepted silently; a real decision
  landed on 4.27 (audio) because the verb never maps the 44 grid names to
  numbers and `status`'s domain-coverage display is one-index-off, so the
  number a user copies is wrong twice.
- **The substrate token the README names does not exist on a fresh box.**
  `~/.duckbrain/foreman-status.token` is a host-convention artifact (this
  control host has one because a previous deployment created it); a fresh
  boot creates no `~/.duckbrain/` at all and runs auth=none.
  `DUCKBRAIN_API_KEY=<any value>` satisfies auger's env-var read — the
  README just never says so.
- **Verified good, unchanged from run 2:** JEV's fail-closed behavior (fresh
  box without a key: check exits rc=1 with "UNKNOWN, not 'already
  answered'" — exactly the documented stance), the what-if hypothesis
  (nothing written, contradictions quoted from the record), verdict's
  record-then-judge flow, and the smoke suite's own hygiene (namespace
  sweep/teardown ran clean on the bunker).

## The HTTP data layer + the seam in live use (2026-09-23, fourth run —
report: docs/dogfood/2026-09-23-seam-http-integration.md)

The substrate's declared-table API, driven by a plain urllib integrator
(auger's own client code untouched), is mostly excellent and sharp in exactly
four places:

- **A 200 is not a write.** `PATCH ?pk=eq.D-002` returned 200 and the row was
  byte-identical afterward (confidence stayed 0.82); the disk JSONL never
  changed. This is AUG-036's silent-no-op class one layer BELOW auger — any
  integrator (and `toggle`'s PATCH path) must read the row back after a
  PATCH, never trust the status code (AUG-043).
- **The primary key is not enforced on POST.** A row POSTed without `id` is
  accepted 201 and stored with `id: null` — permanently unreachable (every
  PATCH/DELETE requires `pk=eq.<value>`, and there is no value). Removed by
  hand-editing the JSONL, which is the exact thing auger's own rules forbid
  (AUG-045).
- **"Versioned in git" is currently false.** duckbrain root-gitignores
  `/namespaces/` — the storage of record is untracked local files, no
  snapshot/commit machinery runs (the `_audit/current.jsonl` append log is an
  audit trail, not versioning). README + DESIGN both claim git versioning
  (AUG-044). Related: `count`/pagination params 200 with no Content-Range,
  and a bad-typed filter value 500s instead of 400 (same row).
- **The embedding store has no HTTP route.** `/memories` → 404 and it is not
  one of the 14 declared tables; "searchable by embedding" is true only
  through auger's verbs (AUG-046).

The AUG-035 seam itself, driven live five times: **it never fired.** Every
JEV proposal scored 0.15-0.24 against T_SUBJECT=0.45 (fail-closed refusals,
each printed with its reason — correct behavior), and the proposal text is
the criterion slug (`how_does_it_fail`), not a question sentence (AUG-041/
AUG-042). The loop the seam unblocks DOES complete end to end by the other
path, proven live: `feedback` proposed a question from a thin decision
(recorded as Q-000001 even when JEV refused it, qclass follow_up) →
`answer --question-id Q-000001` → `closed Q-000001`, status coverage updated,
propagate clean. Practical rule until AUG-041 is fixed: the question path is
feedback → answer, not ask → answer.


---

## 5th run (2026-09-24) — the real drill, and the write that reports success over nothing

This run stopped testing verbs and used the product on a real decision (where the
fleet's heavy multiarch docker builds should run — a decision the rethinkdb
2026-08-31 incident forced). It is the first run to judge the OUTPUT rather than
the mechanism, and the first to exercise `answer` at length.

### How the thing is built, and why the last step is silent

`answer` is the only verb that writes a CONFIGURATION rather than a record, and it
does so through one line:

```python
"active": opt == a.chosen,          # auger.py, cmd_answer's option insert
```

The `option` rows' `active` flag decides everything downstream — what `dump` calls
the ACTIVE CONFIGURATION, what `dump --config` diffs a hypothesis against, what
`toggle` flips, what `verdict` judges. It is set by a byte-for-byte string
comparison between `--chosen` and each `--option`.

That is the defect (AUG-055): `--chosen` is naturally a short name and `--option`
is naturally the full sentence, so the natural invocation stores ZERO active
options — and the verb prints `D-001 recorded (confidence -1.0, 1 options,
embedded, scope project)` with exit 0. Nothing downstream complains until you
render:

```
ACTIVE CONFIGURATION: (nothing active)
WARNING: decisions with != 1 active option (a configuration SELECTS one option per decision):
  - D-001: 0 of 1 options active — this decision contributes NOTHING to the configuration
```

The warning mechanism itself is good and was already hardened (AUG-015). It is the
WRITE that is the problem: it stores a state the API's own renderer calls broken
and reports it as success.

Workaround until fixed: **pass the chosen text verbatim as one of the --option
values.** Compare the two:

```
# stores an EMPTY configuration (exit 0, "recorded")
answer --chosen "The CI box smoke test" --option "CI smoke battery: bunker3 runs ..."

# stores a working configuration
answer --chosen "bunker for heavy builds" --option "bunker for heavy builds" --option "primary host, qemu locally"
```

If you already have a broken record, `toggle --on <option-id>` activates explicitly
and repairs it — the toggle path is sound and refuses ambiguity by name.

### The already-answered gate, and the duplicate it costs

`ask`'s gate is a similarity score (noul) of the proposed question against embedded
evidence. It has no notion of "this question was CLOSED one round ago", so after
answering Q-000001 the very next `ask` stored Q-000002 with the IDENTICAL text
("What test proves this works?"). Closing it required a second, duplicate decision
(D-002). The round after that, JEV correctly said "nothing left worth asking" — so
the loop does terminate; it just pays one wasted round first (AUG-056).

Note the compounding: a mismatched `--chosen` (AUG-055) writes evidence whose chosen
text is not the option text, which is part of what the gate scores. Fixing AUG-055
improves the gate's input but does not remove AUG-056 — the gate still needs the
closed-question identity, not a better score.

### Where the 500 came from on a fresh machine

The install leg's worst moment was not an auger error. On the shared CI box a fresh
agent's first write returned:

```
could not list namespaces (500): {'error': "Cannot determine the duckbrain root:
no duckbrain.config.json was found walking up from /home/bunker-864a37ab/duckbrain/..."}
```

`/home/bunker-864a37ab` is a DIFFERENT dogfood agent, already destroyed. The box was
holding four duckbrain daemons at once — `kara`'s on :3000 and orphans from
destroyed agents 4b004b7a and 864a37ab on :3100/:3210/:3311, still answering. A
fixed `127.0.0.1:3000` therefore reaches whoever owns the port, and you get a 500
whose message names a path that does not exist. The fix is addressing, not code: run
your own substrate on a port you verified is free and pin `DUCKBRAIN_URL`:

```bash
for p in 3901; do (echo >/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "$p BUSY" || echo "$p FREE"; done
cd ~/duckbrain && nohup env DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http --port 3901 &
export DUCKBRAIN_URL=http://127.0.0.1:3901 DUCKBRAIN_API_KEY=$(node bin/duckbrain.js token --name=me | sed -n 2p)
```

With that isolation the documented loop passed in 1 second (AUG-060). Never kill the
other daemon: on a multi-tenant box it may not be yours.

### What held up under real use

- `dump --config` named a CONTRADICTION WITH THE RECORD, quoting the on-file reason,
  for a hypothesis that differed from the stored choice. Unprompted, correct, and
  genuinely useful — no test asserts it.
- `toggle` refused an ambiguous bare token by name (`matches D-001-O1, D-002-O1,
  D-003-O1 — use the full option id; nothing was written`) and wrote nothing.
- `init --seed-domains` still reads the grid from the skill dir rather than copying
  it (42 NOT-REACHED rows after 3 decisions, 2 answered).
- `verdict --good <config> --reasons ...` recorded human judgement on the record.
- Costs, measured (`hyperfine`, 3-decision namespace): `status` 199 ms ± 12 ms,
  `dump` 111 ms ± 9 ms; `recall` 3.9 s; `ask` 5.0–6.4 s (a JEV call, ~$0.00004).
  `status` is ~10x faster than the 2.2 s ± 1.3 s reported in run 3 (evidence for
  AUG-047, not a new row).

## Run 7 (2026-09-24) — how the scale defects were found, and why they hid for six runs

Every earlier dogfood run judged auger on namespaces of 1–8 decisions. The
defects of run 7 live entirely past the 100th row, which is why six green runs
never saw them: the test suite's fixtures are small, and nothing else grows a
namespace. The construction was deliberately boring — 60 serial answers across
the 44-domain grid, then 30+30 from two parallel writers — with an independent
verification script reading the store directly after each leg.

The discovery chain is a lesson in silent-truncation debugging:

1. The symptom was INTERNAL INCONSISTENCY, not an error: `status` said
   "options 100" while dump's per-decision rendering disagreed with it
   (D-031 missing its third option). Neither printed anything wrong-looking on
   its own. When two views of one store disagree, believe neither and read the
   store directly (curl with `limit=1000`) — that probe took one minute and
   proved the data was fine and the READS were wrong.
2. Bisecting the endpoint with curl (`limit=99/100/101/179/180`) showed a hard
   page cap at 100 with no signal: 200 OK, no Content-Range, no field. A client
   can only detect it by comparing `len(rows)` to the limit it sent. Any
   integration on this substrate that "reads a table" without pagination is
   silently wrong past 100 rows — auger was the first caller big enough to find
   out.
3. The P0 write lockout fell out of the same cap through a different door:
   `cmd_answer` mints `D-<count+1>` from an unpaginated select, so the mint
   froze at D-101 while the store held 101+ decisions. The repo already contains
   the correct allocator (`next_id()`, highest-id-based, page-safe, and its
   docstring explicitly names the count-based approach as "the classic bug this
   deliberately does not have") — answer simply doesn't route through it. The
   general lesson: when a codebase carries a "we deliberately don't have bug X"
   comment, grep for every path that could still hit X anyway; the guarantee is
   only as wide as its callers.
4. The concurrency leg (2 writers × 30 answers) produced 18 refusals and left
   duplicate decision/option ids in the store — the check-then-insert window is
   unguarded because DuckBrain enforces no uniqueness. The visible consequence
   was nasty and quiet at once: `dump --config` rendered D-074 twice with two
   different real choices, no drift warning. A renderer that asserts "exactly
   one active option per decision" must count options per id, not rows per
   decision — duplicated ids are invisible to row-grouping.

Left behind on purpose: namespace `auger-df7-scale` (77 decisions, 303 option
rows, duplicates included) is the standing repro for AUG-068/069/070 — see the
reproduction block in `2026-09-24-scale-concurrency-integration.md`. Both
debugged classes were submitted to off-by-one post-debug (`sub_623674`,
`sub_a9b935`) so the next agent hitting a silent 100-row cap anywhere in this
substrate family gets the answer pre-solved.
