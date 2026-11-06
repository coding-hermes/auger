"""AUG-136 criterion 3: the authored corpus is the SOURCE of a generated, byte-pinned Markdown view.

Criteria 1 and 2 already hold at this base: `specs/` carries three authored documents
(`spec-graph.xml`, `spec-verbs.xml`, `verification.xml`), `auger spec validate specs` and
`auger spec check specs` both exit 0, and tests/gate.sh runs both verbs over `specs/`. What was
missing is the third line:

    "PASS: the authored corpus is the source for a generated Markdown view committed to docs/, and a
     test regenerates that view from the XML and asserts byte equality, so the view cannot drift
     from its source."

`docs/SPEC-CORPUS.md` is hand-written prose and there was no generator for any spec view (`auger
spec` verbs: validate|list|assemble|check|bundle|align — no render verb). `scripts/gen_spec_view.py`
now renders `docs/SPEC-CORPUS-VIEW.md` from `specs/` through auger's own loader, and this file is
the parity gate — the shape `scripts/gen_openapi.py` + `tests/test_openapi_parity.py` already use
for the served API.

Arms:
  byte parity ............ test_committed_view_is_exactly_what_the_generator_emits
  derived, not copied .... test_mutating_the_corpus_mutates_the_view
  completeness ........... test_every_document_and_block_in_the_corpus_appears_in_the_view
  counts are measured ..... test_the_view_counts_come_from_the_loader
  reference index ......... test_the_reference_index_lists_every_typed_edge
  narrative stays linked .. test_the_narrative_doc_links_the_generated_view
  determinism ............. test_two_renders_of_one_corpus_are_identical
  location independence ... test_a_byte_identical_corpus_renders_identically_wherever_it_lives
  the arm itself .......... test_gate_arm_runs_this_file

ch:trace row=AUG-136 spec=docs/SPEC-CORPUS.md test=tests/test_spec_corpus_view.py doc=docs/SPEC-CORPUS-VIEW.md
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import REPO_ROOT  # tests/conftest.py defines and exports it

import auger  # noqa: E402  (conftest puts REPO_ROOT on sys.path first)
from scripts import gen_spec_view  # noqa: E402  (needs REPO_ROOT on sys.path first)

SPECS = REPO_ROOT / "specs"
VIEW = REPO_ROOT / "docs" / "SPEC-CORPUS-VIEW.md"
NARRATIVE = REPO_ROOT / "docs" / "SPEC-CORPUS.md"
REGENERATE = "python3 scripts/gen_spec_view.py"


def _loaded():
    return auger.load_spec_corpus(SPECS)


# ------------------------------------------------------------------ byte parity
def test_committed_view_is_exactly_what_the_generator_emits() -> None:
    """The committed view must be BYTE-identical to a fresh render of the current corpus."""
    assert VIEW.is_file(), f"{VIEW} is missing — generate it: {REGENERATE}"
    committed = VIEW.read_text(encoding="utf-8")
    generated = gen_spec_view.build_view(SPECS)
    assert committed == generated, (
        "docs/SPEC-CORPUS-VIEW.md has drifted from specs/ — the XML is the source, so regenerate "
        f"the view: {REGENERATE}"
    )


def test_the_generator_check_mode_reports_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--check` is the same verdict for a human: rc 0 in sync, rc 1 naming the command on drift."""
    assert gen_spec_view.main(["--check"]) == 0
    assert "matches the corpus" in capsys.readouterr().out

    corpus = _copy_corpus(tmp_path)
    before = gen_spec_view.build_view(corpus)
    stale = tmp_path / "SPEC-CORPUS-VIEW.md"
    stale.write_text(before, encoding="utf-8")
    _mutate(corpus)

    monkeypatch.setattr(gen_spec_view, "SPECS_ROOT", corpus)
    monkeypatch.setattr(gen_spec_view, "VIEW_PATH", stale)
    assert gen_spec_view.main(["--check"]) == 1
    stderr = capsys.readouterr().err
    assert REGENERATE in stderr, (
        f"the drift message must name the regeneration command: {stderr}"
    )


# --------------------------------------------------------------- derived, not copied
def test_mutating_the_corpus_mutates_the_view(tmp_path: Path) -> None:
    """The view is DERIVED: an edit to one block's prose and one block's status shows up in it."""
    corpus = _copy_corpus(tmp_path)
    before = gen_spec_view.build_view(corpus)
    _mutate(corpus)
    after = gen_spec_view.build_view(corpus)
    assert after != before, (
        "the view did not change when the corpus did — it is not derived"
    )
    assert "the corpus is the source of this sentence" in after, (
        "the mutated prose is absent from the regenerated view"
    )
    assert "the corpus is the source of this sentence" not in before
    assert "· superseded ·" in after, "the mutated block status is absent from the view"


def _copy_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "specs"
    shutil.copytree(SPECS, corpus)
    return corpus


def _mutate(corpus: Path) -> None:
    """Edit prose and a status in ONE authored document (the source of the view)."""
    target = corpus / "spec-graph.xml"
    text = target.read_text(encoding="utf-8")
    original = "`auger spec validate` and `auger spec check` read one corpus through one loader"
    assert original in text, "the fixture sentence this mutation replaces is gone"
    text = text.replace(original, "the corpus is the source of this sentence")
    old_status = 'id="SPEC-GRAPH-CORPUS-V1" kind="verification" status="active"'
    assert old_status in text, "the fixture status this mutation replaces is gone"
    text = text.replace(
        old_status, 'id="SPEC-GRAPH-CORPUS-V1" kind="verification" status="superseded"'
    )
    target.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ completeness
def test_every_document_and_block_in_the_corpus_appears_in_the_view() -> None:
    """Non-vacuous: every authored document and every block id in the loader's output is present."""
    _root, documents, blocks = _loaded()
    assert documents, "the corpus loader returned no documents"
    assert blocks, "the corpus loader returned no blocks"
    view = VIEW.read_text(encoding="utf-8")
    for rel in documents:
        assert f"## `{rel}`" in view, (
            f"document {rel} is missing from the generated view"
        )
    for block in blocks:
        assert f"### {block['id']} ·" in view, (
            f"block {block['id']} is missing from the view"
        )
        assert block["text"] in view, (
            f"block {block['id']}'s prose is missing from the view"
        )


def test_the_view_counts_come_from_the_loader() -> None:
    """The numbers in the view are measured from the corpus on every render, never maintained."""
    _root, documents, blocks = _loaded()
    references = sum(
        len(targets) for block in blocks for targets in block["refs"].values()
    )
    view = VIEW.read_text(encoding="utf-8")
    assert f"- documents: {len(documents)}" in view, view[:400]
    assert f"- blocks: {len(blocks)}" in view, view[:400]
    assert f"- references: {references}" in view, view[:400]


def test_the_reference_index_lists_every_typed_edge() -> None:
    """Every declared edge appears as one table row, so the reference count is auditable."""
    _root, _documents, blocks = _loaded()
    edges = [
        (block["id"], attribute, target)
        for block in blocks
        for attribute in auger.SPEC_EDGE_ATTRS
        for target in block["refs"].get(attribute, ())
    ]
    assert edges, "the corpus declares no typed edges — this arm would prove nothing"
    view = VIEW.read_text(encoding="utf-8")
    for source, attribute, target in edges:
        assert f"| `{source}` | `{attribute}` | `{target}` |" in view, (
            f"edge {source} --{attribute}--> {target} is missing from the reference index"
        )
    assert view.count("\n| `") >= len(edges), "fewer table rows than declared edges"


# --------------------------------------------------------------- narrative stays
def test_the_narrative_doc_links_the_generated_view() -> None:
    """docs/SPEC-CORPUS.md keeps its prose and points at the generated view (no second copy)."""
    narrative = NARRATIVE.read_text(encoding="utf-8")
    assert "SPEC-CORPUS-VIEW.md" in narrative, (
        "docs/SPEC-CORPUS.md must link the generated view, so a reader lands on the artifact the "
        "parity test pins"
    )
    assert "scripts/gen_spec_view.py" in narrative, (
        "docs/SPEC-CORPUS.md must name the regeneration command"
    )


def test_two_renders_of_one_corpus_are_identical() -> None:
    """Determinism: the render is a pure function of the corpus (no timestamps, no dict order)."""
    assert gen_spec_view.build_view(SPECS) == gen_spec_view.build_view(SPECS)


def test_a_byte_identical_corpus_renders_identically_wherever_it_lives(
    tmp_path: Path,
) -> None:
    """Provenance is the corpus CONTENT, not the directory it sits in (AUG-137 P2).

    `scripts/replay_prd_proofs.py` documents `--specs-root` as the knob for measuring a corpus
    copy, and P2 compares the committed view against a render of that root. If the render carries
    the root directory's name or a checkout-relative path, a byte-identical copy outside the repo
    renders differently and P2 FAILS on unmutated input — the knob is then unusable and the
    reproducibility claim is false.
    """
    elsewhere = tmp_path / "not-the-checkout" / "arbitrary-directory-name"
    elsewhere.parent.mkdir(parents=True)
    shutil.copytree(SPECS, elsewhere)
    assert gen_spec_view.build_view(elsewhere) == gen_spec_view.build_view(SPECS), (
        "the rendered view depends on where the corpus lives, so a byte-identical copy cannot "
        "reproduce the committed view — render paths relative to the corpus root instead"
    )


def test_the_rendered_view_names_no_checkout_absolute_path() -> None:
    """The committed view is an artifact other hosts must be able to regenerate byte for byte."""
    view = gen_spec_view.build_view(SPECS)
    assert str(REPO_ROOT) not in view, (
        f"the rendered view embeds this checkout's path ({REPO_ROOT}) — no other host can "
        "regenerate it"
    )
    for token in view.split():
        assert not token.startswith("/"), (
            f"the rendered view embeds the absolute path {token!r} — paths must be relative to "
            "the corpus root"
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
    assert "tests/test_spec_corpus_view.py" in arm[0], (
        "tests/test_spec_corpus_view.py is not in the gate's pytest arm — the parity claim would "
        "be an unrun proof"
    )
