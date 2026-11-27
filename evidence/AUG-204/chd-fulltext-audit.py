#!/usr/bin/env python3
"""Read-only whole-document audit of a freshly generated CHD corpus.

Run: python3 evidence/AUG-204/chd-fulltext-audit.py CORPUS SOURCE AUTHORED
Generate CORPUS into a scratch directory with scripts/ingest_markdown_specs.py.
"""

import collections
import hashlib
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

base, src, authored = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((base / "manifest.json").read_text())
coverage = json.loads((base / "coverage-manifest.json").read_text())


def normalize(text):
    return " ".join(text.split())


errors = []
blocks = 0
for doc in manifest["documents"]:
    root = ET.parse(base / doc["document"]).getroot()
    sections = [node for node in root if node.tag.endswith("}section")]
    original = pathlib.Path(doc["source_abs"]).read_text(
        encoding="utf-8", errors="replace"
    )
    recovered = "\n".join(node.text or "" for node in sections)
    if normalize(original) != normalize(recovered):
        errors.append(doc["source"])
    blocks += len(sections)
classes = collections.Counter(entry["class"] for entry in coverage["entries"])
paths = {entry["path"] for entry in coverage["entries"]}
actual = {path.relative_to(src).as_posix() for path in src.rglob("*.md")}
bindings = collections.Counter()
for path in authored.rglob("*.xml"):
    for node in ET.parse(path).getroot().iter():
        if node.tag.endswith("}verification"):
            bindings["total"] += 1
            for key in ("runner", "path", "selector"):
                if node.get(key):
                    bindings[key] += 1
print(
    "classes",
    dict(classes),
    "source_paths",
    len(actual),
    "manifest_paths",
    len(paths),
    "missing",
    len(actual - paths),
    "extra",
    len(paths - actual),
)
print(
    "xml_documents",
    len(manifest["documents"]),
    "blocks",
    blocks,
    "whole_document_normalized_mismatches",
    len(errors),
    "examples",
    errors[:5],
)
print(
    "authored_verification_attributes",
    {key: bindings[key] for key in ("total", "runner", "path", "selector")},
)
for name in ("manifest.json", "coverage-manifest.json"):
    print(name, "sha256", hashlib.sha256((base / name).read_bytes()).hexdigest())
if errors or actual != paths:
    raise SystemExit(1)
