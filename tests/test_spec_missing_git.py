"""AUG-170: a missing git binary is a typed spec refusal, never a traceback.

`spec check` and `spec validate` resolve a corpus' repository root through
`git rev-parse` (`_spec_repository_root` -> `_git_toplevel`). On a host without a
`git` binary that used to surface as a raw
`FileNotFoundError: [Errno 2] No such file or directory: 'git'` traceback (exit 1);
both verbs now refuse through the AUG-167 `_spec_git_run` seam as
`spec error (as-of-git-unavailable)` on stderr, exit 2, nothing on stdout — the
SAME code the `--as-of` feature already refused with, so both verbs agree.

Hermetic: no network, no live services. git is hidden by pointing PATH at an
empty directory; the interpreter is invoked by absolute path (`sys.executable`)
so the subprocess still runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

AUGER_PY = Path(__file__).resolve().parent.parent / "auger.py"
REPO_ROOT = AUGER_PY.parent
VERIFICATION_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "xml" / "verification"


def _run_cli_without_git(*args: str) -> subprocess.CompletedProcess:
    """Run `auger.py` with a PATH that contains no `git` binary."""
    with tempfile.TemporaryDirectory(prefix="auger-no-git-bin-") as empty:
        env = dict(os.environ)
        env["PATH"] = empty
        return subprocess.run(
            [sys.executable, str(AUGER_PY), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )


def test_spec_missing_git_check_refuses_typed():
    probe = _run_cli_without_git("spec", "check", str(VERIFICATION_FIXTURE))
    assert probe.returncode == 2, probe.stderr
    assert probe.stdout == ""
    assert "Traceback (most recent call last)" not in probe.stderr
    assert "FileNotFoundError" not in probe.stderr
    assert "spec error (as-of-git-unavailable)" in probe.stderr
    assert "git executable is not available" in probe.stderr


def test_spec_missing_git_validate_refuses_typed():
    probe = _run_cli_without_git("spec", "validate", str(VERIFICATION_FIXTURE))
    assert probe.returncode == 2, probe.stderr
    assert probe.stdout == ""
    assert "Traceback (most recent call last)" not in probe.stderr
    assert "FileNotFoundError" not in probe.stderr
    assert "spec error (as-of-git-unavailable)" in probe.stderr
    assert "git executable is not available" in probe.stderr


def test_spec_missing_git_both_verbs_agree():
    """AUG-170 AC3: under the missing-git environment both verbs return the SAME
    exit code and carry the SAME refusal code on stderr."""
    check = _run_cli_without_git("spec", "check", str(VERIFICATION_FIXTURE))
    validate = _run_cli_without_git("spec", "validate", str(VERIFICATION_FIXTURE))
    assert check.returncode == validate.returncode == 2
    assert "as-of-git-unavailable" in check.stderr
    assert "as-of-git-unavailable" in validate.stderr
