"""AUG-127: test obligations resolve in the CONSUMING repository, not in auger's tree.

Measured defect (2026-11-05, BASE `b9ef2df`): `validate_xml_specs` set
`repository_root = Path(__file__).resolve().parent` — the directory of the INSTALLED MODULE.
Path resolution, the `git ls-files` tracked check and the AST selector check therefore all
ran inside auger's own checkout. A throwaway repository outside auger's tree, holding a real
git-tracked `tests/test_marker.py` and a spec that names it, was refused:

  spec error (invalid-spec-graph): stale-test-ref: contract.xml: SG3-TEST-1:
  tests/test_marker.py (resolved /home/kara/proj-wt-auger-xml-hardening/tests/test_marker.py)

— a file that exists nowhere in auger's tree, named by a repository that has nothing to do
with auger. The converse is worse: a consumer corpus whose obligation happened to match a
path in auger's tree passed for a reason the consumer does not control.

After this change the root is EXPLICIT and never auger's directory:

  * `--repo-root <dir>` on `spec validate|check|bundle` (or `repository_root=` from Python),
    which must BE a git work tree's top level;
  * otherwise `git rev-parse --show-toplevel` resolved FROM THE SPEC ROOT;
  * neither resolving is a typed refusal (`unresolvable-repo-root`), never a fallback.

Each acceptance line and the test that pins it:

  AC1 consumer repo validates + bundles ....... test_a_consumer_repository_validates_and_bundles_its_own_test
  AC1 the file read is the consumer's copy ... test_the_checked_file_is_the_consumers_copy_not_augers
  AC2 tracked-then-deleted => stale-test-ref .. test_a_tracked_then_deleted_test_fails_closed
  AC3 root explicit and documented ........... test_the_repository_root_is_explicit_and_documented
  AC4 untracked path => untracked-test-ref ... test_an_existing_untracked_test_fails_closed
  AC4 escaping path => escaping-test-ref ..... test_a_path_escaping_the_root_fails_closed
  fail-closed, no fallback to auger .......... test_no_root_and_no_flag_refuses_instead_of_using_augers_tree

ch:trace row=AUG-127 spec=docs/XML-SPEC-SCHEMA.md#the-repository-root test=tests/test_spec_repository_root.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUGER = REPO_ROOT / "auger.py"
GATE = REPO_ROOT / "tests" / "gate.sh"
DOC = REPO_ROOT / "docs" / "XML-SPEC-SCHEMA.md"

#: Isolate the throwaway repositories from this host's global/system git config: a global
#: `core.hooksPath` (or `commit.gpgsign`) turns a scratch commit into someone else's hook.
SCRATCH_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

MARKER_TEST = '"""Consumer-repository test named by the spec corpus."""\n\n\ndef test_marker():\n    assert True\n'


def run_auger(*argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
    """Drive `auger spec ...` as a subprocess, the way a consumer repository would."""
    done = subprocess.run(
        [sys.executable, str(AUGER), "spec", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=SCRATCH_GIT_ENV,
        check=False,
    )


def spec_document(
    path: str = "tests/test_marker.py",
    selector: str = "test_marker",
) -> str:
    """A two-block corpus in the shipped grammar, naming one tracked test."""
    return (
        '<spec xmlns="urn:auger:spec:1" id="SG3-CONSUMER" version="1.0.0" '
        'schema-version="1.0">\n'
        '  <requirement id="SG3-REQ-1" kind="requirement" status="active" '
        'version="1.0.0" verification="test" verifies="SG3-TEST-1">A consumer '
        "repository's own tracked test proves this requirement.</requirement>\n"
        '  <verification id="SG3-TEST-1" kind="verification" status="active" '
        'version="1.0.0" method="test"\n'
        f'      runner="pytest" path="{path}" selector="{selector}"\n'
        '      assertions="the named selector passes" />\n'
        "</spec>\n"
    )


def commit_all(repo: Path, message: str = "consumer repo") -> None:
    staged = run_git(repo, "add", "--", ".")
    assert staged.returncode == 0, staged.stderr
    committed = run_git(repo, "commit", "-m", message)
    assert committed.returncode == 0, committed.stderr


def consumer_repo(base: Path) -> Path:
    """A git repository OUTSIDE auger's checkout: a real test file and the spec naming it."""
    repo = base / "consumer"
    (repo / "tests").mkdir(parents=True)
    (repo / "specs").mkdir(parents=True)
    assert run_git(repo, "init").returncode == 0
    run_git(repo, "config", "user.email", "sg3@example.invalid")
    run_git(repo, "config", "user.name", "SG3 Consumer")
    (repo / "tests" / "test_marker.py").write_text(MARKER_TEST, encoding="utf-8")
    (repo / "specs" / "contract.xml").write_text(spec_document(), encoding="utf-8")
    commit_all(repo)
    return repo


def assert_repo_is_untouched(repo: Path) -> None:
    """A read verb must not write into the repository it grades."""
    status = run_git(repo, "status", "--porcelain")
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == "", (
        f"the read verbs wrote into {repo}:\n{status.stdout}"
    )


# ---------------------------------------------------------------- AC1: the consumer repository
def test_a_consumer_repository_validates_and_bundles_its_own_test(
    tmp_path: Path,
) -> None:
    """AC1: a corpus in an unrelated repository validates its own tracked test, and bundles."""
    repo = consumer_repo(tmp_path)
    assert run_git(repo, "rev-parse", "--show-toplevel").stdout.strip() == str(repo)
    tracked = run_git(repo, "ls-files", "--", "tests/test_marker.py")
    assert tracked.stdout.strip() == "tests/test_marker.py", tracked.stdout

    # No flag: the spec root is inside the repository it describes, so its own work tree
    # is the root. Run from INSIDE the repository, as a consumer would.
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 0, f"a consumer repository's own tracked test was refused: {err}"
    assert "spec: 1 document(s), 2 block(s), 1 reference(s) ok" in out, out

    code, out, err = run_auger("check", str(repo / "specs"), cwd=repo)
    assert code == 0, err
    assert json.loads(out)["nodes"] == ["SG3-REQ-1", "SG3-TEST-1"]

    code, out, err = run_auger("bundle", str(repo / "specs"), "SG3-REQ-1", cwd=repo)
    assert code == 0, err
    bundle = json.loads(out)
    obligation = bundle["verifications"][0]
    assert obligation["path"] == "tests/test_marker.py", obligation
    assert obligation["selector"] == "test_marker", obligation
    assert obligation["runner"] == [sys.executable, "-m", "pytest"], obligation
    # No auger-internal path is involved in the RESOLVED obligation fields. `runner` is the
    # registered argv vector by contract — the interpreter inside auger that will run the
    # test — so it is compared as a vector above and excluded here, one field at a time.
    for field in ("path", "fixture", "migration"):
        value = obligation.get(field) or ""
        assert str(REPO_ROOT) not in value, obligation
        assert not Path(value).is_absolute(), obligation

    # The auger checkout appears nowhere in the resolved data a consumer reads.
    resolved_only = {k: v for k, v in obligation.items() if k != "runner"}
    assert str(REPO_ROOT) not in json.dumps(resolved_only), resolved_only
    assert_repo_is_untouched(repo)


def test_the_checked_file_is_the_consumers_copy_not_augers(tmp_path: Path) -> None:
    """AC1: the file that is read is the consumer's, proved in both directions.

    `tests/test_xml_specs.py` exists in auger's tree AND in the consumer's. A selector that
    only the CONSUMER's copy defines must pass; a selector only AUGER's copy defines must be
    refused. Resolution against the wrong tree flips exactly one of those two.
    """
    consumer_only = "test_consumer_only_marker"
    auger_only = "test_gate_runs_these_tests"
    auger_copy = (REPO_ROOT / "tests" / "test_xml_specs.py").read_text(encoding="utf-8")
    assert f"def {auger_only}(" in auger_copy, "the auger-only selector drifted"
    assert f"def {consumer_only}(" not in auger_copy

    repo = consumer_repo(tmp_path)
    (repo / "tests" / "test_xml_specs.py").write_text(
        f'"""Consumer copy: defines a selector auger\'s copy does not."""\n\n\n'
        f"def {consumer_only}():\n    assert True\n",
        encoding="utf-8",
    )
    commit_all(repo, "consumer copy of test_xml_specs.py")

    (repo / "specs" / "contract.xml").write_text(
        spec_document(path="tests/test_xml_specs.py", selector=consumer_only),
        encoding="utf-8",
    )
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 0, (
        "the consumer's own copy of the named test was not the file that was parsed: "
        + err
    )

    (repo / "specs" / "contract.xml").write_text(
        spec_document(path="tests/test_xml_specs.py", selector=auger_only),
        encoding="utf-8",
    )
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 2, f"a selector auger's copy defines was accepted for {repo}: {out}"
    assert "stale-test-ref" in err, err
    assert auger_only in err, err
    assert str(repo) in err, (
        f"the refusal does not resolve inside the consumer repo: {err}"
    )


# ---------------------------------------------------------------- AC2: tracked then deleted
def test_a_tracked_then_deleted_test_fails_closed(tmp_path: Path) -> None:
    """AC2: the path is still in the index, gone from the work tree — a typed refusal."""
    repo = consumer_repo(tmp_path)
    assert run_git(repo, "ls-files", "--", "tests/test_marker.py").stdout.strip()
    (repo / "tests" / "test_marker.py").unlink()
    assert not (repo / "tests" / "test_marker.py").exists()
    # Still tracked: the refusal must be about the file being gone, not about tracking.
    assert run_git(repo, "ls-files", "--", "tests/test_marker.py").stdout.strip(), (
        "the probe did not leave the path tracked"
    )
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 2, f"a tracked-then-deleted test path was accepted: {out!r}"
    assert "stale-test-ref" in err, err
    assert "tests/test_marker.py" in err, err
    assert str(repo) in err, (
        f"the refusal does not resolve inside the consumer repo: {err}"
    )


# ---------------------------------------------------------------- AC3: the root is explicit
def test_the_repository_root_is_explicit_and_documented(tmp_path: Path) -> None:
    """AC3: `--repo-root` exists, is documented, and actually decides the resolution.

    A corpus living OUTSIDE the repository it names has no work tree of its own, so the flag
    is the only way to state the root: without it the run refuses, with it the same corpus
    resolves the same tracked test.
    """
    doc = DOC.read_text(encoding="utf-8")
    assert "--repo-root" in doc, (
        "docs/XML-SPEC-SCHEMA.md does not name the --repo-root flag"
    )
    assert "git rev-parse --show-toplevel" in doc, (
        "docs/XML-SPEC-SCHEMA.md does not name the root-resolution rule"
    )

    repo = consumer_repo(tmp_path)
    for verb, extra in (
        ("validate", ()),
        ("check", ()),
        ("bundle", ("SG3-REQ-1",)),
    ):
        code, out, err = run_auger(verb, "--help")
        assert code == 0, err
        assert "--repo-root" in out, (
            f"`spec {verb} --help` does not offer --repo-root: {out}"
        )

    # A corpus outside any work tree (tmp_path is not a git repository) that names a test
    # in `repo`: the flag is what makes it resolvable.
    outside = tmp_path / "foreign-specs"
    outside.mkdir()
    (outside / "contract.xml").write_text(spec_document(), encoding="utf-8")
    code, out, err = run_auger("validate", str(outside))
    assert code == 2, f"a corpus with no resolvable repository root was graded: {out!r}"
    assert "unresolvable-repo-root" in err, err

    code, out, err = run_auger(
        "validate", str(outside), "--repo-root", str(repo), cwd=repo
    )
    assert code == 0, f"--repo-root did not make the consumer's test resolvable: {err}"
    assert "spec: 1 document(s), 2 block(s), 1 reference(s) ok" in out, out

    code, out, err = run_auger(
        "bundle", str(outside), "SG3-REQ-1", "--repo-root", str(repo), cwd=repo
    )
    assert code == 0, err
    assert json.loads(out)["verifications"][0]["path"] == "tests/test_marker.py"

    # An explicit root that is not a repository root is refused, not quietly reinterpreted.
    code, out, err = run_auger(
        "validate", str(outside), "--repo-root", str(repo / "specs"), cwd=repo
    )
    assert code == 2, (
        f"a --repo-root below the work tree's top level was accepted: {out!r}"
    )
    assert "unresolvable-repo-root" in err, err

    code, out, err = run_auger(
        "validate", str(outside), "--repo-root", str(tmp_path / "nope"), cwd=repo
    )
    assert code == 2, f"a missing --repo-root directory was accepted: {out!r}"
    assert "unresolvable-repo-root" in err, err


# ---------------------------------------------------------------- AC4: the closed paths
def test_an_existing_untracked_test_fails_closed(tmp_path: Path) -> None:
    """AC4: a path that exists but is not in git is `untracked-test-ref`, never ok."""
    repo = consumer_repo(tmp_path)
    untracked = repo / "tests" / "test_untracked.py"
    untracked.write_text("def test_untracked():\n    assert True\n", encoding="utf-8")
    assert untracked.is_file()
    assert (
        run_git(
            repo, "ls-files", "--error-unmatch", "--", "tests/test_untracked.py"
        ).returncode
        != 0
    )

    (repo / "specs" / "contract.xml").write_text(
        spec_document(path="tests/test_untracked.py", selector="test_untracked"),
        encoding="utf-8",
    )
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 2, f"an untracked test path was accepted: {out!r}"
    assert "untracked-test-ref" in err, err
    assert "tests/test_untracked.py" in err, err


def test_a_path_escaping_the_root_fails_closed(tmp_path: Path) -> None:
    """AC4: a relative path that leaves the repository is its own typed refusal."""
    repo = consumer_repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("def test_outside():\n    assert True\n", encoding="utf-8")
    (repo / "specs" / "contract.xml").write_text(
        spec_document(path="../outside.py", selector="test_outside"),
        encoding="utf-8",
    )
    code, out, err = run_auger("validate", str(repo / "specs"), cwd=repo)
    assert code == 2, f"a path escaping the repository root was accepted: {out!r}"
    assert "escaping-test-ref" in err, err
    assert str(outside) in err, f"the refusal does not name the resolved path: {err}"
    assert str(repo) in err, f"the refusal does not name the root it escaped: {err}"


def test_no_root_and_no_flag_refuses_instead_of_using_augers_tree(
    tmp_path: Path,
) -> None:
    """Fail closed: with no root to resolve in, the run refuses — it does not guess auger's.

    The corpus names `tests/test_xml_specs.py`, which EXISTS and is tracked in auger's own
    checkout. The pre-fix code silently used its module directory and accepted this corpus
    for a reason that has nothing to do with the corpus' own repository.
    """
    outside = tmp_path / "foreign-specs"
    outside.mkdir()
    (outside / "contract.xml").write_text(
        spec_document(
            path="tests/test_xml_specs.py", selector="test_gate_runs_these_tests"
        ),
        encoding="utf-8",
    )
    top = run_git(outside, "rev-parse", "--show-toplevel")
    if (
        top.returncode == 0
    ):  # pragma: no cover - only when TMPDIR sits inside a work tree
        pytest.skip(
            f"tmp_path is inside a work tree ({top.stdout.strip()}); cannot prove it"
        )

    code, out, err = run_auger("validate", str(outside))
    assert code == 2, f"a corpus with no repository root was graded: {out!r}"
    assert "unresolvable-repo-root" in err, err
    assert "--repo-root" in err, f"the refusal does not say how to fix it: {err}"


# ---------------------------------------------------------------- the arm
def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    for required in (
        "tests/test_spec_repository_root.py",
        "tests/test_spec_validate_obligations.py",
        "tests/test_spec_validators_agree.py",
        "tests/test_spec_obligation_gate.py",
        "tests/test_verification_graph.py",
        "tests/test_xml_specs.py",
        "tests/test_xsd_conformance.py",
    ):
        assert required in arm[0], (
            f"{required} must be in tests/gate.sh's pytest arm — the guard's test command "
            "is the gate, so a proof file outside it is never run"
        )
