#!/usr/bin/env python3
"""Render the authored spec corpus (`specs/*.xml`) as a committed Markdown view (AUG-136).

The corpus is the SOURCE; `docs/SPEC-CORPUS-VIEW.md` is a generated artifact and is the only
Markdown form that is allowed to describe it. `tests/test_spec_corpus_view.py` regenerates the view
and asserts BYTE equality with the committed file, so the view cannot drift from the XML — the same
contract `scripts/gen_openapi.py` + `tests/test_openapi_parity.py` already enforce for the served
API.

Parsing is auger's, not this script's: the corpus is loaded through `auger.load_spec_corpus`, which
is the loader every `auger spec` verb uses (root/namespace/schema-version checks, block grammar,
typed edges, XSD conformance). Re-implementing it here would create a second reader that could
disagree with the CLI about what the corpus says.

Usage:
    python3 scripts/gen_spec_view.py            # rewrite docs/SPEC-CORPUS-VIEW.md
    python3 scripts/gen_spec_view.py --check    # exit 1 when the committed view has drifted
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run from a checkout without installing auger
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

SPECS_ROOT = REPO_ROOT / "specs"
VIEW_PATH = REPO_ROOT / "docs" / "SPEC-CORPUS-VIEW.md"
REGENERATE = "python3 scripts/gen_spec_view.py"

HEADER = """<!--
  GENERATED FILE — do not edit by hand. `specs/*.xml` is the source; this view is rendered from it
  by `scripts/gen_spec_view.py` and pinned byte for byte by `tests/test_spec_corpus_view.py`.
  Change the XML, then regenerate: `python3 scripts/gen_spec_view.py`
-->

# Authored spec corpus — generated view

`specs/` is the authored source (AUG-136). This view is rendered from it by
`scripts/gen_spec_view.py` through auger's own corpus loader (`auger.load_spec_corpus` — the same
loader `auger spec validate|check|list|assemble|bundle` use), so what follows is what the CLI reads,
including the prose of every block and every typed edge. The narrative about the corpus lives in
[SPEC-CORPUS.md](SPEC-CORPUS.md); this file holds no hand-written claim.
"""


def build_view(specs_root: Path = SPECS_ROOT) -> str:
    """Render `specs_root` as the Markdown view. Deterministic: same corpus, same bytes.

    Every path in the view is relative to `specs_root`, and the render names nothing about WHERE
    the corpus lives (no directory basename, no checkout-relative path). AUG-137 P2 compares the
    committed view against a render of whatever root it is given (`--specs-root`), so a
    byte-identical copy in a scratch directory must render byte-identically — a root-name line
    would make P2 FAIL on unmutated input and make the reproducibility claim false.
    """
    _root, documents, blocks = auger.load_spec_corpus(specs_root)
    references = sum(
        len(targets) for block in blocks for targets in block["refs"].values()
    )
    by_document: dict = {rel: [] for rel in sorted(documents)}
    for block in blocks:
        by_document.setdefault(block["source_file"], []).append(block)

    lines = [HEADER.rstrip("\n"), "", "## Measured size", ""]
    lines.append(f"- documents: {len(documents)}")
    lines.append(f"- blocks: {len(blocks)}")
    lines.append(f"- references: {references}")
    lines.append(f"- schema version: {auger.SPEC_SCHEMA_VERSION}")
    lines.append("- paths: relative to the corpus root")

    for rel in sorted(by_document):
        document = documents.get(rel)
        if (
            document is None
        ):  # a fragment's blocks are assembled at their <include> site
            continue
        lines.extend(["", f"## `{rel}`", ""])
        lines.append(
            f"document id: `{document.get('id')}` · version {document.get('version')}"
        )
        for block in sorted(by_document[rel], key=lambda item: item["order"]):
            lines.extend(
                [
                    "",
                    f"### {block['id']} · {block['kind']} · {block['status']} · v{block['version']}",
                    "",
                    f"- source: `{block['source']}`",
                ]
            )
            if block["applies_to"]:
                lines.append(f"- applies-to: `{' '.join(block['applies_to'])}`")
            if block["method"]:
                label = (
                    "verification"
                    if block["kind"] in auger.SPEC_REQUIRES_METHOD
                    else "method"
                )
                lines.append(f"- {label}: `{block['method']}`")
            if block.get("rollback"):
                # AUG-143: a consumer reads the declaration here instead of guessing whether the
                # change can be undone. Rendered from the loader's own block record, so the view
                # cannot claim a declaration the validator did not read.
                lines.append(f"- rollback: `{block['rollback']}`")
            for attribute in auger.SPEC_EDGE_ATTRS:
                targets = block["refs"].get(attribute)
                if targets:
                    lines.append(
                        f"- {attribute}: "
                        + " ".join(f"`{target}`" for target in targets)
                    )
            if block["text"]:
                lines.extend(["", block["text"]])

    lines.extend(["", "## Reference index", ""])
    if references:
        lines.extend(["| from | edge | to |", "| --- | --- | --- |"])
        for block in blocks:
            for attribute in auger.SPEC_EDGE_ATTRS:
                for target in block["refs"].get(attribute, ()):
                    lines.append(f"| `{block['id']}` | `{attribute}` | `{target}` |")
    else:
        lines.append("No typed edges are declared by this corpus.")
    return "\n".join(lines) + "\n"


def _display(path: Path) -> str:
    """The path relative to the checkout when it is inside it, else the absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="render specs/ as docs/SPEC-CORPUS-VIEW.md"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"compare with the committed view instead of writing it ({REGENERATE})",
    )
    args = parser.parse_args(argv)

    try:
        text = build_view(SPECS_ROOT)
    except auger.SpecError as exc:
        print(f"spec error ({exc.code}): {exc.detail}", file=sys.stderr)
        return 2

    if args.check:
        committed = VIEW_PATH.read_text(encoding="utf-8") if VIEW_PATH.is_file() else ""
        if committed != text:
            print(
                f"{_display(VIEW_PATH)} has drifted from the corpus — "
                f"regenerate: {REGENERATE}",
                file=sys.stderr,
            )
            return 1
        print(f"{_display(VIEW_PATH)} matches the corpus ({len(text)} bytes)")
        return 0

    VIEW_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {_display(VIEW_PATH)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
