# Run 15 — propagate/feedback graph family — 2026-10-12

Promise under test: "a user can grow a drill through the graph walks shipped 2026-10-11 —
`feedback` turns thin decisions into gated questions, `propagate` moots/reopens/cascades
after an invalidation — and the record stays trustworthy while it does."

Namespace: `df15-prop-20261012` (throwaway; lived on the shared DuckBrain :3000, safe to
delete via the DuckBrain API). HEAD 7928e6a. Scrub dir /tmp/df15-run/.

## The loop, as a user drove it

1. init 5.8s (incl. embedding), start ok.
2. answer D-001 (0.72) and D-002 (0.45, thin).
3. `feedback --budget 3` with `AUGER_PROPOSER_MODEL=deepseek/deepseek-chat`: 23s, proposed
   1 question (Q-000001, health-check topic) rooted at D-002, `opens` edge E-000001 with
   the proposer model recorded in the note.
4. `check` on Q-000001: NOT YET ANSWERED (noul 0.04) — honest.
5. answer D-003 --question-id Q-000001 (0.9): closed the question, wrote `closes` edge E-000002.
6. Second `feedback`: proposed Q-000002 from the same thin D-002. This is the near-duplicate
   shape AUG-094 warned about — but note the gate DID stamp fresh jev scores (0.03) and the
   question is domain-distinct, so the P1 row's "paraphrase" premise is only partially
   re-proved here; a same-topic pair was not reproduced.
7. `answer D-004 --invalidates D-003 --invalidates-why ...`: break edge E-000004 + the
   printed trigger line naming `propagate`.
8. `propagate`: moot 1 (Q-000001 mooted, reason "D-003 was invalidated by E-000004"),
   nothing else touched. Exactly the documented contract.

## What held up

- Edge provenance is exact: E-000001 note carries deepseek/deepseek-chat (the override),
  E-000003 carries deepseek/deepseek-v3.2 (the default when the env is cleared). Read
  both back from `export` — they match the runs that wrote them.
- propagate --no-gate vs --recheck: 0s and 1s; the gate stamps jev_already_answered +
  jev_checked_at on open questions (0.04 / 0.03 read back).
- Fail-closed chain (after restarting the scratch substrate on :3119):
  - proposer unreachable → WARNING line, "1 decision(s) left unproposed",
    `fail-closed: no question is invented without the model`, rc=1, zero rows written.
  - duplicate start refused rc=1; --invalidates of a nonexistent decision refused rc=1
    with the SPEC-002 2.2 explanation; toggle on a bogus option refused rc=1.
  - substrate dead → `recall` says "a failed read is NOT an empty store" (fail-open
    fixed earlier, still closed). NOTE: `status` printed the raw transport dict and
    exited 0 on the FIRST dead-substrate attempt (rc captured after a pipe), but with
    correct plumbing it is rc=1 — see the exit-code row below.
- AUG-095's already-drilled stop works: after Q-000002 went open on D-002, the next
  two `feedback` runs refused to re-drill ("already drilled: Q-000002 is open and
  awaiting an answer", proposed: 0).

## Findings (board rows)

- AUG-096 (P1): an invalidation leaves the what-if surface blind. After D-004
  invalidated D-003, current-mode `dump` still renders D-003 with its option
  checked `[x]` and `breaks=—` on every option line; `export` still shows
  D-003 status `decided`, D-003-O1 active:true. Docs say the target's rows are
  deliberately never rewritten and the trigger "sits on the record" — but then the
  read surfaces that render a configuration (dump, export, status counts) are the
  places the break must become visible, and none of them show it. Same class as the
  fixed AUG-077 (contradiction detector hypothetical-only), now on the break path.
  Fix direction: render `breaks=<edge id>` on options targeted by a breaks edge and
  a `INVALIDATED by D-004 (E-000004)` marker on the decision; count a broken decision
  out of the active configuration or flag it in status.
- AUG-097 (P2): two exit-code findings, both proven:
  - `status` on a dead substrate exited 0 while printing the raw transport error
    dict (`select project failed (0): transport ... Connection refused`) — a
    scripting consumer reads success. recall/check/feedback fail closed with a
    named message; status does not. Same verb later exited 1 (see below) — the
    contract is inconsistent between failure paths, which is its own defect.
  - Bunker leg: the walkthrough's `start` refusal ("project name already exists")
    printed rc=0 in run's earlier attempt because of a shell plumbing issue, but
    the real rc IS 1 when captured properly — the leg script used
    `cmd | tail -2; echo rc=$?`, the PIPESTATUS trap. Docs impact: no doc warns
    that refused verbs exit 1; that part is fine. The row is the status-on-dead-
    substrate exit 0 only.
- PERF (no row — nothing a user feels): status warm 0.63s, dump 0.26s, export
  0.72s, propagate 0-1s, feedback 23s cold (dominated by the embedding round-trip
  + proposer call, both remote). Nothing slow enough to file.

## Install leg (bunker-mvp, agent 19eba3ec, destroyed after)

- Bare Debian: node/npm MISSING, git present. The README's substrate block starts at
  `npm i -g pnpm` — a silent toolchain assumption. (The box IS reachable and bunkerd
  active; the leg ran, no SKIPPED.)
- Documented path followed: user-space node tarball (docs never mention this) →
  pnpm 12.4.2 → `pnpm install` refused twice on engine-strict (Node 22.11.0 then
  24.11.0 both too old for transitive engines: @babel/helper-validator-option wants
  ^22.18||>=24.11, @asamuzakjp/dom-selector wants ^22.22.2||^24.15||>=26) →
  `pnpm install --force` succeeded (11s) → `pnpm build` ok (4s) → substrate boots
  degraded (no embedding provider on a fresh box, expected) → `auger init` rc=0
  with DUCKBRAIN_API_KEY set (AUG-091 fix holds; the token requirement is now in
  README's Authentication section and the error is actionable) → `start` refused a
  duplicate name honestly ("nothing was written") and `status` answered clean.
- Total wall: ~14 min of leg time, ~11 min of it me installing node twice and
  fighting engine-strict. The README quickstart is NOT followable on a bare Debian
  box without node already present, and even with node the pin block silently
  requires --force unless the user picks exactly the right Node version. Neither
  is a P1 (a JS-project README assuming node is fair), but the engine-strict wall
  is the kind of thing a fresh user cannot diagnose from the error alone — worth a
  docs line naming the known-good Node version.
- First walkthrough run also hit the AUG-091 token error verbatim — because the
  leg script predated the Authentication section and didn't set the env. rc=1,
  honest, message names the fix. That is the fixed contract holding.

## Verdict

🟡 PROMISING-BUT-ROUGH. The graph family works: feedback→question→answer→closes-edge,
invalidation→break-edge→trigger line→propagate moot, provenance exact, fail-closed
everywhere I pushed. The rough edge is the same class this project keeps finding in
itself: the write path records the truth (break edge) and the read path doesn't
surface it (dump/export/status). One P1 row, one P2 row, no perf rows.
