#!/usr/bin/env python3
"""AUG-176 stage 3: handbook parity table (the owner-facing completeness oracle).

Reads the newest compiled handbook volume (CHD-3.2-complete-print.pdf) via pdftotext,
pulls its curated section set from the MASTER INDEX contents table (the volume declares
16 function sections), and maps every curated section onto the converted
coding-hermes-director corpus: which corpus documents cover the domain and how many
blocks they contributed. A section with no corpus mapping must name WHY — the same
reasoned-skip contract as the markdown parity (AC1 / the row's foreman_note).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF = Path(
    "/home/kara/specs/coding-hermes-director/handbook/CHD-3.2-complete-print.pdf"
)

# handbook curated domain -> the corpus documents whose prose carries it. Matched on the
# handbook's own SHORT titles (word overlap, >=3 letters), so a rewording that keeps the
# domain vocabulary still maps.
DOMAIN_MAP: list[tuple[str, list[str]]] = [
    ("PURPOSE AND SCOPE", ["DESIGN.md"]),
    ("MASTER WORKFLOWS", ["DESIGN.md", "build-spec/INDEX.md"]),
    ("ROLES", ["build-spec/VOL-A-story-roles-lifecycle.md"]),
    ("STORY AND ROLES", ["build-spec/VOL-A-story-roles-lifecycle.md"]),
    ("RECORDS AND STORAGE", ["build-spec/VOL-B-records-storage-recovery.md"]),
    ("TOOLS", ["build-spec/VOL-C-tools-messaging-admission-authz.md"]),
    ("MESSAGING", ["build-spec/VOL-C-tools-messaging-admission-authz.md"]),
    ("CONFIGURATION", ["build-spec/VOL-D-config-acceptance-ops.md", "DESIGN.md"]),
    ("IDENTITY", ["build-spec/VOL-D-config-acceptance-ops.md"]),
    ("PROVISIONING", ["build-spec/VOL-D-config-acceptance-ops.md", "DESIGN.md"]),
    ("ROOMS AND SESSIONS", ["build-spec/VOL-A-story-roles-lifecycle.md"]),
    ("TICKETS", ["build-spec/VOL-E-tickets-workstate.md"]),
    ("PERSISTENCE AND QUERIES", ["build-spec/VOL-B-records-storage-recovery.md"]),
    ("TOOLS AND ERRORS", ["build-spec/VOL-C-tools-messaging-admission-authz.md"]),
    ("AUTHORIZATION", ["build-spec/VOL-C-tools-messaging-admission-authz.md"]),
    (
        "ADMISSION DISPATCH AND RUN CONTROL",
        [
            "build-spec/VOL-C-tools-messaging-admission-authz.md",
            "build-spec/VOL-E-operation-reference.md",
        ],
    ),
    ("OPERATIONS", ["build-spec/VOL-E-operation-reference.md"]),
    ("OPERATION REFERENCE", ["build-spec/VOL-E-operation-reference.md"]),
    ("REPORTS AND ACCEPTANCE", ["build-spec/VOL-E-operation-reference.md"]),
    ("ACCEPTANCE SUITE", ["build-spec/VOL-D-config-acceptance-ops.md"]),
    ("SESSION ARCHIVES AND RESTORE", ["build-spec/VOL-B-records-storage-recovery.md"]),
    ("ARCHIVES AND RESTORE", ["build-spec/VOL-B-records-storage-recovery.md"]),
    ("DEPLOYMENT", ["build-spec/VOL-D-config-acceptance-ops.md"]),
    ("FAILURE AND RECOVERY", ["build-spec/VOL-B-records-storage-recovery.md"]),
    ("MEASUREMENT", ["build-spec/VOL-E-operation-reference.md"]),
    ("TICKETS AND WORKSTATE", ["build-spec/VOL-E-tickets-workstate.md"]),
    (
        "REFERENCE MAP",
        ["build-spec/VOL-F-reference-map.md", "build-spec/REFERENCE-INDEX.md"],
    ),
    ("INTEGRATION", ["build-spec/INTEGRATION.md"]),
    ("DATA MODEL", ["contracts/DATA-MODEL.md"]),
    ("ENDPOINTS", ["contracts/ENDPOINTS.md"]),
    ("VERDICT", ["build-spec/VERDICT.md"]),
    ("NORMATIVE MANIFEST", ["build-spec/NORMATIVE-MANIFEST.md"]),
]


def extract_handbook_sections(pdf: Path) -> list[tuple[str, str]]:
    """(number, short title) for the curated sections, parsed from the MASTER INDEX contents
    table: pdftotext -layout keeps the columns; marker lines carry the short title's first
    fragment, and a wrapped continuation line carries the section-title wrap AND the
    short-title wrap side by side."""
    raw = subprocess.run(
        ["pdftotext", "-layout", "-f", "1", "-l", "8", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    short_fragments: dict[str, list[str]] = []
    short_by_sec: dict[str, list[str]] = {}
    current_sec: str | None = None
    for line in raw.splitlines():
        if re.search(r"SECTION \d+ / SHEET", line):
            fields = [f.strip() for f in re.split(r"\s{2,}", line.strip()) if f.strip()]
            sheet_idx = next(
                (
                    i
                    for i, f in enumerate(fields)
                    if re.match(r"^SECTION \d+ / SHEET", f)
                ),
                None,
            )
            sec_match = re.match(r"^(\d+)\b", fields[0]) if fields else None
            if sheet_idx is None or sheet_idx < 2 or not sec_match:
                continue
            current_sec = sec_match.group(1)
            fragment = re.sub(r"\s+\d+[A-Z]*$", "", fields[sheet_idx - 1].upper()).strip()
            if current_sec not in short_by_sec:
                short_by_sec[current_sec] = []
                short_fragments.append(current_sec)
            if fragment not in short_by_sec[current_sec]:
                short_by_sec[current_sec].append(fragment)
            continue
        stripped = line.rstrip()
        if not stripped.strip() or current_sec is None:
            continue
        indent = len(stripped) - len(stripped.lstrip())
        fields = [f.strip() for f in re.split(r"\s{2,}", stripped.strip()) if f.strip()]
        # A wrap under the sheet/leaf column (>= 55 columns in) or a two-field wrap whose
        # second field sits in the short-title column continues the SHORT title.
        wrap = (
            fields[1]
            if len(fields) >= 2 and indent >= 30
            else (fields[0] if indent >= 55 and len(fields) == 1 else None)
        )
        if wrap and re.search(r"[A-Za-z]", wrap):
            fragment = re.sub(r"\s+\d+[A-Z]*$", "", wrap.upper()).strip()
            if (
                fragment
                and re.search(r"[A-Z]{3}", fragment)
                and fragment not in short_by_sec[current_sec]
            ):
                short_by_sec[current_sec].append(fragment)
    # final cleanup: drop folio/figure/index glue tokens the layout column bleeds in
    cleaned: list[tuple[str, str]] = []
    for sec in short_fragments:
        words = [
            w
            for w in short_by_sec[sec]
            if re.search(r"[A-Z]{3}", w) and w not in {"FIG", "IND"}
        ]
        cleaned.append((sec, " ".join(words)))
    return cleaned


def main() -> int:
    if not PDF.exists():
        print(f"handbook PDF missing: {PDF}", file=sys.stderr)
        return 2
    sections = extract_handbook_sections(PDF)
    manifest = json.loads(
        (REPO_ROOT / "corpora" / "coding-hermes-director" / "manifest.json").read_text()
    )
    by_source = {d["source"]: d for d in manifest["documents"]}

    rows: list[str] = []
    gaps: list[str] = []
    for number, title in sections:
        words = set(re.findall(r"[A-Z]{3,}", title))
        docs: list[str] = []
        for keyword, sources in DOMAIN_MAP:
            if words & set(re.findall(r"[A-Z]{3,}", keyword)):
                docs.extend(sources)
        docs = sorted(
            {d for d in docs if any(d == s or s.endswith("/" + d) for s in by_source)}
        )
        if docs:
            resolved = {
                s for d in docs for s in by_source if s == d or s.endswith("/" + d)
            }
            blocks = sum(by_source[s]["block_count"] for s in resolved)
            named = ", ".join(sorted(resolved))
            rows.append(
                f"  SECTION {number:>2}  {title:<38} -> {blocks:>4} blocks  ({named})"
            )
        else:
            gaps.append(
                f"  SECTION {number:>2}  {title}: NO corpus mapping — NAMED GAP "
                "(curated handbook domain with no converted prose counterpart)"
            )
    print(f"handbook volume: {PDF.name}")
    print(f"curated sections parsed from the MASTER INDEX: {len(sections)}")
    print(f"mapped to converted corpus blocks: {len(rows)}; named gaps: {len(gaps)}")
    print()
    print("\n".join(rows + gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
