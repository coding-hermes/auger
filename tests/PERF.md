# Auger test suite — wall-time and reproducibility analysis (QA-AUGER-13)

Measured on the fleet box (16 physical cores, 59 GiB RAM), DuckBrain HTTP API on
127.0.0.1:3000, clean venv at `/tmp/qa-venv` (Python 3.11.15, pytest 9.1.1).

## The two wall times being compared

| Run | Tree | Result | Wall |
|---|---|---|---|
| baseline (AUG-077 era) | pre-09-22 | green | **810s** |
| QA audit run | `68f3ca22` | 7 failed, 247 passed, **106 errors** | **1802.88s** |
| this analysis, clean venv, `DUCKBRAIN_API_KEY` exported | `f05f2d1` | 2 failed, 4 errors visible before the run was killed at 36% | ≥855s (partial) |

The delta to explain is `1802.88 − 810 = 992.88s`.

## Root cause: per-namespace 409 backoff during a full-fleet sync push

Every live test creates a throwaway namespace and deletes it in teardown.
`conftest.teardown_namespace` treats DuckBrain's `409 CONFLICT` as "a push is holding
the namespace sync lock" and retries on the ladder `TEARDOWN_CONFLICT_BACKOFFS =
(1, 2, 4, 8, 16, 30)` — **61s of patience per namespace**, by design (REVIEW-AUGER-005
cut this down from 600s).

61s is enough for DuckBrain's ordinary post-write push window. It is **not** enough for
a full-fleet native sync. Reproduced in this tick:

```
E   Failed: teardown of auger-pytest-52910a80-120445075c6a was incomplete:
    DELETE /api/namespaces/auger-pytest-52910a80-120445075c6a returned 409:
    {'error': "Push in flight for 'auger-pytest-52910a80-120445075c6a'
     (native sync lock held by pid 2385306 (since 2026-10-23T06:37:51.714Z)).
     Retry after it completes.", 'code': 'CONFLICT'};
    namespace auger-pytest-52910a80-120445075c6a is still listed by /api/namespaces
```

`pid 2385306` was `node bin/duckbrain.js s3 sync all push`, started by
`~/.hermes/scripts/duckbrain-s3-native-sync.sh` (its `S3_SYNC_ALL_DEADLINE_S` defaults
to **3600s**). While that job walks the fleet it takes each namespace's native sync lock
in turn; a test teardown that lands inside a namespace's window burns the full 61s ladder
and then reports honest residue evidence instead of a fake success.

### The arithmetic closes

```
992.88s of unexplained wall  /  61s per exhausted teardown ladder  ≈ 16 teardown waits
```

~16 live namespaces paying the full ladder accounts for the entire increase over the
810s baseline. Nothing else in the run got slower in proportion: the non-live majority
of the suite (`test_api_service.py` 6 tests → 1%, local DB tests in `test_auger.py`)
runs at roughly 10-40ms/test.

## So the missing `DUCKBRAIN_API_KEY` was NOT the dominant cause

The audit row attributed the mass `ERROR`s to a missing token. That is only half true:

- A **missing token** makes `live_check()` report UNREACHABLE and `require_live()`
  `pytest.skip()` — it produces **skips**, not 106 errors.
- The **106 errors and 7 failures** came from teardowns colliding with the sync lock,
  which happens whether or not the token is exported. In this tick the token *was*
  exported (`DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token)`) and the
  failures still reproduced, verbatim, on demand.
- Isolated spot-reruns pass because there is no full-fleet push running at that moment —
  the lock is free, teardown deletes on the first try, 19-25s per spot run.

Export the token anyway (see `tests/README.md`): it removes a second, independent class
of failure (SystemExit from `auger._token()` mid-run) and makes the run's live-arm
outcome depend on one read instead of a read-then-request pair racing a service restart.

## Recommendation (follow-up row QA-AUGER-14)

Do not let a correctness run silently spend 61s × N in backoff. Add a **preflight** at
session start:

1. `pgrep -af "duckbrain.js s3 sync all push"` (or probe a scratch namespace's DELETE
   for 409) before the first live test;
2. if a full-fleet push is in flight, either fail fast with a named reason
   (`AUGER_BLOCKED_BY: full-fleet sync push pid N`) or mark the live arms skipped —
   never burn the ladder;
3. keep the 61s ladder for the ordinary post-write window it was designed for.

That converts a 30-minute ambiguous red into a one-line named cause, and it makes the
gate's 2400s budget (RELEASE-017) meaningful: with the preflight, a green run is ~810s
plus jitter; without it, no budget is large enough on a box that also runs the sync.

## How to read a future red

| Symptom | Cause | Action |
|---|---|---|
| mass `ERROR` in live arms, 409 text with `Push in flight` | full-fleet sync push overlap | wait for the push, or use the QA-AUGER-14 preflight |
| live arms `SKIPPED`, `no DuckBrain token` | `DUCKBRAIN_API_KEY` unset and token file unreadable | export the token (README) |
| run dies at a fixed percentage with no summary | parent session killed (worker/harness), not a suite defect | re-run detached; check `pgrep -af pytest` |
| single test red in isolation | real defect | fix it |
