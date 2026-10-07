"""Packaging metadata checks (QA-AUGER-11).

The repo historically shipped releases as raw GitHub tags with no
pip-installable metadata, so the v0.2.0 -> HEAD upgrade path was never
exercisable by QA. These tests keep pyproject.toml honest: present,
parseable, correctly named, stdlib-only dependencies (docs/DESIGN.md v0.1
decision), and a console-script target that actually resolves against the
module. Stdlib + pytest only, no network.
"""

import importlib
import tomllib

from conftest import REPO_ROOT  # tests/conftest.py defines and exports it

import auger  # noqa: E402  (conftest puts REPO_ROOT on sys.path first)

PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    assert PYPROJECT_PATH.is_file(), "pyproject.toml must exist at repo root"
    with open(PYPROJECT_PATH, "rb") as fh:
        return tomllib.load(fh)


def test_pyproject_exists_and_parses():
    data = _load_pyproject()
    assert "project" in data
    assert "build-system" in data


def test_project_name():
    assert _load_pyproject()["project"]["name"] == "auger"


def test_requires_python_floor():
    assert _load_pyproject()["project"]["requires-python"] == ">=3.11"


def test_dependencies_stay_empty_for_stdlib_only_module():
    # auger.py is intentionally stdlib-only (docs/DESIGN.md, v0.1 decision).
    # If a third-party import ever lands in auger.py, this test should be
    # updated together with [project] dependencies.
    data = _load_pyproject()
    assert data["project"].get("dependencies", []) == []
    assert data["build-system"]["requires"] == ["setuptools>=68"]
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"


def test_dynamic_version_attr_resolves_to_module_constant():
    data = _load_pyproject()
    assert "version" in data["project"].get("dynamic", [])
    attr_path = data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    module_name, _, attr_chain = attr_path.partition(".")
    obj = importlib.import_module(module_name)
    for part in attr_chain.split("."):
        obj = getattr(obj, part)
    assert isinstance(obj, str)
    assert obj.strip()
    assert auger.AUGER_VERSION == obj  # declared attr is the real constant


def test_console_script_target_is_importable():
    scripts = _load_pyproject()["project"].get("scripts", {})
    assert "auger" in scripts
    target = scripts["auger"]
    module_name, sep, attr = target.partition(":")
    assert sep == ":", f"entry target must be 'module:attr', got {target!r}"
    module = importlib.import_module(module_name)
    entry = getattr(module, attr)
    assert callable(entry)
    assert entry is auger.main
