# Run 7 — scale & concurrency (2026-09-24)

Lane: auger-dogfood · tick auger-dogfood-2026-09-24-18-11-36 · verdict **PROMISING-BUT-ROUGH**
(scales beautifully to the doorstep of 100 rows, then silently lies and locks)

## The promise under test

Runs 1–6 proved the loop on namespaces of 1–8 decisions. A real drill is not eight
decisions. This run asked the question none of them asked: **what does auger look
like grown up?** — 44 domains seeded, 100+ decisions, two agents writing the same
namespace at once, and the read/write verbs measured at that size.

## Setup (all on the shared substrate, shared namespace `auger-df7-scale`)

- `init --seed-domains` 1 s (44 NOT-REACHED domain rows) · `start --seed-file` <1 s.
- Serial leg: 60 × `answer` (3 options each) across 30 domains in **9 s**
  (6.7 answers/s, ~0.15 s warm per write) — zero errors, zero corruption.
- Concurrency leg: 2 parallel writers × 30 answers in **58 s**.
- Read costs at ~100 rows (hyperfine, n=10): `status` 1.99 s ± 1.28 s,
  `dump` 0.43 s ± 0.50 s; `propagate` 2 s; hypothesis dump (`--config D-001=O2`)
  196 ms; `recall` **22 s warm** at 61 embedded memories (run 5 measured 3.9 s
  on a small namespace — the wait scales with what's stored, not just the query).

## What broke (three defects, one of them P0)

### AUG-068 (P1) — reads silently truncate at the substrate's 100-row page cap

After 60 answers (180 option rows), `status` printed `options 100` and `dump`
rendered D-031 with 2 of its 3 options. The store held all 180 rows correctly.
Proven with raw curls: the declared-tables endpoint returns exactly 100 rows with
no `limit` (200 OK, no Content-Range, no truncation flag); `limit=101` returns 101.
`cmd_status`/`cmd_dump` never paginate. **There is no way to detect the cap from
the response alone** — the only signal is `len(rows) == limit` when you chose to
send a limit.

### AUG-069 (P0) — every write locks out permanently past 100 decisions

`cmd_answer` mints the default id from a row count:
`did = "D-" + (len(select(decision, project))+1)`. That unpaginated select caps at
100, so the mint sticks at `D-101` forever while the real next id is D-102+. Every
answer after the 100th — concurrent OR single-writer — is refused:

```
refused: decision 'D-101' already exists — answer decision IDs must be unique
```

The freeze outlives the writers and does not self-heal. Explicit `--id D-102`
succeeds; the next count-minted answer refuses again. The irony is on record in
the repo: `next_id()` (auger.py:519) exists precisely to avoid "the classic bug"
of count-based minting, is already page-safe (`order=id.desc&limit=1`), and is
used by every OTHER table — `cmd_answer` just doesn't use it for the default id.

### AUG-070 (P1) — concurrent writers corrupt ids; dump renders the collision as a real configuration

Two writers minted the same ids in the check-then-insert window (DuckBrain
enforces no uniqueness — auger's own comment says referential integrity is OURS).
The loser is refused instead of re-minting. Store afterwards: 102 decision rows
for 101 logical answers, 9 duplicate option ids, D-074/D-092/D-094 carrying 6
option rows each. User-visible: `dump --config` renders **D-074 twice with two
different real choices** ("Adopt approach 69A" and "Adopt approach 99A"), and the
drift warning that is supposed to flag a decision without exactly one active
option never fired — two `[x]` rows is still "one per decision" to the renderer
if it counts decisions, not options.

## What held up

- Serial writes at scale: 60 answers, 9 s, perfect consistency afterwards
  (verified per-decision against the store with an independent script).
- The refusal guard is fail-closed: a colliding write never half-lands; the id
  uniqueness refusal is correct behavior, just terminal when it shouldn't be.
- `dump --config` still named a contradiction with the record at 100 rows.
- `propagate` stayed at 2 s at scale; hypothesis dumps ~0.2 s.
- init/start stay sub-second; the 44-domain grid seeds correctly.

## Cost of using it at scale (headline numbers)

| operation | small namespace (runs 3/5) | this run (~100 rows) |
|---|---|---|
| answer (write) | ~0.1–0.2 s | 0.15 s serial · 58 s/30 concurrent (incl. refusals) |
| status | 0.20 s | 1.99 s ± 1.28 s |
| dump | 0.11 s | 0.43 s ± 0.50 s |
| recall | 3.9 s | **22 s** |
| propagate | — | 2 s |
| init + start | ~1 s | ~1 s |

## Install leg

SKIPPED-install-bunker, explicit: the documented fresh-install path was re-proven
same-day by run 5 on bunker las-bunker-03 (agent d1a530b6, substrate 142 s + loop,
smoke=ok). This run's fresh surface — scale and concurrency — is install-independent.
Host probed healthy (`bunker3`: HOST_OK, bunkerd active) but no agent was spawned.

## Off-by-one submissions (cadence post-debug)

Both classes were absent from the corpus (`not_found` on discover):

- `sub_623674` — unpaginated-table-select-silent-100-row-truncation
- `sub_a9b935` — row-count-id-minting-freeze-past-page-cap

## What a maintainer should fix first (one hour)

1. `cmd_answer`: mint via `next_id()` — one line, kills the P0 freeze (AUG-069).
2. Paginate `select` in `cmd_status`/`cmd_dump` (AUG-068) — or have `select()`
   re-fetch with a doubled limit whenever `len(rows) == limit` sent.
3. Duplicate-id refusal → re-mint + bounded retry (AUG-070).

## Reproduction

```bash
export AUGER_NS=auger-df7-scale    # left behind, 77+ decisions, intentionally unpolluted further
python3 auger.py status | sed -n 2p          # 'options 100' while the store holds 300+
python3 auger.py answer --chosen X           # refused: decision 'D-101' already exists
python3 auger.py answer --id D-200 --chosen X  # explicit id recovers
curl -s "http://127.0.0.1:3000/api/ns/$AUGER_NS/tables/decision?select=id&limit=1000" \
  -H "x-api-key: $(cat ~/.duckbrain/foreman-status.token)" | jq length
```
