# Dogfood run 10 — CLI error surface + multi-project isolation (2026-09-26)

Lane: auger-dogfood (tick auger-dogfood-2026-09-26-01-16-46). Code under test: HEAD
`3d4b9cb`, exercised from a scratch copy (/tmp/df10) against a live dedicated
namespace, so the run is unaffected by sibling in-flight work in the checkout.

## Promise under test (this run's angle)

Runs 1-9 swept the drill loop, graph/bundles, HTTP API, registers seam, scale,
failure semantics and the mind-change surface. Never swept: what the CLI does
when the *user* makes mistakes — bad args, contract violations, missing
namespaces — and whether multiple projects in one namespace stay isolated.
Promise probed: "the CLI's errors are honest and consistent, and `-p` scopes
what a user sees to their project."

## What was run (real use, not the test suite)

1. Help surface: all 13 verbs' `--help` (main help + 175 lines of verb help) —
   every verb documents its flags; `start`'s documented flags (`--name --id
   --seed --seed-file`) match reality (an initial guess of `--project` correctly
   refused).
2. A fresh namespace drill end to end: `init` (307 ms, 14/14 declared) →
   `start` → 2× `ask` (JEV) → 2 real `answer`s with alternatives + why-not →
   `check` → `feedback` (proposed and asked Q-000003 off the thin D-006) →
   `verdict --good` on the rendered config → `status`/`dump`.
3. Multi-project: a second project started in the same namespace; isolation
   probed via `status`, `dump`, `check`, `recall` both with and without `-p`.
4. Error leg: AUG-055 regression probe (chosen∉options), missing --chosen,
   confidence 1.5, toggle/verdict with wrong flags (both correctly refused per
   their documented forms — not findings), duplicate `--id`, missing namespace
   on status/dump/check, invalid verb.
5. Teardown: namespace deleted through the substrate API (200, path removed).

## Verdict: PROMISING-BUT-ROUGH (error UX + project scoping)

The drill loop itself is the best it has been: all previously-fixed behaviors
re-verified holding in real use (AUG-055 refuses rc=1 nothing-written; AUG-062
renders `conf n/a`; AUG-056 gate holds; AUG-069 duplicate-id refusal is
impeccably worded; `check` fails closed on a dead/missing namespace per
AUG-075). Warm timings: dump 477 ms, status 915 ms, recall 1.0 s, init 307 ms.
But the multi-project story has a P1 hole (AUG-082) and the error surface is
inconsistent between verbs (AUG-085) with a real validation gap (AUG-083).

## Findings (board rows)

| id | pri | finding |
|---|---|---|
| AUG-082 | P1 | recall/status/dump are not project-scoped: `-p` does not filter recall — a project-B query returned project A's decision at 0.992 as the TOP hit (and vice versa). Cross-project contamination of the evidence gate. |
| AUG-083 | P2 | `answer --confidence 1.5` accepted and stored verbatim (`conf 1.5` in dump); no 0..1 clamp or refusal. Poisons status means. |
| AUG-084 | P2 | Starting a second project silently retargets every no-flag verb to it mid-drill; duplicate `--name` accepted without warning. |
| AUG-085 | P2 | status/dump on a missing namespace print a raw Python dict repr; `check` on the same input prints a clean fail-closed message (AUG-075 pattern). Inconsistent error UX. |
| AUG-086 | P2 | check's already-answered gate straddles its own threshold: identical evidence judged ALREADY ANSWERED (noul 0.55 = threshold 0.55) then NOT YET ANSWERED (0.53) 3 s apart. |
| AUG-087 | P2 | SKIPPED-install-bunker (explicit): las-bunker-03 unreachable at tick time (ssh connect timeout). Install path re-proven by runs 2/3/5 (last 2026-09-24, agent d1a530b6, smoke=ok). This run's surface is install-independent. |

## Measurements

| operation | result |
|---|---|
| init (fresh ns, 14 tables) | 307 ms |
| ask (JEV round trip) | ~5-6 s (model call, consistent with runs 5-9) |
| answer | <1 s each, honest output, embedding written |
| check (warm) | 3.1 s |
| recall (warm) | 1.0 s (improved vs run 8's 22 s at scale — small store here) |
| dump (warm) | 477 ms |
| status (warm) | 915 ms |
| fresh-install leg | SKIPPED (AUG-087) — bunker host down |

## Why scratch-copy + scratch namespace

The auger-foreman's AUG-078 worker was mid-flight in this workdir with
uncommitted auger.py/tests changes. Dogfooding HEAD (3d4b9cb) from /tmp/df10
keeps the run honest (the sibling's diff does not silently change behavior
under test) and the namespace was dedicated + torn down, so the shared
:3000 substrate holds no run-10 state.

## What this run leaves behind

- This report and board rows AUG-082..087 (+ events) in `.coding-hermes/board/`.
- A dogfood-log.md entry (run 10).
- No code changes: dogfood never fixes; the foreman works the rows.
