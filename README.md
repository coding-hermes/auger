# auger

Spec drilling for projects, backed by DuckBrain.

The `spec-decomposition-matrix-tradeoff` skill works — but it is a skill an agent picks up and
puts down. Auger makes it a program: it keeps drilling, remembers every choice and the reason
the alternatives lost, scores its own confidence, and can render what the system looks like
under a different set of choices.

**Status: v0.2 (tag [v0.2.0](https://github.com/coding-hermes/auger/releases/tag/v0.2.0)) — the loop works end to end against live DuckBrain + JEV.** See
[docs/DESIGN.md](docs/DESIGN.md) for the concept and
[docs/SUBSTRATE-VERIFICATION.md](docs/SUBSTRATE-VERIFICATION.md) for the live proof that the
substrate does what this design assumes.

## Substrate pin (read before installing)

Auger is built on DuckBrain's **declared-tables HTTP API** (`/api/ns/<ns>/tables/...`).
That API now exists on the public `main` branch of
[wojons/duckbrain](https://github.com/wojons/duckbrain) — see commit 42cea0a1280c8d4937d95285b1f8c72feacaa62d.
Against default `main`, `auger init` declares all tables and `auger start` succeeds.

Install the working substrate:

```bash
git clone https://github.com/wojons/duckbrain ~/duckbrain
# main already has the working API; no checkout needed
pnpm install && pnpm build
node bin/duckbrain.js http
```

**First, on a bare box (no `pnpm`; node already installed).** The two standard routes both die
`EACCES` for a plain user — `corepack enable pnpm` symlinks into `/usr/bin`, `npm i -g pnpm`
writes `/usr/lib`. Install into a user prefix instead (~5s, pnpm 12.4.2, measured on the
2026-09-23 bunker leg):

```bash
npm config set prefix ~/.npm-global
export PATH=~/.npm-global/bin:$PATH
npm i -g pnpm
pnpm install && pnpm build          # ~76s from a cold checkout on the same leg
```

**And give the daemon a data directory that already exists.** The boot line above assumes the
default data dir is there; when you point `DUCKBRAIN_DATA_DIR` somewhere new, create it first —
the daemon writes its pidfile into it, and an absent directory surfaces as a pidfile `ENOENT`
that names the pidfile rather than the missing directory:

```bash
mkdir -p ~/dd-data
DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http
```

**On a box that already runs a production DuckBrain, data-dir env alone does not isolate a
second instance.** The namespace registry is the shared surface: the `/api/namespaces` census
is the union of `namespaceMappings` in `duckbrain.config.json` plus the on-disk scan, so
pointing `DUCKBRAIN_DATA_DIR` and `DUCKBRAIN_NAMESPACES_PATH` at a scratch dir still lists the
co-located production namespaces (review-lane finding AUG-090). Writes do land in the scratch
data dir, but the namespace list is wrong, which can mislead you into thinking scratch writes
reached production. Isolate the config too:

```bash
mkdir -p ~/dd-scratch
printf '{"namespaceMappings": {}, "namespacesPath": "~/dd-scratch/namespaces"}' > ~/dd-scratch/config.json
DUCKBRAIN_DATA_DIR=~/dd-scratch \
DUCKBRAIN_CONFIG_PATH=~/dd-scratch/config.json \
node bin/duckbrain.js http
```

`DUCKBRAIN_CONFIG_PATH` is an env-only runtime override (same pattern as
`DUCKBRAIN_NAMESPACES_PATH`): it is read at boot and never written back into the config file.
After boot, confirm isolation by checking that `/api/namespaces` on the new instance is empty.

## The loop

```
auger init        point it at a namespace; it declares the tables it needs if absent
auger start       register the project, store + embed the seed
auger ask         JEV says what to drill next, and how complete we are
auger check "Q"   is Q already answered by what we hold?  (retrieval + JEV confidence)
auger answer ...  record the decision, the rejected options, and why
auger status      confidence map, coverage by domain, what is thin
auger toggle      flip options — the what-if switch
auger dump        render the system under the current set, or a --config hypothesis
auger propagate   run the graph walks: moot, reopen, cascade — and re-gate the children
auger feedback    turn thin decisions into the next question batch (model proposes, JEV gates)
auger recall "Q"  semantic search over everything the project has embedded
                  (first recall embeds the query — can take ~20s; progress on stderr)
auger serve       serve the embedding store over HTTP — semantic search for external agents
auger bundle      group decisions into named bundles (add / member subcommands)
auger export      render the whole namespace as one readable spec document
auger verdict     judge a configuration good/bad with reasons, on the record (DESIGN R11)
auger record ...  write the registers: break, assumption, unknown, option costs/breaks
```

Every verb is documented — arguments, what it writes, what it must never write — in
[docs/VERBS.md](docs/VERBS.md).

## Quick start

First get the substrate up — the three commands that block a bare Debian box (no `pnpm`, no
data dir, no skill tree) are spelled out with their failure modes in **Substrate pin** above:

```bash
npm config set prefix ~/.npm-global && export PATH=~/.npm-global/bin:$PATH && npm i -g pnpm
mkdir -p ~/dd-data && DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http
# and for `init --seed-domains`: export AUGER_DOMAIN_GRID=<a canonical 44-domain grid>
```

Then the loop:

```bash
python3 auger.py -n myproject init
python3 auger.py -n myproject start --name myproject --seed-file seed.txt
python3 auger.py -n myproject answer --id D-001 \
    --chosen "single SQLite file" \
    --option "single SQLite file" --option "Postgres" --option "flat JSONL only" \
    --why-not "Postgres needs a service the seed forbids" \
    --reversal-cost medium --confidence 0.82
python3 auger.py -n myproject status
python3 auger.py -n myproject ask
python3 auger.py -n myproject check "How should we store the record of files we have seen?"
python3 auger.py -n myproject dump --config D-001=Postgres
```

`init --seed-domains` also seats the method's 44-domain grid as `domain` rows — but the grid
lives with the `spec-decomposition-matrix-tradeoff` skill, **not in this repo**, because a copy
here would drift the moment the skill's grid changes. On a box without that skill tree the
default path does not exist and the flag refuses (loudly, and correctly — a seeded 43 would be
the silent-domain failure the rule exists to forbid):

```
domain grid not found: ~/.hermes/skills/software-development/spec-decomposition-matrix-tradeoff/references/dogfood-artifact/02-domains — point AUGER_DOMAIN_GRID at the skill's references/dogfood-artifact/02-domains directory
```

Point `AUGER_DOMAIN_GRID` at any canonical 44-domain grid, or run `init` without
`--seed-domains`: the domain rows are the coverage map, not a prerequisite for the loop. The
same holds for `answer --domain`: on a box without the grid, omit the flag (as the Quick
start loop above does); once `AUGER_DOMAIN_GRID` points at a grid, add it back (`--domain 4.05`)
to file the decision under a domain. The
grid's shape is exact — one `4.NN-<slug>.md` file per domain (`4.05-data.md`; the file name IS
the number and the name), all 44 of `4.01`–`4.44` and nothing else; anything short of that is
refused by name rather than seeded partially.

Requirements: a running DuckBrain (see below) with authentication set up, and an
OpenRouter key for JEV — `OPENROUTER_API_KEY` (or `OR_API_KEY`) in the environment wins,
otherwise every `sk-or-v1-` key found in `~/.hermes/.env` is used. Python 3, standard
library only — no dependencies.

**Substrate port on shared hosts.** The daemon's default address is
`127.0.0.1:3000` (`node bin/duckbrain.js http`), and auger reads `DUCKBRAIN_URL`
(env, default `http://127.0.0.1:3000`). On a box where several people (or agents)
run DuckBrain, that fixed port collides: your boot can lose the race or lose it
silently, and the requests meant for your instance get answered by *someone
else's* daemon — you see another agent's namespaces and wrong-looking 500s. So on
any multi-user box, boot on a port you verified free and point auger at it:

```bash
ss -tlnp | grep :3987        # must print nothing before you boot on it
node bin/duckbrain.js http --port 3987
export DUCKBRAIN_URL=http://127.0.0.1:3987
```

**Authentication.** auger checks `DUCKBRAIN_API_KEY` first; if unset it falls back to
token files in order: `~/.duckbrain/foreman-status.token`, then `~/.duckbrain/token`.
On a fresh box, set the env var (`export DUCKBRAIN_API_KEY=<your-key>`) or create one
of the token files before running `init` — without either, every command exits rc=1
with `no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token`.

## Where the data lives

Nothing is stored in this repo. Every row lives in the DuckBrain namespace you name, as
declared tables over the JSONL files that namespace holds:

```
~/duckbrain/namespaces/<ns>/tables/<table>.table.json   the declared schema
~/duckbrain/namespaces/<ns>/tables/<table>.jsonl        the rows
```

That means the spec lives in the DuckBrain database (the namespace you name): readable as plain
files, queryable over HTTP, and searchable by embedding; the dump is a *view* of it, not a
separate artifact to keep in sync. DuckBrain's own repo gitignores `/namespaces/`, so these
files are not tracked by git and carry no git history.

The rows are queryable through DuckBrain's declared-table API as they stand; the embeddings the
write verbs store are published as their own route by `auger serve` (AUG-046):

```bash
# the namespace you practiced in; AUGER_NS is the env default every verb honours
AUGER_NS=myproject python3 auger.py serve --port 8765
curl "http://127.0.0.1:8765/api/ns/myproject/embeddings/search?q=how+is+the+record+stored&limit=5"
```

That answers ranked, scored hits as JSON — each tagged with the decision its key is evidence of,
and with the retrieval tier the substrate's own `/health` reports. See
[docs/VERBS.md](docs/VERBS.md), section *serve*, for the routes, the refusals, and what the
score scale means.

## The tables

Fourteen, and `COLS` in `auger.py` is the source of truth for this list; this section follows
its order. The count is not asserted on trust here — `init` declares the tables and prints the
tally, so one command checks this section against the code:

```bash
python3 auger.py -n myproject init
# declared: 14/14 tables -> assumption, break, bundle, bundle_member, decision, domain,
# edge, escalation, facet, option, project, question, unknown, verdict
```

The tables are the registers of the SDM method, made addressable:

```
project  question  decision  option  break  escalation  assumption  unknown  domain
edge     facet     bundle    bundle_member  verdict
```

- `project` · `question` · `decision` · `option` · `break` · `escalation` · `assumption` ·
  `unknown` · `domain` are the original registers: the project and its seed, the open
  questions, the decisions with their rejected alternatives and confidence, the what-if
  switches, what a choice breaks, what only a person may decide, what is assumed, what is
  not yet known, and the domain map.
- `edge` and `facet` are the graph (SPEC-001): `edge` holds the relationships the engine
  reasons over (`derives_from`, `blocks`, `closes`, `breaks`, `satisfies`, ...), `facet`
  holds one row per way of looking at every question — and, practically, each question's
  reason for being in the state it is in.
- `bundle` and `bundle_member` are the contracts (SPEC-002): which projects must honour
  one contract. `option` is what makes the what-if engine possible: each decision keeps
  its rejected alternatives as rows, so a configuration is a selection across them rather
  than a rewrite.
- `verdict` is the judgment register (DESIGN R11): what a human or a model SAID about a
  rendered configuration — the word, the reasons, the judge, and the `config_summary` that
  names the artifact judged — so a judgment is evidence on file instead of a sentence in a
  transcript. `dump` renders the configuration; with `--ask-jev` the model renders its own
  verdict on that same render.

## Design rules this tool is built on

- **Never build a request body by string interpolation.** An apostrophe in `seed's rule`
  silently truncates a quoted JSON body. Payloads go to the HTTP layer as bytes from a file.
- **JEV is fail-closed.** A transport error or malformed answer becomes an explicit UNKNOWN —
  never a silent optimistic default.
- **Thresholds live in code, not in the model.** They are constants at the top of `auger.py`.
- **Evidence must not contradict itself.** The chosen option is chosen; only the others are
  rejected. Violating this made a genuinely-answered question score as unanswered (0.11 → 0.78
  once fixed).
- **A hypothesis never writes.** `dump --config` renders without touching the record, and it
  names the contradictions between the hypothesis and the reasons on file.

## Test

```bash
bash tests/smoke.sh          # full loop against a scratch namespace
```

## Muster contract (OpenAPI for consumers)

Auger publishes the OpenAPI 3.x contract the [muster](https://github.com/wojons/muster)
protocol consumes: `docs/openapi.yaml`, a GENERATED artifact (one source of truth —
`scripts/gen_openapi.py` emits it from the code's own registry, and
`tests/test_openapi_parity.py` fails the gate if the file drifts from the code or is
hand-edited). It describes the read-only HTTP API on `http://127.0.0.1:8766` — every
operation carries `x-safety: read-only`, and no mutating operation exists in the
contract. The exact `mcpServers` consumer block and the three-line quickstart live in
[docs/MUSTER-CONSUMER.md](docs/MUSTER-CONSUMER.md); the scope decision behind the
surface is [docs/muster-scope.md](docs/muster-scope.md). (The service that implements
this contract is the next build row; the existing `auger serve` embedding surface on
`:8765` is unchanged by it.)

## Limits and known gaps

Auger v0.2 is a day-one-plus-a-release proof, deliberately small: one standard-library Python file (`auger.py`),
no daemon, no auth layer of its own — it expects a substrate you control, a DuckBrain you started
with tokens you placed (see **Quick start → Authentication**), and it works on one namespace at a
time (the `-n` flag every verb takes). The dogfood integration reports under
[docs/dogfood/](docs/dogfood/) have graded the loop PROMISING-BUT-ROUGH run after run; the rough
edges below are the ones still live at this writing. Current known gaps are tracked in the
project's public issue tracker.

- **JEV scores carry model noise near thresholds; the `check` verdict does not.** The
  already-answered gate is a code constant (`T_ANSWERED = 0.55` in `auger.py`), but the score under
  it comes from the configured judge model: run 10 read identical evidence as ALREADY ANSWERED
  (0.55) and then NOT YET ANSWERED (0.53) three seconds apart
  (docs/dogfood/2026-09-26-error-surface-integration.md, AUG-086). Inside `T_ANSWERED_BAND` (0.03)
  of the threshold the score no longer decides: `check` reads the stored `question` rows instead —
  a settled question (`answered`/`linked`) whose text matches reads ALREADY ANSWERED whatever the
  score drifted to, and with no settled match it reads NOT YET ANSWERED — and prints which of the
  two decided it. Outside the band the score still decides, as it always has.
- **A green CI run proves the offline suite, not the live loop.** The arms that need a reachable
  DuckBrain and JEV keys are skipped by name under `AUGER_CI=1`, with a visible marker instead of
  a pass (.github/workflows/ci.yml, tests/gate.sh).
- **A second substrate on a shared host is not isolated by data-dir env alone.** With
  `DUCKBRAIN_DATA_DIR` + `DUCKBRAIN_NAMESPACES_PATH` pointed at a scratch dir, the instance still
  lists a co-located production DuckBrain's namespaces (review-lane finding AUG-090) — the
  namespace registry is the shared surface. See the *Substrate pin* section for the
  `DUCKBRAIN_CONFIG_PATH` escape hatch that isolates the config as well.
- **Bundle membership is validated against a fleet scheduler DB.** `bundle member` checks the
  project against a read-only `projects` table (docs/VERBS.md, section *bundle*); on a standalone
  box membership needs the documented `AUGER_ALLOW_SCRATCH_MEMBERS=1` escape, and membership
  management stays CLI-light.

## Scratch files

Scratch scripts (tick probes, one-off checks) never live in this repo — write them under
`/tmp/`, not the workdir. Untracked `.tmp_*` litter in the repo root used to turn the
Tier-1 lint arm red on a clean tree (a committed tree can lint clean while the gate fails
on a scratch file it never should have seen); `.gitignore` now carries a repo-root
`.tmp_*` rule as the backstop, and ruff respects it, so `ruff check .` stays clean even
when litter exists. The convention is the fix; the gitignore line is the seatbelt.
