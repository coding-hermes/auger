# AUG-156 — board closure evidence is machine-resolvable (goal auger-xml-hardening, AC A12)

Row: `AUG-156`. Branch `wt/auger-xml-hardening`, base `eb22795`.

## The claim that was false

Goal-level acceptance **A12** says every closed board row carries a resolvable evidence run id
(`.gitreins/history/<date>/<run-id>/verdict.json`) and **no fabricated ids, ever**.

Measured on the pre-fix board (`git show HEAD:.coding-hermes/board/tasks.jsonl`, 201 rows with
`status=complete`, 91 verdict artifacts):

| measure | pre-fix |
| --- | --- |
| top-level `evidence_run_id` field | **0 of 201** |
| cite a verdict whose `task_id` equals the row id | 35 |
| cite a verdict belonging to a DIFFERENT task | 6 |
| cite no verdict at all | 160 |
| self-declared `run:` labels resolving to no artifact | 8 (in `detail.evidence[]`, e.g. `run:local-auger-RELEASE-018-2026-10-30`) |

`scripts/audit_board_evidence.py` reproduces exactly those counters pre-fix
(`complete_rows=201 bound=35 unresolvable=6 missing=160`, exit 1 — `01-prefix-falsifier.txt`) and
exits 0 on the fixed board (`complete_rows=201 bound=61 unresolvable=0 missing=0`,
`none_reason=140`, exit 0 — `02-postfix-audit.txt`).

## What the fix is

1. `scripts/audit_board_evidence.py` (new, stdlib only) decides exactly one outcome per complete
   row — `bound` / `none_reason` / `unresolvable` / `missing` — and prints
   `complete_rows=N bound=N unresolvable=N missing=N` plus
   `none_reason=N sum=bound+none_reason+unresolvable=N complete_rows=N`. Exit 0 only when
   `missing==0 and unresolvable==0` and no complete row carries an unresolvable
   `detail.evidence[].run_id` label.
   - A binding resolves to a real `verdict.json` **whose `task_id` EQUALS the row id**. A verdict
     for another task is `unresolvable: foreign-task`, never evidence.
   - `none:<reason>` is a **closed, re-verified vocabulary**. `check_none_reason` re-checks the
     reason's predicate against the repository on every run, so an annotation cannot be invented to
     turn the audit green: `none:no-verdict-artifact-for-task-id` is refused when an artifact names
     the row, `none:pre-gitreins-history-window` is refused when the row does not predate the
     artifact window, and `none:cites-foreign-verdict-<run>-task-<task>[+…]` must name **every**
     foreign verdict the row cites and no others. An unknown reason is refused.
   - Legacy rows without the field are still measured by the run ids their own text cites — that is
     what makes the pre-fix board measurable at all (the 35/6/160 falsifier).
2. `tests/test_board_evidence_audit.py` (new, 17 tests, named in `tests/gate.sh`'s pytest arm) pins
   the falsifier shape, every refusal, and the live invariant that the **tracked** board audits
   clean — re-resolving every binding by hand instead of trusting the audit's own verdict.
3. The board: a top-level `evidence_run_id` on every one of the 201 complete rows
   (`evidence/auger-AUG-156/apply-backfill.py`, one-off writer, imports the audit so writer and
   auditor cannot disagree).
4. 6 judge artifacts for `AUG-114` (2026-10-30/31) existed only as **untracked** files in the
   canonical checkout. They are adopted byte-identically (`diff -r` clean) so their run ids resolve
   in-tree; without them `AUG-114` could only be annotated, and that annotation would become false
   the moment the canonical checkout committed them to `main`.

## The 201 rows, by outcome

- **61 bound** to a `verdict.json` whose `task_id` is the row id.
  - 35 of them were already cited in the row's prose (`bound_legacy_citation` pre-fix = 35).
  - **26 promoted**: a real artifact existed for the task id and the row never cited it.
  - Deterministic picker (`pick_run`): a passing verdict wins, then the newest date, then the run id.
- **140 annotated `none:<reason>`** (no artifact anywhere under `.gitreins/history` names the row):
  - **135** `none:no-verdict-artifact-for-task-id` — the fact: 0 of them predate the artifact window
    (earliest artifact 2026-09-21); 103 were closed in 2026-10, 34 in 2026-09, 3 carry no date.
    93 of the 140 DO carry a `commit_hash`, but no verdict names their task id, and only **1**
    (`AUG-058`) has a commit that some verdict judged under another task id — so the honest reading
    is "closed by a foreman/PM verification tick with no gitreins lifecycle run", not "judged and
    lost".
  - **5** `none:cites-foreign-verdict-…` — the AC's 6 foreign rows, corrected:

| row | cited pre-fix | corrected to |
| --- | --- | --- |
| `AUG-037` | `54b3e377` → AUG-036 | `none:cites-foreign-verdict-54b3e377-task-AUG-036` |
| `AUG-040` | `047fdeed` → AUG-004, `1987bcbb` → AUG-031 | `none:cites-foreign-verdict-047fdeed-task-AUG-004+1987bcbb-task-AUG-031` |
| `RELEASE-001` | `189ee436` → AUG-069, `3e1fcc1e` → AUG-068 | `none:cites-foreign-verdict-189ee436-task-AUG-069+3e1fcc1e-task-AUG-068` |
| `AUG-049` | `fb2bff77` → AUG-035 | **bound to its own verdict `114f5250`** (existed all along, uncited) |
| `AUG-076` | `b9eafc22` → DOC-002 | `none:cites-foreign-verdict-b9eafc22-task-DOC-002` |
| `AUG-088` | `54b3e377` → AUG-036, `8a5da4f4` → AUG-083-VERDICT-BACKFILL | `none:cites-foreign-verdict-54b3e377-task-AUG-036+8a5da4f4-task-AUG-083-VERDICT-BACKFILL` |

  `AUG-049` is the one row where "corrected" means bound rather than annotated: its own verdict
  artifact existed and passes, so the foreign citation was a mis-citation, not an absence.
- **0 unresolvable**, **0 missing**.

## The fabricated labels (AC2)

11 `detail.evidence[].run_id` values on 9 rows resolved to no verdict file. None is deleted — each
is demoted to `removed_label` with `removed_label_kind` (`self-declared-run-label` for `run:…`,
`session-id` for a session name) so the correction stays auditable:

- 8 self-declared run labels: `AUG-074-R2`, `AUG-106-R2`, `QA-AUGER-11-R2` ×2, `QA-AUGER-12-R2` ×2,
  `QA-AUGER-15`, `RELEASE-018` (`run:local-auger-RELEASE-018-2026-10-30`).
- 3 session ids: `AUG-117`, `REVIEW-AUGER-005`, `QA-AUGER-2`.

`RELEASE-018` (the row AC2 names) is now
`evidence_run_id = "none:no-verdict-artifact-for-task-id"` — no verdict anywhere names
`RELEASE-018`, so no run id can honestly be cited — and its fabricated label is preserved as
`removed_label` with `run_id: null`.
`QA-AUGER-2` is the one label row that binds: its session-id label was replaced by its real verdict
run `82fc96f2` in the SG-126 position boardctl's own `--evidence-run-id` writes.

## Board edit is surgical (AC5)

- `git diff --numstat .coding-hermes/board/tasks.jsonl` → `201 201` (one line per touched row, no
  added/removed lines).
- `wc -l` → 250 before and after: **49 lines byte-identical**, 201 rows touched, all of them
  `status=complete`.
- Per-row guard in the writer: only `evidence_run_id` and `detail.evidence[]` label demotion may
  move; key order preserved; each row re-serialized in the style its own line already used
  (`"> ": "` spacing vs compact, ASCII-escape flag taken from the original line); re-serialization
  verified to round-trip; refusal (exit 2) otherwise.
- Independent verifier (`evidence/auger-AUG-156/verify-surgical.py` semantics, run from the
  pre-fix copy): `lines=250 identical=49 changed=201 problems=0`.
- `boardctl -C <repo> validate` → `RESULT: OK` (03-board-validate.txt).

## Residual risk — read this before trusting the green

1. **17 rows are bound to a verdict whose `passed` is false** (`AUG-004`, `AUG-013`, `AUG-014`,
   `AUG-015`, `AUG-031`, `AUG-035`, `AUG-036`, `AUG-050`, `AUG-051`, `AUG-060`, `AUG-075`,
   `AUG-080`, `AUG-082`, `AUG-114`, `DOC-002`, `GITREINS-JUDGE`, `QA-AUGER-6`). The artifact is
   real and its `task_id` is the row id, which is exactly what A12 asks to be able to check — but a
   failing judge verdict is weak closure evidence. No row preferred a failing verdict over a
   passing artifact for the same task (`pick_run` prefers `passed=true`), so each of these 17 has
   only failing artifacts. The audit prints `bound_failed_verdict=17` on every run rather than
   hiding it.
2. **A binding proves a judge ran for that task id, not that the row's closure commit is the judged
   commit.** A12 defines identity as `task_id == row id`; that is the only identity check the goal
   asks for and the only one this audit performs. A row could in principle cite a real-but-stale
   verdict for itself (`AUG-006` has four). Closing that would need a commit-identity rule
   (verdict `commit` == row `commit_hash`), which the AC does not specify and the artifacts would
   not satisfy consistently.
3. **`boardctl validate` warning count rose 212 → 378, all of it one class.** The +166 are
   `key(s) {evidence_run_id} outside the declared canon`. boardctl's own key canon
   (`coding-hermes-boardctl/internal/board/keys.go`, `sanctionedExtras`) declares `evidence` but not
   `evidence_run_id`, while its own `RowEvidenceRunIDs()` (`evidence_gate.go`) honors the top-level
   field and its `--status complete` refuses a completion without a run id (TR-251). Every other
   warning class is unchanged (168 → 168). The field name is mandated by this row's acceptance
   criteria; the canon gap is in the tools repo and is a follow-up there, not something to paper
   over here.
4. **The AUG-156 row itself is left `pending`.** It has no verdict naming it, so closing it in this
   same commit would manufacture exactly the unbacked completion this row removes. Its closure
   belongs to the judge/foreman that evaluates this branch.
5. **6 adopted artifacts are FAIL verdicts** for `AUG-114` (×2), `AUG-116`, `AUG-119`, `AUG-123`
   (×2). Only `AUG-114` is a closed row here; the other four are open rows whose artifacts are
   committed as-is because they are real history, not because they justify a closure.
6. The audit is a *falsifier for claims*, not a substitute for reading the row: a `none:` reason
   states a verifiable fact about the artifact set, it does not state that the row's work is good.

## Reproduce

```bash
cd /home/kara/proj-wt-auger-xml-hardening
python3 scripts/audit_board_evidence.py --board .coding-hermes/board/tasks.jsonl           # exit 0
python3 scripts/audit_board_evidence.py --board .coding-hermes/board/tasks.jsonl --verbose # per row
python3 -m pytest tests/test_board_evidence_audit.py -q                                    # 17 passed
boardctl -C /home/kara/proj-wt-auger-xml-hardening validate                                # RESULT: OK
```

## Gate

See `04-gate.txt`.
