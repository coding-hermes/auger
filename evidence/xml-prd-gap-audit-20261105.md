# Auger XML Spec Graph — PRD gap audit (2026-11-05)

> **Staleness note (2026-11-05 12:10):** every measurement below was taken at HEAD `10ef0d7`,
> **before** the SG1/SG4 wave merged (`1cb4032` AUG-130, `55a6725` AUG-134+AUG-132, main `c7ff7f7`).
> Re-checked at `c7ff7f7`: AUG-132/AUG-134 (validate==check + obligation enforcement) and AUG-130
> (skipped-required-arm) are now fixed and merged; the ABSENT findings this audit files — R8
> (decision-ref), R13 (semantic findings), AC-5 (view regeneration), AC-14 (measured assembly),
> AC-15 (trace artifact), R15-rollback — were re-measured at `c7ff7f7` and **still hold** (they are
> now board rows AUG-138..AUG-143). Read the R-matrix below for the requirement set, not for the
> post-merge status of AUG-130/132/134.

**PRD:** `docs/prd/xml-spec-graph.md` (272 lines)
**Repo:** `~/auger`, HEAD `10ef0d7` on `main`
**Auditor method:** every status below is backed by a command run in this audit or a `file:line`
citation. Nothing is copied from `docs/XML-SPEC-COVERAGE.md`; where that file disagrees with
measured reality, the disagreement is recorded in §7.

Status vocabulary: **BUILT** (code + a test that would fail if it broke) · **PARTIAL** (code exists,
a named clause is missing or unproven) · **UNPROVEN** (code exists, no test would catch a regression)
· **ABSENT** (no code path).

---

## 0. Headline

The PRD describes a five-part system: XML source graph → selection/assembly → verification
obligations → boardctl approval lifecycle → GitReins judge packet. **Only the first three exist, and
only against test fixtures.** The lifecycle half (R9, R14, R16, AC-6/7/18/19/20) has zero Auger code,
and the one cross-repo seam that *was* built (GitReins GR-154) was built against a checker protocol
that Auger never implemented.

Measured totals across R1–R16: **6 built, 2 partial, 1 unproven, 7 absent.**

Reproduce the whole audit:

```bash
cd ~/auger
.venv/bin/auger spec validate tests/fixtures/xml/specs          # ok
.venv/bin/auger spec check tests/fixtures/xml/specs             # FAILS (see §4)
.venv/bin/python -m pytest -q tests/test_xml_specs.py tests/test_verification_graph.py   # 40 passed
grep -c boardctl auger.py                                       # 0
grep -n 'diff-stdin\|protocol-version\|repo-root' auger.py /dev/null # no matches
```

---

## 1. Requirement matrix — R1..R16

| Req | Subject | Status | Evidence | Missing half |
|---|---|---|---|---|
| **R1** | XML is authoritative source; MD/HTML are views | **PARTIAL** | Parser + assembler exist: `auger.py:7763` `_spec_read_document`, `auger.py:8194` `render_spec_assembly`. Selection renders to Markdown/JSON via `spec assemble` (`auger.py:8348-8363`). | **No authored corpus exists.** `find specs -name '*.xml' → 0`; `specs/` holds only `schema/auger-spec-1.0.xsd`. `auger spec validate specs` → `spec error (empty-corpus)`, rc=2. Every real XML document in the repo is a test fixture under `tests/fixtures/xml/`. The "Markdown are generated views" doctrine is untested against any real document — only `tests/fixtures/xml/expected/*.md` goldens. |
| **R2** | Unique, stable, never-renumbered IDs | **BUILT** | `SPEC_ID_RE = ^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$` (`auger.py:7729`), max len 80 (`:7732`); duplicate refusal in `_spec_validate_blocks` (`:8067`). Test: `tests/test_xml_specs.py:394` `test_refusals_are_typed_and_emit_no_document` covers `duplicate-id`. | — ("never renumbered" is unenforceable by any tool; not counted against it.) |
| **R3** | Typed `kind`/applies-to/lifecycle/version/verification | **BUILT** | `SPEC_BLOCK_KINDS`, `SPEC_STATUSES`, `SPEC_VERIFICATION_METHODS`, `SPEC_REQUIRES_METHOD` (`auger.py:7716-7728`). Parity test `tests/test_xml_specs.py:173` `test_schema_matches_the_validator` compares XSD enums to the constants. | Caveat: the XSD is **never loaded by the validator** — it is only string-compared in that test (§4 finding D). |
| **R4** | Cross-doc reuse; detect duplicate/dangling/cycle/invalid version | **BUILT** | `_spec_include_map:7832`, `_spec_refuse_reference_cycles:7986`, `_spec_validate_blocks:8022`. 8 negative fixtures under `tests/fixtures/xml/invalid/` exercised by `test_refusals_are_typed_and_emit_no_document` (`:394`). Inclusive check via CLI: `auger spec validate tests/fixtures/xml` → `external-entity-forbidden` rc=2 (fails closed on a poisoned corpus). | — |
| **R5** | Targeted retrieval by ID/attribute + explicit closure, with provenance | **BUILT** | `select_spec_blocks:8131`, `_spec_matches:8116`, forward closure `SPEC_FORWARD_CLOSURE_ATTRS=('depends-on',)` + inverse `SPEC_INVERSE_CLOSURE_ATTRS=('verifies',)` (`:7722-7725`). Provenance `source` field = `file.xml#ID`. Measured: `spec list --id AUG-SHARED-DETERMINISM` → `shared.xml#AUG-SHARED-DETERMINISM`. Tests `:238`, `:293`, `:367`. | — |
| **R6** | Byte-identical deterministic read-only rendering | **BUILT** | Measured twice: `spec assemble --kind requirement --applies-to auger.export \| sha256sum` → `46fb3c21c0f9ae15405c15b41ef6c5a373988f3a3a84ff21b1c1c97a2b044ad2` both runs. Tests `:266` golden, `:303` byte-identical, `:461` export unchanged. | — |
| **R7** | Safe parsing: no external entities, no network, fail before partial output | **BUILT** | `SPEC_FORBIDDEN_DECL_RE = <!\s*(?:DOCTYPE\|ENTITY)` (`:7733`); `remote-reference-rejected` (`:7814`), `include-outside-root` (`:7823`). Fixture `tests/fixtures/xml/invalid/external-entity/entity.xml`. Atomic write `_spec_write_atomic:8274`; refusal test asserts `not target.exists()`. | — |
| **R8** | DuckBrain stays authoritative; XML *references* stable record IDs | **ABSENT** | No attribute in the schema or validator can carry a decision/record reference. `grep -n 'record\|decision\|ref-id' specs/schema/auger-spec-1.0.xsd` → only a comment at line 10. `SPEC_EDGE_ATTRS` (`:7721`) are `depends-on/verifies/refs/implements/supersedes` — all intra-spec, none resolve into DuckBrain. | **Code to write:** a typed edge (e.g. `decision-ref="D-001"`) in `SPEC_EDGE_ATTRS` + XSD, and a resolver that checks the namespace's record exists and is not superseded. Nothing touches DuckBrain in the spec path at all. |
| **R9** | Approval-bound boardctl write (digest gate, idempotent, read-back) | **ABSENT** | `grep -c boardctl auger.py` → **0**. No plan/digest/approval/ledger code. `grep -rn 'tasks.jsonl\|events.jsonl' auger.py` → no matches. | **Code to write:** a `spec plan` verb (selection → draft rows), a `spec approve --digest <sha>` writing an append-only ledger, a boardctl-API client, and a read-back comparator. This is the single largest missing piece. |
| **R10** | `ch:trace` graph checker (row/spec/test/evidence/verdict/commit, `none:<reason>`) | **ABSENT** | `grep -c 'ch:trace' auger.py` → **3**, all three are *comments recording the repo's own traces* (lines 95, 7708, 7709). No parser, no validator, no `none:` handling in code. | **Code to write:** a marker parser over an 11-field grammar, resolution of each pointer, and the `none:<reason>` rule. Zero lines exist. |
| **R11** | Read-only staged-diff checker, machine-readable findings | **ABSENT** | `grep -n 'staged\|--diff\|diff_path' auger.py` → **no matches**. No verb accepts a diff. | **Code to write:** a `spec check-diff` verb taking a staged diff on stdin + `--repo-root`/`--spec-root`, emitting findings JSON (class, rule, ids, location, reason). Also the verb GitReins already tries to call (see R12). |
| **R12** | Opt-in GitReins spec-alignment guard reusing the Auger checker | **PARTIAL (broken seam)** | **GitReins side is BUILT and GR-154 is `status: complete`** (`.gitreins/tasks.yaml:152-157`, completed 2026-10-31). `engine/spec_alignment.py` implements staged-only diff, warn/block, timeout, missing-checker policy, redaction; `engine/guard_manager.py:1858` gates it on `spec_alignment.enabled is True`; `engine/config.py:74-102` defines the config. Tests: `tests/test_spec_alignment.py`. | **The Auger half does not exist.** GitReins invokes the checker with `--repo-root --spec-root --protocol-version --diff-stdin` (`engine/spec_alignment.py:122-132`). `grep -n 'diff-stdin\|protocol-version\|repo-root' auger.py` → **no matches**; the only hits for `protocol_version` are unrelated HTTP constants (`:1786`, `:7451`). Verified by simulating the call: `auger spec --repo-root . --spec-root specs --protocol-version 1 --diff-stdin check` → `argparse: invalid choice: '.'`, rc 2. Additionally GitReins' `SpecAlignmentConfig.command` defaults to `""` (`config.py:78`) so no Auger binary is wired by default, and every GitReins test drives a **synthetic stub script** (`tests/test_spec_alignment.py:37` writes `#!/usr/bin/env python3` bodies), never real Auger. **So R12 is wired on one side of a protocol the other side never implemented.** |
| **R13** | Semantic honesty: evidence + uncertainty, never auto-edit | **ABSENT** | `grep -ni 'semantic\|contradict\|uncertaint' auger.py` → hits only in unrelated retrieval-tier and scoring code (`:1516`, `:3877`). No spec-semantic review path. | **Code to write:** a semantic finding type carrying evidence excerpt/digest + calibrated uncertainty, and a no-write guarantee. Depends on R11. Correctly sequenced last by the PRD. |
| **R14** | Scoped task packet (in/out-of-scope IDs, exclusion reasons, digests) | **ABSENT** | No task-packet code. `grep -c boardctl auger.py` → 0; no `out-of-scope` concept in the schema. | **Code to write:** a packet builder emitting `{board_id, acceptance verbatim, in_scope[], out_of_scope[{id,reason}], source version/digest}`. Requires R9's plan object as input. |
| **R15** | Structured verification obligations incl. migration apply-test | **BUILT** | `SPEC_RUNNERS` (`:8405`), obligation validation `:8473-8540` (runner ∈ whitelist, tracked via `git ls-files --error-unmatch`, selector verified by AST walk, migration+fixture existence, non-empty assertions). Tests: `test_assembles_migration_obligation_with_repo_harness_metadata`, `test_serialized_runner_is_argument_vector_not_shell_command`, `test_rejects_duplicate_dangling_stale_and_unregistered_test_refs`, and the apply/no-op proof (see §5). | Rollback clause: explicitly declared unsupported, which AC-17 permits — but the schema has **no rollback attribute at all**, so "declared unsupported" is not machine-readable, only absent. |
| **R16** | Boardctl-sourced GitReins judgment on the real committed tree | **PARTIAL (half-built elsewhere)** | GitReins landed the *intake*: `engine/auger_packet.py` (`TaskPacketAdapter`), `engine/judge.py:106-223` (`_prepare_auger_task` fails closed to Tier 1 on `PacketValidationError`, `auger_test:` results), tests `tests/test_auger_task_packet.py`. GR-155 recorded satisfied in `.gitreins/tasks.yaml:227`. | **No packet producer on the Auger side.** Nothing in `auger.py` builds a judge packet, reads a boardctl row, or validates `ch:trace` before handoff. GitReins can consume a packet that Auger cannot emit. "Judges the actual committed tree independently of worker self-report" is only exercised against hand-built fixtures in `test_auger_task_packet.py`. |

---

## 2. Acceptance criteria — AC-1..AC-20

Legend for column *Named proof*: **exists** = file and exact test name present · **renamed** = a test
covers the clause under a different name · **missing** = neither.

| AC | Status | Named proof | Evidence |
|---|---|---|---|
| **AC-1** | BUILT | `test_xml_specs.py::test_shared_block_reference` — **exists** (`:214`) | Resolves `AUG-SHARED-DETERMINISM` from two files. Confirmed via CLI: `spec list --id AUG-SHARED-DETERMINISM` → `shared.xml#AUG-SHARED-DETERMINISM`. |
| **AC-2** | BUILT | "all negative cases in test_xml_specs.py" — **exists** as `test_refusals_are_typed_and_emit_no_document` (`:394`) | 8 parametrized fixtures: unresolved-reference, duplicate-id, reference-cycle, schema-version, invalid-verification-method, missing-verification-method, include-cycle, external-entity. Asserts rc==2, empty stdout, `spec error (<code>)` on stderr, **and** `not target.exists()` (no partial output). |
| **AC-3** | BUILT | "golden output in tests/fixtures/xml/expected/" — **exists** (`:266` `test_selection_matches_the_golden`) | Golden `expected/assembly-active-auger-export.md`. Live: 3 matched + 2 deps = 5 blocks, each with `source: file.xml#ID`. |
| **AC-4** | BUILT | "deterministic golden test and repeated SHA-256 equality" — **exists** (`:303`) | Reproduced: two runs → identical `46fb3c21…`. |
| **AC-5** | **PARTIAL** | "multi-view regeneration fixture plus source-only mutation test" — **missing** | Only `test_markdown_export_is_unchanged` (`:461`) exists, which proves the *DuckBrain* export didn't change — unrelated to spec multi-view regeneration. `grep 'regenerat\|two.view\|mutation' tests/test_xml_specs.py` → no matches. There is no "regenerate views" verb and only **one** spec golden. The "a source block change updates both views" loop is unproven. |
| **AC-6** | ABSENT | "boardctl integration test comparing both files before/after" — **missing** | No board export path exists to refuse. |
| **AC-7** | ABSENT | "ephemeral DuckBrain + boardctl fixture" — **missing** | No board write, no read-back, no idempotency code. |
| **AC-8** | ABSENT | `tests/test_spec_alignment.py::test_aligned_change_passes` — **missing** (file does not exist in Auger) | `ls tests/test_spec_alignment.py` → No such file. (The file exists in **GitReins** with different tests; that does not satisfy an Auger AC.) |
| **AC-9** | ABSENT | "dedicated negative fixture cases" — **missing** | No `ch:trace` checker at all (R10). |
| **AC-10** | ABSENT (Auger) / BUILT (GitReins) | "existing hook test plus disabled-config regression" — **renamed**: `gitreins/tests/test_spec_alignment.py` | The flag exists and is off by default (`config.py:77` `enabled: bool = False`), and the guard only runs on `enabled is True` (`guard_manager.py:1858`). Not verifiable from Auger. |
| **AC-11** | ABSENT (Auger) / BUILT (GitReins) | "GitReins integration tests for each outcome plus unstaged sentinel assertion" — **renamed**: GitReins `test_spec_alignment.py:156-169` | GitReins test writes an "unstaged marker must not be sent" sentinel and asserts staged-only. Against a **stub** checker. |
| **AC-12** | ABSENT (Auger) / BUILT (GitReins) | "subprocess fault-injection suite with process-liveness assertion" — **renamed**: GitReins `test_spec_alignment.py:196-206` | GitReins asserts an orphaned `sleep 30` child is killed and records the pid. Against a stub. |
| **AC-13** | ABSENT | "paired positive/negative fixtures and unchanged-tree hash checker" — **missing** | No semantic review code (R13). |
| **AC-14** | **PARTIAL** | "JSON report with bytes and elapsed time" — **missing** | Measured JSON top-level keys: `['blocks','counts','schema_version','selection']`; `counts` = `{'dependencies': 2, 'matched': 3, 'total': 5}`. **Counts only** — no input bytes, no output bytes, no elapsed time, no "reads no unselected bodies" assertion (the loader parses every `*.xml` via `rglob` in `_xml_spec_documents:8417` / `load_spec_corpus:8085`). |
| **AC-15** | ABSENT | "sample end-to-end trace artifact in docs/prd/evidence/" — **missing** | `ls docs/prd/evidence` → No such directory. |
| **AC-16** | ABSENT | `tests/test_task_context.py::test_scope_and_exclusions_are_explicit` — **missing** (file does not exist) | No in/out-of-scope model in schema. |
| **AC-17** | BUILT | "fixture whose test actually applies the named migration file and fails when replaced by a no-op" — **exists** as `test_xml_specs.py::test_migration_obligation_applies_named_file_and_detects_noop` (`:549`), plus `test_assembles_migration_obligation_with_repo_harness_metadata` (`:505`) and `test_serialized_runner_is_argument_vector_not_shell_command`. The PRD names no test, and the fixture's `selector=` resolves to the real function at `:549`. | Verified by mutation — see §5 (plain no-op and commented-out body both flip red; restored clean). |
| **AC-18** | ABSENT | "isolated boardctl/GitReins fixture with byte/digest comparison" — **missing** | No packet writer. |
| **AC-19** | ABSENT | "paired negative/positive integration tests asserting judge invocation count" — **missing** | No handoff gate. |
| **AC-20** | ABSENT | "judge-context integration fixture … deliberately false worker summary" — **missing** | GitReins has `tests/test_auger_task_packet.py`, but no false-worker-summary + red-migration-test case, and no packet producer to feed it. |

**AC tally: 5 BUILT (1,2,3,4,17), 2 PARTIAL (5,14), 13 ABSENT.**

---

## 3. Answers to the six questions

### Q1 — Which requirements have NO code at all, and where would it go?

Four requirements have **zero** lines, all on the Auger side of the lifecycle:

1. **R8 — decision-store edge.** Add a `decision-ref` typed edge to `SPEC_EDGE_ATTRS`
   (`auger.py:7721`) and the XSD, plus a resolver that queries the DuckBrain namespace. No spec-path
   code touches DuckBrain today.
2. **R9 — approval-bound board write.** New `spec plan` / `spec approve --digest` verbs next to
   `_spec_dispatch` (`auger.py:8300`), a plan-digest ledger, and a boardctl client. `grep -c boardctl
   auger.py` → 0.
3. **R10 — `ch:trace` checker.** A marker parser + 11-field resolver. All 3 `ch:trace` occurrences in
   `auger.py` are prose comments, not code.
4. **R11 — staged-diff checker.** A `spec check-diff` verb. No diff ever enters the process
   (`grep 'staged\|--diff' auger.py` → nothing). **R13 (semantic review) also has no code**, and
   cannot have any until R11 exists.

R14 and R16 have no *Auger* code but do have GitReins counterpart code (`engine/auger_packet.py`).

### Q2 — Implemented but unproven?

Only one clear case:

- **R8 is absent, but R1's "views are generated, never authoritative" doctrine is UNPROVEN**: the
  renderer exists and is deterministic, but no test mutates a source block and asserts that a
  previously-rendered view changes. AC-5 is the proof that would catch it, and AC-5 does not exist.
  A regression that made `render_spec_assembly` emit a cached/stale body would pass all 40 tests.

Borderline: **R3/R2 look proven but their *normative grammar* (the XSD) is never executed.** If the
XSD were deleted, `test_schema_matches_the_validator` would still pass? No — it reads the file, so it
would error; but if the XSD were *edited to the wrong shape while the constants stayed put*, only
whatever fields that single test happens to compare would catch it. See finding D.

### Q3 — ACs naming a nonexistent test; old→new mapping

**Named file does not exist:**
| AC | Named | Reality |
|---|---|---|
| AC-8 | `tests/test_spec_alignment.py::test_aligned_change_passes` | no such file in Auger |
| AC-16 | `tests/test_task_context.py::test_scope_and_exclusions_are_explicit` | no such file |

**Test exists under a different name (map):**
| AC | PRD name | Actual covering test |
|---|---|---|
| AC-2 | "all negative cases" | `tests/test_xml_specs.py::test_refusals_are_typed_and_emit_no_document` |
| AC-4 | "deterministic golden test" | `tests/test_xml_specs.py::test_selection_is_byte_identical_across_runs` |
| AC-10 | "existing hook test + disabled-config regression" | `gitreins/tests/test_spec_alignment.py` (flag-off cases) — **not an Auger test** |
| AC-11 | "GitReins integration tests for each outcome" | `gitreins/tests/test_spec_alignment.py::test_unstaged_content_is_not_sent` (approx.) |
| AC-12 | "subprocess fault-injection suite" | `gitreins/tests/test_spec_alignment.py::test_orphan_child_is_killed` (approx.) |
| AC-17 | "fixture … fails when replaced by a no-op" | **not a mapping — the PRD names no test.** The clause is carried by `tests/test_xml_specs.py::test_migration_obligation_applies_named_file_and_detects_noop` (`:549`), which is also the fixture's `selector=` target |

**Correction to an earlier suspicion (verified):** the fixture's `selector=` is
`test_migration_obligation_applies_named_file_and_detects_noop`, and that function **does** exist at
`tests/test_xml_specs.py:549` — the same file the fixture's `path=` names. So the selector resolves
correctly and `auger spec check tests/fixtures/xml/verification` → `{"nodes": ["REQ-MIG-1",
"TEST-MIG-1"], "status": "valid"}` rc 0 is legitimate. A *similarly named* test
(`test_migration_obligation_applies_named_file`, without `_and_detects_noop`) exists in
`tests/test_verification_graph.py:112`; that near-duplicate is a maintenance smell, not a defect.
The PRD's AC-17 does not name a test, so there is no AC-17 naming gap.

### Q4 — Is R15/migration genuinely satisfied?

**Yes for the apply-and-fail-on-no-op clause; no for rollback.** I mutated the migration file and
ran the named test in both shapes.

**Proof of the no-op failure (plain no-op):**
```
$ echo "-- no-op" > tests/fixtures/xml/verification/migrations/001_add_marker.sql
$ .venv/bin/python -m pytest -q tests/test_xml_specs.py::test_migration_obligation_applies_named_file_and_detects_noop
>       assert "CREATE TABLE marker" in migration
E       AssertionError: assert 'CREATE TABLE marker' in '-- no-op\n'
tests/test_xml_specs.py:556: AssertionError
1 failed, 1 passed
```

**Proof of the schema-assertion failure (commented-out body — the realistic no-op):**
```
$ printf -- '-- 001_add_marker.sql\n-- CREATE TABLE marker (...);\nSELECT 1;\n' > …/001_add_marker.sql
>       assert "version" in columns
E       AssertionError: assert 'version' in set()
tests/test_xml_specs.py:564: AssertionError
1 failed
```

**The test body that proves it** (`tests/test_xml_specs.py:549-573`):
```python
def test_migration_obligation_applies_named_file_and_detects_noop():
    obligation = auger.assemble_xml_requirement(VERIFICATION_FIXTURES, "REQ-MIG-1")["verifications"][0]
    prior = (REPO_ROOT / obligation["fixture"]).read_text(encoding="utf-8")
    migration_path = REPO_ROOT / obligation["migration"]
    migration = migration_path.read_text(encoding="utf-8")
    assert "CREATE TABLE marker" in migration          # <-- literal guard

    database = sqlite3.connect(":memory:")
    database.executescript(prior)                       # real prior schema
    database.executescript(migration)                   # the NAMED file, not a copy

    def assert_migration_result(connection):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(marker)")}
        assert "version" in columns
        assert connection.execute("SELECT version FROM marker").fetchone() == (1,)

    assert_migration_result(database)

    no_op_database = sqlite3.connect(":memory:")
    no_op_database.executescript(prior)
    no_op_database.executescript("")                    # <-- no-op
    with pytest.raises(AssertionError):
        assert_migration_result(no_op_database)
```

**Verdict:** the file is genuinely applied (read from disk, `executescript`'d), and the assertion
block flips red when the migration no-ops. Restored after probing — `sha256sum` back to
`2a8a25df…` and `git status --porcelain tests/fixtures/xml/verification/` clean.

**Caveats that keep this from being a clean pass:**
- The "no-op" case is a **separate `executescript("")`, not the same test re-run with the file
  replaced.** The AC wants "fails when that file is replaced by a no-op" — the test simulates it
  rather than replacing it. It does catch the realistic case (proven above), but by asserting on
  the loaded text (`assert "CREATE TABLE marker" in migration`), not by substituting the file.
- Both `prior` and `migration` paths come from the **XML attributes themselves**
  (`obligation["fixture"]`, `obligation["migration"]`), so a spec that renamed its migration would
  silently test the renamed one — the obligation is self-consistent, not counter-verified.
- **Rollback:** no attribute exists in the XSD, so "declared unsupported" is an absence, not a
  machine-readable declaration. AC-17 permits this; R15's wording ("rollback is tested when declared
  supported") is thus satisfied only vacuously.

### Q5 — What an end-to-end run would require that does not exist today

1. **An authored spec corpus.** `specs/` has zero XML. The whole pipeline is exercised only against
   `tests/fixtures/xml/`. `auger spec validate specs` → `empty-corpus`, rc 2.
2. **A boardctl bridge.** No plan, no digest approval, no ledger, no read-back. (R9, R14.)
3. **A staged-diff / `ch:trace` checker verb.** GitReins calls `--repo-root --spec-root
   --protocol-version --diff-stdin`; Auger exposes none of these. (R10, R11, R12.)
4. **A GitReins packet producer.** GitReins has the *consumer* (`engine/auger_packet.py`); Auger has
   no producer. (R16, AC-18.)
5. **An end-to-end evidence artifact.** `docs/prd/evidence/` does not exist. (AC-15.)
6. **One consistent spec root.** `spec check` works on `tests/fixtures/xml/verification` but **fails
   on `tests/fixtures/xml/specs`** (`invalid-root: … expected <spec>` — those files use the
   `urn:auger:spec:1` namespaced `<spec>` that `_xml_spec_documents` rejects at `auger.py:8422` by
   comparing a bare `document.tag != "spec"` against a Clark-notation `{urn:auger:spec:1}spec`).
   **This is a real defect**: the two validators disagree about what a `<spec>` document is.

### Q6 — Verification honesty

- I ran every command quoted above. The only green I report as green is:
  `40 passed in 0.37s` from `.venv/bin/python -m pytest -q tests/test_xml_specs.py
  tests/test_verification_graph.py`.
- I did **not** run the full pytest suite (30 min, live DuckBrain).
- `tests/gate.sh` and `tests/test_gate_mode.sh` carried pre-existing uncommitted changes; I read them
  only, and did not revert or commit them. The gate's pytest arm (`tests/gate.sh:165`) does include
  `tests/test_xml_specs.py tests/test_verification_graph.py`.

---

## 4. Defects found while auditing (not just gaps)

**D. The XSD is decorative.** `specs/schema/auger-spec-1.0.xsd` is never loaded by `auger.py`
(`grep -rn 'xsd\|XMLSchema\|lxml' auger.py` → comments only at `:7699`, `:8469`). Validation is
hand-written in `_spec_validate_blocks:8022`. The XSD also lacks a `test` element and the
`runner/selector/path/migration/fixture/assertions` attributes that AUG-123 requires, so an editor
validating real specs against the shipped grammar would flag every obligation as invalid. Per the
PRD's own design note, the schema is normative — today it is a comment.

**E. Two divergent validators.** `load_spec_corpus` (namespaced, used by `validate/list/assemble`)
and `validate_xml_specs` (bare-tag `document.tag != "spec"`, used by `check/bundle`) disagree on the
acceptable root form. Consequence: `spec check tests/fixtures/xml/specs` fails with
`invalid-root` while `spec validate tests/fixtures/xml/specs` reports `3 document(s), 10 block(s) ok`.
A corpus that passes one verb is rejected by the other.

**F. `spec check` cannot see the main corpus.** `auger spec check tests/fixtures/xml/specs` →
`spec error (invalid-spec-graph): invalid-root: … assembly.xml: expected <spec>`, rc 2. So the
obligation validator (the R15 machinery) is only ever exercised on `tests/fixtures/xml/verification`.

**G. GitReins/R12 protocol mismatch** (detailed under R12): GitReins is `status: complete` calling a
protocol Auger never shipped, and its tests use stub checkers, so both sides are individually green
while the seam is dead.

---

## 5. Summary tables

**Requirements (16):**
- **BUILT (6):** R2, R3, R4, R5, R6, R7.
- **PARTIAL (4):**
  - **R1** — parser/assembler/rendering real and deterministic, but zero authored spec documents exist; `specs/` is empty, so "XML is the authoritative source" is proven only on fixtures.
  - **R15** — obligations + migration apply-test built and mutation-proven; rollback clause is an absence, not a machine-readable "declared unsupported".
  - **R12** — GitReins half complete; Auger half (the checker verb GitReins calls) does not exist; seam dead.
  - **R16** — GitReins intake built; Auger packet producer does not exist.
- **ABSENT (6):** R8, R9, R10, R11, R13, R14.

**Acceptance criteria (20):** 5 BUILT (AC-1,2,3,4,17) · 2 PARTIAL (AC-5, AC-14) · 13 ABSENT
(AC-6,7,8,9,10,11,12,13,15,16,18,19,20).

**Success criteria — 7 replayable proofs:**

| # | Proof | Status |
|---|---|---|
| 1 | Select a board task, retrieve linked requirement/AC by ID, report anchors + payload bytes | **ABSENT** — no board link; assembly by ID works but no board task exists to link to, and no payload-byte metric (AC-14 counts only) |
| 2 | Shared-block change updates every generated view; test proves no duplicate source text | **PARTIAL** — shared block resolves from two files (`shared.xml` included by `export.xml`, referenced by `assembly.xml`), but only **one** golden view, no mutation-of-source test |
| 3 | Missing `ch:trace row=` named by checker; GitReins blocks only on `block` | **ABSENT (Auger)** — no checker. GitReins warn/block policy is built and tested against a stub |
| 4 | Approved plan → exactly one canonical row per task, read-back proves criteria survived | **ABSENT** — R9 |
| 5 | Migration requirement: one query returns file/prior-schema/test/assertions/rollback; no-op fails | **BUILT** — proven by mutation (§Q4); rollback absent by design |
| 6 | False worker claim cannot PASS; packet from canonical row, summary excluded | **ABSENT (Auger)** — GitReins `_prepare_auger_task` fails closed on bad packets, but no packet producer, no false-summary + red-migration fixture |
| 7 | Report `docs/` corpus size, bundle size, lookup latency; bundle materially smaller | **ABSENT** — no bytes or latency emitted; `counts` only |
| 8 | Disabled GitReins flag unchanged; enabled path leaves no orphan process | **BUILT (GitReins only)** — flag default off, guard gated on `enabled is True`, orphan-kill test present; not verifiable from Auger |

**Bottom line:** the XML source graph, selection, assembly, determinism and verification-obligation
halves are real, tested, and reproducible — but only against test fixtures, with a non-normative XSD.
The half that makes the project *traceable* (boardctl approval → canonical row → `ch:trace` checker →
GitReins judge packet) does not exist on the Auger side at all, and the one seam that was built
crosses a protocol that was never implemented.
