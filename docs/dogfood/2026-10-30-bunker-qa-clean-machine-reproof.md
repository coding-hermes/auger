# Clean-machine QA battery — REPROOF after the bunker-mvp ENOSPC invalidation (2026-10-30)

**Row:** QA-AUGER-15 · *clean-machine QA battery is invalidated by bunker-mvp ENOSPC before
ci-pass/upgrade can run* · **Server:** `bunker-mvp` · **Tested HEAD:**
`255fc9bf04945b0f618d8a597e91def5e093e165`

**Verdict:** 🟢 **INVALIDATION CLEARED — the battery now produces real, attributable cells.**
The host that invalidated the 2026-10-29 run (95 % full) is back to 59 % used; the re-run records
**zero** ENOSPC occurrences in `qa-run.log` and in every per-cell log, `ci-pass` and `upgrade` carry
real command output, and `chaos-resource` records the memory cap applied inside the child and the
subshell. The two red cells the earlier run could not read are now readable — and they are **real
repo reds at HEAD**, not harness noise: a failing test the repo's own CI never collects, plus a CI
leg that fails only inside `act`.

This note records the run. It changes no product code, no test and no board row.

---

## 1 · Admission: capacity preflight (AC 1)

The 2026-10-29 run was launched against a server already at **142.4 GB / 149.9 GB (95 %)**. The
launcher (external `~/.hermes/scripts/bunker-qa.sh`) has **no capacity gate of its own** — it was
grepped for `df`/disk checks and contains none — so admission was verified by hand, before launch,
on the SERVER host (not the agent):

| when (UTC) | reading | source |
|---|---|---|
| 2026-10-30T03:25:57Z — **pre-launch admission check** | `150G size, 84G used, 61G avail, 58% /` | `ssh bunker-mvp 'df -h /'` |
| 2026-10-30T03:30:08Z — 70 s after launch | `150G size, 89G used, 56G avail, 62% /` | `ssh bunker-mvp 'df -h /'` |
| 2026-10-30T03:38:10Z — after collect/destroy | `160938012672 B total, 90334130176 B used, 63999660288 B avail, 59 %` | `ssh bunker-mvp 'df -B1 /'` |

Headroom at admission: **~61 GB free (58 % used)** versus **~7.5 GB free (95 % used)** on the
invalidated run — the agent home plus its CI workspace fits with room to spare. Server load at
admission: `load average: 1.28, 1.87, 2.16` (2 containers running).

## 2 · The run (AC 2)

The battery was launched through the canonical harness in its two-phase form — the shape the QA
procedure requires, because a synchronous `run` dies with the tool window:

```
bash ~/.hermes/scripts/bunker-qa.sh launch /home/kara/auger   # phase A (admission + spawn + sync + start)
bash ~/.hermes/scripts/bunker-qa.sh collect --evidence /tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl
```

| what | value |
|---|---|
| agent id | **`69129f1f`** @ `bunker-mvp` (ttl 4 h, destroyed by collect) |
| evidence path | **`/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl`** (20 rows pulled, merge-deduped) |
| battery script | `script_bytes=131795`, started detached as `~/qa-run.sh` |
| agent home archive | `/var/backups/bunker/bunker-69129f1f-20261030T033312Z.tar.gz` (server, root-owned) |
| windows | repo-head 03:28:24Z → launch 03:28:57Z → cells 03:29–03:32 → collect 03:34:27Z |
| tested HEAD | `255fc9bf04945b0f618d8a597e91def5e093e165` (same as the invalidated run's successor HEAD; `pull=clone dirty — pull skipped, testing recorded sha`) |

### ENOSPC scan — the specific check this row exists for

Both artefacts were pulled out of the server-side archive after the agent was destroyed and grepped:

| artefact | content | `grep -c -iE "no space left|ENOSPC"` |
|---|---|---|
| `~/qa-run.log` | 6 lines (`QA-HEAD=…`, `HARNESS-COMPOSE=ok`, `HARNESS_NODE=ok`, teardown, `CELLS-DONE`) | **0** |
| `~/qa-logs-1879714/*.log` (all cell logs, concatenated) | 15 558 B | **0** |

No `mkdir` failure, no failed `ci.yml` link, no truncated fixture — the failure mode of the
invalidated run is absent, by measurement rather than by headline.

### Per-cell grades (from the evidence JSONL, cross-read against the raw cell logs)

| cell | grade | evidence |
|---|---|---|
| `fresh-install` | OK | `Successfully installed auger-0.3 pyyaml-6.0.3` |
| `toolchain-bootstrap` ×9 | OK | go 1.27.3, python venv + pytest, gcc/g++, make 4.3, compose plugin, buildx v0.38.1, node v22.15.0, `HARNESS-COMPOSE=ok`, `HARNESS_NODE=ok` |
| `ci-pass` | **FAIL (real output)** | native `rc=1`: `FAILED tests/test_usage_skill.py::test_usage_skill_describes_failed_not_reached_advances_as_drift`; act leg `exitcode '1'` at step *run the gate (tests/gate.sh)* — full pytest summary `1 failed, 163 passed, 210 skipped in 29.75s` |
| `upgrade` | **FAIL (real output)** | `pip install auger==0.2.0` → `ERROR: Could not find a version that satisfies the requirement auger==0.2.0 (from versions: none)` / `ERROR: No matching distribution found` |
| `chaos-resource` | **FAIL under a VERIFIED cap** | `rc=1 under cap (cap=3145728KB child_saw=3145728 subshell_saw=3145728 parent_saw=unlimited)`; `res.log` → `SUBSHELL_CAP=3145728`, `CAP_EFFECTIVE=3145728 CAP_REQUESTED=3145728`. Cap applied in **child + subshell**; the parent is uncapped by design (the probe caps the child). Last run reported `child_saw=NONE subshell_saw=NONE` — the cap evidence this row demands now exists. |
| `chaos-errorpath` | OK | clean error on missing config (rc=1) |
| `chaos-disconnect` | UNVERIFIED (attributed) | `ci-pass native suite FAILED first (nat_rc=1)` — not derivable from a failing suite; disconnect probe itself rc=0 |
| `docker-deploy`, `chaos-shutdown` | N/A | no compose file |
| `ui-probe` | N/A | no web UI detected |
| `chaos-corruption` | N/A | no db/state files in repo |
| `collect` | OK | `pulled 20 evidence row(s); temp agent destroyed` |

**No cell was capped by budget or killed by the tool window, and no UNVERIFIED cell is
unattributed.** `chaos-disconnect` is the one dependent cell, and its reason names the red that
produces it in the same run.

## 3 · What the cleared battery exposes (the point of the row)

The invalidation was hiding two independent, reproducible reds at `255fc9b`.

**(a) `tests/test_usage_skill.py` is red on `main` — and the repo gate never runs that file.**
Reproduced on the control host at the same HEAD (`/home/kara/auger/.venv/bin/python -m pytest
tests/test_usage_skill.py`): `1 failed, 3 passed`.

```
>       assert "advance write" in lowered
E       assert 'advance write' in '---\nname: auger-usage\n…'
tests/test_usage_skill.py:46: AssertionError
```

Attribution by `git log -S`: `09652df` (*docs: correct domain usage guidance*, AUG-113) introduced
the literal phrase into `skills/auger-usage/SKILL.md`; `a19e04f` (*fix: repair null-ring domain
advancement*, AUG-059-R2 — pushed to `main` as "CI GREEN") reworded that paragraph and dropped the
phrase. The suite has been red since 2026-10-27 15:39 and **nothing reported it**, because
`tests/gate.sh:118` runs pytest over an explicit file list —
`tests/test_auger.py tests/test_openapi_parity.py tests/test_api_service.py
tests/test_upgrade_path.py` — and `tests/test_usage_skill.py` is not in it. A full-suite run
(the battery's native leg, `pytest` over `testpaths = tests`) is the only surface that collects it.

**(b) The `act` CI leg fails in `act` only.** Reproduced locally at HEAD with
`act -W .github/workflows/ci.yml -P ubuntu-latest=catthehacker/ubuntu:act-latest push` → `rc=1`:

```
tests/test_gate_mode.sh: line 91: 226 Killed  setsid timeout --kill-after=3s 3s bash "$d/stub.sh" "$d/pids"
hung stub pid 227 is STILL ALIVE after the wrapper returned (rc=137) — the kill did not fire
AUG-081 wrapper regression FAILED (rc=1): a hung pytest arm must die as a process group
```

The same script passes **3/3** on the host (`bash tests/test_gate_mode.sh` → rc=0 each time) and the
whole gate passes on the host (`AUGER_CI=1 CI=1 bash tests/gate.sh` → `GATE PASS`, 158 passed /
207 skipped). Execution-context class: the 3 s process-group kill behaves differently inside the
`act` container, so `ci.yml` is red at HEAD for a reason unrelated to auger's own code.

**(c) The `upgrade` leg can never succeed as written.** `pip install auger==0.2.0` resolves
against PyPI, where the name `auger` is held by an **unrelated placeholder distribution**
(`https://pypi.org/pypi/auger/json` → `version 0.0`, `releases ["0.0"]`, empty summary). No auger
release is published under that name, so "from versions: none" is the correct answer to the wrong
question — a release/naming finding, not an upgrade regression.

All three are outside this row's fix scope (QA evidence only, and (a)+(b) are product/CI changes);
they are recorded here so the foreman can file them against their owners.

## 4 · AC 4 — the invalidated run's record kept, and its limit stated plainly

The 2026-10-29 record survives as:

| field | value |
|---|---|
| agent id | `58b58514` @ `bunker-mvp` (destroyed by collect) |
| evidence path | `/tmp/bunker-qa-evidence-20261029T172308Z-3627547.jsonl` (21 rows) |
| tested HEAD | `2fcff58447c8ae8be16227587fad2c1c95387b13` |
| server disk | **142.4 GB / 149.9 GB (95 %)** before launch |
| first failure | `mkdir` failure creating `qa-act-wf` — *no space left on device* — before any `ci-act`/native command output; a failed `ci.yml` link; another ENOSPC creating the errorpath fixture |
| cell shape | `ci-pass` FAIL `native rc=120` **with no output**; `upgrade` FAIL `rc=120` with no pip output; `chaos-disconnect` UNVERIFIED (blocked by ci-pass); `chaos-resource` UNVERIFIED with `cap NOT applied in the child (child_saw=NONE subshell_saw=NONE)` |

**Limit, stated rather than papered over:** the verbatim `qa-run.log` lines from `58b58514` are
**not recoverable** — that agent's home was destroyed and, unlike `69129f1f`, no archive remains
under `/var/backups/bunker/` (21 archives present, none matching that id). The row's reasoning text
above is the surviving first-hand record of the ENOSPC. The class is independently corroborated by
the harness' own contemporaneous classifier output for the same host/window:

```json
{"cause": "disk-capacity", "class": "infrastructure-failure", "environment": "environment-blocked",
 "leg": "disconnect", "rc": "0", "log": "/home/bunker-39103075/qa-logs-579544/native.log",
 "signature": "/home/bunker-39103075/qa-tmp-579544/duckbrain-test-ns-QQIkgt/hermes-memory/.git: No space left on device",
 "disk_total_bytes": 160938012672, "disk_free_bytes": 2995453952, "ts": "2026-10-29T15:11:25.961960+00:00"}
```

(`/tmp/bunker-qa-evidence-1793285709298-1.jsonl.enospc.json` — 2.99 GB free of 160.94 GB on the
same server class, ~2 h before the auger run.)

## 5 · Residual — AC 3 is NOT met by the launcher

AC 3 asks that a low-disk server be **refused**, or classified harness-UNVERIFIED **without
emitting product FAIL cells**. This run satisfies the first half of AC 1 by measurement, but the
launcher itself still has **no capacity gate**: `~/.hermes/scripts/bunker-qa.sh` contains no `df`
probe on the target server before spawn, so on 2026-10-29 a 95 %-full host was admitted and the
battery emitted `ci-pass FAIL`, `upgrade FAIL` and `chaos-resource UNVERIFIED` cells that read as
product verdicts. Nothing in this run changes that — it only proves the operator can avoid it.

Recommended (not applied here — the harness is an external HOME-meta-repo artefact, it carries an
uncommitted sibling hunk from another lane, and this row's commit scope is the evidence note):
add a pre-spawn probe in `launch()`/`run()` that SSHes the selected server, reads
`df --output=avail -B1 /`, and either refuses below a floor (e.g. 20 GiB) or records the reading on
a `capacity` cell and downgrades every product cell to harness-UNVERIFIED. That is a harness row
against `~/.hermes/scripts/bunker-qa.sh`, not a change to this repository.

## Provenance

- row `QA-AUGER-15` · tick `auger-qa-2026-10-29-16-54-28` · worker dispatch `auger-foreman-2026-10-30-03-22-49`
- tested commit `255fc9bf04945b0f618d8a597e91def5e093e165` (unchanged during the run: `repo-head` row)
- run evidence `/tmp/bunker-qa-evidence-qa15-20261030T032824Z.jsonl` · agent `69129f1f` · server `bunker-mvp`
- prior evidence `/tmp/bunker-qa-evidence-20261029T172308Z-3627547.jsonl` · agent `58b58514`
- per-cell logs `bunker-69129f1f/qa-logs-1879714/` inside
  `/var/backups/bunker/bunker-69129f1f-20261030T033312Z.tar.gz`
