<!--
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

## Measured size

- documents: 3
- blocks: 12
- references: 7
- schema version: 1.0
- corpus root: `specs/`

## `spec-graph.xml`

document id: `AUGER-SPEC-GRAPH` · version 1.0.0

### SPEC-GRAPH-CORPUS · requirement · active · v1.0.0

- source: `spec-graph.xml#SPEC-GRAPH-CORPUS`
- applies-to: `auger.spec.load auger.spec.validate auger.spec.check`
- verification: `test`
- verifies: `SPEC-GRAPH-CORPUS-V1` `SPEC-GRAPH-CORPUS-V2`

`auger spec validate` and `auger spec check` read one corpus through one loader: every `*.xml` file below the spec root is indexed, each document's root element is `<spec>` in the `urn:auger:spec:1` namespace carrying `schema-version="1.0"`, `id` and `version`, and a document declaring a DOCTYPE or ENTITY is refused (`external-entity-forbidden`). Because both verbs hand the same documents to the same obligation pass, one corpus earns one verdict and one diagnostic, and a document the published XSD rejects is refused (`schema-invalid`) even where the code's own rules would have accepted it.

### SPEC-GRAPH-CORPUS-V1 · verification · active · v1.0.0

- source: `spec-graph.xml#SPEC-GRAPH-CORPUS-V1`
- applies-to: `auger.spec.load auger.spec.validate auger.spec.check`
- method: `test`

Drives `spec validate` and `spec check` as subprocesses over every fixture corpus root and asserts the same exit code, the same diagnostic (byte-identical stderr on refusal) and the same verdict on acceptance.

### SPEC-GRAPH-CORPUS-V2 · verification · active · v1.0.0

- source: `spec-graph.xml#SPEC-GRAPH-CORPUS-V2`
- applies-to: `auger.spec.load auger.spec.validate auger.spec.check`
- method: `test`

A well-formed document whose only defect is an attribute the grammar does not declare is refused by both verbs with `schema-invalid`, naming the document and the attribute — the backstop the code's own rules leave open.

## `spec-verbs.xml`

document id: `AUGER-SPEC-VERBS` · version 1.0.0

### SPEC-VERBS-SELECTION · requirement · active · v1.0.0

- source: `spec-verbs.xml#SPEC-VERBS-SELECTION`
- applies-to: `auger.spec.select`
- verification: `test`
- verifies: `SPEC-VERBS-SELECTION-V1`

`auger spec list` and `auger spec assemble` select blocks by `--id`, `--kind`, `--applies-to`, `--status` and `--version`, ANDing criteria across flags and ORing repeated values within one flag. An assembly contains the matched blocks plus their explicit closure — forward `depends-on` and inverse `verifies`, so a requirement pulls the criteria and verifications that name it — and `--no-deps` emits the matched set alone.

### SPEC-VERBS-SELECTION-AC1 · acceptance · active · v1.0.0

- source: `spec-verbs.xml#SPEC-VERBS-SELECTION-AC1`
- applies-to: `auger.spec.select`
- verification: `test`
- verifies: `SPEC-VERBS-SELECTION`

`auger spec assemble <root> --out <file>` with no selector exits 2, prints `spec error (empty-selector)` on stderr, prints nothing on stdout, and creates no file: assembly is never a silent whole-corpus dump.

### SPEC-VERBS-SELECTION-V1 · verification · active · v1.0.0

- source: `spec-verbs.xml#SPEC-VERBS-SELECTION-V1`
- applies-to: `auger.spec.select`
- method: `test`

Assembles one requirement from a two-file fixture corpus and asserts the emitted ids are the requirement, its acceptance criterion and its `depends-on` target, in provenance order, with the edge that pulled each dependency and `refs=`-only blocks never expanded.

### SPEC-VERBS-SELECTION-V2 · verification · active · v1.0.0

- source: `spec-verbs.xml#SPEC-VERBS-SELECTION-V2`
- applies-to: `auger.spec.select`
- method: `test`

Runs `spec assemble` with no selector and an `--out` target, asserting exit 2, empty stdout, the `empty-selector` error class, and that the target was never created.

## `verification.xml`

document id: `AUGER-SPEC-OBLIGATIONS` · version 1.0.0

### SPEC-OBL-CONSUMER · requirement · active · v1.0.0

- source: `verification.xml#SPEC-OBL-CONSUMER`
- applies-to: `auger.spec.obligation`
- verification: `test`
- verifies: `SPEC-OBL-CONSUMER-V1` `SPEC-OBL-CONSUMER-V2`

A test obligation's `path` and `selector` are repository-relative and resolve inside the repository that CONSUMES the corpus: a consumer repository's own git-tracked test file is accepted by `spec validate` and `spec check`, and `spec bundle` returns that `path` and `selector` verbatim with the registered runner's argv vector. `--repo-root DIR` names the consuming repository when the corpus lives outside it. With no resolvable root the run refuses (`unresolvable-repo-root`) rather than grading the obligation against Auger's own checkout.

### SPEC-OBL-CONSUMER-V1 · verification · active · v1.0.0

- source: `verification.xml#SPEC-OBL-CONSUMER-V1`
- applies-to: `auger.spec.obligation`
- method: `test`

Builds a fresh consumer repository with its own tracked `tests/test_marker.py`, validates and checks the corpus that names it, and asserts the bundle carries `path="tests/test_marker.py"` and `selector="test_marker"` with no Auger path in the resolved fields.

### SPEC-OBL-CONSUMER-V2 · verification · active · v1.0.0

- source: `verification.xml#SPEC-OBL-CONSUMER-V2`
- applies-to: `auger.spec.obligation`
- method: `test`

Points a corpus outside any work tree at `tests/test_xml_specs.py`, which exists and is tracked in Auger's own checkout, and asserts the run refuses with `unresolvable-repo-root` and names `--repo-root` as the fix.

### SPEC-OBL-RUNNER · requirement · active · v1.0.0

- source: `verification.xml#SPEC-OBL-RUNNER`
- applies-to: `auger.spec.obligation`
- verification: `test`
- verifies: `SPEC-OBL-RUNNER-V1`

A `<verification>` or `<test>` block that points at a test carries `runner`, `path` and `selector` together. `runner` is an identifier in Auger's registry — today only `pytest` — serialized to the argv vector `<current Python> -m pytest`; anything else, including a shell string such as `bash -c`, is refused (`invalid-test-ref`), so XML never executes a command.

### SPEC-OBL-RUNNER-V1 · verification · active · v1.0.0

- source: `verification.xml#SPEC-OBL-RUNNER-V1`
- applies-to: `auger.spec.obligation`
- method: `test`

Asserts a resolved obligation's runner is the argv list `[sys.executable, "-m", "pytest"]`, then writes a `<test runner="bash -c">` obligation and asserts validation refuses it with `invalid-test-ref`.

## Reference index

| from | edge | to |
| --- | --- | --- |
| `SPEC-GRAPH-CORPUS` | `verifies` | `SPEC-GRAPH-CORPUS-V1` |
| `SPEC-GRAPH-CORPUS` | `verifies` | `SPEC-GRAPH-CORPUS-V2` |
| `SPEC-VERBS-SELECTION` | `verifies` | `SPEC-VERBS-SELECTION-V1` |
| `SPEC-VERBS-SELECTION-AC1` | `verifies` | `SPEC-VERBS-SELECTION` |
| `SPEC-OBL-CONSUMER` | `verifies` | `SPEC-OBL-CONSUMER-V1` |
| `SPEC-OBL-CONSUMER` | `verifies` | `SPEC-OBL-CONSUMER-V2` |
| `SPEC-OBL-RUNNER` | `verifies` | `SPEC-OBL-RUNNER-V1` |
