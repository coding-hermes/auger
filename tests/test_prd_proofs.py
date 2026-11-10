"""AUG-137: the PRD's replayable proofs have a runner, and the runner cannot be quietly narrowed.

The replays themselves run in the gate's own `== prd proofs (AUG-137) ==` arm
(`python3 scripts/replay_prd_proofs.py --check`), so this file holds the CONTRACT arms — the
properties that make that arm trustworthy and that fail fast (sub-second, no subprocess replays):

  coverage ......... the reviewed allowlist classifies every PRD bullet, by sha256 of its text
  no quiet narrowing a required proof without an implemented replay fails; a deferral needs an owner
  no quiet dropping a dropped allowlist entry is refused (the PRD still lists the proof)
  artifact .......... the checked-in artifact records every proof, and its verdict is pass
  deferrals ......... every deferred proof is recorded `not-run`, never `pass`
  provenance ........ a recorded command carries no interpreter path and no absolute path, so the
                      artifact is reproducible on another host and under another interpreter name
  falsifiers ........ the runner is driven as a SUBPROCESS through the documented knobs
                      (`--specs-root`, `--artifact`) so the verdict is proved to go non-zero on a
                      forced `fail` and on a forced `not-run` — and to stay zero as a control
  the arm itself .... tests/gate.sh runs the proof runner and names this file in its pytest arm

ch:trace row=AUG-137 spec=docs/prd/xml-spec-graph.md#success-criteria--replayable-proofs test=tests/test_prd_proofs.py doc=docs/prd/evidence/AUG-137-prd-proofs.json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT  # tests/conftest.py defines and exports it

from scripts import replay_prd_proofs as runner  # noqa: E402  (needs REPO_ROOT on sys.path first)

ARTIFACT = REPO_ROOT / "docs" / "prd" / "evidence" / "AUG-137-prd-proofs.json"
GATE = REPO_ROOT / "tests" / "gate.sh"
RUNNER = REPO_ROOT / "scripts" / "replay_prd_proofs.py"


def _parsed() -> list:
    return runner.parse_prd_proofs(runner.PRD_PATH)


def _allowlist() -> dict:
    return runner.load_allowlist(runner.ALLOWLIST_PATH)


def _artifact() -> dict:
    assert ARTIFACT.is_file(), (
        f"{ARTIFACT} is missing — record a run: python3 scripts/replay_prd_proofs.py"
    )
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_the_prd_still_lists_eight_replayable_proofs() -> None:
    """Non-vacuous: the section is found and carries the bullets the row is about."""
    parsed = _parsed()
    assert len(parsed) == 8, (
        f"the PRD's '## {runner.SECTION_TITLE}' section has {len(parsed)} bullet(s), not 8 — if "
        "the PRD changed, re-review the allowlist: "
        "python3 scripts/replay_prd_proofs.py --print-prd-proofs"
    )
    assert [proof["id"] for proof in parsed] == [f"P{n}" for n in range(1, 9)]


def test_the_allowlist_classifies_every_prd_bullet() -> None:
    """Coverage is reconciled by sha256, in both directions (uncovered bullet OR stale entry)."""
    rows = runner.reconcile(_parsed(), _allowlist())
    assert len(rows) == 8
    assert {row["prd_sha256"] for row in rows} == {
        proof["sha256"] for proof in _parsed()
    }
    for row in rows:
        assert row["disposition"] in ("required", "deferred")
        if row["disposition"] == "deferred":
            assert row["owner"].strip(), (
                f"{row['id']}: a deferral must name its owning row"
            )
            assert row["reason"].strip(), f"{row['id']}: a deferral must carry a reason"


def test_every_required_proof_has_an_implemented_replay() -> None:
    """The quiet-narrowing guard: a REQUIRED proof with no replay is a hole, not a green."""
    required = [
        row["id"]
        for row in runner.reconcile(_parsed(), _allowlist())
        if row["disposition"] == "required"
    ]
    assert required, (
        "no proof is REQUIRED — the arm would pass without replaying anything"
    )
    missing = [proof_id for proof_id in required if proof_id not in runner.REPLAYS]
    assert not missing, f"REQUIRED proof(s) with no implemented replay: {missing}"


def test_dropping_an_allowlist_entry_is_refused() -> None:
    """Deleting a classification cannot silently retire a proof."""
    allowlist = _allowlist()
    allowlist["proofs"] = allowlist["proofs"][:-1]
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(_parsed(), allowlist)
    assert "in no allowlist entry" in str(caught.value), caught.value


def test_a_deferral_without_an_owner_is_refused() -> None:
    """A deferral is a reviewed decision: it must name the row that owns the missing capability."""
    allowlist = _allowlist()
    deferred = next(
        entry for entry in allowlist["proofs"] if entry["disposition"] == "deferred"
    )
    deferred.pop("owner", None)
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(_parsed(), allowlist)
    assert "must name the board row" in str(caught.value), caught.value


def test_an_edited_prd_bullet_is_refused() -> None:
    """The sha pin: rewriting a bullet's text forces a re-review of its disposition."""
    parsed = _parsed()
    parsed[0] = {**parsed[0], "sha256": "0" * 64}
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(parsed, _allowlist())
    assert "in no allowlist entry" in str(caught.value), caught.value


def test_the_check_artifact_records_every_proof_and_passes() -> None:
    """The checked-in artifact is the record of a real run: 8 entries, verdict pass."""
    artifact = _artifact()
    assert artifact["generated_by"] == "scripts/replay_prd_proofs.py"
    proofs = artifact["proofs"]
    assert [proof["id"] for proof in proofs] == [f"P{n}" for n in range(1, 9)]
    assert artifact["verdict"] == "pass", artifact["required_not_passing"]
    assert artifact["required_not_passing"] == []
    for proof in proofs:
        assert proof["status"] in ("pass", "fail", "not-run")
        assert proof["command"].strip(), f"{proof['id']}: no command recorded"
        if proof["status"] == "pass":
            assert proof["corpus_bytes"] and proof["bundle_bytes"], (
                f"{proof['id']}: a passing proof must carry measured bytes"
            )
            assert proof["latency_ms"] and proof["latency_ms"] > 0, (
                f"{proof['id']}: a passing proof must carry a measured latency"
            )
        else:
            assert proof["corpus_bytes"] is None and proof["latency_ms"] is None, (
                f"{proof['id']}: {proof['status']} must not carry measurements"
            )
            assert proof["detail"].strip(), f"{proof['id']}: a non-pass needs a reason"


def test_every_deferred_proof_is_recorded_not_run() -> None:
    """A deferral is never a pass — that is the whole point of the status vocabulary."""
    artifact = {proof["id"]: proof for proof in _artifact()["proofs"]}
    for entry in _allowlist()["proofs"]:
        if entry["disposition"] != "deferred":
            continue
        recorded = artifact[entry["id"]]
        assert recorded["status"] == "not-run", (
            f"{entry['id']} is deferred to {entry.get('owner')} but the artifact records "
            f"{recorded['status']!r}"
        )
        assert recorded["detail"].startswith("not-run (deferred to"), recorded["detail"]


def test_the_recorded_bytes_obey_the_documented_size_floor() -> None:
    """Proof 7's numbers come from the run and are held to the same floor the replay asserts."""
    artifact = {proof["id"]: proof for proof in _artifact()["proofs"]}
    proof = artifact["P7"]
    assert proof["status"] == "pass"
    assert proof["bundle_bytes"] < proof["corpus_bytes"], (
        "the recorded bundle is not smaller than the corpus it was selected from"
    )
    ratio = proof["bundle_bytes"] / proof["corpus_bytes"]
    assert ratio <= runner.SIZE_FRACTION_FLOOR, (
        f"the recorded bundle is {ratio:.1%} of the corpus, above the "
        f"{runner.SIZE_FRACTION_FLOOR:.0%} floor — the artifact and the replay disagree"
    )


# ------------------------------------------------------- provenance: no volatile strings recorded
def test_the_recorded_commands_carry_no_interpreter_or_absolute_path() -> None:
    """A recorded command must be reproducible on another host and under another interpreter name.

    The gate runs the runner through `python3`; a developer runs it through `.venv/bin/python`.
    If the artifact records whichever interpreter happened to run it, `--check` disagrees with
    itself on the same tree (measured: rc 0 under one name, rc 1 under the other).
    """
    interpreters = {
        sys.executable,
        str(Path(sys.executable).resolve()),
        sys.base_prefix,
    }
    for proof in _artifact()["proofs"]:
        command = proof["command"]
        assert command.strip(), f"{proof['id']}: no command recorded"
        for interpreter in interpreters:
            if not interpreter or len(interpreter) < 5:
                continue
            assert interpreter not in command, (
                f"{proof['id']}: the recorded command embeds this host's interpreter "
                f"({interpreter!r}) — no other host or interpreter name can reproduce it: "
                f"{command}"
            )
        for token in command.split():
            assert not token.startswith("/"), (
                f"{proof['id']}: the recorded command embeds the absolute path {token!r} — the "
                f"documented invocation records `python3` and repo-relative paths: {command}"
            )
    p5 = {proof["id"]: proof for proof in _artifact()["proofs"]}["P5"]
    assert p5["status"] == "pass", p5["detail"]
    assert "python3 -m pytest" in p5["command"], (
        "P5's recorded command must name the interpreter the way a reader can retype it "
        f"(`python3 -m pytest …`), not the path of whichever Python ran the replay: {p5['command']}"
    )


def test_the_runner_normalizes_the_recorded_interpreter() -> None:
    """The one place a recorded runner is rendered: any Python becomes `python3`, nothing else moves."""
    assert (
        runner._recorded_runner((sys.executable, "-m", "pytest", "tests/test_x.py::t"))
        == "python3 -m pytest tests/test_x.py::t"
    )
    assert (
        runner._recorded_runner(("/usr/bin/python3.11", "-m", "pytest"))
        == "python3 -m pytest"
    )
    assert runner._recorded_runner(("pytest", "-q")) == "pytest -q", (
        "a non-Python runner must be recorded verbatim"
    )


def _check_with(executable) -> subprocess.CompletedProcess:
    """Run `--check` against the COMMITTED artifact under the given interpreter path."""
    return subprocess.run(
        [str(executable), str(RUNNER), "--check", "--artifact", str(ARTIFACT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
        },
        timeout=900,
    )


def test_the_artifact_check_agrees_under_a_differently_named_interpreter() -> None:
    """Defect pin: the SAME tree and the SAME committed artifact under BOTH interpreter names.

    `sys.executable` is `…/python3` for the gate and `…/python` for a developer (both ship in a
    venv). If the recorded command carries whichever path ran it, `--check` disagrees with itself
    on one tree — measured: rc 0 under one name, rc 1 under the other, on the same commit.
    The second name is resolved from a REAL interpreter of the same installation: a symlink outside
    the venv loses `site-packages` (and with it lxml and pytest), so the probe refuses any
    candidate that cannot import the XSD processor.
    """
    first = _check_with(sys.executable)
    assert first.returncode == 0, (
        f"the committed artifact disagrees with a run of this tree under {sys.executable}:\n"
        + first.stdout
        + first.stderr
    )

    candidates = [
        Path(sys.executable).with_name("python"),
        Path(shutil.which("python") or ""),
    ]
    usable = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import lxml.etree"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            usable.append(candidate)
    if not usable:
        pytest.skip(
            "no second interpreter name of this installation can import lxml.etree, so the "
            "two-name pin cannot be exercised here; the artifact-level provenance assertions "
            "above still hold"
        )
    second = _check_with(usable[0])
    assert second.returncode == 0, (
        f"the committed artifact disagrees with a run of the same tree through {usable[0]} "
        f"instead of {sys.executable} — the recorded provenance is volatile:\n"
        + second.stdout
        + second.stderr
    )


# ----------------------------------------------------- falsifiers: drive the verdict, as a process
def _replay(tmp_path: Path, specs_root: Path, name: str = "artifact.json"):
    """Run the runner as a SUBPROCESS through its documented knobs (never a test hook)."""
    artifact = tmp_path / name
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--specs-root",
            str(specs_root),
            "--artifact",
            str(artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=900,
    )
    report = (
        json.loads(artifact.read_text(encoding="utf-8")) if artifact.is_file() else None
    )
    return completed, report


def _statuses(report: dict) -> dict:
    return {proof["id"]: proof["status"] for proof in report["proofs"]}


def test_the_control_replay_passes_through_the_documented_knobs(tmp_path: Path) -> None:
    """The control half of the falsifier: the real corpus, a scratch artifact, rc 0 and pass."""
    completed, report = _replay(tmp_path, REPO_ROOT / "specs")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert report is not None, "the control run recorded no artifact"
    assert report["verdict"] == "pass", report["required_not_passing"]
    assert report["required_not_passing"] == []
    assert {
        pid for pid, status in _statuses(report).items() if status == "pass"
    } == set(runner.REPLAYS), (
        "the control run did not actually pass every implemented replay"
    )


def test_a_required_proof_forced_to_not_run_fails_the_verdict(tmp_path: Path) -> None:
    """`not-run` is never a pass: an empty corpus must make the verdict non-zero and NAME the proof."""
    empty = tmp_path / "empty-corpus"
    empty.mkdir()
    completed, report = _replay(tmp_path, empty)
    assert completed.returncode != 0, (
        "an empty corpus left the verdict passing — `not-run` is being read as a pass:\n"
        + completed.stdout
    )
    assert report is not None
    assert report["verdict"] == "fail"
    statuses = _statuses(report)
    not_run = [
        pid for pid in report["required_not_passing"] if statuses[pid] == "not-run"
    ]
    assert not_run, (
        "the verdict failed but named no REQUIRED proof as `not-run`: "
        f"{report['required_not_passing']} {statuses}"
    )
    assert "required proofs not passing" in completed.stderr, completed.stderr
    for pid in not_run:
        assert pid in completed.stderr, (
            f"{pid} did not run, so the failure message must name it: {completed.stderr}"
        )


def test_a_required_proof_forced_to_fail_fails_the_verdict(tmp_path: Path) -> None:
    """A corpus that disagrees with the committed view must FAIL a required proof, not pass it."""
    corpus = tmp_path / "mutated-specs"
    shutil.copytree(REPO_ROOT / "specs", corpus)
    target = corpus / "spec-verbs.xml"
    text = target.read_text(encoding="utf-8")
    original = "assembly is never a silent whole-corpus dump."
    assert original in text, "the corpus sentence this falsifier edits is gone"
    replacement = "an assembly always announces the whole corpus it copies."
    assert original not in replacement, (
        "the replacement must not contain the pre-edit text"
    )
    target.write_text(text.replace(original, replacement, 1), encoding="utf-8")

    completed, report = _replay(tmp_path, corpus, name="fail-artifact.json")
    assert completed.returncode != 0, (
        "a corpus that no longer matches the committed view left the verdict passing:\n"
        + completed.stdout
    )
    assert report is not None
    assert report["verdict"] == "fail"
    assert "P2" in report["required_not_passing"], report["required_not_passing"]
    p2 = {proof["id"]: proof for proof in report["proofs"]}["P2"]
    assert p2["status"] == "fail", (
        f"P2 should FAIL on a diverged corpus, got {p2['status']}"
    )
    assert p2["detail"].startswith("failed:"), p2["detail"]


def test_check_mode_is_red_when_the_artifact_is_stale(tmp_path: Path) -> None:
    """`--check` must COMPARE, not merely re-run: a stale artifact against a clean corpus is rc 1."""
    corpus = tmp_path / "stale-specs"
    shutil.copytree(REPO_ROOT / "specs", corpus)
    target = corpus / "spec-verbs.xml"
    text = target.read_text(encoding="utf-8")
    original = "assembly is never a silent whole-corpus dump."
    assert original in text
    target.write_text(
        text.replace(
            original, "an assembly always announces the whole corpus it copies.", 1
        ),
        encoding="utf-8",
    )
    stale, stale_report = _replay(tmp_path, corpus, name="stale-artifact.json")
    assert stale_report is not None
    assert stale_report["verdict"] == "fail", (
        "the fixture artifact is not the stale one"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--check",
            "--specs-root",
            str(REPO_ROOT / "specs"),
            "--artifact",
            str(tmp_path / "stale-artifact.json"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
        },
        timeout=900,
    )
    assert completed.returncode != 0, (
        "a FAILED artifact recorded for a different corpus was accepted by `--check` on the real "
        "one — the check is not comparing anything:\n" + completed.stdout
    )
    assert "disagrees with this run" in completed.stderr, completed.stderr


def _checked(tmp_path: Path, artifact: Path) -> subprocess.CompletedProcess:
    """Drive `--check` as a SUBPROCESS through the documented knobs, against a chosen artifact."""
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--check",
            "--specs-root",
            str(REPO_ROOT / "specs"),
            "--artifact",
            str(artifact),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
        },
        timeout=900,
    )


def _edited_artifact(tmp_path: Path, mutate, name: str) -> Path:
    """Copy the committed artifact through `mutate`, leaving the tree's real record untouched."""
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    mutate(payload)
    target = tmp_path / name
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def test_the_check_signature_covers_recorded_byte_counts() -> None:
    """The signature must carry the measured bytes, and must NOT carry the per-run timings."""
    signature = runner._signature(_artifact())
    assert signature["proofs"], "the signature carries no proofs"
    for proof in signature["proofs"]:
        assert "corpus_bytes" in proof and "bundle_bytes" in proof, (
            "the check signature omits corpus_bytes/bundle_bytes, so a tampered or stale byte "
            f"count is invisible to `--check`: {sorted(proof)}"
        )
        assert "latency_ms" not in proof, (
            "latency_ms is a timing of one run; comparing it would make `--check` red on a "
            "byte-identical tree"
        )


def test_check_mode_is_red_when_a_recorded_byte_count_drifts(tmp_path: Path) -> None:
    """A tampered byte count on a still-`pass` proof is a disagreement, not a detail (AUG-137 AC3)."""

    def tamper_numbers(payload: dict) -> None:
        for proof in payload["proofs"]:
            if proof["id"] == "P7":
                assert proof["status"] == "pass", proof["status"]
                proof["corpus_bytes"] = 1
                proof["bundle_bytes"] = 99999
                return
        raise AssertionError("the artifact no longer records P7")

    artifact = _edited_artifact(tmp_path, tamper_numbers, "numbers-drift.json")
    completed = _checked(tmp_path, artifact)
    assert completed.returncode != 0, (
        "`--check` accepted an artifact whose recorded P7 byte counts were tampered to "
        "corpus_bytes=1/bundle_bytes=99999 — the recorded numbers are never compared:\n"
        + completed.stdout
    )
    assert "disagrees with this run" in completed.stderr, completed.stderr
    assert "corpus_bytes" in completed.stderr, (
        "the refusal must name the drifting field, so a reader can tell numbers from verdicts: "
        + completed.stderr
    )


def test_check_mode_ignores_only_the_timings(tmp_path: Path) -> None:
    """Control: a timing-only edit stays green, so the byte arm is not a proxy for comparing runs."""

    def tamper_latency(payload: dict) -> None:
        for proof in payload["proofs"]:
            proof["latency_ms"] = 999999.9

    artifact = _edited_artifact(tmp_path, tamper_latency, "latency-drift.json")
    completed = _checked(tmp_path, artifact)
    assert completed.returncode == 0, (
        "a latency-only edit made `--check` red — timings are a snapshot of one run and must not "
        "be compared, or the arm flakes on every byte-identical tree:\n"
        + completed.stderr
    )
    assert "agrees with this run" in completed.stdout, completed.stdout


def test_check_mode_refuses_a_missing_artifact(tmp_path: Path) -> None:
    """A gate arm that passes on a missing record proves nothing: rc 1, and the fix is named."""
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--check",
            "--specs-root",
            str(REPO_ROOT / "specs"),
            "--artifact",
            str(tmp_path / "never-recorded.json"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
        },
        timeout=900,
    )
    assert completed.returncode != 0, completed.stdout
    assert "is missing — record a run" in completed.stderr, completed.stderr


def test_gate_arm_runs_the_proof_runner_and_names_its_test_files() -> None:
    """The runner must run on every gate, and neither file this row's proofs live in may drop out."""
    text = GATE.read_text(encoding="utf-8")
    assert "scripts/replay_prd_proofs.py --check" in text, (
        "tests/gate.sh does not run the PRD proof runner — the proofs would be unrun"
    )
    arm = [line for line in text.splitlines() if "python3 -m pytest" in line]
    assert arm, "tests/gate.sh must have a pytest arm"
    for name in ("tests/test_prd_proofs.py", "tests/test_spec_corpus_view.py"):
        assert name in arm[0], (
            f"{name} is not in the gate's pytest arm — its proofs would never be executed by CI "
            "or the guard"
        )
