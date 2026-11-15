# REVIEW-AUGER-006 — evidence map

Row: smoke teardown rmtrees the namespace before checking DELETE status — a 409 orphans
the registry row (AUG-117 recurrence). Branch `wt/REVIEW-AUGER-006`, base `d65cc3b`.

Commits: 1 `d91b983` (hermetic proof, red) → 2 `cdc12f4` (the fix) → 3 (gate arm +
evidence). Run the proof at any point with:

    PATH=$PWD/.venv/bin:$PATH python3 -m pytest tests/test_smoke_teardown.py -q

## AC1 — teardown treats 409 like 429: bounded backoff, re-enter, rmtree only on 200/404

Proven by `tests/test_smoke_teardown.py` driving the REAL `ns_helper` heredoc extracted
from `tests/smoke.sh` against a stub `auger` module (no live substrate):

- `test_teardown_settles_a_transient_409_and_removes_both_halves` — a scripted
  409-then-200 DELETE ends with BOTH halves removed and exactly 2 DELETEs (the
  bounded re-entry). Red on the unfixed tree: one DELETE, directory rmtree'd under
  the surviving row.
- `test_teardown_survives_a_persistent_409_with_the_directory_intact` — a persistent
  409 keeps the directory AND the row, with a bounded delete count (ladder length + 1).
  Red on the unfixed tree: the directory was destroyed while the DELETE was refused
  (the AUG-117 ghost-row shape, asserted `d.is_dir()` failed).
- FAILIF check: there is no code path where `shutil.rmtree` runs before `st` is
  consulted — the fix orders `status check → rmtree`; the red transcript is the
  mutation proof.

Transcripts: `01-red-base.txt` (4 failed / 3 passed, unfixed), `02-green-fixed.txt`
(7 passed, fixed).

## AC2 — a smoke run on a locked substrate (409) leaves zero rows and zero directories

Transcript: `03-live-locked-substrate.txt` — a full live `bash tests/smoke.sh
auger-smoke-r006-live` run (21 PASS / 0 FAIL), then quoted:

- `rows for auger-smoke-r006-live: 0` (filtered GET /api/namespaces)
- `0 directories for auger-smoke-r006-live under ~/duckbrain/namespaces`

Honesty note: this particular run's DELETE answered 200 on the first attempt (the
substrate was quiet by then); the 409 path itself was observed live three times during
this session (probes at 09:13Z and 10:22Z answered `409 Push in flight`, and the first
guard attempt's pytest leg collected seven 409s naming pid 4150279) — all before the
fix, when each 409'd teardown would have orphaned its row. The 409 ordering and the
bounded ladder are pinned by AC1's hermetic tests; the live run proves the end state
(zero rows, zero directories) that the fixed teardown delivers.

## AC3 — the sweep reaps the auger-pytest-* class under the existing protections

The code: `THROWAWAY` gains `"auger-pytest-"`; the reaping rules are unchanged
(prefix-gated, and only when the directory is ABSENT or older than 24h). Hermetic pins:
`test_sweep_reaps_a_stale_pytest_namespace`, `test_sweep_reaps_a_pytest_row_whose_directory_is_gone`,
`test_sweep_spares_a_fresh_pytest_directory`, `test_sweep_never_takes_a_foreign_prefix`,
`test_sweep_still_reaps_the_original_smoke_and_eval_classes`.

Transcript: `04-backlog-drain.txt`. Its postscript carries the attribution this report
owes the reader:

- The foreman's 41-row backlog (09:08Z) drained to 15 rows between 10:33Z and 10:37Z
  by an EXTERNAL actor (the s3-sync push window 10:22–10:35Z; DuckBrain's post-sync
  reconciliation dropped the ghost rows). This session's sweep deleted nothing in that
  window — so no 41→0 claim is made for the script.
- What one sweep run DID prove live, repeatably: the in-class stale fixture
  (`auger-pytest-r006e2e-old`, dir mtime 3 days) was reaped — `removed 1 stale
  throwaway(s)`, row and directory both gone — while the fresh in-class fixture, the
  foreign prefix (`auger-prod-r006e2e-guarded`, also 3 days old) and the 15 live
  sibling rows were all KEPT. Prefix gate and >24h gate both honored on the live
  substrate.
- The absent-directory branch did not trigger live: DuckBrain's indexer recreated the
  ghost fixture's directory before the sweep. That branch is pinned hermetically.
- The surviving fixtures were deleted by hand AFTER the sweep and disclosed in the
  transcript (own fixtures, not substrate backlog).

## Gate wiring

`tests/test_smoke_teardown.py` is named in tests/gate.sh's pytest arm (commit 3), and
`test_gate_arm_runs_this_file` pins the naming so the list cannot re-narrow silently.
