"""
Shared fixtures for the pytest suite that replaces shell-only coverage of `auger`.

Three rules shape this file, all of them cost-places in the shell suite it replaces:

  * ONE EPHEMERAL NAMESPACE PER TEST SESSION, torn down in a fixture finalizer so a failing
    assertion still removes it. `tests/smoke.sh` deliberately leaves its namespace
    behind (it prints a `rm -rf` line and asks the reader to run it); a test suite that
    leaks a namespace per case would be worse, so teardown happens here, in a `finally`.
    The namespace name carries a SESSION-UNIQUE tag (REVIEW-AUGER-005): a stale audit
    (the 2026-10-18 pluggy run, docs/dependency_pluggy_2026-10-18.md) read
    `auger-pytest-*` rows that were a CONCURRENT session's live namespaces and called
    them this run's leaks — the tag makes every session's names disjoint, so the
    prefix scan can never report another run's in-flight namespace.
  * TEARDOWN GOES DOWN BOTH PATHS — `DELETE /api/namespaces/<ns>` (the registry mapping
    plus the directory) and `shutil.rmtree` of `~/duckbrain/namespaces/<ns>` — but the
    DELETE GOES FIRST AND DECIDES (AUG-117): the directory is removed only when the DELETE
    settled (2xx, or 404 = already gone). A REFUSED DELETE (409 push in flight, 429 rate
    limit, 5xx) leaves the registry mapping standing, so the directory stays standing with
    it; removing the directory under a surviving row is what made 15,268 of the registry's
    15,932 rows ghosts. Whatever a path could not fix is RETURNED to the caller and raised
    by the fixture, never swallowed: a namespace that could not be removed is an error,
    not a footnote.
  * A 409 "Push in flight" has TWO meanings, and AUG-197 makes the body's own
    discriminator decisive (prior art: off-by-one classes 2944/2827). When the
    parenthetical names a FOREIGN holder pid ("native sync lock held by pid N"),
    the holder is an external job (a native sync push, deadline 3600s) that will
    outlive any short retry: teardown fails FAST with an attributed
    environment-contention result (prefix `ENV-CONTENTION`) instead of burning
    the 61s ladder, keeps the row+directory consistent (AUG-117), does NOT fail
    the test, and leaves the namespace to the NEXT RUN's reaper
    (`reap_prior_session_namespaces`, runs inside the substrate lock at fixture
    setup). Without a named holder pid the 409 is the ordinary post-write push
    window and keeps the short ladder.
  * THE LIVE DEPENDENCY IS EXPLICIT. Every test that needs DuckBrain depends on the
    `live_service` fixture, which probes the API and either skips naming `DUCKBRAIN_URL`
    and the remedy, or (under `AUGER_REQUIRE_LIVE=1`) fails hard. There is no path where
    an unreachable service produces a silent green.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # The repo has no packaging/pytest config, so the standard `tests/conftest.py`
    # idiom puts the root on sys.path instead of requiring `pip install -e .`.
    sys.path.insert(0, str(REPO_ROOT))

# AUG-101/QA-AUGER-13 class, proven again 2026-11-16: an ambient DUCKBRAIN_URL that is
# the DEFAULT substrate spelled differently (localhost vs 127.0.0.1, trailing slash)
# made auger.DB_URL import as an "isolated substrate" and `_init_ns_dir` refuse every
# fixture init with SystemExit — the whole suite errored at setup whenever the guard's
# live gate set DUCKBRAIN_URL. A URL-equivalent spelling of the default IS the default;
# normalize it BEFORE auger is imported (auger reads DB_URL at import time).
_ambient_url = os.environ.get("DUCKBRAIN_URL", "").strip().rstrip("/")
if _ambient_url in ("http://localhost:3000", "http://127.0.0.1:3000"):
    os.environ["DUCKBRAIN_URL"] = "http://127.0.0.1:3000"

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

TEST_NS_TAG = uuid.uuid4().hex[:8]
#: REVIEW-AUGER-005: the tag makes one session's names disjoint from every other
#: session's (concurrent gate legs, a sibling worktree's suite, a gitreins judge
#: re-run). A namespace is identified by the FULL prefix, never by the literal name.
TEST_NS_GLOBAL_PREFIX = "auger-pytest-"
TEST_NS_PREFIX = f"{TEST_NS_GLOBAL_PREFIX}{TEST_NS_TAG}-"

#: Every namespace this session created. The leak audit reads it.
CREATED: list[str] = []

REMEDY = (
    "start DuckBrain on 127.0.0.1:3000 and put a token in "
    "~/.duckbrain/foreman-status.token (or set DUCKBRAIN_API_KEY)"
)


# ---------------------------------------------------------------- CLI harness
def run_cli(argv: list[str]) -> tuple[int, str]:
    """Invoke the CLI in-process and capture its stdout. SystemExit is NOT caught here.

    ONE exception (AUG-209): `auger.SubstrateLockContended` — a 503 SERIALIZER_LOCKED
    on a write, i.e. a competing writer process holding the namespace's lock — is
    environment contention, not this suite's defect, so it converts into a TYPED
    SKIP that names the competing writer/namespace. The skip fires wherever the
    contention lands (fixture setup or test body), which is why one lock event
    costs skipped tests instead of ERRORing every fixture-dependent case at
    setup. Every other SystemExit propagates: the loud path is untouched.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = auger.main(argv)
        except auger.SubstrateLockContended as exc:
            pytest.skip(
                f"substrate write contention (503 SERIALIZER_LOCKED, AUG-209): {exc}"
            )
    return rc, buf.getvalue()


def run_cli_exit(argv: list[str]) -> tuple[int, str, str]:
    """Run a verb that is expected to REFUSE; returns (exit_code, message, stdout).

    AUG-211: `SubstrateLockContended` IS a SystemExit subclass, so the
    `pytest.raises(SystemExit)` below catches a 503 SERIALIZER_LOCKED landing and
    this harness used to report it as a PASSED verb refusal — a lock storm read as
    green refusals. Same rule as `run_cli` (AUG-209): the contention is the
    substrate's answer, not this suite's defect, so it converts into the SAME
    attributed skip naming the lock and the namespace. Every other SystemExit
    keeps its loud refusal shape.
    """
    buf = io.StringIO()
    with pytest.raises(SystemExit) as ei, contextlib.redirect_stdout(buf):
        auger.main(argv)
    exc = ei.value
    if isinstance(exc, auger.SubstrateLockContended):
        pytest.skip(
            f"substrate write contention (503 SERIALIZER_LOCKED, AUG-209): {exc}"
        )
    code = exc.code if isinstance(exc.code, int) else 1
    return code, str(exc), buf.getvalue()


# ---------------------------------------------------------------- liveness
def live_check(url: str, timeout: int = 15) -> tuple[bool, str]:
    """Probe the API's namespace list. Returns (reachable, one-line detail).

    A missing token counts as UNREACHABLE: the verbs cannot run without it, so the
    suite must not call that situation green.
    """
    try:
        status, body, _ = auger.db("/api/namespaces", timeout=timeout)
    except SystemExit as exc:  # auger._token() raises SystemExit when no token exists
        return False, f"no DuckBrain token ({exc})"
    except Exception as exc:  # noqa: BLE001 - a transport surprise must be reported, not hidden
        return False, f"{type(exc).__name__}: {exc}"
    if status != 200:
        return False, f"GET /api/namespaces returned {status}: {str(body)[:200]}"
    return True, "ok"


def require_live(url: str | None = None) -> str:
    """Return the live URL, or skip loudly / fail hard. Never returns when unreachable."""
    url = url or auger.DB_URL
    ok, detail = live_check(url)
    if ok:
        return url
    msg = f"live DuckBrain is required at {url} (env DUCKBRAIN_URL): {detail}. Remedy: {REMEDY}."
    if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
        raise AssertionError(f"{msg} (AUGER_REQUIRE_LIVE=1 forbids skipping)")
    pytest.skip(msg)


def api_namespaces() -> list[str]:
    """The namespace registry as the API reports it."""
    status, body, _ = auger.db("/api/namespaces")
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"could not list namespaces ({status}): {body}")
    return [n.get("name") for n in body.get("namespaces", [])]


def ns_path(ns: str) -> str:
    return auger.ns_dir(ns)


# ---------------------------------------------------------------- the just-deleted lag (AUG-080)
#: A registry listing can LAG a DELETE that already landed. Observed 2026-09-25 (gate.sh,
#: 197/198): the audit reported `api: ['auger-pytest-8ff530dda03b']` while `disk` was EMPTY
#: for the same namespace — deleted, rmtree'd, and already confirmed gone by the
#: per-namespace re-check inside `teardown_namespace` (no fixture error was raised), yet
#: still listed seconds later; in isolation the same audit passed clean in 0.29s. That is
#: an eventual-consistency window of a few seconds, not a leak. The reconciliation lives
#: HERE, once per audit read, rather than a wait in every teardown finalizer: the window
#: is rare, and ~200 finalizers would each pay for one stale read.
LAG_RECHECKS = 3
#: A few seconds TOTAL, never minutes — the gate has a time budget, and a lag measured in
#: minutes belongs to teardown's 409 budget, not to an audit that must stay honest.
LAG_RECHECK_BACKOFF_S = 2.0


def _registry_lag_recheck(ns: str) -> bool:
    """True once `ns` is confirmed no longer listed; False if it persists past the budget.

    A re-LIST, not a per-namespace GET: DuckBrain serves no single-namespace read, and the
    list endpoint is the same surface the stale read came from. Bounded to
    LAG_RECHECKS * LAG_RECHECK_BACKOFF_S (~6s), so the audit cannot hang the gate — and a
    row that survives the whole window is reported as what it is: a leak.
    """
    for attempt in range(LAG_RECHECKS):
        if attempt:
            time.sleep(LAG_RECHECK_BACKOFF_S)
        try:
            if ns not in api_namespaces():
                return True
        except SystemExit as exc:
            # A listing that ERRORS (no token, non-200 after the transport's retries) is
            # the audit's pre-existing loud failure — never a confirmation of absence.
            raise SystemExit(f"could not re-check {ns} over the API: {exc}") from exc
    return False


def leftovers(scope: list[str] | None = None) -> dict[str, list[str]]:
    """Every leftover test namespace, split by the two places one can linger.

    `scope` restricts the audit to a known set of names, and the gate relies on
    that: the suite can run CONCURRENTLY with another process running the same
    suite in the same workdir (a gitreins judge re-runs it — proven 2026-09-20,
    two async judges plus this suite at once), and an unscoped prefix scan then
    reports the other run's live, in-flight namespace as a leak of THIS run. That
    race blocked a correct board commit. The property this suite must prove is
    that IT cleaned up, which is exactly and only the names it created.

    A name still listed gets ONE bounded reconciliation (AUG-080): the registry
    can lag a delete that already landed, so a listed name is re-checked over a
    few seconds before it counts as a leak. A row that persists past that window
    is still reported — this tolerance never makes a real leak pass.
    """
    api = [n for n in api_namespaces() if n.startswith(TEST_NS_PREFIX)]
    root = Path(os.path.expanduser("~")) / "duckbrain" / "namespaces"
    disk = (
        sorted(p.name for p in root.iterdir() if p.name.startswith(TEST_NS_PREFIX))
        if root.is_dir()
        else []
    )
    if scope is not None:
        want = set(scope)
        api = [n for n in api if n in want]
        disk = [n for n in disk if n in want]
    api = [n for n in api if n in disk or not _registry_lag_recheck(n)]
    return {"api": api, "disk": disk}


# ---------------------------------------------------------------- teardown
#: DuckBrain's own native push can briefly hold the namespace sync lock after a write.
#: Retry that conflict for 61s total — enough for the ordinary push window, one tenth
#: of the old per-namespace 600s stall. Persistent residue remains a loud failure.
TEARDOWN_CONFLICT_BACKOFFS = (1, 2, 4, 8, 16, 30)

#: AUG-197: the 409 body answers which kind of conflict it is (AC2, prior art
#: off-by-one classes 2944/2827 — both verified live 2026-10-18/10-23):
#:
#:   ``Push in flight for '<ns>'. Retry after it completes.``            -> transient
#:   ``Push in flight for '<ns>' (native sync lock held by pid <N> (since ...
#:      )). Retry after it completes.``                                  -> foreign lock
#:
#: A FOREIGN holder is an external job (a native sync push, deadline 3600s) that will
#: outlive any short retry — retrying only burns the ladder. So the body's pid token
#: is the discriminator: transient keeps the ladder, foreign lock fails fast with an
#: ATTRIBUTED result naming the pid, never a retry-until-burn.
SYNC_LOCK_PID_RE = re.compile(r"held by pid\s+(\d+)", re.IGNORECASE)

#: Prefix of the attributed environment-contention result a teardown appends when a
#: foreign lock refused the DELETE. The fixture finalizer tests for THIS prefix: a
#: contention-only teardown is not a test failure (it must not red the suite) — it
#: leaves the row+directory consistent and the NEXT RUN's reaper finishes the job.
ENV_CONTENTION_PREFIX = "ENV-CONTENTION:"

#: Namespace rows this session left under a refused DELETE are reaped here on the
#: next run — see `reap_prior_session_namespaces`. Set AUGER_REAP_SKIP=1 to keep the
#: reaper off for a run that must not touch anything but its own rows.
REAP_SKIP_ENV = "AUGER_REAP_SKIP"


def classify_push_conflict(body: object) -> tuple[str, int | None]:
    """Classify a 409 body: ("foreign-lock", pid) | ("transient", None) | ("other", None).

    The body already carries the discriminator, so the classifier only reads text:
    a "held by pid <N>" parenthetical names an external lock holder (fail fast,
    attribute the pid); the bare "Push in flight" sentence is the ordinary seconds
    window a short ladder covers. Anything else on a 409 is left "other" and keeps
    the existing ladder/reporting behavior unchanged.
    """
    text = str(body)
    match = SYNC_LOCK_PID_RE.search(text)
    if match:
        return "foreign-lock", int(match.group(1))
    if re.search(r"push in flight", text, re.IGNORECASE):
        return "transient", None
    return "other", None


def teardown_contention_only(problems: list[str]) -> bool:
    """True when EVERY problem is an attributed environment-contention result.

    Contention is the substrate's answer, not this suite's bug: it must not fail
    the test (AC2), so the finalizer reports it loudly and lets it pass, leaving
    the namespace consistent for the next run's reaper.
    """
    return bool(problems) and all(p.startswith(ENV_CONTENTION_PREFIX) for p in problems)


def contention_pid(problems: list[str]) -> int | None:
    """The foreign holder pid of a contention-only teardown result, else None.

    Callers that cannot treat a contended teardown as success (a test asserting a
    teardown postcondition, a fixture whose residue must stay reachable) use this
    to SKIP loudly, naming the pid — the attributed outcome AC2 asks for.
    """
    if not teardown_contention_only(problems):
        return None
    for problem in problems:
        match = SYNC_LOCK_PID_RE.search(problem)
        if match:
            return int(match.group(1))
    return None


#: Waits between attempts to remove the DIRECTORY of a namespace whose DELETE already settled
#: (AUG-131). A concurrent DuckBrain indexer can recreate `<ns>/.embeddings/...` after the
#: delete, and the API's own rmtree then answers ENOTEMPTY. The window is sub-second, so the
#: budget is short by design: sum(REMOVAL_BACKOFFS) ≈ 1.9s per namespace in the worst case.
REMOVAL_BACKOFFS = (0.0, 0.1, 0.25, 0.5, 1.0)


def teardown_namespace(ns: str) -> list[str]:
    """Remove a test namespace over BOTH paths. Returns the problems it could not fix.

    Refuses anything without the test prefix, so a bug here can never rmtree a real
    namespace. 404 counts as success: the desired end state is "gone". A 409 gets
    a short bounded retry budget; if it persists, the failure and registry evidence
    are reported rather than hidden or retried for ten minutes (REVIEW-AUGER-005).

    THE DELETE DECIDES WHETHER THE DIRECTORY IS REMOVED (AUG-117). The two paths are not
    independent: the registry mapping and the directory are two halves of ONE namespace, so
    it is BOTH or NEITHER. Removing `~/duckbrain/namespaces/<ns>` after a refused DELETE
    (409 Push in flight, 429 rate limit, 5xx) leaves a registry row that points at nothing —
    a namespace that is listed but cannot exist — and that is not a leak the next run can
    clean up, because nothing distinguishes it from a namespace whose data was lost. Measured
    2026-10-27: 15,268 such ghost rows out of 15,932 (98.4% of the listing was test garbage).
    So a refused DELETE keeps the directory (stated loudly on stderr, and returned as a
    problem so the fixture still fails the test) and a settled DELETE removes it.

    The deletion goes through `auger.delete_namespace`, never a hand-rolled request: that
    call is the one the 429 retry exists for, so the retry budget, the Retry-After handling
    and the "no bypass" property are all one implementation, proved in `test_auger.py`.
    """
    if not ns.startswith(TEST_NS_PREFIX):
        raise ValueError(
            f"refusing to tear down a namespace that is not a test namespace: {ns!r}"
        )
    problems: list[str] = []

    # 409 = DuckBrain's sync lock is held by another writer. AUG-197 (AC2): the body
    # says WHICH writer. A "held by pid <N>" parenthetical is a FOREIGN process (a
    # native sync push, deadline 3600s) that will outlive any short retry, so the
    # ladder is NOT run at all — the DELETE is reported as an attributed
    # environment-contention result naming the pid instead (a retry-until-burn is
    # exactly the 992s wall-growth shape prior art 2944 measured). Without a pid
    # token the 409 is the ordinary post-write push window and keeps the ladder.
    # The session-wide flock below prevents sibling pytest legs from causing that
    # contention. A conflict that survives all six waits is still not reinterpreted
    # as success: teardown still LISTs the registry so the caller gets honest
    # residue evidence. Worst-case conflict wait: 61s, not 600s.
    status, body = auger.delete_namespace(ns)
    foreign_pid: int | None = None
    if status == 409:
        _kind, foreign_pid = classify_push_conflict(body)
    for backoff in TEARDOWN_CONFLICT_BACKOFFS:
        if status != 409 or foreign_pid is not None:
            break
        time.sleep(backoff)
        status, body = auger.delete_namespace(ns)

    # 2xx = the mapping is gone (200/202/204 ...); 404 = it was already gone. Anything else
    # means the registry row SURVIVED, and the directory must survive with it.
    deleted = 200 <= status < 300 or status == 404
    if not deleted:
        if status == 409 and foreign_pid is not None:
            problems.append(
                f"{ENV_CONTENTION_PREFIX} DELETE /api/namespaces/{ns} returned 409; "
                f"foreign sync lock held by pid {foreign_pid} (an external job that "
                "outlives any retry) — not retried, namespace left row+directory "
                "consistent (AUG-117) for the next run's reaper"
            )
            print(
                f"auger: teardown of {ns} deferred to a foreign sync push "
                f"(pid {foreign_pid}); reported as environment contention, "
                "reaped on a later run",
                file=sys.stderr,
            )
        else:
            problems.append(
                f"DELETE /api/namespaces/{ns} returned {status}: {str(body)[:200]}"
            )

    path = ns_path(ns)
    if deleted:
        # A SETTLED DELETE CAN STILL BE UNDONE BY A CONCURRENT INDEXER (AUG-131). DuckBrain
        # writes embeddings into `<ns>/.embeddings/<model>/` on its own thread; if that write
        # lands after the delete it recreates the directory, and rmtree racing it returns
        # ENOTEMPTY from the API (observed 500 "Failed to remove namespace directory:
        # ENOTEMPTY", twice in a row on 2026-11-03). The window is short but real, so removal
        # gets a bounded retry budget: remove, re-check, re-issue the DELETE when the tree came
        # back (its registry row may have come back with it), and only report what survives.
        for wait in REMOVAL_BACKOFFS:
            if wait:
                time.sleep(wait)
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            status, _body = auger.delete_namespace(ns)
            if status == 404:
                # the row is gone and only the tree came back: another rmtree is enough
                continue
        if os.path.exists(path):
            problems.append(
                f"directory still present after {len(REMOVAL_BACKOFFS)} removal attempts: {path}"
            )
    else:
        # Registry mapping survived -> directory kept: a half-torn namespace whose row and
        # directory still AGREE is repairable (a later DELETE, or an operator's cleanup,
        # finishes it). Stated on stderr because a silent skip would read as success. Under
        # a contended 409 the attributed block above already said all of this loudly.
        if not problems or not problems[-1].startswith(ENV_CONTENTION_PREFIX):
            print(
                f"auger: teardown of {ns} INCOMPLETE — DELETE returned {status}; "
                f"directory KEPT at {path} so the registry mapping and the directory "
                "stay consistent",
                file=sys.stderr,
            )
        if not os.path.exists(path):
            problems.append(
                f"directory is already gone while the registry row survived: {path}"
            )

    # The registry listing check is the sweep's last word for a normal teardown. A
    # contended namespace is EXPECTED to stay listed until the reaper runs (the row
    # and the directory agree; running this check there would append a second,
    # unattributed problem for the same substrate event), so it is skipped only in
    # that case.
    if status == 409 and foreign_pid is not None:
        return problems
    try:
        if ns in api_namespaces():
            problems.append(f"namespace {ns} is still listed by /api/namespaces")
    except SystemExit as exc:
        problems.append(f"could not confirm removal over the API: {exc}")
    return problems


# ---------------------------------------------------------------- the reaper (AUG-197)
def reap_prior_session_namespaces(
    ns_list: list[str] | None = None,
    delete=None,
) -> dict[str, list[str]]:
    """Delete `auger-pytest-*` registry rows left by PRIOR sessions (AC2/AC4).

    Called from the `live_service` fixture BEFORE the first live test, inside the
    cross-process substrate lock, so the sweep cannot race a sibling leg (a live
    leg of this suite on this host does not exist while we hold the flock — the
    lock file is shared). The current session's own prefix is always spared; so
    is anything outside the test prefix, unconditionally.

    A namespace whose DELETE answers 409 with a foreign lock-holder pid is NOT
    retried and NOT forced: it is reported attributed (naming the pid) and is
    left for a later run — the same fail-fast rule teardown follows.

    Returns {"reaped": [names], "contended": ["name (reason)", ...]} so the
    caller prints an honest census instead of a silent mutation. Injectable
    parameters exist for the hermetic tests; production callers pass nothing.
    """
    global_names = ns_list if ns_list is not None else api_namespaces()
    candidates = sorted(
        n
        for n in global_names
        if isinstance(n, str)
        and n.startswith(TEST_NS_GLOBAL_PREFIX)
        and not n.startswith(TEST_NS_PREFIX)
    )
    delete_one = delete if delete is not None else auger.delete_namespace
    reaped: list[str] = []
    contended: list[str] = []
    for ns in candidates:
        status, body = delete_one(ns)
        if 200 <= status < 300 or status == 404:
            reaped.append(ns)
            # The DELETE is authoritative for the directory too (AUG-117); only a
            # leftover tree the server did not get needs a local removal, and its
            # refusal just leaves a ghost the census will report again — never an
            # error that would poison the live session.
            try:
                shutil.rmtree(ns_path(ns), ignore_errors=True)  # type: ignore[arg-type]
            except OSError:
                pass
            continue
        if status == 409:
            kind, pid = classify_push_conflict(body)
            if kind == "foreign-lock":
                contended.append(f"{ns} (foreign sync lock held by pid {pid})")
                continue
            contended.append(f"{ns} (409 transient push in flight; not forced)")
            continue
        contended.append(f"{ns} (DELETE returned {status}: {str(body)[:120]})")
    return {"reaped": reaped, "contended": contended}


def reap_prior_session_namespace_directories() -> list[str]:
    """Remove ghost directories under the substrate tree: a test-prefixed tree with
    no registry row (the AUG-117 ghost shape, 15,268 of them measured 2026-10-27).
    Report-only names; removal is ignore_errors and best-effort, exactly like the
    smoke harness's hygiene sweep. Never touches the current session's prefix."""
    root = Path(auger.namespaces_base())
    if not root.is_dir():
        return []
    removed: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.name.startswith(TEST_NS_GLOBAL_PREFIX) or entry.name.startswith(
            TEST_NS_PREFIX
        ):
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry.name)
    return removed


def write_reap_report(reap: dict[str, list[str]], reporter=None) -> None:
    """Print the reaper census so a mutation of shared substrate state is loud.

    `reporter` is the session's terminal pytest reporter (the fixture resolves it
    via `request.config.pluginmanager.getplugin`): it writes straight to the
    terminal, so even a `-q` run sees the census — a silent delete is exactly the
    failure shape AUG-197 exists to end. The same lines are also printed to
    stderr, so any captured log carries them too.
    """
    lines: list[str] = []
    if reap.get("reaped"):
        lines.append(
            f"auger: reaped {len(reap['reaped'])} prior-session test namespace(s): "
            + ", ".join(reap["reaped"])
        )
    if reap.get("contended"):
        lines.append(
            f"auger: reaper deferred {len(reap['contended'])} prior-session test "
            "namespace(s): " + "; ".join(reap["contended"])
        )
    if reap.get("ghosts_reaped"):
        lines.append(
            f"auger: reaped {len(reap['ghosts_reaped'])} ghost test-directory trees "
            "(no registry row): " + ", ".join(reap["ghosts_reaped"])
        )
    for line in lines:
        print(line, file=sys.stderr)
        if reporter is not None and hasattr(reporter, "write_line"):
            reporter.write_line(line)


# ------------------------------------------------- the substrate lock (QA-AUGER-6)
#: The full suite ran TWICE CONCURRENTLY on one host (a gate.sh pytest arm plus a sibling
#: leg, loadavg 18-33) and both legs failed the SAME three live-service tests with
#: `transport: timed out` (status 0): the 45s `auger.db` budget is mostly WAITING while
#: ~240 sibling tests hammer the same live DuckBrain, and it is a wall-clock budget, so
#: under fleet load a perfectly healthy substrate can miss it. Deterministic under
#: concurrency, green solo. Two-layer fix, both here and only here:
#:
#: 1. CROSS-PROCESS — `substrate_lock()` takes an EXCLUSIVE `fcntl.flock` on
#:    AUGER_TEST_LOCK (default under /tmp, overridable) for the fixture's whole
#:    lifetime, so two concurrent pytest LEGS (separate processes) serialize and
#:    each leg's namespace create/teardown and test bodies never interleave on the
#:    live substrate. Blocking flock: there is no timeout kill — a leg that cannot
#:    take the lock waits for its sibling to finish. The price of this design is
#:    documented, not hidden: the wait is unbounded, so a leg holding the lock
#:    while HUNG blocks its sibling until the sibling's own per-call budgets
#:    (`auger._req`'s timeout) fire; a process never deadlocks ITSELF (the
#:    re-entrancy rule below).
#: 2. INTRA-LEG headroom — the fixture widens `auger.db`'s default timeout to
#:    `SUBSTRATE_TEST_TIMEOUT_S` for every call made while a live test runs
#:    (restored by the fixture's own teardown ordering, offline tests unaffected).
SUBSTRATE_TEST_TIMEOUT_S = 120
_DEFAULT_LOCK_PATH = "/tmp/auger-pytest-substrate.lock"


def _substrate_lock_path() -> str:
    env = os.environ.get("AUGER_TEST_LOCK", "").strip()
    return env if env else _DEFAULT_LOCK_PATH


#: Module-level re-entrancy state: pytest fixtures won't nest `substrate_lock`, but a leg
#: re-acquiring it in one process must never self-block. Because `fcntl.flock()` locks ride
#: on the open file DESCRIPTION, the fd must be the SAME one while held (a second open()
#: creates a second description whose flock would self-deadlock on Linux). The counter is
#: re-entrancy depth in this process; it is reset defensively in the exception path.
_SUBSTRATE_LOCK_FD: int | None = None
_SUBSTRATE_LOCK_DEPTH = 0

#: The nested-suite inheritance contract (QA-AUGER-6 rework): `test_a_broken_verb_...`
#: spawns a CHILD pytest whose copied conftest.py also takes `substrate_lock`, and the
#: child's 4 tests all chain into `live_service`. Under the session-scoped hold that
#: child would flock against its OWN PARENT's lock — parent waits on the child (a
#: synchronous `subprocess.run`), child waits on the parent's session end: a
#: self-deadlock that stalled three guard runs at exactly the nested-suite test
#: (~170/284). The spawner therefore sets AUGER_TEST_LOCK_INHERITED=1 for the child,
#: and every acquisition in that process becomes a depth count with NO flock: the
#: nested suite runs inside the ancestor's hold, which is exactly what it is — the
#: parent cannot touch the substrate while it waits — while sibling legs still
#: serialize against the one real flock. A process without the flag is unaffected.
SUBSTRATE_LOCK_INHERITED_ENV = "AUGER_TEST_LOCK_INHERITED"

# A full-fleet native sync holds every namespace's native lock for much longer than the
# ordinary post-write window. Detect it before any live fixture takes the flock, so a live
# arm never spends 61 seconds per namespace discovering the same known blocker.
#: AUG-197: matched as a REGEX because the runner was renamed to node --import tsx
#: bin/duckbrain.ts (measured 2026-11-22: a LIVE `bin/duckbrain.ts s3 sync all push`
#: held every namespace lock while the old literal "duckbrain.js ..." marker matched
#: nothing, so the preflight flew and every live leg hit the 409s it exists to
#: prevent). Both spellings must keep matching.
FULL_FLEET_SYNC_MARKER = r"duckbrain\.(?:js|ts) s3 sync all push"
AUGER_BLOCKED_BY_PREFIX = "AUGER_BLOCKED_BY:"
SUBSTRATE_PREFLIGHT_BLOCKED: str | None = None


def _process_command_lines() -> list[tuple[int, str]]:
    """Return (pid, command) for visible processes, with unreadable entries ignored."""
    commands: list[tuple[int, str]] = []
    proc_root = Path("/proc")
    try:
        entries = proc_root.iterdir()
    except OSError:
        return commands
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            command = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode(errors="replace")
            )
        except OSError:
            continue
        commands.append((int(entry.name), command))
    return commands


def _full_fleet_sync_holders(
    process_commands: list[tuple[int, str]] | None = None,
) -> list[tuple[int, str]]:
    """Find full-fleet sync processes without shelling out to a process-list command."""
    commands = (
        _process_command_lines() if process_commands is None else process_commands
    )
    marker = re.compile(FULL_FLEET_SYNC_MARKER)
    return [
        (pid, command)
        for pid, command in commands
        if marker.search(command) and pid != os.getpid()
    ]


def _probe_full_fleet_sync_push() -> tuple[int, object] | None:
    """Probe the configured substrate for the named 409 Push-in-flight response.

    The probe is injectable for unit tests and uses the same DELETE transport shape as
    teardown, but a scratch namespace is never created: a server that exposes the
    documented conflict response identifies the substrate-wide blocker directly.
    """
    try:
        status, body, _ = auger.db("/api/namespaces/auger-preflight", "DELETE")
    except BaseException:  # noqa: BLE001 - preflight must not mask ordinary liveness errors
        return None
    if status != 409:
        return None
    text = str(body)
    if re.search(r"push in flight", text, re.IGNORECASE):
        return status, body
    return None


def _foreign_substrate_lock_holder() -> int | None:
    """Return a PID holding AUGER_TEST_LOCK, or None when the lock is immediately free."""
    if _substrate_lock_is_inherited():
        return None
    path = _substrate_lock_path()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # flock does not expose the holder PID. Match the lock file's device/inode
            # against /proc/<pid>/fd so the diagnostic names the actual foreign holder.
            identity = os.fstat(fd)
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit() or int(entry.name) == os.getpid():
                    continue
                fd_dir = entry / "fd"
                try:
                    for held_fd in fd_dir.iterdir():
                        try:
                            held = os.stat(held_fd)
                        except OSError:
                            continue
                        if (held.st_dev, held.st_ino) == (
                            identity.st_dev,
                            identity.st_ino,
                        ):
                            return int(entry.name)
                except OSError:
                    continue
            return None
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
    finally:
        os.close(fd)


def substrate_preflight(
    process_commands: list[tuple[int, str]] | None = None,
    push_probe=None,
    lock_holder=None,
) -> str | None:
    """Return one named substrate blocker, or None when live arms may start."""
    holders = _full_fleet_sync_holders(process_commands)
    if holders:
        return f"{AUGER_BLOCKED_BY_PREFIX} full-fleet sync push pid {holders[0][0]}"
    probe = (push_probe or _probe_full_fleet_sync_push)()
    if probe is not None:
        _status, body = probe
        match = re.search(r"pid\s+(\d+)", str(body), re.IGNORECASE)
        pid = f" pid {match.group(1)}" if match else " pid unknown"
        return f"{AUGER_BLOCKED_BY_PREFIX} full-fleet sync push{pid} (probe returned 409 Push in flight)"
    holder = (lock_holder or _foreign_substrate_lock_holder)()
    if holder is not None:
        return f"{AUGER_BLOCKED_BY_PREFIX} foreign AUGER_TEST_LOCK holder pid {holder}"
    return None


# ------------------------------------------- gate progress attribution (AUG-197 AC1)
#: The gate's pytest arm is bounded by a process timeout; when that fires there is no
#: pytest summary to read the hung test from (the timeout used to grade
#: `first_failing_test: none detected`). When tests/gate.sh exports
#: AUGER_GATE_PROGRESS_FILE, every test's start and every test report is APPENDED to
#: that file as one JSON line, so the gate can name the test in flight at the moment
#: of the kill (`scripts/gate_timeout_report.py` reads it). Writing is OFF unless the
#: variable is set, so an ordinary suite run pays nothing.
GATE_PROGRESS_ENV = "AUGER_GATE_PROGRESS_FILE"


def _gate_progress_write(record: dict) -> None:
    path = os.environ.get(GATE_PROGRESS_ENV, "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**record, "t": round(time.time(), 3)}) + "\n")
    except OSError:
        return  # a progress record must never break, slow, or retry a test


def pytest_runtest_logstart(nodeid, location):
    """Record that `nodeid` is STARTING (the in-flight test the gate attributes a
    kill to — the last `start` line without a matching teardown report)."""
    _gate_progress_write({"event": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report):
    """Record each phase report so the gate can separate "hung here" from
    "failed earlier" in the attribution report."""
    _gate_progress_write(
        {
            "event": "report",
            "when": getattr(report, "when", "?"),
            "outcome": getattr(report, "outcome", "?"),
            "nodeid": report.nodeid,
        }
    )


def pytest_sessionstart(session):
    """Fail or skip live arms before they can enter a substrate conflict backoff."""
    global SUBSTRATE_PREFLIGHT_BLOCKED
    blocked = substrate_preflight()
    SUBSTRATE_PREFLIGHT_BLOCKED = blocked
    if blocked is None:
        return
    session.config._auger_substrate_blocked = blocked
    if os.environ.get("AUGER_REQUIRE_LIVE") == "1":
        raise pytest.UsageError(blocked)
    terminal = session.config.pluginmanager.getplugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(f"{blocked}; live arms will be skipped")


def pytest_collection_modifyitems(config, items):
    """Skip only live-dependent tests when the session preflight found a blocker."""
    blocked = getattr(config, "_auger_substrate_blocked", None)
    if blocked is None:
        return
    marker = pytest.mark.skip(reason=f"{blocked}; live arm skipped")
    for item in items:
        if "live_service" in item.fixturenames:
            item.add_marker(marker)


def _substrate_lock_is_inherited() -> bool:
    return os.environ.get(SUBSTRATE_LOCK_INHERITED_ENV, "").strip() == "1"


@contextlib.contextmanager
def substrate_lock():
    """Exclusive cross-process lock over the live test substrate, re-entrant in-process.

    Create/teardown of the ephemeral namespace and the test body both run inside it via
    `ns_created`, so a sibling pytest process never touches DuckBrain mid-test. Blocking
    `flock` with no timeout kill is the DELIBERATE choice (documented at the class of
    failure this closes): a healthy-but-slow sibling is waited out, a sibling that dies
    releases its lock with its fds, and nothing here can deadlock itself.

    A process launched with AUGER_TEST_LOCK_INHERITED=1 (a nested pytest suite spawned
    by a test of a session that already holds the lock) never flocks at all: its
    acquisitions are depth counts under the ancestor's hold. See the constant's comment
    for why that is correct rather than a bypass.
    """
    global _SUBSTRATE_LOCK_FD, _SUBSTRATE_LOCK_DEPTH
    if _substrate_lock_is_inherited() or (
        _SUBSTRATE_LOCK_DEPTH > 0 and _SUBSTRATE_LOCK_FD is not None
    ):
        _SUBSTRATE_LOCK_DEPTH += 1
        try:
            yield
        finally:
            _SUBSTRATE_LOCK_DEPTH -= 1
        return
    fd = os.open(_substrate_lock_path(), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # blocking: no timeout kill, by design
        _SUBSTRATE_LOCK_FD = fd
        _SUBSTRATE_LOCK_DEPTH = 1
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            _SUBSTRATE_LOCK_FD = None
            _SUBSTRATE_LOCK_DEPTH = 0
    finally:
        os.close(fd)


@contextlib.contextmanager
def _test_transport_budget():
    """Raise `auger.db`'s default timeout for the fixture's lifetime, restore after.

    Restoring `__defaults__` VERBATIM (never via the module-level RETRIES) means a future
    change to `auger.RETRIES` cannot be silently pinned by this file's teardown, and a
    caller like `delete_namespace` that passes an EXPLICIT retries cannot have it quietly
    dropped. No caller in this repo asserts on `db.__defaults__`.

    The defaults tuple is `db`'s SIGNATURE order: (method, body, timeout, retries) — four
    slots, the first two non-numeric. Slot-splice, never a two-tuple: assigning a shorter
    tuple puts the budget into `method` and leaves `method`/`body` REQUIRED, which turns
    the liveness probe's `db("/api/namespaces", timeout=...)` into a TypeError skip
    (caught live by AC3 evidence; the substrate was healthy the whole time).
    """
    saved = auger.db.__defaults__
    auger.db.__defaults__ = saved[:2] + (SUBSTRATE_TEST_TIMEOUT_S,) + saved[3:]
    try:
        yield
    finally:
        auger.db.__defaults__ = saved


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def live_service(request: pytest.FixtureRequest) -> str:
    """The live DuckBrain URL. Skips (loudly) or fails; never passes silently.

    Both the probe and the WHOLE session live on the substrate run inside the substrate
    lock and the widened timeout budget: the `yield` sits INSIDE the `with`-block, and
    because the fixture is session-scoped the lock is taken at the first live test's
    setup and released only at session finalization. Every live test body, every
    namespace create/teardown, and every API call they make therefore serializes
    cross-process against a sibling pytest leg; in-process re-entry (the probe's own
    helpers, a nested acquisition) never self-blocks because the lock is re-entrant on
    one fd. The hold is deliberately coarse — one session-long critical section per leg
    rather than per-test — and it applies only to tests that depend on `live_service`;
    offline tests are unaffected.

    AUG-197 (AC2/AC4): while the lock is held, BEFORE the first live test, the fixture
    REAPS prior sessions' `auger-pytest-*` registry rows (and ghost directories the
    rows once pointed at) — the namespaces a foreign sync lock deferred in an earlier
    run are the ones finally deleted here, and the census (reaped vs deferred) is
    printed through the terminal reporter so the mutation is never silent. The
    inherited-lock mode (a nested child of this session) does not reap: the parent's
    session already ran the sweep. AUGER_REAP_SKIP=1 keeps a run from touching
    anything but its own rows.
    """
    with substrate_lock(), _test_transport_budget():
        if SUBSTRATE_PREFLIGHT_BLOCKED is not None:
            reason = f"{SUBSTRATE_PREFLIGHT_BLOCKED}; live arm skipped"
            pytest.skip(reason)
        url = require_live()
        reap_disabled = os.environ.get(REAP_SKIP_ENV, "").strip() == "1"
        if not _substrate_lock_is_inherited() and not reap_disabled:
            reporter = request.config.pluginmanager.getplugin("terminalreporter")
            reap: dict[str, list[str]] = {}
            try:
                reap = reap_prior_session_namespaces()
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                write_reap_report(
                    {
                        "contended": [f"<session census read failed: {exc}>"],
                        "reaped": [],
                    },
                    reporter,
                )
            else:
                try:
                    ghosts = reap_prior_session_namespace_directories()
                except (Exception, SystemExit):  # noqa: BLE001
                    ghosts = []
                if ghosts:
                    reap["ghosts_reaped"] = ghosts
                write_reap_report(reap, reporter)
        yield url


@pytest.fixture
def ns_created(live_service: str, request: pytest.FixtureRequest) -> str:
    """A fresh ephemeral namespace, removed in a finalizer even when the test fails.

    Depends on `live_service`, so the cross-process substrate lock is held for the whole
    namespace lifecycle (create, test body, teardown) and the timeout headroom applies to
    every call the fixture and the test make against the live substrate.

    AUG-197 (AC2): a teardown that ends ONLY in attributed environment contention
    (a 409 whose body names a foreign lock-holder pid) does NOT fail the test — the
    finalizer states it loudly (terminal + stderr) and leaves the namespace
    row+directory consistent for the next run's reaper.
    """
    ns = TEST_NS_PREFIX + uuid.uuid4().hex[:12]
    status, body, _ = auger.db("/api/namespaces", "POST", {"name": ns})
    if status not in (200, 201):
        pytest.fail(f"could not create test namespace {ns} ({status}): {body}")
    CREATED.append(ns)
    try:
        yield ns
    finally:
        problems = teardown_namespace(ns)
        if problems and teardown_contention_only(problems):
            reporter = request.config.pluginmanager.getplugin("terminalreporter")
            for problem in problems:
                message = (
                    f"auger: teardown of {ns} attributed to environment contention "
                    f"— NOT a test failure (AUG-197): {problem}"
                )
                print(message, file=sys.stderr)
                if reporter is not None and hasattr(reporter, "write_line"):
                    reporter.write_line(message)
            return
        if problems:
            if any("429" in p for p in problems):
                # The limiter beat even the transport's own budget on this one namespace.
                # AUG-020's contract: ONE extra retry, then LEAVE it with a skip note — a
                # saturated limiter must not error a passing test, and the leak is stated,
                # never silent.
                time.sleep(1)
                problems = teardown_namespace(ns)
                if not problems:
                    return
                print(
                    f"auger: teardown of {ns} skipped after one 429 retry: "
                    + "; ".join(problems),
                    file=sys.stderr,
                )
            else:
                # Raised in teardown, so a REAL teardown bug surfaces as an error, not as silence.
                pytest.fail(f"teardown of {ns} was incomplete: " + "; ".join(problems))


@pytest.fixture
def ns(ns_created: str) -> str:
    """An ephemeral namespace with its SDM tables declared (`auger init`)."""
    rc, out = run_cli(["-n", ns_created, "init"])
    if rc != 0 or "declared:" not in out:
        pytest.fail(f"auger init failed in {ns_created} (rc={rc}):\n{out}")
    return ns_created


SEED_TEXT = (
    "One small CLI that watches directories, records what it has seen, and posts an hourly digest.\n"
    "Runs on one Linux box, survives reboot, and tells me when it has stopped working.\n"
    "Must not lose anything if it crashes mid-run. Keep it simple - one box, no extra services.\n"
)


@pytest.fixture
def project(ns: str, tmp_path: Path) -> dict:
    """A started project in the ephemeral namespace: the seed stored and embedded."""
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text(SEED_TEXT)
    pid = "P-PYTEST"
    rc, out = run_cli(
        [
            "-n",
            ns,
            "start",
            "--name",
            "pytesttest",
            "--id",
            pid,
            "--seed-file",
            str(seed_file),
        ]
    )
    if rc != 0 or "seed stored" not in out:
        pytest.fail(f"auger start failed (rc={rc}):\n{out}")
    return {
        "ns": ns,
        "pid": pid,
        "seed": SEED_TEXT,
        "seed_file": str(seed_file),
        "out": out,
    }


def answer(
    ns: str,
    did: str,
    domain: str,
    chosen: str,
    options: list[str],
    why_not: str,
    confidence: float,
) -> tuple[int, str]:
    argv = ["-n", ns, "answer", "--id", did, "--domain", domain, "--chosen", chosen]
    for opt in options:
        argv += ["--option", opt]
    argv += ["--why-not", why_not, "--confidence", str(confidence)]
    return run_cli(argv)


D001 = dict(
    did="D-001",
    domain="4.05",
    chosen="single SQLite file",
    options=["single SQLite file", "Postgres"],
    why_not="Postgres needs a service the seed forbids",
    confidence=0.82,
)
D002 = dict(
    did="D-002",
    domain="4.06",
    chosen="staging table",
    options=["staging table", "row locking"],
    why_not="no concurrent writer",
    confidence=0.41,
)


@pytest.fixture
def decided(project: dict) -> dict:
    """The smoke-loop state: two decisions, one thin enough to need drilling."""
    ns = project["ns"]
    for spec in (D001, D002):
        rc, out = answer(ns, **spec)
        if rc != 0 or "recorded" not in out:
            pytest.fail(f"auger answer failed for {spec['did']} (rc={rc}):\n{out}")
    return {**project, "D001": D001, "D002": D002}
