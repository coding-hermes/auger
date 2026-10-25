"""Upgrade-cell regression seam (QA-AUGER-11-R2).

QA run auger-qa-2026-10-25-06-39-44 ran the documented upgrade cell as
`pip install auger==0.2.0` and got `No matching distribution found for
auger==0.2.0`: the v0.2.0 tag predates the repo's packaging metadata
(pyproject.toml, commit 22698ee) and no auger distribution was ever published
to any index. The fix is NOT a retroactive PyPI publication — the tag is
immutable history and none is fabricated — it is a reproducible, OFFLINE
artifact path: scripts/upgrade-verify.sh exports the v0.2.0 tree, injects only
the current packaging declaration, builds a wheel from the TAG's own module,
installs it into a fresh venv under the exact pin the QA cell failed on,
upgrades that venv to the checkout, and smokes `auger --version` + `--help`.

These tests pin that procedure so it cannot silently regress:

  * the script exists, is executable, and parses (bash -n);
  * the historical limitation it works around is REAL and stays pinned
    (v0.2.0 has no pyproject.toml; its module declares AUGER_VERSION "0.2");
  * the checkout is a real upgrade target (different version, entry point
    resolvable on the tag's module);
  * the procedure keeps its contract: the exact failed pin, fresh venvs,
    --no-index (offline), version + --help smoke, and the artifact built from
    the TAG tree (never a cp of HEAD's auger.py).

The end-to-end leg (actually building venvs and installing) is opt-in via
AUGER_UPGRADE_VERIFY_LIVE=1 because it creates three venvs (~30s); the worker
verification runs it explicitly. Everything else is hermetic: no network, no
live DuckBrain, no pytest plugins — needs only git and the checkout.

CI note: GitHub's default fetch-depth=1 checkout does not carry the v0.2.0
tag. The tag-dependent tests SKIP (out loud, with the remedy) rather than
fail there; scripts/upgrade-verify.sh itself refuses loudly for the same
condition.
"""

import ast
import os
import stat
import subprocess

import pytest

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "upgrade-verify.sh"
PREV_TAG = "v0.2.0"
PREV_PIN = "auger==0.2.0"  # the exact requirement string the QA cell failed on
PREV_VERSION = "0.2"


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return proc.stdout


def _tag_present() -> bool:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{PREV_TAG}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return probe.returncode == 0


def _read_head_version() -> str:
    src = (REPO_ROOT / "auger.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "AUGER_VERSION" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    pytest.fail("auger.py carries no top-level AUGER_VERSION assignment")


def _read_tag_version() -> str:
    src = _run_git("show", f"{PREV_TAG}:auger.py")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "AUGER_VERSION" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    pytest.fail("the tag's auger.py carries no top-level AUGER_VERSION assignment")


# ---------------------------------------------------------------- the script itself


def test_upgrade_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"{SCRIPT} missing — the upgrade cell's artifact path is gone"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/upgrade-verify.sh must stay executable"


def test_upgrade_script_parses_with_bash():
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


# ---------------------------------------------------------------- the pinned history


def test_prev_tag_predates_packaging_metadata():
    """The redproof: v0.2.0 really has no pyproject.toml.

    This pins the QA finding the cell works around. If it ever fails, the tag
    gained packaging (it cannot — tags are immutable) or the wrong tag is
    being tested; either way the cell's premise must be re-derived, not
    assumed.
    """
    if not _tag_present():
        pytest.skip(f"tag {PREV_TAG} not in this checkout (shallow clone? git fetch --tags {PREV_TAG})")
    listing = _run_git("ls-tree", "--name-only", PREV_TAG)
    assert "pyproject.toml" not in listing.splitlines(), (
        f"{PREV_TAG} now carries packaging metadata — the upgrade cell's "
        "local-artifact bridge may be simplifiable; re-derive before editing"
    )


def test_prev_tag_declares_0_2():
    if not _tag_present():
        pytest.skip(f"tag {PREV_TAG} not in this checkout (shallow clone? git fetch --tags {PREV_TAG})")
    assert _read_tag_version() == PREV_VERSION


def test_head_is_a_real_upgrade_target():
    assert _read_head_version() != PREV_VERSION, (
        "HEAD declares the previous release's version — there is no upgrade "
        "delta to verify; bump AUGER_VERSION or retarget the cell"
    )


def test_pyproject_entry_point_resolves_on_the_tag_module():
    """The bridge is sound: the current pyproject.toml's entry point
    (auger:main) and dynamic version attr (auger.AUGER_VERSION) must resolve
    against the TAG's module, since that is the tree the artifact is built
    from."""
    if not _tag_present():
        pytest.skip(f"tag {PREV_TAG} not in this checkout (shallow clone? git fetch --tags {PREV_TAG})")
    tag_src = _run_git("show", f"{PREV_TAG}:auger.py")
    tree = ast.parse(tag_src)
    top_names = set()
    has_main_def = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            has_main_def = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_names.add(target.id)
    assert has_main_def, "the tag's auger.py has no main() — pyproject's [project.scripts] would break"
    assert "AUGER_VERSION" in top_names, (
        "the tag's auger.py has no AUGER_VERSION — pyproject's dynamic attr would break the build"
    )


# ---------------------------------------------------------------- the procedure's contract


def test_script_pins_the_exact_failed_requirement():
    src = SCRIPT.read_text(encoding="utf-8")
    assert PREV_PIN in src, (
        f"the script must install the exact pin the QA cell failed on ({PREV_PIN})"
    )
    assert PREV_TAG in src, "the script must name the tag it exports"


def test_script_installs_offline():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "--no-index" in src, (
        "the artifact install must be --no-index: the cell verifies a LOCAL "
        "artifact and must never silently satisfy the pin from a remote index"
    )


def test_script_uses_fresh_venvs():
    src = SCRIPT.read_text(encoding="utf-8")
    # builder + previous-release venv + second fresh venv — count real
    # invocation lines, not the die messages that name the same command.
    invocations = [ln for ln in src.splitlines() if ln.startswith("python3 -m venv ")]
    assert len(invocations) == 3, (
        "the cell needs exactly three fresh venvs (builder, prev-release, fresh-again)"
    )
    assert "--force-reinstall" in src, "the upgrade leg must replace, not no-op"


def test_script_smokes_version_and_help():
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"auger v${PREV_VERSION}"' in src and '"auger v${CUR_VERSION}"' in src, (
        "both install legs must assert the exact --version output"
    )
    assert "--help" in src, "the CLI smoke (auger --help) must stay in the cell"


def test_script_builds_from_the_tag_tree_not_head():
    """The artifact must carry the TAG's module. Injecting HEAD's auger.py
    would make the 'upgrade' a no-op that still looks green."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'cp "$ROOT/pyproject.toml"' in src, "the packaging injection is the bridge"
    assert 'cp "$ROOT/auger.py"' not in src, (
        "HEAD's auger.py must never be copied over the exported tag tree"
    )


# ---------------------------------------------------------------- opt-in end-to-end leg


@pytest.mark.skipif(
    os.environ.get("AUGER_UPGRADE_VERIFY_LIVE") != "1",
    reason=(
        "end-to-end leg builds 3 venvs (~30s); set AUGER_UPGRADE_VERIFY_LIVE=1 "
        "to run scripts/upgrade-verify.sh for real"
    ),
)
def test_upgrade_verify_script_end_to_end():
    proc = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, (
        f"upgrade-verify.sh rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "UPGRADE VERIFY: PASS" in proc.stdout
