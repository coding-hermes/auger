"""AUG-167: corpus time-travel — `spec list|assemble --as-of <ref>`.

Hermetic: no network, no DuckBrain, no live server. Git fixtures are built in
`tmp_path` with local user config, so the suite passes on a box with no global
git identity. The CLI is driven as a subprocess over `auger.py` (stdlib-only)
so exit codes and stderr are the real contract, not an in-process shortcut.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

AUGER_PY = Path(__file__).resolve().parent.parent / "auger.py"
REPO_ROOT = AUGER_PY.parent
SHIPPED_SPECS = REPO_ROOT / "specs"
TEMP_LITTER_PREFIX = "auger-as-of-"

OLD_SENTINEL = "OLD-SENTINEL-AUG167 body text"
NEW_SENTINEL = "NEW-SENTINEL-AUG167 body text"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUGER_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> str:
    probe = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "auger-as-of-test@example.invalid")
    _git(repo, "config", "user.name", "Auger As-Of Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _corpus_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spec xmlns="urn:auger:spec:1" id="AUGER-AS-OF-FIXTURE" version="1.0.0"'
        ' schema-version="1.0">\n'
        '  <requirement id="R-TIMETRAVEL" kind="requirement" status="active"'
        ' version="1.0.0" applies-to="auger.asof.fixture" verification="test"'
        ' verifies="R-TIMETRAVEL-V1">\n'
        f"    {body}\n"
        "  </requirement>\n"
        '  <verification id="R-TIMETRAVEL-V1" kind="verification" status="active"'
        ' version="1.0.0" applies-to="auger.asof.fixture" method="test" type="test"'
        ' runner="pytest" path="tests/test_marker.py"'
        ' selector="test_marker">\n'
        "    Marker verification for the time-travel fixture.\n"
        "  </verification>\n"
        "</spec>\n"
    )


def _two_commit_repo(repo: Path) -> tuple[str, str]:
    """A repo whose block BODY is rewritten between two commits."""
    _init_repo(repo)
    specs = repo / "specs"
    specs.mkdir()
    (specs / "corpus.xml").write_text(_corpus_xml(OLD_SENTINEL), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old body")
    old_ref = _git(repo, "rev-parse", "HEAD")
    (specs / "corpus.xml").write_text(_corpus_xml(NEW_SENTINEL), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "new body")
    new_ref = _git(repo, "rev-parse", "HEAD")
    assert old_ref != new_ref
    return old_ref, new_ref


def _specs_tree_sha256() -> dict[str, str]:
    return {
        p.relative_to(REPO_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(SHIPPED_SPECS.rglob("*"))
        if p.is_file()
    }


def _temp_litter() -> set[str]:
    return {
        p.name
        for p in Path(tempfile.gettempdir()).glob(f"{TEMP_LITTER_PREFIX}*")
        if p.is_dir()
    }


# --- 1. the OLD-text proof: an earlier commit returns the old body ------------


def test_as_of_earlier_commit_returns_old_text(tmp_path):
    old_ref, _ = _two_commit_repo(tmp_path / "repo")
    root = str(tmp_path / "repo" / "specs")

    listed = _run_cli("spec", "list", root, "--id", "R-TIMETRAVEL", "--as-of", old_ref)
    assert listed.returncode == 0, listed.stderr
    assert "R-TIMETRAVEL" in listed.stdout

    as_of = _run_cli(
        "spec",
        "assemble",
        root,
        "--id",
        "R-TIMETRAVEL",
        "--format",
        "json",
        "--as-of",
        old_ref,
    )
    assert as_of.returncode == 0, as_of.stderr
    payload = json.loads(as_of.stdout)
    texts = [block["text"] for block in payload["blocks"]]
    assert any(OLD_SENTINEL in text for text in texts), payload
    assert not any(NEW_SENTINEL in text for text in texts), payload

    plain = _run_cli(
        "spec",
        "assemble",
        root,
        "--id",
        "R-TIMETRAVEL",
        "--format",
        "json",
    )
    assert plain.returncode == 0, plain.stderr
    assert NEW_SENTINEL in plain.stdout
    assert OLD_SENTINEL not in plain.stdout


# --- 2. --as-of HEAD equals plain selection, byte for byte --------------------


def _skip_if_specs_dirty():
    dirty = _git(REPO_ROOT, "status", "--porcelain", "--", "specs")
    if dirty:
        pytest.skip(
            "specs/ has uncommitted changes; --as-of HEAD compares committed state"
        )


def test_as_of_head_equals_plain_list_on_shipped_corpus():
    _skip_if_specs_dirty()
    plain = _run_cli("spec", "list", str(SHIPPED_SPECS))
    head = _run_cli("spec", "list", str(SHIPPED_SPECS), "--as-of", "HEAD")
    assert plain.returncode == 0, plain.stderr
    assert head.returncode == 0, head.stderr
    assert head.stdout == plain.stdout
    assert (
        hashlib.sha256(head.stdout.encode()).hexdigest()
        == hashlib.sha256(plain.stdout.encode()).hexdigest()
    )


def test_as_of_head_equals_plain_assemble_json_on_shipped_corpus():
    _skip_if_specs_dirty()
    plain = _run_cli(
        "spec",
        "assemble",
        str(SHIPPED_SPECS),
        "--id",
        "SPEC-VERBS-SELECTION",
        "--format",
        "json",
    )
    head = _run_cli(
        "spec",
        "assemble",
        str(SHIPPED_SPECS),
        "--id",
        "SPEC-VERBS-SELECTION",
        "--format",
        "json",
        "--as-of",
        "HEAD",
    )
    assert plain.returncode == 0, plain.stderr
    assert head.returncode == 0, head.stderr
    assert head.stdout == plain.stdout


def test_as_of_explicit_commit_equals_head_on_shipped_corpus():
    _skip_if_specs_dirty()
    head_sha = _git(REPO_ROOT, "rev-parse", "HEAD")
    head = _run_cli("spec", "list", str(SHIPPED_SPECS), "--as-of", "HEAD")
    pinned = _run_cli("spec", "list", str(SHIPPED_SPECS), "--as-of", head_sha)
    assert pinned.returncode == 0, pinned.stderr
    assert pinned.stdout == head.stdout


# --- 3-5. typed refusals, rc 2 ------------------------------------------------


def test_invalid_ref_is_a_typed_refusal(tmp_path):
    old_ref, _ = _two_commit_repo(tmp_path / "repo")
    root = str(tmp_path / "repo" / "specs")
    probe = _run_cli("spec", "list", root, "--as-of", "no-such-ref-aug167")
    assert probe.returncode == 2
    assert probe.stdout == ""
    assert "as-of-unknown-ref" in probe.stderr


def test_non_git_directory_is_a_typed_refusal(tmp_path):
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    (plain_dir / "corpus.xml").write_text(_corpus_xml(NEW_SENTINEL), encoding="utf-8")
    probe = _run_cli("spec", "list", str(plain_dir), "--as-of", "HEAD")
    assert probe.returncode == 2
    assert probe.stdout == ""
    assert "as-of-not-a-repository" in probe.stderr


def test_corpus_absent_at_ref_is_a_typed_refusal(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("no specs yet\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "before specs")
    early = _git(repo, "rev-parse", "HEAD")
    specs = repo / "specs"
    specs.mkdir()
    (specs / "corpus.xml").write_text(_corpus_xml(NEW_SENTINEL), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "specs arrive")
    probe = _run_cli("spec", "list", str(specs), "--as-of", early)
    assert probe.returncode == 2
    assert probe.stdout == ""
    assert "as-of-corpus-missing" in probe.stderr


def test_missing_git_binary_is_a_typed_refusal(tmp_path, monkeypatch):
    old_ref, _ = _two_commit_repo(tmp_path / "repo")
    root = str(tmp_path / "repo" / "specs")
    empty_path = tmp_path / "no-git-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    probe = _run_cli("spec", "list", root, "--as-of", old_ref)
    assert probe.returncode == 2
    assert probe.stdout == ""
    assert "as-of-git-unavailable" in probe.stderr


# --- 6. the flag lives on the selection verbs only ----------------------------


def test_validate_refuses_as_of_flag():
    probe = _run_cli("spec", "validate", str(SHIPPED_SPECS), "--as-of", "HEAD")
    assert probe.returncode == 2
    assert "unrecognized arguments" in probe.stderr


def test_check_refuses_as_of_flag():
    probe = _run_cli("spec", "check", str(SHIPPED_SPECS), "--as-of", "HEAD")
    assert probe.returncode == 2
    assert "unrecognized arguments" in probe.stderr


# --- 7. read-only proof --------------------------------------------------------


def test_as_of_run_is_read_only_on_repo_and_temp():
    _skip_if_specs_dirty()
    before_files = _specs_tree_sha256()
    before_status = _git(REPO_ROOT, "status", "--porcelain")
    before_top_level = sorted(p.name for p in REPO_ROOT.iterdir())
    before_litter = _temp_litter()

    probe = _run_cli("spec", "list", str(SHIPPED_SPECS), "--as-of", "HEAD")
    assert probe.returncode == 0, probe.stderr

    assert _specs_tree_sha256() == before_files
    assert _git(REPO_ROOT, "status", "--porcelain") == before_status
    assert sorted(p.name for p in REPO_ROOT.iterdir()) == before_top_level
    assert _temp_litter() == before_litter
