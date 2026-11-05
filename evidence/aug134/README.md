# AUG-134 evidence — one corpus loader, one verdict

The defect: `load_spec_corpus` matched the Clark-notation root `{urn:auger:spec:1}spec`,
while `validate_xml_specs` compared the bare `document.tag` to `"spec"`. On the SAME
directory one verb accepted the corpus and the other refused it as `invalid-root`, so the
verdict depended on which verb a caller happened to type.

| # | transcript | tree | disagreements |
|---|---|---|---|
| 1 | `01-pre-fix-matrix.txt` | clean checkout of `fcb68b0` | **3** of 13 corpus roots (`specs`, `verification`, `invalid/stale-test-obligation`) |
| 2 | `02-post-fix-matrix.txt` | this branch | **0** of 13 |

Each row runs both verbs on the same root and prints their exit codes:

```
  tests/fixtures/xml/specs                         0        2       NO     <- pre-fix
  tests/fixtures/xml/verification                  0        2       NO
  tests/fixtures/xml/invalid/stale-test-obligation 0        2       NO
```

## The fix

One root/shape loader. `_xml_spec_documents` no longer parses documents itself; it calls
`_spec_read_document` (the loader `load_spec_corpus` already used), so the root element, the
`urn:auger:spec:1` namespace, `schema-version`, the document id/version metadata and the
DOCTYPE/ENTITY refusal are decided in exactly one place. Its typed `SpecError` is re-raised
as `SpecValidationError` with the same code and detail, so both verbs print the same
diagnostic and exit with the same code.

The obligation pass then consumes the SAME loaded documents: `_spec_dispatch` loads the
corpus once and calls `validate_xml_specs(root, loaded=(root, documents))`. That is also why
this commit carries AUG-132: agreement on the shipped fixture corpus is only possible if
`spec validate` runs the obligation pass (`spec check`'s `missing-verification-ref` rule
otherwise refused `specs/` while `validate` accepted it).

Three rules had to be reconciled for one verdict to be honest rather than a narrowing:

1. **Root shape** — both verbs now require the shape the authoring loader requires
   (namespace, `schema-version`, document id/version). Probe corpora in
   `tests/test_verification_graph.py` and `tests/test_xml_specs.py` were updated to write that
   shape; the rules they prove are unchanged.
2. **"A requirement must be verifiable"** — refs (`verifies=`) OR a declared verification
   METHOD (`verification=`), which the authoring grammar already requires on every
   requirement. Keying on refs alone refused every corpus written in the authoring grammar.
3. **Method vocabulary** — the obligation validator accepted `manual`, the authoring enum
   names the same intent `demo`; the union is accepted here while the loader still enforces
   the authored enum for any corpus reaching the CLI.

Nothing was relaxed for test paths: a declared test path must still resolve inside the
repository, be tracked in git, and contain the named selector.

## Reproduce

```bash
git archive fcb68b0 | tar -x -C /tmp/aug134-pre
mkdir -p /tmp/aug134-pre/tests/fixtures/xml/invalid/stale-test-obligation
cp tests/fixtures/xml/invalid/stale-test-obligation/stale-test-obligation.xml \
   /tmp/aug134-pre/tests/fixtures/xml/invalid/stale-test-obligation/
bash evidence/aug134/matrix.sh /tmp/aug134-pre /tmp/pre.txt      # disagreements=3
bash evidence/aug134/matrix.sh "$PWD"        /tmp/post.txt       # disagreements=0
```

Lock-in: `tests/test_spec_validators_agree.py` drives both verbs as subprocesses over every
corpus root under `tests/fixtures/xml/`, asserts they agree on exit code AND on the exact
diagnostic, and carries an expected-verdict table (plus a coverage test that fails when a new
fixture directory is added without a row) so agreement cannot be satisfied by both verbs
refusing everything. Named in `tests/gate.sh`'s pytest arm.
