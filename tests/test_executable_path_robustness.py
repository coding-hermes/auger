"""AUG-161: the suite is robust to a RELATIVE PATH entry.

With a relative PATH entry (`PATH=.venv/bin:$PATH python3 -m pytest`), CPython derives a
RELATIVE `sys.executable` (measured: `'.venv/bin/python3'`). Every subprocess spawn that
passes `cwd=<scratch dir>` while keeping the raw `sys.executable` in argv then dies with
FileNotFoundError before any assertion executes, so the failure reads as a code red when the
tree is fine. The audit (docs/prd/evidence/AUG-161-path-audit.md) enumerates every site; this
file pins the invariant mechanically so the defect class cannot return silently.

Deliberately NOT a pytest-subprocess re-run: these pins are unit-level (AST + one tiny
`-c` spawn) so they stay fast and hermetic inside the gate's pytest arm.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

SPAWN_ATTRS = {"run", "Popen", "check_output", "check_call", "call"}
# cwd expressions that resolve the child INSIDE the repo root: a relative sys.executable
# still resolves there, so those sites are proven unaffected (audit disposition
# "cwd-repo-root-unaffected"). Anything else (tmp_path, a scratch repo, ...) needs an
# absolute-resolved interpreter.
REPO_ROOT_CWD = {"REPO_ROOT", "str(REPO_ROOT)"}


def _is_sys_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _contains_raw_sys_executable(node: ast.AST) -> bool:
    """True if `node`'s subtree contains the bare `sys.executable` attribute expression."""
    return any(_is_sys_executable(n) for n in ast.walk(node))


def _spawn_is_raw(call: ast.Call, tree: ast.AST) -> bool:
    """Does this spawn's executable argument carry the RAW `sys.executable`?

    Covers both shapes: the attribute directly in the argv expression, AND the mediator
    shape — an argv VARIABLE assigned (in the same module) from an expression containing
    raw `sys.executable`, then passed to the spawn (the `run_cli` defect shape).
    """
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        if _contains_raw_sys_executable(arg):
            return True
        if isinstance(arg, ast.Name):
            for assign in ast.walk(tree):
                if (
                    isinstance(assign, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == arg.id
                        for t in assign.targets
                    )
                    and _contains_raw_sys_executable(assign.value)
                ):
                    return True
    return False


def _iter_spawn_sites() -> list[tuple[str, int, ast.Call, ast.AST]]:
    """Every spawn site whose args carry raw sys.executable (direct or via variable)."""
    sites = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spawn_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SPAWN_ATTRS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        for call in spawn_calls:
            if _spawn_is_raw(call, tree):
                sites.append((path.name, call.lineno, call, tree))
    return sites


def test_no_scratch_cwd_spawn_keeps_the_raw_relative_interpreter() -> None:
    """A spawn that sets a NON-repo-root cwd must not pass raw `sys.executable` in argv.

    Red on the unfixed tree: the four scratch-cwd sites (tests/test_auger.py child-pytest,
    tests/test_spec_alignment_check.py `run_cli` helper AND its `--diff-stdin` loop,
    tests/test_trace_artifact.py `spec align --diff-file`) passed raw `sys.executable` and
    died under a relative PATH entry before any assertion ran.
    """
    offenders = []
    for fname, line, call, tree in _iter_spawn_sites():
        cwd = next((kw for kw in call.keywords if kw.arg == "cwd"), None)
        if cwd is None:
            continue  # inherited cwd = the pytest invocation dir (repo root) — unaffected
        cwd_expr = ast.unparse(cwd.value)
        if cwd_expr in REPO_ROOT_CWD:
            continue  # relative interpreter still resolves inside the repo root
        if _spawn_is_raw(call, tree):
            offenders.append(f"{fname}:{line} (cwd={cwd_expr})")
    assert not offenders, (
        "spawns with a scratch cwd= keep raw sys.executable in argv — under a relative "
        "PATH entry the child dies with FileNotFoundError before any assertion: "
        + ", ".join(offenders)
    )


def test_every_spawn_with_a_cwd_kwarg_is_either_repo_root_or_absolute_resolved() -> (
    None
):
    """The audit's full disposition table, mechanically: every cwd= spawn is accounted for."""
    unaccounted = []
    for fname, line, call, tree in _iter_spawn_sites():
        cwd = next((kw for kw in call.keywords if kw.arg == "cwd"), None)
        if cwd is None or ast.unparse(cwd.value) in REPO_ROOT_CWD:
            continue
        if _spawn_is_raw(call, tree):
            unaccounted.append(f"{fname}:{line}")
    assert not unaccounted, (
        "spawn sites set a scratch cwd= without an absolute-resolved interpreter: "
        + ", ".join(unaccounted)
    )


def test_no_command_string_executor_carries_the_raw_relative_interpreter() -> None:
    """Command-STRING executors are a fifth defect shape: `spec_alignment.run_checker(
    command=...)` executes the string FROM the scratch consumer repo cwd, so a raw
    `sys.executable` inside the f-string kills the checker the same way an argv would.

    Red on the unfixed tree: tests/test_spec_alignment_check.py built
    `command = f"{sys.executable} {AUGER} spec align"` and the rel-PATH run failed the
    gitreins handshake with `[Errno 2] No such file or directory: '.venv/bin/python3'`.
    """
    offenders = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "run_checker"
            ):
                continue
            for kw in node.keywords:
                if kw.arg != "command":
                    continue
                if _contains_raw_sys_executable(kw.value):
                    offenders.append(f"{path.name}:{kw.value.lineno}")
                    continue
                # mediator shape: `command = f"...{sys.executable}..."` assigned in the
                # same module, then passed as `command=command`
                if isinstance(kw.value, ast.Name) and any(
                    isinstance(a, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == kw.value.id
                        for t in a.targets
                    )
                    and _contains_raw_sys_executable(a.value)
                    for a in ast.walk(tree)
                ):
                    offenders.append(f"{path.name}:{kw.value.lineno}")
    assert not offenders, (
        "command strings handed to a scratch-cwd executor keep raw sys.executable — "
        "under a relative PATH entry the executed checker dies with FileNotFoundError: "
        + ", ".join(offenders)
    )


def test_abspath_resolved_interpreter_runs_from_a_scratch_cwd(tmp_path: Path) -> None:
    """The fix pattern itself: `os.path.abspath(sys.executable)` spawns from ANY cwd.

    Unit-level (one tiny `-c` child), so it stays fast inside the gate arm. On a relative
    PATH launch this is the difference between `.venv/bin/python3` (resolves) and the raw
    relative sys.executable (FileNotFoundError).
    """
    interpreter = os.path.abspath(sys.executable)
    assert os.path.isabs(interpreter)
    done = subprocess.run(
        [interpreter, "-c", "import sys; print(sys.executable)"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    # the child resolved the SAME interpreter from a cwd where the relative spelling
    # (if any) would not exist
    assert os.path.isabs(done.stdout.strip())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
