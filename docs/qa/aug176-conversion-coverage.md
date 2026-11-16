# AUG-176 conversion coverage report

Generated from the committed manifests (corpora/*/manifest.json); counts are read
back from the manifests, never hand-typed. Owner directive: convert the
code-factory, coding-hermes-director and auger residual-prose spec files into the
auger XML corpus format with zero data loss, so the embedded text returns good
recall (AUG-176, AC1/AC2). Regenerate with scripts/gen_coverage_report.py.

## corpora/code-factory

Source root: /home/kara/code-factory/specs — code-factory repo, specs/ (7 files)

Id scheme: <PREFIX>-<FILE-SLUG>-<HEADING-SLUG>-S<ordinal> (see scripts/ingest_markdown_specs.py module docstring)

Block integrity: text_sha256 = sha256 of the block's whitespace-normalized source-span text (see scripts/ingest_markdown_specs.py module docstring)

| source file | sections | blocks | skipped |
|---|---|---|---|
| code-factory/specs/ENVELOPE.md | 10 | 10 | 0 |
| code-factory/specs/FLEET-MEMORY.md | 13 | 13 | 0 |
| code-factory/specs/HARNESS-MATRIX.md | 10 | 10 | 0 |
| code-factory/specs/HARNESS-MEMORY.md | 14 | 14 | 0 |
| code-factory/specs/HARNESS.md | 11 | 11 | 0 |
| code-factory/specs/NODE.md | 13 | 13 | 0 |
| code-factory/specs/TRACE.md | 9 | 9 | 0 |
| TOTAL | 80 | 80 | 0 |

Excluded source files (AC2 — each with a named reason):

- none: every markdown file under the source root converts

Skipped sections: none — every H2/H3 heading and the preamble converted to exactly one block (AC1).

## corpora/auger-docs

Source root: /home/kara/worktrees/auger-AUG-179/docs — auger repo, docs/ residual prose

Id scheme: <PREFIX>-<FILE-SLUG>-<HEADING-SLUG>-S<ordinal> (see scripts/ingest_markdown_specs.py module docstring)

Block integrity: text_sha256 = sha256 of the block's whitespace-normalized source-span text (see scripts/ingest_markdown_specs.py module docstring)

| source file | sections | blocks | skipped |
|---|---|---|---|
| docs/DESIGN.md | 8 | 8 | 0 |
| docs/ENGINE.md | 9 | 9 | 0 |
| docs/MUSTER-CONSUMER.md | 4 | 4 | 0 |
| docs/RELEASE.md | 12 | 12 | 0 |
| docs/SPEC-001-graph.md | 14 | 14 | 0 |
| docs/SPEC-002-bundles.md | 16 | 16 | 0 |
| docs/SPEC-003-xml-specs.md | 9 | 9 | 0 |
| docs/SPEC-CORPUS.md | 8 | 8 | 0 |
| docs/SUBSTRATE-VERIFICATION.md | 8 | 8 | 0 |
| docs/VERBS.md | 19 | 19 | 0 |
| docs/XML-SPEC-COVERAGE.md | 5 | 5 | 0 |
| docs/XML-SPEC-SCHEMA.md | 10 | 10 | 0 |
| docs/dependency_pluggy_2026-10-18.md | 9 | 9 | 0 |
| docs/muster-scope.md | 8 | 8 | 0 |
| TOTAL | 139 | 139 | 0 |

Excluded source files (AC2 — each with a named reason):

- `docs/SPEC-CORPUS-VIEW.md` — generated view (scripts/gen_spec_view.py) of the specs/ XML corpus, not authored prose — converting it would snapshot a derived artifact and drift on every regeneration

Skipped sections: none — every H2/H3 heading and the preamble converted to exactly one block (AC1).

## corpora/coding-hermes-director

Source root: /home/kara/specs/coding-hermes-director — coding-hermes-director spec tree (structured set; audit/tickets/handbook/review-evidence excluded with reasons)

Id scheme: <PREFIX>-<FILE-SLUG>-<HEADING-SLUG>-S<ordinal> (see scripts/ingest_markdown_specs.py module docstring)

Block integrity: text_sha256 = sha256 of the block's whitespace-normalized source-span text (see scripts/ingest_markdown_specs.py module docstring)

| source file | sections | blocks | skipped |
|---|---|---|---|
| coding-hermes-director/DESIGN.md | 57 | 57 | 0 |
| coding-hermes-director/build-spec/INDEX.md | 18 | 18 | 0 |
| coding-hermes-director/build-spec/INTEGRATION.md | 8 | 8 | 0 |
| coding-hermes-director/build-spec/NORMATIVE-MANIFEST.md | 2 | 2 | 0 |
| coding-hermes-director/build-spec/REFERENCE-INDEX.md | 4 | 4 | 0 |
| coding-hermes-director/build-spec/VERDICT.md | 4 | 4 | 0 |
| coding-hermes-director/build-spec/VOL-A-story-roles-lifecycle.md | 48 | 48 | 0 |
| coding-hermes-director/build-spec/VOL-B-records-storage-recovery.md | 53 | 53 | 0 |
| coding-hermes-director/build-spec/VOL-C-tools-messaging-admission-authz.md | 76 | 76 | 0 |
| coding-hermes-director/build-spec/VOL-D-config-acceptance-ops.md | 73 | 73 | 0 |
| coding-hermes-director/build-spec/VOL-E-operation-reference.md | 541 | 541 | 0 |
| coding-hermes-director/build-spec/VOL-E-tickets-workstate.md | 18 | 18 | 0 |
| coding-hermes-director/build-spec/VOL-F-reference-map.md | 1 | 1 | 0 |
| coding-hermes-director/build-spec/terminology.md | 8 | 8 | 0 |
| coding-hermes-director/contracts/DATA-MODEL.md | 22 | 22 | 0 |
| coding-hermes-director/contracts/ENDPOINTS.md | 33 | 33 | 0 |
| coding-hermes-director/contracts/OPERATIONS.md | 1 | 1 | 0 |
| coding-hermes-director/contracts/gateway-identity.md | 3 | 3 | 0 |
| coding-hermes-director/contracts/request-attempt-lifecycle.md | 7 | 7 | 0 |
| coding-hermes-director/review/PLAN-3.3.md | 8 | 8 | 0 |
| coding-hermes-director/review/PLAN.md | 34 | 34 | 0 |
| coding-hermes-director/sections/9-multi-host.top.md | 8 | 8 | 0 |
| TOTAL | 1027 | 1027 | 0 |

Excluded source files (AC2 — each with a named reason):

- `audit/` — process artifact (audit transcripts), not spec content — owner row names audit/ as excluded
- `tickets/` — process artifact (ticket tracker), not spec content — owner row names tickets/ as excluded
- `handbook/` — compiled PDF volumes (owner-facing digest). The handbook is the COMPLETENESS ORACLE for this conversion (foreman_note), not a conversion source: its curated section list is cross-checked against the corpus in evidence/AUG-176/04-handbook-parity.txt
- `review/ (except PLAN.md, PLAN-3.3.md)` — process artifacts: per-review evidence .txt files, review-log.json, packaging dirs (chdNN-package/) and handoff notes — not spec content. The two review PLAN documents ARE spec content and are converted
- `contracts/ (non-markdown)` — machine-readable contract files (openapi.yaml, config templates, JSON schemas, fixtures, __pycache__, validate_config.py) — the markdown contracts are converted; binary/schema files are not prose
- `build-spec/autogen/, acceptance/, integrated/, integration-evidence/, non-markdown files` — generated/derived build artifacts (JSON state machines, inventories, evidence bundles) — the 14 authored markdown volumes and indexes are converted

Skipped sections: none — every H2/H3 heading and the preamble converted to exactly one block (AC1).
