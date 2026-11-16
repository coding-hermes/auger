"""AUG-176 ingestion: markdown -> XML corpus conversion, coverage and recall battery.

Tests marked HERMETIC run on fixtures in tmp dirs; tests over the SHIPPED corpora in
corpora/ re-drive the committed XML through auger's own validators and (when the external
source trees exist on this host) re-check the verbatim contract against the sources.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

ingest = importlib.import_module("ingest_markdown_specs")
recall_battery = importlib.import_module("recall_battery")

NS = "{urn:auger:spec:1}"
PROJECTS = ("code-factory", "auger-docs", "coding-hermes-director")

FIXTURE_FILES = {
    "INTRO.md": (
        "# Fixture\n\n"
        "Preamble prose that must survive as its own block.\n\n"
        "## Setup\n\nInstall the tool.\n\n```\ncode <fence> & entity\n```\n\n"
        "### Sub-setup details\n\nDetails under an H3.\n\n"
        "#### H4 stays inside its parent\n\nH4 prose belongs to the enclosing H3 block.\n\n"
        "## Teardown\n\nTear it down.\n"
    ),
    "SECOND.md": "## Only heading\n\nBody only.\n",
}


def _parse_with_comments(path: Path):
    """lxml keeps comments; the standard library drops them (provenance lives in comments)."""
    from lxml import etree

    return etree.parse(str(path))


def _is_comment(node) -> bool:
    from lxml import etree

    return node is not None and isinstance(node, etree._Comment)


def _write_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in FIXTURE_FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


def _convert_fixture(tmp_path: Path) -> tuple[Path, dict]:
    source_root = _write_fixture(tmp_path / "src")
    ingest.PROJECTS["fixture"] = {
        "prefix": "FIX",
        "spec_id": "FIXTURE-CORPUS",
        "scope": "ingest.fixture",
        "source_root": str(source_root),
        "globs": ["*.md"],
        "source_label": "test fixture",
    }
    out_root = tmp_path / "corpus"
    manifest = ingest.convert_project("fixture", out_root)
    return out_root, manifest


def _run_auger(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "auger.py"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_gate_pytest_arm_names_this_file():
    """The gate's pytest arm names files explicitly; an unnamed file is unenforced."""
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text(encoding="utf-8")
    assert "tests/test_spec_ingest.py" in gate


def test_gate_validates_every_converted_corpus():
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text(encoding="utf-8")
    loop = "for corpus_project in code-factory auger-docs coding-hermes-director"
    assert loop in gate, "gate corpus arm does not enumerate the converted corpora"
    assert "spec validate" in gate and "spec check" in gate


def test_converter_is_deterministic(tmp_path):
    out_one, manifest_one = _convert_fixture(tmp_path / "one")
    out_two, manifest_two = _convert_fixture(tmp_path / "two")
    files_one = sorted(p.name for p in out_one.glob("*") if p.suffix == ".xml")
    files_two = sorted(p.name for p in out_two.glob("*") if p.suffix == ".xml")
    assert files_one == files_two
    for name in files_one:
        assert (out_one / name).read_bytes() == (out_two / name).read_bytes(), name

    # The manifests agree on everything but the tmp location of the sources.
    def portable(manifest):
        stripped = json.loads(json.dumps(manifest))
        stripped.pop("source_root")
        for document in stripped["documents"]:
            document.pop("source_abs")
        return stripped

    assert portable(manifest_one) == portable(manifest_two)


def test_emitted_document_passes_validate_and_check(tmp_path):
    out_root, _manifest = _convert_fixture(tmp_path)
    for verb in ("validate", "check"):
        run = _run_auger("spec", verb, str(out_root))
        assert run.returncode == 0, (
            f"spec {verb} refused the fixture corpus:\n{run.stderr}"
        )
    validate_run = _run_auger("spec", "validate", str(out_root))
    assert "ok" in validate_run.stdout


def test_every_h2_h3_maps_to_one_block_or_a_reasoned_skip(tmp_path):
    _out_root, manifest = _convert_fixture(tmp_path)
    ingest.validate_manifest(manifest)  # must not raise
    # The fixture's counts: INTRO.md = preamble + 2 H2 + 1 H3 = 4; SECOND.md has no preamble
    # text and one H2 = 1 (a heading-less preamble block would not be verbatim source prose).
    counts = {d["source"]: d["section_count"] for d in manifest["documents"]}
    assert counts == {"fixture/INTRO.md": 4, "fixture/SECOND.md": 1}
    # A skip without a reason is refused (the mechanism AC1 demands).
    broken = json.loads(json.dumps(manifest))
    broken["documents"][0]["skipped"].append({"title": "ghost", "reason": ""})
    with pytest.raises(SystemExit, match="no reason"):
        ingest.validate_manifest(broken)


def test_block_text_is_verbatim_substring_of_source(tmp_path):
    source_root = tmp_path / "src"
    _write_fixture(source_root)
    out_root, manifest = _convert_fixture(tmp_path)
    for document in manifest["documents"]:
        source = Path(document["source_abs"]).read_text(encoding="utf-8")
        normalized = " ".join(source.split())
        tree = _parse_with_comments(out_root / document["document"])
        for block in tree.getroot().findall(f"{NS}section"):
            body = " ".join((block.text or "").split())
            assert body, f"empty block {block.get('id')}"
            assert body in normalized, (
                f"block {block.get('id')} is not verbatim source text"
            )
            previous = block.getprevious()
            assert _is_comment(previous) and "source: " in (previous.text or ""), (
                f"provenance comment missing before {block.get('id')}"
            )


def test_no_raw_trace_marker_in_emitted_documents(tmp_path):
    """A converted source quoting its own `ch:trace` marker must not emit the marker raw:
    the align scanner resolves raw markers against THIS repo and would refuse the corpus."""
    out_root, manifest = _convert_fixture(tmp_path)
    for document in manifest["documents"]:
        raw = (out_root / document["document"]).read_text(encoding="utf-8")
        assert "ch:trace" not in raw, document["document"]
    # ...and the escape must parse back to identical text (verbatim contract intact).
    ingest.PROJECTS["fixture"]["source_root"] = str(tmp_path / "src")
    (tmp_path / "src" / "TRACED.md").write_text(
        "## Tracing\n\ncite it as ch:trace row=X spec=docs/SPEC-001-graph.md\n",
        encoding="utf-8",
    )
    out_root2, manifest2 = _convert_fixture(tmp_path)
    raw = (out_root2 / "TRACED.xml").read_text(encoding="utf-8")
    assert "ch&#58;trace" in raw and "ch:trace" not in raw
    tree = _parse_with_comments(out_root2 / "TRACED.xml")
    body = tree.getroot().findall(f"{NS}section")[0].text
    assert "ch:trace row=X spec=docs/SPEC-001-graph.md" in body


def test_shipped_corpus_documents_validate_and_check():
    for project in PROJECTS:
        root = REPO_ROOT / "corpora" / project
        if not (root / "manifest.json").exists():
            pytest.skip(
                f"corpora/{project} not generated yet (named skip, never a vacuous pass)"
            )
        for verb in ("validate", "check"):
            run = _run_auger("spec", verb, str(root))
            assert run.returncode == 0, (
                f"spec {verb} corpora/{project} rc={run.returncode}:\n{run.stderr}"
            )


@pytest.mark.skipif(
    not all((REPO_ROOT / "corpora" / p / "manifest.json").exists() for p in PROJECTS),
    reason="shipped corpora not generated yet",
)
def test_shipped_blocks_are_verbatim_and_manifests_reconcile():
    for project in PROJECTS:
        manifest = json.loads(
            (REPO_ROOT / "corpora" / project / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        ingest.validate_manifest(manifest)
        for document in manifest["documents"]:
            source = Path(document["source_abs"])
            assert source.exists(), f"source vanished: {source}"
            normalized = " ".join(
                source.read_text(encoding="utf-8", errors="replace").split()
            )
            tree = _parse_with_comments(
                REPO_ROOT / "corpora" / project / document["document"]
            )
            blocks = tree.getroot().findall(f"{NS}section")
            assert len(blocks) == document["block_count"], document["source"]
            for block in blocks:
                body = " ".join((block.text or "").split())
                assert body in normalized, (
                    f"{project}/{block.get('id')} is not a verbatim span of its source"
                )


def test_recall_hit_requires_target_key_in_hits():
    target = "FIX-INTRO-SETUP-S001"
    assert recall_battery._hit([], target) is False
    assert (
        recall_battery._hit(
            [{"key": "/auger/AUG176-fixture/FIX-INTRO-TEARDOWN-S003"}], target
        )
        is False
    )
    assert (
        recall_battery._hit([{"key": f"/auger/AUG176-fixture/{target}"}, {}], target)
        is True
    )
    assert recall_battery._hit([{"key": "no keys at all"}], target) is False


def test_recall_battery_reports_misses_instead_of_passing(tmp_path, monkeypatch):
    out_root, _manifest = _convert_fixture(tmp_path)
    monkeypatch.setattr(recall_battery, "CORPORA_ROOT", out_root.parent)
    monkeypatch.setattr(
        recall_battery,
        "load_blocks",
        lambda project: [
            {
                "id": "FIX-INTRO-SETUP-S001",
                "text": "Install the tool.",
                "status": "active",
            }
        ],
    )
    queries = [{"query": "how do I set it up", "target": "FIX-INTRO-SETUP-S001"}]
    # A search that returns nothing must READ as a miss, never as a pass.
    monkeypatch.setattr(recall_battery.auger, "recall", lambda *a, **k: [])
    report = recall_battery.run_battery("fixture", queries)
    assert report["hits"] == 0 and report["rows"][0]["hit"] is False
    # And a search that returns the target must read as a hit.
    monkeypatch.setattr(
        recall_battery.auger,
        "recall",
        lambda *a, **k: [{"key": "/auger/AUG176-fixture/FIX-INTRO-SETUP-S001"}],
    )
    report = recall_battery.run_battery("fixture", queries)
    assert report["hits"] == 1 and report["rows"][0]["hit"] is True


def test_recall_battery_refuses_unresolvable_target(tmp_path, monkeypatch):
    out_root, _manifest = _convert_fixture(tmp_path)
    monkeypatch.setattr(recall_battery, "CORPORA_ROOT", out_root.parent)
    monkeypatch.setattr(
        recall_battery,
        "load_blocks",
        lambda project: [
            {"id": "FIX-INTRO-SETUP-S001", "text": "x", "status": "active"}
        ],
    )
    with pytest.raises(SystemExit, match="not a block of the corpus"):
        recall_battery.resolve_targets("fixture", [("q", "FIX-NOPE-NOPE-S999")])
    with pytest.raises(SystemExit, match="unresolved target"):
        recall_battery.resolve_targets("fixture", [("q", None)])
