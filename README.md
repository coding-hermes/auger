# auger

Spec drilling for projects, backed by DuckBrain.

The `spec-decomposition-matrix-tradeoff` skill works — but it is a skill an agent picks up and
puts down. Auger makes it a program: it keeps drilling, remembers every choice and the reason
the alternatives lost, scores its own confidence, and can render what the system looks like
under a different set of choices.

**Status: v0.1 — the loop works end to end against live DuckBrain + JEV.** See
[docs/DESIGN.md](docs/DESIGN.md) for the concept and
[docs/SUBSTRATE-VERIFICATION.md](docs/SUBSTRATE-VERIFICATION.md) for the live proof that the
substrate does what this design assumes.

## Substrate pin (read before installing)

Auger is built on DuckBrain's **declared-tables HTTP API** (`/api/ns/<ns>/tables/...`).
That API exists on the `feat/native-s3` branch of
[wojons/duckbrain](https://github.com/wojons/duckbrain) — **not** on the public
`main` branch (`ce936ae`), where `POST /api/ns/<ns>/tables/<table>` returns
`404 ROUTE_NOT_FOUND`. Against default `main`, `auger init` claims success while
declaring 0 tables and `auger start` fails with a 404.

Install the working substrate:

```bash
git clone https://github.com/wojons/duckbrain ~/duckbrain
cd ~/duckbrain && git checkout feat/native-s3   # verified working: e5fdbd3
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
auger verdict     judge a configuration good/bad with reasons, on the record (DESIGN R11)
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
python3 auger.py -n myproject answer --id D-001 --domain 4.05 \
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
grid's shape is exact — one `4.NN-<slug>.md` file per domain (`4.05-data.md`; the file name IS
the number and the name), all 44 of `4.01`–`4.44` and nothing else; anything short of that is
refused by name rather than seeded partially.

Requirements: a running DuckBrain on `127.0.0.1:3000` (`~/duckbrain`, `node bin/duckbrain.js http`)
with its token at `~/.duckbrain/foreman-status.token`, and an OpenRouter key in `~/.hermes/.env`
for JEV. Python 3, standard library only — no dependencies.

## Where the data lives

Nothing is stored in this repo. Every row lives in the DuckBrain namespace you name, as
declared tables over git-backed JSONL:

```
~/duckbrain/namespaces/<ns>/tables/<table>.table.json   the declared schema
~/duckbrain/namespaces/<ns>/tables/<table>.jsonl        the rows
```

That means the spec is versioned in git, readable as plain files, queryable over HTTP, and
searchable by embedding — and the dump is a *view* of it, not a separate artifact to keep in
sync.

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

## Scratch files

Scratch scripts (tick probes, one-off checks) never live in this repo — write them under
`/tmp/`, not the workdir. Untracked `.tmp_*` litter in the repo root used to turn the
Tier-1 lint arm red on a clean tree (a committed tree can lint clean while the gate fails
on a scratch file it never should have seen); `.gitignore` now carries a repo-root
`.tmp_*` rule as the backstop, and ruff respects it, so `ruff check .` stays clean even
when litter exists. The convention is the fix; the gitignore line is the seatbelt.
