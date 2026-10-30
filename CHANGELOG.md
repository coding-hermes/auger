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
