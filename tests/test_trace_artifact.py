"""AUG-141 / PRD AC-15: the sample end-to-end trace artifact is REAL and stays resolvable.

AC-15 asks for something the repository did not have: a trace a reviewer can actually follow —
"requirement through board row, code, test, verdict and commit" — proven by a sample artifact in
`docs/prd/evidence/`. A hand-written sample rots the day a test is renamed, so the sample is
GENERATED (`scripts/gen_trace_artifact.py`) and this file resolves every pointer in the generated
bytes independently of the generator's own resolvers:

  every doctrine field is present ......... test_the_artifact_carries_all_eleven_doctrine_fields
  the marker is the doctrine's marker ...... test_the_marker_reproduces_the_doctrine_order
  the shipped parser reads it ............. test_the_marker_parses_through_the_shipped_parser
  every pointer resolves (independently) ... test_every_pointer_resolves[<field>]
  the row is a real board row ............. test_the_row_is_a_row_in_the_tracked_board
  the spec pointer names its own file ..... test_the_spec_pointer_names_the_blocks_own_file
  the test pointer is a TRACKED file ...... test_the_test_pointer_is_tracked_by_git
  the commit pointer reaches HEAD ......... test_the_commit_pointer_is_an_ancestor_of_head
  no host provenance ...................... test_the_artifact_carries_no_host_provenance
  a double build is byte-identical ........ test_a_double_build_is_byte_identical
  `--check` passes on the committed file .. test_check_mode_passes_on_the_committed_artifact
  `--check` fails on a stale file ......... test_check_mode_fails_on_an_edited_artifact
  the tool REFUSES a bad pointer .......... test_the_generator_refuses_an_unresolvable_pointer
  the tool REFUSES a bare `none` .......... test_the_generator_refuses_a_bare_none
  the shipped checker accepts the marker .. test_the_shipped_checker_accepts_the_marker
  the command is documented ............... test_the_regeneration_command_is_documented
  the gate arm runs this file ............. test_gate_arm_runs_this_file

ch:trace row=AUG-141 spec=docs/prd/xml-spec-graph.md#ch:trace wave=none:no-wave-record test=tests/test_trace_artifact.py doc=docs/prd/xml-spec-graph.md prompt=none:no-committed-prompt evidence=docs/prd/evidence/AUG-136-trace.json witness=none:worker-self-report verdict=none:no-gitreins-verdict commit=none:pre-commit memory=none:no-memory-record
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SCRIPT = REPO_ROOT / "scripts" / "gen_trace_artifact.py"
ARTIFACT = REPO_ROOT / "docs" / "prd" / "evidence" / "AUG-136-trace.json"
SPEC_ROOT = REPO_ROOT / "specs"
BOARD = REPO_ROOT / ".coding-hermes" / "board" / "tasks.jsonl"
PRD = REPO_ROOT / "docs" / "prd" / "xml-spec-graph.md"
GATE = REPO_ROOT / "tests" / "gate.sh"
AUGER = REPO_ROOT / "auger.py"

#: Isolate throwaway repositories from this host's global git config: a global `core.hooksPath`
#: turns a scratch commit into someone else's hook.
SCRATCH_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

#: One resolver per doctrine field, written HERE rather than borrowed from the generator: a bug in
#: the generator's resolver must not be able to certify its own artifact.
TRACKED_FIELDS = ("doc", "evidence")


# ------------------------------------------------------------------------------------- helpers
def load_artifact(path: Path = ARTIFACT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_script(*args: str) -> subprocess.CompletedProcess:
    """Drive the real generator as a subprocess — the same way the documented command does."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
        timeout=180,
    )


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def is_tracked(relative: str) -> bool:
    done = git("ls-files", "--error-unmatch", relative)
    return done.returncode == 0


def assert_tracked(relative: str, why: str) -> None:
    """Both halves of the claim: the file EXISTS and git tracks it.

    `git ls-files` reads the INDEX, so a file deleted or moved in the worktree without a
    `git add`/`git mv` is still "tracked" — measured while proving this arm: moving the cited test
    aside left this check green. A pointer resolves only when a reviewer can OPEN the file AND check
    it out, so the existence test comes first.
    """
    assert (REPO_ROOT / relative).is_file(), (
        f"{relative} does not exist in the working tree — {why}"
    )
    assert is_tracked(relative), f"{relative} is not tracked by git — {why}"


def resolve_none(value: str) -> str:
    assert value.startswith("none:"), (
        f"{value!r} is not an explicit absence; an absent link must read none:<reason>"
    )
    assert value != "none:", "a bare `none:` gives no reason"
    assert len(value) > len("none:"), f"{value!r} states an absence without a reason"
    return "explicit-absence"


def resolve_field(name: str, value: str) -> str:
    """Resolve ONE doctrine field to a human-readable provenance, or fail the test.

    Deliberately independent of `scripts/gen_trace_artifact.py`: this is the second opinion the
    artifact needs, and it is what turns a renamed test into a red artifact test instead of a
    silently rotted sample.
    """
    assert isinstance(value, str) and value.strip(), f"{name}= is empty"
    assert " " not in value, f"{name}={value!r} is not a single token"
    if value.startswith("none:"):
        return resolve_none(value)

    if name == "row":
        rows = {
            row["id"]
            for row in (
                json.loads(line)
                for line in BOARD.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        assert value in rows, (
            f"row={value} is not a row in the tracked board {BOARD.name}"
        )
        return "board-row"

    if name == "spec":
        relative, _, block_id = value.partition("#")
        assert block_id, f"spec={value} names no block id (want file.xml#ID)"
        _root, _documents, blocks = auger.load_spec_corpus(SPEC_ROOT)
        block = next((item for item in blocks if item["id"] == block_id), None)
        assert block is not None, f"spec={value} names no block in {SPEC_ROOT.name}/"
        assert isinstance(block.get("source_file"), str) and relative.endswith(
            block["source_file"]
        ), (
            f"spec={value} names a file the block is not authored in "
            f"(it lives in {block['source_file']!r})"
        )
        return "spec-block"

    if name == "test":
        relative, _, selector = value.partition("::")
        assert_tracked(relative, "a trace must cite a test a reviewer can open")
        if selector:
            tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
            names = {
                node.name
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            }
            assert selector.split("::")[-1] in names, (
                f"test={value} names a selector {relative} does not define"
            )
        return "tracked-test"

    if name in TRACKED_FIELDS:
        assert_tracked(value, f"{name}= must name a committed artifact, not a promise")
        return "tracked-path"

    if name == "commit":
        assert git("cat-file", "-e", f"{value}^{{commit}}").returncode == 0, (
            f"commit={value} is not a commit in this repository"
        )
        assert git("merge-base", "--is-ancestor", value, "HEAD").returncode == 0, (
            f"commit={value} is not reachable from HEAD — the trace names a commit nobody can "
            "reach from the published tip"
        )
        return "git-commit"

    raise AssertionError(
        f"{name}={value} has no resolver in this repository: this artifact may only carry a "
        f"resolvable {name} pointer or an explicit none:<reason>"
    )


# ------------------------------------------------------------------------------------- the chain
def test_the_artifact_carries_all_eleven_doctrine_fields() -> None:
    artifact = load_artifact()
    missing = [name for name in auger.SPEC_TRACE_FIELDS if name not in artifact]
    assert not missing, (
        f"the artifact omits {missing}: the eleven-field marker is the closeout contract "
        "(docs/prd/xml-spec-graph.md, ch:trace)"
    )
    for name in auger.SPEC_TRACE_FIELDS:
        value = artifact[name]
        assert isinstance(value, str) and value, f"{name}= is empty"
        assert value != "none", f"{name}=none states an absence without a reason"


def test_the_marker_reproduces_the_doctrine_order() -> None:
    artifact = load_artifact()
    marker = artifact["marker"]
    assert isinstance(marker, list) and marker, "the artifact must carry its marker"
    assert marker[0] == "ch:trace", (
        f"the marker starts with {marker[0]!r}, not 'ch:trace'"
    )
    keys = [token.partition("=")[0] for token in marker[1:]]
    assert tuple(keys) == auger.SPEC_TRACE_FIELDS, (
        f"the marker's field order is {keys}, not the doctrine's {auger.SPEC_TRACE_FIELDS}"
    )
    assert dict(token.partition("=")[::2] for token in marker[1:]) == {
        name: artifact[name] for name in auger.SPEC_TRACE_FIELDS
    }, "the marker and the artifact's fields disagree — two copies of one claim"


def test_the_marker_parses_through_the_shipped_parser() -> None:
    artifact = load_artifact()
    rendered = " ".join(artifact["marker"])
    assert rendered.startswith("ch:trace "), rendered
    fields = auger.parse_trace_marker(rendered)
    assert set(fields) == set(auger.SPEC_TRACE_FIELDS), (
        f"the shipped parser dropped a field: {sorted(set(auger.SPEC_TRACE_FIELDS) - set(fields))}"
    )
    for name in auger.SPEC_TRACE_FIELDS:
        assert fields[name] == artifact[name], (
            f"marker {name}= disagrees with the artifact"
        )


@pytest.mark.parametrize("name", auger.SPEC_TRACE_FIELDS)
def test_every_pointer_resolves(name: str) -> None:
    artifact = load_artifact()
    assert resolve_field(name, artifact[name]), f"{name}= did not resolve"


def test_this_files_own_marker_is_complete() -> None:
    """This proof file's own marker is a claim too, and a WRAPPED marker is a partial one.

    Measured while shipping this file: the docstring marker was wrapped over four lines, and the
    shipped staged-diff checker read only the first one — `trace-missing-field` for
    commit/evidence/verdict. A marker is ONE line; this is the cheap guard against re-wrapping it.
    """
    lines = [line for line in (__doc__ or "").splitlines() if "ch:trace" in line]
    assert len(lines) == 1, (
        f"this file's docstring mentions ch:trace on {len(lines)} lines"
    )
    fields = auger.parse_trace_marker(lines[0])
    assert set(fields) == set(auger.SPEC_TRACE_FIELDS), (
        "this file's own marker must carry all eleven fields on ONE line, or the staged-diff "
        f"checker sees a partial claim (got {sorted(fields)})"
    )


def test_every_field_is_resolved_in_the_artifact_itself() -> None:
    """The artifact states HOW each pointer resolved, so a reader does not have to guess."""
    artifact = load_artifact()
    resolution = artifact.get("resolution")
    assert isinstance(resolution, dict), "the artifact must carry a resolution map"
    assert set(resolution) == set(auger.SPEC_TRACE_FIELDS), (
        f"resolution covers {sorted(resolution)}, not the eleven fields"
    )


def test_the_row_is_a_row_in_the_tracked_board() -> None:
    artifact = load_artifact()
    assert is_tracked(str(BOARD.relative_to(REPO_ROOT))), (
        "the board the row resolves against must itself be tracked, or the pointer proves nothing"
    )
    assert resolve_field("row", artifact["row"]) == "board-row"


def test_the_spec_pointer_names_the_blocks_own_file() -> None:
    artifact = load_artifact()
    assert artifact["spec"] != "none", "the spec pointer is the one link that must land"
    assert resolve_field("spec", artifact["spec"]) == "spec-block"


def test_the_test_pointer_is_tracked_by_git() -> None:
    artifact = load_artifact()
    assert resolve_field("test", artifact["test"]) == "tracked-test"


def test_the_commit_pointer_is_an_ancestor_of_head() -> None:
    artifact = load_artifact()
    assert resolve_field("commit", artifact["commit"]) == "git-commit"


def test_the_artifact_carries_no_host_provenance() -> None:
    """A sample another host cannot regenerate is a screenshot of one laptop."""
    text = ARTIFACT.read_text(encoding="utf-8")
    assert str(REPO_ROOT) not in text, (
        "the artifact embeds this checkout's absolute path"
    )
    assert "/home/" not in text, "the artifact embeds an absolute host path"
    assert sys.executable not in text, "the artifact embeds this interpreter's path"
    for token in text.split():
        assert not token.strip('",').startswith("/"), (
            f"the artifact embeds the absolute path {token!r}"
        )


# ----------------------------------------------------------------------------------- generation
def test_a_double_build_is_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for out in (first, second):
        done = run_script("--out", str(out))
        assert done.returncode == 0, done.stderr
    assert first.read_bytes() == second.read_bytes(), (
        "two builds over one tree disagree — the artifact is not reproducible"
    )
    assert first.read_bytes() == ARTIFACT.read_bytes(), (
        "the committed artifact is not what the generator produces now: re-run "
        "`python3 scripts/gen_trace_artifact.py`"
    )


def test_check_mode_passes_on_the_committed_artifact() -> None:
    done = run_script("--check")
    assert done.returncode == 0, (
        f"--check refused the committed artifact:\n{done.stderr}"
    )
    assert "match" in done.stdout.lower() or "ok" in done.stdout.lower(), done.stdout


def test_check_mode_fails_on_an_edited_artifact(tmp_path: Path) -> None:
    stale = tmp_path / "stale.json"
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    artifact["evidence"] = "docs/prd/xml-spec-graph-proofs.json"
    stale.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    done = run_script("--check", "--out", str(stale))
    assert done.returncode != 0, (
        "--check accepted an artifact whose evidence pointer does not match a fresh build"
    )
    assert "evidence" in (done.stdout + done.stderr), (
        f"the refusal must name the field that drifted, got:\n{done.stdout}{done.stderr}"
    )


def test_check_mode_fails_when_the_artifact_is_missing(tmp_path: Path) -> None:
    done = run_script("--check", "--out", str(tmp_path / "absent.json"))
    assert done.returncode != 0, "--check passed over an artifact that does not exist"


@pytest.mark.parametrize(
    "flag,bad_value,code",
    [
        ("--row", "AUG-999999", "unknown-trace-row"),
        ("--spec", "specs/spec-graph.xml#SPEC-GRAPH-NOT-A-BLOCK", "unknown-spec-id"),
        ("--spec", "specs/spec-verbs.xml#SPEC-GRAPH-CORPUS", "stale-spec-ref"),
        ("--test", "tests/test_does_not_exist.py", "unknown-test-ref"),
        (
            "--test",
            "tests/test_spec_corpus_view.py::no_such_selector",
            "stale-test-ref",
        ),
        ("--doc", "docs/prd/does-not-exist.md", "unknown-ref"),
        ("--evidence", "docs/prd/evidence/nope.json", "unknown-ref"),
        ("--commit", "0" * 40, "unknown-commit"),
        ("--wave", "run:invented", "unverifiable-ref"),
    ],
)
def test_the_generator_refuses_an_unresolvable_pointer(
    flag: str, bad_value: str, code: str, tmp_path: Path
) -> None:
    """Every pointer is checked, so a mutated artifact cannot be generated at all."""
    out = tmp_path / "refused.json"
    done = run_script(flag, bad_value, "--out", str(out))
    assert done.returncode == 2, (
        f"{flag} {bad_value} was accepted (rc={done.returncode}):\n{done.stdout}{done.stderr}"
    )
    assert f"spec error ({code})" in done.stderr, (
        f"{flag} {bad_value} must refuse with {code}, got:\n{done.stderr}"
    )
    assert not out.exists(), "a refused build must not leave a partial artifact behind"


def test_the_generator_refuses_an_untracked_pointer(tmp_path: Path) -> None:
    """A pointer at a file that exists but is not committed resolves for nobody else."""
    relative = f"docs/prd/untracked-trace-probe-{os.getpid()}.md"
    target = REPO_ROOT / relative
    assert not is_tracked(relative), f"{relative} is tracked — pick another probe name"
    target.write_text("# probe\n", encoding="utf-8")
    try:
        done = run_script("--doc", relative, "--out", str(tmp_path / "refused.json"))
    finally:
        target.unlink()
    assert done.returncode == 2, (
        f"an untracked doc was accepted:\n{done.stdout}{done.stderr}"
    )
    assert "spec error (untracked-ref)" in done.stderr, done.stderr


@pytest.mark.parametrize(
    "flag", ["--wave", "--witness", "--memory", "--verdict", "--prompt"]
)
def test_the_generator_refuses_a_bare_none(flag: str, tmp_path: Path) -> None:
    done = run_script(flag, "none", "--out", str(tmp_path / "refused.json"))
    assert done.returncode == 2, (
        f"{flag} none was accepted:\n{done.stdout}{done.stderr}"
    )
    assert "unexplained-absent-field" in done.stderr, done.stderr


# ------------------------------------------------------------------------------- the real checker
def test_the_shipped_checker_accepts_the_marker(tmp_path: Path) -> None:
    """The marker must survive the checker GitReins actually invokes, not just this file."""
    artifact = load_artifact()
    marker = " ".join(artifact["marker"]) + "\n"

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert (
        subprocess.run(
            ["git", "-C", str(scratch), "init", "-q"], env=SCRATCH_GIT_ENV, check=False
        ).returncode
        == 0
    )
    target = scratch / "trace.md"
    target.write_text("# Sample trace\n\n" + marker, encoding="utf-8")
    staged = subprocess.run(
        ["git", "-C", str(scratch), "add", "--", "trace.md"],
        env=SCRATCH_GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    assert staged.returncode == 0, staged.stderr
    diff = subprocess.run(
        ["git", "-C", str(scratch), "diff", "--cached", "--binary", "--no-renames"],
        env=SCRATCH_GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert marker.strip() in diff, "the scratch diff does not carry the marker"

    diff_file = tmp_path / "trace.patch"
    diff_file.write_text(diff, encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            str(AUGER),
            "spec",
            "align",
            "--repo-root",
            str(REPO_ROOT),
            "--spec-root",
            str(SPEC_ROOT),
            "--protocol-version",
            "1",
            "--diff-file",
            str(diff_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout.strip().splitlines()[-1])
    failures = [item for item in payload["findings"] if item["severity"] == "fail"]
    assert not failures, f"the shipped checker fails this artifact's marker: {failures}"
    assert payload["status"] in ("pass", "warn"), payload["status"]


# ------------------------------------------------------------------------------- documented + arm
def test_the_regeneration_command_is_documented() -> None:
    text = PRD.read_text(encoding="utf-8")
    assert "scripts/gen_trace_artifact.py" in text, (
        "the PRD (or README) must name the exact command that regenerates the artifact; an "
        "undocumented artifact is a hand-written one with extra steps"
    )


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    lines = GATE.read_text(encoding="utf-8").splitlines()
    arm = [line for line in lines if "python3 -m pytest" in line]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_trace_artifact.py" in arm[0], (
        "tests/test_trace_artifact.py is not in the gate's pytest arm — the resolution claim "
        "would be an unrun proof"
    )
    # ...and a rename cannot leave the arm naming a file that no longer exists: a stale name in
    # the arm is a silently narrowed suite, which reads exactly like a green one.
    named = [token for token in arm[0].split() if token.startswith("tests/test_")]
    missing = [name for name in named if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"tests/gate.sh's pytest arm names files that do not exist: {missing}"
    )
