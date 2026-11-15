"""Hermetic proof for REVIEW-AUGER-006: smoke.sh teardown ordering + sweep class.

The live defect (AUG-117 recurrence in the smoke arm): `drop()` in tests/smoke.sh ran
`shutil.rmtree` on the namespace directory BEFORE consulting the DELETE status, so a
409 (sync push in flight, e.g. the hourly duckbrain-s3-sync push) destroyed the
directory while the registry row survived — an orphaned mapping no later run can
repair. The pytest half of the same defect is already pinned in tests/test_auger.py
(:6587, :6645, :6682); this file brings the same contract to the SMOKE arm.

These tests are hermetic: no live DuckBrain, no substrate flock. They extract the
REAL `ns_helper` python heredoc out of tests/smoke.sh and run it against a stub
`auger` module whose DELETE answers a scripted status sequence and whose registry
answers a scripted listing. Two seams (both documented at the script) keep the
operator's home out of the blast radius: `AUGER_NS_ROOT` redirects the namespace
directory root, and (with the fix) `AUGER_CONFLICT_BACKOFFS` collapses the 409
backoff ladder to zero waits so the hermetic run stays fast.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SMOKE = REPO_ROOT / "tests" / "smoke.sh"

STUB_AUGER = '''\
"""Scripted DuckBrain transport stub — NOT the product. Answers from AUGER_STUB_STATE."""
import json
import os

_STATE = json.load(open(os.environ["AUGER_STUB_STATE"], encoding="utf-8"))
_LOG = os.environ["AUGER_STUB_LOG"]


def _record(entry):
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\\n")


def db(path, method="GET", *args, **kwargs):
    _record({"call": "db", "path": path, "method": method})
    if path == "/api/namespaces" and method == "GET":
        return 200, {"namespaces": [{"name": n} for n in _STATE["listing"]]}, None
    return 500, {"error": f"unexpected db call: {method} {path}"}, None


def delete_namespace(ns, retries=2):
    # The scripted sequence is consumed one answer per call; when it runs dry the LAST
    # scripted status replays, so a persistent-409 script stays persistent for any
    # (bounded) number of retries the caller buys.
    statuses = _STATE["delete_statuses"]
    if statuses:
        st = statuses.pop(0) if len(statuses) > 1 else statuses[0]
    else:
        st = 200
    _record({"call": "delete_namespace", "ns": ns, "status": st})
    return st, {"deleted": st in (200, 404)}
'''


def _heredoc_body() -> str:
    """Extract the ns_helper python heredoc from tests/smoke.sh — the code under test."""
    lines = SMOKE.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if "<<'PY'" in line]
    assert len(starts) == 1, "expected exactly one python heredoc in tests/smoke.sh"
    ends = [i for i, line in enumerate(lines) if i > starts[0] and line.strip() == "PY"]
    assert ends, "heredoc terminator PY missing in tests/smoke.sh"
    body = "\n".join(lines[starts[0] + 1 : ends[0]])
    assert "def drop(" in body, "extracted body is not the ns_helper (def drop missing)"
    return body


@pytest.fixture
def rig(tmp_path):
    here = tmp_path / "repo"
    (here / "tests").mkdir(parents=True)
    shutil.copy2(SMOKE, here / "tests" / "smoke.sh")
    (here / "auger.py").write_text(STUB_AUGER, encoding="utf-8")
    helper = tmp_path / "ns_helper_body.py"
    helper.write_text(_heredoc_body(), encoding="utf-8")
    nsroot = tmp_path / "nsroot"
    nsroot.mkdir()
    state = tmp_path / "stub_state.json"
    state.write_text(
        json.dumps({"listing": [], "delete_statuses": []}), encoding="utf-8"
    )
    log = tmp_path / "stub_log.jsonl"
    return SimpleNamespace(
        here=here, helper=helper, nsroot=nsroot, state=state, log=log
    )


def _set_state(rig, listing, delete_statuses):
    rig.state.write_text(
        json.dumps({"listing": listing, "delete_statuses": delete_statuses}),
        encoding="utf-8",
    )


def _run(rig, mode, ns=None):
    env = dict(os.environ)
    env["AUGER_NS_ROOT"] = str(rig.nsroot)
    env["AUGER_STUB_STATE"] = str(rig.state)
    env["AUGER_STUB_LOG"] = str(rig.log)
    env["AUGER_CONFLICT_BACKOFFS"] = (
        "0"  # hermetic: the ladder stays bounded, just zero-wait
    )
    argv = [sys.executable, str(rig.helper), str(rig.here), mode]
    if ns is not None:
        argv.append(ns)
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)


def _calls(rig):
    if not rig.log.exists():
        return []
    return [
        json.loads(line)
        for line in rig.log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _deletes(rig, ns=None):
    return [
        c
        for c in _calls(rig)
        if c["call"] == "delete_namespace" and (ns is None or c["ns"] == ns)
    ]


def _make_ns(rig, name, mtime=None):
    d = rig.nsroot / name
    (d / "tables").mkdir(parents=True)
    (d / "tables" / "decision.json").write_text("[]", encoding="utf-8")
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    return d


OLD = 1_000_000_000.0  # 2001 — older than any 24h cutoff


def test_teardown_settles_a_transient_409_and_removes_both_halves(rig):
    """A 409 that settles inside the conflict budget ends with BOTH halves removed.

    RED on the unfixed drop(): it rmtree's the directory on the FIRST attempt (before
    the status is consulted), reports "HTTP 409" and — because the caller only re-enters
    for a 429 — never issues the second DELETE. The directory is gone while the registry
    row survives: the orphan this row exists to close.
    """
    ns = "auger-smoke-r006a"
    _set_state(rig, listing=[ns], delete_statuses=[409, 200])
    d = _make_ns(rig, ns)

    proc = _run(rig, "teardown", ns)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(_deletes(rig, ns)) == 2, _calls(rig)
    assert not d.exists(), "a settled DELETE must end with the directory removed"
    assert "removed" in proc.stdout and "teardown skipped" not in proc.stdout, (
        proc.stdout
    )


def test_teardown_survives_a_persistent_409_with_the_directory_intact(rig):
    """A 409 that outlives the budget keeps the directory AND the row — both or neither.

    RED on the unfixed drop(): rmtree ran before the status check, so the directory was
    destroyed under the surviving registry row (the AUG-117 ghost-row shape). The delete
    count must also be bounded: one try per ladder step, never an unbounded loop.
    """
    ns = "auger-smoke-r006b"
    _set_state(rig, listing=[ns], delete_statuses=[409])
    d = _make_ns(rig, ns)

    proc = _run(rig, "teardown", ns)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.is_dir(), (
        "the directory was removed while the DELETE was refused — AUG-117 ghost row"
    )
    with open(d / "tables" / "decision.json", encoding="utf-8") as fh:
        assert fh.read() == "[]", "the namespace's data was removed with the directory"
    ladder = os.environ.get("AUGER_CONFLICT_BACKOFFS", "0")
    bounded = len(ladder.split(",")) + 1
    assert len(_deletes(rig, ns)) == bounded, _calls(rig)
    assert "409" in proc.stdout and "teardown skipped" in proc.stdout, proc.stdout


def test_sweep_reaps_a_stale_pytest_namespace(rig):
    """AC3: the sweep's throwaway class covers auger-pytest-* rows older than 24h.

    RED on the unfixed script: THROWAWAY at tests/smoke.sh:34 never matches the
    auger-pytest- prefix, so the 41-row pytest backlog the foreman measured is invisible
    to every sweep the script can run.
    """
    ns = "auger-pytest-r006-old"
    _set_state(rig, listing=[ns], delete_statuses=[200])
    d = _make_ns(rig, ns, mtime=OLD)

    proc = _run(rig, "sweep")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(_deletes(rig, ns)) == 1, _calls(rig)
    assert "removed 1" in proc.stdout, proc.stdout
    assert not d.exists(), "a reaped namespace must not keep its directory"


def test_sweep_reaps_a_pytest_row_whose_directory_is_gone(rig):
    """The observed leak shape (registry row survives, directory does not) is a target."""
    ns = "auger-pytest-r006-ghost"
    _set_state(rig, listing=[ns], delete_statuses=[200])
    # no directory created on purpose

    proc = _run(rig, "sweep")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(_deletes(rig, ns)) == 1, _calls(rig)
    assert "removed 1" in proc.stdout, proc.stdout


def test_sweep_spares_a_fresh_pytest_directory(rig):
    """A directory younger than 24h belongs to a live sibling suite — never a target."""
    ns = "auger-pytest-r006-fresh"
    _set_state(rig, listing=[ns], delete_statuses=[])
    d = _make_ns(rig, ns)  # mtime = now

    proc = _run(rig, "sweep")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _deletes(rig) == [], _calls(rig)
    assert d.is_dir(), "a fresh (in-flight) namespace directory must survive the sweep"


def test_sweep_never_takes_a_foreign_prefix(rig):
    """Prefix-gated: production and unrelated namespaces are never sweep targets."""
    foreign = ["auger-prod-one", "releases", "auger"]
    _set_state(rig, listing=foreign, delete_statuses=[])
    dirs = {n: _make_ns(rig, n, mtime=OLD) for n in foreign}

    proc = _run(rig, "sweep")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _deletes(rig) == [], _calls(rig)
    for n, d in dirs.items():
        assert d.is_dir(), f"foreign namespace {n} was touched"
    assert "no stale" in proc.stdout, proc.stdout


def test_sweep_still_reaps_the_original_smoke_and_eval_classes(rig):
    """Extending THROWAWAY must not lose the auger-smoke-/auger-eval- classes."""
    old, fresh = "auger-smoke-r006-old", "auger-eval-r006-fresh"
    _set_state(rig, listing=[old, fresh], delete_statuses=[200])
    d_old, d_fresh = _make_ns(rig, old, mtime=OLD), _make_ns(rig, fresh)

    proc = _run(rig, "sweep")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(_deletes(rig, old)) == 1, _calls(rig)
    assert _deletes(rig, fresh) == [], _calls(rig)
    assert not d_old.exists()
    assert d_fresh.is_dir()


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in SMOKE.with_name("gate.sh").read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_smoke_teardown.py" in arm[0], (
        "tests/test_smoke_teardown.py must be named in tests/gate.sh's pytest arm"
    )
