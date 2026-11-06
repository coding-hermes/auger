"""AUG-139 [PRD AC-5]: every shipped view is REGENERATED from the XML source, and a source-only
mutation moves both of them.

PRD AC-5, verbatim:

    "Given a source block referenced by two generated documents, when that source block changes and
     views are regenerated, then both views reflect the change and neither contains an independently
     edited stale copy; proof: multi-view regeneration fixture plus source-only mutation test."

PRD R1, verbatim:

    "Formal spec content and typed relationships shall be authored in versioned XML; Markdown/HTML
     outputs are generated views, never independently authoritative copies."

So the two views are Markdown and HTML. At this base only ONE view existed
(`docs/SPEC-CORPUS-VIEW.md`, produced by `scripts/gen_spec_view.py`) and no CLI verb could render
anything; a renderer that emitted a cached or stale body would have passed every XML test. The verb
`auger spec render <root> --out <dir>` renders every view this product ships, through the ONE corpus
loader, and this file is the gate that keeps that true.

Arms:
  the verb ships every view ..... test_the_render_verb_writes_every_shipped_view
  byte-identical twice .......... test_two_consecutive_renders_are_byte_identical
  committed views in sync ....... test_the_committed_views_are_what_the_verb_emits
  source-only mutation .......... test_mutating_one_block_body_moves_both_views
  untouched blocks unchanged .... test_untouched_blocks_are_byte_identical_across_a_mutation
  read-only on the corpus ....... test_the_render_path_is_read_only_on_the_corpus
  refuses the corpus root ....... test_the_render_verb_refuses_to_write_into_the_corpus_root
  documented .................... test_the_render_verb_is_documented
  HTML is a view, not a dump .... test_the_html_view_carries_every_block_and_edge
  the arm itself ................ test_gate_arm_runs_this_file

The load-bearing arm lives outside this file by design: pinning the renderer to a stale body is a
THROWAWAY-copy mutation of production code, and the failing transcript is kept as evidence
(evidence for AUG-139 in the wave report) rather than a `--force-fail` switch in the shipped verb.

ch:trace row=AUG-139 spec=docs/prd/xml-spec-graph.md#acceptance-criteria
         test=tests/test_spec_render.py doc=docs/SPEC-CORPUS-VIEW.md
         evidence=docs/SPEC-CORPUS-VIEW.html
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import io
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if (
    str(REPO_ROOT) not in sys.path
):  # tests/conftest.py does this too; keep the file standalone
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SPECS = REPO_ROOT / "specs"
GATE = REPO_ROOT / "tests" / "gate.sh"
VIEWS = REPO_ROOT / "docs"
MARKDOWN_VIEW = VIEWS / "SPEC-CORPUS-VIEW.md"
HTML_VIEW = VIEWS / "SPEC-CORPUS-VIEW.html"
REGENERATE = "auger spec render specs --out docs"


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def tree_hash(root: Path) -> str:
    """A content hash of every file under `root`, so a read-only claim is measurable."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
    return digest.hexdigest()


def copy_corpus(tmp_path: Path, name: str = "specs") -> Path:
    corpus = tmp_path / name
    shutil.copytree(SPECS, corpus)
    return corpus


def render(corpus: Path) -> dict:
    """Every view the verb ships, keyed by file name (the product's own renderer)."""
    return auger.render_spec_views(corpus)


def block_section(view: str, fmt: str, block_id: str) -> str:
    """The exact bytes ONE block occupies in a rendered view.

    A view is a flat document, so "this block changed and that one did not" has to be measured on a
    slice, not on the whole file: a whole-file diff proves *something* changed, never *which* block.
    """
    if fmt == "markdown":
        marker = f"### {block_id} ·"
        start = view.find(marker)
        assert start >= 0, f"block {block_id} has no section in the Markdown view"
        following = [
            index
            for index in (view.find("\n### ", start + 1), view.find("\n## ", start + 1))
            if index >= 0
        ]
        end = min(following) if following else len(view)
        return view[start:end]
    marker = f'<section class="block" id="block-{block_id}">'
    start = view.find(marker)
    assert start >= 0, f"block {block_id} has no section in the HTML view"
    end = view.find("</section>", start)
    assert end >= 0, f"block {block_id}'s HTML section is unterminated"
    return view[start : end + len("</section>")]


MUTATED_BLOCK = "SPEC-OBL-RUNNER"
UNTOUCHED_BLOCK = "SPEC-GRAPH-CORPUS"
ORIGINAL_PROSE = "so XML never executes a command."
MUTATED_PROSE = "so no XML text is ever executed as a shell string."


def mutate_one_block_body(corpus: Path) -> None:
    """Edit ONE block's body (prose only) in the corpus that feeds both views."""
    target = corpus / "verification.xml"
    text = target.read_text(encoding="utf-8")
    assert ORIGINAL_PROSE in text, (
        f"the fixture sentence this mutation replaces is gone from {target.name}"
    )
    assert MUTATED_PROSE not in text, "the replacement must not already be present"
    target.write_text(text.replace(ORIGINAL_PROSE, MUTATED_PROSE), encoding="utf-8")


# ------------------------------------------------------- the verb ships every view
def test_the_render_verb_writes_every_shipped_view(tmp_path: Path) -> None:
    """One command, both views, and the command says what it wrote."""
    out = tmp_path / "views"
    out.mkdir()
    code, stdout, stderr = run_spec("render", str(SPECS), "--out", str(out))
    assert code == 0, stderr
    for name in ("SPEC-CORPUS-VIEW.md", "SPEC-CORPUS-VIEW.html"):
        written = out / name
        assert written.is_file(), f"{name} was not rendered: {stdout}"
        assert written.stat().st_size > 0, f"{name} is empty"
        assert name in stdout, f"the verb's report does not name {name}: {stdout}"
    assert "2 view(s)" in stdout, stdout


def test_two_consecutive_renders_are_byte_identical(tmp_path: Path) -> None:
    """AC-5's reproducibility half: same corpus, same bytes — sha256 equal on both views."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for target in (first, second):
        code, _stdout, stderr = run_spec("render", str(SPECS), "--out", str(target))
        assert code == 0, stderr
    for name in ("SPEC-CORPUS-VIEW.md", "SPEC-CORPUS-VIEW.html"):
        left = hashlib.sha256((first / name).read_bytes()).hexdigest()
        right = hashlib.sha256((second / name).read_bytes()).hexdigest()
        assert left == right, f"{name} is not reproducible: {left} != {right}"
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_the_committed_views_are_what_the_verb_emits() -> None:
    """The checked-in views are the verb's output for `specs/`, byte for byte (no hand edits)."""
    views = render(SPECS)
    for path in (MARKDOWN_VIEW, HTML_VIEW):
        assert path.is_file(), f"{path.name} is missing — regenerate: {REGENERATE}"
        committed = path.read_text(encoding="utf-8")
        assert committed == views[path.name], (
            f"{path.name} has drifted from specs/ — the XML is the source, so regenerate the "
            f"view: {REGENERATE}"
        )


# ------------------------------------------------------------- source-only mutation
def test_mutating_one_block_body_moves_both_views(tmp_path: Path) -> None:
    """AC-5: one source edit, BOTH views reflect it, and the pre-edit body is gone from both."""
    corpus = copy_corpus(tmp_path)
    before = render(corpus)
    mutate_one_block_body(corpus)
    after = render(corpus)

    for name, text in after.items():
        assert text != before[name], (
            f"{name} did not change when the corpus body changed"
        )
        assert MUTATED_PROSE in text, f"{name} does not carry the edited body"
        assert ORIGINAL_PROSE not in text, (
            f"{name} still carries the pre-edit body — a view is independently edited or cached"
        )
        fmt = "markdown" if name.endswith(".md") else "html"
        assert block_section(text, fmt, MUTATED_BLOCK) != block_section(
            before[name], fmt, MUTATED_BLOCK
        ), f"{name}'s section for {MUTATED_BLOCK} did not move with the source"


def test_untouched_blocks_are_byte_identical_across_a_mutation(tmp_path: Path) -> None:
    """The mutation is scoped: an untouched block's rendered section must not move at all."""
    corpus = copy_corpus(tmp_path)
    before = render(corpus)
    mutate_one_block_body(corpus)
    after = render(corpus)
    for name, text in after.items():
        fmt = "markdown" if name.endswith(".md") else "html"
        assert block_section(text, fmt, UNTOUCHED_BLOCK) == block_section(
            before[name], fmt, UNTOUCHED_BLOCK
        ), (
            f"{name} moved an untouched block ({UNTOUCHED_BLOCK}) when one other block changed"
        )


# --------------------------------------------------------------- read-only, and refusals
def test_the_render_path_is_read_only_on_the_corpus(tmp_path: Path) -> None:
    """Rendering reads the corpus and writes only into --out: the corpus tree hash is unchanged."""
    corpus = copy_corpus(tmp_path)
    before = tree_hash(corpus)
    out = tmp_path / "views"
    out.mkdir()
    code, _stdout, stderr = run_spec("render", str(corpus), "--out", str(out))
    assert code == 0, stderr
    assert tree_hash(corpus) == before, "rendering mutated the corpus it read"
    assert sorted(p.name for p in corpus.rglob("*.md")) == []


@pytest.mark.parametrize("target", ["root", "out-subdirectory"])
def test_the_render_verb_refuses_to_write_into_the_corpus_root(
    tmp_path: Path, target: str
) -> None:
    """A view written INTO the source tree would be a second, editable copy beside the XML."""
    corpus = copy_corpus(tmp_path)
    before = tree_hash(corpus)
    out = corpus if target == "root" else corpus / "views"
    if target != "root":
        out.mkdir()

    code, stdout, stderr = run_spec("render", str(corpus), "--out", str(out))
    assert code == 2, (stdout, stderr)
    assert "spec error (render-into-corpus)" in stderr, stderr
    assert stdout == "", "a refusal must not print a rendered document"
    assert tree_hash(corpus) == before, "the refusal still wrote into the corpus"
    assert not (corpus / "SPEC-CORPUS-VIEW.md").exists()
    assert not (corpus / "SPEC-CORPUS-VIEW.html").exists()


def test_the_render_verb_refuses_a_missing_out_directory(tmp_path: Path) -> None:
    """`--out` names an existing directory; a missing parent is a typed refusal, not a tree."""
    code, stdout, stderr = run_spec(
        "render", str(SPECS), "--out", str(tmp_path / "missing" / "views")
    )
    assert code == 2, (stdout, stderr)
    assert "spec error (missing-out-directory)" in stderr, stderr
    assert not (tmp_path / "missing").exists(), "the refusal created a directory tree"


# ------------------------------------------------------------------- documentation
def test_the_render_verb_is_documented() -> None:
    """A verb nobody documents is a verb nobody can find (docs/VERBS.md is the registry)."""
    verbs = (REPO_ROOT / "docs" / "VERBS.md").read_text(encoding="utf-8")
    assert "auger spec render" in verbs, (
        "docs/VERBS.md must document `auger spec render` beside the other spec verbs"
    )
    code, stdout, stderr = run_spec("render", "--help")
    assert code == 0, stderr
    assert "--out" in stdout, stdout


def test_the_html_view_carries_every_block_and_edge() -> None:
    """The second view is a VIEW: every block id, its prose and every typed edge are present."""
    _root, _documents, blocks = auger.load_spec_corpus(SPECS)
    html_view = HTML_VIEW.read_text(encoding="utf-8")
    for block in blocks:
        assert f'id="block-{block["id"]}"' in html_view, (
            f"{block['id']} missing from the HTML view"
        )
        if block["text"]:
            # The view is HTML, so the prose is escaped there: `<verification>` in a block body is
            # markup in the file, not text. Comparing the RAW body would demand an unescaped
            # document — i.e. ask the renderer to emit broken HTML.
            escaped = html.escape(block["text"])
            assert escaped in html_view, (
                f"{block['id']}'s prose missing from the HTML view"
            )
    edges = [
        (block["id"], attribute, target)
        for block in blocks
        for attribute in auger.SPEC_EDGE_ATTRS
        for target in block["refs"].get(attribute, ())
    ]
    assert edges, "the corpus declares no typed edges — this arm would prove nothing"
    for source, attribute, target in edges:
        assert (
            f"<code>{source}</code></td><td><code>{attribute}</code></td><td><code>{target}</code>"
            in html_view
        ), f"edge {source} --{attribute}--> {target} is missing from the HTML view"


def test_no_rendered_view_embeds_this_checkout() -> None:
    """Views are artifacts another host must reproduce: no absolute path, no timestamp."""
    for name, text in render(SPECS).items():
        assert str(REPO_ROOT) not in text, f"{name} embeds this checkout's path"
        for token in text.split():
            assert not token.startswith("/home/"), f"{name} embeds {token!r}"


# ------------------------------------------------------------------- the arm itself
def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_render.py" in arm[0], (
        "tests/test_spec_render.py is not in the gate's pytest arm — the AC-5 claim would be an "
        "unrun proof"
    )
