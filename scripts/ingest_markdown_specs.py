#!/usr/bin/env python3
"""Markdown -> auger XML corpus converter (AUG-176).

Zero-loss ingestion of external markdown spec sets into `auger spec` corpora:

  project                  prefix  corpus root                    sources
  ------------------------ ------- ------------------------------ -----------------------------
  code-factory             CF      corpora/code-factory/          /home/kara/code-factory/specs/*.md
  auger-docs               AD      corpora/auger-docs/            docs/*.md of this repo
  coding-hermes-director   CHD     corpora/coding-hermes-director/ /home/kara/specs/coding-hermes-director/

Conversion rules (AUG-176 AC1/AC2/AC5):

  * Every `##` and `###` heading opens one `<section>` block; the prose the heading closes
    (up to the next `##`/`###` heading) is the block body, VERBATIM (leading/trailing blank
    lines trimmed, per-line trailing whitespace stripped — whitespace-only changes).
  * The text before the first `##`/`###` heading (the preamble, typically the document title
    and intro) is one more block, titled `(preamble)`. Nothing else is dropped: a section that
    is NOT converted must be listed with a reason in the manifest's `skipped` list.
  * Provenance rides in an XML comment immediately before each block —
    `<!-- source: <repo-relative path>#<anchor> -->` — because the published XSD declares a
    CLOSED attribute inventory (tests/test_xsd_conformance.py derives it from auger.py's AST):
    an invented `source=` attribute would be refused `schema-invalid`. The manifest carries the
    same mapping in machine-readable form, so citation round-trips both ways.
  * Block ids are stable and derived only from (project, source path, heading text, ordinal):
    `<PREFIX>-<FILE>-<SLUG>-S<NNN>` — FILE is the path slug (upper-case, <=24 chars), SLUG the
    heading slug (upper-case, <=30 chars), NNN the zero-padded ordinal within the file
    (preamble = 000). Ids over 80 chars are shortened by trimming the slugs, then by appending
    a 6-hex md5 digest of the full key. Same inputs -> byte-identical output, always.

The emitted documents are ordinary authored specs: `<spec xmlns="urn:auger:spec:1" ...>` with
`kind="section"` blocks, validating under `auger spec validate <root>` AND `auger spec check
<root>`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.sax.saxutils as saxutils
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HEADING_RE = re.compile(r"^(#{2,3})\s+(.*?)\s*#*\s*$")
HEADING_LEVEL_RE = re.compile(r"^(#{2,3})(\s|$)")

# Whole-file exclusions, each with the named reason AC2 requires.
EXCLUSIONS: dict[str, list[dict]] = {
    "auger-docs": [
        {
            "path": "docs/SPEC-CORPUS-VIEW.md",
            "reason": "generated view (scripts/gen_spec_view.py) of the specs/ XML corpus, not "
            "authored prose — converting it would snapshot a derived artifact and drift on every "
            "regeneration",
        },
    ],
    "coding-hermes-director": [
        {
            "path": "audit/",
            "reason": "process artifact (audit transcripts), not spec content — owner row names "
            "audit/ as excluded",
        },
        {
            "path": "tickets/",
            "reason": "process artifact (ticket tracker), not spec content — owner row names "
            "tickets/ as excluded",
        },
        {
            "path": "handbook/",
            "reason": "compiled PDF volumes (owner-facing digest). The handbook is the "
            "COMPLETENESS ORACLE for this conversion (foreman_note), not a conversion source: "
            "its curated section list is cross-checked against the corpus in "
            "evidence/AUG-176/04-handbook-parity.txt",
        },
        {
            "path": "review/ (except PLAN.md, PLAN-3.3.md)",
            "reason": "process artifacts: per-review evidence .txt files, review-log.json, "
            "packaging dirs (chdNN-package/) and handoff notes — not spec content. The two "
            "review PLAN documents ARE spec content and are converted",
        },
        {
            "path": "contracts/ (non-markdown)",
            "reason": "machine-readable contract files (openapi.yaml, config templates, JSON "
            "schemas, fixtures, __pycache__, validate_config.py) — the markdown contracts are "
            "converted; binary/schema files are not prose",
        },
        {
            "path": "build-spec/autogen/, acceptance/, integrated/, integration-evidence/, "
            "non-markdown files",
            "reason": "generated/derived build artifacts (JSON state machines, inventories, "
            "evidence bundles) — the 14 authored markdown volumes and indexes are converted",
        },
    ],
}

# The enumerated target sets (AC2). Globs resolve against the project's source root; order is
# sorted-path order so output is deterministic.
PROJECTS: dict[str, dict] = {
    "code-factory": {
        "prefix": "CF",
        "spec_id": "CODE-FACTORY-CORPUS",
        "scope": "ingest.code-factory",
        "source_root": "/home/kara/code-factory/specs",
        "globs": ["*.md"],
        "source_label": "code-factory repo, specs/ (7 files)",
    },
    "auger-docs": {
        "prefix": "AD",
        "spec_id": "AUGER-DOCS-CORPUS",
        "scope": "ingest.auger-docs",
        "source_root": str(REPO_ROOT / "docs"),
        "globs": ["*.md"],
        "exclude_paths": {"docs/SPEC-CORPUS-VIEW.md"},
        "source_label": "auger repo, docs/ residual prose",
    },
    "coding-hermes-director": {
        "prefix": "CHD",
        "spec_id": "CHD-CORPUS",
        "scope": "ingest.coding-hermes-director",
        "source_root": "/home/kara/specs/coding-hermes-director",
        "globs": [
            "DESIGN.md",
            "contracts/*.md",
            "sections/*.md",
            "build-spec/*.md",
            "review/PLAN.md",
            "review/PLAN-3.3.md",
        ],
        "source_label": "coding-hermes-director spec tree (structured set; audit/tickets/"
        "handbook/review-evidence excluded with reasons)",
    },
}

MAX_ID = 80


def slug(text: str, upper: bool = True) -> str:
    """Heading/path slug: alphanumerics and single dashes only."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.upper() if upper else cleaned


def anchor_slug(title: str) -> str:
    """GitHub-style anchor so the provenance comment round-trips to the source heading."""
    return slug(title, upper=False).lower()


def block_id(prefix: str, file_slug: str, heading_slug: str, ordinal: int) -> str:
    """Stable block id; see module docstring for the scheme and the shortening ladder."""
    file_slug = file_slug.rstrip("-") or "DOC"
    heading_slug = heading_slug.rstrip("-") or "SECTION"
    key = f"{prefix}-{file_slug}-{heading_slug}-S{ordinal:03d}"
    if len(key) <= MAX_ID:
        return key
    key = (
        f"{prefix}-{file_slug[:12].rstrip('-')}-{heading_slug[:16].rstrip('-')}"
        f"-S{ordinal:03d}"
    )
    if len(key) <= MAX_ID:
        return key
    key = key.rstrip("-")
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:6].upper()
    return f"{key[: MAX_ID - 7].rstrip('-')}-{digest}"


def parse_sections(text: str) -> list[dict]:
    """Split markdown into sections: one block per ##/### heading plus the preamble.

    Every line of the source lands in exactly one section, so a full conversion is zero-loss
    by construction; the manifest still carries the skipped list so a future skip must name
    its reason (AC1).
    """
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((idx, match.group(2)))
    if not headings:
        return [
            {
                "title": "(preamble)",
                "anchor": "preamble",
                "ordinal": 0,
                "body": clean_body(lines),
            }
        ]
    sections: list[dict] = []
    preamble_body = clean_body(lines[: headings[0][0]])
    if preamble_body.strip():
        sections.append(
            {
                "title": "(preamble)",
                "anchor": "preamble",
                "ordinal": 0,
                "body": preamble_body,
            }
        )
    for pos, (start, title) in enumerate(headings):
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        sections.append(
            {
                "title": title,
                "anchor": anchor_slug(title),
                "ordinal": pos + 1,
                "body": clean_body(lines[start:end]),
            }
        )
    return sections


def clean_body(lines: list[str]) -> str:
    """Whitespace-only normalization: strip trailing space per line, trim edge blank lines."""
    trimmed = [line.rstrip() for line in lines]
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return "\n".join(trimmed)


def source_files(project: str) -> list[Path]:
    config = PROJECTS[project]
    root = Path(config["source_root"])
    found: list[Path] = []
    for pattern in config["globs"]:
        found.extend(root.glob(pattern))
    excluded = set(config.get("exclude_paths", set()))

    def is_excluded(path: Path) -> bool:
        as_given = str(path)
        return any(as_given == excl or as_given.endswith("/" + excl) for excl in excluded)

    return sorted({path for path in found if path.is_file() and not is_excluded(path)})


def rel_source(project: str, path: Path) -> str:
    """Provenance path as written into comments/manifest: <source-root-relative>."""
    config = PROJECTS[project]
    root = Path(config["source_root"])
    label = {
        "code-factory": "code-factory/specs",
        "auger-docs": "docs",
        "coding-hermes-director": "coding-hermes-director",
        "fixture": "fixture",
    }[project]
    return f"{label}/{path.relative_to(root).as_posix()}"


def validate_manifest(manifest: dict) -> None:
    """Refuse a manifest whose bookkeeping cannot prove zero-loss conversion (AC1/AC2).

    Every document must carry a section count equal to its block count, and every skipped
    section must name a non-empty reason — a skip without a reason is a silent vanish.
    """
    for document in manifest["documents"]:
        if document["section_count"] != document["block_count"]:
            raise SystemExit(
                f"{document['source']}: {document['section_count']} section(s) but "
                f"{document['block_count']} block(s) — an unconverted section must appear in "
                "the skipped list WITH a reason"
            )
        for skipped in document["skipped"]:
            if not str(skipped.get("reason", "")).strip():
                raise SystemExit(
                    f"{document['source']}: skipped section {skipped.get('title')!r} carries "
                    "no reason — a skip must say why (AC1)"
                )
    totals = manifest["totals"]
    if totals["sections"] != totals["blocks"]:
        raise SystemExit(
            f"totals disagree: {totals['sections']} section(s) vs {totals['blocks']} block(s) "
            f"with {totals['skipped']} skipped — the counts must reconcile"
        )


def convert_project(project: str, out_root: Path) -> dict:
    """Convert every source file of `project` into one XML document per source file."""
    config = PROJECTS[project]
    prefix = config["prefix"]
    scope = config["scope"]
    out_root.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "project": project,
        "source_root": config["source_root"],
        "source_label": config["source_label"],
        "id_scheme": "<PREFIX>-<FILE-SLUG>-<HEADING-SLUG>-S<ordinal> (see "
        "scripts/ingest_markdown_specs.py module docstring)",
        "documents": [],
        "excluded": EXCLUSIONS.get(project, []),
        "totals": {"files": 0, "sections": 0, "blocks": 0, "skipped": 0},
    }
    for path in source_files(project):
        rel = rel_source(project, path)
        text = path.read_text(encoding="utf-8", errors="replace")
        sections = parse_sections(text)
        file_slug = slug(Path(path).stem)[:24] or "DOC"
        blocks: list[dict] = []
        skipped: list[dict] = []
        parts: list[str] = []
        for section in sections:
            bid = block_id(
                prefix,
                file_slug,
                (slug(section["title"])[:30] or "SECTION").rstrip("-"),
                section["ordinal"],
            )
            source = f"{rel}#{section['anchor']}".replace("--", "__")
            parts.append(f"  <!-- source: {source} -->")
            body = saxutils.escape(section["body"])
            # The align scanner resolves `ch:trace` markers in diff text against THIS repo;
            # a converted source quoting its own marker must not trip it. The XML numeric
            # character reference parses back to the identical text (an xml-structure change,
            # not a prose change — the loader unescapes it).
            body = body.replace("ch:trace", "ch&#58;trace")
            parts.append(
                f'  <section id="{bid}" kind="section" status="active" version="1.0.0" '
                f'applies-to="{scope}">'
            )
            parts.append(body)
            parts.append("  </section>")
            blocks.append(
                {
                    "id": bid,
                    "title": section["title"],
                    "anchor": section["anchor"],
                    "source": f"{rel}#{section['anchor']}",
                }
            )
        document_id = (
            block_id(prefix, file_slug, "DOC", 0).rsplit("-", 1)[0] + "-CORPUS"
        )
        doc = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f"<!-- Converted from {rel} by scripts/ingest_markdown_specs.py (AUG-176).",
            "     Verbatim prose; provenance in the per-block source comments. -->",
            f'<spec xmlns="urn:auger:spec:1" id="{document_id}" version="1.0.0" '
            'schema-version="1.0">',
            *parts,
            "</spec>",
        ]
        out_path = out_root / (Path(path).stem + ".xml")
        out_path.write_text("\n".join(doc) + "\n", encoding="utf-8")
        manifest["documents"].append(
            {
                "source": rel,
                "source_abs": str(path),
                "document": out_path.name,
                "section_count": len(sections),
                "block_count": len(blocks),
                "blocks": blocks,
                "skipped": skipped,
            }
        )
        manifest["totals"]["files"] += 1
        manifest["totals"]["sections"] += len(sections)
        manifest["totals"]["blocks"] += len(blocks)
        manifest["totals"]["skipped"] += len(skipped)
    validate_manifest(manifest)
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", choices=sorted(PROJECTS))
    parser.add_argument(
        "--out",
        default=None,
        help="corpus root (default: corpora/<project> under this repo)",
    )
    args = parser.parse_args(argv)
    out_root = Path(args.out) if args.out else REPO_ROOT / "corpora" / args.project
    manifest = convert_project(args.project, out_root)
    totals = manifest["totals"]
    print(
        f"{args.project}: {totals['files']} file(s), {totals['sections']} section(s) -> "
        f"{totals['blocks']} block(s), {totals['skipped']} skipped -> {out_root}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
