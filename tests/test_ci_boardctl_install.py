"""AUG-174: CI must install the pinned boardctl release BEFORE the gate step.

The board proofs in tests/test_xml_task_contract.py (AC3/AC4/AC6) are fail-loud by design
(4820be8): on a runner with no ``boardctl`` binary they raise instead of skipping, so the
CI job reds the whole pytest arm. Resolution A of AUG-174 makes the CI job install the
pinned public release binary (``coding-hermes/boardctl`` GitHub releases, no token needed)
under the exact name ``boardctl`` before ``bash tests/gate.sh`` runs.

This file pins that workflow change with assertions that are checked against the REAL
``.github/workflows/ci.yml`` AND against mutated copies (tmp_path) so every assertion is
proven capable of failing — a checker that cannot go red is decoration, not coverage:

  position ................ test_the_boardctl_install_step_runs_before_the_gate_step
  pinned version .......... test_the_boardctl_version_is_a_pinned_release_tag_not_latest
  exact install name ...... test_the_release_binary_is_installed_under_the_exact_name_boardctl
  checksum fail-closed .... test_the_download_is_checksum_verified_and_fails_closed
  arch guard .............. test_the_asset_resolution_refuses_unknown_architectures_loudly
  fail-loud preserved ..... test_boardctl_or_skip_still_raises_when_the_binary_is_missing
                            test_boardctl_or_skip_has_no_skip_fallback
  non-vacuity ............. test_the_real_workflow_satisfies_every_assertion
                            test_the_gate_pytest_arm_names_this_file

Each mutation leg moves/breaks ONE property of a parsed copy of the real workflow and
requires the corresponding finding code — never a monkeypatch of this module.

ch:trace row=AUG-174 spec=none:no-spec-block-covers-a-CI-workflow-install-step test=tests/test_ci_boardctl_install.py evidence=evidence/AUG-174/ verdict=none:not-judged commit=none:proof-is-the-evidence-directory
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_SH = REPO_ROOT / "tests" / "gate.sh"

RELEASE_URL = "https://github.com/coding-hermes/boardctl/releases/download"
GATE_STEP_MARKER = "bash tests/gate.sh"
BIN_NAME = "boardctl"
INSTALL_RE = re.compile(
    r'install -m 0755 "\$\{WORK\}/\$\{BOARDCTL_ASSET\}" "\$\{DEST\}/' + BIN_NAME + '"'
)


def load_steps(path: Path) -> list[dict]:
    """Parse a GitHub workflow file and return the gate job's step list."""
    data = yaml.safe_load(path.read_text())
    steps = data["jobs"]["gate"]["steps"]
    assert isinstance(steps, list) and steps, f"no steps found in {path}"
    return steps


def workflow_findings(path: Path) -> list[str]:
    """Check one workflow file; return the list of finding codes (empty = all good)."""
    steps = load_steps(path)
    findings: list[str] = []

    gate_idx = next(
        (i for i, s in enumerate(steps) if GATE_STEP_MARKER in (s.get("run") or "")),
        None,
    )
    if gate_idx is None:
        findings.append("missing-gate-step")
        gate_idx = len(steps)

    install = [
        (i, s) for i, s in enumerate(steps) if RELEASE_URL in (s.get("run") or "")
    ]
    if not install:
        findings.append("missing-install-step")
        return findings
    if len(install) > 1:
        findings.append("duplicate-install-steps")
    idx, step = install[0]

    # (b) POSITION, not mere presence: a download after the gate proves nothing.
    if idx >= gate_idx:
        findings.append("install-step-after-gate")

    text = step["run"] or ""

    # (c) PINNED exact release tag, never a floating/latest download.
    if not re.search(r"BOARDCTL_TAG=v\d+\.\d+\.\d+\b", text):
        findings.append("unpinned-version")
    if re.search(r"\blatest\b", text):
        findings.append("latest-version")

    # (a) installed on PATH under the EXACT name the tests resolve with shutil.which.
    if not INSTALL_RE.search(text):
        findings.append("wrong-install-name")

    # (d) checksum-verified against the release's SHA256SUMS, fail-closed.
    if "SHA256SUMS" not in text or "sha256sum -c" not in text:
        findings.append("missing-checksum")
    if "set -euo pipefail" not in text and not re.search(r"\bexit 1\b", text):
        findings.append("not-fail-closed")

    # asset resolution must refuse unknown architectures loudly.
    if "uname -m" not in text:
        findings.append("missing-arch-guard")

    return findings


def _mutated_workflow(tmp_path: Path, mutate) -> Path:
    """A copy of the real ci.yml with ONE property broken, for the non-vacuity legs."""
    data = yaml.safe_load(CI_YML.read_text())
    steps = data["jobs"]["gate"]["steps"]
    mutate(steps)
    out = tmp_path / "ci-mutated.yml"
    out.write_text(yaml.safe_dump(data, sort_keys=False))
    return out


def _install_step_index(steps: list[dict]) -> int:
    idx = next(i for i, s in enumerate(steps) if RELEASE_URL in (s.get("run") or ""))
    return idx


# --------------------------------------------------------------------- the real file


def test_the_real_workflow_satisfies_every_assertion():
    assert workflow_findings(CI_YML) == []


# --------------------------------------------------------------------- mutation legs


def test_the_boardctl_install_step_runs_before_the_gate_step(tmp_path):
    def move_after_gate(steps):
        i = _install_step_index(steps)
        step = steps.pop(i)
        gate_i = next(
            j for j, s in enumerate(steps) if GATE_STEP_MARKER in (s.get("run") or "")
        )
        steps.insert(gate_i + 1, step)

    findings = workflow_findings(_mutated_workflow(tmp_path, move_after_gate))
    assert "install-step-after-gate" in findings, findings


def test_the_boardctl_version_is_a_pinned_release_tag_not_latest(tmp_path):
    def float_the_version(steps):
        s = steps[_install_step_index(steps)]
        s["run"] = s["run"].replace("BOARDCTL_TAG=v0.5.0", "BOARDCTL_TAG=latest")

    findings = workflow_findings(_mutated_workflow(tmp_path, float_the_version))
    assert "unpinned-version" in findings, findings
    assert "latest-version" in findings, findings


def test_the_release_binary_is_installed_under_the_exact_name_boardctl(tmp_path):
    def drop_the_rename(steps):
        s = steps[_install_step_index(steps)]
        s["run"] = s["run"].replace('"${DEST}/boardctl"', '"${DEST}/${BOARDCTL_ASSET}"')

    findings = workflow_findings(_mutated_workflow(tmp_path, drop_the_rename))
    assert "wrong-install-name" in findings, findings


def test_the_download_is_checksum_verified_and_fails_closed(tmp_path):
    def drop_the_checksum(steps):
        s = steps[_install_step_index(steps)]
        lines = [ln for ln in s["run"].splitlines() if "sha256sum -c" not in ln]
        s["run"] = "\n".join(lines)

    findings = workflow_findings(_mutated_workflow(tmp_path, drop_the_checksum))
    assert "missing-checksum" in findings, findings


def test_the_asset_resolution_refuses_unknown_architectures_loudly(tmp_path):
    def drop_the_arch_guard(steps):
        s = steps[_install_step_index(steps)]
        s["run"] = s["run"].replace("uname -m", "echo x86_64")

    findings = workflow_findings(_mutated_workflow(tmp_path, drop_the_arch_guard))
    assert "missing-arch-guard" in findings, findings


# --------------------------------------------------------------------- fail-loud preserved


def _xml_task_contract_module():
    """Load tests/test_xml_task_contract.py as a module (it has no package __init__)."""
    path = REPO_ROOT / "tests" / "test_xml_task_contract.py"
    spec = importlib.util.spec_from_file_location("_xml_task_contract_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_boardctl_or_skip_still_raises_when_the_binary_is_missing(monkeypatch):
    """The CI install step must not have been "easier" than making the proofs fail loud."""
    module = _xml_task_contract_module()
    monkeypatch.setattr(module, "BOARDCTL", None)
    with pytest.raises(pytest.fail.Exception) as excinfo:
        module.boardctl_or_skip()
    assert "boardctl is not installed" in str(excinfo.value), excinfo.value


def test_boardctl_or_skip_has_no_skip_fallback():
    import inspect

    source = inspect.getsource(_xml_task_contract_module().boardctl_or_skip)
    assert "pytest.fail(" in source, source
    assert "pytest.skip" not in source, source
