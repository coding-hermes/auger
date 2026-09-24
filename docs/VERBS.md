# The verbs

One entry per verb the CLI ships: its arguments, what it writes, and what it must never
write. This is the contract — the verb names are public, and every argument listed here
exists in the verb's `add_parser` definition in `auger.py`. If the code and this file
disagree, the code wins and this file is a bug.

The 12 verbs — one `add_parser` registration each in `auger.py`, and the count here is the
registry's own, not a hand-kept tally — in the order `auger --help` lists them:

```
init  start  ask  answer  check  status  toggle  dump  propagate  feedback  recall  verdict
```

Conventions every verb shares:

- `--namespace/-n` selects the DuckBrain namespace (default `$AUGER_NS`, else `auger`);
  `--project-id/-p` names the project row. Verbs that need a project resolve it from
  `--project-id`, or fall back to the most recent `project` row in the namespace.
- "Writes" means rows in the declared tables and/or entries in DuckBrain's embedded
  memory store (`/auger/<project>/<thing>` keys). Nothing is ever stored in this repo.
- A refused write exits non-zero and says why. A model call that fails is fail-closed:
  no verdict is invented, no question is made up, nothing is written on an answer the
  model did not give.

---

## init

Create (or verify) the namespace and declare the SDM tables. The namespace list is only a fast
path: `init` verifies the namespace resource itself and treats a confirmed create conflict as
`existing`.

**Arguments:** `--seed-domains` (also seed the 44-domain grid as `domain` rows). Without it
there is nothing beyond the top-level `--namespace`.

**Writes:** the namespace itself, when it does not exist yet, and one declaration file
per table in `COLS` — 14 today — under `~/duckbrain/namespaces/<ns>/tables/<table>.table.json`.
Idempotent: a declaration file whose content already matches is not rewritten. The run states
the tally itself (`declared: 14/14 tables -> ...`), which is how the count the README repeats is
checked against the code rather than trusted.
With `--seed-domains`, a further 44 `domain` rows — one per domain of the method's grid,
4.01-4.44 — read from the skill's own `design/02-domains/` files (the default path is the
dogfood artifact under `~/.hermes/skills/`; `AUGER_DOMAIN_GRID` moves it) rather than copied
into this repo, because a copy would drift while the skill's grid does not. Each row carries
the grid's `num`, `name`, triage score, ring floor and terminating ring, and starts
`NOT-REACHED` — owner `unassigned`, trigger `first question in domain`, containment `none`
(the `domain` table has no `reason` column; the reason for every seeded row is the same
sentence, so the report states it rather than the table holding 44 copies). Rows are bound to
the project that already exists, or left unbound when `init` runs before `start`. Idempotent
the same way: a domain number already stored is skipped, never written twice. A grid that is
not exactly the canonical 44, or a file whose header cannot be read, is REFUSED by name — a
seeded 43 would be the silent-domain failure the rule exists to forbid.

**Never writes:** without `--seed-domains`, no rows in any table at all; and nothing in the
memory store either way. `init` shapes the namespace — its declarations, plus the grid's 44
`NOT-REACHED` rows when asked — and it never records an answer, a decision, or a question.

## start

Register the project and store + embed its seed.

**Arguments:** `--name` (defaults to the namespace), `--id` (defaults to
`P-<timestamp>`), `--seed` (inline text), `--seed-file` (path; wins over `--seed`).

**Writes:** one `project` row (id, name, seed, status `open`), and one embedded memory
under `/auger/<pid>/seed`.

**Never writes:** nothing else — no question, decision, or edge rows. A project that has
only run `start` has exactly one table row and one memory.

## ask

Surface the next questions: JEV's pick, plus every low-confidence decision.

**Arguments:** none.

**Writes:** one `question` row for the question JEV picks — `status=open`,
`qclass=ask_proposed`, `ring=1`, the proposal's text, and the two JEV values the table
declares columns for: `jev_already_answered` (the gate's noul for exactly that question)
and `jev_checked_at` — plus a full set of `facet` rows for it. Its id follows the table's
own convention (`next_id`: `Q-000001`), and it is printed as `stored question: <id>`. That
row is what makes the next move in the README's loop (`answer --question-id Q-...`) a real
one: before it, ask named a question no verb could store.

**Never writes:** a question when the proposal is below `T_SUBJECT`, when the gate scores
it already answered (`noul >= T_ANSWERED`), when the gate returns no verdict for it
(unknown is not "genuinely new"), or when the same text is already open in this project
(said out loud, not stored twice). No decision, no option, no edge, no memory entry: an
`opens` edge would name a decision the proposal does not come from. A refused row insert
stores nothing (fail-closed); a failure after the row landed is reported as a partial
store instead of being printed as a clean one. `ask` stays a report — exit 0 for every
storage outcome, and exit non-zero only when JEV itself is unreachable (`JEV unavailable`,
nothing stored).

## answer

Record a decision with its rejected alternatives.

**Arguments:** `--chosen` (required), `--id` (defaults to the next `D-` number),
`--option` (repeatable — every alternative, chosen ones included), `--why-not`,
`--domain`, `--question-id`, `--reversal-cost`, `--confidence` (float), `--status`
(defaults `decided`), `--scope` (`project` | `bundle`, default `project`).

**Writes:** one `decision` row; one `option` row per `--option` (id `<D-###>-O<n>`,
`active` on exactly the chosen one); one embedded memory under
`/auger/<pid>/<did>` stating the choice, the rejected alternatives, and the reason —
never listing the chosen option as rejected. With `--question-id`: a `closes` edge
(`decision` → `question`) and the question moved to `answered`, its reason recorded on
its `facet` rows. With `--scope bundle` (as stored): the bundle impact pass — `affects`
or `breaks` edges naming each sibling's bundle-scoped decision the answer touches, and
an `escalation` row for every break, recorded with a default action.

**Never writes:** anything into a sibling project's namespace — a break in a member's
contract is recorded as an edge and an escalation HERE, naming the sibling there; the
sibling's rows are read, never rewritten. Without `--question-id` it never touches
`question`, `facet`, or `edge` rows at all.

## check

Is this question already answered by what we hold?

**Arguments:** `question` (positional), `--limit` (retrieval depth, default 5).

**Writes:** nothing. It retrieves the nearest stored evidence and makes one JEV call.

**Never writes:** anything. On a JEV failure it prints UNKNOWN and exits non-zero —
"unknown" is never turned into "already answered" or "new".

## status

The confidence map: coverage by domain, what is thin, what is contradictory — and what the
44-domain grid does not have at all.

**Arguments:** none.

**Writes:** nothing. Reads decisions, options, escalations, unknowns, domains, and the
question/edge rows, and prints the report. Two coverage blocks come out of that: the decisions
grouped by the domain string they carry, and — from the stored `domain` rows — the method's
grid walked domain by domain, one line each, with the row's state, the grid's triage score and
ring floor, the TERMINATING RING, and how many decision/question rows carry that number
(the evidence half of "answered vs NOT-REACHED"). A grid domain with no stored row prints
`ABSENT` and is repeated by number in an explicit list: absence is a claim of its own, and
"we have no row for 4.21" is not the same sentence as "4.21 does not apply". A stored number
the grid does not define prints as off-grid. When the grid cannot be read at all, `status`
says so and claims nothing about absence — with no expected set there is nothing to be absent
against — instead of printing a silent zero.

**Never writes:** anything. A drifted record (a decision with zero or several active
options) is named in the output, not "fixed" — and a `domain` row still reading `NOT-REACHED`
while a decision carries its number is reported as exactly that, not quietly promoted.

## toggle

Turn options on/off — the what-if switch, one active option per decision by default.

**Arguments:** `--on OPTION` (repeatable), `--off OPTION` (repeatable),
`--set OPTION=on|off` (repeatable), `--additive` (do NOT deactivate the target's siblings).
`OPTION` is a full option id such as `D-001-O2`, a label such as `Postgres`,
or a bare index such as `O2`. An unresolvable or ambiguous token is refused by name
before any PATCH is attempted; it is never reported as a successful zero-row toggle.

**Writes:** `option.active` flags only, via PATCH. By default turning an option on
turns its siblings off, so the decision keeps exactly one active option — and the flips
are part of the printed report, not a silent side effect.

**Never writes:** any other table, any new row, any deletion. `toggle` selects between
existing options; it cannot create or remove one.

## dump

Render the system as it looks with the current set — or a `--config` hypothesis.

**Arguments:** `--config D-001=OPTION` (repeatable; the value may be the option's full id,
`D-001-O2`, its label matched case-insensitively, or its bare index `O2`), `--out FILE` (write the markdown there
instead of stdout). Every `--config` entry must name an existing decision and exactly one
option. An unresolvable or ambiguous entry is refused by name before anything is rendered;
there is no ignored-entry warning and a typo can never fall back to the current stored state.

**Writes:** nothing in the namespace — not in the current mode, not in hypothesis mode.
With `--out`, a local file. That is the whole write surface.

**Never writes:** any table or memory entry, ever. A hypothesis that contradicts the
recorded reasons is named in the output ("CONTRADICTIONS WITH THE RECORD"), not repaired.

## propagate

Run the graph walks: moot what a dead decision answered, reopen what went stale,
cascade down the `derives_from` tree, and re-gate the children of closed questions.

**Arguments:** `--no-gate` (rule walk only: no retrieval, no model call), `--recheck`
(re-gate questions the gate has already examined).

**Writes:** question `status` changes and their reasons on the `facet` rows (a question
with no facet rows gets the full set first); `jev_already_answered` / `jev_checked_at`
on every question the gate examines; `satisfies` edges (`decision` → `question`) for
questions the gate links to stored evidence — `source=gate`, the noul confidence on the
edge, and `src_project` naming the sibling whose namespace held the answer, when it did.

**Never writes:** a state change without its reason (facets are written before the
status flips); a link to a decision that cannot be read where it lives; anything on a
gate failure — the question stays open and the verb exits non-zero. The rule walk never
calls a model; the gate walk never invents an answer.

## feedback

Turn low-confidence decisions into the next question batch, bounded by a governor.

**Arguments:** `--budget N` — how many questions ONE run may ask (default 3, env
`AUGER_QUESTION_BUDGET`). A question the ceiling stopped is recorded, not dropped.

**Writes:** follow-up `question` rows (`qclass=follow_up`, the gate's verdict and check
timestamp carried on the row); a full set of `facet` rows per new question; `opens`
edges (`decision` → question) and `derives_from` edges (question → the parent question)
that make the branch trackable; `escalation` rows — one per decision thin enough to be
a priority judgment, plus the run-level `budget-thin: true` marker when the ceiling was
hit; and, for questions the gate REFUSES as already answered: the row recorded `linked`
to the decision that answers it plus a `satisfies` edge, never asked.

**Never writes:** a question when the proposer failed (no question is invented without
the model); a question whose gate verdict is unknown (not asked, not stored); a question
the gate scores as answered but whose evidence names no decision (not asked, not linked,
not stored — said out loud instead).

## recall

Semantic search over everything the namespace has embedded.

**Arguments:** `query` (positional), `--limit` (default 5).

**Writes:** nothing. One GET against the memory store.

**Never writes:** anything.

## verdict

Judge a configuration — good or bad, with the reasons — and put that judgment on the
record (DESIGN R11). Two shapes, one row shape: a verdict ON FILE (you or another model
judged it elsewhere), or `--ask-jev`, where JEV judges the dump itself — rendered exactly
as `dump` renders it, so the model judges the real artifact, not a summary string.

**Arguments:** exactly one of `--good [CONFIG]` / `--bad [CONFIG]` (the configuration
string that was judged); `--reasons TEXT` (why); `--ask-jev` (JEV judges the dump first);
`--config D-001=Postgres` (repeatable — judge a hypothetical option set instead of the active
one, requires `--ask-jev`; the value is the option's full id, `D-001-O2`, its bare index
`O2`, or its label, case-insensitive); `--judged-by NAME` (default `human`; `--ask-jev` forces
`jev`); `--confidence X`; `--note TEXT`; `--list` (show every recorded verdict, newest
first).

**Writes:** one `verdict` row per call (`id` V-prefixed, `config_summary`, the word,
`reasons`, `judged_by`, `confidence` when given). `--ask-jev` is fail-closed: no key, no
score, NO row. `status` reads verdicts to tally good/bad per project.

**What `config_summary` holds — the `--config` semantics.** The row names the artifact that
was judged, and the two arms name it differently:

- With `--ask-jev`, `config_summary` is the `ACTIVE CONFIGURATION:` line of the render JEV
  judged — the first line of the dump that starts with those words, read back off the rendered
  artifact, never off the flags. A HYPOTHETICAL render still summarizes the ACTIVE
  CONFIGURATION line. `verdict --ask-jev --config D-001=Postgres` renders
  `mode: HYPOTHETICAL (nothing written)` and closes with
  `ACTIVE CONFIGURATION: D-001=Postgres, D-002=staging table` — the decisions you overrode
  carry the option you named, every other decision keeps its stored active option — and that
  whole line, prefix included, is what the row stores. The line names options by LABEL, so a
  hypothesis given as an option id (`--config D-001=D-001-O2`) is recorded as
  `ACTIVE CONFIGURATION: D-001=Postgres`: the verdict reads as evidence about a configuration
  even when the caller quoted an id, and even after that configuration is toggled away.
- Without `--ask-jev` (a verdict ON FILE) there is no render at all, so `config_summary` is
  the CONFIG string passed to `--good`/`--bad`, verbatim; a bare `--good` with no string and
  no `--ask-jev` therefore records an empty summary. That is also why `--config` without
  `--ask-jev` is refused by name: a hypothesis is a render, and only the rendering arm can
  name it.

**Never writes:** anything when both or neither of `--good`/`--bad` is given; anything
when an invalid word is named (the word set is closed in code — `good`, `bad`); a verdict
row when `--ask-jev` cannot reach JEV; a `--config` hypothetical without `--ask-jev`.
