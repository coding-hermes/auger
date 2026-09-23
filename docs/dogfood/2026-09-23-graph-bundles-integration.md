# auger dogfood integration report — 2026-09-23 (graph, bundles, verdict surface)

Second real-use run. The 2026-09-22 run proved the entry surface (init/start/
ask/answer/check/toggle/dump/status) and the fresh-install pairing. This run
took the surfaces that run did NOT touch — the pitfall rule: re-dogfooding a
project means changing the ANGLE, because the untouched surface is where the
defects live. That rule paid again:

- the four never-field-tested verbs: `propagate`, `feedback`, `recall`, `verdict`
- SPEC-001 graph walks (moot / reopen / gate) over real edges
- SPEC-002 bundles: a two-project contract with a cross-project impact pass

## The scenario (a genuine use, not a test script)

Two projects sharing one contract, exactly the shape SPEC-002 was written for:

- `mccli` — an MQTT→sqlite recorder for a workshop (the producer)
- `mcview` — the dashboard that replays the store (the consumer)
- contract B-01 `event-envelope`: hash + seq on every event; both decisions
  recorded `--scope bundle`

Everything below ran with real verbs against the production DuckBrain
(`feat/native-s3`), JEV live over OpenRouter. Scratch namespaces `mccli` and
`mcview` (the member-namespace convention — see finding AUG-029).

## What WORKS (all proven live, quoted from the run)

- **The graph is real and honest.** `feedback` turned decision D-007
  (confidence 0.45) into Q-000001 in 17s; the question text showed the model
  had READ the seed ("…the storage constraints of the N100 device…"). Answering
  with `--question-id` wrote the `closes` edge and closed the question. Six
  edges later the graph read like documentation: `opens` rows annotate WHY
  ("the confidence in D-007 is 0.45 (< 0.6), so the engine proposed a
  follow-up (deepseek/deepseek-v3.2)").
- **The gate is a real dedupe.** Proposed Q-000004 was REFUSED as already
  answered by D-009 (noul 0.69) — recorded `linked` with a `satisfies` edge,
  never asked. Cross-decision dedupe for free, at $0.00003/call.
- **propagate moot cascade works** (given its trigger — see AUG-028): after
  decision D-010 recorded that "the July bench meter was mis-wired", a
  `breaks` edge D-010→D-008 made `propagate` moot the question D-008 had
  closed, with the reason stored on the question's facets.
- **`verdict` judged MY workflow, correctly.** Driving the scenario left five
  envelope decisions and three retention decisions simultaneously active.
  `verdict --ask-jev` on that configuration: **BAD at noul 0.36** (and 0.14 on
  a worse variant) — the model saw the incoherence the engine itself does not
  flag (now AUG-030). Cost of the call: $0.00010.
- **`dump --config` hypothesis rendering is correct** (initially suspected
  broken — re-checked: the `[ ]`/`[x]` flip is right; the first grep was too
  narrow) and writes nothing.

## The findings (full rows on the board: AUG-027…032)

1. **AUG-027 (P1) — the bundle impact pass is invisible and floor-less.**
   `answer --scope bundle` on mccli (D-003..D-006) returned in ~1s with no
   output beyond "recorded". Function-level shims proved the pass DOES walk
   the sibling — and that JEV scored the sha256→blake3 envelope swap as
   "unchanged" at confidence **0.24**, which the engine accepted: no edge, no
   warning, no line. Any human would call that a break. Two defects stack:
   no confidence floor on impact verdicts (fail-OPEN, against the module's own
   doctrine), and `cmd_answer` prints nothing for unchanged/skipped walks.
   Repro: `shim` logs referenced in diagnostics.md; namespaces still hold the
   rows.
2. **AUG-028 (P1) — the moot cascade has no verb.** The walk fires only on a
   LOCAL `breaks` edge, which no command can write (the impact pass always
   sets `dst_project`; the `option.breaks` free-text column is unconnected to
   the edge graph). I hand-POSTed edge E-900001 to trigger it — exactly what
   the project's own tests do. The documented loop "moot what a dead decision
   answered" is currently test-only.
3. **AUG-029 (P2) — SPEC-002 is not usable from the CLI.** No verb creates
   bundle/bundle_member rows; `bundle_member()`'s verification REQUIRES a
   readable fleet scheduler DB (refuses otherwise); and the member-namespace
   convention (a member's rows live in the namespace named exactly after the
   member) exists only in a code docstring — zero hits in docs/. My first
   pairing (both projects in one namespace) silently crossed nothing.
4. **AUG-030 (P2) — no supersession.** Re-answering a contract area leaves the
   old decision active; the configuration self-contradicts and only JEV's
   external verdict notices. Needs a `supersedes` edge + status + dump/status
   rendering.
5. **AUG-031 (P2) — docs drift.** README lists 13 tables (verdict, added
   c8c32fa, missing); VERBS.md index line still lists 11 verbs (the full
   verdict entry exists — the index was missed); `verdict --config` stores an
   "ACTIVE CONFIGURATION:" summary even for HYPOTHETICAL renders, which is
   wrong wording and enormous in `--list`.
6. **AUG-032 (P3, perf) — cold recall 25s vs warm 2s.** First search a user
   runs pays ~12x. Wall-clock, one cold + one warm run; embedding RTT
   dominates. A docs line would fix the UX; a cache would fix the number.

## Verb timing table (this run, warm unless noted)

| verb | time | notes |
|---|---|---|
| init --seed-domains | <1s | 44 domain rows, idempotent |
| start | <1s | seed stored + embedded |
| answer (project scope) | <1s | no model call |
| answer --scope bundle | ~1s | impact pass invisible — AUG-027 |
| feedback --budget N | 5-17s | one proposer + N gate calls |
| propagate | 0-2s | 0s = nothing to gate (correct); 2s = one recheck |
| recall (cold / warm) | 25s / 2s | AUG-032 |
| verdict --ask-jev | 1s | ~$0.0001/call |
| dump --config | <1s | writes nothing |

## Verdict on the surface

The graph engine and the feedback/gate loop are the strongest parts of the
tool — they read like they were designed by someone who has been burned by
silent drift, and they WORK. The bundle layer is designed just as carefully
on paper and is the weakest part in practice: invisible when it runs,
unreachable when you need to feed it (AUG-027..029 all land there). The
verdict verb is new and does what R11 promised.

Install leg: see the bunker section below; auger itself remains
clone-and-run (stdlib only), verified on a fresh Debian user.

## Fresh-machine notes (bunker las-bunker-03, agent 864a37ab, destroyed)

Re-ran the install leg because `eebdac3` (domain seeding) landed after the
2026-09-22 proof. Fresh bare Debian user, node 22.23 + python 3.13
preinstalled, nothing else:

- **auger itself: clone-and-run is REAL.** Clone 1s; full loop (init 14/14
  tables → start → status) in **7s with zero installs**. The stdlib-only
  claim held again.
- **The substrate quickstart needed 3 undocumented steps** (now AUG-033):
  1. `pnpm` is missing and both standard bootstraps hit root walls on this
     box — `corepack enable pnpm` → EACCES symlinking /usr/bin/pnpm; `npm i
     -g pnpm` → EACCES in /usr/lib. Working path: `npm config set prefix
     ~/.npm-global` + PATH + `npm i -g pnpm` (5s). After that: install+build
     green in ~76s (pnpm 12.4.2, typescript 7.0.2, vitest 5.0.1).
  2. `DUCKBRAIN_DATA_DIR=~/dd-data` daemon start died `ENOENT ...
     duckbrain-http-3310.pid` until `mkdir -p ~/dd-data` — the error names
     the pidfile, never the missing directory.
  3. `init --seed-domains` correctly REFUSES on a box without the skill tree
     ("grid not found … point AUGER_DOMAIN_GRID at …") — good and loud, but
     the README quickstart runs it bare, so a fresh user hits it immediately.
- Daemon booted `degraded` (no embedding provider on the box — correct),
  declared-tables route 404 until auger's own `init` declared tables (that is
  how declared tables work: the API serves what auger declares; the
  smoke's 404 on an undeclared table is CORRECT behavior, not a failure).
- `auger start` embedded the seed even with a degraded embedding provider
  (16-char seed; the store accepted it — cross-checked on 09-22 with a real
  embedding via the box's auto-probe; on a truly airgapped box this needs one
  clean repro, still open from last run).

Install leg totals: substrate bootstrap+install+build ~85s of tool time once
the three gaps are known; smoke 7s. agent destroyed, key removed.
