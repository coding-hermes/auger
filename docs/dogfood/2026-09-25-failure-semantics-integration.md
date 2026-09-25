# Dogfood Run 8 — Failure Semantics (2026-09-25)

**Angle:** the surface none of runs 1–7 touched — what a user experiences when the
substrate dies mid-session. Runs 1–7 proved the loop works while the substrate is
healthy; this run asked the question every real user eventually hits: *what happens
when it isn't?*

**Setup:** dedicated scratch substrate on :3121 (fresh data/namespaces/auth dirs,
`--auth-file` pointing at a throwaway store — the substrate itself refuses to boot
against the production auth store when given an explicit path, DB-GAP-043 guard;
trust-positive). Control :3000 was never touched. Small real drill armed first:
init → start (seed stored, embedded) → ask (6.7s, JEV proposed Q-000001 "What
happens when a component stops working?") → answer (D-001, 235ms, 3 options).

## Finding 1 — `ns_dir` hardcodes `~/duckbrain/namespaces` (P1, AUG-074)

`init` writes its 14 table declaration files to
`auger.py:318 ns_dir()` = `~/duckbrain/namespaces/<ns>/tables/`, a **hardcoded
home-relative path**, while the substrate honors its own `DUCKBRAIN_NAMESPACES_PATH`.
Run a substrate anywhere else and:

- the declaration files land in the production tree on disk (this run polluted
  `~/duckbrain/namespaces/auger-df8-failure/` on the control host before I noticed),
- the API never sees them: `init` prints `declared: 0/14 tables` **and a WARNING
  listing all 14 as "not visible to the API yet" — then exits 0**,
- `start` dies with `insert project failed (404): Table 'project' not found`.

A user following the README's `DUCKBRAIN_DATA_DIR` advice (which the README itself
teaches for fresh installs) plus any non-default namespaces path gets a broken
install with a green-looking init. VERBS.md's init contract says the run "states the
tally itself (`declared: 14/14`) which is how the count is checked" — but a 0/14
tally still exits 0, so the check exists and is ignored by the exit code. Fix
direction: refuse (or at least exit non-zero) when the tally says 0/14, and resolve
the declaration dir from the substrate's configured path (a `GET /api/namespaces`
registry entry or an env passthrough), not from `~`.

Reproduced live: :3121 substrate with `DUCKBRAIN_NAMESPACES_PATH=/tmp/dogfood8-ns`,
default run → 0/14 + rc=0 + 404 on start; symlink bridge → 14/14 and start works.

## Finding 2 — `check` and `recall` fail OPEN on a dead substrate (P1, AUG-075)

Kill the substrate (`kill -9`) and probe every verb. Result table:

| verb | rc | latency | behavior |
|---|---|---|---|
| status | 1 | <1s | `select project failed (0): transport: Connection refused` — honest |
| dump | 1 | <1s | same — honest |
| ask | 1 | <1s | same — honest |
| answer | 1 | <1s | same — honest |
| feedback | 1 | <1s | same — honest |
| verdict --good --ask-jev | 1 | <1s | same — honest |
| **check "Q"** | **0** | <1s | **`no stored evidence matched — treat as a new question`** |
| **recall "Q"** | **0** | <1s | **empty result set, no error** |

`check` returned rc=0 with "treat as a new question" while D-001 — a decision that
closes exactly that question — sat intact in the (paused) store. VERBS.md's check
contract says a drifted/unknown state must never be silently misreported; a dead
substrate produces the most confident false negative in the tool: "we hold nothing"
instead of "we cannot reach what we hold". A user trusts it and re-drills an
answered decision. Fix direction: distinguish *empty answer set* (lookup 200, 0
rows) from *transport failure* (connection error) in `db_select`/`db_retrieve`
callers, and fail closed (rc=1, "substrate unreachable") for retrieval verbs the
same way write verbs already do.

Note the asymmetry: writes fail closed everywhere (verified — see Finding 3),
retrieval-honesty verbs fail open. The fix is two call sites.

## Finding 3 — crash mid-write leaves a phantom decision the retry duplicates (P1, AUG-076)

`answer` is a multi-write pipeline (decision row → option rows → memory remember).
Kill the substrate inside the write window and the failure lands in the LAST stage:

- stdout: `remember /auger/P-20260925103605/D-002 failed (0): transport: Remote end
  closed connection without response`, rc=1 — honest.
- After restart: **decisions 2, options 5** — the decision and option table rows
  COMMITTED, the memory remember did not. `recall` cannot see D-002 (only seed +
  D-001 come back), so the decision is invisible to every retrieval surface forever.
- The user's natural recovery — rerun the same answer — mints **D-003** (AUG-069's
  highest-id mint correctly avoids the frozen id) and now the domain carries TWO
  active decisions recording the same choice, with no supersession, no duplicate
  warning (the AUG-070 dump duplicate-id warning only fires for duplicate *ids*,
  and D-002/D-003 are distinct), and no memory entry for the earlier one.

This is the crash-consistency sibling of run 7's AUG-070 (concurrent writers): the
table writes and the memory write are not one unit, and nothing detects the
aftermath. Fix direction (any one suffices to change the failure mode): record the
decision with `status=provisional` and flip to `decided` only after the remember
succeeds; or a consistency self-check on `status`/`dump` that flags decisions with
no memory entry (the store can be queried by key prefix); or make `answer`'s retry
path detect an identical unembedded decision and repair instead of minting.

Timing note for reproducers: the write window is ~60–150ms inside a ~235ms command;
kill at +210ms from launch to land inside `remember` (evidence script pattern:
`/tmp/dogfood8-midkill.sh`, three kills at +50/+150/+210ms hit select/select/
remember respectively).

## Recovery and restart (the good news)

- Restart on the same data/auth/ns dirs: namespace intact, project row, D-001,
  options, embedding — all present. `status` healthy again immediately.
- Post-restart `check` finds D-001 at distance 1.000 and `recall` ranks it 0.984.
  Data survives kill -9 cleanly (substrate buffered-durability held).
- Fresh `start` this run stored its seed with **no remember-400** — live-verifying
  the AUG-072 fix (e3df3af, "fresh-start seed remember no longer 400s") which had
  been committed but never verified by use; its board row is closed with this run's
  evidence.

## Minor frictions (P3-level, folded into rows)

- `start -p <id>` silently ignores the short flag's intent: the project got
  `P-20260925103605` regardless; `--id` is the real knob. An "unknown/ignored
  flag" hint would save a round-trip. (Noted inside AUG-074's reasoning — same
  "argparse honesty" family as the 0/14 tally.)
- The scratch substrate refused to boot against a missing/production auth store
  when given `--auth-file` — good trust behavior, recorded here as a positive.
- Verdict gate note: post-restart `check` finds D-001 at 1.000 but still prints
  "NOT YET ANSWERED (noul 0.52 < 0.55)" on a question the record closed — same
  family as AUG-056's gate re-proposal; the run-6 row already covers the gate
  behavior, so no new row.

## Verdict

🟡 **PROMISING-BUT-ROUGH (failure semantics).** The happy path (runs 1–7) is real
and the substrate survives hard kills with zero data loss — but the tool's contract
with the user *during failure* is where trust breaks: two retrieval verbs lie
confidently when the store is unreachable, a misconfigured install exits green with
0/14 tables, and a mid-write crash leaves a phantom decision whose repair path
duplicates it. None of these need new subsystems — two call sites, one exit-code
check, one provisional state.

## Evidence

- Scratch substrate: port 3121, dirs under /tmp/dogfood8-* (destroyed after the run)
- Namespace: `auger-df8-failure` (scratch substrate only; control :3000 untouched —
  one init misfire polluted `~/duckbrain/namespaces/auger-df8-failure/` on disk and
  the leftover directory was removed during cleanup)
- Board rows: AUG-074 (ns_dir + silent 0/14), AUG-075 (check/recall fail-open),
  AUG-076 (crash half-write + duplicate repair)
- Kill-timing evidence: three staged kills at +50/+150/+210ms → select / select /
  remember failures; state diff after each restart recorded via `status`
