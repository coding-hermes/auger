# Dogfood run 9 — the mind-change surface (2026-09-25)

**Angle:** runs 1–8 covered the entry loop, write-seam, HTTP layer, real drill,
regression, scale/concurrency, and failure semantics. Never judged was the flow the
README leads with: *"it can render what the system looks like under a different set of
choices"* — the counterfactual — and the change-of-mind path after a decision is
stored: toggle → what does the spec show → supersede → recall. This run is that user.

**Setup:** scratch substrate on :3122 (`--auth-file` throwaway store, single apikey,
embedding env mirrored from the production daemon — same model, key not printed), data
dir `/tmp/df9-data`, DEFAULT namespaces path so no AUG-074 declaration drift. Namespace
`df9`, project `P-20260925172404`, a real decision about where question "meaning"
should live (phrasing vs a concepts table). JEV key via `OPENROUTER_API_KEY`. Control
substrate :3000 never touched.

## What works (measured)

| step | verb | result | time |
|---|---|---|---|
| declare | init | 14/14 tables, correct tally printed | 0.63s |
| arm | start | seed stored + embedded | 0.15s |
| propose | ask | Q-000001 proposed, gated (already-answered 0.07), stored | 7.1s, $0.000028 |
| decide | answer | D-001, both options + why-not on file, embedded | 1.6s |
| **counterfactual** | `dump --config D-001=<other>` | **hypothetical render: checkbox flipped, "nothing written" stated, and it names the contradiction against the record quoting the recorded why-not — unprompted** | 0.30s |
| mind-change | toggle --on D-001-O2 | honest sibling auto-off with explanation printed | 0.19–2.3s (σ high) |
| gate walk | propagate --no-gate / --recheck | clean, correct counts, 0.6s | |
| judge | verdict --ask-jev | V-000001 recorded on the changed config | 1.6s, $0.000023 |
| re-ask guard | ask (round 2) | JEV noul 0.66 → refused to store the same question again | 5.7s |
| evidence check | check | ALREADY ANSWERED 0.93, retrieval 1.000 on D-001's memory row | 2.9s |
| formal reversal | answer --supersedes D-001 | D-002 recorded, edge E-000003, D-001 → superseded, facets refreshed | 4.4s |

The flagship counterfactual is genuinely good — hypothetical mode is the strongest
single output in the product: it shows the flipped world AND argues with itself using
the recorded why-not.

## Finding 1 — the contradiction detector exists only in hypothetical mode (AUG-077, P1)

After `toggle --on D-001-O2`, `dump` in CURRENT mode renders chosen-label (O1) and
active-checkbox (O2) — a two-valued configuration — with no warning. The exact
"renders the collision as a real config" class AUG-070 fixed for duplicate ids, now
reachable through the legitimate mind-change path (toggle even prints what it did, so
the tool KNOWS the state diverged from the recorded choice). Same state rendered as a
hypothesis gets the full `CONTRADICTIONS WITH THE RECORD` block. `dump | grep -ci
CONTRADICT`: 0 in current mode, 1 in hypothetical, same underlying rows.

## Finding 2 — recall tells the old story with equal authority (AUG-078, P1)

After the formal supersede (status flipped, edge recorded, facets refreshed — the
mechanism works), `recall` ranks the superseded D-001 at 0.992 EQUAL with its successor
D-002, both snippets reading "we chose X", no marker. `check` is unaffected (it
correctly gated the re-ask off D-001's memory row). The supersedes relation is in the
edge table at recall time; ranking it (or annotating "[superseded by D-002]") needs no
new data.

## Install leg — partial, box died mid-build (AUG-079)

Ephemeral agent 2576331c on las-bunker-03: fresh clone of the public repo SUCCEEDED
(fresh-user fetch proven), README quickstart read on the box and complete on paper
(pnpm user-prefix, DUCKBRAIN_DATA_DIR mkdir advice, seed-file loop, OR key
requirement). Mid-build the BOX went offline (tailscale offline, no ping, bunker exec
i/o timeout); pnpm/build/boot/loop legs lost. Failover spawn on las-02 refused
(bunkerd down). Explicit SKIPPED row filed; install path itself was last proven
end-to-end by runs 2/3/5 (agent d1a530b6, 2026-09-24).

## Performance (real workload, this run's operations)

- `dump` cold: 307ms ± 17ms (hyperfine, 10 runs) — comfortable.
- `status`: **8.6s ± 3.5s** (range 1.0–10.9s) on a 2-decision namespace — 28× dump,
  confirming AUG-047 with fresh numbers (board says 2.2s vs 0.19s on 4 decisions).
- `toggle` round-trip (on O1 ↔ on O2): 1.17s ± 0.64s — the what-if switch occasionally
  costs seconds; small sample, watching not filing.

## Verdict

🟡 PROMISING-BUT-ROUGH (mind-change surface). The decide → counterfactual → judge
spine is excellent and fast; both failure modes are at the edges of the flow it
exists for: the current-mode render lies silently after a toggle, and retrieval gives
the reversed decision's ghost equal voice. Nothing hit blocked real use; both fixes
have data already in the store.

Filed: AUG-077 (P1), AUG-078 (P1), AUG-079 (P2, explicit skipped-install).
Evidence: this doc; rows in `.coding-hermes/board/tasks.jsonl`; log entry in
`.coding-hermes/dogfood-log.md`; scratch substrate + namespace destroyed after the run.
