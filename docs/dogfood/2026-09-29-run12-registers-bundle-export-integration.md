# Dogfood: registers, bundle verbs, export — and the AUG-091 fresh-install re-verify (2026-09-29/30, run 12)

## Surface Tested

Three surfaces no prior run exercised, plus the completion of run 11's failed leg:

1. **`record`** — the register-writing verb (AUG-019's fix; every prior run confirmed
   the registers were read-only).
2. **`export`** — the whole-namespace deterministic spec render.
3. **`bundle` in its VERB form** — run 2 (09-23) could only create bundles by
   hand-POSTing rows to the table API (AUG-029); the verb family landed later and
   was never used by a dogfood run.
4. **The AUG-091 fix re-verified on a genuinely fresh box** — run 11 (09-29) proved
   a fresh user is blocked at `auger init` with no token guidance; this run repeated
   the fresh-box path AFTER the fix to confirm the P0 is actually closed.

## Environment

- Host: bunker-las-03, ephemeral agent `f4ec72e8` (TTL 2h, destroyed after test —
  `bunker destroy` verified: agent gone, local key removed).
- Bare Debian user: no sudo, no pnpm, no project toolchains.
- Transfer: git bundles over ssh (`auger.bundle` 1.9 MB, `dd.bundle` 31 MB), clone
  from bundle, then `git remote set-url` back to the real origins. **Honesty note:**
  the duckbrain clone from the bundle landed on `feat/native-s3` (bundle HEAD = the
  local checkout's branch), NOT default `main`. This leg therefore proves the
  README's *pinned* substrate path works on a fresh box; it does NOT verify the
  README's newer claim that default `main` also has the declared-tables API. A real
  fresh user cloning from GitHub would get `main` (origin/HEAD) — that path remains
  untested by this run.
- Versions: pnpm 12.8.1, node 22, duckbrain `8f14f52`, auger `d0c6992` (HEAD-matched
  to the control-host repo at run time).

## The fresh-install leg (AUG-091 re-verify)

Documented path, timed end to end:

| step | result | time |
|---|---|---|
| `npm config set prefix ~/.npm-global` + `npm i -g pnpm` | ok | ~5s |
| clone duckbrain + auger (from bundles), `pnpm install && pnpm build` | ok | 81s total |
| `mkdir -p ~/dd-data` + daemon on `--port=3117 --auth=apikey` | ok, PID verified, `ss -ltn` confirms 3117 | ~4s |
| `node bin/duckbrain.js token` → fallback path | ok | ~2s |
| `auger -n df12 init` (no env var, token via file fallback) | **ok: `declared: 14/14 tables`** | <1s |
| `start --seed-file` | ok, "seed stored (215 chars) + embedded", 155ms | |
| `ask` (no OpenRouter key on the box) | fail-closed, honest: "JEV unavailable … no next-question is invented without the model" | |

**AUG-091 is genuinely closed**: the P0 blocker is gone. The README now documents
`DUCKBRAIN_API_KEY`/token-file auth (README lines 121-125) and one of the two
documented fallback paths worked with zero extra steps. One fresh-box friction
REMAINS and is filed as AUG-092 (below).

**Multi-tenant port trap re-proved (cross-evidence for AUG-060, not a new row):**
the first daemon start on the README's default `:3000` reported `EADDRINUSE` — a
sibling agent's daemon (uptime 92367s ≈ 25h, far older than my 5-minute-old agent)
holds the shared host's port. Every response from `:3000` on that box belongs to a
stranger's substrate. The AUG-060 rule ("never assume 127.0.0.1:3000 is yours") is
not theoretical; it fired within 4 seconds on a brand-new agent.

## The record verb — 6/6 probes correct

| probe | result |
|---|---|
| `record break --decision D-001 …` | `BR-000001 recorded`, rc=0, fields echoed |
| `record break --decision D-999 …` (nonexistent) | refused rc=1: "decision 'D-999' does not exist … **nothing was written**" — the refusal happens before id minting, exactly as VERBS.md claims |
| `record assumption --text … --falsifier … --monitoring …` | `A-000001 recorded` |
| `record assumption` without `--falsifier` | parser-level refusal rc=2 naming the missing flag (an unfalsifiable assumption is a wish) |
| `record unknown --text … --owner … --trigger …` | `U-000001 recorded` |
| `record option <label> --costs … --breaks …` | `D-001-O1 recorded`, and `export`/`dump` now render `costs=`/`breaks=` populated — AUG-058's "always empty" state is fixed and verified in use |

AUG-019 is closed in practice: the registers are writable, refusals are
pre-mint and honest, and the rows surface in `status`/`export` immediately.

## The export verb

- 100 lines, 245ms, rc=0, all 14 sections in documented order; empty tables render
  `### (none stored)`.
- **Determinism verified**: two consecutive exports `cmp` byte-identical (the
  contract says no timestamp is included — confirmed).
- Renders the registers verbatim, including the `per-option-v1` why-not JSON —
  a reviewer can read the whole spec without auger.

## The bundle verb (verb form, never exercised before)

Real contract (`--help`, and note my first guesses were wrong — the subcommands are
`add` and `member`, not create/add-member/show):

- `bundle add "df12 storage stack"` → `B-000001 … (status proposed)` — no `--id`
  flag exists (ids are minted; the `B-NNN` in VERBS.md is the minted form).
- `bundle member B-000001 df12 owner` → `BM-000001`, with a LOUD warning when
  `AUGER_ALLOW_SCRATCH_MEMBERS=1` is used ("skips scheduler project validation …
  this member's rows must live in namespace 'df12'"). The escape hatch refuses to
  be silent — good.
- Both rows render in `export` under `## Bundles` / `## Bundle members`.

## The recall/check embedding trap (new finding AUG-092-class, see rows)

The substrate was booted with NO embedding provider (health: `degraded`, all three
providers unreachable/missing-key). Yet:

- `start` printed "seed stored + **embedded**", `answer` printed "**embedded**".
- `recall`/`check` returned scores of **1.319** and **0.477** — cosine similarity
  cannot exceed 1.0. These are the substrate's LEXICAL/BM25-tier scores (the
  searcher is a hybrid: BM25 + semantic, RRF-fused — `src/search/fusion.ts`), not
  cosine values. Every prior dogfood run recorded cosine-scale scores (~0.99x) on
  a healthy substrate.

So the same `recall` verb returns two different score semantics depending on
substrate health, `auger` says "embedded" regardless, and nothing tells the user
which retrieval tier answered. A user comparing today's 1.319 against yesterday's
0.99 has no way to know they are different scales. Filed as AUG-092 (P1).

## Fresh-box friction: the README quick start's `answer` fails gridless (AUG-093)

The README Quick start example runs `answer --domain 4.05`. On the fresh box that
refuses rc=1: the canonical 44-domain grid is not on the machine (it lives in a
Hermes skill tree), the error says so precisely, and `status` honestly refuses to
claim domain coverage ("absence CANNOT be claimed from here"). Dropping `--domain`
works — so the grid is genuinely not a loop prerequisite (as the README claims) —
but the README's own example form cannot run as printed on a gridless box. The fix
is one sentence of docs (or shipping the example gridless). Filed as AUG-093 (P2).

## Measured cost (Step 2b)

All fast enough that no perf row is warranted:

- `answer` (write + embed attempt): 0.19s
- `recall`: 0.22s cold / 0.43s warm — the 6.5-22s warm figures from runs 6-7 are
  GONE on this substrate build
- `check`: 0.20s
- `export`: 0.245s (cold, 100 lines)
- `status`: instant
- Full fresh install to first successful `init`: 81s substrate + ~10s auth/boot
  + instant CLI = ~95s on a bare box.

## Verdict

🟡 **PROMISING-BUT-ROUGH** for the new-verb surface; the install path itself now
deserves ✅-grade marks: the AUG-091 fix closed the only P0, the documented
commands run as printed (one docs sentence short, AUG-093), and everything a
fresh user touches is fast and honest. The rough edges are the unlabeled
score-semantics switch (AUG-092) and the gridless-example friction (AUG-093).

- Time-to-first-success on a genuinely fresh box: **~95s** (was: never, run 11).
- Friction count: 2 (score semantics unlabeled; gridless example fails as printed).
- What works: every verb probed (init/start/answer/ask-fail-closed/status/
  record×6/export/bundle add+member), refusal paths precise, export deterministic.

## What I left behind

- Board rows: AUG-092 (P1 recall score semantics), AUG-093 (P2 gridless example),
  cross-evidence note on AUG-060 (port squatter re-proved).
- This doc, a SKILL.md update (15 verbs, substrate pin folded, `record`/`bundle`
  recipes, score-semantics pitfall), a diagnostics.md section, dogfood-log entry.
- Ephemeral agent destroyed and verified gone.
