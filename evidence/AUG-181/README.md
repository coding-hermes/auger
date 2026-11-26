# AUG-181 — idempotent recall registration + per-project duplicate census

Root defect: `scripts/recall_battery.py register` appends a new memory row on every run
(DuckBrain has no upsert and no DELETE route for a memory row), so the `auger` namespace
stored every `/auger/AUG176-*/<BLOCK-ID>` key at least twice (measured 2026-11-17), and
duplicate rows consume the retrieval window.

## What changed

- `scripts/recall_battery.py`:
  - new `census` action (AC1): per-project `keys` / `rows` / `duplicate_keys` over the
    whole corpus prefix, derived ENTIRELY from paged read-backs
    (`GET /api/memories?namespace=auger&prefix=/auger/AUG176-<project>/&limit=500&offset=...`,
    walked via `hasMore`/`nextOffset`). Each count prints the read-back command that
    produced it — never asserted from a registration log.
  - idempotent register (AC2): before writing, the keys already stored under the project
    prefix are read back and skipped; only genuinely missing keys are POSTed. The run
    report prints `rows_written` (added), `skipped_existing`, `failed_rows`. A failed
    write is named and NOT counted as added.
  - new `read_back_rows()` pager (limit 500/page, bounded at 200 pages) and
    `read_back_command()` used by both halves; read failures are loud (`SystemExit`
    naming the substrate's own words), never an empty list.
- tests (`tests/test_spec_ingest.py`): second-run-writes-0 idempotence, partial-prefix
  skip, failed-write naming, census derivation from read-back rows.

## Evidence (this directory)

Every count below is reproducible with the read-back command printed inside each census
transcript; register counts come from the transcripted register runs.

| file | what it proves |
|---|---|
| `01-census-before.txt` | AC1 census BEFORE the re-runs: auger-docs 157 keys/314 rows/157 dup keys (x2), code-factory 80/160/80 (x2), coding-hermes-director 7490/8695/1059 (max x3). Run: `python3 scripts/recall_battery.py census` (2026-11-25). |
| `02-register-rerun.txt` | AC2: run 1 (post-fix) backfills 39 keys the corpora gained since the 2026-11-16 registration (auger-docs +17, code-factory +22, director +0); run 2 — the idempotence proof — writes **0** rows everywhere (`rows_written=0`, `skipped_existing=148/102/7490`). |
| `03-census-after.txt` | Read-back census after both runs: rows grew by exactly the 39 backfilled keys (331/182/8695); `duplicate_keys` UNCHANGED (157/80/1059) — no new duplicates were written by the re-runs. |

## AC4 — board evidence audit on this tree

```
python3 scripts/audit_board_evidence.py
complete_rows=276 bound=129 unresolvable=0 missing=0
none_reason=147 sum=bound+none_reason+unresolvable=276 complete_rows=276
rows=320 verdicts=204 earliest_artifact=2026-09-21
bound_failed_verdict=28 bound_legacy_citation=0 label_defects=0
reason=none:cites-foreign-verdict-<run>-task-<task>[+...] rows=6
reason=none:no-verdict-artifact-for-task-id rows=141
RESULT: OK
```

## Not done here, on purpose

- No deduplication of the EXISTING duplicate rows: there is no DELETE route for a memory
  row and the substrate write path is duckbrain-owned (AUG-180). This task's deliverable
  is the census + the idempotent write path; the standing duplicates (157/80/1059 keys)
  remain until the substrate-side action lands.
- Existing `PART` rows of the pre-fix era keep their keys; the idempotence guard keys on
  the stored key strings, so re-runs are safe regardless.
