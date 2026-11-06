#!/usr/bin/env python3
"""Render the authored spec corpus (`specs/*.xml`) as the views the product ships (AUG-136/139).

The corpus is the SOURCE; `docs/SPEC-CORPUS-VIEW.md` and `docs/SPEC-CORPUS-VIEW.html` are generated
artifacts and are the only readable forms allowed to describe it (PRD R1: "Markdown/HTML outputs are
generated views, never independently authoritative copies"). `tests/test_spec_corpus_view.py` and
`tests/test_spec_render.py` regenerate them and assert BYTE equality with the committed files, so a
view cannot drift from the XML — the same contract `scripts/gen_openapi.py` +
`tests/test_openapi_parity.py` already enforce for the served API.

Parsing AND rendering are auger's, not this script's: the corpus is loaded through
`auger.load_spec_corpus` (the loader every `auger spec` verb uses) and rendered through
`auger.render_spec_view_document`. This file is only the checkout-shaped front end with a `--check`
mode; `auger spec render <root> --out <dir>` is the CLI form of the same functions, and the two
cannot disagree because neither owns a renderer of its own.

Usage:
    python3 scripts/gen_spec_view.py            # rewrite every generated view
    python3 scripts/gen_spec_view.py --check    # exit 1 when a committed view has drifted
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
#: The Markdown view is the primary one this script has always written; AUG-139 added the HTML view.
#: `view_path()` derives every other view from it, so a caller that points VIEW_PATH elsewhere
#: (tests do) moves the whole set with it.
VIEW_PATH = REPO_ROOT / "docs" / auger.SPEC_VIEW_FILENAMES["markdown"]
REGENERATE = "python3 scripts/gen_spec_view.py"


def view_path(fmt: str = "markdown") -> Path:
    """Where a view of `fmt` is committed. Derived from VIEW_PATH — never a second hand-kept path."""
    if fmt == "markdown":
        return VIEW_PATH
    return VIEW_PATH.with_name(auger.SPEC_VIEW_FILENAMES[fmt])


def build_view(specs_root: Path = SPECS_ROOT, fmt: str = "markdown") -> str:
    """Render `specs_root` as one view. Deterministic: same corpus, same bytes.

    Every path in a view is relative to `specs_root`, and the render names nothing about WHERE the
    corpus lives (no directory basename, no checkout-relative path). AUG-137 P2 compares the
    committed views against a render of whatever root it is given (`--specs-root`), so a
    byte-identical copy in a scratch directory must render byte-identically — a root-name line would
    make P2 FAIL on unmutated input and make the reproducibility claim false.
    """
    _root, documents, blocks = auger.load_spec_corpus(specs_root)
    return auger.render_spec_view_document(documents, blocks, fmt=fmt)


def _display(path: Path) -> str:
    """The path relative to the checkout when it is inside it, else the absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="render specs/ as the committed spec views"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"compare with the committed views instead of writing them ({REGENERATE})",
    )
    args = parser.parse_args(argv)

    try:
        views = {fmt: build_view(SPECS_ROOT, fmt) for fmt in auger.SPEC_VIEW_FORMATS}
    except auger.SpecError as exc:
        print(f"spec error ({exc.code}): {exc.detail}", file=sys.stderr)
        return 2

    if args.check:
        drift = False
        for fmt in auger.SPEC_VIEW_FORMATS:
            path = view_path(fmt)
            committed = path.read_text(encoding="utf-8") if path.is_file() else ""
            if committed != views[fmt]:
                print(
                    f"{_display(path)} has drifted from the corpus — "
                    f"regenerate: {REGENERATE}",
                    file=sys.stderr,
                )
                drift = True
            else:
                print(
                    f"{_display(path)} matches the corpus "
                    f"({len(views[fmt].encode('utf-8'))} bytes)"
                )
        return 1 if drift else 0

    for fmt in auger.SPEC_VIEW_FORMATS:
        path = view_path(fmt)
        path.write_text(views[fmt], encoding="utf-8")
        print(f"wrote {_display(path)} ({len(views[fmt].encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
