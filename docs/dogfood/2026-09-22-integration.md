# auger dogfood integration report — 2026-09-22

Real-use run: drilled a genuine micro-spec end-to-end (a fleet log-triage CLI
seed), on both the control box (against the production DuckBrain) and a fresh
ephemeral bunker agent (fresh clone, fresh DuckBrain, no prior state). This is
the record of how it actually went, not how the README wishes it went.

## Time-to-first-success

| leg | time | notes |
|---|---|---|
| control box, scratch namespace `auger-dogfood-df1` | ~90s total | init 0s · start 1s · ask 7s (JEV) · answer 0s · check 5s · toggle/dump/status 0s |
| fresh box, documented install path | **does not complete** | see AUG-016; works in ~4 min once `feat/native-s3` is checked out (npm install dominates) |

## The working loop (verbatim, proven)

```bash
export DUCKBRAIN_URL=http://127.0.0.1:3000      # or DUCKBRAIN_API_KEY / ~/.duckbrain/foreman-status.token
python3 auger.py -n myspec init                 # declares 13 tables (idempotent, repairs drifted files)
python3 auger.py -n myspec start --name demo --seed-file seed.txt
python3 auger.py -n myspec ask                  # JEV picks next question; ~5-7s = network RTT, not compute
python3 auger.py -n myspec answer --id D-001 --domain testability \
  --chosen "stream line-by-line with a dict of counters" \
  --option "stream line-by-line with a dict of counters" \
  --option "slurp whole file then Counter" \
  --why-not "slurping 500MB breaks the memory cap" \
  --reversal-cost medium --confidence 0.82
python3 auger.py -n myspec check "Should the tool hold the whole log in memory?"
#   -> retrieval 0.992 + JEV noul 0.93 => ALREADY ANSWERED, honestly
python3 auger.py -n myspec dump --config D-001="grep + sort | uniq -c pipeline"
#   -> hypothetical render, names the contradiction, writes NOTHING (verified byte-level)
```

What each verb actually writes (the part the docs don't say): `init` writes
declaration files; `start` writes project + seed; `answer` writes decision +
option rows + embeddings; `toggle` PATCHes option.active; `ask`/`check`/
`status`/`dump`/`recall` write nothing (dump --config proven no-op).

## Errors hit, and what they actually meant

- `declared: 0/13 tables` + `404 ROUTE_NOT_FOUND` on any table route → the
  DuckBrain you are running has no declared-tables API (public default branch).
  Not a bug in your invocation. See AUG-016.
- `EACCES ... /tmp/duckbrain-http.pid` when starting DuckBrain → someone else
  on the shared host already runs one; on default-branch duckbrain the pidfile
  is unsuffixed and unwritable for you. `DUCKBRAIN_DATA_DIR` env moves it; the
  per-port pidfile only exists on `feat/native-s3`.
- `auger toggle --on X` expecting a boolean → the flag takes the option ID
  (`--on D-001-O3`). The help text reads like `--on` is a switch. (Cosmetic,
  noted in AUG-015 context.)
- `could not create namespace (409)` right after a failed first attempt → the
  namespace row from the failed run survives; `init` again after the substrate
  is fixed, or use a new namespace name.

## Fresh-machine notes (bunker las-bunker-03, bare Debian user)

- auger itself: clone-and-run is REAL — stdlib-only claim verified (help +
  compileall + full read verbs with zero installs).
- DuckBrain (the substrate) needs node 22 + a package install (`tsx` et al) and
  is the hard part of the quickstart; the auger README understates this.
- No auth needed for a fresh daemon (`--auth=apikey` is opt-in); auger accepts
  any non-empty `DUCKBRAIN_API_KEY` in that case.
- Embedding: a fresh daemon with no embedding provider runs DEGRADED; `start`
  still embedded the seed successfully via the box's auto-probe on the branch
  build, but a truly airgapped box would fail `start`. Not yet a finding —
  needs one clean repro (see diagnostics).
