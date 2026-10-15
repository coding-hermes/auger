# Dogfood run 16 — 2026-10-15 — re-proof of the closed read-surface fixes on current HEAD

Angle: runs 7-10 filed AUG-070/077/078/083/100/101 and foremen closed them, but no run re-drove
the full mind-change + invalidation chain through real use after the fixes landed. This run
armed a fresh drill on a scratch substrate and drove: answer → toggle → current-dump →
supersede → recall → invalidates → propagate → export, plus the confidence band probe and a
fresh-box install leg on bunker-mvp.

Substrate: local DuckBrain (kara/duckbrain HEAD) on :3123, DUCKBRAIN_DATA_DIR=/tmp/df16-data,
throwaway namespace df16, token via foreman-status fallback. auger HEAD 9c097c8, v0.2.

## Re-proofs (all on HEAD 9c097c8)

- AUG-077 (contradictions in current mode) HOLDS: after `toggle --on D-001-O2` with
  D-001 chosen=embed-cache, current-mode dump prints "CONTRADICTIONS WITH THE RECORD"
  quoting the recorded why-not (grep -c CONTRADICT = 2 lines). Fixed.
- AUG-078 (superseded recall parity) HOLDS: after `answer --supersedes D-001`,
  recall annotates the old row "[superseded by D-004]" and dump marks
  "[SUPERSEDED by D-004]"; successor ranks above (0.484+ vs 0.500→0.492 after restart —
  annotation present either way; scores no longer equal at the top with the old row unmarked).
- AUG-100 (invalidation invisible in reads) PARTIALLY HOLDS/FIXED: dump prints
  "[INVALIDATED by D-005 (E-000003)]"; export still lists D-002 status "decided" —
  the header line carries the invalidation but the structured status field does not
  change. Same class as the original row; recorded as a residual, see AUG-103 below? no —
  folded into run notes; the visible read surface is honest, the machine field lags.
- AUG-083 (out-of-range confidence) HOLDS: -0.5 and 1.5 both refused rc=1, nothing stored.
- AUG-069/070 era duplicate-protection: duplicate --id refused rc=1 honestly.
- AUG-095 (already-drilled stop) HOLDS: gate refused re-ask of answered subject (noul 0.74)
  and later stored questions carried proposer/cost stamps; ceiling 3 enforced loudly.
- Durability: kill + restart of the substrate lost nothing; namespace, decisions,
  superseded annotations, retrieval tier all read back identical; export byte-identical
  across two runs (1377 lines).
- AUG-092 (tier labeling) HOLDS: every write and recall named its tier; the fresh box
  (embed provider degraded) printed "keyword-searchable only (embedding.healthy=false)"
  on seed and answer rows instead of claiming "embedded".

## New findings

1. (P1, AUG-102) Auto-mint poisons itself on any non-numeric decision id. A user answering
   with an explicit descriptive id (D-CACHE-EMBED) — or any id where the part after the last
   dash is not digits — becomes the lexically-highest decision row; next_id()'s
   `order=id.desc&limit=1` read then returns that row, the `-(\d+)$` regex fails, and the
   fallback mints D-001, which exists. The AUG-070 re-mint loop re-derives the same D-001
   three times and refuses with a FALSE "concurrent writers" claim. Reproduced on df16:
   after D-BAND-1.0 landed, every no-flag `answer` refused; deleting the poison rows
   restored minting (D-007). The refusal's remedy text is wrong too — no other writer
   exists. Fix direction: filter the highest-id read to the `^D-\d+$` shape (the id law the
   docstring itself declares), and reword the exhausted-retry error to name a malformed
   stored id as the likely cause. auger.py:5249-5261 (retry loop), :832-867 (next_id),
   :869-882 (decision_id_width).
2. (P2, AUG-103) Fresh-box quickstart assumes node exists. The README's Substrate pin
   documents the pnpm user-prefix route (correct, verified: pnpm 12.11.2 in 5s) but the
   bunker-mvp fresh agent had NO node at all — "node: command not found" is the real first
   wall for a bare Debian user, and the README never says how to get Node >=22.22.2 itself
   (no apt guidance, no tarball recipe). This run hand-installed node-v22.22.2 tarball to
   $HOME. Fix direction: a "get Node" step ahead of the pnpm step, or a curl-tarball
   one-liner like the pnpm one.
3. (P2, AUG-104) `duckbrain token` does not reach a running daemon: the daemon started at
   20:02, the token was minted at 20:05, and every request with the freshly-minted admin
   key 401'd until the daemon was killed and restarted. A fresh user following the
   quickstart order (start daemon → run auger init → 401 → mint token → still 401) has no
   hint that a restart is needed; the 401 says "Invalid API key" which reads as a typo, not
   a reload gap. Fix direction: hot-reload the auth store on the daemon side, or document
   "mint the token BEFORE starting the daemon" in the substrate pin.
4. (PERF, AUG-105) `ask` is the one user-felt wait: warm 17.4s, 29.0s, 19.7s across three
   invocations on a 7-decision namespace (fresh-process, embed cached server-side;
   /usr/bin/time: 0.13s user / 0.03s sys = 0% CPU, pure remote-model wait — JEV proposer
   deepseek-v3.2 + scorer). Everything else is comfortable: dump 0.43-0.58s, export 1.05s,
   status 1.08s, recall 5.56s warm. A user running ask repeatedly in a session feels every
   one of those 20s. Not profiled further: the time is remote inference, not local compute;
   the fix direction is concurrency or caching in the proposer/score pipeline, which only
   the foreman can judge.

## Install leg (bunker-mvp, agent df16inst, destroyed)

- 3 of 4 bunker hosts down (bunker3/2/4 ssh timeout); bunker-mvp up, bunkerd active.
- clone 2s; node tarball + pnpm + duckbrain clone + pnpm install + build: 30s total.
- daemon boot: ok (degraded embedding, expected on a bare box).
- fresh-user walls hit in order: no node (AUG-103), token mint → 401 until restart
  (AUG-104), then the loop ran green: init 14/14, start, answer, dump — LOOP_SECONDS=2.
- smoke=pass. Agent destroyed (2.0GB home archived), no residue at 90s recheck.
