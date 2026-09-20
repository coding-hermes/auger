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
```

## Quick start

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

## The nine tables

`project` · `question` · `decision` · `option` · `break` · `escalation` · `assumption` ·
`unknown` · `domain`

They are the registers of the SDM method, made addressable. `option` is what makes the what-if
engine possible: each decision keeps its rejected alternatives as rows, so a configuration is a
selection across them rather than a rewrite.

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
