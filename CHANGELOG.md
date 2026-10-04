# Changelog

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
