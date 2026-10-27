# Dogfood run 18 — the domain coverage map (2026-10-27)

**Angle (untouched by runs 1-17):** the 44-domain grid is auger's answer to a real user's
question — *"what have I not decided yet?"* Runs 1-17 exercised the loop, the graph, bundles,
registers, HTTP surfaces, the released artifact, failure semantics and scale; none of them ever
seeded the grid, filed a decision under a domain, and read the coverage map back. This run did
exactly that, in a real drill, on a scratch substrate.

**Verdict:** 🟡 PROMISING-BUT-ROUGH — the coverage map is the best *reporting* surface in the
product (44 rows, per-domain triage/floor/terminating-ring, honest ABSENT vs NOT-REACHED
vocabulary, 0.1 s), and the write that keeps it honest is broken for 35 of the 44 domains, so
the map's own status column lies about the domains a user has answered (AUG-059-R2).

**Tick:** `auger-dogfood-2026-10-27-15-09-28` · **product legs at** `a9c979b`
(`git diff a9c979b..HEAD -- auger.py` is empty, so every number below holds at the HEAD the
checkout moved to during the tick, `39b11d6`) · **install leg at** `39b11d6`.

## Substrate and isolation (footprint)

| what | value |
|---|---|
| DuckBrain substrate | scratch clone `/home/kara/duckbrain` (clean), daemon on **:3918** |
| data / namespaces / auth | `run18/dd/` (throwaway `DUCKBRAIN_DATA_DIR`, `DUCKBRAIN_NAMESPACES_PATH`, single-apiKey `auth.json`) |
| identity proof | `dd/duckbrain-http-3918.pid` == the listening pid (`ss -tlnp`) at every measurement |
| embeddings | `qwen/qwen3-embedding-8b` via OpenRouter, `health.embedding.healthy=true` |
| grid | `~/.hermes/skills/software-development/spec-decomposition-matrix-tradeoff/references/dogfood-artifact/02-domains` (44 files, read never copied) |
| auger invocation | `python3 /home/kara/auger/auger.py` (system python 3.11.15), namespace `df18` / `df18g` |
| nothing touched | the live substrate on :3000, the checkout's tracked files, repo visibility/permissions |

The scratch substrate's default rate limiter is part of the rig and it bit this run — see
*Discarded measurements* below.

## The promise under test

> *"A user can seed the method's 44-domain grid, file each decision under a domain, and read a
> coverage map that says exactly which domains are decided and which are still untouched — and
> the map keeps up when the user changes their mind."*

## What the user actually did (legs)

| leg | what was driven | result |
|---|---|---|
| **M** | `init` (no seeds) → `start` (real seed) → `answer --domain 4.05` → `status` → `init --seed-domains` → `answer --domain 4.03` → `status` → off-grid `--domain 9.99` | the advance fires for 4.05 and NOT for 4.03; `status` prints the AUG-059 contradiction |
| **G** | the README's blessed gridless path: `init` (no `--seed-domains`) → `start` → `answer --domain 4.05` → `status`/`dump` | answer stored with `domain=4.05` (substrate read); `status` says **answered 0** and lists 4.05 ABSENT; `dump` prints `## D-001 (4.05)` |
| **C** | supersede inside a covered domain; a SECOND project in one namespace; the `--domain` form matrix | supersede supersedes; implicit project selection refuses honestly; 4 of 6 `--domain` forms refuse (see AUG-115) |
| **D** | grid-shape refusals (missing dir, 43 files, 45 files, mis-slugged file); re-seeding with two projects; write-path timing | all four refusals loud and precise, 0 rows written; re-seed is idempotent per project |
| **install** | `bunker-qa.sh launch /home/kara/auger` on a fresh ephemeral agent (bunker-mvp, agent `fbb49307`, destroyed after harvest) | fresh-install OK, native suite PASS, `upgrade` FAIL, `ci-act` UNVERIFIED |

Raw transcripts: `~/.hermes/stand-in/dogfood/auger/run18/logs/{legB,legC,legD}.log`,
`g3_status.out`, `c2_status.out`, `f1/f2.time`, plus the generated scripts in the same dir.

## Findings (filed as rows; `acceptance_criteria` + the `acceptance` alias mirrored)

| row | pri | finding |
|---|---|---|
| **AUG-059-R2** | P1 | recurrence of AUG-059. `answer --domain` prints `domain 4.05 -> REACHED (seed status retired)` for an integer-ring domain and NOTHING for a null-ring one: the substrate refuses the PATCH (`422 Column 'terminating_ring' (integer) requires an integer number, got null`) because auger declares that column `integer` (auger.py:236) while 35 of 44 grid headers say `terminating_ring: null`. The failure is skipped by design, so the user is never told and `status` keeps printing `answered … (row status NOT-REACHED)`. |
| **AUG-112** | P2 | the coverage map is blind to evidence when the domain row is absent: on the gridless path the README blesses, `status` prints `answered 0` / `44 absent` while the decision table holds `{D-001, domain: 4.05}`. The code's own docstring defines `answered` as evidence; the filter at auger.py:4053-4056 additionally demands a row. |
| **AUG-113** | P3 | stale coaching in `skills/auger-usage/SKILL.md`: it says `--domain` is unvalidated free text (AUG-038), that `status`'s display is one-index-off, and that the `(row status NOT-REACHED)` parenthetical is a display quirk to be ignored — all three refuted on HEAD. Corrected in place by this run; the row is the guard that keeps prose in step with behaviour. |
| **AUG-114** | P4 | `status` says `NOT-REACHED 42 (no such row)` about 42 rows that exist — the line below it says the opposite, and the counter (`seeded - answered`) can never make the parenthetical true. |
| **AUG-115** | P4 | the `--domain` refusal names an example value it refuses: `(e.g. 4.05=data)` → rc=1 with the same message. The two tokens a reader has in front of them (`4.05-data` from the grid filenames, `4.05=data` from the hint) both refuse. |
| **QA-AUGER-11-R3** | P2 | install leg: the battery's `upgrade` cell still probes PyPI (`pip install auger==0.2.0` → `No matching distribution found`) although the repo already ships the documented offline verifier (`scripts/upgrade-verify.sh`, `tests/test_upgrade_path.py`, `docs/RELEASE.md` — all from d6ba77e, the fix that closed QA-AUGER-11-R2). Recurrence, minted with `--recur`. |
| **AUG-116** | P2 | install leg: the `ci-act` cell fails (`tests/gate.sh` under act, 38.4 s, live arms skipped) and the collected evidence carries only the step annotation, so the red is unattributable; the per-cell act log was not harvested before the agent was destroyed. Hosted CI is green at the same sha. |

## What held up (worth keeping)

- **`init --seed-domains` on the default grid path just works**: 44 rows `DM-000001…DM-000044`
  in 0.12 s, names/triage/floor/terminating-ring read from the grid files, source path printed,
  `0 already present` on the second run (idempotent per project) — and it binds the slice to the
  newest project, which is how a second project in one namespace gets its own map.
- **The grid is validated as a shape, loudly**: a missing dir, 43 files, 45 files and a
  mis-slugged `4.05data.md` each refuse rc=1 naming exactly what is wrong
  (`missing ['4.44']`, `unexpected ['4.45']`, `4.05data.md is not named 4.NN-<slug>.md`), with
  **0 domain rows written** and the refusal text matching the README's quoted message.
- **Implicit project selection refuses honestly** once a namespace has two projects
  (`refused: namespace 'df18' has multiple projects; implicit selection would choose newest
  project P-…19 ('second-drill'). Pass -p/--project-id`), and `-p` then scopes the report.
- **Read surfaces are deterministic**: `export` byte-identical across runs (cmp) on a namespace
  with 2 projects / 88 domain rows / 20+ decisions, and `dump` names the domain on each decision.
- **Durability**: the scratch daemon died mid-tick (`DuckBrain MCP server stopped`), was
  restarted, and every row was still there (88 domain rows grouped `{P1: 44, P2: 44}`,
  11 decisions) — AUG-100-era read-back was not re-broken.
- **Errors stay fail-closed**: a bad `--domain` prints `nothing was stored` and stores nothing
  (verified by reading the decision table after each refusal).

## Negative results (not filed — recorded so the next run does not re-chase them)

- **`init --seed-domains` really is idempotent.** A third run with two projects answered
  `44 domains -> 0 row(s) written, 44 already present`; `dup_within_project = 0`. The help's
  `(idempotent)` is accurate per project — my earlier draft finding was wrong and was dropped.
- **All four grid-shape arms refused before writing** (no partial seed, no orphan namespace).
- **`NOT-REACHED N (no such row)` is the only false gloss** — the per-domain lines themselves are
  correct (state word, triage, floor, terminating ring, `(not opened)` on null rings).

## Performance (Step 2b) — measured, and one measurement block DISCARDED

All numbers: `/usr/bin/time -f`, scratch substrate :3918, idle between runs, 3 samples.
**Write verbs (the rate limit raised to 1,000,000 — see the A/B below):**

| verb | wall | user+sys |
|---|---|---|
| `answer` (plain, `--domain 4.09`) | 0.18 / 0.18 / 0.20 s | 0.09 s |
| `answer --supersedes` | 0.22 / 0.22 / 0.23 s | 0.08 s |
| `answer --invalidates` | 0.20 / 0.20 / 0.21 s | 0.08 s |

**Reads:** `status` 0.09 s (2 projects) / 0.17 s (gridless), `dump` 0.10 s, `export` 0.14–0.18 s,
`init` 0.13 s, `init --seed-domains` 0.12 s, `start` 0.11 s. First `answer` after a cold substrate
paid 1.19 s (embed + the advance PATCH ≈ 0.9 s).

**Discarded block (the honest part):** an earlier burst measured `export` 4.32 s / 8.17 s and
`answer --supersedes`/`--invalidates` 5.3–10.3 s. Those numbers are **my probe rig**, not the
product: the scratch daemon's **default rate limiter** (100 requests/window) answered
`{"event":"rate_limit_refused","limit":100,"retryAfter":1}` and auger's transport honours
`Retry-After`, so a burst of ~20 CLI calls in a few seconds measured backoff, not work.
**A/B that settles it:** same verbs, same rows, same binary, daemon restarted with
`--rate-limit 1000000` → 0 rate-limit events, every arm 0.18–0.23 s. Both arms and the log line
are in `run18/logs/` (`daemon.log`, `daemon-nolimit.log`).

**Verdict: no PERF row.** Nothing here is slow enough for a user to feel (0.1–0.25 s per verb on
a warm substrate); the one real wait a *user* pays is the remote embed on a cold first write, and
that is the documented model call, already covered by the open/complete AUG-110 family.

## Install leg (fresh machine, never a silent pass)

`bunker-qa.sh launch /home/kara/auger` → agent **fbb49307** on **bunker-mvp** (tailscale),
TTL 4h, launched 15:28Z at `39b11d6`, battery detached; `collect` pulled **22 evidence rows** and
destroyed the agent. Cells:

| cell | status | detail |
|---|---|---|
| repo-head | OK | tested HEAD `39b11d6a…` (clone dirty — pull skipped) |
| toolchain-bootstrap | OK | go 1.27.3, python venv+pytest, gcc/g++, make, jq 1.8.2, node 22.15.0, compose+buildx |
| fresh-install | OK | build-only install rc=0 |
| ci-pass | OK | **native suite PASS** |
| ci-act | **UNVERIFIED** | `tests/gate.sh` under act failed rc=1 after 38.4 s → AUG-116 |
| upgrade | **FAIL** | `pip install auger==0.2.0` → `No matching distribution found` → QA-AUGER-11-R3 |
| chaos-resource | PASS | suite survives a 3 GiB cap |
| chaos-disconnect | OK | fails fast rc=1 on a cut network |
| chaos-errorpath | OK | clean error on a missing config (rc=1) |
| docker-deploy / ui-probe / chaos-shutdown / chaos-corruption | N/A | no compose file, no web UI, no db state files |

So: **installability is proven on a clean box** (install + the whole native suite), and the two
red/unknown cells are filed rather than smoothed over.

## Leftovers and cleanup queue

- scratch substrate still running: `ss -tlnp | grep :3918` → kill the pid in
  `run18/dd/duckbrain-http-3918.pid`; the whole `run18/dd/` tree is throwaway.
- scratch grids `/tmp/df18-grid43`, `/tmp/df18-grid45`, `/tmp/df18-gridbad` — safe to remove.
- namespaces `df18`, `df18g`, `df18c…df18f` live only inside the throwaway data dir.
- the install agent was destroyed by `collect` (no bunker residue; `bunker list` shows only
  other lanes' agents).
- evidence kept: `run18/logs/*` (transcripts, generator scripts, timings), the battery evidence
  file `/tmp/bunker-qa-evidence-20261027T152741Z-4053372.jsonl`.

## Reproduce the headline finding in five commands

```bash
# scratch substrate (throwaway dirs), then:
python3 auger.py -n df18 init --seed-domains
python3 auger.py -n df18 start --name drill --seed-file seed.txt
python3 auger.py -n df18 answer --id D-001 --chosen x --option x --option y --why-not y --domain 4.05
python3 auger.py -n df18 answer --id D-002 --chosen x --option x --option y --why-not y --domain 4.03
python3 auger.py -n df18 status   # 4.05 advanced; 4.03 shows "(row status NOT-REACHED)"
curl -X PATCH -H "x-api-key: $DUCKBRAIN_API_KEY" -H 'content-type: application/json' \
  -d '{"status":"REACHED"}' "$DUCKBRAIN_URL/api/ns/df18/tables/domain?pk=eq.DM-000003"  # 422
```
