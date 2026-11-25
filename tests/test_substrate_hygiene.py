"""AUG-197 — the substrate-hygiene contract for a loaded box.

Four behaviors this file keeps honest, matching the tick's ACs:

  AC1  the gate's pytest arm names a killed run's test in flight (conftest
       progress hooks + scripts/gate_timeout_report.py, both wired in gate.sh);
  AC2  a 409 "Push in flight" with a FOREIGN holder pid is classified as
       environment contention, fails fast (no ladder burn), does not fail the
       test, and the namespace is reaped by the next run's reaper;
  AC4  the leftover-namespace census script reports counts + ids and never
       deletes anything.

HERMETIC: no live DuckBrain, no network. The live substrate is exercised only
through injectable fakes, exactly as tests/test_teardown_resilience.py does —
scratch-clone live proofs (AC3) are run by hand against real ticks, not here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import conftest as cf  # noqa: E402  (pytest puts tests/ on sys.path for conftest)

# The body shapes below are the LIVE-RECORDED ones (off-by-one classes 2944/2827,
# verified: ticks 2026-11-22 reproduced the pid shape on this box).
FOREIGN_409 = (
    "Push in flight for 'auger-pytest-x-y' (native sync lock held by pid 2385306 "
    "(since 312s)). Retry after it completes."
)
TRANSIENT_409 = "Push in flight for 'auger-pytest-x-y'. Retry after it completes."


def _fake_ns(suffix: str = "deadbeefcafe") -> str:
    return cf.TEST_NS_PREFIX + suffix


# ------------------------------------------------------------ AC2: classification
def test_foreign_pid_409_is_classified_as_a_foreign_lock():
    kind, pid = cf.classify_push_conflict(FOREIGN_409)
    assert (kind, pid) == ("foreign-lock", 2385306)


def test_transient_409_has_no_foreign_holder():
    assert cf.classify_push_conflict(TRANSIENT_409) == ("transient", None)


def test_unrelated_409_body_is_other():
    assert cf.classify_push_conflict("Push in flight for 'other'. Later.") is not None
    assert cf.classify_push_conflict("some other conflict (no push no pid)") == (
        "other",
        None,
    )


# ------------------------------------------ AC2: teardown fails fast, attributes
def test_foreign_pid_409_fails_fast_without_burning_the_ladder(monkeypatch, tmp_path):
    ns = _fake_ns()
    path = Path(cf.ns_path(ns))
    path.mkdir(parents=True, exist_ok=True)

    sleeps: list[float] = []
    monkeypatch.setattr(cf.time, "sleep", lambda s: sleeps.append(s))
    calls = {"delete": 0, "list": 0}

    def fake_delete(namespace):
        calls["delete"] += 1
        assert namespace == ns
        return 409, {"error": FOREIGN_409, "code": "CONFLICT"}

    def fake_list():
        calls["list"] += 1
        pytest.fail("the listing check must not run on an attributed contention")

    monkeypatch.setattr(cf.auger, "delete_namespace", fake_delete)
    monkeypatch.setattr(cf, "api_namespaces", fake_list)

    problems = cf.teardown_namespace(ns)

    assert calls["delete"] == 1, (
        f"a foreign-lock 409 must fail fast, not retry: saw {calls['delete']} DELETEs"
    )
    assert sleeps == [], "a foreign-lock 409 must not spend any ladder sleep"
    assert problems and cf.teardown_contention_only(problems), (
        f"the contention result must be the ONLY problem, got: {problems}"
    )
    problem_text = "; ".join(problems)
    assert problem_text.startswith(cf.ENV_CONTENTION_PREFIX)
    assert "pid 2385306" in problem_text, "the foreign holder pid must be named"
    assert path.exists(), "AUG-117: a refused DELETE must keep the directory"
    assert calls["list"] == 0
    import shutil  # noqa: PLC0415

    shutil.rmtree(path)  # clean the fake directory up


def test_transient_409_keeps_the_existing_short_ladder(monkeypatch, tmp_path):
    ns = _fake_ns()
    calls = {"delete": 0}
    sleeps: list[float] = []

    def fake_delete(namespace):
        calls["delete"] += 1
        if calls["delete"] == 1:
            return 409, {"error": TRANSIENT_409, "code": "CONFLICT"}
        return 200, {"success": True}

    monkeypatch.setattr(cf.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(cf, "TEARDOWN_CONFLICT_BACKOFFS", (0.0,))
    monkeypatch.setattr(cf.auger, "delete_namespace", fake_delete)
    monkeypatch.setattr(cf, "api_namespaces", lambda: [])

    problems = cf.teardown_namespace(ns)

    assert problems == [], (
        f"a transient push window is covered by the ladder: {problems}"
    )
    assert calls["delete"] == 2
    assert cf.teardown_contention_only(problems) is False


def test_contention_only_predicate_is_not_loose():
    assert cf.teardown_contention_only([]) is False
    mixed = [
        cf.ENV_CONTENTION_PREFIX + "attributed contention",
        "DELETE /api/namespaces/x returned 500: boom",
    ]
    assert cf.teardown_contention_only(mixed) is False


# ------------------------------------------------------------ AC2: the next-run reaper
def test_reaper_reaps_prior_sessions_and_spares_the_current_one(monkeypatch):
    current = _fake_ns("aaaa1111")
    prior_1 = cf.TEST_NS_GLOBAL_PREFIX + "bbbb2222-111111111111"
    prior_2 = cf.TEST_NS_GLOBAL_PREFIX + "cccc3333-222222222222"
    foreign = "hermes-memory"
    monkeypatch.setattr(
        cf.auger, "delete_namespace", lambda ns: (200, {"success": True})
    )

    reap = cf.reap_prior_session_namespaces(
        ns_list=[current, prior_1, prior_2, foreign]
    )

    assert reap["reaped"] == sorted([prior_1, prior_2])
    assert reap["contended"] == []
    assert current not in reap["reaped"], "the CURRENT session's rows must be spared"
    assert foreign not in reap["reaped"], "non-test namespaces must never be touched"


def test_reaper_defers_a_foreign_pid_409_and_names_it(monkeypatch):
    prior = cf.TEST_NS_GLOBAL_PREFIX + "bbbb2222-111111111111"
    attempts: list[str] = []

    def fake_delete(ns):
        attempts.append(ns)
        return 409, {"error": FOREIGN_409, "code": "CONFLICT"}

    reap = cf.reap_prior_session_namespaces(ns_list=[prior], delete=fake_delete)

    assert len(attempts) == 1, "a foreign lock must not be retried by the reaper"
    assert reap["reaped"] == []
    assert reap["contended"] == [f"{prior} (foreign sync lock held by pid 2385306)"]


def test_reaper_reports_a_plain_refusal_instead_of_failing(monkeypatch):
    prior = cf.TEST_NS_GLOBAL_PREFIX + "bbbb2222-111111111111"
    reap = cf.reap_prior_session_namespaces(
        ns_list=[prior], delete=lambda ns: (500, "boom")
    )
    assert reap["reaped"] == []
    assert reap["contended"] == [f"{prior} (DELETE returned 500: boom)"]


def test_reaper_removes_ghost_directories_but_only_test_prefixed(monkeypatch, tmp_path):
    base = tmp_path / "namespaces"
    base.mkdir()
    current = base / _fake_ns("aaaabbbb")
    prior = base / "auger-pytest-oldtag-999999999999"
    non_test = base / "hermes-memory"
    for p in (current, prior, non_test):
        p.mkdir()

    monkeypatch.setattr(cf.auger, "namespaces_base", lambda: str(base))

    removed = cf.reap_prior_session_namespace_directories()

    assert removed == [prior.name]
    assert not prior.exists()
    assert current.exists(), "the CURRENT session's tree must be spared"
    assert non_test.exists(), "a non-test namespace tree must be spared"


def test_reap_report_names_every_leg_of_the_census(capsys, monkeypatch):
    lines: list[str] = []
    reporter = SimpleNamespace(write_line=lines.append)
    monkeypatch.delenv(cf.GATE_PROGRESS_ENV, raising=False)

    cf.write_reap_report(
        {
            "reaped": ["auger-pytest-oldtag-1"],
            "contended": ["auger-pytest-oldtag-2 (foreign sync lock held by pid 7)"],
            "ghosts_reaped": ["auger-pytest-oldtag-3"],
        },
        reporter,
    )
    out = capsys.readouterr().err
    assert "reaped 1 prior-session test namespace(s)" in out
    assert "deferred 1 prior-session test namespace(s)" in out
    assert "pid 7" in out
    assert "ghost test-directory trees" in out
    assert len(lines) == 3, "the terminal reporter must receive the same three lines"


# ---------------------------------------- AC1: progress hooks + attribution report
def _fake_node(nodeid: str) -> SimpleNamespace:
    return SimpleNamespace(nodeid=nodeid)


def test_progress_hooks_are_silent_without_the_gate_env(monkeypatch, tmp_path):
    monkeypatch.delenv(cf.GATE_PROGRESS_ENV, raising=False)
    cf.pytest_runtest_logstart("tests/a.py::test_b", None)
    assert not list(tmp_path.iterdir())


def test_progress_hooks_record_start_and_reports(monkeypatch, tmp_path):
    progress = tmp_path / "progress.jsonl"
    monkeypatch.setenv(cf.GATE_PROGRESS_ENV, str(progress))
    cf.pytest_runtest_logstart("tests/a.py::test_a", None)
    cf.pytest_runtest_logreport(
        SimpleNamespace(nodeid="tests/a.py::test_a", when="setup", outcome="passed")
    )
    cf.pytest_runtest_logreport(
        SimpleNamespace(nodeid="tests/a.py::test_a", when="call", outcome="passed")
    )
    cf.pytest_runtest_logreport(
        SimpleNamespace(nodeid="tests/a.py::test_a", when="teardown", outcome="passed")
    )

    lines = [json.loads(line) for line in progress.read_text().splitlines()]
    assert lines[0]["event"] == "start" and lines[0]["nodeid"] == "tests/a.py::test_a"
    assert {line["when"] for line in lines if line["event"] == "report"} == {
        "setup",
        "call",
        "teardown",
    }


def _load_report_script():
    spec = importlib.util.spec_from_file_location(
        "gate_timeout_report", REPO_ROOT / "scripts" / "gate_timeout_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_names_the_test_in_flight(tmp_path, capsys):
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        "\n".join(
            [
                json.dumps(
                    {"event": "start", "nodeid": "tests/a.py::test_a", "t": 1.0}
                ),
                json.dumps(
                    {
                        "event": "report",
                        "when": "teardown",
                        "outcome": "passed",
                        "nodeid": "tests/a.py::test_a",
                        "t": 2.0,
                    }
                ),
                json.dumps(
                    {"event": "start", "nodeid": "tests/b.py::test_b", "t": 3.0}
                ),
                json.dumps(
                    {
                        "event": "report",
                        "when": "setup",
                        "outcome": "passed",
                        "nodeid": "tests/b.py::test_b",
                        "t": 3.1,
                    }
                ),
                json.dumps(
                    {"event": "start", "nodeid": "tests/c.py::test_c", "t": 4.0}
                ),
            ]
        )
        + "\n"
    )
    script = _load_report_script()
    assert script.main(["--progress", str(progress), "--budget", "2400s"]) == 0
    out = capsys.readouterr().out
    assert (
        "first failing test (in flight when the arm was killed): tests/c.py::test_c"
        in out
    )
    assert "tests that FAILED earlier in this arm: 0" in out
    assert "last completed test: tests/b.py::test_b [setup:passed]" in out


def test_report_lists_failures_recorded_before_the_kill(tmp_path, capsys):
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        "\n".join(
            [
                json.dumps(
                    {"event": "start", "nodeid": "tests/a.py::test_f1", "t": 1.0}
                ),
                json.dumps(
                    {
                        "event": "report",
                        "when": "call",
                        "outcome": "failed",
                        "nodeid": "tests/a.py::test_f1",
                        "t": 2.0,
                    }
                ),
                json.dumps(
                    {
                        "event": "report",
                        "when": "teardown",
                        "outcome": "passed",
                        "nodeid": "tests/a.py::test_f1",
                        "t": 2.5,
                    }
                ),
                json.dumps(
                    {"event": "start", "nodeid": "tests/b.py::test_hang", "t": 9.0}
                ),
                json.dumps(
                    {
                        "event": "report",
                        "when": "setup",
                        "outcome": "passed",
                        "nodeid": "tests/b.py::test_hang",
                        "t": 9.1,
                    }
                ),
            ]
        )
        + "\n"
    )
    script = _load_report_script()
    assert script.main(["--progress", str(progress)]) == 0
    out = capsys.readouterr().out
    assert (
        "first failing test (in flight when the arm was killed): tests/b.py::test_hang"
        in out
    )
    assert "tests that FAILED earlier in this arm: 1" in out
    assert "- tests/a.py::test_f1 (call: failed)" in out


def test_report_stays_loud_when_nothing_is_attributable(tmp_path, capsys):
    progress = tmp_path / "progress.jsonl"
    progress.write_text("not json at all\n")
    script = _load_report_script()
    assert script.main(["--progress", str(progress)]) == 0
    out = capsys.readouterr().out
    assert "ATTRIBUTION UNAVAILABLE" in out, (
        "empty attribution must be stated, not hidden"
    )
    assert "undecodable line" in out


# -------------------------------------------------- AC2: preflight (fleet-push match)
def test_preflight_catches_both_fleet_push_spellings(monkeypatch):
    """AUG-197: the live runner is `node --import tsx bin/duckbrain.ts` now — the
    preflight marker matched only the old `duckbrain.js` spelling, so a REAL
    fleet push sailed past it (measured: pid 3520037, sync in flight while the
    suite ran live arms)."""
    commands = [
        (4242, "node --import tsx bin/duckbrain.ts s3 sync all push"),
        (99, "node bin/duckbrain.js s3 sync all push"),
        (7, "node bin/duckbrain.js http --port 3000"),
    ]
    holders = [pid for pid, _ in cf._full_fleet_sync_holders(commands)]
    assert holders == [4242, 99]

    blocked = cf.substrate_preflight(
        process_commands=commands,
        push_probe=lambda: None,
        lock_holder=lambda: None,
    )
    assert blocked is not None
    assert blocked.startswith(cf.AUGER_BLOCKED_BY_PREFIX)
    assert "4242" in blocked, "the blocker's pid must be named"


def test_preflight_flies_when_only_an_unrelated_daemon_runs(monkeypatch):
    commands = [(7, "node bin/duckbrain.js http --port 3000")]
    assert (
        cf.substrate_preflight(
            process_commands=commands,
            push_probe=lambda: None,
            lock_holder=lambda: None,
        )
        is None
    )


# ------------------------------------------------------------ AC4: the census script
def _load_census_script():
    spec = importlib.util.spec_from_file_location(
        "auger_ns_census", REPO_ROOT / "scripts" / "auger_ns_census.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_census_counts_and_partitions_both_sides(monkeypatch, tmp_path, capsys):
    script = _load_census_script()
    base = tmp_path / "namespaces"
    base.mkdir()
    for name in ("auger-pytest-oldtag-a", "auger-pytest-oldtag-b", "hermes-memory"):
        (base / name).mkdir()

    def fake_db(path, *args, **kw):
        assert path == "/api/namespaces"
        return (
            200,
            {
                "namespaces": [
                    {"name": "auger-pytest-oldtag-a"},
                    {"name": "hermes-memory"},
                ]
            },
            None,
        )

    monkeypatch.setitem(
        sys.modules,
        "auger",
        SimpleNamespace(
            db=fake_db,
            namespaces_base=lambda: str(base),
            DB_URL="http://127.0.0.1:3000",
        ),
    )
    monkeypatch.delenv("DUCKBRAIN_URL", raising=False)

    result = script.census("auger-pytest-")

    assert result["registry"] == ["auger-pytest-oldtag-a"]
    assert result["disk"] == ["auger-pytest-oldtag-a", "auger-pytest-oldtag-b"]
    assert result["both"] == ["auger-pytest-oldtag-a"]
    assert result["registry_only"] == []
    assert result["disk_only"] == ["auger-pytest-oldtag-b"]
    out = capsys.readouterr().out  # nothing printed by census() itself
    assert out == ""


def test_census_refuses_the_substrate_loudly(monkeypatch, capsys):
    script = _load_census_script()
    monkeypatch.setitem(
        sys.modules,
        "auger",
        SimpleNamespace(
            db=lambda *a, **kw: (_ for _ in ()).throw(SystemExit("no DuckBrain token")),
            DB_URL="http://127.0.0.1:3000",
            namespaces_base=lambda: "/nonexistent",
        ),
    )
    assert script.main(["--prefix", "auger-pytest-"]) == 2
    err = capsys.readouterr().err
    assert "could not read the substrate" in err
    assert "no DuckBrain token" in err


# ------------------------------------------------- wiring: the gate must carry it
def test_gate_wires_the_progress_file_and_the_attribution_report():
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text()
    assert "export AUGER_GATE_PROGRESS_FILE" in gate, (
        "the gate must export the progress file the conftest hooks append to"
    )
    assert "scripts/gate_timeout_report.py" in gate
    assert '--progress "$AUGER_GATE_PROGRESS_FILE"' in gate
    assert "attribution UNAVAILABLE" in gate, (
        "a missing progress file must be stated loudly on a timeout"
    )
    # Source-derived rule of this series (W3): the timeout attribution branch must
    # be reachable for BOTH overrun codes without duplicating the pytest arm.
    timeout_branch = gate[gate.index('if [ "$rc" -eq 124 ]') :]
    assert '"$rc" -eq 137' in timeout_branch
    assert gate.count("first failing test: none attributable") == 1


def test_conftest_hooks_are_wired_by_names_the_gate_helpers_read():
    source = (REPO_ROOT / "tests" / "conftest.py").read_text()
    assert "def pytest_runtest_logstart(" in source
    assert "def pytest_runtest_logreport(" in source
    assert cf.GATE_PROGRESS_ENV == "AUGER_GATE_PROGRESS_FILE"


# ------------------------------------------------- AUG-211: the lock-attributed skip-count bound
# Failure mode 0 of the AUG-209 lock classification (judge verdict 40aa2b34): the
# typed-contention skips are the RIGHT per-test answer, but a chronic 503
# SERIALIZER_LOCKED storm can convert dozens of tests into skips while the
# battery still exits 0 "green". The gate must read the skips back, attribute
# them to the lock by the AUG-209 skip-reason convention, and fail loudly past a
# stated ceiling. Non-lock skips (hosted-CI live-arm skips, foreign-leg
# contention on unrelated surfaces) are OUT of scope — they must never trip the
# bound. The decision lives in scripts/gate_skip_bound.py so these proofs never
# run the whole gate.
def _load_bound_script():
    spec = importlib.util.spec_from_file_location(
        "gate_skip_bound", REPO_ROOT / "scripts" / "gate_skip_bound.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock_skip_line(count: int, nodeid: str = "tests/test_auger.py::test_x") -> str:
    """The exact -rs short-summary shape conftest's attributed skips print."""
    return (
        f"SKIPPED [{count}] {nodeid}: substrate write contention "
        f"(503 SERIALIZER_LOCKED, AUG-209): held by a competing writer"
    )


def test_skip_bound_counts_only_lock_attributed_skips():
    """A lock storm is counted by the AUG-209 skip-reason convention, not by every skip.

    The hosted-CI mode legitimately skips 139 live-dependent tests; those must
    never trip the bound (DEFECT-1 rework: mode-blind ceiling was a false red
    for every hosted-CI run).
    """
    mod = _load_bound_script()
    output = (
        _lock_skip_line(1, "tests/test_auger.py::test_insert_refuses")
        + "\n"
        + "SKIPPED [139] tests/test_live.py::test_live_arm: live substrate not configured\n"
        + _lock_skip_line(1, "tests/test_auger.py::test_ask_writes")
        + "\n"
        + "2 failed, 1183 passed, 141 skipped, 3 warnings in 123.45s\n"
    )
    assert mod.lock_skip_count(output) == 2
    assert mod.total_skip_count(output) == 141


def test_skip_bound_counts_the_aggregated_short_summary_bracket():
    """pytest aggregates same-location skips as ``SKIPPED [n]`` — the n is the storm."""
    mod = _load_bound_script()
    output = (
        "SKIPPED [47] tests/test_auger.py:910: substrate write contention "
        "(503 SERIALIZER_LOCKED, AUG-209): held\n"
        "1183 passed, 47 skipped in 500.11s\n"
    )
    assert mod.lock_skip_count(output) == 47


def test_skip_bound_decision_keys_on_the_ceiling():
    mod = _load_bound_script()
    assert mod.within_bound(0, 10)
    assert mod.within_bound(10, 10)  # at the ceiling is still a pass
    assert not mod.within_bound(11, 10)


def test_skip_bound_fails_closed_when_skips_cannot_be_attributed(tmp_path, capsys):
    """A skip total with NO short-summary lines means the reasons were never captured.

    The gate invokes pytest with -rs; if a future edit drops it, the bound must
    say the attribution is UNPROVEN and fail — never silently count 0 and pass.
    """
    mod = _load_bound_script()
    summary = tmp_path / "summary.txt"
    summary.write_text("1183 passed, 12 skipped in 9.00s\n")
    assert mod.main(["--summary-file", str(summary), "--ceiling", "10"]) == 1
    err = capsys.readouterr().err
    assert "UNATTRIBUTED" in err, err


def test_skip_bound_script_fails_loud_over_the_ceiling(tmp_path, capsys):
    mod = _load_bound_script()
    summary = tmp_path / "summary.txt"
    summary.write_text(_lock_skip_line(47) + "\n1183 passed, 47 skipped in 500.11s\n")
    assert mod.main(["--summary-file", str(summary), "--ceiling", "10"]) == 1
    err = capsys.readouterr().err
    assert "SKIP STORM" in err, err
    assert "47" in err and "10" in err, err
    assert "SERIALIZER_LOCKED" in err, err  # names the suspected lock storm


def test_skip_bound_script_passes_within_the_ceiling(tmp_path, capsys):
    mod = _load_bound_script()
    summary = tmp_path / "summary.txt"
    # One lock-attributed skip PLUS 139 hosted-CI live-arm skips: the bound
    # grades the LOCK count, so this healthy hosted-CI shape passes.
    summary.write_text(
        _lock_skip_line(1)
        + "\nSKIPPED [139] tests/test_live.py::test_live_arm: live substrate not configured\n"
        + "1183 passed, 140 skipped in 500.11s\n"
    )
    assert mod.main(["--summary-file", str(summary), "--ceiling", "10"]) == 0
    out = capsys.readouterr().out
    assert "1" in out and "10" in out  # the attributed count and ceiling are stated


def test_gate_wires_the_skip_count_bound():
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text()
    assert "scripts/gate_skip_bound.py" in gate, (
        "the gate's pytest arm must grade the run's skip count through the helper"
    )
    assert "AUGER_GATE_SKIP_CEILING" in gate, (
        "the ceiling must be visible (and overridable) in tests/gate.sh"
    )


def test_gate_pytest_arm_names_this_file():
    """The repo pattern: an unrun proof is an unenforced proof — the arm must name us."""
    arm = [
        line
        for line in (REPO_ROOT / "tests" / "gate.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_substrate_hygiene.py" in arm[0], (
        "tests/test_substrate_hygiene.py is not in the gate's pytest arm — the AUG-211 "
        "proofs would be an unrun proof"
    )
    assert "-rs" in arm[0].split(), (
        "the pytest arm must run with -rs so skip REASONS reach the captured "
        "summary the lock-attributed bound parses"
    )
    named = [token for token in arm[0].split() if token.startswith("tests/test_")]
    missing = [name for name in named if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"tests/gate.sh's pytest arm names files that do not exist: {missing}"
    )
