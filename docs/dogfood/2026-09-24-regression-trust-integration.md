# auger dogfood integration report — 2026-09-24 (6th run: returning-user regression + trust leg)

Five prior runs swept the loop, the graph/bundles, the ask→answer seam, the raw HTTP
layer, and a real drill. This run asked what runs 1-5 did not:

1. **Do the fixes from run 5 actually hold?** (A returning user's first question.)
2. **Does the substrate survive a restart with a live namespace in it?** (The trust
   question no prior run answered.)
3. The verbs nobody had driven as workflow: `check` (the gate), `feedback` (the
   governor), `recall` as search, the toggle what-if lifecycle.

## The promise under test

> "A user can come back to a drilled decision set, check whether a question is
> already answered, drill further, and trust that what they recorded survives the
> substrate going down and coming back up."

## The run

Namespaces `df6-regress` (fresh) and `df7-dur` (fresh) on the control host, against
a dedicated ephemeral substrate on :3117 for the durability leg and the fleet
daemon on :3000 for the regression leg. Live JEV throughout.

### Fix verification (run 5's P1s)

| defect | run-5 behavior | run-6 result |
|---|---|---|
| AUG-055 chosen/option mismatch | accepted a `--chosen` matching no option, stored a configuration selecting NOTHING, printed success | **FIXED**: refuses rc=1, "no such option for D-001: '…' — use the full option id (like D-00X-OY) or a label; nothing was written" |
| AUG-056 gate re-proposes the closed question | ask re-stored the same question text as a new Q row | **FIXED**: `question not stored: already closed/answered as Q-000001 by D-001: answered by D-001` |

Both fixes verified by real use, not by reading the diff. Also confirmed idempotent
`init --seed-domains` on an existing namespace (44 already present, 0 rewritten).

### Durability leg (new this run)

Dedicated substrate (`DUCKBRAIN_DATA_DIR=/tmp/df6-dd`, port 3117, same embedding
env as the fleet unit). Loop: init → start → ask → answer (--confidence 0.9) →
`kill $(pidfile)` → verified port dead (HTTP 000) → restart from the same data
dir → dump + recall.

Result: **the namespace survived completely.** Decision D-001, its chosen option,
the active configuration, and both embeddings (seed 1.000 / D-001 0.492 on the
post-restart recall) all intact. This is the strongest trust evidence the project
has produced: prior runs proved the API layer; this proves the persistence layer
under the documented restart path.

### What works, verified live

- `check` discriminates well: "Where should the fleet's heavy multiarch docker
  builds run" → ALREADY ANSWERED (noul 0.95) on df5-real; an unrelated question →
  NOT YET ANSWERED (0.03) on the same namespace. But see AUG-063: it costs 6.5-9.7s
  warm (JEV round-trip), vs dump 0.10s / status 0.17s.
- `recall` as a returning-user workflow: query "docker build placement" on
  df5-real returned the seed, the decision that resolved it (0.984), and the
  related D-002 (0.484) — exactly what a person coming back after two weeks would
  want.
- Toggle what-if lifecycle: `toggle --off D-001-O2` → `dump --config D-001=O1`
  renders the hypothesis "mode: HYPOTHETICAL (nothing written)" with the option
  boxes flipped. Clean.
- `feedback` with a measured-thin decision (0.35) correctly buckets it as needing
  its own question.

### New findings (filed as AUG-061/062/063)

1. **AUG-061 (P1): `feedback` crashes with a raw `InvalidURL` traceback when the
   project name contains a space** — which `start --name` accepts and stores. The
   DB helpers interpolate query values into URLs unencoded (one `quote(` in the
   whole file). Any name with a space, `&`, `#`, or `?` breaks name-filtered
   lookups. A user hits this on their FIRST `feedback` after `start` with a
   natural name like "Fleet judge placement".
2. **AUG-062 (P2): the default `--confidence` sentinel -1.0 is user-facing** —
   dump prints `conf -1`, status would average it in, and feedback's thin filter
   silently treats unmeasured decisions as confident.
3. **AUG-063 (P3): check is 6.5-9.7s warm** — the gate verb is the second-slowest
   thing in the CLI; a local cosine prefilter could make obvious cases instant.

### Perf (measured, warm, 3 runs each)

| verb | warm | note |
|---|---|---|
| dump | 0.10s | |
| status | 0.16-0.17s | |
| check | 6.51-9.74s | JEV noul round-trip dominated |
| recall | ~1s | not re-measured this run (AUG-032 covers cold) |

### Verdict

🟡 **PROMISING-BUT-ROUGH** — the trust leg passed cleanly (the strongest result of
any run: real restart survival on the documented path), both run-5 P1 fixes hold
under real use, and the read/what-if surface remains excellent. The blocker moved:
it is no longer the write seam (fixed) but a single unencoded-URL interpolation
that crashes `feedback` for anyone who named their project the way humans name
things. One afternoon of URL-encoding at the db() boundary clears the last P1.

Time-to-first-success on the fresh namespace: ~3 min to a rendered configuration
(init/start/ask/answer/dump, 5.2s of it JEV).
