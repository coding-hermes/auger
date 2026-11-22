"""AUG-194/AUG-195: the commit path must REFUSE lost board rows or baseline ids.

Commit 45a29b9 rewrote ``.coding-hermes/board/tasks.jsonl`` from 297 rows to 2 — the two rows it
had just closed. 295 rows vanished for ~3 minutes and nothing on the commit path noticed, because
``boardctl validate`` grades a short board as VALID. A comparison of row counts and ids against a
baseline blob can see row loss; ``scripts/board_shrink_check.py`` makes that comparison and
``scripts/install_board_guard.py`` puts it in the pre-commit hook.

Every arm here runs against a SCRATCH repository (its own ``git init``, its own
``core.hooksPath``) — never against this worktree's commit path, and never against the shared
``.git/hooks`` of the primary checkout:

  AC1 shrink is refused ................ test_shrink_is_refused_and_the_commit_does_not_land
                                        test_deleting_the_board_is_refused
  missing ids are refused .............. test_same_count_replacement_is_refused_naming_missing_ids
                                        test_missing_id_is_refused_even_without_a_count_drop
                                        test_recent_mode_refuses_missing_ids_without_a_count_drop
  AC2 legit traffic passes ............. test_append_and_same_count_edit_pass
                                        test_adding_the_board_for_the_first_time_passes
                                        test_single_row_in_place_edit_passes
                                        test_reordering_surviving_ids_passes
                                        test_append_preserving_baseline_ids_passes
  AC3 override is explicit ............. test_override_lands_and_still_names_counts_and_ids
                                        test_same_count_replacement_override_is_loud
                                        test_override_does_not_excuse_a_parse_failure
  AC4 quarantine + attribution ......... test_the_hook_only_checks_when_the_board_is_staged
                                        test_the_real_297_row_board_rewrite_is_quarantined
  AC5 boardctl still grades it ......... (verified out of band: `boardctl validate` on the real
                                          board and on a scratch copy — see the AUG-194 report)
  post-commit mode (CI) ................ test_recent_mode_compares_a_commit_to_its_parent
  fail closed on a shallow clone ....... test_missing_parent_fails_closed_naming_the_deepen_hint
  pre-push mode (AUG-196, AC1-AC3) ..... test_a_no_verify_shrink_commit_is_refused_at_the_push_path
                                        and the override / skip / empty-stdin / parse-failure /
                                        installer-mode / default-mode-no-change arms below —
                                        every push arm drives the REAL generated hook block via
                                        `git push` against a scratch LOCAL bare remote.
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


def run(repo, *args, env_extra=None, input_text=None):
    return subprocess.run(
        args,
        cwd=repo,
        text=True,
        capture_output=True,
        env=env(**(env_extra or {})),
        input=input_text,
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


def test_same_count_replacement_is_refused_naming_missing_ids(repo):
    before = head(repo)
    stage_board(repo, ("X", "Y", "Z"))  # 3 different rows, same count
    result = git(repo, "commit", "-m", "wholesale same-count replacement")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED baseline=3 candidate=3 lost ids=A, B, C" in result.stderr
    assert head(repo) == before


@pytest.mark.parametrize("ids", [("A", "B", "X"), ("A", "B", "X", "Y")])
def test_missing_id_is_refused_even_without_a_count_drop(repo, ids):
    before = head(repo)
    stage_board(repo, ids)
    result = git(repo, "commit", "-m", "replace one id")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"REFUSED baseline=3 candidate={len(ids)} lost ids=C" in result.stderr
    assert head(repo) == before


def test_single_row_in_place_edit_passes(repo):
    board = repo / BOARD
    original = board.read_text().splitlines()
    lines = original.copy()
    changed = json.loads(lines[1])
    changed.update(status="complete", notes="verified", commit_hash="a" * 40)
    lines[1] = json.dumps(changed)
    board.write_text("\n".join(lines) + "\n")
    assert lines[0] == original[0] and lines[2] == original[2]
    assert git(repo, "add", str(BOARD)).returncode == 0
    result = git(repo, "commit", "-m", "edit a single surviving row")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK baseline=3 candidate=3 lost ids=(none)" in result.stdout + result.stderr
    assert git(repo, "show", f"HEAD:{BOARD}").stdout == board.read_text()


def test_reordering_surviving_ids_passes(repo):
    stage_board(repo, ("C", "A", "B"))
    result = git(repo, "commit", "-m", "reorder surviving rows")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK baseline=3 candidate=3 lost ids=(none)" in result.stdout + result.stderr


def test_append_preserving_baseline_ids_passes(repo):
    stage_board(repo, ("A", "B", "C", "D"))
    result = git(repo, "commit", "-m", "append with every baseline id retained")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK baseline=3 candidate=4 lost ids=(none)" in result.stdout + result.stderr


@pytest.mark.parametrize("override", ["1", "true", "yes"])
def test_same_count_replacement_override_is_loud(repo, override):
    stage_board(repo, ("X", "Y", "Z"))
    result = git(
        repo,
        "commit",
        "-m",
        "deliberate replacement",
        env_extra={"BOARD_ALLOW_SHRINK": override},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert (
        "NOTICE BOARD_ALLOW_SHRINK override: baseline=3 candidate=3 lost ids=A, B, C"
        in output
    )
    committed = git(repo, "show", f"HEAD:{BOARD}").stdout
    assert [json.loads(line)["id"] for line in committed.splitlines()] == [
        "X",
        "Y",
        "Z",
    ]


@pytest.mark.parametrize("ids", [("X", "Y", "Z"), ("A", "B", "X", "Y")])
def test_recent_mode_refuses_missing_ids_without_a_count_drop(repo_without_hook, ids):
    repo = repo_without_hook
    stage_board(repo, ids)
    assert git(repo, "commit", "-qm", "replacement without a hook").returncode == 0
    result = run(repo, sys.executable, str(CHECKER), "--recent")
    assert result.returncode == 1, result.stdout + result.stderr
    missing = "A, B, C" if len(ids) == 3 else "C"
    assert (
        f"REFUSED baseline=3 candidate={len(ids)} lost ids={missing}" in result.stderr
    )


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


def test_the_real_297_row_board_rewrite_is_quarantined(tmp_path):
    """The incident, replayed: 45a29b9 kept 2 of 297 rows."""
    board_text = (ROOT / BOARD).read_text(encoding="utf-8")
    lines = [line for line in board_text.splitlines() if line.strip()]
    assert len(lines) >= 297, (
        f"the tracked board should hold ~298 rows, found {len(lines)}"
    )
    # Seed surviving real ids: loading the full board must be growth, not replacement of A/B/C.
    repo = make_repo(tmp_path, ids=tuple(json.loads(line)["id"] for line in lines[:3]))

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


# ------------------------------------------------------------- AUG-196: the push path


BEGIN = "# >>> board-shrink managed block >>>"
END = "# <<< board-shrink managed block <<<"
PUSH_BEGIN = "# >>> board-shrink-push managed block >>>"
PUSH_END = "# <<< board-shrink-push managed block <<<"
NULL_SHA = "0" * 40


def pre_push_line(local_ref, local_sha, remote_ref, remote_sha):
    """One pre-push stdin line in git's wire order (refs first, oids second)."""
    return f"{local_ref} {local_sha} {remote_ref} {remote_sha}"


def make_push_pair(tmp_path):
    """A scratch repo, a LOCAL bare remote, and a clone of it carrying BOTH hooks.

    The clone installs the pre-commit guard too, so a shrink commit there NEEDS
    --no-verify — the push arm then grades the real bypass, not a hook-less commit.
    """
    repo = make_repo(tmp_path, install=False)
    origin = tmp_path / "origin.git"
    assert (
        git(repo, "clone", "--bare", "--quiet", str(repo), str(origin)).returncode == 0
    )
    clone = tmp_path / "clone"
    assert git(tmp_path, "clone", "--quiet", str(origin), str(clone)).returncode == 0
    installed = run(clone, sys.executable, str(INSTALLER))
    assert installed.returncode == 0, installed.stderr
    push_install = run(clone, sys.executable, str(INSTALLER), "--hook", "pre-push")
    assert push_install.returncode == 0, push_install.stderr
    return clone


def clone_branch(clone):
    return git(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def clone_oids(clone):
    branch = clone_branch(clone)
    local = git(clone, "rev-parse", "HEAD").stdout.strip()
    remote = git(clone, "rev-parse", f"origin/{branch}").stdout.strip()
    return branch, local, remote


def remote_board_sha(clone):
    """The sha the remote currently holds for the tracked branch."""
    branch = clone_branch(clone)
    listing = git(clone, "ls-remote", "origin", f"refs/heads/{branch}").stdout
    return listing.split()[0]


def test_a_no_verify_shrink_commit_is_refused_at_the_push_path(tmp_path):
    """AC2: the incident shape — --no-verify lands the shrink locally, the PUSH refuses it."""
    clone = make_push_pair(tmp_path)
    remote_before = remote_board_sha(clone)
    stage_board(clone, ("A",))
    committed = git(clone, "commit", "-qm", "shrink", "--no-verify")
    assert committed.returncode == 0, committed.stdout + committed.stderr
    result = git(clone, "push", "--quiet", "origin", "HEAD")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED baseline=3 candidate=1 lost ids=B, C" in result.stderr
    # The refusal is real: the remote branch still carries the baseline sha.
    assert remote_board_sha(clone) == remote_before


def test_an_append_push_passes_and_lands_the_new_row(tmp_path):
    clone = make_push_pair(tmp_path)
    stage_board(clone, ("A", "B", "C", "D"))
    # The pre-commit guard is installed in the clone too: a plain append commits normally.
    committed = git(clone, "commit", "-qm", "append D")
    assert committed.returncode == 0, committed.stdout + committed.stderr
    result = git(clone, "push", "--quiet", "origin", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    branch = clone_branch(clone)
    assert git(clone, "rev-parse", f"origin/{branch}").stdout.strip() == head(clone)


def test_a_push_that_does_not_touch_the_board_passes(tmp_path):
    clone = make_push_pair(tmp_path)
    (clone / "notes.txt").write_text("unrelated\n")
    assert git(clone, "add", "notes.txt").returncode == 0
    assert git(clone, "commit", "-qm", "unrelated change").returncode == 0
    result = git(clone, "push", "--quiet", "origin", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFUSED" not in result.stdout + result.stderr


def test_pre_push_refs_that_leave_the_board_identical_are_skipped_silently(tmp_path):
    """Skip-when-unrelated lives in the checker per line, not in a cwd heuristic."""
    clone = make_push_pair(tmp_path)
    branch, local, _ = clone_oids(clone)
    stdin = pre_push_line(f"refs/heads/{branch}", local, f"refs/heads/{branch}", local)
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text=stdin + "\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "", "an unchanged board must not be graded at all"


def test_pre_push_skips_a_deletion_line_and_grades_the_rest_of_the_push(tmp_path):
    """A remote deletion pushes nothing of ours: that line is skipped, the rest is graded."""
    clone = make_push_pair(tmp_path)
    stage_board(clone, ("A", "B", "C", "D"))
    assert git(clone, "commit", "-qm", "append D", "--no-verify").returncode == 0
    branch, local, remote = clone_oids(clone)
    stdin = "\n".join(
        [
            # a deletion of some other remote ref, in git's wire order
            pre_push_line(f"refs/heads/{branch}", NULL_SHA, "refs/tags/old", remote),
            # and the real pair the push grades
            pre_push_line(
                f"refs/heads/{branch}", local, f"refs/heads/{branch}", remote
            ),
        ]
    )
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text=stdin + "\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK baseline=3 candidate=4" in result.stdout


def test_pre_push_exits_zero_when_every_line_is_a_delete(tmp_path):
    clone = make_push_pair(tmp_path)
    branch, _local, remote = clone_oids(clone)
    stdin = "\n".join(
        (
            pre_push_line(
                f"refs/heads/{branch}", NULL_SHA, f"refs/heads/{branch}", remote
            ),
            pre_push_line("refs/tags/old", NULL_SHA, "refs/tags/old", remote),
        )
    )
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text=stdin + "\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_shrink_push_with_the_override_is_loud(tmp_path):
    """AC3: the push override is explicit AND still names the counts and lost ids."""
    clone = make_push_pair(tmp_path)
    stage_board(clone, ("A",))
    assert git(clone, "commit", "-qm", "shrink", "--no-verify").returncode == 0
    result = git(
        clone,
        "push",
        "--quiet",
        "origin",
        "HEAD",
        env_extra={"BOARD_ALLOW_SHRINK": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert (
        "NOTICE BOARD_ALLOW_SHRINK override: baseline=3 candidate=1 lost ids=B, C"
        in output
    )
    # the push LANDED with the prune
    branch = clone_branch(clone)
    assert git(clone, "rev-parse", f"origin/{branch}").stdout.strip() == head(clone)


def test_the_override_does_not_excuse_a_parse_failure_at_the_push_path(tmp_path):
    clone = make_push_pair(tmp_path)
    (clone / BOARD).write_text("{not json\n")
    assert git(clone, "add", str(BOARD)).returncode == 0
    assert git(clone, "commit", "-qm", "torn row", "--no-verify").returncode == 0
    result = git(
        clone,
        "push",
        "--quiet",
        "origin",
        "HEAD",
        env_extra={"BOARD_ALLOW_SHRINK": "1"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "parse failures baseline=0 candidate=1" in result.stderr


def test_pre_push_fails_closed_on_empty_stdin(tmp_path):
    """Empty stdin never happens on a real push — refuse blind, never pass blind."""
    clone = make_push_pair(tmp_path)
    result = run(clone, sys.executable, str(CHECKER), "--pre-push", input_text="")
    assert result.returncode == 1
    assert "no ref lines on stdin" in result.stderr


def test_pre_push_fails_closed_on_an_unresolvable_ref(tmp_path):
    clone = make_push_pair(tmp_path)
    missing = "a" * 40
    stdin = pre_push_line("refs/heads/master", missing, "refs/heads/master", NULL_SHA)
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text=stdin + "\n"
    )
    assert result.returncode == 1
    assert f"cannot resolve {missing}" in result.stderr
    assert "git fetch --deepen=1" in result.stderr


def test_pre_push_fails_closed_on_an_unparseable_stdin_line(tmp_path):
    clone = make_push_pair(tmp_path)
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text="garbage line\n"
    )
    assert result.returncode == 1
    assert "cannot parse" in result.stderr


def test_pre_push_tolerates_the_swapped_oid_first_field_order(tmp_path):
    """The board row names `<local_sha> <remote_sha> <local_ref> <remote_ref>`; grade it too."""
    clone = make_push_pair(tmp_path)
    stage_board(clone, ("A",))
    assert git(clone, "commit", "-qm", "shrink", "--no-verify").returncode == 0
    branch, local, remote = clone_oids(clone)
    stdin = f"{local} {remote} refs/heads/{branch} refs/heads/{branch}"
    result = run(
        clone, sys.executable, str(CHECKER), "--pre-push", input_text=stdin + "\n"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED baseline=3 candidate=1 lost ids=B, C" in result.stderr


def test_installer_pre_push_mode_creates_the_hook_and_is_idempotent(tmp_path):
    """Mirror of the pre-commit installer legs: create, preserve, repeat."""
    repo = make_repo(tmp_path, install=True)
    push_hook = repo / "hooks" / "pre-push"
    assert not push_hook.exists(), (
        "the test starts without it — the installer must CREATE it"
    )
    result = run(repo, sys.executable, str(INSTALLER), "--hook", "pre-push")
    assert result.returncode == 0, result.stderr
    content = push_hook.read_text()
    assert content.startswith("#!/bin/sh"), "a created hook carries the shebang"
    assert os.access(push_hook, os.X_OK), (
        "git refuses to run a non-executable pre-push hook"
    )
    assert content.count(PUSH_BEGIN) == 1 and content.count(PUSH_END) == 1
    assert "--pre-push" in content
    assert BEGIN not in content, "the pre-commit markers never leak into the push block"

    push_hook.write_text(content + "echo preserved\n")
    for _ in range(2):
        assert (
            run(repo, sys.executable, str(INSTALLER), "--hook", "pre-push").returncode
            == 0
        )
    content = push_hook.read_text()
    assert content.count(PUSH_BEGIN) == 1 and content.count(PUSH_END) == 1
    assert content.count("echo preserved") == 1
    assert content.startswith("#!")


def test_installer_pre_push_refuses_a_malformed_block_and_leaves_it_intact(tmp_path):
    repo = make_repo(tmp_path, install=False)
    assert git(repo, "config", "core.hooksPath", "hooks").returncode == 0
    hook = repo / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    broken = "#!/bin/sh\n" + PUSH_BEGIN + "\nno closing marker\n"
    hook.write_text(broken)
    hook.chmod(0o755)
    result = run(repo, sys.executable, str(INSTALLER), "--hook", "pre-push")
    assert result.returncode == 1
    assert "malformed managed block" in result.stderr
    assert "pre-push" in result.stderr
    assert hook.read_text() == broken


def test_the_default_mode_still_installs_the_pre_commit_hook(tmp_path):
    """No behavior change for existing callers: default mode never touches the push path."""
    repo = make_repo(tmp_path, install=False)
    assert git(repo, "config", "core.hooksPath", "hooks").returncode == 0
    assert not (repo / "hooks" / "pre-push").exists()
    assert run(repo, sys.executable, str(INSTALLER)).returncode == 0
    pre_commit = (repo / "hooks" / "pre-commit").read_text()
    assert pre_commit.count(BEGIN) == 1 and pre_commit.count(END) == 1
    assert "--pre-push" not in pre_commit
    assert not (repo / "hooks" / "pre-push").exists(), (
        "default mode must not create the pre-push hook"
    )
    # and the two modes stay in their own hook files
    assert (
        run(repo, sys.executable, str(INSTALLER), "--hook", "pre-push").returncode == 0
    )
    push_content = (repo / "hooks" / "pre-push").read_text()
    assert push_content.count(PUSH_BEGIN) == 1
    assert pre_commit == (repo / "hooks" / "pre-commit").read_text(), (
        "push-mode install must not rewrite the pre-commit hook"
    )


def test_the_print_mode_prints_each_hook_block_without_changing_anything():
    pre_commit = run(ROOT, sys.executable, str(INSTALLER), "--print")
    assert pre_commit.returncode == 0
    assert BEGIN in pre_commit.stdout and END in pre_commit.stdout
    assert PUSH_BEGIN not in pre_commit.stdout
    assert pre_commit.stdout.endswith(END + "\n")

    pre_push = run(
        ROOT, sys.executable, str(INSTALLER), "--print", "--hook", "pre-push"
    )
    assert pre_push.returncode == 0
    assert PUSH_BEGIN in pre_push.stdout and PUSH_END in pre_push.stdout
    assert "--pre-push" in pre_push.stdout
    assert BEGIN not in pre_push.stdout
