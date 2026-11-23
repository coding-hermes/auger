#!/usr/bin/env python3
"""Guard: docs/SPEC-CORPUS.md's example fence stays byte-identical to docs/spec-example.

The spec-to-build walkthrough (AUG-206) claims the fenced XML block IS the shipped corpus file
minus nothing — declaration and comment included. Drift on either side breaks that claim, and a
docs reader who copies a stale example learns the wrong corpus. Run it before committing a change
to either file; green when the fence equals the file byte for byte.
"""

from __future__ import annotations

import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "SPEC-CORPUS.md"
CORPUS = ROOT / "docs" / "spec-example" / "tracker-loop.xml"


def main() -> int:
    doc = DOC.read_text()
    if "```xml" not in doc:
        print(f"FAIL [example]: {DOC.name} lost its ```xml example fence")
        return 1
    start = doc.index("```xml") + len("```xml\n")
    end = doc.index("\n```", start)
    fenced = doc[start:end].strip("\n")
    shipped = CORPUS.read_text().strip("\n")
    if fenced != shipped:
        diff = "".join(
            difflib.unified_diff(
                shipped.splitlines(keepends=True),
                fenced.splitlines(keepends=True),
                str(CORPUS.relative_to(ROOT)),
                f"{DOC.name} fence",
            )
        )
        print(
            f"FAIL [example]: the fence in {DOC.name} drifted from {CORPUS.name}:\n{diff}"
        )
        return 1
    print(
        f"PASS: SPEC-CORPUS.md example fence == docs/spec-example/tracker-loop.xml "
        f"({len(shipped)} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
