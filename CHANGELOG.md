# Changelog

## v0.3

Tagged v0.3.0 on 2026-10-22.

- ask split into generation (large model) + judgement (JEV) (AUG-052)
- read-first HTTP API on :8768 (muster contract, AUG-067) + OpenAPI contract + parity gate (AUG-066)
- embedding-store HTTP search route (AUG-046)
- breaks-edge invalidation in dump/export/status (AUG-100)
- lexical cosine prefilter in gate_question (AUG-063)
- fail-closed init on a 0/N declaration tally (AUG-105); ask ceiling-stop (AUG-095); dead-substrate verbs exit consistently (AUG-101)
- ask runs proposer + JEV concurrently (AUG-110), set_state/decision NOT NULL fixes
- pyproject.toml: pip-installable (QA-AUGER-11); pyyaml in tests/requirements-dev.txt (QA-AUGER-12)
- next_id declared-id law (AUG-107); check sticky-band hysteresis (AUG-086); gate teardown None-tolerance (AUG-040)
- README: api verb documented, muster private-link removed, real gate named, install block added (README-008..014)
- shipped usage skill + diagnostics refreshed (DOC-011..019)

## Unreleased

- AUG-204 full-tree corpus coverage: `scripts/ingest_markdown_specs.py` ingests the FULL
  coding-hermes-director tree by directory scope — all 648 canonical `.md` paths accounted in
  `corpora/coding-hermes-director/coverage-manifest.json` (111 first-class renders, 411
  handbook-unique renders, 15 duplicate-of-canonical + 64 duplicate-of-handbook provenance
  pointers, 47 audit provenance records); the AUG-176 distilled 22 stay byte-stable with their
  block ids. `scripts/auger-chd-battery.sh` wraps the AUG-176 recall battery over the full
  corpus (register+run, PASS = every query measured at >= the 7/12 distilled baseline);
  `tests/test_chd_full_corpus.py` pins coverage/dedupe/determinism/battery-contract in the
  gate pytest arm.
- code-factory corpus regenerated after upstream source drift (CF-131/CF-313/CF-521 + new
  GOALS.md / NODE-PHYSICAL.md, 2026-11-21/22): 80 -> 93 blocks, every AUG-176 battery target
  id preserved (drift caught by `test_shipped_blocks_are_verbatim_and_manifests_reconcile`,
  fixed by the sanctioned regen).
- markdown spec sets convert into `auger spec` XML corpora with no loss:
  `scripts/ingest_markdown_specs.py` emits one `<section>` block per H2/H3 heading plus the
  preamble, verbatim prose with per-block provenance, and a `manifest.json` carrying the
  per-file section/block counts and reasoned skips; three corpora ship converted
  (code-factory, auger-docs, coding-hermes-director), every one validating and checking clean.
  `scripts/recall_battery.py` registers those blocks in the substrate and scores 36
  named-target queries by top-5 hit rate per project (AUG-176)
- `spec list` and `spec assemble` gain `--as-of REF`: the corpus is read as it existed at a
  commit, tag or branch, so a query can be answered against a past state of the specs instead
  of the working tree. `--as-of HEAD` is byte-identical to plain selection, the read touches
  neither the corpus nor `.git`, and an unknown ref / non-repository / missing corpus is a
  typed refusal rather than an empty result (AUG-167)
- `spec align` gains an opt-in `--semantic` pass (default OFF, also reachable as
  `spec check --diff-stdin`): it reports active blocks that cite a block the corpus itself
  marks deprecated or superseded, as findings of class `semantic` that quote the literal
  source text they judged and carry an uncertainty band. The pass is deterministic — no LLM,
  no network — a finding that cannot quote its evidence is refused rather than emitted,
  severity stays warn/info, and without the flag the payload is byte-identical to before
  (AUG-142)
- a worker brief can now carry the governing spec blocks for its task:
  `scripts/brief_spec_context.py` resolves a board row through auger's own `spec context`
  selection and emits the selected blocks verbatim as a Markdown section of the brief. A row
  with no spec context writes zero bytes plus one honest stderr line (never a placeholder
  heading), and an ambiguous row exits 3 with its candidate ids instead of guessing (AUG-165)
- `status`'s coverage summary no longer calls a seeded NOT-REACHED domain a missing row: the
  `NOT-REACHED N` half now carries the gloss its counter implements — "a seeded row with no
  decision or question row carrying the num" — instead of "(no such row)", the ABSENT claim the
  header line above it contradicted (AUG-114)
- pyproject.toml: `dev` extra (`pip install -e '.[dev]'`) installs pytest + PyYAML — the clean
  dev install can collect the OpenAPI parity suite (QA-AUGER-12-R2); runtime install stays
  stdlib-only

## v0.2

Tagged [v0.2.0](https://github.com/coding-hermes/auger/releases/tag/v0.2.0) on 2026-09-30.

- record verb family (break/assumption/unknown/option)
- export verb
- retrieval-tier labeling on recall/check (AUG-092)
- clean missing-namespace errors (AUG-085); namespace-leak audit lag tolerance (AUG-080)
- answer advances domain row so status stops contradicting itself (AUG-059)
- README: auth requirements, gridless answer example, Limits section

## v0.1

Initial release of Auger's spec-drilling loop and CLI surface: `init`, `start`, `bundle`, `ask`,
`answer`, `check`, `status`, `toggle`, `dump`, `propagate`, `feedback`, `recall`,
`serve`, and `verdict`. (`export` and `record` shipped in v0.2.)
