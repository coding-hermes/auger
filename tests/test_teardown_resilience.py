"""AUG-131 — a namespace whose directory is RECREATED after a settled DELETE.

DuckBrain writes embeddings on its own thread into `<ns>/.embeddings/<model>/`. If that write
lands after the delete, the directory comes back and the API's own rmtree answers ENOTEMPTY
(observed twice on 2026-11-03 as a 500 that blocked two board commits). `teardown_namespace`
used to remove the directory exactly once and then report "directory still present", so the
suite's leak audit failed for a race in the substrate.

These tests are HERMETIC: no DuckBrain, no network. They drive `teardown_namespace` with the
removal primitive patched to behave like a racing indexer, so the retry contract is proved
without a live service and without a 20-minute suite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import conftest as cf  # noqa: E402  (pytest puts tests/ on sys.path for conftest)


def _fake_ns() -> str:
    return cf.TEST_NS_PREFIX + "deadbeefcafe"


def test_directory_recreated_twice_is_still_removed(monkeypatch):
    """The indexer recreates the tree twice; the third removal wins and teardown is clean."""
    ns = _fake_ns()
    path = Path(cf.ns_path(ns))
    path.mkdir(parents=True, exist_ok=True)

    real_rmtree = cf.shutil.rmtree
    calls = {"n": 0}
    deletes = {"n": 0}

    def flaky_rmtree(target, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            # The indexer put the directory back between our removal and this call.
            path.mkdir(parents=True, exist_ok=True)
            (path / ".embeddings").mkdir(exist_ok=True)
            return
        return real_rmtree(target, *a, **kw)

    def fake_delete(namespace):
        deletes["n"] += 1
        return (
            404,
            "",
        )  # the registry row is already gone; only the tree keeps returning

    monkeypatch.setattr(cf.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(cf.auger, "delete_namespace", fake_delete)
    monkeypatch.setattr(cf, "api_namespaces", lambda: [])

    problems = cf.teardown_namespace(ns)

    assert problems == [], (
        f"teardown reported problems for a transient race: {problems}"
    )
    assert not path.exists(), "the directory survived the retry budget"
    assert calls["n"] >= 3, (
        f"expected at least three removal attempts, saw {calls['n']}"
    )
    assert deletes["n"] >= 1, "a recreated tree did not trigger a re-issued DELETE"


def test_directory_that_never_goes_away_is_reported(monkeypatch):
    """A tree that always comes back is still a problem — the retry is bounded, not a loop."""
    ns = _fake_ns()
    path = Path(cf.ns_path(ns))
    path.mkdir(parents=True, exist_ok=True)

    calls = {"n": 0}

    def never_sticks(target, *a, **kw):
        calls["n"] += 1
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cf.shutil, "rmtree", never_sticks)
    monkeypatch.setattr(cf.auger, "delete_namespace", lambda namespace: (404, ""))
    monkeypatch.setattr(cf, "api_namespaces", lambda: [])

    problems = cf.teardown_namespace(ns)

    assert any("still present" in p for p in problems), (
        f"a directory that never disappears must be reported, got: {problems}"
    )
    assert calls["n"] == len(cf.REMOVAL_BACKOFFS), (
        f"the retry must be bounded at {len(cf.REMOVAL_BACKOFFS)} attempts, saw {calls['n']}"
    )


def test_refused_delete_keeps_the_directory_and_the_row(monkeypatch):
    """The AUG-117 rule still holds: a refused DELETE must NOT orphan the registry row."""
    ns = _fake_ns()
    path = Path(cf.ns_path(ns))
    path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        cf.shutil, "rmtree", lambda *a, **kw: pytest.fail("rmtree must not run")
    )
    monkeypatch.setattr(cf.auger, "delete_namespace", lambda namespace: (500, "boom"))
    monkeypatch.setattr(cf, "api_namespaces", lambda: [ns])
    monkeypatch.setattr(cf, "TEARDOWN_CONFLICT_BACKOFFS", (0,))

    problems = cf.teardown_namespace(ns)

    assert path.exists(), (
        "a refused DELETE removed the directory, orphaning the registry row"
    )
    assert any("500" in p for p in problems), problems
    os.rmdir(path)


def test_teardown_refuses_a_real_namespace_name():
    """The guard that makes a teardown bug unable to touch real data."""
    with pytest.raises(ValueError, match="not a test namespace"):
        cf.teardown_namespace("scheduler-canary-test")
