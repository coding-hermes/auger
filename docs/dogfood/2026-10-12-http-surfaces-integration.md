# Dogfood run 15 — auger's own HTTP surfaces (`serve` + `api`), 2026-10-12

## Promise (what a real user would do)

"An external agent with no shell can read a drilled spec over auger's own HTTP:
`auger serve` for semantic search, `auger api` for status/dump/recall/export/check."
No prior run (1-14) drove either surface; run 4 probed the *substrate's* raw HTTP, not auger's.

## Substrate

Scratch duckbrain daemon :3119 (data dir /tmp/df15-run/data, namespaces base
/tmp/df15-run/nsbase, throwaway auth file, one API key). Scratch copy of auger HEAD
(7928e6a) at /tmp/df15-run/app. Namespace `df15`, project P-20261012171133,
one drill: D-001 (domain 4.05) chosen "single declarative JSON snapshot" over
paginated endpoints + GraphQL, conf 0.81. Namespace torn down after the run.

## Timeline (real use, with numbers)

- init 14/14 declarations: 325ms (after the DUCKBRAIN_NAMESPACES_PATH lesson — see "Setup friction")
- start (seed stored + embedded): 4.3s
- ask (JEV): 13.5s, question Q-000001 stored, cost 3.3e-05
- answer D-001: 4.9s; dump 244ms
- `auger api` (:8779) + `auger serve` (:8778) both booted; /health reports tier semantic,
  lmstudio text-embedding-qwen3-embedding-0.6b (scratch daemon fell back from openrouter)
- serve search warm: 3.5-7.0s per call (n=5); root cause isolated — NOT auger's layer:
  - substrate /api/memories?namespace=df15&q=... : 3.6-5.0s (2 rows!)
  - same daemon bare q= (no namespace): 97-208ms
  - direct LM Studio embed round-trip: 1.07s
  - api/status warm 833-926ms, api/dump warm 430-472ms (same server, table reads)
  → filed AUG-096
- api surface behaves: status/dump/export/recall/verdicts JSON clean; dump?config=
  hypothesis renders the CONTRADICTIONS block quoting the recorded why-not; POST /check
  runs JEV (5.5s, verdict ALREADY ANSWERED decided-by score, cost 1.9e-05); unknown
  namespace 404; bad limit refused with message; PUT/PATCH/DELETE → 405; unknown route
  → 404. serve 404 carries the route list (self-describing). project= filter works
  (NONEXIST project → count 0, not an error).
- One honest wart: serve's search response keys results under `results` while api's
  /recall uses `hits` — a caller moving between the two surfaces renames its parser.

## Setup friction (lesson for future runs)

1. DUCKBRAIN_NAMESPACES_PATH is a DIRECTORY base, not a file path — pointing it at
   the json file made auger write declarations under ".../namespaces.json/df15/tables"
   while the daemon served /tmp/df15-run/nsbase → 0/14 init, rc=1, three retries.
2. /api/keys POST does not exist (ROUTE_NOT_FOUND) — seed keys only via --auth-file
   at boot.
3. The namespace registry census still lists the co-located production namespaces
   even with scratch data dir + namespaces base (AUG-090 re-proved; config isolation
   reduces but does not remove the misleading census).

## Install leg (fresh machine, bunker-mvp, agent 19eba3ec — PASSED, destroyed after)

Real fresh box (no node, no npm). Sequence + wall times:
1. pnpm user-prefix per README — blocked: no node on the box (README assumes node exists)
2. node v22.14.0 (current LTS): pnpm install FAILS engine gate (jsdom@30.1.1 wants
   ^22.22.2 || ^24.15.0 || >=26) → AUG-097
3. node v24.11.0: FAILS same gate
4. node v26.0.0: pnpm install + build OK (106s), daemon boots (degraded status, no
   embedding providers — expected on a bare box)
5. auger init: 14/14, rc=0
6. start → 500 GIT-IDENTITY-001 (no git author configured; README silent) → AUG-098
7. git identity set; start ok; answer --domain refuses (no domain grid on fresh box —
   README's documented escape hatch works: answer without --domain succeeded)
8. Full loop verified on the fresh box: start/status/ask/answer/dump/toggle/export
   all rc=0 (loop 2s once substrate stayed up); status honestly warned "0 of 2 options
   active — this decision contributes NOTHING" after my toggle-off.
9. Teardown: destroy refused once (live node/esbuild children — gate worked as
   designed), then destroyed cleanly.

Harness note (AUG-099): boot the substrate and run the whole loop inside ONE
bunker exec session — a setsid+nohup daemon from a previous exec session was dead
by the next call (connection refused, 3 retries across install legs 2/7/9); a boot
with sleep+health-check in the same session held. Environment friction, not an
auger defect. Also add a node install step to the README path (leg 1: box had no
node at all — README assumes it).

Install verdict: PASS with two doc gaps (AUG-097 node pin, AUG-098 git identity).
bunker-qa.sh full battery NOT run — this leg ran the README install path directly
(which is the dogfood contract); no auger code was modified anywhere.

## Verdict

🟡 PROMISING-BUT-ROUGH (HTTP faces — the untouched surface). Both servers are real,
honest, and well-shaped: tier labels everywhere, fail-closed errors, self-describing
404s, read-only serve, contradiction renderer over HTTP. The substrate's namespace-
scoped semantic query (3.6-5s for 2 rows) is the cost every agent-caller feels, and
the fresh-box path still has two undocumented walls.

## Findings → board

- AUG-096 (P2) namespace-scoped semantic query 3.6-5s vs 0.1s unscoped
- AUG-097 (P2) no Node version pin; LTS 22/24 both fail pnpm engine gates
- AUG-098 (P2) GIT-IDENTITY-001 blocks the fresh-box first write; --domain example
  on a gridless quickstart
- AUG-099 (P3) bunker exec sessions kill the daemon between calls (harness note)
