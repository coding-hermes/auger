# Dogfood integration report — the `spec` verb family and the spec-to-build loop

**Run:** tick `auger-dogfood-2026-11-23-14-28-25` (19th dogfood run) · 2026-11-23
**Tree measured:** `main` @ `a8bf275` (origin/main, 2026-11-23 00:16 +05)
**Consumer setup:** scratch project at `~/.hermes/cache/scratch/df19-auger/myproj`
(git repo, `tracker.py` + `tests/test_tracker.py`, authored corpus `specs/scratch-tracker.xml`)
— auger was driven as a pure CLI consumer, never modified.

## Promise tested this fire

*"A user can author an XML spec corpus for their own project and drive the
spec-to-build loop — task packet → plan draft → approval → board export →
judge packet — using only the documented commands and the XSD."*

Prior runs (16th–18th) proved the DuckBrain decision loop and the coverage
map; none had driven the `spec` surface beyond `validate/list`, and the
spec-to-build loop shipped after the 18th run (AUG-118 → AUG-152/165/167/200-202).

## What worked, end to end (all rc=0, timings below)

| Step | Command | Result |
|---|---|---|
| Author corpus | hand-written `scratch-tracker.xml` (`<task plan="TT-PLAN" plan-version="1.0.0" in-scope=... out-of-scope=...>`) | loader accepted first try after one fix (obligation path must be tracked in `--repo-root`) |
| Validate | `auger.py spec validate specs` | `1 document(s), 5 block(s), 5 reference(s) ok` — 0.40 s |
| Task packet | `spec task-packet specs --task TT-TASK-1` | packet with in-scope TT-REQ-1, acceptance TT-AC-1, out-of-scope TT-REQ-9 **with reason**, digest = sha256 of canonical packet — 0.14 s |
| Plan draft | `spec plan-draft specs --plan TT-PLAN` | plan digest `3c4b3a6e…`, counts in_scope 1 / out_of_scope 1 / tasks 1 — 0.14 s |
| Approval guard | `spec plan-approve … --digest 000…0` | rc=2 `spec error (stale-plan-digest): … approve the plan you are actually looking at` |
| Approve | `spec plan-approve … --digest 3c4b… --approver "Dogfood Agent"` | rc=0, append-only ledger `specs/.auger/plan-approvals.jsonl` written and echoed back — 0.14 s |
| Board export | `spec plan-export specs --plan TT-PLAN --board <scratch board>` | created board row `TT-TASK-1` via `boardctl` with acceptance criteria verbatim, capability tags `in-scope:TT-REQ-1@1.0.0` etc., and the `ch:trace` marker embedded in `reasoning` — 0.15 s |
| Judge packet | `spec judge-packet specs --plan TT-PLAN --row TT-TASK-1 --board <scratch> --trace "ch:trace row=…"` | full packet: `board_id`, `id_mapping`, resolved `ch_trace` (`spec:[TT-REQ-1]`, `test:[TT-VER-1]`), verbatim board acceptance criteria, digests for every block + packet + plan — 0.20 s |
| Reconcile | `spec plan-reconcile specs --plan TT-PLAN --board <scratch>` | lifecycle census per row (`pending`, `commit: none:not-recorded` …) — rc=0 |
| Context query | `spec context specs --task TT-TASK-1` | selected 4 blocks, dependency 1, excluded block with reason, input/output byte counts — rc=0 |
| Brief enrichment | `scripts/brief_spec_context.py --row TT-TASK-1` | emits `## SPEC CONTEXT — row TT-TASK-1 (4 block(s) selected…)` with verbatim block texts; unknown row → zero bytes + one honest stderr line |
| Worker handoff refusal | `spec task-export` without digest | rc=2 `spec error (unapproved-plan-digest)` — fail-closed, digest echoed |

Negative arms all behaved: `--as-of no-such-ref` → rc=2 `as-of-unknown-ref`;
`--as-of v0.3.0` → rc=2 `as-of-corpus-missing` (the `specs/` corpus did not
exist at that tag — correct); `spec task-packet --task TT-NONE` → rc=2
`unknown-task` (and helpfully: "a task packet is built from a `<task>` block,
never from a board row's prose"); `spec align --protocol-version 99` → rc=2
`unsupported-protocol-version`. `--as-of HEAD` is byte-identical to plain
selection (`cmp` clean); `spec render` twice is byte-identical
(`RENDER-BYTE-IDENTICAL`); `spec align` on a real staged diff (commit
`5157273`, the AUG-152 fix) returns `status=pass, scope=scoped` in 0.15 s,
`--semantic` in 0.12 s.

## Verdict on this surface

The spec-to-build loop **works end to end at the CLI level** and its fail-closed
behaviour is consistently typed (`spec error (<code>)`, exit 2, offending ids
named). The digest chain (packet → plan → approval → export → judge packet)
means nobody can approve a plan they are not looking at, and the judge packet
binds board acceptance criteria verbatim. This is the strongest new surface
since v0.3.

**One friction dominates:** the loop is invisible in the docs. See board rows
AUG-206 (docs) and AUG-207 (usage skill / README). I had to read
`tests/test_task_packet.py:84-111` to learn the `<task>` attributes
(`plan=`, `plan-version=`, `in-scope=`, `acceptance=`, `out-of-scope=`,
`<exclusion ref= reason=>`) — no shipped document or example corpus shows a
`<task>` block.

## Performance (all single cold CLI process, shared N100-class host, warm≈cold: interpreter startup ~0.1 s dominates; the spread verb-to-verb is <100 ms)

| Operation | Wall |
|---|---|
| `spec validate` (12-block corpus) | 0.44 s cold / 0.40 s warm |
| `spec check` | 0.36 s |
| `spec list` plain | 0.14 s |
| `spec list --as-of <old commit>` | 0.23 s first (git archive extract) / 0.20 s repeat |
| `spec align` + real diff | 0.15 s |
| `spec align --semantic` | 0.12 s |
| `spec task-packet` | 0.14 s |
| `spec plan-draft` | 0.14 s |
| `spec plan-approve` (ledger write) | 0.14 s |
| `spec plan-export` (boardctl write + read-back) | 0.15 s |
| `spec judge-packet` (digests + board reads) | 0.20 s |

**Non-finding:** nothing here is slow enough for a user to notice; the
`--as-of` git-archive overhead is ~60–90 ms. No PERF row filed.

## Errors hit on the way (consumer's log)

1. First `spec validate` of a corpus outside a git repo → typed refusal
   `unresolvable-repo-root` with the exact remedy in the message. Correct.
2. Second refusal `stale-test-ref`: my obligation pointed at
   `tests/test_tracker.py` before that file existed/tracked in the consumer
   repo. Fail-closed as designed; a docs sentence in SPEC-CORPUS.md would help.
3. `spec align` accepts **no positional ROOT** (flag-only), while
   `spec align --help` describes `--spec-root`'s default as "the positional
   ROOT". Folded into AUG-206.
4. `plan-approve` without `--at` records an **empty** `at` field in the
   approval ledger ("recorded verbatim" by design, but an empty timestamp
   weakens audit traceability). Folded into AUG-206.

## What this fire did NOT verify

- `spec serve`/API external consumption — already owned by AUG-166/AUG-172.
- `judge-handoff` full dispatch (needs a live dispatch command + gitreins
  judge) — refused by design without `--dispatch-cmd` (`dispatch-unconfigured`);
  the judge-leg integration is AUG-202's pending work.
- The install battery on a fresh bunker agent was **launched** (agent
  `5e0a4289` @ bunker-mvp, TTL 4 h); harvest/verdict recorded in the run log
  entry and evidence dir.
