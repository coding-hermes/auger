# CHD corpus coverage (AUG-204)

Status: full canonical coverage. Every one of the 648 canonical `.md` paths of
`/home/kara/specs/coding-hermes-director` is accounted in
`coverage-manifest.json` (this directory) in exactly one class; `manifest.json`
is the AUG-176 conversion manifest and now covers every rendered document.

Measured at conversion time (regenerate with
`python3 scripts/ingest_markdown_specs.py coding-hermes-director`):

| class | paths | what it is |
|---|---:|---|
| first-class | 111 | build-spec (98), contracts (5), review (6), sections (1), DESIGN.md (1) — one XML render each |
| handbook-unique | 411 | handbook files whose normalized content matches no canonical render — rendered ONCE |
| duplicate-of-canonical | 15 | handbook files byte-equal (whitespace-normalized) to a canonical first-class source — provenance pointers, no render |
| duplicate-of-handbook | 64 | handbook files equal to another handbook render — pointer to that render, no own render |
| audit-provenance | 47 | audit transcripts — provenance/attachment record, no render |
| **total** | **648** | zero unaccounted paths |

Renders: 522 XML documents, 7314 blocks (522 documents = 111 first-class +
411 handbook-unique; the AUG-176 distilled 22 are pinned byte-stable with
their stem-slug ids, so every AUG-176 block id and battery target is
unchanged). `auger spec validate` and `auger spec check` pass on the whole
corpus (rc 0).

## Dedupe (AC2), measured — and the estimate it overrode

Method: sha256 of the whitespace-normalized whole-file text
(`' '.join(text.split())`), every handbook file compared against the 111
canonical first-class sources, then within handbook (first sorted path owns
the render).

The row estimated a unique residue of ≤ 50 handbook files. Measured: **411
unique renders** — the "handbook is mostly rendered duplicates of build-spec"
premise does NOT hold at content-hash granularity. Measured at three
granularities (evidence/AUG-204/03-dedupe-counts.txt):

- file-level byte hash: 15 duplicates;
- file-level normalized hash: the same 15;
- block-level full coverage (every parsed section of a handbook file matching
  a canonical section): 15.

The duplicates that DO exist are real: chd34/chd35 reference + snapshot
copies of build-spec/contracts documents, and chd34↔chd35 opcard pairs. The
handbook's bulk (manuscript prose, edition src files, example readmes) is
unique normative content and renders once each. Duplicate count is asserted
below the 490 ceiling by `tests/test_chd_full_corpus.py::test_handbook_renders_held_below_490`.

## Layout

- The AUG-176 distilled set stays flat at this directory's root, byte-stable
  (`DESIGN.xml`, `DATA-MODEL.xml`, … — the converter's `stem_slug_globs`).
- Every NEW render mirrors its source path under this directory
  (`build-spec/autogen/...`, `handbook/chd35/src/...`, `review/chd34-package/...`)
  with parent-path-aware block-id slugs; the loader discovers documents
  recursively, so `spec validate|check|list|assemble` and the recall battery
  see the whole tree.
- Non-duplicate renders carry per-block provenance comments back to their
  source file + heading anchor; duplicates carry `canonical_source` +
  `canonical_render` in the coverage manifest instead of a render.

## Recall battery (AUG-204 AC3)

`scripts/auger-chd-battery.sh` registers every active block of THIS corpus
(one row per block, ~30 min against the live substrate) and runs the AUG-176
query battery over it; it exits 0 only on a PASS verdict — every query
measured, zero register failures, hit rate not below the AUG-176 distilled
baseline of 7/12 (evidence/AUG-176/06-recall-battery.md).
`--check` is the hermetic battery-readiness pass (no substrate contact).

## Portability

`manifest.json` carries per-block `text_sha256` digests, so the corpus is
verifiable WITHOUT the external source tree (AUG-179);
`tests/test_chd_full_corpus.py` follows the same two-leg convention as
`tests/test_spec_ingest.py` — PORTABLE legs read only committed artifacts;
HOST legs (enumeration, re-ingest determinism) skip with a named reason when
`/home/kara/specs/coding-hermes-director` is absent.
