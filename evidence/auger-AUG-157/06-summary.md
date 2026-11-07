# AUG-157 — summary

Row: AUG-157 (P2, complexity 2) — "Shipped example corpus claims it validates outside any
repository; at HEAD it refuses unresolvable-repo-root without --repo-root"
Base: 37374fa (wt/auger-157). Single writer; no pushes, no merges, no board/lifecycle writes.

## Resolution chosen: A (lazy root)

`validate_xml_specs` resolved `_spec_repository_root()` eagerly for every corpus, before any
obligation was read, so the shipped obligation-free wheel example refused
`unresolvable-repo-root` (rc 2) outside a git work tree — exactly what its own comment claims
it does not do.

Fix (auger.py): an explicit `--repo-root`/`repository_root=` is still validated EAGERLY (bad
explicit root = immediate typed refusal); an implicit root is resolved only at the first block
that names a `path`/`migration`/`fixture` to resolve against it. A corpus carrying a
`type="test"` obligation in a non-git directory with no `--repo-root` still refuses rc 2
`unresolvable-repo-root` — the AUG-127 invariant is unchanged. Resolution B (rewriting the
comment/docs-only) was not needed: the shipped comment is now TRUE.

No existing test asserted the old eager behaviour for an obligation-free corpus; every prior
`unresolvable-repo-root` test pins the refusal for an obligation-CARRYING corpus and still
passes untouched. The check did not weaken; the code moved.

## Files touched

- auger.py — lazy root in `validate_xml_specs` (explicit root eager, implicit root on first
  obligation) + docstring.
- tests/test_spec_repository_root.py — new
  `test_an_obligation_free_corpus_validates_outside_any_repository` (validates rc 0 in a
  non-git dir via both verbs; bad explicit `--repo-root` still refused eagerly) + docstring AC
  map line. The file is already named in tests/gate.sh's pytest arm, and its
  `test_gate_arm_runs_this_file` asserts that naming — no new file, no gate.sh edit needed.
- docs/XML-SPEC-SCHEMA.md — "The repository root" now states the lazy resolution in words.

## Commands and results (full transcripts in 01–05)

| # | command | result |
|---|---------|--------|
| 1 (falsifier, pre-fix) | `spec validate` / `spec check` on a mktemp non-git copy of examples/xml/minimal.xml | rc 2 `unresolvable-repo-root` both; `grep -c "validates outside any repository" examples/xml/minimal.xml` = 1 |
| 0 (red proof) | `pytest tests/test_spec_repository_root.py::test_an_obligation_free_corpus_validates_outside_any_repository` with auger.py at 37374fa | 1 failed (rc 2 vs expected 0) |
| 2 (post-fix) | same `spec validate` / `spec check` | rc 0; stdout `spec: 1 document(s), 2 block(s), 1 reference(s) ok`; check `status: valid` |
| 3 (invariant) | obligation-carrying corpus in mktemp non-git dir, no flag | rc 2 `unresolvable-repo-root`; `pytest tests/test_spec_repository_root.py` → 9 passed |
| 4 (packaging) | `pytest tests/test_packaging_wheel.py` | 6 passed |
| 4 (packaging) | `pip wheel . --no-deps --no-build-isolation` + fresh-venv offline install | installed `<venv>/share/auger/specs/examples/minimal.xml` diff vs repo copy → rc 0 |
| 5 (gate) | `PATH=.../.venv/bin:$PATH DUCKBRAIN_URL=... DUCKBRAIN_API_KEY=... AUGER_GATE_PYTEST_BUDGET=3000s bash tests/gate.sh` | 711 passed, 2 skipped (declared live arms), `GATE PASS`, script rc 0 (16m28s; launched 21:41Z, clear of the :36 DuckBrain S3 sync window) |

## Could not prove / notes

- Nothing outstanding against the acceptance criteria. The gate's two skips are the declared
  hosted-CI-only live arms; the gate's own skipped-arm policy arm passed (`AUG-130
  skipped-arm policy: PASS`) and no escape hatch was used.
- Gate ran on the working tree at this tree's state; the commit below stages exactly the three
  files above plus the evidence dir. The driver merges and re-gates.
