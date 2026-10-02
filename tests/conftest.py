"""
Shared fixtures for the pytest suite that replaces shell-only coverage of `auger`.

Three rules shape this file, all of them cost-places in the shell suite it replaces:

  * ONE EPHEMERAL NAMESPACE PER TEST, torn down in a fixture finalizer so a failing
    assertion still removes it. `tests/smoke.sh` deliberately leaves its namespace
    behind (it prints a `rm -rf` line and asks the reader to run it); a test suite that
    leaks a namespace per case would be worse, so teardown happens here, in a `finally`.
  * TEARDOWN GOES DOWN BOTH PATHS — `DELETE /api/namespaces/<ns>` (the registry mapping
    plus the directory) and `shutil.rmtree` of `~/duckbrain/namespaces/<ns>`. Whatever a
    path could not fix is RETURNED to the caller and raised by the fixture, never
    swallowed: a namespace that could not be removed is an error, not a footnote.
  * THE LIVE DEPENDENCY IS EXPLICIT. Every test that needs DuckBrain depends on the
    `live_service` fixture, which probes the API and either skips naming `DUCKBRAIN_URL`
    and the remedy, or (under `AUGER_REQUIRE_LIVE=1`) fails hard. There is no path where
    an unreachable service produces a silent green.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import os
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

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

TEST_NS_PREFIX = "auger-pytest-"

#: Every namespace this session created. The leak audit reads it.
CREATED: list[str] = []

REMEDY = (
    "start DuckBrain on 127.0.0.1:3000 and put a token in "
    "~/.duckbrain/foreman-status.token (or set DUCKBRAIN_API_KEY)"
)


# ---------------------------------------------------------------- CLI harness
def run_cli(argv: list[str]) -> tuple[int, str]:
    """Invoke the CLI in-process and capture its stdout. SystemExit is NOT caught here."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = auger.main(argv)
    return rc, buf.getvalue()


def run_cli_exit(argv: list[str]) -> tuple[int, str, str]:
    """Run a verb that is expected to REFUSE; returns (exit_code, message, stdout)."""
    buf = io.StringIO()
    with pytest.raises(SystemExit) as ei, contextlib.redirect_stdout(buf):
        auger.main(argv)
    exc = ei.value
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
def teardown_namespace(ns: str) -> list[str]:
    """Remove a test namespace over BOTH paths. Returns the problems it could not fix.

    Refuses anything without the test prefix, so a bug here can never rmtree a real
    namespace. 404 from the API counts as success: the desired end state is "gone".

    The deletion goes through `auger.delete_namespace`, never a hand-rolled request: that
    call is the one the 429 retry exists for. A 429 landing on a bare DELETE leaves the
    registry row behind while this function still removes the directory — a namespace that
    is listed but does not exist — which is the leak this teardown was observed to produce.
    Routing it through the module means the retry budget, the Retry-After handling and the
    "no bypass" property are all one implementation, proved in `test_auger.py`.
    """
    if not ns.startswith(TEST_NS_PREFIX):
        raise ValueError(
            f"refusing to tear down a namespace that is not a test namespace: {ns!r}"
        )
    problems: list[str] = []

    # 409 = DuckBrain's native sync lock is held by an in-flight git push. The server
    # itself says "Retry after it completes": under fleet load a push can run for
    # minutes (observed: >90s), so retry 409 here with a bounded backoff before
    # recording the problem. Bounded: ~10 min ceiling — a push that outlives it is
    # recorded as a problem, never hung on.
    deadline = time.monotonic() + 600
    backoff = 5
    while True:
        status, body = auger.delete_namespace(ns)
        if status not in (200, 404):
            problems.append(
                f"DELETE /api/namespaces/{ns} returned {status}: {str(body)[:200]}"
            )
            if status == 409 and time.monotonic() < deadline:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                problems.pop()  # retried below; only the final outcome is reported
                continue
        break

    path = ns_path(ns)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        problems.append(f"directory still present: {path}")

    try:
        if ns in api_namespaces():
            problems.append(f"namespace {ns} is still listed by /api/namespaces")
    except SystemExit as exc:
        problems.append(f"could not confirm removal over the API: {exc}")
    return problems


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
def live_service() -> str:
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
    """
    with substrate_lock(), _test_transport_budget():
        url = require_live()
        yield url


@pytest.fixture
def ns_created(live_service: str) -> str:
    """A fresh ephemeral namespace, removed in a finalizer even when the test fails.

    Depends on `live_service`, so the cross-process substrate lock is held for the whole
    namespace lifecycle (create, test body, teardown) and the timeout headroom applies to
    every call the fixture and the test make against the live substrate.
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
