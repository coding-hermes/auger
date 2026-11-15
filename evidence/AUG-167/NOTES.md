# AUG-167 — Corpus time-travel: query the spec selection as-of a version

## What was added

`--as-of REF` on `spec list` and `spec assemble` ONLY (added once in
`_add_spec_selectors`, which only those two parsers share; `validate` / `check` /
`bundle` / `render` / `context` refuse it via argparse). Implementation:

* `_spec_corpus_as_of(spec_root, ref)` (auger.py, next to `_git_toplevel` /
  `_spec_repository_root`): resolves the repo toplevel FROM the spec root
  (reused, not re-implemented), computes the corpus path relative to it,
  materialises that subtree at REF via `git archive --format=tar` (captured as
  bytes, extracted with the stdlib `tarfile` module — no shell string, no
  `.git` mutation, no write into the corpus) into a
  `tempfile.mkdtemp(prefix="auger-as-of-")` staging dir, yields the historical
  corpus root, and `shutil.rmtree`s it in `finally`.
* `cmd_spec` swaps `a.root` to the historical root for the dispatch and
  restores it afterwards; the `SpecError` handler wraps the historical read too,
  so an unknown ref renders as a typed refusal, not a traceback.
* The EXISTING loader/selection/graph code runs unchanged over the historical
  tree — this feature adds no selection semantics of its own.

## Typed refusal codes (all `spec error (<code>)` on stderr, exit 2, empty stdout)

* `as-of-unknown-ref` — REF does not resolve (`git rev-parse --verify` fails)
* `as-of-not-a-repository` — the spec root is not inside a git work tree
* `as-of-corpus-missing` — the corpus path does not exist at REF
  (`git cat-file -e <ref>:<rel>`)
* `as-of-git-unavailable` — the git executable is absent (`FileNotFoundError`
  mapped, including the pathological mid-run disappearance)

## The old-text proof (01-as-of-old-text.txt)

Both repo tags predate the authored corpus (`specs/*.xml` first appear at
6b0d190; v0.2.0/v0.3.0 contain no `specs/`), so the AC's "earlier tag" leg is
proven hermetically: the test (and the evidence script) build a throwaway
2-commit repo whose block BODY is rewritten between commits. At the first
commit `spec assemble --format json --as-of <c1>` carries ONLY
`OLD-SENTINEL-AUG167 body text`; the plain run carries ONLY the NEW sentinel;
`spec list` at the old commit lists the block with its historical citation.

## HEAD equivalence (02-head-equivalence.txt)

On the shipped `specs/` corpus: `spec list` and
`spec assemble --format json` — sha256(plain) == sha256(--as-of HEAD)
(f9d91d0a… / 79eeac3c…), byte for byte.

## Deliberately NOT done

* **Superseded-successor rewriting across versions.** The row reasoning mentions
  "superseded blocks resolved to their historical successors". A historical
  query returns the historical corpus exactly as the loader sees any corpus; no
  cross-version successor chain is resolved. Reason: the board's own ACs (the
  three measurable legs) do not require it, and rewriting block graphs across
  versions is a semantics decision (which status counts, whether a superseded
  block at REF resolves to a successor that may not exist at REF) that belongs
  to its own row. The historical supersede edges that exist AT REF still work,
  because the ordinary loader runs unchanged.
* **`--as-of` on any non-selection verb.** Refused by argparse (tests 6 and 7).
* **No runtime dependency added.** `tarfile`/`shutil` are stdlib; `dependencies`
  stays `[]`.

## Notes on the evidence

`05-read-only.txt` shows `git status --short` with this task's own files
present (auger.py, docs, gate.sh, tests/, evidence/) — that is the pre-commit
worktree of THIS task, not a mutation by the `--as-of` run; the proof is that
the file set is byte-identical before/after (sha256 diff empty) and that no
`auger-as-of-*` staging dir survives.

## Red → green

Tests were written first (`tests/test_spec_as_of.py`): pre-implementation the
run was 9 failed / 2 passed (the 2 passes are the argparse-refusal negatives,
which pass by construction). After implementation: 11 passed. Adjacent spec
suites re-run green (test_xml_specs, test_spec_repository_root,
test_spec_corpus_view, test_spec_render, test_spec_assembly_report,
test_spec_context, test_xml_task_contract = 125 passed total).
