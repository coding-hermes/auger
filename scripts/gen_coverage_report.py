#!/usr/bin/env python3
"""Regenerate docs/qa/aug176-conversion-coverage.md from the committed manifests.

Every count, source root, exclusion and id scheme line is READ BACK from
corpora/<project>/manifest.json — never hand-typed. Counts here must never be
edited by hand: change the converter, regenerate the corpora, then re-run this
script (AUG-179).

Usage: python3 scripts/gen_coverage_report.py [--check]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT = REPO_ROOT / "docs" / "qa" / "aug176-conversion-coverage.md"
PROJECTS = ("code-factory", "auger-docs", "coding-hermes-director")

HEADER = """\
# AUG-176 conversion coverage report

Generated from the committed manifests (corpora/*/manifest.json); counts are read
back from the manifests, never hand-typed. Owner directive: convert the
code-factory, coding-hermes-director and auger residual-prose spec files into the
auger XML corpus format with zero data loss, so the embedded text returns good
recall (AUG-176, AC1/AC2). Regenerate with scripts/gen_coverage_report.py.
"""


def render_project(corpora: Path, project: str) -> str:
    manifest = json.loads((corpora / project / "manifest.json").read_text("utf-8"))
    lines = [
        f"## corpora/{project}",
        "",
        f"Source root: {manifest['source_root']} — {manifest['source_label']}",
        "",
        f"Id scheme: {manifest['id_scheme']}",
        "",
        f"Block integrity: {manifest.get('digest_scheme', 'no digest scheme recorded (pre-AUG-179 manifest)')}",
        "",
        "| source file | sections | blocks | skipped |",
        "|---|---|---|---|",
    ]
    for document in manifest["documents"]:
        lines.append(
            f"| {document['source']} | {document['section_count']} | "
            f"{document['block_count']} | {len(document['skipped'])} |"
        )
    totals = manifest["totals"]
    lines.append(
        f"| TOTAL | {totals['sections']} | {totals['blocks']} | {totals['skipped']} |"
    )
    lines += ["", "Excluded source files (AC2 — each with a named reason):", ""]
    if manifest["excluded"]:
        lines += [f"- `{e['path']}` — {e['reason']}" for e in manifest["excluded"]]
    else:
        lines.append("- none: every markdown file under the source root converts")
    if totals["skipped"] == 0:
        lines += [
            "",
            "Skipped sections: none — every H2/H3 heading and the preamble converted "
            "to exactly one block (AC1).",
        ]
    else:
        lines += ["", "Skipped sections (each with a named reason, AC1):", ""]
        for document in manifest["documents"]:
            for skipped in document["skipped"]:
                lines.append(
                    f"- `{document['source']}` section `{skipped['title']}` — "
                    f"{skipped['reason']}"
                )
    return "\n".join(lines) + "\n"


def build_report() -> str:
    corpora = REPO_ROOT / "corpora"
    sections = [render_project(corpora, project) for project in PROJECTS]
    return HEADER + "\n" + "\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed report is not current with the manifests",
    )
    args = parser.parse_args(argv)
    rendered = build_report()
    if args.check:
        current = REPORT.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"STALE: {REPORT} does not match the committed manifests — "
                "re-run scripts/gen_coverage_report.py"
            )
            return 1
        print(f"OK: {REPORT} matches the committed manifests")
        return 0
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(rendered, encoding="utf-8")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
