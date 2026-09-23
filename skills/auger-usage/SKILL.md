---
name: auger-usage
description: How to USE auger (spec-drilling CLI backed by DuckBrain + JEV) — verbs, run commands, pitfalls, and the working substrate pin. Load this before touching auger or its namespace.
category: software-development
---

# auger — usage skill for agents

## What it is

`auger` drills a project's spec into a git-backed, DuckBrain-stored record of
DECISIONS: each decision keeps its rejected options and the reasons they lost,
scores confidence via JEV, and can render what the system would look like under
a different set of choices (`dump --config`). One file, `auger.py`, stdlib
only. Public repo: `coding-hermes/auger`.

## Entry points

- CLI: `python3 auger.py -n <namespace> <verb> ...` — 12 verbs: `init start
  ask answer check status toggle dump propagate feedback recall verdict`. The
  verb surface is a PUBLIC CONTRACT; renames are breaking.
- Storage: DuckBrain declared tables in namespace `<ns>` — 14 tables
  (project question decision option break escalation assumption unknown domain
  edge facet bundle bundle_member verdict). Files:
  `~/duckbrain/namespaces/<ns>/tables/`.
- Tests: `bash tests/smoke.sh` (15 assertions, ~21s, leaves a namespace
  behind on purpose) · `AUGER_CI=1 pytest` (68 tests, ~5.5 min).

## Environment you need

- DuckBrain HTTP on `127.0.0.1:3000` — **on the working branch
  `feat/native-s3` of wojons/duckbrain**; the public default branch has NO
  declared-tables API and auger's happy path 404s on it (AUG-016).
- Credentials: `DUCKBRAIN_API_KEY` env or `~/.duckbrain/foreman-status.token`;
  `DUCKBRAIN_URL` overrides the base URL; JEV needs an OpenRouter key in
  `~/.hermes/.env`.

## The right-way patterns

1. **Init first, always.** `init` is idempotent and REPAIRS drifted table
   declarations (rewrites files that differ from code's `COLS`). If a field
   "mysteriously" never appears in rows, run init, then re-check.
2. **`answer` records the WHOLE choice**: chosen option + every alternative +
   why-not. The options ARE the what-if engine; an answer with one option
   produces a dump with nothing to toggle.
3. **Never write namespace rows by editing JSONL files** — go through the
   HTTP table API (or the CLI). Direct edits desync the declared-table layer.
4. **`dump --config` is read-only by design.** To actually change the stored
   config: `toggle --on <option-id> --off <option-id>` (batch flips together —
   see pitfall 2).
5. **Scratch work goes in throwaway namespaces** (`auger-smoke-*` style), and
   you own the cleanup: `rm -rf ~/duckbrain/namespaces/<ns>` when done.

## Common pitfalls (each one bit a real run)

1. **Reading an error body as data.** `init` on a 429/401 body once "succeeded"
   with 0 tables. If `init` prints `declared: 0/13` or an odd count, STOP —
   the substrate is lying or unreachable; do not proceed to `start`.
2. **toggle has no exclusivity (AUG-015)**: turning a second option ON leaves
   the first ON, and `dump` then renders two bindings for one decision as THE
   configuration. Until fixed, always pair `--on X --off Y` in one call.
3. **JEV probe shape**: a probe missing the body's `model` field 400s on every
   key and looks like a total outage. Mirror `JEV_MODEL` from auger.py.
4. **ask/check take 5-7s**: that is the JEV network round trip. Fail-closed
   UNKNOWN output = JEV unavailable; report it, don't retry blindly.
5. **Fresh DuckBrain without auth**: start daemon plain (no `--auth=apikey`),
   set any non-empty `DUCKBRAIN_API_KEY`. With auth enabled, keys live in
   `~/.duckbrain/auth.json`.
6. **Shared-host pidfile clash**: `EACCES /tmp/duckbrain-http.pid` = another
   user's daemon; use a different port and `DUCKBRAIN_DATA_DIR`.

## Quick sanity check (30 seconds)

```bash
python3 auger.py -n <ns> status        # instant; proves API + token + tables
python3 auger.py -n <ns> recall "any seed phrase"   # proves embeddings
```

## Bundles and the graph (the 2026-09-23 surface)

- **One namespace per member, named exactly the member** (`mccli`, `mcview`).
  This convention is code-only (`member_namespace`, auger.py ~L865); a
  different pairing silently disables every cross-project walk.
- **Bundle rows have no verb.** Create them via the table API (POST
  `/api/ns/<ns>/tables/bundle` and `/tables/bundle_member`) — see
  docs/dogfood/2026-09-23-graph-bundles-integration.md for the working
  recipe. AUG-029 tracks the missing verbs.
- **`answer --scope bundle` may be silent.** If the sibling walk found
  nothing to flag, the verb prints nothing (AUG-027). Do not assume the pass
  did not run; check the edge table.
- **`propagate` is cheap and mostly a no-op on feedback-born questions**
  (feedback pre-gates them); `--recheck` forces real re-examination.
- **The moot cascade needs a hand-written LOCAL `breaks` edge** (no verb
  writes one, AUG-028). Shape: kind=breaks, src decision, dst decision,
  dst_project ABSENT — with dst_project set it is a sibling break and the
  local walk ignores it by design.
- **Verdict shapes:** `verdict --good|--bad [--judged-by NAME]
  [--confidence X]` (on-file), `--ask-jev` (JEV judges the rendered dump,
  fail-closed), `--config D-001=O2` (hypothesis; values accept option ids OR
  labels; requires --ask-jev), `--list`. Cost ~$0.0001/call.
