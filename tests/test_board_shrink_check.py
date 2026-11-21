"""AUG-194: the commit path must REFUSE a board shrink.

Commit 45a29b9 rewrote ``.coding-hermes/board/tasks.jsonl`` from 297 rows to 2 — the two rows it
had just closed. 295 rows vanished for ~3 minutes and nothing on the commit path noticed, because
``boardctl validate`` grades a short board as VALID. Only a row-count comparison against a
baseline blob can see row loss; ``scripts/board_shrink_check.py`` makes that comparison and
``scripts/install_board_guard.py`` puts it in the pre-commit hook.

Every arm here runs against a SCRATCH repository (its own ``git init``, its own
``core.hooksPath``) — never against this worktree's commit path, and never against the shared
``.git/hooks`` of the primary checkout:

  AC1 shrink is refused ................ test_shrink_is_refused_and_the_commit_does_not_land
                                        test_deleting_the_board_is_refused
  AC2 legit traffic passes ............. test_append_and_same_count_edit_pass
                                        test_adding_the_board_for_the_first_time_passes
                                        test_same_count_replacement_is_the_documented_blind_spot
  AC3 override is explicit ............. test_override_lands_and_still_names_counts_and_ids
                                        test_override_does_not_excuse_a_parse_failure
  AC4 quarantine + attribution ......... test_the_hook_only_checks_when_the_board_is_staged
                                        test_the_real_297_row_board_rewrite_is_quarantined
  AC5 boardctl still grades it ......... (verified out of band: `boardctl validate` on the real
                                          board and on a scratch copy — see the AUG-194 report)
  post-commit mode (CI) ................ test_recent_mode_compares_a_commit_to_its_parent
  fail closed on a shallow clone ....... test_missing_parent_fails_closed_naming_the_deepen_hint
  wiring, not decoration ............... test_the_gate_pytest_arm_names_this_file
                                        test_the_real_workflow_runs_the_check
                                        (plus four mutation legs on a copy of ci.yml)

ch:trace row=AUG-194 spec=none:no-spec-block-covers-a-commit-path-guard test=tests/test_board_shrink_check.py evidence=none:board-declares-evidence-git-verdict verdict=none:not-judged commit=none:proof-is-this-file
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BOARD = Path(".coding-hermes/board/tasks.jsonl")
CHECKER = Path("scripts/board_shrink_check.py")
INSTALLER = Path("scripts/install_board_guard.py")
GATE_SH = ROOT / "tests" / "gate.sh"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
GATE_STEP_MARKER = "bash tests/gate.sh"
SHRINK_STEP_MARKER = "scripts/board_shrink_check.py"

# A scratch repository must not inherit this host's git identity, hooks, or safe.directory list:
# every arm below would otherwise grade the machine instead of the change.
BASE_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean.update(BASE_ENV)
    clean.update(extra)
    return clean


def run(repo, *args, env_extra=None):
    return subprocess.run(
        args,
        cwd=repo,
        text=True,
        capture_output=True,
        env=env(**(env_extra or {})),
        timeout=120,
    )


def git(repo, *args, env_extra=None):
    return run(repo, "git", *args, env_extra=env_extra)


def write_board(repo, ids, extra=None):
    board = repo / BOARD
    board.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for ident in ids:
        row = {"id": ident}
        row.update(extra or {})
        lines.append(json.dumps(row))
    board.write_text("".join(line + "\n" for line in lines))


def make_repo(tmp_path, ids=("A", "B", "C"), board=True, install=True):
    """A scratch repository with the checker copied in and, optionally, the hook installed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "-q").returncode == 0
    (repo / "scripts").mkdir()
    for name in (CHECKER, INSTALLER):
        shutil.copy(ROOT / name, repo / name)
    (repo / "README.md").write_text("scratch\n")
    if board:
        write_board(repo, ids)
    assert git(repo, "add", ".").returncode == 0
    assert git(repo, "commit", "-qm", "initial").returncode == 0
    if install:
        hooks = repo / "hooks"
        assert git(repo, "config", "core.hooksPath", str(hooks)).returncode == 0
        installed = run(repo, sys.executable, str(INSTALLER))
        assert installed.returncode == 0, installed.stderr
    return repo


def stage_board(repo, ids, extra=None):
    write_board(repo, ids, extra=extra)
    assert git(repo, "add", str(BOARD)).returncode == 0


def head(repo):
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture
def repo_without_hook(tmp_path):
    return make_repo(tmp_path, install=False)


# --------------------------------------------------------------------------- AC1: refuse a shrink


def test_shrink_is_refused_and_the_commit_does_not_land(repo):
    before = head(repo)
    stage_board(repo, ("A",))
    result = git(repo, "commit", "-m", "shrink")
    assert result.returncode == 1, result.stdout + result.stderr
    # BOTH counts, and the ids that disappeared.
    assert "baseline=3 candidate=1 lost ids=B, C" in result.stderr
    assert "REFUSED" in result.stderr
    assert head(repo) == before


def test_deleting_the_board_is_refused(repo):
    before = head(repo)
    assert git(repo, "rm", "-q", str(BOARD)).returncode == 0
    result = git(repo, "commit", "-m", "delete the board")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "baseline=3 candidate=0" in result.stderr
    assert head(repo) == before


# ------------------------------------------------------------------- AC2: legitimate traffic


def test_append_and_same_count_edit_pass(repo):
    stage_board(repo, ("A", "B", "C", "D"))
    appended = git(repo, "commit", "-m", "append")
    assert appended.returncode == 0, appended.stdout + appended.stderr
    assert "OK" in appended.stdout or "OK" in appended.stderr

    stage_board(repo, ("A", "B", "C", "D"), extra={"updated": True})
    edited = git(repo, "commit", "-m", "edit one row in place")
    assert edited.returncode == 0, edited.stdout + edited.stderr


def test_adding_the_board_for_the_first_time_passes(tmp_path):
    """A board that does not exist at the baseline has 0 rows — adding it is growth, not shrink."""
    repo = make_repo(tmp_path, board=False)
    stage_board(repo, ("A", "B"))
    result = git(repo, "commit", "-m", "first board rows")
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_count_replacement_is_the_documented_blind_spot(repo):
    """The honest limit is stated in the header AND is really a limit — both are asserted."""
    source = (ROOT / CHECKER).read_text(encoding="utf-8")
    assert "SAME-COUNT wholesale replacement is NOT detected" in source, (
        "the checker must state its blind spot plainly in its header comment"
    )
    stage_board(repo, ("X", "Y", "Z"))  # 3 different rows, same count
    result = git(repo, "commit", "-m", "wholesale same-count replacement")
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------------------- AC3: explicit override


def test_override_lands_and_still_names_counts_and_ids(repo):
    stage_board(repo, ("A",))
    result = git(
        repo, "commit", "-m", "deliberate prune", env_extra={"BOARD_ALLOW_SHRINK": "1"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "NOTICE" in output
    assert "baseline=3 candidate=1 lost ids=B, C" in output
    rows = (repo / BOARD).read_text().splitlines()
    assert [json.loads(line)["id"] for line in rows if line.strip()] == ["A"]


def test_override_does_not_excuse_a_parse_failure(repo):
    before = head(repo)
    (repo / BOARD).write_text("{not json\n")
    assert git(repo, "add", str(BOARD)).returncode == 0
    result = git(
        repo, "commit", "-m", "torn row", env_extra={"BOARD_ALLOW_SHRINK": "1"}
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "parse failures baseline=0 candidate=1" in result.stderr
    assert head(repo) == before


def test_missing_parent_fails_closed_naming_the_deepen_hint(repo):
    """Shallow clone: nothing staged, no HEAD^ — the check must fail closed, never pass blind."""
    result = run(repo, sys.executable, str(CHECKER), "--recent")
    assert result.returncode == 1
    assert "git fetch --deepen=1" in result.stderr


# ------------------------------------------------------------------- AC4: quarantine, end to end


def test_the_hook_only_checks_when_the_board_is_staged(tmp_path):
    """The block is not decoration: it skips unrelated commits and blocks a board write."""
    repo = make_repo(tmp_path, install=False)
    stub = repo / "scripts" / "stub_checker.py"
    marker = repo / "scripts" / "stub.ran"
    stub.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "sys.exit(1)\n"
    )
    installed = run(
        repo, sys.executable, str(INSTALLER), "--checker", "scripts/stub_checker.py"
    )
    assert installed.returncode == 0, installed.stderr

    (repo / "notes.txt").write_text("unrelated\n")
    assert git(repo, "add", "notes.txt").returncode == 0
    unrelated = git(repo, "commit", "-m", "unrelated change")
    assert unrelated.returncode == 0, unrelated.stdout + unrelated.stderr
    assert not marker.exists(), (
        "the hook ran the checker for a commit that does not touch the board"
    )

    stage_board(repo, ("A", "B", "C", "D"))
    blocked = git(repo, "commit", "-m", "board append")
    assert blocked.returncode != 0
    assert marker.exists(), "the hook did not run the checker for a staged board write"


def test_the_real_297_row_board_rewrite_is_quarantined(repo):
    """The incident, replayed: 45a29b9 kept 2 of 297 rows."""
    board_text = (ROOT / BOARD).read_text(encoding="utf-8")
    lines = [line for line in board_text.splitlines() if line.strip()]
    assert len(lines) >= 297, (
        f"the tracked board should hold ~298 rows, found {len(lines)}"
    )

    (repo / BOARD).write_text(board_text)
    assert git(repo, "add", str(BOARD)).returncode == 0
    assert git(repo, "commit", "-m", "real board").returncode == 0, (
        "growing the board must pass"
    )

    before = head(repo)
    (repo / BOARD).write_text("\n".join(lines[:2]) + "\n")
    assert git(repo, "add", str(BOARD)).returncode == 0
    result = git(repo, "commit", "-m", "45a29b9 replay")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"baseline={len(lines)} candidate=2" in result.stderr
    assert head(repo) == before


def test_recent_mode_compares_a_commit_to_its_parent(repo_without_hook):
    """CI has nothing staged, so it grades HEAD against HEAD^. Both directions are proven."""
    repo = repo_without_hook
    stage_board(repo, ("A", "B", "C", "D"))
    assert git(repo, "commit", "-qm", "append").returncode == 0
    ok = run(repo, sys.executable, str(CHECKER), "--recent")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "baseline=3 candidate=4" in ok.stdout

    stage_board(repo, ("A",))
    assert (
        git(
            repo, "commit", "-qm", "shrink", env_extra={"BOARD_ALLOW_SHRINK": "1"}
        ).returncode
        == 0
    )
    refused = run(repo, sys.executable, str(CHECKER), "--recent")
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "baseline=4 candidate=1" in refused.stderr


def test_installer_is_idempotent_and_preserves_existing_hook(repo):
    hook = repo / "hooks" / "pre-commit"
    hook.write_text(hook.read_text() + "echo preserved\n")
    for _ in range(2):
        assert run(repo, sys.executable, str(INSTALLER)).returncode == 0
    content = hook.read_text()
    assert content.count("# >>> board-shrink managed block >>>") == 1
    assert content.count("# <<< board-shrink managed block <<<") == 1
    assert content.count("echo preserved") == 1
    assert content.startswith("#!"), "the block must never displace the shebang"


def test_installer_refuses_when_the_checker_is_missing(tmp_path):
    repo = make_repo(tmp_path, install=False)
    (repo / CHECKER).unlink()
    result = run(repo, sys.executable, str(INSTALLER))
    assert result.returncode == 1
    assert "checker not found" in result.stderr.lower()
    assert not (repo / "hooks" / "pre-commit").exists(), (
        "a refused install must not leave a hook behind that fails every commit"
    )


# ---------------------------------------------------------------------------- wiring: CI + gate


def test_the_gate_pytest_arm_names_this_file():
    """A proof outside gate.sh's pytest arm is never executed by the gate or by CI."""
    arm = [
        line
        for line in GATE_SH.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_board_shrink_check.py" in arm[0], (
        "tests/test_board_shrink_check.py is not in the gate's pytest arm — AC1-AC4 would be "
        "an unrun proof"
    )
    named = [token for token in arm[0].split() if token.startswith("tests/test_")]
    missing = [name for name in named if not (ROOT / name).is_file()]
    assert not missing, (
        f"tests/gate.sh's pytest arm names files that do not exist: {missing}"
    )


def load_steps(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = data["jobs"]["gate"]["steps"]
    assert isinstance(steps, list) and steps, f"no steps found in {path}"
    return steps


def workflow_findings(path: Path) -> list[str]:
    """Finding codes for one workflow file; empty means the shrink gate is wired and runnable."""
    steps = load_steps(path)
    findings: list[str] = []

    gate_idx = next(
        (i for i, s in enumerate(steps) if GATE_STEP_MARKER in (s.get("run") or "")),
        None,
    )
    if gate_idx is None:
        findings.append("missing-gate-step")
        gate_idx = len(steps)

    hits = [
        (i, s)
        for i, s in enumerate(steps)
        if SHRINK_STEP_MARKER in (s.get("run") or "")
    ]
    if not hits:
        findings.append("missing-shrink-step")
    else:
        if len(hits) > 1:
            findings.append("duplicate-shrink-steps")
        idx, step = hits[0]
        text = step["run"] or ""
        # CI has nothing staged: only the post-commit comparison can see a landed shrink.
        if "--recent" not in text:
            findings.append("not-post-commit-mode")
        if idx >= gate_idx:
            findings.append("shrink-step-after-gate")

    # The post-commit comparison needs HEAD^; checkout must not be depth-1.
    checkout = steps[0]
    with_block = checkout.get("with") or {}
    depth = with_block.get("fetch-depth")
    if depth is None:
        findings.append("checkout-default-depth")
    elif not isinstance(depth, int) or (depth != 0 and depth < 2):
        # 0 is git's "full history" — strictly more than the two commits the check needs.
        findings.append("checkout-too-shallow")
    return findings


def mutated(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    mutate(data["jobs"]["gate"]["steps"])
    out = tmp_path / "ci-mutated.yml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def shrink_step_index(steps):
    return next(
        i for i, s in enumerate(steps) if SHRINK_STEP_MARKER in (s.get("run") or "")
    )


def test_the_real_workflow_runs_the_check():
    assert workflow_findings(CI_YML) == [], (
        "ci.yml must run scripts/board_shrink_check.py --recent before the gate, from a "
        "checkout that carries the parent commit"
    )


def test_the_checkout_keeps_the_parent_commit(tmp_path):
    def too_shallow(steps):
        steps[0].setdefault("with", {})["fetch-depth"] = 1

    assert "checkout-too-shallow" in workflow_findings(mutated(tmp_path, too_shallow))


def test_the_shrink_step_runs_before_the_gate(tmp_path):
    def move_after_gate(steps):
        step = steps.pop(shrink_step_index(steps))
        gate_i = next(
            i for i, s in enumerate(steps) if GATE_STEP_MARKER in (s.get("run") or "")
        )
        steps.insert(gate_i + 1, step)

    assert "shrink-step-after-gate" in workflow_findings(
        mutated(tmp_path, move_after_gate)
    )


def test_the_shrink_step_uses_post_commit_mode(tmp_path):
    def drop_recent(steps):
        step = steps[shrink_step_index(steps)]
        step["run"] = re.sub(r"\s*--recent\b", "", step["run"])

    assert "not-post-commit-mode" in workflow_findings(mutated(tmp_path, drop_recent))


def test_removing_the_shrink_step_is_a_finding(tmp_path):
    def drop_step(steps):
        steps.pop(shrink_step_index(steps))

    assert "missing-shrink-step" in workflow_findings(mutated(tmp_path, drop_step))
