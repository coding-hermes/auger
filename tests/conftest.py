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
import io
import os
import shutil
import sys
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


def leftovers(scope: list[str] | None = None) -> dict[str, list[str]]:
    """Every leftover test namespace, split by the two places one can linger.

    `scope` restricts the audit to a known set of names, and the gate relies on
    that: the suite can run CONCURRENTLY with another process running the same
    suite in the same workdir (a gitreins judge re-runs it — proven 2026-09-20,
    two async judges plus this suite at once), and an unscoped prefix scan then
    reports the other run's live, in-flight namespace as a leak of THIS run. That
    race blocked a correct board commit. The property this suite must prove is
    that IT cleaned up, which is exactly and only the names it created.
    """
    api = [n for n in api_namespaces() if n.startswith(TEST_NS_PREFIX)]
    root = Path(os.path.expanduser("~")) / "duckbrain" / "namespaces"
    disk = sorted(p.name for p in root.iterdir() if p.name.startswith(TEST_NS_PREFIX)) \
        if root.is_dir() else []
    if scope is not None:
        want = set(scope)
        api = [n for n in api if n in want]
        disk = [n for n in disk if n in want]
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
        raise ValueError(f"refusing to tear down a namespace that is not a test namespace: {ns!r}")
    problems: list[str] = []

    status, body = auger.delete_namespace(ns)
    if status not in (200, 404):
        problems.append(f"DELETE /api/namespaces/{ns} returned {status}: {str(body)[:200]}")

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


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def live_service() -> str:
    """The live DuckBrain URL. Skips (loudly) or fails; never passes silently."""
    return require_live()


@pytest.fixture
def ns_created(live_service: str) -> str:
    """A fresh ephemeral namespace, removed in a finalizer even when the test fails."""
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
            # Raised in teardown, so a leaking namespace surfaces as an error, not as silence.
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
    rc, out = run_cli(["-n", ns, "start", "--name", "pytesttest", "--id", pid,
                       "--seed-file", str(seed_file)])
    if rc != 0 or "seed stored" not in out:
        pytest.fail(f"auger start failed (rc={rc}):\n{out}")
    return {"ns": ns, "pid": pid, "seed": SEED_TEXT, "seed_file": str(seed_file), "out": out}


def answer(ns: str, did: str, domain: str, chosen: str, options: list[str],
           why_not: str, confidence: float) -> tuple[int, str]:
    argv = ["-n", ns, "answer", "--id", did, "--domain", domain, "--chosen", chosen]
    for opt in options:
        argv += ["--option", opt]
    argv += ["--why-not", why_not, "--confidence", str(confidence)]
    return run_cli(argv)


D001 = dict(did="D-001", domain="4.05", chosen="single SQLite file",
            options=["single SQLite file", "Postgres"],
            why_not="Postgres needs a service the seed forbids", confidence=0.82)
D002 = dict(did="D-002", domain="4.06", chosen="staging table",
            options=["staging table", "row locking"],
            why_not="no concurrent writer", confidence=0.41)


@pytest.fixture
def decided(project: dict) -> dict:
    """The smoke-loop state: two decisions, one thin enough to need drilling."""
    ns = project["ns"]
    for spec in (D001, D002):
        rc, out = answer(ns, **spec)
        if rc != 0 or "recorded" not in out:
            pytest.fail(f"auger answer failed for {spec['did']} (rc={rc}):\n{out}")
    return {**project, "D001": D001, "D002": D002}
