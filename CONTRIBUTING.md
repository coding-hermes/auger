# Contributing

Auger is fleet tooling: the CLI that drills a project into a git-backed spec with its choices,
the reasons the alternatives lost, and a what-if toggle.

## Before you open a PR

- **The verbs are the API.** `init`, `start`, `ask`, `answer`, `check`, `status`, `toggle`,
  `dump` are what others build on. Changing a verb's name or its output shape is a breaking
  change — say so explicitly in the PR.
- **Run the smoke test.** `bash tests/smoke.sh` runs the whole loop against a throwaway
  DuckBrain namespace with real assertions and exits non-zero on any failure. Paste the tail.
- **It is stdlib-only on purpose.** No dependencies. If a PR needs one, explain why the
  standard library cannot do it.
- **Never build a request body by string interpolation.** An apostrophe in a JSON value inside
  a quoted shell body silently truncates it. Payloads go to the HTTP layer as bytes from a file
  or from memory.
- **A check that has never failed has not been tested.** New validation comes with a case that
  proves it fails on the thing it is meant to catch, not only that it passes on good input.

## Reporting

Include the namespace, the verb, the exact command, and the observed versus expected output.
If a stored row looks wrong, paste the row — the JSONL on disk is the ground truth.
