# XML spec graph schema (AUG-118 / AUG-123)

Auger XML specs are rooted at `<spec id="..." version="...">`. Every graph node has a stable, repository-unique `id`. This implementation recognizes `<requirement>`, `<verification>`, and `<test>` nodes. A requirement's whitespace-separated `verifies` attribute names one or more verification IDs; `<verification-ref ref="..."/>` is an equivalent explicit edge. A verification may also name its requirement with `verifies="REQ-ID"`. References must resolve to the expected node type.

Example migration obligation — the shape `spec validate` and `spec check` both read (one loader reads
the root and the blocks for both). The paths below are illustrative: a corpus is only valid if they
exist in the repository and are tracked in git, and `tests/fixtures/xml/verification/migration.xml` is
the same shape with paths that resolve.

```xml
<spec xmlns="urn:auger:spec:1" id="EXAMPLE" version="1.0.0" schema-version="1.0">
  <requirement id="REQ-MIG-1" kind="requirement" status="active" version="1.0.0"
               verification="test" verifies="TEST-MIG-1">Add a marker row.</requirement>
  <verification id="TEST-MIG-1" kind="verification" status="active" version="1.0.0"
      method="test" verifies="REQ-MIG-1"
      runner="pytest" path="tests/test_migrations.py"
      selector="test_marker_migration"
      migration="migrations/001_marker.sql"
      fixture="tests/fixtures/prior_schema.sql"
      assertions="marker table exists; version is 1" />
</spec>
```

For automated tests, `runner` is an identifier in Auger's repository harness registry, not a command. The current registered identifier is `pytest`, serialized to an argv vector (`<current Python> -m pytest`). `path` and `selector` are tracked repository-relative test path and Python test function name. Validation confirms both exist and remain inside the repository. No shell string or arbitrary command is accepted or executed.

ch:trace row=AUG-123 spec=docs/prd/xml-spec-graph.md#R15 test=tests/test_verification_graph.py doc=docs/XML-SPEC-SCHEMA.md

Migration obligations require the exact repository-relative migration path, supported prior-schema fixture, tracked test path/selector, and explicit assertions. The selected requirement bundle carries those values verbatim. The test obligation itself must load and apply the named migration; a test that merely runs a package suite is insufficient. The XML validator checks that migration and fixture paths exist, while the regression test proves application behavior, including failure against a no-op migration.

Validation reports typed `SpecValidationError` identifiers including `duplicate-id`, `dangling-test-ref`, `dangling-requirement-ref`, `wrong-ref-type`, `invalid-test-ref`, `stale-test-ref`, `stale-migration-ref`, and `incomplete-migration-obligation`. Use:

```text
auger spec validate <spec-root>                    # the whole verdict: block corpus + every obligation
auger spec check    <spec-root>                    # the same verdict, obligation node ids as JSON
auger spec bundle   <spec-root> <requirement-id>   # one requirement's verification bundle as JSON
auger spec list     <spec-root>                     # blocks with kind, status, version, source
auger spec assemble <spec-root> --id <block-id>    # block selection + explicit dependency closure
```

`spec validate` and `spec check` are one verdict from one loader. The corpus root, the
`urn:auger:spec:1` namespace, the schema version, the document metadata and the
block/reference grammar are read once by `load_spec_corpus`; the obligation pass then resolves
every test obligation (tracked path, selector, migration, prior-schema fixture, assertions)
over those SAME documents. The verbs differ only in what they print. Before AUG-134 each verb
parsed the corpus itself and they disagreed — `validate` accepted a namespaced root that
`check` refused as `invalid-root`; before AUG-132 `validate` resolved no obligation at all, so
a corpus whose verification block named `tests/does-not-exist.py` reported `ok` with exit 0.
A block that only declares `type="test"`/`method="test"` states a METHOD (the authoring
grammar's `verification=`/`method=` shape), not a test location; an obligation is a block that
actually points at a test with `runner`/`selector`/`path`.

Assembly is deterministic JSON data and does not invoke tests. Consumers may pass the returned registered runner argv and tracked selector to the repository harness under their own policy; XML never executes code.
