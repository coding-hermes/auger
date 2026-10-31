# SPEC-003 — XML specification source and targeted cross-file assembly

> Status: **implemented (AUG-118).** Additive: the Markdown export contract is unchanged and
> pinned by a golden captured from the base commit.

Feature request and full product rationale: `docs/prd/xml-spec-graph.md` (board rows
`AUG-118`–`AUG-124`). This document is the **engineering design record for the first slice**: the
XML source model, the selector/assembler, and the refusal contract. Later slices (task packets,
one-pass context retrieval, typed test obligations, staged-diff checking) build on this model and
are deliberately out of scope here.

## Rationale

Auger's specifications are currently Markdown documents rendered from DuckBrain tables. Markdown
has no stable identity: a requirement can be quoted, copied into a second page, and edited
independently, and no tool can say whether two paragraphs are the same contract. The concrete
failure this slice fixes is **unresolved identity and relationship**, not reading speed — a
prose search finds a *likely* paragraph but cannot prove that a requirement, its acceptance
criterion, its verification plan and a later change all refer to the same thing.

The design consequence: authored spec content becomes versioned XML where

* every block carries a **stable, repository-unique ID** that is never renumbered;
* the block's **kind, lifecycle status, version, applicability and verification method** are
  typed attributes, so selection is a filter rather than a search;
* relationships are **typed edges** between IDs, so a tool can expand exactly the declared
  closure instead of the whole corpus; and
* Markdown/HTML remain **generated views** — `auger spec assemble` renders a selected bundle and
  never writes back to the XML.

The engineering win is small, exact context: one command returns the requirement you named, its
acceptance criterion, its verification plan, and the shared blocks it declares it depends on —
with a citation per block — instead of a multi-thousand-character document.

## The schema

Normative grammar: `specs/schema/auger-spec-1.0.xsd` (XSD 1.0, `targetNamespace`
`urn:auger:spec:1`, `version="1.0"`). `auger.py` carries the executable half of the same rules,
and `tests/test_xml_specs.py::test_schema_matches_the_validator` compares the two so the prose
grammar and the validator cannot drift: the closed sets, the ID/version/scope patterns and the
attribute inventory are asserted equal.

A document is a `<spec>` root; its children are blocks and `<include>` instructions.

```xml
<spec xmlns="urn:auger:spec:1" id="AUGER-EXPORT" version="1.0.0" schema-version="1.0">
  <include src="shared.xml"/>
  <requirement id="AUG-REQ-014" kind="requirement" status="active" version="1.0.0"
               applies-to="auger.export" verification="test"
               depends-on="AUG-SHARED-DETERMINISM">
    The default `auger export` output remains deterministic and read-only.
  </requirement>
  <acceptance id="AUG-AC-014-1" kind="acceptance" status="active" version="1.0.0"
              applies-to="auger.export" verification="test" verifies="AUG-REQ-014">
    Running the export twice over the same stored rows produces byte-identical output.
  </acceptance>
</spec>
```

| Attribute | On | Rule |
|---|---|---|
| `id` | every block | Stable ID, `^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$`, ≤ 80 chars, unique repository-wide, never renumbered |
| `kind` | every block | One of `section`, `requirement`, `acceptance`, `verification`; **must equal the element name** |
| `status` | every block | One of `active`, `deprecated`, `superseded` |
| `version` | every block | The block's own `X.Y.Z` revision, independent of the document version |
| `applies-to` | optional | Whitespace-separated scope tokens (`auger.export`), matched by the selector |
| `verification` | required on `requirement`, `acceptance` | The *declared* verification method: `test`, `demo`, `analysis`, `inspection` |
| `method` | required on `verification` | The same closed set, under the block that describes the plan |
| `depends-on` | optional | **Closure** edge: forward, expanded by assembly |
| `verifies` | optional | Edge to a `requirement`; expanded **inversely** (see below) |
| `refs`, `implements`, `supersedes` | optional | Navigable edges: must resolve, never expand a bundle |

Document root: `id`, `version` and `schema-version` are required. `schema-version` names the
schema file the document was authored against; any version this build does not implement is
refused rather than guessed at.

**Verification is declared, not claimed.** The attribute states the intended method. A block is
not "verified" because the attribute says `test`; the evidence side of the chain (test path,
verdict, commit) belongs to later slices (`AUG-123`, `AUG-124`).

## Assembly and selection

`auger spec assemble <root> --id … --kind … --applies-to … --status … --version …`
(`--format markdown|json`, `--out PATH`, `--no-deps`).

* **Corpus**: every `*.xml` under the spec root, indexed recursively.
* **Matching**: AND across different criteria, OR within one criterion's repeated values. An
  empty selector is refused — assembly is never a silent whole-corpus dump.
* **Closure** (`--no-deps` disables it): forward `depends-on`, plus **inverse `verifies`** — a
  block that declares it verifies a selected block is part of that block's explicit closure,
  because a requirement without its acceptance criteria and verification plan is not the selected
  contract. `refs`/`implements`/`supersedes` must resolve but never expand a bundle.
* **Order**: corpus order — documents in sorted repository-relative path order, blocks in
  document order, and a spliced fragment's blocks at their `<include>` position. Identical inputs
  produce byte-identical output (`test_selection_is_byte_identical_across_runs`).
* **Citations**: every block carries `source: <repo-relative file>#<id>` — the file the block is
  **authored** in, even when another document splices it, so a citation always points at the
  source of truth rather than at an assembly site.

`auger spec list <root>` prints the same metadata without assembling, so an author can inspect
the corpus (`id`, `kind`, `status`, `version`, citation, scopes) before selecting.

### Includes versus references

Both cross-document mechanisms exist, and they are different tools:

* `<include src="shared.xml"/>` **splices** a fragment's blocks at that position. A fragment may
  be spliced at exactly one site in a corpus; a second splice is refused (`duplicate-include`)
  rather than silently duplicating or silently skipping blocks.
* An **ID reference** (`depends-on`, `verifies`, `refs`, …) is how a block is reused from any
  number of documents without copying or splicing it. This is the mechanism R4 calls for, and the
  fixture corpus demonstrates both: `export.xml` includes `shared.xml`, while `assembly.xml`
  references the same shared blocks by ID.

Only repository-relative local paths are accepted: a scheme-bearing, absolute, or `..`-escaping
`src` is refused (`remote-reference-rejected`, `include-outside-root`). There is no network
fetch and no external entity expansion — a document containing a `DOCTYPE`/`ENTITY` declaration
is refused before any element is built (`external-entity-forbidden`), which also means no
billion-laughs or entity-substitution surface.

## Refusal contract

Every failure is a typed `SpecError(code, detail)`; the CLI prints
`spec error (<code>): <detail>` to **stderr** and exits **2**. Nothing is written to stdout, and
`--out` is created only after the whole corpus has validated and the document has rendered (a
sibling temp file plus `os.replace`, so even an I/O failure cannot leave a truncated document).

| Code | Refused when |
|---|---|
| `missing-spec-root`, `empty-corpus`, `no-root-document` | the root is not a directory / has no `*.xml` / every document is included by another |
| `invalid-xml`, `invalid-root`, `unreadable-document` | unparsable XML, wrong root element, unreadable file |
| `unsupported-schema-version` | `schema-version` is absent or not in `SPEC_SCHEMA_VERSIONS` |
| `unsupported-namespace` | a non-empty namespace other than `urn:auger:spec:1` |
| `external-entity-forbidden` | a `DOCTYPE`/`ENTITY` declaration anywhere in the document |
| `missing-document-metadata` | `<spec>` without `id` or `version` |
| `missing-id`, `invalid-id` | a block with no ID, or an ID outside the stable-ID pattern |
| `missing-kind`, `kind-mismatch` | no `kind`, or `kind` ≠ element name |
| `invalid-status`, `invalid-version`, `invalid-applies-to` | metadata outside the declared shapes |
| `invalid-verification-method` | a `requirement`/`acceptance` without a valid `verification`, or a `<verification>` without a valid `method` |
| `duplicate-id` | one stable ID declared by two blocks (across files) |
| `unresolved-reference`, `invalid-reference-type` | an edge naming a missing block, or `verifies` naming a non-`requirement` |
| `reference-cycle` | a cycle over the typed reference graph (including a self-reference) |
| `invalid-include`, `unresolved-include`, `remote-reference-rejected`, `include-outside-root`, `duplicate-include`, `include-cycle`, `unknown-element` | include instruction and traversal failures |
| `empty-selector`, `no-match` | assembly without a selector, or a selector that matches nothing |
| `unsupported-format` | a format other than `markdown`/`json` (also refused by argparse choices) |

Cycles are refused at validation time, not discovered during assembly, so a cyclic corpus can
never produce a partial bundle.

## Alternatives considered

1. **XSD validation via `lxml` versus a stdlib strict validator (chosen: both, in their places).**
   `pyproject.toml` documents auger as deliberately stdlib-only; adding `lxml` would make the
   runtime dependency surface depend on a C toolchain for a read-only feature. The XSD ships as
   the **normative, editor-facing grammar** (IDE completion, schema-aware validation in an
   editor), and `auger.py` carries an executable validator that enforces the same rules with no
   dependency. The parity test compares the schema's enumerations and patterns with the code's, so
   "same rules" is asserted rather than promised. Rejected: Relax NG (worse editor support,
   still needs a third-party library).
2. **General XInclude / XPath versus a closed `include` element and fixed selector flags
   (chosen: closed).** XInclude invites arbitrary URI resolution and entity expansion, which the
   PRD forbids for security reasons; XPath is a query language whose expressive power the selector
   does not need and whose failure modes (empty match, surprise ordering) are hard to make
   explicit. Fixed typed flags keep the selection inspectable and the ordering specifiable.
   Rejected: `--query EXPR` (a second language to learn, debug and secure).
3. **One aggregate document versus a corpus of documents with cross-file references (chosen:
   corpus).** A single file gives identity but reintroduces the retrieval problem the feature
   exists to solve. The corpus keeps authoring local and lets selection assemble across files.
4. **Follow every typed edge during closure versus only `depends-on` + inverse `verifies`
   (chosen: the narrow pair).** Silent expansion through `refs`/`implements` would make "what did
   I ask for" unknowable; the narrow pair is the exact closure a requirement needs to be
   actionable. Everything else stays navigable, and `--no-deps` turns closure off entirely.
5. **Rewrite `export` to render from XML versus adding a new verb (chosen: additive).** The
   export contract is consumed by other tooling; the golden in `tests/fixtures/xml/expected/`
   (captured from base commit `86764e7`) makes "unchanged" a test rather than an intention.
6. **Multiple `<include>` sites for one fragment versus refuse the second splice (chosen:
   refuse).** Silently skipping or silently duplicating blocks are both lies about the corpus;
   reuse belongs to ID references, and the refusal message says so.

## Verification

| Acceptance criterion | Proof |
|---|---|
| AC-1 versioned schema, stable IDs, typed attributes | `tests/test_xml_specs.py::test_schema_matches_the_validator` (enumerations, patterns, required attributes compared against the code) |
| AC-2 select by ID and attributes, ≥ 2 files, deterministic order, citations preserved | `test_shared_block_reference`, `test_selects_only_requested_blocks_and_their_dependencies`, `test_selection_is_byte_identical_across_runs`, `test_list_reports_ids_attributes_and_citations`, `test_version_and_status_selectors_are_exact`, `test_verifies_expands_from_the_requirement_not_from_the_criterion` |
| AC-3 refusals (unresolved ref, duplicate ID, cycle, unsupported schema version, invalid verification metadata), no partial document | `test_refusals_are_typed_and_emit_no_document` (8 fixtures; each asserts exit 2, empty stdout, the typed code, and that `--out` was not created), plus `test_empty_selector_refused`, `test_unknown_id_is_a_typed_no_match`, `test_successful_out_is_written_atomically` |
| AC-4 Markdown export unchanged; selection emits only requested blocks + explicit dependencies | `test_markdown_export_is_unchanged` against `tests/fixtures/xml/expected/markdown-export-golden.md` (generated from base `86764e7` and re-verified identical against it), `test_selection_matches_the_golden` against `tests/fixtures/xml/expected/assembly-active-auger-export.md` |
| AC-5 the record and its wiring | `test_spec_doc_records_the_decision`, `test_gate_runs_these_tests` (asserts `tests/gate.sh` names this file) |

`tests/gate.sh` names `tests/test_xml_specs.py` in its pytest arm, and the commit guard's test
command is `bash tests/gate.sh`, so the guard and CI run these tests. The suite is hermetic: no
DuckBrain, no JEV, no network, no clock, so it runs identically on hosted CI.

Fixture corpus (`tests/fixtures/xml/specs/`): `export.xml` (splices `shared.xml`, plus a decoy
requirement in another scope and a deprecated one in the same scope), `shared.xml` (authored once,
reused from two documents), `assembly.xml` (reuses the shared blocks by ID; its `refs=` target must
NOT be expanded). Refusal fixtures live under `tests/fixtures/xml/invalid/<code>/`, one directory
per rule, so a refusal test names the rule it proves.

Reproduce by hand:

```bash
python3 auger.py spec validate tests/fixtures/xml/specs
python3 auger.py spec assemble tests/fixtures/xml/specs \
    --kind requirement --applies-to auger.export --status active
python3 -m pytest tests/test_xml_specs.py -q
```

## ch:trace

`ch:trace row=AUG-118 spec=docs/SPEC-003-xml-specs.md#assembly-and-selection test=tests/test_xml_specs.py doc=docs/SPEC-003-xml-specs.md evidence=tests/fixtures/xml/expected wave=none:no-wave-single-worker prompt=none:worker-brief-issued-out-of-band witness=none:self-verified verdict=none:not-judged commit=none:this-commit memory=/project/auger/spec/xml-spec-graph-2026-10-30`
