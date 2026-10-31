# XML spec graph schema (AUG-118 / AUG-123)

Auger XML specs are rooted at `<spec id="..." version="...">`. Every graph node has a stable, repository-unique `id`. This implementation recognizes `<requirement>`, `<verification>`, and `<test>` nodes. A requirement's whitespace-separated `verifies` attribute names one or more verification IDs; `<verification-ref ref="..."/>` is an equivalent explicit edge. A verification may also name its requirement with `verifies="REQ-ID"`. References must resolve to the expected node type.

Example migration obligation:

```xml
<spec id="EXAMPLE" version="1">
  <requirement id="REQ-MIG-1" verifies="TEST-MIG-1">Add a marker row.</requirement>
  <verification id="TEST-MIG-1" type="test" verifies="REQ-MIG-1"
      runner="pytest" path="tests/test_migrations.py"
      selector="test_marker_migration"
      migration="migrations/001_marker.sql"
      fixture="tests/fixtures/prior_schema.sql"
      assertions="marker table exists; version is 1" />
</spec>
```

For automated tests, `runner` is an identifier in Auger's repository harness registry, not a command. The current registered identifier is `pytest`, serialized to an argv vector (`<current Python> -m pytest`). `path` and `selector` are tracked repository-relative test path and Python test function name. Validation confirms both exist and remain inside the repository. No shell string or arbitrary command is accepted or executed.

Migration obligations require the exact repository-relative migration path, supported prior-schema fixture, tracked test path/selector, and explicit assertions. The selected requirement bundle carries those values verbatim. The test obligation itself must load and apply the named migration; a test that merely runs a package suite is insufficient. The XML validator checks that migration and fixture paths exist, while the regression test proves application behavior, including failure against a no-op migration.

Validation reports typed `SpecValidationError` identifiers including `duplicate-id`, `dangling-test-ref`, `dangling-requirement-ref`, `wrong-ref-type`, `invalid-test-ref`, `stale-test-ref`, `stale-migration-ref`, and `incomplete-migration-obligation`. Use:

```text
auger spec validate <spec-root>
auger spec assemble <spec-root> <requirement-id>
```

Assembly is deterministic JSON data and does not invoke tests. Consumers may pass the returned registered runner argv and tracked selector to the repository harness under their own policy; XML never executes code.
