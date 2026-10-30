# QA-AUGER-15 — clean-machine battery re-run after the bunker-mvp ENOSPC invalidation (2026-10-30)

**Row:** `QA-AUGER-15` (P2) — *clean-machine QA battery is invalidated by bunker-mvp ENOSPC before
ci-pass/upgrade can run.*
**Verdict:** 🟢 **INVALIDATION CLEARED** — the battery ran to completion on a server with real
headroom, **zero ENOSPC** in any agent-side log, `ci-pass` and `upgrade` both produced real command
output, and `chaos-resource` recorded the memory cap **applied inside the child and subshell**. The
durable host gate the row asked for (refuse a low-disk server before admitting a battery) is now in
the harness, RED-proven. Two `FAIL` cells remain and **neither is caused by the invalidation**: one
is a pre-existing repo red reproduced on the control host at the same HEAD (separate finding, §5),
the other is a documented harness grading gap (index miss should be `SKIP`, §6).

**Tested HEAD:** `255fc9bf04945b0f618d8a597e91def5e093e165` (main; the row's failed run tested the
older `2fcff58`, so this run is at a *later* HEAD as the acceptance allows).
**Server:** `bunker-mvp` · **Agent:** `69129f1f` (destroyed by collect) · **Battery window:**
2026-10-30T03:28:57Z → 03:32:34Z (`CELLS-DONE`), collected 03:33Z.

---

## 1. What invalidated the previous run (the row's premise, re-confirmed)

The 2026-10-29 run (`auger-qa-2026-10-29-16-54-28`, agent `58b58514`, HEAD `2fcff58`) was launched on
a host that was **95% full — 142.4 GB used of 149.9 GB (≈7.5 GB free)** with 59 containers resident.
Its agent-side `qa-run.log` first died creating the act workspace, and every downstream cell inherited
that failure:

> "The agent-side qa-run.log records mkdir failure for qa-act-wf because there was no space left on
> device, a failed ci.yml link, and another ENOSPC while creating the errorpath fixture. The evidence
> then reports ci-pass FAIL native rc=120 with no command output, upgrade FAIL rc=120 with no pip
> output, chaos-disconnect UNVERIFIED because ci-pass failed first, and chaos-resource UNVERIFIED
> because its 3 GiB cap was not applied and the suite ran uncapped."
> — board row `QA-AUGER-15.reasoning` (filed by the QA foreman from the live agent log)

`rc=120` with **no output** is the tell: bash cannot allocate a redirect / command-substitution temp
file either, so a cell reports a hard failure with an empty log — indistinguishable from a product
defect at the headline level.

**Evidence limit, stated rather than papered over:** the literal ENOSPC lines are quoted above from
the row's own filing, but the agent home archive that held them was **pruned** on the server
(`/var/backups/bunker/` retains 22 archives; none matches `58b58514`). The first lines therefore
survive **in the QA record only** (the row), not as a re-openable log artifact. The prior run's
evidence JSONL is intact at `/tmp/bunker-qa-evidence-20261029T172308Z-3627547.jsonl`.

## 2. Capacity preflight before admitting the battery (AC 1)

The harness gates a full **agent pool** before spawning (QA-OFF-BY-ONE-9) but had **no gate on the
host filesystem** — a full server was admitted and then graded as a failing product. Verified state at
launch, and the reading carried into the run transcript:

| when | reading |
|---|---|
| 2026-10-30T03:28:24Z (launch) | `/dev/sda1` total **160,938,012,672 B**, used **89,634,279,424 B**, **avail 64,699,453,440 B (59%)** |
| 2026-10-30T03:32Z (agent, mid-battery) | `150G` size, **58G avail**, 60% used — the agent's own root, no ENOSPC |
| 2026-10-30T03:40Z (post-run) | used **92,145,401,856 B**, **avail 62,188,331,008 B (60%)** |
| pool | `bunker status --server bunker-mvp` → **Agents: 1/50** |

64.7 GB free is ~8.6× the space the failed run had, and ~6× the new floor. **The server was admitted
because it demonstrably had headroom, not because the previous failure was "probably fixed".**

## 3. The run (AC 2, AC 4)

Launch/collect two-phase, background, unique evidence path (never the synchronous `run` form, which
dies at the tool window and leaves a 0-byte evidence file):

```bash
export BUNKER_QA_SERVER=bunker-mvp BUNKER_QA_SSH_PORT=22
export CODING_HERMES_PROJECT=qa-auger CODING_HERMES_QA_TARGET=auger
export BUNKER_QA_EVIDENCE=/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl
bash ~/.hermes/scripts/bunker-qa.sh launch ~/auger
# → QA-HEAD=255fc9bf04945b0f618d8a597e91def5e093e165 (clone dirty — pull skipped, testing recorded sha)
# → agent=69129f1f @ bunker-mvp (tailscale)
# → CELL fresh-install: sync repo → ~/qa-auger
# → LAUNCHED agent=69129f1f evidence=/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl
# poll ~/qa-run.finished (file marker, never pgrep: the poll's own argv self-matches)
# harvest ~/qa-evidence.jsonl + qa-logs-* BEFORE destroy, then:
bash ~/.hermes/scripts/bunker-qa.sh collect --evidence /tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl
# → COLLECTED agent=69129f1f rows=20 evidence=/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl
```

Artifacts (all resolvable on this host):

| artifact | path |
|---|---|
| merged evidence (23 rows incl. collect) | `/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl` |
| raw agent evidence, pre-destroy harvest | `/tmp/qa15-ev/qa-evidence.jsonl` (20 rows) |
| per-cell logs, pre-destroy harvest | `/tmp/qa15-ev/qa-logs-1879714/` (ci.log, native.log, res.log, up1.log, up2.log, inst.log, venv.log, ep.log) |
| agent run log | `/tmp/qa15-ev/qa-run.log` |

**ENOSPC scan (the invalidation test):** `grep -ric "No space left on device\|ENOSPC"` over the
harvested evidence **and every per-cell log** → **0 matches**. `qa-act-wf` was created, `HARNESS_NODE=ok`,
`HARNESS-COMPOSE=ok`, the whole battery reached `CELLS-DONE`.

## 4. Per-cell verdicts, graded from raw logs (not the headline)

| cell | verdict | evidence |
|---|---|---|
| `fresh-install` | **OK** | `inst.log`: "Successfully installed auger-0.3 pyyaml-6.0.3" |
| toolchain-bootstrap ×8 | **OK** | go 1.27.3, `~/tools/venv` + pytest, gcc/g++, make, compose, buildx, node v22.15.0 |
| `ci-pass` | **FAIL** | **real output**: `native.log` `1 failed, 163 passed, 210 skipped in 29.75s`; act leg rc=1 with full step output. Failure is one test — see §5. |
| `upgrade` | **FAIL** | **real output**: `up1.log` `ERROR: Could not find a version that satisfies the requirement auger==0.2.0 (from versions: none)`; `up2.log` in-tree arm `Successfully installed auger-0.3`. Grading gap — see §6. |
| `chaos-resource` | **FAIL** | **cap applied**: `cap=3145728KB child_saw=3145728 subshell_saw=3145728 parent_saw=unlimited`; `res.log` = the same single test failure. Per the echo rule this is **one** finding with `ci-pass`, not a second one. |
| `chaos-disconnect` | UNVERIFIED | suite failed first (`nat_rc=1`), so the disconnect verdict is not derivable; probe rc=0 |
| `chaos-errorpath` | **OK** | "clean error on missing config (rc=1)" |
| `docker-deploy`, `chaos-shutdown` | N/A | no compose file |
| `ui-probe` | N/A | no web UI |
| `chaos-corruption` | N/A | no db/state files |
| `collect` | **OK** | 20 rows pulled, temp agent destroyed (`69129f1f` absent from `bunker list` afterwards) |

Acceptance wording satisfied literally: **no ENOSPC anywhere**, **ci-pass and upgrade produced real
command output** (pinned above, byte-for-byte), **chaos-resource recorded the cap applied in child and
subshell**. `parent_saw=unlimited` is by construction — the cap is applied *inside* the capped
subshell the suite runs in; the battery's own parent shell is never capped.

## 5. Remaining `ci-pass` / `chaos-resource` red is a pre-existing repo failure, not this row

One test, reproduced on the **control host at the same tested HEAD**, in a clean worktree:

```
tests/test_usage_skill.py::test_usage_skill_describes_failed_not_reached_advances_as_drift
    assert "advance write" in lowered
E   AssertionError
E   1 failed, 3 passed in 0.02s      # ~/auger/venv/bin/python -m pytest tests/test_usage_skill.py
```

`skills/auger-usage/SKILL.md:268-271` documents the NOT-REACHED suffix as a *failed seed-row advance*
but never uses the literal phrase `advance write` that the test requires. So this is **repo-state, not
clean-machine, not ENOSPC**: an assertion/doc drift at `255fc9b`.

It is **not** fixed here — this row's authorised scope is QA evidence, and product-code/skill edits
belong to a product row. No board row exists for it yet (`grep -c
test_usage_skill_describes_failed_not_reached_advances_as_drift` over
auger `.coding-hermes/board/tasks.jsonl` → **0**), and board writes were out of scope for this
dispatch, so it is filed here as a finding for the foreman to mint.

## 6. The `upgrade` cell's index miss is a documented `SKIP`, not a product failure

`docs/RELEASE.md` § *How the QA battery must grade the upgrade cell* (added by QA-AUGER-11-R3) already
states the contract the battery did not honour:

> "An index probe for `auger==0.2.0` … is EXPECTED to fail with `No matching distribution found for
> auger==0.2.0` (from versions: none) …; an index miss is an honest `SKIP` with that reason, not a
> product failure. The offline script is the PASS/FAIL upgrade verdict."

The documented verdict, run at the tested HEAD, is green:

```
bash scripts/upgrade-verify.sh
  installed: auger==0.2.0 -> auger v0.2
  upgraded:  auger v0.2 -> auger v0.3; auger --help rc=0
  fresh install: auger v0.2
UPGRADE VERIFY: PASS
```

So the product's upgrade path is **verified**; the `FAIL` cell is the harness's PyPI arm still grading
an expected index miss as red (a harness-class gap, distinct from this row's ENOSPC scope).

## 7. The durable gate (AC 3) — refused before the spawn, no product cells

Committed to the harness, not to this repo (it is an external HOME-meta-repo artifact; a product-repo
edit here would be a phantom fix):

**Commit `3ef1290202`** — `fix(qa): refuse a low-disk bunker server before the battery spawns.
Addresses QA-AUGER-15.` (`~/.hermes/scripts/bunker-qa.sh` + `~/.hermes/scripts/test_bunker_qa_disk_preflight.sh`)

`preflights()` — the shared block both `run()` and `launch()` call — now grades the server's root
filesystem **after** the ssh reachability gate and **before** the spawn:

| reading | behaviour |
|---|---|
| unreadable / absent `df` | `UNKNOWN` → stderr WARN, proceed (a probe hiccup must not wedge the lane — same policy as the capacity probe) |
| free < `BUNKER_QA_MIN_FREE_GB` (default **10**, absolute GB not a percentage) | **one** harness-class `{"cell":"run_battery","status":"FAIL"}` row naming reading, floor and recovery → `exit 2`, **no spawn, no product cell** |
| free ≥ floor | log line records the headroom; battery proceeds |

Proofs (all re-runnable):

- **Pure arms** — `parse_disk_free_gb` (df text → GB + pct), `disk_gate_verdict` (GB + floor →
  `OK`/`LOW`/`UNKNOWN`), exercised through the `__parse-disk-free` / `__disk-gate` hooks: measured
  failure shape 6 GB/96% → `LOW`; healthy 60 GB/59% → `OK`; floor boundary exact (10 GB at floor 10 →
  `OK`, 9 GB → `LOW`); both floor-override directions move the verdict; garbage / empty / header-only
  / human-unit output → `UNKNOWN`.
- **Regression test** `test_bunker_qa_disk_preflight.sh` → **21/21 pass**; **RED-proven** against the
  pre-fix harness (`git show HEAD~1:.hermes/scripts/bunker-qa.sh`) → **0 passed, 21 failed**.
- **No regressions** in the existing harness suites against this file: classifier **58/58**,
  pytest-detect **39/39**, stdin-ship **23/23**, and `__fence-check` →
  `body_lines=1818 intended_substitutions=35 violations=0`.
- **Live refusal (end-to-end, real server, no spawn)** — `launch ~/auger` on `bunker-mvp` with an
  impossible floor (`BUNKER_QA_MIN_FREE_GB=100000`, evidence `/tmp/qa15-refuse-1793331535.jsonl`)
  produced exactly one row and **no agent**:

  ```json
  {"cell":"run_battery","status":"FAIL","detail":"server bunker-mvp is LOW ON DISK (56 GB free, 61 used on /; floor 100000 GB) — refusing to spawn (disk-capacity preflight, QA-AUGER-15). … this row is HARNESS-class and no product cell was produced. …"}
  ```

  the launch's `.meta` carries `agent=` **empty** (nothing was spawned) and no product cell exists —
  exactly the AC-3 shape ("refused, not a product FAIL").
- Ordering is asserted by the test itself (reachability → disk → `agent=$(spawn_agent`) so a future
  refactor cannot move the gate after the spawn.

## 8. Method notes worth keeping (harness behaviour observed this run)

- **`launch` refuses a git-worktree root.** Its guard is `[ ! -d "$repo/.git" ]`; in a linked worktree
  `.git` is a **file**, so the launch dies `is not a git repo (git resolves toplevel: <itself>)` even
  though `git rev-parse --show-toplevel` returns the directory itself. Worked around by launching the
  primary clone at the same HEAD (recorded as `clone dirty — pull skipped, testing recorded sha` — its
  only dirty paths were board/state bookkeeping, no source). Worth a separate harness row.
- **Per-server battery lock serialises lanes.** A sibling lane (code-factory) held
  `/tmp/bunker-qa-bunker-mvp.battery.lock` during this tick; a queued launch waits in the controller
  instead of double-spawning — that is the mechanism working, not a hang.
- **Nested-suite time:** this battery is ~3.6 min end-to-end for auger (small pytest suite) — the
  earlier 7–15 min estimate is per-cell-class, and the 420 s tool window is still too small to hold a
  launch+collect in one foreground call.

## 9. Residuals for the foreman

1. **Mint a product row for §5** — `tests/test_usage_skill.py` asserts a phrase
   (`advance write`) that `skills/auger-usage/SKILL.md` does not use; red on the control host at
   `255fc9b`. Not filed here (board writes out of scope).
2. **Harness row for §6** — the `upgrade` cell must grade an index miss as `SKIP` per
   `docs/RELEASE.md`, not `FAIL`.
3. **Harness row for §8** — `launch` should accept a linked worktree root (`.git` file).
4. `QA-AUGER-15`'s own acceptance is met by §2–§7; the two FAIL cells it leaves behind are
   attributable to 1–2 above, **not** to a full server.

ch:trace row=QA-AUGER-15 spec=docs/RELEASE.md#Verifying-the-v0.2.0-upgrade-path wave=.coding-hermes/waves/auger-foreman-2026-10-29-22-53-26.json#QA-AUGER-15 test=none:external-battery doc=docs/dogfood/2026-10-30-qa-auger-15-clean-machine-enospc.md prompt=/tmp/auger-foreman-2026-10-29-22-53-26-QA-AUGER-15-brief.txt evidence=/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl witness=run:bunker-mvp-agent-69129f1f verdict=none:foreman-will-judge commit=HARNESS-3ef1290202 memory=none:foreman-writes
