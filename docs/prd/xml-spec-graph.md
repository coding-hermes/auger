# PRD: Auger XML Spec Graph — author once, select precisely, verify the whole chain

**Project:** Auger (`coding-hermes/auger`) · **Status:** proposed · **Effort:** L
**Board work:** `AUG-118`, `AUG-119`, `AUG-120` · GitReins integration: `GR-154`
**Primary source:** Oct 15 discussion on XML specs; follow-up clarification that the target is Auger, not the SpecLang suite.
**Traceability doctrine:** `docs/traceability-doctrine.md` in GitReins and the `coding-hermes-traceability` skill.

## The one-liner

Auger will manage modular, versioned XML specifications whose tagged requirements and verification criteria can be selected and assembled across documents, then use those same links to map approved plans into boardctl and run a GitReins pre-commit alignment check.

## What you asked for — shared understanding

The October 15 discussion established these needs:

- Specifications contain distinct sections, rules, requirements, acceptance criteria, and verification details.
- Each item needs stable identity and XML attributes so tools can select it by type, applicability, status, version, or relationship.
- An author should define a block once and reuse it from several documents; changing the source block should update every rendered view instead of leaving sibling Markdown pages in conflict.
- A tool should retrieve only the relevant sections across several spec files, not read a large Markdown corpus to find them.
- PRD statements, spec requirements, acceptance criteria, implementation, and verification should be linked and auditable.

The later Auger clarification adds two connected integrations: approved Auger plans should create/manage canonical tasks through boardctl, and GitReins should optionally check staged changes against the assembled specs and `ch:trace` rules. This PRD treats them as integration edges of the same traceable lifecycle, not as a second task database or a new GitReins backend.

## Current state and measured problem

**Live repository:** `~/auger`, remote `git@github.com:coding-hermes/auger.git`, `main` at `89637db` when measured. The board had 211 rows; `AUG-118`, `AUG-119`, and `AUG-120` are pending P1 work. GitReins has `docs/traceability-doctrine.md`; its paired task is `GR-154`.

**Auger does not yet provide the requested XML spec graph.** The `auger export` and `auger export --profile nasa` paths render Markdown from DuckBrain tables. They do not parse XML specs, select XML attributes, resolve cross-document inclusions, or expose a staged-diff spec-alignment interface. Auger’s current design document (`docs/DESIGN.md`) describes its DuckBrain decision registers and Markdown export; it is not an XML requirements language.

**The current retrieval problem is measurable.** A fresh inventory found 32 Markdown/text documents under `docs/`, 300,694 characters (about 75,173 tokens at the rough four-characters-per-token estimate), and 340 Markdown headings. On the current 211-row board, 97 rows contain a prose `docs/` path and 51 contain a `ch:trace` mention. These counts show the scale and discoverability problem; a mention is not proof that a trace is valid or complete.

The October 15 audit recorded an earlier baseline of 28 docs / 264,131 characters / 207 second-level sections, with 76 board rows referring to docs paths in prose and only 2–3 rows carrying `ch:trace`. The collection has since grown; the current counts above supersede the old size snapshot. Neither count alone measures contradictory requirements or proven trace coverage—that is a required output of this project.

**Named failure case:** the Oct 15 Auger docs/board audit found that requirement-like work was not structurally connected: acceptance criteria appeared under multiple field names, many board rows pointed to docs only as prose, and `ch:trace` adoption was rare. A prose search can find a likely paragraph, but it cannot reliably prove that a PRD statement, its spec requirement, its acceptance test, and the changed code refer to the same contract. The present failure is therefore *unresolved identity and relationship*, not merely slow Markdown reading.

## Value case

A person or agent should spend one lookup resolving the selected contract, rather than repeatedly reading the full docs tree and manually reconciling duplicated sections. One XML source block can feed multiple assembled spec views; a board task, its code diff, and its verification evidence can point back to stable IDs. This trades one-time schema/adapter work for lower repeated retrieval, fewer orphaned criteria, and a reusable pre-commit check.

Value is proved by replaying real Auger board/spec cases and reporting retrieval bytes, resolution accuracy, and trace coverage—not by a generic “quality improved” score.

## State model

| State | Authoritative source | What may be derived | Drift this PRD must expose |
|---|---|---|---|
| Spec contract | Version-controlled XML source files | Selected block bundle; Markdown/HTML views | Rendered text differs from source or references a stale version |
| Auger decision/evidence | Existing DuckBrain namespace tables | XML links to decision/option/unknown IDs | An XML claim cites a missing or superseded record |
| Approved work | Canonical JSONL board written through boardctl | Auger draft plan and task mapping | Draft has no approval, or board row differs from approved plan |
| Implementation | Git staged diff and source comments | Candidate related spec blocks and trace findings | Change is unscoped, points to wrong requirement, or contradicts its contract |
| Verification | Tests, CI, GitReins verdicts and committed evidence | Verification links in assembled view | A requirement claims evidence that did not run or is not bound to the commit |

The XML repository is authoritative for authored specification text and relationships. DuckBrain remains authoritative for Auger’s existing decision records. The board remains authoritative for tasks. Git history and recorded test/verdict artifacts remain authoritative for implementation and verification. No system silently overwrites another authority.

## User stories

1. **As a spec author**, I can define a requirement once with stable identity and reuse it from multiple XML documents, so a shared rule cannot drift between copied pages.
2. **As an agent**, I can select requirements by IDs and attributes and receive only those blocks plus their declared dependency closure, so I can work from a small, exact context bundle.
3. **As a reviewer**, I can follow one trace from PRD intent through requirement, acceptance criterion, board row, code change, test, verdict, and commit, so missing or contradictory links are visible.
4. **As a foreman**, I can turn a human-approved Auger plan into canonical boardctl rows and reconcile their status, so Auger never becomes a competing board or dispatcher.
5. **As a contributor**, I can enable GitReins’ spec-alignment flag and get bounded staged-change feedback before commit, so a missing or wrong `ch:trace` link is caught while it is cheap to fix.

## Proposed design

### 1. XML contract model

Define a versioned schema (XSD or equivalently strict schema validator; the implementation task must justify the choice) for document metadata and typed blocks. Minimum conceptual form:

```xml
<spec id="AUGER-ARCH" version="1.0.0" xmlns="urn:auger:spec:1">
  <section id="ARCH-DECISION-STORE" kind="design-decision" status="active">
    <requirement id="AUG-REQ-014" applies-to="auger.export" verification="test">
      The default export remains deterministic and read-only.
    </requirement>
    <acceptance id="AUG-AC-014-1" verifies="AUG-REQ-014" method="test">
      Running the export twice over the same fixture produces byte-identical output.
    </acceptance>
  </section>
</spec>
```

Each block has a globally unique, stable `id`; IDs are not renumbered when wording changes. Version, `kind`, `status`, `applies-to`, and verification metadata are explicit attributes where applicable. Define typed references (`ref`, `implements`, `verifies`, `supersedes`, `depends-on`) with resolvable targets. XML IDs and relationship edges form the queryable graph; visible prose does not substitute for edges.

The schema must define whether deprecation/supersession retains old blocks, how version compatibility works, whether references cross repository boundaries, and how an applicability selector matches. The initial version should support local repository files and explicitly reject unsupported remote references rather than fetching arbitrary URLs.

### 2. Selection and assembly

Provide one stable Auger interface to:

- validate documents against the schema and repository-wide ID/reference constraints;
- select by exact ID, `kind`, `applies-to`, `status`, `version`, and declared relation;
- expand only explicit references/dependency closure;
- assemble a deterministic bundle with source file, source ID, source version, and relationship provenance for each block;
- render that selected bundle into Markdown (and later other views) without changing the XML source.

The bundle must preserve identity and order. Selection is explicit and inspectable: default behavior never silently includes all files, silently drops unresolved references, or silently chooses one of several matching versions. Duplicate IDs, broken references, include cycles, invalid methods, and incompatible schema versions fail before emitting a partial artifact. Avoid general-purpose XInclude or arbitrary entity expansion unless the security review shows it is bounded and local-only; external entity/network access must be disabled.

A caller can ask, for example, for all active requirements applying to `auger.export`, plus their linked acceptance criteria and verification blocks. The result contains precisely that selection and dependency closure—not a full Markdown dump.

### 3. Relationship and verification graph

Use stable XML IDs and typed edges to connect:

`PRD statement → requirement → acceptance criterion → board row → code path/change → test → GitReins verdict → commit`.

A requirement is not “verified” because an XML attribute says so. The attribute declares the expected method; a verification edge points to the concrete test, demonstration, analysis, or inspection evidence. A verdict must bind to the commit being evaluated. Missing evidence is represented as missing, not green.

`ch:trace` follows the loaded fleet doctrine. At minimum, the checker validates the required `row=` field, resolvable spec/test/evidence pointers, explicit `none:<reason>` values for absent links, evidence that is an artifact or witness rather than another row, and verdict-to-commit binding. The full eleven-field marker remains the closeout contract: `row`, `spec`, `wave`, `test`, `doc`, `prompt`, `evidence`, `witness`, `verdict`, `commit`, `memory`.

### 4. Boardctl and approval boundary

Auger may draft a plan from selected XML requirements and verification blocks. It must not write canonical tasks until a person approves the exact plan version/digest. After approval, Auger uses the supported boardctl interface to create/update rows, then reads the exact rows back and checks that IDs, acceptance criteria, dependencies, and spec anchors match the approved plan. Re-export is idempotent. On partial failure, it reports which rows were written and resumes without duplicating them.

Boardctl remains the only canonical task writer; the JSONL board remains the store; the existing scheduler remains dispatch authority. Auger does not create a second task database, directly dispatch workers, or mark tasks complete from its own inference.

### 5. GitReins pre-commit alignment check

GitReins owns the commit gate and its existing guard/evaluation backend. Add an explicit opt-in feature flag (proposed config key: `spec_alignment.enabled`, exact public spelling to be decided in implementation) and an explicit policy mode (`warn` or `block`) with a bounded timeout. Disabled mode is byte-for-byte behavior-compatible with current commits.

When enabled, the hook passes the staged diff and repository/spec root to Auger’s documented machine-readable checker. The checker returns JSON findings with severity, relevant spec IDs, source locations, `ch:trace` violations, reason, and confidence/uncertainty. GitReins renders the result and applies the configured policy. The pre-commit path only examines staged content; it does not alter the working tree, spec files, or board. No orphaned asynchronous check may outlive the hook. If an implementation runs a check in a background subprocess for responsiveness, it must be bounded and joined before the commit decision.

Deterministic checks (schema, references, missing trace fields, unresolved evidence IDs) may block when configured. Semantic claims such as “this code contradicts the requirement” must include evidence and a calibrated uncertainty outcome; the check must not invent missing rationale or auto-edit code/spec text. Define timeout, missing Auger binary, and checker error behavior explicitly for each mode. Reuse GitReins’ existing review/evaluator path rather than adding another LLM provider or judge service.

## Core loops and closure rules

1. **Authoring loop:** edit XML → validate schema/IDs/edges → run focused assembly → review rendered view. Closed when validation passes, references resolve, and generated views are reproducible. A recurrence becomes a validator regression test.
2. **Plan-to-board loop:** assemble approved requirement set → draft board tasks → human approves plan digest → boardctl writes → read back exact rows. Closed only when row criteria and links match the approved plan; failed/partial writes remain open and retry idempotently.
3. **Change-to-spec loop:** staged diff → resolve explicit trace links and affected spec blocks → run deterministic and semantic checks → apply GitReins policy → commit or report. Closed only when blocking findings are resolved or explicitly acknowledged according to policy and the final verdict binds to the commit. Repeated mismatch patterns become a rule/test, not repeated warnings.
4. **Verification loop:** requirement declares method → test/analysis/demo/inspection executes → artifact is recorded → verdict binds artifact and commit → spec bundle reflects verified state. A method declaration alone never closes the loop.

## Taxonomy of findings

- **Structure:** malformed XML, unsupported schema version, duplicate stable ID, unresolved or cyclic reference.
- **Selection:** no match, ambiguous match, stale/deprecated target, or dependency expansion outside declared scope.
- **Traceability:** missing `row=`; broken `spec` or `test` target; unexplained absent field; `evidence` pointing to another task/verdict; verdict not bound to the staged/final commit.
- **Content alignment:** changed behavior has no related requirement; wrong section is cited; comment or documentation asserts behavior inconsistent with selected spec; requirement changed without an intentional version/supersession record.
- **Verification:** required check absent, ran against another commit, failed, or result cannot be read back.
- **Integration/runtime:** Auger unavailable, schema incompatibility, boardctl partial failure, timeout, GitReins policy disabled, or output parse failure.

Every finding names the class, rule, source and target IDs, evidence, and the reason it is actionable. “No related spec” is an explicit unscoped result, not an automatic pass.

## Requirements

- **R1 — XML source:** Formal spec content and typed relationships shall be authored in versioned XML; Markdown/HTML outputs are generated views, never independently authoritative copies.
- **R2 — Stable identity:** Every document and reusable block shall have a unique stable ID; IDs shall not be recycled or silently renumbered.
- **R3 — Typed metadata:** Requirements, PRD lines, acceptance criteria, verification plans, and implementation notes shall have typed `kind`, applicability, lifecycle/version, and verification metadata where relevant.
- **R4 — Cross-document reuse:** A block shall be defined once and referenced from multiple documents by an explicit stable ID; resolution shall preserve source identity and detect duplicate IDs, dangling references, cycles, and invalid versions.
- **R5 — Targeted retrieval:** The CLI shall select by stable ID and typed attributes and assemble only the selected blocks plus explicitly requested relation closure, with provenance for every block.
- **R6 — Deterministic rendering:** Identical XML and selector inputs shall produce byte-identical assembled output; Markdown rendering shall be deterministic and read-only.
- **R7 — Safe parsing:** External entity expansion and implicit network fetches shall be disabled; invalid input shall fail before partial output.
- **R8 — Decision-store boundary:** Auger’s existing DuckBrain tables shall remain authoritative for decision records; XML shall reference stable record IDs rather than silently shadowing mutable row data.
- **R9 — Approval-bound board write:** Auger shall use boardctl as the sole canonical task writer and require explicit human approval bound to the exact plan digest before creating/updating tasks.
- **R10 — Trace graph:** The checker shall validate `ch:trace` fields and resolution of row/spec/test/evidence/verdict/commit links according to the loaded traceability doctrine; missing values shall carry explicit `none:<reason>`.
- **R11 — Staged-change checker:** The Auger checker shall accept staged diff + repository/spec root, return machine-readable findings with source evidence, and shall not mutate code, XML, or board state.
- **R12 — GitReins integration:** GitReins shall expose an opt-in, bounded spec-alignment guard that reuses the Auger checker and GitReins’ existing policy/evaluator; disabled behavior shall remain unchanged.
- **R13 — Semantic honesty:** Semantic contradiction findings shall include evidence and uncertainty; no checker shall fabricate a rationale, claim verification from metadata alone, or auto-edit source text.

## Acceptance criteria

- **AC-1 (T):** Given a valid XML fixture with two files that share a referenced block, when `auger spec validate` runs, then it returns success and resolves both files to the same stable block ID; proof: `pytest -q tests/test_xml_specs.py::test_shared_block_reference`.
- **AC-2 (T):** Given duplicate IDs, a dangling reference, an include cycle, an external entity, an unsupported schema version, or an invalid verification method, when validation/assembly runs, then each fixture returns a typed non-zero failure and no partial output; proof: all negative cases in `tests/test_xml_specs.py`.
- **AC-3 (T):** Given a selector for `kind=requirement` and `applies-to=auger.export`, when assembly runs across at least two XML files, then output contains only matching requirements plus explicit linked AC/verification dependencies, each with source ID/file/version; proof: golden output in `tests/fixtures/xml/expected/`.
- **AC-4 (T):** Given unchanged XML and the same selector, when assembly runs twice, then output bytes and provenance order are identical; proof: deterministic golden test and repeated SHA-256 equality.
- **AC-5 (T):** Given a source block referenced by two generated documents, when that source block changes and views are regenerated, then both views reflect the change and neither contains an independently edited stale copy; proof: multi-view regeneration fixture plus source-only mutation test.
- **AC-6 (T):** Given a board plan that has not been approved for its current digest, when export-to-board is attempted, then Auger refuses and `tasks.jsonl` and `events.jsonl` remain byte-identical; proof: boardctl integration test comparing both files before/after.
- **AC-7 (T):** Given an approved plan digest and existing DuckBrain decision records, when Auger creates board rows and repeats the same export, then exactly one row per task ID exists, read-back acceptance/dependency/spec fields match the approved plan, and the board stores only stable decision references (not a shadow copy of decision state); proof: ephemeral DuckBrain + boardctl fixture with duplicate-count and no-shadow assertions.
- **AC-8 (T):** Given an aligned staged diff with complete valid `ch:trace`, when the Auger checker runs, then JSON output is `pass` with resolved spec IDs and artifact provenance; proof: `tests/test_spec_alignment.py::test_aligned_change_passes`.
- **AC-9 (T):** Given missing `row=`, an unresolved spec ID, an evidence pointer to another board row, an unexplained absent field, or a verdict bound to another commit, when the checker runs, then each is reported as a distinct finding with rule, location, and reason; proof: dedicated negative fixture cases.
- **AC-10 (T):** Given GitReins with the flag disabled, when a staged commit is checked, then the existing guard result and hook behavior remain unchanged; proof: existing hook test plus disabled-config regression asserting no Auger invocation.
- **AC-11 (T):** Given GitReins with the flag enabled, when staged diff is aligned, warning-only, or blocking, then configured `warn`/`block` policy is applied exactly and only staged content is passed to Auger; proof: GitReins integration tests for each outcome plus unstaged sentinel assertion.
- **AC-12 (T):** Given Auger missing, incompatible, timing out, returning malformed JSON, or exiting unexpectedly, when the hook runs in each policy mode, then it produces the documented bounded result and never leaves an orphan check process; proof: subprocess fault-injection suite with process-liveness assertion.
- **AC-13 (T):** Given a source comment/docs file in the wrong spec section or contradicting its selected requirement, when semantic review is enabled, then the finding includes both source locations, evidence excerpt/digest, and uncertainty; no code or text is auto-edited; proof: paired positive/negative fixtures and unchanged-tree hash check.
- **AC-14 (C):** Given a representative Auger project selector, when `auger spec assemble` runs, then it reports selected-block count, dependency count, input bytes, output bytes, and elapsed time; proof: checked-in benchmark fixture produces a JSON report and reads no unselected document bodies beyond the index needed for selection.
- **AC-15 (M):** Given the feature complete, when an external reviewer follows a sample `ch:trace` from requirement through board row, code, test, verdict and commit, then every link resolves and the verdict is reachable from the published tip; proof: sample end-to-end trace artifact in `docs/prd/evidence/`.

**Requirement-to-acceptance coverage:** R1→AC-2, AC-5; R2→AC-1, AC-2; R3→AC-1, AC-3; R4→AC-1, AC-2, AC-3, AC-5; R5→AC-3, AC-14; R6→AC-4, AC-5; R7→AC-2; R8→AC-6, AC-7; R9→AC-6, AC-7; R10→AC-8, AC-9, AC-15; R11→AC-8, AC-9; R12→AC-10, AC-11, AC-12; R13→AC-12, AC-13.

## Success criteria — replayable proofs

- Replay the current board-to-doc mismatch class: select a board task and retrieve its linked requirement/AC by ID rather than searching all 32+ docs; the report shows exact source anchors and payload bytes.
- Replay a shared-block change: one XML source edit updates every generated spec view; the test proves no duplicate source text is maintained.
- Replay an intentionally missing `ch:trace row=` in a staged fixture: the checker names the missing field and correct source location; GitReins blocks only when configured `block`.
- Replay a valid, approved plan: boardctl writes exactly one canonical row per approved task and the read-back proves the criteria and spec links survived.
- Report current `docs/` corpus size, selected-bundle size, and lookup latency from the same reproducible fixture. Target: selected bundle excludes unrelated blocks and is materially smaller than full-corpus input; do not claim savings without reporting actual measured bytes.
- Demonstrate disabled GitReins flag behavior is unchanged and the enabled path creates no unbounded or orphan process.

## Scope

**In scope:** XML schema/versioning; stable IDs and metadata; modular block references; attribute selectors; deterministic cross-file assembly; generated Markdown view; verification/trace relationship model; boardctl plan export after explicit approval; read-only staged-diff checker; opt-in GitReins flag and policy; skills/playbook, docs, fixtures, CI tests, and traceability evidence.

**Out of scope:** replacing DuckBrain as Auger’s decision store; replacing boardctl or the scheduler; automatic task dispatch or completion; silently mutating specs/code to “fix” a finding; arbitrary URL/entity expansion; mandatory GitReins enablement in all repositories; migrating every existing Markdown doc in one wave; a second LLM evaluator/backend; treating an unverified claim as a passing verdict.

## Boundaries and source authority

- XML source files own formal requirements, criteria, and typed cross-document links.
- Markdown/HTML are rendered views, not a second hand-edited source of truth.
- Existing Auger tables own decision/option/unknown/verdict records; XML references those stable IDs and does not duplicate live row state without provenance.
- boardctl owns canonical task writes; scheduler owns dispatch; GitReins owns pre-commit policy and verdict execution.
- Auger’s checker is read-only against staged code, XML, and board data.
- A human approves the exact plan digest before task creation; no implied consent from a prior approval.
- Every absent trace field is `none:<reason>`; no blanket “complete” status is emitted when evidence is missing.

## Proposed sequencing and owners

1. **AUG-118 — XML spec graph and selection.** Agree the smallest schema, IDs, references, selectors, and rendering contract; then implement validator/assembler and fixtures.
2. **AUG-120 — Auger spec-alignment interface.** Build after the XML graph contract is stable; provide deterministic JSON, `ch:trace` rules, and staged-diff fixtures.
3. **AUG-119 — approved boardctl plan lifecycle.** Implement with digest-bound approval, boardctl-only writes, idempotency, and read-back. This can proceed alongside schema work after task/spec ID mapping is agreed.
4. **GR-154 — GitReins hook flag.** Implement against AUG-120’s published interface; preserve disabled behavior, bound runtime, test warn/block and infrastructure failures.

Cross-repo acceptance must be evaluated against the exact Auger checker version and GitReins commit. The `ch:trace` graph itself requires row → spec → wave → test → evidence → witness → verdict → commit → memory, with all eleven marker fields present or explicitly absent by reason.

## Risks and mitigations

- **XML becomes more verbose than it saves.** Keep the syntax typed and machine-selected; benchmark against real queries, and render Markdown for reading. Do not claim XML is cheaper to author.
- **Reference semantics become a second programming language.** Limit v1 to stable local IDs, a small typed relation set, deterministic selectors, and a strict schema; reject ambiguity.
- **Circular references or entity expansion create security/performance issues.** Disable external entities/network reads, detect cycles, bound expansion, and test malicious fixtures.
- **The checker claims semantic certainty it cannot prove.** Separate deterministic integrity errors from semantic warnings; cite evidence and uncertainty; require a configured policy for blocking.
- **Git hooks increase commit latency or strand work.** Keep the feature opt-in, staged-only, bounded, with explicit missing-checker/timeout policy and no process surviving the hook.
- **Board write race or duplicate tasks.** Human approval binds to a plan digest; use boardctl; make exports idempotent; read back exact targets; no direct JSONL writer.
- **Legacy Markdown migration becomes a big-bang blocker.** Start with the new/changed spec slices and adapters; migrate incrementally, maintaining a declared coverage inventory and explicit unconverted status.
- **Trace fields become paperwork.** Validate against real artifacts and commit-bound verdicts; an external reviewer, not the author, checks the final chain.

## Cost and shape

**Effort:** Large, cross-repository. Auger owns XML grammar, source graph, selection/assembly, boardctl approval lifecycle, and checker API. GitReins owns only its configurable hook integration and policy. The first usable slice is XML schema + cross-file selection + deterministic Markdown assembly; do not begin with an LLM reviewer.

**Dependencies:** Auger tasks `AUG-118`, `AUG-119`, `AUG-120`; GitReins task `GR-154`; boardctl CLI/API; `docs/traceability-doctrine.md`; `coding-hermes-traceability` skill. Task states are pending; no implementation is claimed by this PRD.

## Why other Auger users want it

- Contributors can read a generated Markdown document but still point tools at exact requirement/verification IDs.
- Foremen can turn approved plans into canonical board work without copying acceptance criteria by hand.
- GitReins can offer a spec-aware gate without growing a second evaluator backend.
- Reviewers can verify that a claim is backed by the right spec, test, verdict, and commit instead of trusting prose.
- Existing Markdown readers remain supported through generated views; adoption can be incremental.

## Open decisions to settle before implementation

1. **Canonical XML placement:** project-level `specs/` directory or the current docs-first layout? Default proposal: `specs/` for authored XML, `docs/` for generated/readable views.
2. **Schema validator:** XSD versus Relax NG versus an in-repo strict parser. Choose based on editor support, Python dependency policy, and exact validation requirements.
3. **Selection language:** fixed flags (`--id`, `--kind`, `--applies-to`, `--status`) versus a small query expression. Default proposal: fixed typed flags first; no general XPath execution in v1.
4. **Reference closure:** which edges are followed automatically? Default proposal: only explicit `include`/`depends-on` references; `implements`/`verifies` are navigable but do not silently expand.
5. **Semantic review policy:** can semantic contradiction findings ever block commits, or are only deterministic contract failures blocking by default? Default proposal: semantic findings warn until calibrated on a labeled replay set.
6. **Approval UX:** what exact human action signs a plan digest before boardctl write? Default proposal: explicit CLI `approve --digest <sha>` with identity recorded in an append-only plan ledger.

## ch:trace

`ch:trace row=AUG-118 spec=docs/prd/xml-spec-graph.md#proposed-design wave=none:prd-only test=none:prd-not-implemented doc=docs/prd/xml-spec-graph.md prompt=none:not-dispatched evidence=none:prd-not-implementation-evidence witness=none:proposal-only verdict=none:not-judged commit=none:not-implemented memory=/project/auger/integration/xml-boardctl-gitreins-2026-10-29`
