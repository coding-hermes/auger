# Auger test suite — wall time, prerequisites, and reproducibility (QA-AUGER-13)

Every figure below is followed by the artifact that backs it. Three provenance classes are
used and they are NOT interchangeable:

  * **ARTIFACT** — a log or command output retained on the box, re-readable now (path given).
  * **PRIOR READING** — a figure recorded by an earlier session (the AUG-077 note, the filed
    QA-AUGER-13 row). Its log was not retained, so it is quoted as a reading and never
    re-derived here.
  * **MEASURED** — produced by the session that wrote the line, command included.

Box: AMD Ryzen 7 7840HS, **16 logical CPUs (8 cores × 2 threads)**, 59 GiB RAM, DuckBrain
HTTP API on `127.0.0.1:3000`.

## The wall times being compared

| Run | Tree | Result | Wall | Provenance |
|---|---|---|---|---|
| baseline (AUG-077 era) | pre-09-22 | green | **810s** | PRIOR READING (AUG-077 note); corroborated in-tree by `.gitreins/config.yaml` — "the suite grew past the 09-22 measurement (810s)" |
| QA audit run | `68f3ca22` | 7 failed, 106 errors | **1802s** | PRIOR READING (filed QA-AUGER-13 row) |
| clean-venv run, clear window | `43faa6a` | **797 passed, 2 skipped** | **1017.66s** | ARTIFACT `/tmp/qa13-run2-20261117T1320.log` |
| clean-venv run, this session (clear window) | `43faa6a` + the conftest fix in this commit | **793 passed, 6 skipped** | **953.32s** | ARTIFACT `~/.hermes/cache/scratch/qa13-full-r5-1741.log` (see "Re-verified on 2026-11-17" for why 4 JEV arms skip there) |

The delta to explain is `1802 − 810 ≈ 992s`.

*(An earlier clean-venv attempt, killed part-way through, is deliberately absent: its log was
not kept, so its numbers cannot be checked, and a partial run whose log is gone is not
evidence. The point of this file is that every number in it can be.)*

## Root cause: per-namespace 409 backoff during a full-fleet sync push

Every live test creates a throwaway namespace and deletes it in teardown.
`conftest.teardown_namespace` treats DuckBrain's `409 CONFLICT` as "a push is holding the
namespace sync lock" and retries on

```python
TEARDOWN_CONFLICT_BACKOFFS = (1, 2, 4, 8, 16, 30)      # tests/conftest.py:210
```

— **61s of patience per namespace**, by design (REVIEW-AUGER-005 cut this down from 600s;
the constant and its comment are in the file).

61s is enough for DuckBrain's ordinary post-write push window. It is **not** enough for a
full-fleet native sync. Quoted from the 2026-10-23 audit session (PRIOR READING, log not
retained — the shape is what matters here, and the same shape is reproduced verbatim by any
teardown that lands inside a push):

```
E   Failed: teardown of auger-pytest-52910a80-120445075c6a was incomplete:
    DELETE /api/namespaces/auger-pytest-52910a80-120445075c6a returned 409:
    {'error': "Push in flight for 'auger-pytest-52910a80-120445075c6a'
     (native sync lock held by pid 2385306 (since 2026-10-23T06:37:51.714Z)).
     Retry after it completes."}
```

`pid 2385306` was recorded as a `node bin/duckbrain.js s3 sync all push`, started by
`~/.hermes/scripts/duckbrain-s3-native-sync.sh`, whose deadline default is checkable:

```
39: SYNC_ALL_DEADLINE_S="${S3_SYNC_ALL_DEADLINE_S:-3600}"
40: OUT=$(timeout "$SYNC_ALL_DEADLINE_S" node bin/duckbrain.js s3 sync all push 2>&1)
```

While that job walks the fleet it takes each namespace's native sync lock in turn; a test
teardown that lands inside a namespace's window burns the full 61s ladder and then reports
honest residue evidence instead of a fake success.

### The arithmetic is consistent (it is an argument, not a per-namespace measurement)

```
~992s of unexplained wall  /  61s per exhausted teardown ladder  ≈ 16 teardown waits
```

~16 live namespaces paying the full ladder accounts for the delta. The audit run's own log
was not retained, so this closes the arithmetic rather than proving the count namespace by
namespace. Nothing else in the run scales with the delta: the offline majority is fast
(MEASURED — `pytest tests/test_api_service.py tests/test_packaging.py -q --durations=12` →
`17 passed in 2.60s`, heaviest fixture teardowns 0.50s each, ordinary calls 0.01–0.04s;
`tests/test_api_service.py` collects 9 tests).

## A missing `DUCKBRAIN_API_KEY` produces SKIPS, not errors

Read off the code, not off a run:

* `auger._token()` raises `SystemExit` when it finds no token anywhere;
* `conftest.live_check()` maps that to `False, "no DuckBrain token (...)"`;
* `conftest.require_live()` turns it into `pytest.skip(...)` (or an `AssertionError` under
  `AUGER_REQUIRE_LIVE=1`).

So the mass `ERROR`s in the audit run cannot be a missing token: a skip is not an error. They
are teardown failures colliding with the sync lock, which happen whether or not the token is
exported. Export the token anyway — `tests/README.md` says why (it removes a second,
independent class: a `SystemExit` from `auger._token()` discovered mid-run after live arms
have already started).

## The clean-venv green run of 2026-11-17 (criteria 1 and 3)

Venv created **outside** the checkout, so no in-tree import can satisfy a test:

```
python3 -m venv /tmp/qa13-venv-20261117T1318      # Python 3.14.9 (verified: <venv>/bin/python -V)
<venv>/bin/pip install -e '.[dev]'
<venv>/bin/pip install setuptools                 # see prerequisite 1 below
export DUCKBRAIN_API_KEY="$(cat ~/.duckbrain/foreman-status.token)"
export DUCKBRAIN_URL=http://127.0.0.1:3000
<venv>/bin/python -m pytest tests/ -q -ra
```

`pytest 9.2.0`, `setuptools 84.2.1` (ARTIFACT `/tmp/qa13-venv-create.log` + the venv itself,
both on disk). Tail, verbatim:

```
797 passed, 2 skipped in 1017.66s (0:16:57)
```

ARTIFACT `/tmp/qa13-run2-20261117T1320.log` (its mtime is 13:25, i.e. the run's end). No `F`
or `E` character in the progress line. Both skips are environmental and
named by the suite: the 44-domain grid is not canonical on this host, and the upgrade e2e leg
is opt-in (`AUGER_UPGRADE_VERIFY_LIVE=1`).

| Run | Wall | Live arms |
|---|---|---|
| baseline (AUG-077 era) | 810s (PRIOR READING, in-tree corroboration: `.gitreins/config.yaml`) | ran |
| audit run (`68f3ca22`, sync overlap) | 1802s (PRIOR READING) | ran, 7 failed / 106 errors |
| clean venv, clear window (`43faa6a`) | **1017.66s** (ARTIFACT) | ran, 0 failed / 0 errors |

1017.66s is ~208s above the 810s baseline while also carrying an interpreter move (the
baseline's interpreter is a PRIOR READING; this run was Python 3.14.9). Read it as **~42% of
the 2400s gate budget** (`1017.66 / 2400`), i.e. ~1380s of headroom, and ~784s below the
collision run (`1802 − 1017.66`) — the window, not the suite, is what moved.

The 2400s budget itself is checkable: `tests/gate.sh:154`
`local budget="${AUGER_GATE_PYTEST_BUDGET:-2400s}"`, with the comment
"2400s = 2x the measured wall (~1170s)".

Same-tree cross-check (ARTIFACT: the tree's own gate reading, recorded in `43faa6a`'s commit
message — `git show 43faa6a --format=%B`): `GATE PASS 797 passed / 2 skipped in 872.82s`. The
same counts through the clean-venv path cost **144.84s** more (`1017.66 − 872.82`), which is
what a second interpreter and an editable install add on this box.

### Three prerequisites a clean venv exposes

1. **`setuptools` must be importable by the interpreter running pytest.** On Python 3.14
   `python3 -m venv` no longer seeds it, and neither `.[dev]` nor `tests/requirements-dev.txt`
   installs it — verified in the tree: `pyproject.toml` `dev = ["pytest>=8", "pyyaml",
   "lxml>=5"]`, and `tests/requirements-dev.txt` is `pytest>=8` + `pyyaml`. Without it
   `tests/test_packaging_wheel.py` fails at setup (2 ERRORS, deliberately not skips: "an
   unbuilt wheel proves nothing about what ships"). ARTIFACT
   `/tmp/qa13-full-run-20261117T1305.log`. Candidate follow-up row: the `dev` extra claims to
   be the test-dependency set and is not complete for that class.
2. **The preflight's sync detection is a substring test on other processes' argv, and it is
   wrong in BOTH directions.** `conftest.FULL_FLEET_SYNC_MARKER` is the literal
   `duckbrain.js s3 sync all push`, matched against every `/proc/<pid>/cmdline`.

   *FALSE POSITIVE* — a process whose argv merely CONTAINS that text blocks the run. Two
   ordinary ways to produce one: a `bash -c` wrapper running the documented probe
   (`pgrep -af "..."`), and **a dispatched coding worker whose brief quotes the marker**, which
   is how this task's own sibling worker (pid 507240) ended up on the holder list. MEASURED:
   a run started seconds after a clear probe printed `AUGER_BLOCKED_BY: full-fleet sync push
   pid 1955801; live arms will be skipped` and finished in **86.01s — 585 passed, 212 skipped,
   2 errors** (ARTIFACT `/tmp/qa13-full-run-20261117T1305.log`). A blocked window looks FAST,
   not slow, and it is not a green run: 212 live arms never executed.

   *FALSE NEGATIVE* — the daemon's own sync is spelled differently:
   `node --import tsx bin/duckbrain.ts s3 sync all push` (`.ts`, not `.js`), so it does NOT
   match the marker. MEASURED 2026-11-17: a full run that started while that push was live
   passed the preflight with no `AUGER_BLOCKED_BY` line and then paid for it — 2 teardown
   ERRORs, `409 ... native sync lock held by pid 2599244 (since 2026-11-17T22:20:23.870Z)`,
   where pid 2599244 is exactly that `bin/duckbrain.ts` push, still running minutes later.
   Any preflight that only greps argv text will keep getting this wrong; the durable signal is
   the API's own `409 Push in flight` answer, which the preflight already probes.

3. **The 44-domain grid must be READABLE, not merely absent.** `auger.resolve_answer_domain`
   refuses a `--domain` write when the grid cannot be read (`SystemExit: refused: --domain
   '4.05' cannot be validated — the canonical domain grid is unreadable ... domain grid not
   found: <skill>/references/dogfood-artifact/02-domains`), and that raises in fixture setup,
   i.e. an ERROR, not a skip. The suite only SKIPS when a grid exists but is not the canonical
   44. MEASURED: a run in an environment missing `~/.hermes/skills` produced that `SystemExit`
   on the domain-storing tests until the skills tree was mounted read-only. Set
   `AUGER_DOMAIN_GRID`, or make the skill's `references/dogfood-artifact/02-domains` readable.

### The `DUCKBRAIN_URL` spelling class (fixture-init SystemExit)

An ambient `DUCKBRAIN_URL` that is the default substrate spelled with a **different host
alias** (`http://localhost:3000`) is not the default as far as `auger.py` is concerned:
`DB_URL.rstrip("/") != DEFAULT_DUCKBRAIN_URL`, so `_init_ns_dir` refuses with

```
SystemExit: cannot resolve namespace declarations for isolated substrate
'http://localhost:3000/'; set DUCKBRAIN_NAMESPACES_PATH or DUCKBRAIN_DATA_DIR before running
init (refusing the legacy default namespaces tree)
```

and every namespace-creating fixture errors at setup. `auger` reads `DB_URL` at IMPORT time,
so the normalization has to happen before `import auger` — which is what `tests/conftest.py`
now does (pytest imports `conftest.py` before any test module).

MEASURED, three arms, one ambient value each, child process per arm
(script: `~/.hermes/cache/scratch/qa13-url-probe.py`):

```
A  ambient http://localhost:3000/ + bare `import auger`            (pre-change order)
     auger.DB_URL = 'http://localhost:3000/'   -> SystemExit: isolated substrate
B  ambient http://localhost:3000/ + `import conftest` first        (pytest order)
     auger.DB_URL = 'http://127.0.0.1:3000'    -> _init_ns_dir RESOLVED
C  ambient http://127.0.0.1:3000/ (trailing slash only) + bare import
     auger.DB_URL = 'http://127.0.0.1:3000/'   -> _init_ns_dir RESOLVED
```

Arm C is the correction: the **host alias** is the trigger, not the trailing slash —
`_init_ns_dir` strips trailing slashes before comparing. `conftest.py` folds both spellings
to the canonical value anyway, so every consumer (the lock's holder names, the substrate
probe, the `live_check` URL echo) reads one string rather than two that mean the same thing.

## Re-verified on 2026-11-17 (this session): a clean-venv full run, and what it cost

The 1017.66s run above is a **prior** session's artifact on this tree. This session re-ran the
suite so criterion 3 is not inherited evidence. Two environmental facts shaped how it had to be
executed, and both are findings in their own right:

* The host is not a clean process table. pid 507240 — a *wedged sibling worker* on this same
  task, 0 bytes of `/proc/<pid>/io` growth across 10 minutes, still in `futex_do_wait` 43
  minutes after dispatch — carries this task's brief in its argv, and that brief quotes the
  sync marker. Every host-side pytest run therefore prints `AUGER_BLOCKED_BY` and skips 212
  live arms while it lives.
* Unprivileged PID namespaces are unavailable on this box
  (`/proc/sys/kernel/apparmor_restrict_unprivileged_userns` = 1, so `unshare -r` dies on
  `uid_map: Operation not permitted`), so the run was executed in a **container** whose
  `/proc` holds only the run itself.

Reproduction recipe (image: `python:3.11-slim` + `git` + `procps`):

```
docker run --network host --user "$(id -u):$(id -g)" \
  -w /home/kara/worktrees/auger-QA-AUGER-13 \
  -e HOME=/home/kara -e TMPDIR=/mnt/ssd/tmp \
  -e PATH=/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin \
  -e DUCKBRAIN_URL=http://127.0.0.1:3000 \
  -e AUGER_SCHEDULER_DB=/home/kara/.hermes/coding-hermes/scheduler.db \
  -e DUCKBRAIN_API_KEY="$(cat ~/.duckbrain/foreman-status.token)" \
  -v <checkout>:<checkout> -v ~/auger/.git:~/auger/.git -v ~/.gitconfig:ro \
  -v ~/.duckbrain:ro -v ~/duckbrain -v /mnt/ssd/tmp \
  -v ~/.hermes/skills:ro -v ~/.hermes/coding-hermes:ro -v ~/.local/bin:ro \
  -v ~/gitreins:ro -v ~/specs:ro -v ~/code-factory:ro \
  qa13-py311:<tag> bash <runner.sh>     # venv created INSIDE the container, outside the checkout
```

What the scoping does NOT change: the substrate is the same live DuckBrain over `--network
host`, and `_probe_full_fleet_sync_push` still asks the real API. What it DOES change:
`/tmp` inside the container is not the host's, so `AUGER_TEST_LOCK` is a different file — a
concurrent host leg would not serialize against this run (none was running; namespaces are
session-unique, so the shared resource is the API rate limiter, not namespace ownership).

Attempt ledger (artifacts under `~/.hermes/cache/scratch/`):

| Attempt | Environment | Result | Wall | Cause of the non-green cells |
|---|---|---|---|---|
| 1 | container, minimum mounts | 4 failed, 755 passed, 40 skipped | 922.09s | `boardctl` and `ps` absent, so the AUG-119/124 board proofs and the orphan scan cannot run; `~/.hermes/coding-hermes/scheduler.db`, `~/gitreins`, `~/specs`, `~/code-factory`, `~/.hermes/skills` unmounted → 34 further skips |
| 2 | container, full parity mounts | **793 passed, 6 skipped, 2 errors** | 1056.17s | the 2 errors are teardown 409s against the live `bin/duckbrain.ts` push (prerequisite 2) — the documented ladder class, not the URL class |
| 3 | container, full parity mounts, clear window (the push above had exited; both of attempt 2's stuck namespaces deleted first) | **793 passed, 6 skipped, 0 failed, 0 errors** — `PYTEST_RC=0` | **953.32s** | none |

Attempt 3 is criterion 3: `793 passed, 6 skipped in 953.32s (0:15:53)`, zero `E`/`F`
characters in the progress line, `PYTEST_RC=0`, **39.7% of the 2400s budget** (1447s of
headroom). ARTIFACT `~/.hermes/cache/scratch/qa13-full-r5-1741.log`
(clean venv `/home/kara/.hermes/cache/scratch/qa13-venv311`: Python 3.11.18, pytest 9.2.0,
setuptools 79.0.1, auger 0.3 editable from this checkout, created inside the container and
outside the tree).

Its 6 skips are all environmental, and 4 of them are the reason it reports 793 where the host
run reports 797: 3 `JEV is unreachable (no OpenRouter key found ...)` + 1 `question proposer is
unreachable`, because `~/.hermes/.env` was deliberately NOT mounted into the container (the
only credential in there is the DuckBrain token, mounted read-only). The other two are the
opt-in upgrade e2e leg and the test-created non-canonical 44-domain grid.

All three attempts ran with `DUCKBRAIN_API_KEY` exported and
`DUCKBRAIN_URL=http://127.0.0.1:3000`, and none printed `AUGER_BLOCKED_BY`, i.e. the live arms
actually ran — ~200 of them, the same set a blocked window reports as 212 skipped live arms.

## How to read a future red

| Symptom | Cause | Action |
|---|---|---|
| mass `ERROR` in live arms, 409 text with `Push in flight` | full-fleet sync push overlap | wait for the push, or use the QA-AUGER-14 preflight |
| run finishes fast (~86s), arms `SKIPPED` with `AUGER_BLOCKED_BY:` | marker text in some process's argv, or a foreign substrate-lock holder | find the quoted marker holder; probe from a script file |
| live arms `SKIPPED`, `no DuckBrain token` | `DUCKBRAIN_API_KEY` unset and token file unreadable | export the token (`tests/README.md`) |
| every namespace fixture errors, `isolated substrate` text | `DUCKBRAIN_URL` is the default spelled differently | unset it, or let `conftest.py` normalize it |
| `ERROR` at setup, `refused: --domain ... grid is unreadable` | 44-domain grid not readable by the pytest user | set `AUGER_DOMAIN_GRID` or mount the skill tree |
| `ERROR` at setup in `test_packaging_wheel.py`, `setuptools is not importable` | clean venv on Python ≥3.14 | `pip install setuptools` in the venv |
| run dies at a fixed percentage with no summary | parent session killed (worker/harness), not a suite defect | re-run detached; check `pgrep -af pytest` |
| single test red in isolation | real defect | fix it |

## Recommendation (follow-up row QA-AUGER-14)

Do not let a correctness run silently spend 61s × N in backoff. Add a **preflight** at
session start:

1. probe the window with a real signal first — a scratch namespace `DELETE` for `409` — rather
   than matching marker text on other processes' argv. The marker test is wrong in both
   directions today (prerequisite 2): it false-positives on any worker whose brief or `pgrep`
   command quotes the phrase, and it misses the daemon's own sync, which runs
   `bin/duckbrain.ts`, not `bin/duckbrain.js`. If an argv arm is kept at all, match the
   SEMANTICS (`duckbrain` + `s3 sync all push`, any extension) and treat it as a hint, not a
   verdict;
2. if a full-fleet push is in flight, either fail fast with a named reason
   (`AUGER_BLOCKED_BY: full-fleet sync push pid N`) or mark the live arms skipped — never burn
   the ladder;
3. keep the 61s ladder for the ordinary post-write window it was designed for.

That converts a 30-minute ambiguous red into a one-line named cause, and it makes the gate's
2400s budget (RELEASE-017) meaningful: with the preflight, a green run is ~1000-1100s plus
jitter; without it, no budget is large enough on a box that also runs the sync.
