# Dogfood run 14 — the ask candidates surface (2026-10-09)

Angle: runs 1-13 never exercised the AUG-052 generation/judgement split in `ask`
(large model proposes a candidate batch, JEV gates and ranks, the governor stores a
bounded slice with `budget_thin` markers) — landed 2026-10-07, two days before this
run. Real use = a full drill session on a fresh isolated substrate, driven exactly as
the README's loop documents: init → start → answer → ask → answer → ask … then
check/status/dump/verdict/export.

Substrate: scratch DuckBrain (prebuilt checkout, not rebuilt — the run tests auger,
not the substrate build), port 3123, isolated data/ns dirs (`/tmp/df14-data`), isolated
auth store minted with `DUCKBRAIN_AUTH_FILE=/tmp/df14-auth.json` (keyHash-only store —
the printed token is the only copy). `auger` driven with `DUCKBRAIN_URL`,
`DUCKBRAIN_API_KEY`, `DUCKBRAIN_NAMESPACES_PATH` env only. JEV + proposer:
OpenRouter key from the ambient env (dogfood keys, untracked here).

## What a real session feels like (all numbers from the run)

| step | time | note |
|---|---|---|
| `auger init` (fresh ns) | 0.11s | declared 0/14, then 14/14 on second init (see friction F1) |
| `auger start --seed-file` | 3.1s | seed embedded; "embedded" honest on a healthy substrate |
| `auger answer D-001` | 19s | first-answer path, includes memory embed |
| `auger ask` #1 (legacy path, no decisions yet) | 26s | JEV picked from the fixed criteria set; stored Q-000001 |
| `auger ask` #2 (generated path, AUG-052 live) | 63s | 2 candidates proposed by deepseek-v3.2, JEV-gated, 2 asked |
| `auger ask` #3-#8 | 8-36s each | 3 asked + 1 budget-thin per run; `generation_cost` printed per run |
| `auger check` (answered question) | ~2s | "ALREADY ANSWERED (noul 0.87, threshold 0.55)" — correct |
| `auger check` (open question) | ~2s | "NOT YET ANSWERED (noul 0.3)" — correct |
| `auger dump` | 0.17-0.19s | current + `--config` hypothetical, both clean |
| `auger verdict --ask-jev` | ~1s | stored V-000001 good (noul 0.84); hypothetical arms bad |
| `auger export` | <1s | 2930 lines, byte-identical across two runs |
| `auger recall "…"` | ~1s warm | semantic tier labeled, top hits = the drilled decisions |

The generated path is genuinely better than the legacy criteria list: candidates were
specific to the seed ("What mechanism will be used to move notifications to the
dead-letter queue after 5 failed retries?") where the legacy path offers generic
subjects. Provenance printed per question (`proposed=… scored=… generation_cost=…`) is
exactly what an auditor wants. The `budget_thin` marker (Q asked over ceiling, stored
with reason, one escalation row) is honest shallow-branch record keeping.

## Findings (filed on the board)

- **AUG-094 (P1): ask's generated-candidate gate is blind to stored questions.**
  `cmd_ask` builds the JEV state from seed + decisions + COUNTS (`OPEN QUESTIONS: {len(unans)}`)
  — never the stored question texts — and `store_proposed_question` deliberately disables
  the legacy exact-text duplicate guard for generated candidates ("For generated candidates
  JEV is the duplicate gate"). But JEV never sees the stored texts, so nothing can catch a
  paraphrase. Measured: 7 ask runs over one session produced 4 near-duplicate pairs
  ≥0.40 content-word jaccard across 31 stored questions (worst 0.57); Q-000024 was asked,
  answered as D-005, and its substance re-asked as Q-000028 the very next run.
  Each surviving near-dup costs the gate + a full answer + an impact pass + a graph edge —
  the exact cost the engine's Q1 rationale says a duplicate question exists to avoid.
- **AUG-095 (P2): ask has no stop condition tied to progress.** 5 consecutive runs asked
  3 questions each while completeness sat at 1.5-2.1/4, re-drilling the thinnest decisions
  whose questions were already open. `auger feedback` documents the stop ("D-006 already
  drilled: Q-000035 is open and awaiting an answer"); ask is silent. A user drilling over
  days gets dozens of overlapping open questions and no "start answering" signal.
- **F1 (docs-level, not filed): `auger init` on a namespace whose tables were declared but
  whose first init hit the not-yet-visible window prints a confusing double warning**
  (declared 0/14 → "not visible to the API yet" → the legacy `~/duckbrain/namespaces`
  hint path). Re-running init fixes it (14/14) — but a fresh user cannot know that. This
  is the AUG-074 family (ns path resolution) showing residual rough edges; no new row
  filed, AUG-074's fix covers the substance.
- **Not reproducible on this box:** the no-key fail-closed path for the generator. The
  key fallback chain (`OPENROUTER_API_KEY`/`OR_API_KEY` env → every `sk-or-v1-` in
  `~/.hermes/.env`) means a scrubbed-env `ask` still finds keys on any Hermes host —
  the generator always ran. Documented, not filed: the fail-closed behavior is covered
  by the suite; only its live reachability is untestable here.

## Install leg

SKIPPED-install-bunker: `ssh bunker3` connect timeout at tick time (100.69.3.13:22
unreachable). The fresh-install path was re-proven by runs 2/3/5/11/12 (last: 2026-09-30,
agent f4ec72e8, install_seconds=81, smoke=ok) and this run's surface (the ask pipeline)
is install-independent. Per the skill, the explicit skip row stands in the dogfood log.

## Verdict

🟡 PROMISING-BUT-ROUGH — the AUG-052 split delivers a visibly better ask (specific,
provenance-stamped, honestly budgeted), and the whole loop held under a real session.
The blocker is that the new path regressed the duplicate discipline the old one had:
the gate is calibrated but blind. One fix (feed stored open questions into the state)
closes the gap.
