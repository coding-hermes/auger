# Auger tests — running the suite

Quickstart (clean venv, the shape every clean-venv verification leg should use):

    python3 -m venv /tmp/qa-venv
    /tmp/qa-venv/bin/pip install -e . -r tests/requirements-dev.txt
    DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token 2>/dev/null || echo "test-token") \
        setsid timeout --kill-after=60 2400s /tmp/qa-venv/bin/pytest tests/

`tests/requirements-dev.txt` does not install `pytest-timeout`, so `--timeout=` is an
unrecognised argument in the clean venv (verified: `pytest: error: unrecognized
arguments: --timeout=400`). Bound the run with `setsid timeout` exactly as
`tests/gate.sh` does, so the whole process group is killable.

## DUCKBRAIN_API_KEY is REQUIRED for the live-DuckBrain arms

Every test that touches DuckBrain chains into the session-scoped `live_service`
fixture (tests/conftest.py). That fixture probes the API before yielding, and a
missing token counts as UNREACHABLE (`live_check()`: `auger._token()` raises
SystemExit when it finds no token anywhere, `conftest.live_check` maps that to
"no DuckBrain token" and `require_live()` turns it into a skip). A missing token
therefore produces SKIPS, not errors.

The mass `ERROR`s an audit run sees are a DIFFERENT cause: a teardown `DELETE`
that lands while DuckBrain's native sync lock is held answers `409 CONFLICT`
("Push in flight ... native sync lock held by pid N") and the namespace stays
listed. The teardown retries on a 61s ladder, which covers an ordinary
post-write push but not a full-fleet `s3 sync all push` (deadline 3600s). See
`tests/PERF.md` for the measured proof: with the token exported the 409 failures
still reproduce, and the 992s of extra wall time is that ladder multiplied by
the live namespaces.

Do not run a correctness suite while a full-fleet sync push is in flight:

    pgrep -af "duckbrain.js s3 sync all push"    # must print nothing

Export the token anyway — it removes a second, independent failure class (a
SystemExit from `auger._token()` discovered mid-run, after some live arms have
already started):

    export DUCKBRAIN_API_KEY=$(cat ~/.duckbrain/foreman-status.token)

or from a running DuckBrain HTTP API: the token DuckBrain issued is stored at
`~/.duckbrain/foreman-status.token` (readable from the box, no service call
needed) — `auger.py` reads the same file when `DUCKBRAIN_API_KEY` is unset, so
why export it at all? Because the clean-venv `pip install -e .` and the pytest
process inherit a shell environment that a file-read at every `auger._token()`
call site cannot fix: if the token file is unreadable by the venv's user, or is
rotated between the file-read and the request, the read-then-request pair inside
one call is not atomic with respect to a later `duckbrain-http.service` restart.
Exporting the var removes the whole class: one read, once, before the suite
starts; every live arm inherits the identical token.

The token file must contain the key DuckBrain's HTTP API accepts (`x-api-key`
on 127.0.0.1:3000). If the file is missing, `auger._token()` raises SystemExit
("no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token").

## Live-service concurrency: the substrate lock

QA-AUGER-6's fix (QA-AUGER-6 rework, commit 0ddeeeb + 1970102) serializes live
substrate access ACROSS pytest processes with an `fcntl.flock`
(`/tmp/auger-pytest-substrate.lock`, overridable via `AUGER_TEST_LOCK`). Do not
delete the lock file while a run is in flight — a sibling leg holding it would
lose the serialization contract and the legs would interleave on DuckBrain. The
session-scoped `live_service` fixture holds the lock from the first live test's
setup to session finalization.

The lock is inherited by a nested pytest suite (`AUGER_TEST_LOCK_INHERITED=1`,
set by the spawner, not by hand) under the ancestor's hold. Never set that env
var by hand.

## Gate budget

`tests/gate.sh` runs the live pytest arm with `--timeout=2400s` (RELEASE-017:
2x the measured ~1170s wall, leaving headroom for load-induced stalls). The wall
time is NOT the suite's own cost — see `tests/PERF.md`: a clean-venv run that
overlaps a full-fleet `s3 sync all push` pays the 61s teardown-conflict ladder
per live namespace, which is what turned an 810s baseline into 1802s. If your run
exceeds the 2400s budget, check for that push before suspecting the tests:
343 collected tests do not take 40 minutes on their own.
