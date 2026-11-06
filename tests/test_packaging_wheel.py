"""AUG-129: the built wheel carries the published grammar — not just `auger.py`.

Measured defect (2026-11-05, base `057844c`): `pip wheel . --no-deps --no-build-isolation` produced
`auger-0.3-py3-none-any.whl` containing `auger.py` + `dist-info` ONLY (154,237 bytes, 7 members), so
`from specs…` — the grammar `auger spec` grades against — did not exist for anyone who installed
from PyPI, and every spec verb on a wheel install refused `xsd-schema-missing`. `py-modules =
["auger"]` means there is no package directory to hang `package-data` on, so the data travels as
`[tool.setuptools.data-files]` entries and auger.py resolves the installed location itself.

What this file pins, per acceptance line:

  AC1 shipped at a documented path .... test_the_wheel_carries_the_schema_and_the_example
                                        test_the_declared_data_files_are_the_shipped_ones
  AC2 fresh venv validates an example . test_a_fresh_venv_install_validates_the_shipped_example
  AC3 the claim cannot rot ............ test_the_wheel_carries_the_schema_and_the_example (builds it)
  fail-closed stays ................... test_with_no_schema_anywhere_the_loader_still_refuses
                                        test_the_installed_layout_is_a_resolution_candidate

OFFLINE. `pip wheel` and `pip install` are run with `--no-index`/`--no-build-isolation` against the
staged checkout. The ONE thing that is not fetched is the XSD processor: lxml has no wheel available
offline on this host, so `_provision_offline_processor` copies the ALREADY BUILT lxml out of the
interpreter running this suite into the fresh venv's site-packages. The auger wheel itself is
installed only from the wheel under test, and the venv's `auger.__file__` is asserted to be inside
the venv, so a stray checkout on `PYTHONPATH` cannot make this arm green.

ch:trace row=AUG-129 spec=docs/XML-SPEC-SCHEMA.md#installed-layout test=tests/test_packaging_wheel.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT  # tests/conftest.py defines and exports it

import auger  # noqa: E402  (conftest puts REPO_ROOT on sys.path first)

SCHEMA_SOURCE = REPO_ROOT / "specs" / "schema" / "auger-spec-1.0.xsd"
EXAMPLE_SOURCE = REPO_ROOT / "examples" / "xml" / "minimal.xml"
SCHEMA_TARGET = "share/auger/specs/schema"
EXAMPLE_TARGET = "share/auger/specs/examples"
PYPROJECT = REPO_ROOT / "pyproject.toml"
BUILD_INPUTS = ("pyproject.toml", "auger.py", "README.md", "LICENSE")


def _pyproject() -> dict:
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


def _declared_data_files() -> dict[str, list[str]]:
    """The `[tool.setuptools.data-files]` map from pyproject.toml (target dir -> source paths)."""
    table = _pyproject()["tool"]["setuptools"].get("data-files", {})
    assert table, (
        "pyproject.toml declares no [tool.setuptools.data-files]; with py-modules = ['auger'] there "
        "is no package-data surface, so the schema would not ship at all (AUG-129)"
    )
    return table


def _clean_env() -> dict[str, str]:
    """A subprocess environment without an inherited `PYTHONPATH`, so a checkout cannot leak in."""
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(name, None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def _run(argv, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, env=_clean_env(), timeout=600, **kwargs
    )


def _run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process (the seam tests/test_xsd_conformance.py uses)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _require_build_tooling() -> None:
    """Fail loudly rather than skip: an unbuilt wheel proves nothing about what ships."""
    for module in ("pip", "setuptools"):
        if importlib.util.find_spec(module) is None:
            pytest.fail(
                f"{module} is not importable by {sys.executable}, so the wheel cannot be built and "
                "AUG-129's shipping claim cannot be checked — run the suite from the repo's dev "
                "environment (`pip install -e '.[dev]'`)"
            )


def _staged_checkout(destination: Path) -> Path:
    """A copy of the checkout holding exactly what the wheel build reads.

    The data files are taken FROM the declared table, so a renamed or deleted source path fails
    here rather than silently building a wheel without it.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for rel in BUILD_INPUTS:
        shutil.copy2(REPO_ROOT / rel, destination / rel)
    for target, sources in _declared_data_files().items():
        for rel in sources:
            source = REPO_ROOT / rel
            assert source.is_file(), (
                f"declared data file {rel} is missing from the checkout"
            )
            copy = destination / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copy)
    return destination


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel from a staged copy of the checkout; one build for the whole module."""
    _require_build_tooling()
    root = tmp_path_factory.mktemp("wheel")
    source = _staged_checkout(root / "src")
    out = root / "dist"
    out.mkdir()
    result = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(source),
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(out),
        ]
    )
    assert result.returncode == 0, (
        f"the wheel did not build:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    wheels = sorted(out.glob("auger-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {[w.name for w in wheels]}"
    return wheels[0]


def _data_member(wheel: Path, target: str, name: str) -> str:
    """The wheel member a `data_files` entry lands at (`<dist>.data/data/<target>/<name>`)."""
    prefix = f"auger-{auger.AUGER_VERSION}.data/data/{target}/{name}"
    with zipfile.ZipFile(wheel) as archive:
        if prefix in archive.namelist():
            return prefix
    return ""  # reported by the caller with the real member list


def test_the_declared_data_files_are_the_shipped_ones() -> None:
    """pyproject.toml ships the grammar and the example, and stays a stdlib-only dependency set."""
    project = _pyproject()["project"]
    assert project.get("dependencies", []) == [], (
        "shipping a data file is not a reason to add a runtime dependency — auger.py is "
        "deliberately stdlib-only (docs/DESIGN.md v0.1)"
    )
    declared = _declared_data_files()
    assert declared.get(SCHEMA_TARGET) == ["specs/schema/auger-spec-1.0.xsd"], (
        f"the grammar must ship at {SCHEMA_TARGET}: {declared!r}"
    )
    assert declared.get(EXAMPLE_TARGET) == ["examples/xml/minimal.xml"], (
        f"the example corpus must ship at {EXAMPLE_TARGET}: {declared!r}"
    )
    assert SCHEMA_SOURCE.is_file() and EXAMPLE_SOURCE.is_file(), (
        "the declared sources must exist in the checkout"
    )


def test_the_wheel_carries_the_schema_and_the_example(built_wheel: Path) -> None:
    """AC1 + AC3: the BUILT wheel holds the grammar and the example, byte for byte."""
    with zipfile.ZipFile(built_wheel) as archive:
        members = archive.namelist()
        schema_member = _data_member(built_wheel, SCHEMA_TARGET, auger.SPEC_SCHEMA_NAME)
        assert schema_member, (
            f"{built_wheel.name} ships no schema under {SCHEMA_TARGET}/ — members: {members}"
        )
        example_member = _data_member(built_wheel, EXAMPLE_TARGET, EXAMPLE_SOURCE.name)
        assert example_member, (
            f"{built_wheel.name} ships no example under {EXAMPLE_TARGET}/ — members: {members}"
        )
        assert archive.read(schema_member) == SCHEMA_SOURCE.read_bytes(), (
            "the wheel's grammar differs from the checkout's — the copied file is stale"
        )
        assert archive.read(example_member) == EXAMPLE_SOURCE.read_bytes(), (
            "the wheel's example differs from the checkout's"
        )
        # The module still ships, so the data is beside the code it serves rather than instead.
        assert "auger.py" in members, members


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_site_packages(venv: Path) -> Path:
    return venv / (
        "Lib/site-packages"
        if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )


def _provision_offline_processor(site_packages: Path) -> list[str]:
    """Copy the built lxml out of THIS interpreter into the fresh venv (no network, no wheelhouse).

    The wheel under test is installed offline; lxml has no offline wheel on this host, so the
    processor is provisioned by copying the already-built distribution. That is stated in the
    commit and in the evidence rather than papered over with a network install.
    """
    spec = importlib.util.find_spec("lxml")
    origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
    if origin is None:
        pytest.fail(
            "lxml (the XSD processor) is not importable here, so the fresh-venv arm cannot "
            "validate anything — install the dev extra (`pip install -e '.[dev]'`)"
        )
    source_site = origin.parent.parent
    copied: list[str] = []
    for pattern in ("lxml", "lxml-*.dist-info", "lxml.libs"):
        for path in sorted(source_site.glob(pattern)):
            target = site_packages / path.name
            if target.exists():
                continue
            if path.is_dir():
                shutil.copytree(path, target)
            else:
                shutil.copy2(path, target)
            copied.append(path.name)
    assert "lxml" in copied or (site_packages / "lxml").is_dir(), (
        f"the processor was not provisioned into {site_packages}"
    )
    return copied


def test_a_fresh_venv_install_validates_the_shipped_example(
    built_wheel: Path, tmp_path: Path
) -> None:
    """AC2: `pip install <wheel>` in a throwaway venv, then the documented command validates.

    Nothing here reads auger's checkout as source: the venv's `auger.__file__` is asserted to live
    INSIDE the venv, and the corpus validated is the one the wheel installed.
    """
    _require_build_tooling()
    venv = tmp_path / "fresh"
    created = _run([sys.executable, "-m", "venv", str(venv)])
    assert created.returncode == 0, f"venv creation failed:\n{created.stderr}"
    python = _venv_python(venv)

    installed = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            str(built_wheel),
            "--no-index",
            "--no-deps",
        ]
    )
    assert installed.returncode == 0, (
        f"the wheel did not install offline:\nstdout:\n{installed.stdout}\n"
        f"stderr:\n{installed.stderr}"
    )

    # The grammar and the example must be at the DOCUMENTED installed path.
    schema = venv / "share" / "auger" / "specs" / "schema" / auger.SPEC_SCHEMA_NAME
    examples = venv / "share" / "auger" / "specs" / "examples"
    assert schema.is_file(), f"no installed grammar at {schema}"
    assert (examples / "minimal.xml").is_file(), (
        f"no installed example under {examples}"
    )

    _provision_offline_processor(_venv_site_packages(venv))

    # Anti-false-green: the module under test is the INSTALLED one, not a checkout on the path.
    # Run from a neutral cwd as well — `python -c` puts the cwd first on sys.path, so a check run
    # from the repo root would import the checkout and prove nothing.
    where = _run(
        [str(python), "-c", "import auger; print(auger.__file__)"], cwd=str(tmp_path)
    )
    assert where.returncode == 0, (
        f"`import auger` failed in the fresh venv:\n{where.stderr}"
    )
    module_path = Path(where.stdout.strip()).resolve()
    assert module_path.is_relative_to(venv.resolve()), (
        f"the fresh venv imported {module_path}, outside {venv} — this arm proved nothing"
    )

    # `--repo-root` is explicit by design (AUG-127): obligation paths are proved tracked in a
    # repository, so the command names one. The shipped example declares no obligations at all.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    init = _run(["git", "init", "-q", str(consumer)])
    assert init.returncode == 0, f"git init failed:\n{init.stderr}"

    validated = _run(
        [
            str(venv / "bin" / "auger"),
            "spec",
            "validate",
            str(examples),
            "--repo-root",
            str(consumer),
        ],
        cwd=str(tmp_path),
    )
    assert validated.returncode == 0, (
        "the documented command did not validate the shipped example:\n"
        f"stdout:\n{validated.stdout}\nstderr:\n{validated.stderr}"
    )
    assert validated.stdout.startswith("spec: 1 document(s), 2 block(s)"), (
        f"unexpected verdict from the fresh venv: {validated.stdout!r} {validated.stderr!r}"
    )


def test_with_no_schema_anywhere_the_loader_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shipping the file must not turn an ABSENT schema into a verdict (fail-closed, unchanged)."""
    monkeypatch.setattr(
        auger, "SPEC_SCHEMA_PATH", tmp_path / "absent" / auger.SPEC_SCHEMA_NAME
    )
    monkeypatch.setattr(auger, "_spec_data_roots", lambda: ())
    with pytest.raises(auger.SpecError) as caught:
        auger.spec_schema_path()
    assert caught.value.code == "xsd-schema-missing", caught.value
    assert "share/auger/specs/schema" in caught.value.detail, (
        f"the refusal does not tell the user where an installed grammar lives: {caught.value}"
    )

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.xml").write_text(
        '<spec xmlns="urn:auger:spec:1" id="AUG-NO-SCHEMA-1" version="1.0.0" '
        'schema-version="1.0"><section id="NO-SCHEMA-SEC-1" kind="section" status="active" '
        'version="1.0.0">text</section></spec>',
        encoding="utf-8",
    )
    code, out, err = _run_spec("validate", str(corpus))
    assert code == 2, f"a missing grammar reported rc={code}: {out!r}"
    assert out == "", "a refusal printed a verdict"
    assert "spec error (xsd-schema-missing)" in err, err


def test_the_installed_layout_is_a_resolution_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `data`-scheme location is what an installed auger resolves, and it still loads."""
    root = tmp_path / "prefix"
    installed = root / auger.SPEC_SCHEMA_INSTALL_RELPATH
    installed.parent.mkdir(parents=True)
    shutil.copy2(SCHEMA_SOURCE, installed)

    monkeypatch.setattr(
        auger, "SPEC_SCHEMA_PATH", tmp_path / "absent" / auger.SPEC_SCHEMA_NAME
    )
    monkeypatch.setattr(auger, "_spec_data_roots", lambda: (root,))

    assert (
        auger.spec_schema_candidates()[0]
        == tmp_path / "absent" / auger.SPEC_SCHEMA_NAME
    )
    assert auger.spec_schema_path() == installed
    assert auger._spec_load_schema() is not None, (
        "the installed grammar resolved but did not load — the path is right and the file is not"
    )


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in (REPO_ROOT / "tests" / "gate.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_packaging_wheel.py" in arm[0], (
        "tests/test_packaging_wheel.py is not in the gate's pytest arm — the shipping claim would "
        "be an unrun proof"
    )
