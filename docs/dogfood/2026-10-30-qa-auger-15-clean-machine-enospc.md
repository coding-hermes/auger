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
| `ci-pass` | **FAIL** | **real output**: `native.log` `1 failed, 163 passed, 210 skipped in 29.75s`; the native red is one test — see §5a. The act leg is a **second, independent** red (`exitcode '1'` at step *run the gate (tests/gate.sh)*) caused by `tests/test_gate_mode.sh`, not by that pytest failure — `tests/gate.sh` never collects `test_usage_skill.py`; reproduced with `act` on the control host, see §5b. |
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

### 5a. Why the repo never noticed: the gate's pytest arm does not collect that file

`tests/gate.sh:118` runs pytest over an **explicit file list**:

```bash
python3 -m pytest tests/test_auger.py tests/test_openapi_parity.py tests/test_api_service.py tests/test_upgrade_path.py
```

`tests/test_usage_skill.py` is not in it, so the repo's own gate — and therefore hosted CI — is green
while the full suite is red. The only surface that collects the file is a **full-suite** run, which is
exactly what the battery's native leg does (`pytest.ini` → `testpaths = tests`). That is why this has
been invisible since 2026-10-27.

Attribution by `git log -S "advance write"`: the literal was **introduced** by `09652df` (*docs:
correct domain usage guidance*, AUG-113) and **removed** by `a19e04f` (*fix: repair null-ring domain
advancement*, AUG-059-R2), which reworded the same paragraph and was pushed to `main` with a
"CI GREEN" record. The test's other assertions still pass; only the pinned phrase drifted.

Reproduced on the control host at the tested HEAD:

```
$ /home/kara/auger/.venv/bin/python -m pytest tests/test_usage_skill.py -q
FAILED tests/test_usage_skill.py::test_usage_skill_describes_failed_not_reached_advances_as_drift
1 failed, 3 passed in 0.02s
$ PATH=<venv>/bin:$PATH AUGER_CI=1 CI=1 bash tests/gate.sh
158 passed, 207 skipped in 260.70s (0:04:20) … GATE PASS          # the blind spot, demonstrated
```

Two candidate remedies, both product/CI scope: add `tests/test_usage_skill.py` to the gate's list, or
make the arm collect `tests/` so a new test file cannot be born outside the gate.

### 5b. The act leg's red is a different test — a host-green, container-red process-kill regression

`ci.log` stops at the step boundary (`❌ Failure - Main run the gate (tests/gate.sh) [37.9s]`,
`exitcode '1'`) without the gate's own stdout, so it was reproduced on the control host with `act`
over a clean `git archive HEAD` copy:

```
$ act -W .github/workflows/ci.yml -P ubuntu-latest=catthehacker/ubuntu:act-latest push   # rc=1
| 155 passed, 210 skipped in 27.15s
| == wrapper regression (AUG-081) ==
| gate mode regression: PASS
| tests/test_gate_mode.sh: line 91: 226 Killed  setsid timeout --kill-after=3s 3s bash "$d/stub.sh" "$d/pids"
| hung stub pid 227 is STILL ALIVE after the wrapper returned (rc=137) — the kill did not fire
| AUG-081 wrapper regression FAILED (rc=1): a hung pytest arm must die as a process group
```

The same script on the host: **3 runs, 3 PASS** (`bash tests/test_gate_mode.sh` → rc=0 each time).
So this red is not the doc-drift test and not a code regression — it is a 3-second process-group kill
whose behaviour differs inside the `act` container (execution-context / timing class). Hosted `ci.yml`
is red at this HEAD because of it, independently of §5a.

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

1. **Mint a product row for §5 / §5a** — `tests/test_usage_skill.py` asserts a phrase
   (`advance write`) that `skills/auger-usage/SKILL.md` does not use; red on the control host at
   `255fc9b`. Include the coverage half: `tests/gate.sh`'s pytest arm lists four files by name, so a
   new test file (this one) is invisible to the gate and to CI. Not filed here (board writes out of
   scope).
1b. **Product/CI row for §5b** — `tests/test_gate_mode.sh` (AUG-081 wrapper regression) fails inside
   the `act` container on the 3 s `setsid`+`timeout` process-group kill while passing 3/3 on the host,
   so `ci.yml` is red at this HEAD for a test-harness reason. Widen the kill budget or make the
   assertion container-tolerant.
2. **Harness row for §6** — the `upgrade` cell must grade an index miss as `SKIP` per
   `docs/RELEASE.md`, not `FAIL`.
3. **Harness row for §8** — `launch` should accept a linked worktree root (`.git` file).
4. `QA-AUGER-15`'s own acceptance is met by §2–§7; the two FAIL cells it leaves behind are
   attributable to 1–2 above, **not** to a full server.

## 10. Provenance and independent second pass

Two workers were dispatched for this row in the same window (duplicate dispatch, 22:24:49 and
22:25:10). Both examined the same battery run (`agent 69129f1f`); this note is the single
consolidated record, and its claims were re-verified independently of the first pass:

| claim | independent check |
|---|---|
| zero ENOSPC | `qa-run.log` (6 lines) and every per-cell log were re-extracted **from the server archive** `bunker-69129f1f-20261030T033312Z.tar.gz` and grepped → 0 matches (15,558 B of cell logs) |
| host disk readings | `df -B1 /` on `bunker-mvp` at 03:38:10Z → `160938012672` total / `90334130176` used / `63999960288` avail / 59 % — consistent with §2's 03:28 / 03:40 readings |
| disk preflight (§7) | commit `3ef1290202` present; `bash scripts/test_bunker_qa_disk_preflight.sh` → **21 passed, 0 failed**; live refusal row on disk at `/tmp/qa15-refuse-1793331535.jsonl` with `agent=` empty in its `.meta` |
| upgrade verdict (§6) | `PATH=<venv>/bin:$PATH bash scripts/upgrade-verify.sh` at HEAD → `UPGRADE VERIFY: PASS` (auger 0.2.0 local artifact → auger 0.3) |
| repo red (§5a) | reproduced at HEAD; gate green on the host in 4:20 (`158 passed, 207 skipped`, `GATE PASS`) |
| act red (§5b) | reproduced with `act` on a clean archive of HEAD; host `test_gate_mode.sh` 3/3 PASS |

Trace marker for this record (tested commit, evidence file, run witness):

ch:trace row=QA-AUGER-15 spec=docs/RELEASE.md#Verifying-the-v0.2.0-upgrade-path wave=.coding-hermes/waves/auger-foreman-2026-10-29-22-53-26.json#QA-AUGER-15 test=none:external-battery doc=docs/dogfood/2026-10-30-qa-auger-15-clean-machine-enospc.md prompt=/tmp/auger-foreman-2026-10-29-22-53-26-QA-AUGER-15-brief.txt evidence=/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl witness=run:bunker-mvp-agent-69129f1f tested=255fc9bf04945b0f618d8a597e91def5e093e165 verdict=none:foreman-will-judge commit=HARNESS-3ef1290202 memory=none:foreman-writes

---

## 11. Live re-verification by an independent dispatch (2026-10-30, 19:34Z → 20:12Z)

A second worker was dispatched for this row after §1–§10 had landed but the board row was still `pending`.
Everything in this section was measured or probed by that dispatch; none of it is copied from §1–§10.

### 11.1 bunker-mvp ENOSPC — cleared (measured)

`ssh root@100.115.67.116 'df -B1 /'` at **2026-10-30T19:34:31Z**:

```
/dev/sda1  160938012672  30593957888  123739774976  20%  /
```

**123,739,774,976 B available = 115.2 GiB free, 20 % used.** `bunker status` agrees — `Status: ONLINE`,
`Version: 0.2.1`, `Uptime: 6d 21h`, `Agents: 0/50`, `Disk: 19 % (28.5 GB/149.9 GB)`. The failed 2026-10-29 run
had **≈7.5 GB** free (142.4/149.9 GB, 95 %); this host now carries ~15× that headroom, and ~11× the harness
floor (10 GB). The ENOSPC class is not present on this host.

### 11.2 The §3 battery artifacts were re-read from disk (not trusted from this write-up)

| check | command | result |
|---|---|---|
| zero ENOSPC | `grep -ric "No space left on device\|ENOSPC"` over the merged evidence (23 rows), the agent harvest (20 rows), `qa-run.log` and all 15 `qa-logs-1879714/*.log` | **0 matches in every file** |
| battery completed | `cat /tmp/qa15-ev/qa-run.log` | `QA-HEAD=255fc9b…`, `HARNESS_NODE=ok`, `HARNESS-COMPOSE=ok`, `CELLS-DONE` |
| ci-pass produced real output | `cat …/native.log` | `1 failed, 163 passed, 210 skipped in 29.75s` plus the full traceback of `test_usage_skill_describes_failed_not_reached_advances_as_drift` |
| upgrade produced real output | `cat …/up1.log`, `tail …/up2.log` | `ERROR: Could not find a version that satisfies the requirement auger==0.2.0 (from versions: none)` / `Successfully installed auger-0.3` |
| chaos-resource cap recorded verbatim | `jq -r .detail` on the chaos-resource row; `head …/res.log` | `rc=1 under cap (cap=3145728KB child_saw=3145728 subshell_saw=3145728 parent_saw=unlimited)`; `PARENT_CAP=unlimited SUBSHELL_CAP=3145728 CAP_EFFECTIVE=3145728 CAP_REQUESTED=3145728` |
| the 20 agent rows and the 23 collected rows agree | `jq -r '[.cell,.status]\|@tsv'` on both files | identical cell/status sets (+`launch`, +`collect` in the merged file) |

`parent_saw=unlimited` is **not** an unapplied cap and is not normalised away: the cap is applied inside the
capped subshell the suite runs in, so the battery's own parent shell is uncapped by construction. The AC's
"child/subshell/parent" is met as a recorded observation at all three points — two of them capped, the third
reported as the truth.

### 11.3 The §7 durable gate — verified in both directions, live

* **Negative arm** (artifact on disk from the prior tick): `/tmp/qa15-refuse-1793331535.jsonl` holds exactly
  **one** row — `{"cell":"run_battery","status":"FAIL", … "LOW ON DISK (56 GB free, 61 used on /; floor
  100000 GB) — refusing to spawn …"}` — and its `.meta` carries `agent=` (empty). Nothing was spawned and no
  product cell exists.
* **Behaviour arm**: `bash ~/.hermes/scripts/test_bunker_qa_disk_preflight.sh` → **21 passed, 0 failed**.
* **Positive arm, executed by this dispatch**: a real `bunker-qa.sh launch ~/auger` printed
  `→ disk preflight ok: 115 GB free (20 used) on bunker-mvp (floor 10 GB)` — the gate runs on the live host,
  after reachability and before the spawn, exactly as §7 asserts.

### 11.4 A NEW host blocker observed this tick — spawn is broken, and it is NOT disk

The launch above could not admit a battery at current main. `bunkerd` fails at stage `rootless-install`; from
`journalctl -u bunkerd` on bunker-mvp, three consecutive attempts (ids `25c271dd`, `2f800c24`, `35cc666e`,
19:38:35Z → 19:43:47Z):

```
{"level":"ERROR","msg":"spawn failed","agent_id":"35cc666e","stage":"rootless-install",
 "error":"install rootless docker for bunker-35cc666e: enable linger for bunker-35cc666e: exit status 1
  (output: Could not enable linger: Connection timed out\n; linger marker … absent; linger enable attempts: 3)"}
```

Each attempt rolls back (`loginctl disable-linger`, `userdel`) and leaves **no agent** (`Agents: 0/50` after
every attempt). The harness therefore recorded exactly one HARNESS-class row and its `.meta` `agent=` is empty:

```
/tmp/bunker-qa-evidence-qa15-20261030T144200Z.jsonl
{"cell":"run_battery","status":"FAIL","detail":"spawn failed on bunker-mvp: … enable linger … Connection timed out …"}
```

A direct probe reproduces the host fault with no auger or battery involvement: `timeout 15 loginctl
enable-linger <user>` **hangs and never returns** (`systemd-logind` is `active`, `NRestarts=0`, one session,
`/var/lib/systemd/linger` holds only `root`). The current blocker is a **logind/dbus hang on bunker-mvp** —
unrelated to disk (115 GiB free) and unrelated to auger.

The harness's own appended classification on that row (`[root cause: agent ssh port 22 … unreachable —
server/network down, not a docker fault]`) is a **misattribution**: ssh is reachable (this dispatch ssh'd the
host throughout, and the launch's own reachability gate passed) and the failure happens *during* spawn, before
any agent ssh endpoint exists.

**Consequence for the ACs:** AC 2 ("re-run the full battery at the same or a later HEAD") is discharged by the
`255fc9b` run verified in §11.2 — a HEAD later than the failed run's `2fcff58`. A *fresher* run at current main
`832d010` could not be admitted today for the linger reason above; that is recorded here rather than papered
over by substituting the older evidence silently.

### 11.5 Repo gate at current main `832d010` — GATE PASS

```
$ PYTHONUNBUFFERED=1 AUGER_TEST_LOCK=/tmp/auger-pytest-substrate-qa15b.lock \
    PATH=/home/kara/auger/venv/bin:$PATH AUGER_CI=1 CI=1 bash tests/gate.sh
== gate mode: FULL gate ==
== syntax ==            ok
== lint ==              All checks passed!
== pytest ==            364 passed, 2 skipped in 1335.02s (0:22:15)
== wrapper regression (AUG-081) ==
gate mode regression: PASS
AUG-116: zombie pid 2980657 classified dead (kill -0 alone would have said alive)
AUG-081 wrapper regression: PASS
== end-to-end smoke ==   SKIPPED [e2e-smoke]: needs a live DuckBrain namespace — arm NOT run, NOT passed
== openapi-parity (AUG-066) ==  docs/openapi.yaml matches the generator byte-for-byte
== docs check ==
LIVE ARMS: ran 0; skipped 2
  live arms SKIPPED (not passed): pytest-live, e2e-smoke
GATE PASS
```

`rc=0`, HEAD `832d01000aaa8be2ed2134f3076c571ff49af139` == `origin/main`. The two skipped arms are named by the
gate itself (live DuckBrain/JEV unavailable to a CI-mode run) — green here means "syntax + lint + every test
that needs no live API", exactly the meaning CI's green carries.

### 11.6 Method disclosure: why the pytest arm ran with a redirected lock

The host-global substrate lock (`/tmp/auger-pytest-substrate.lock`, `tests/conftest.py:303`) was held for the
whole window by foreign full-suite legs (two `pytest -q tests/` processes, pids `2109278` and `2253200`, from
other sessions). A first gate run at `832d010` blocked in `locks_lock_inode_wait` for 8+ minutes without
executing a single test and was killed. `AUGER_TEST_LOCK` is the documented override
(`tests/conftest.py:306-308`); in CI mode the live arms are skipped, so no live substrate is touched and there
is nothing to serialise — the redirect is faithful, and it is disclosed rather than silently changed.

One intermediate attempt (`/tmp/qa15-gate2.log`) died mid-arm with `rc=1` after 15 tests and a truncated log
(no reaper or OOM record — `earlyoom` logged memory at 80 % available and `forkbomb-reaper` recorded 0 kills);
that attempt is **not** counted as evidence. The instrumented re-run (`PYTHONUNBUFFERED=1`) above is.

### 11.7 Verdict of this dispatch

The row's blocker — bunker-mvp ENOSPC invalidating the clean-machine battery — is **cleared** on three
independent grounds: measured disk truth (§11.1), an ENOSPC-free full battery at a later HEAD with real cell
output and a recorded cap (§11.2), and a refusal gate proven live in both directions plus 21/21 unit arms
(§11.3). The repo gate is green at current main (§11.5). Residuals, unchanged and un-owned by this row:
the `test_usage_skill.py` doc-drift red and its gate blind spot (§5/§5a), the act-container `test_gate_mode.sh`
kill (§5b), the upgrade cell's expected-index-miss grading (§6), and the **new** bunker-mvp logind/linger spawn
fault (§11.4), which will block the next battery on that host and must not be re-filed as ENOSPC.
