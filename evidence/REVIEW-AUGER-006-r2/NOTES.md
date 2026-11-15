# REVIEW-AUGER-006-r2 — close the ROOT-derivation gap (verdict b41fdb86, tier 2 FAIL)

Worker: isolated worktree `wt/REVIEW-AUGER-006-r2`, base 7872eef. The fix commit is
the single commit on this branch; the foreman closes the row after the judge verdict.

## What the defect was

`tests/smoke.sh`'s `ns_helper` resolved its namespace root as
`AUGER_NS_ROOT or ~/duckbrain/namespaces` — a hardcoded legacy guess. The product
resolves its namespace tree through `auger.namespaces_base()` (auger.py:483-491):
`DUCKBRAIN_NAMESPACES_PATH` → `DUCKBRAIN_DATA_DIR/namespaces` → the legacy default.
`AUGER_NS_ROOT` is read nowhere in auger.py, so on any host that sets either knob
the helper rmtree'd a DIFFERENT tree, printed "namespace <ns> removed", returned
success, and left the real namespace directory on disk — and the hermetic tests'
"directory intact after a 409" assertion was vacuous, because the rig created its
directory under `AUGER_NS_ROOT`, a path auger never writes to.

## 01-red.txt — the new tests fail on the pre-fix tree

Command (exact):

    cd /home/kara/worktrees/auger-REVIEW-AUGER-006-r2 && \
    PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_smoke_teardown.py -q \
      -k 'follows_auger_data_dir or actually_writes or namespaces_path'

Result: `3 failed, 8 deselected` — all three new tests red against the unfixed
`tests/smoke.sh` (HEAD 7872eef, smoke.sh untouched at that point):

- `test_helper_root_follows_auger_data_dir_on_a_200` — helper printed "removed",
  the directory under `auger.namespaces_base()` survived.
- `test_persistent_409_keeps_the_directory_where_auger_actually_writes` — the 409
  half passed vacuously, and the non-vacuity tail (flip the script to a 200 and
  require the SAME directory gone) failed: the kept path was never a path auger
  writes.
- `test_helper_root_prefers_duckbrain_namespaces_path` — helper did not follow
  `DUCKBRAIN_NAMESPACES_PATH`, `namespaces_base()`'s first choice.

## 02-green.txt — the same suite after the fix

Command (exact):

    cd /home/kara/worktrees/auger-REVIEW-AUGER-006-r2 && \
    PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_smoke_teardown.py -q

Result: `11 passed in 0.62s`, exit code 0, zero skips. (8 pre-existing + 3 new.)
Re-run after `ruff format` touched the file: still 11 passed.

## 03-gate.txt — the full gate

Command (exact):

    cd /home/kara/worktrees/auger-REVIEW-AUGER-006-r2 && \
    PATH=$PWD/.venv/bin:$PATH DUCKBRAIN_URL=http://127.0.0.1:3000 \
      DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token) bash tests/gate.sh

Two runs, both honest:

- Run 1 (08:09, quiet window) aborted with 2 ERRORs in `tests/test_packaging_wheel.py`
  and 753 passed / 2 skipped / GATE_RC=1 — NOT a diff defect: `uv venv` creates a venv
  WITHOUT pip/setuptools, so the wheel could not be built ("pip is not importable by
  .../.venv/bin/python3"). Fixed the environment (`uv pip install pip setuptools`),
  verified both tests standalone (6 passed), then re-ran.
- Run 2 (09:21, after the 08:58 duckbrain-s3-sync push window closed — service went
  inactive 09:19:48, and a create=201/delete=200 lock probe passed immediately before
  launch): **755 passed, 2 skipped in 952.90s (0:15:52), LIVE ARMS: ran 2; skipped 0,
  GATE PASS, exit code 0**. The gate's end-to-end smoke arm exercised the REAL fixed
  tests/smoke.sh against the live substrate (its final teardown printed
  `passed 21, failed 0`).

## 04-root-coupling.txt — the helper's ROOT follows auger's resolver

Probe (`python3 /tmp/probe_root_coupling.py`, script committed inline at the bottom
of the file): under a scratch substrate env (`DUCKBRAIN_DATA_DIR=<scratch>`,
`AUGER_NS_ROOT` and `DUCKBRAIN_NAMESPACES_PATH` unset) it prints

- `auger.namespaces_base()` → `<scratch>/namespaces`
- the helper's ROOT, computed by exec'ing the actual `ROOT = ` line extracted from
  `tests/smoke.sh` → the SAME path; `ROOT == namespaces_base(): True`
- an end-to-end teardown: a namespace directory created under THAT root is the one
  the helper's `drop()` removes on a scripted 200 DELETE (`exists after teardown:
  False`, helper stdout `namespace auger-smoke-r006-probe removed`).

Honest note: the probe's first run had a probe bug — it set `DUCKBRAIN_DATA_DIR`
only on the child env, so the in-process `namespaces_base()` read the ambient env
and the probe created its directory in the operator's real tree. That litter
(`~/duckbrain/namespaces/auger-smoke-r006-probe/`, empty fixture files only) was
removed before anything else touched it; the committed probe pins the env in the
probe process itself so the mistake is not reproducible. The recorded run is the
corrected one.

## The fix (tests/smoke.sh)

One line, plus its comment:

    ROOT = os.environ.get("AUGER_NS_ROOT") or auger.namespaces_base()

`AUGER_NS_ROOT` stays the hermetic override with first priority (all 8 pre-existing
tests set it and still pass unchanged). The fallback is now the product's own
resolver — called directly, no duplicated env precedence. Nothing else in the
script moved: the status-before-rmtree ordering, the bounded 409 ladder
(`TEARDOWN_CONFLICT_BACKOFFS` / `AUGER_CONFLICT_BACKOFFS`), the 429 one-retry
contract, the THROWAWAY prefix gate (incl. `auger-pytest-`) and the 24h sweep gate
are all untouched, and the existing tests that pin them pass unmodified.

## Gate arm naming (required work item 4)

`tests/gate.sh`'s pytest arm already names `tests/test_smoke_teardown.py` exactly
once (line 212; the only other occurrence is the comment at line 206). The new
tests live in the same file, so the arm needed no edit and no duplicate. The file's
own `test_gate_arm_runs_this_file` pins this.

## Row acceptance criteria — worker's self-check (the foreman closes the row)

1. 409 treated like 429, rmtree only after 200/404 — untouched from the landed fix
   and still pinned (`test_teardown_survives_a_persistent_409_...`,
   `test_teardown_settles_a_transient_409_...`). PASS.
2. Zero rows / zero directories after `bash tests/smoke.sh <ns>` under a sibling
   lock — not re-proven live by this worker (needs a live substrate run and a held
   sync lock; the foreman/judge owns that run). The code path that would leave a
   directory behind on a persistent conflict is the one this row's landed fix
   already refuses, and the vacuity gap in the pinning test is what this r2 change
   closed. NOT re-measured here — stated, not claimed.
3. The leftover class is `auger-pytest-*`, inside THROWAWAY; the 24h/prefix guards
   were not weakened (all five sweep tests pass unmodified). PASS by test.
4. No code path where rmtree runs before the status check — the only
   `shutil.rmtree` in the script is still behind `if st not in (200, 404): return`
   (tests/smoke.sh, `drop()`), pinned by the same tests as (1). PASS.
