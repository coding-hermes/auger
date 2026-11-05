# AUG-132 evidence — `spec validate` enforces test obligations

The defect: `spec validate` counted documents, blocks and references and resolved nothing,
so a corpus whose `<verification>` block named `tests/does-not-exist.py` printed
`spec: 1 document(s), 2 block(s), 1 reference(s) ok` and exited **0**.

The fix: `spec validate` and `spec check` run one verdict — the corpus is loaded once by
`load_spec_corpus` and the obligation pass (`validate_xml_specs`) runs over those same
documents. A test obligation naming a path that is not a tracked file in the repository
refuses the run with the document, the block id, the declared path and the resolved path.

| # | transcript | tree | `spec validate` |
|---|---|---|---|
| 1 | `01-pre-fix-validate-passes.txt` | clean checkout of `fcb68b0` | `... ok`, **rc 0** |
| 2 | `02-post-fix-validate-refuses.txt` | this branch | `stale-test-ref: stale-test-obligation.xml: AUG-VER-STALE-1: tests/does-not-exist.py (resolved /…/tests/does-not-exist.py)`, **rc 2** |

Both transcripts also show `spec check` on the same directory, which is how AUG-134 surfaced
inside this proof: on the pre-fix code the two verbs disagree (0 vs 2), and `check` could not
even reach the obligation because it refused the namespaced root first.

## Fixture

`tests/fixtures/xml/invalid/stale-test-obligation/stale-test-obligation.xml` — a corpus that
is valid everywhere except the obligation: namespaced root, schema version, stable ids,
`kind`/`status`/`version`, and a resolving `verifies` edge. `tests/test_spec_validate_obligations.py`
pins that non-vacuity: it loads the fixture with `load_spec_corpus` and asserts the two blocks
resolve, so the refusal above can only come from the obligation pass.

## Reproduce

```bash
git archive fcb68b0 | tar -x -C /tmp/aug132-pre
mkdir -p /tmp/aug132-pre/tests/fixtures/xml/invalid/stale-test-obligation
cp tests/fixtures/xml/invalid/stale-test-obligation/stale-test-obligation.xml \
   /tmp/aug132-pre/tests/fixtures/xml/invalid/stale-test-obligation/
bash evidence/aug132/probe.sh /tmp/aug132-pre /tmp/pre.txt "pre-fix code"   # rc 0  <- the defect
bash evidence/aug132/probe.sh "$PWD"        /tmp/post.txt "post-fix code"   # rc 2  <- the fix
```

## What the obligation gate now treats as an obligation

A node that **points at a test**: a `<test>` block, or any block carrying `runner`/`selector`/
`path`. A block that only declares `type="test"`/`method="test"` states a METHOD, not a
location — the authoring grammar (`SPEC_REQUIRES_METHOD`, `method=` on `<verification>`) uses
exactly that shape for verification plans, and treating it as a broken pointer would refuse
the shipped fixture corpus. That reconciliation is what let the two verbs become one verdict
without weakening either: the shipped corpus still validates, and a corpus that names a test
path still has that path resolved, tracked, and selector-checked.

Lock-in: `tests/test_spec_validate_obligations.py` (5 tests), named in `tests/gate.sh`'s
pytest arm.
