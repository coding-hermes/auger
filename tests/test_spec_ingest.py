"""AUG-176 ingestion: markdown -> XML corpus conversion, coverage and recall battery.

Tests marked HERMETIC run on fixtures in tmp dirs; tests over the SHIPPED corpora in
corpora/ re-drive the committed XML through auger's own validators and re-verify the
integrity contract two ways (AUG-179):

  * PORTABLE (runs everywhere, CI included): every shipped block's whitespace-normalized
    text must still hash to the `text_sha256` digest the converter computed FROM THE
    SOURCE SPAN at conversion time and committed in the manifest. No external filesystem
    is needed; a mutated or truncated block refuses with its block id named.
  * HOST (only where the sources exist): the true verbatim containment assertion against
    the source files. It runs when the manifest's declared root (or an env override, or
    this repo's own tree for in-repo sources) resolves on this host; otherwise it SKIPS
    with a NAMED reason — a missing source root is never a green test that asserted
    nothing silently. When the manifest carries `source_revision` (AUG-198), containment
    failure against a live tree newer than the bound git HEAD is a further typed skip
    naming both revisions (upstream drifted; regenerate the corpus to re-bind) — a
    failure at the bound revision itself stays a hard failure.

Env knobs the tests honour:
  AUGER_CORPORA_ROOT        corpora root override (default: <repo>/corpora)
  AUGER_SPEC_SOURCE_ROOTS   comma-separated `project=<root>` overrides; naming a project
                            pins its root for the host leg (a nonexistent pinned root
                            simulates a CI host where the sources are absent).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
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


def _corpora_root() -> Path:
    return Path(os.environ.get("AUGER_CORPORA_ROOT", str(REPO_ROOT / "corpora")))


def _load_manifest(corpora: Path, project: str) -> dict:
    return json.loads((corpora / project / "manifest.json").read_text(encoding="utf-8"))


def _source_roots_override() -> dict[str, str]:
    override = os.environ.get("AUGER_SPEC_SOURCE_ROOTS", "")
    return dict(part.split("=", 1) for part in override.split(",") if "=" in part)


def _resolve_source_file(project: str, manifest: dict, document: dict) -> Path | None:
    """Resolve a document's source file on THIS host, or None when unavailable.

    Resolution order: the AUGER_SPEC_SOURCE_ROOTS pin for this project (when set, it is
    authoritative — a missing pinned root means "sources absent", the CI simulation);
    the source as recorded absolutely at conversion time; the manifest's declared
    source_root; and finally this repo's own tree, for sources that are committed in
    it (the auger-docs corpus converts docs/ of THIS repository).
    """
    rel_from_root = Path(document["source_abs"]).relative_to(manifest["source_root"])
    override = _source_roots_override()
    if project in override:
        pinned = Path(override[project])
        if not pinned.exists():
            return None
        return pinned / rel_from_root
    as_recorded = Path(document["source_abs"])
    if as_recorded.exists():
        return as_recorded
    declared_root = Path(manifest["source_root"])
    if declared_root.exists():
        return declared_root / rel_from_root
    in_repo = REPO_ROOT / document["source"]
    if in_repo.exists():
        return in_repo
    return None


def _live_source_revision(source: Path | None) -> str | None:
    """Git HEAD of the worktree owning `source` (AUG-198), or None.

    Determined best-effort from the resolved source's own directory: a non-git
    tree, a bare pin onto a scratch copy, or an absent git all return None, which
    leaves the verbatim leg exactly as strict as before AUG-198.
    """
    if source is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(source.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


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
def test_shipped_block_digests_match_manifest():
    """PORTABLE integrity leg (AUG-179): runs on any host, CI included.

    Every shipped block's whitespace-normalized text must still hash to the
    `text_sha256` the converter computed from the block's SOURCE SPAN at conversion
    time and committed in the manifest. No external filesystem is consulted, so a
    mutated, truncated or reordered block refuses here with its block id named —
    even on a host where none of the external source trees exist.
    """
    corpora = _corpora_root()
    for project in PROJECTS:
        manifest = _load_manifest(corpora, project)
        ingest.validate_manifest(manifest)  # refuses a manifest without valid digests
        for document in manifest["documents"]:
            tree = _parse_with_comments(corpora / project / document["document"])
            blocks = tree.getroot().findall(f"{NS}section")
            assert len(blocks) == document["block_count"], document["source"]
            digests = {b["id"]: b["text_sha256"] for b in document["blocks"]}
            for block in blocks:
                block_id = block.get("id")
                assert block_id in digests, (
                    f"{project}: block {block_id} is absent from the manifest"
                )
                body = " ".join((block.text or "").split())
                actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
                assert actual == digests[block_id], (
                    f"{project}: block {block_id} no longer matches its committed "
                    "source digest (text_sha256) — the shipped XML drifted from the "
                    "manifest written by scripts/ingest_markdown_specs.py"
                )


@pytest.mark.skipif(
    not all((REPO_ROOT / "corpora" / p / "manifest.json").exists() for p in PROJECTS),
    reason="shipped corpora not generated yet",
)
@pytest.mark.parametrize("project", PROJECTS)
def test_shipped_blocks_are_verbatim_and_manifests_reconcile(project):
    """HOST containment leg: the true verbatim assertion against the real sources.

    Parametrized per project so EACH unavailable root yields its own NAMED skip —
    a named skip IS the evidence; the digest leg covered integrity on that host.
    AUG-198: when the manifest records `source_revision`, the corpus is bound to
    that upstream git HEAD. Containment still runs verbatim every time; a
    containment FAILURE against a live tree that has moved PAST the bound revision
    is classified as upstream source drift (typed skip naming both revisions —
    regenerate the corpus to re-bind), while a failure AT the bound revision stays
    a hard assertion. A manifest without `source_revision` (pre-AUG-198 corpora)
    keeps the original hard-fail behavior untouched.
    """
    corpora = _corpora_root()
    manifest = _load_manifest(corpora, project)
    ingest.validate_manifest(manifest)
    resolved = _resolve_source_file(project, manifest, manifest["documents"][0])
    if resolved is None:
        pytest.skip(
            f"source root for corpora/{project} not available on this host "
            f"(manifest declares {manifest['source_root']}); the portable digest "
            "leg (test_shipped_block_digests_match_manifest) covered corpus "
            "integrity here"
        )
    recorded_revision = manifest.get("source_revision")
    live_revision = _live_source_revision(resolved)
    for document in manifest["documents"]:
        source = _resolve_source_file(project, manifest, document)
        assert source is not None and source.exists(), f"source vanished: {source}"
        normalized = " ".join(
            source.read_text(encoding="utf-8", errors="replace").split()
        )
        tree = _parse_with_comments(corpora / project / document["document"])
        blocks = tree.getroot().findall(f"{NS}section")
        assert len(blocks) == document["block_count"], document["source"]
        for block in blocks:
            body = " ".join((block.text or "").split())
            if body not in normalized:
                if (
                    recorded_revision
                    and live_revision
                    and live_revision != recorded_revision
                ):
                    pytest.skip(
                        f"{project}: upstream source drift past the manifest's "
                        f"recorded revision (corpus bound to {recorded_revision}, "
                        f"live tree at {live_revision}); first containment break at "
                        f"{document['source']}/{block.get('id')} — regenerate "
                        f"corpora/{project} with scripts/ingest_markdown_specs.py "
                        "to re-bind; the portable digest leg "
                        "(test_shipped_block_digests_match_manifest) covered the "
                        "shipped corpus's integrity here"
                    )
                assert body in normalized, (
                    f"{project}/{block.get('id')} is not a verbatim span of its source"
                )


def test_manifest_blocks_carry_source_span_digests(tmp_path):
    """HERMETIC (AUG-179): the converter digests each block's SOURCE span, and the
    digest matches the same normalization applied to the EMITTED block text."""
    out_root, manifest = _convert_fixture(tmp_path)
    for document in manifest["documents"]:
        tree = _parse_with_comments(out_root / document["document"])
        for block_record, block in zip(
            document["blocks"], tree.getroot().findall(f"{NS}section"), strict=True
        ):
            assert block_record["source"].startswith(document["source"] + "#")
            body = " ".join((block.text or "").split())
            expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
            assert block_record["text_sha256"] == expected, block_record["id"]


def test_validate_manifest_refuses_a_block_without_source_digest():
    manifest = {
        "documents": [
            {
                "source": "fixture/INTRO.md",
                "section_count": 1,
                "block_count": 1,
                "blocks": [{"id": "FIX-X-S000", "text_sha256": ""}],
                "skipped": [],
            }
        ],
        "totals": {"files": 1, "sections": 1, "blocks": 1, "skipped": 0},
    }
    with pytest.raises(SystemExit, match="text_sha256"):
        ingest.validate_manifest(manifest)


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


def test_recall_hit_counts_distinct_blocks_not_rows():
    """AUG-178 follow-up: the store returns every key twice, so top-5 ROWS was top-2..4 BLOCKS."""
    target = "FIX-INTRO-SETUP-S001"
    dup = [
        {"key": "/auger/AUG176-fixture/A"},
        {"key": "/auger/AUG176-fixture/A"},
        {"key": "/auger/AUG176-fixture/B"},
        {"key": "/auger/AUG176-fixture/B"},
        {"key": f"/auger/AUG176-fixture/{target}"},
        {"key": f"/auger/AUG176-fixture/{target}"},
    ]
    assert recall_battery._distinct_keys(dup) == ["A", "B", target]
    assert recall_battery._rank(dup, target) == 3
    # A PART row is the same block: it ranks with its block, it does not add a slot.
    parts = [
        {"key": "/auger/AUG176-fixture/A"},
        {"key": f"/auger/AUG176-fixture/{target}-P01"},
        {"key": f"/auger/AUG176-fixture/{target}-P02"},
    ]
    assert recall_battery._rank(parts, target) == 2
    assert recall_battery._hit(parts, target) is True


def test_recall_battery_hit_is_top_five_blocks_and_wide_window(tmp_path, monkeypatch):
    """A target at distinct position 6 is a MISS; at 5 a HIT — and the window is CANDIDATE_ROWS."""
    _out_root, _manifest = _convert_fixture(tmp_path)
    monkeypatch.setattr(recall_battery, "CORPORA_ROOT", _out_root.parent)
    monkeypatch.setattr(
        recall_battery,
        "load_blocks",
        lambda project: [
            {"id": "FIX-INTRO-SETUP-S001", "text": "x", "status": "active"}
        ],
    )
    queries = [{"query": "q", "target": "FIX-INTRO-SETUP-S001"}]
    seen: dict = {}

    def fake_recall(ns, q, limit, **kwargs):
        seen["limit"] = limit
        keys = [f"/auger/AUG176-fixture/FILLER-{i:02d}" for i in range(1, 6)]
        if seen.pop("tailed", False):
            return [{"key": k} for k in keys] + [
                {"key": "/auger/AUG176-fixture/FIX-INTRO-SETUP-S001"}
            ]
        seen["tailed"] = True
        return [{"key": k} for k in keys[:4]] + [
            {"key": "/auger/AUG176-fixture/FIX-INTRO-SETUP-S001"}
        ]

    monkeypatch.setattr(recall_battery.auger, "recall", fake_recall)
    report = recall_battery.run_battery("fixture", queries)
    assert seen["limit"] == recall_battery.CANDIDATE_ROWS
    assert report["rows"][0]["rank"] == 5 and report["hits"] == 1
    report = recall_battery.run_battery("fixture", queries)
    assert report["rows"][0]["rank"] == 6 and report["hits"] == 0


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
