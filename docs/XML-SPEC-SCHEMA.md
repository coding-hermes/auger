# XML spec graph schema (AUG-118 / AUG-120 / AUG-121 / AUG-122 / AUG-123 / AUG-125 / AUG-127 / AUG-129 / AUG-133 / AUG-135)

Auger XML specs are rooted at `<spec id="...">` in the `urn:auger:spec:1` namespace, carry `version` and `schema-version`, and every graph node has a stable, repository-unique `id`. This implementation recognizes `<requirement>`, `<verification>`, and `<test>` nodes — all three are block kinds in the published grammar, and `<test>` is the shape that carries a machine-checkable obligation. A requirement's whitespace-separated `verifies` attribute names one or more verification IDs; `<verification-ref ref="..."/>` is an equivalent explicit edge. A verification may also name its requirement with `verifies="REQ-ID"`. References must resolve to the expected node type.

## The grammar is loaded, and the processor is an extra

`specs/schema/auger-spec-1.0.xsd` is loaded by a real XSD 1.0 processor on every `auger spec` verb,
and a document it rejects is refused (`spec error (schema-invalid)`). Two properties make that a
gate rather than decoration:

* **XSD 1.0 only.** No `(?:`, no lookahead, no Python escapes in a facet — one of those makes the
  whole schema unloadable, so the CLI refuses with `xsd-schema-unloadable` instead of grading a
  corpus against a grammar it could not load.
* **No processor, no verdict.** `lxml` is deliberately NOT a runtime dependency: `pyproject.toml`
  keeps `dependencies = []` and ships it in the `dev` extra and in an optional `auger[xsd]` extra
  (owner decision D-1). `auger.py` imports it lazily; a host without it gets
  `spec error (xsd-processor-missing)` and exit 2. Install it with
  `pip install -e '.[dev]'` (contributors) or `pip install 'auger[xsd]'` (schema-aware use).

The code's rules run first (their diagnostics name the offending attribute pair, path or ID) and the
schema is the backstop for everything the code does not model — an undeclared attribute, an unknown
element, a value outside a facet. Every attribute the code reads on a block is declared on the
schema's block type, and `tests/test_xsd_conformance.py` derives that inventory from `auger.py`'s AST
so the two cannot drift again.

## Installed layout — where the grammar lives after `pip install` (AUG-129)

`auger.py` is a single module (`py-modules = ["auger"]`), so a wheel has no package directory to hang
`package-data` on and the grammar travels as a `[tool.setuptools.data-files]` entry instead.
setuptools installs data files under the interpreter's `data` scheme, which puts the same file in a
different place depending on how auger was installed:

| layout | path |
| --- | --- |
| source checkout | `specs/schema/auger-spec-1.0.xsd`, beside `auger.py` |
| inside the wheel | `auger-<version>.data/data/share/auger/specs/schema/auger-spec-1.0.xsd` |
| installed (venv or system prefix) | `<prefix>/share/auger/specs/schema/auger-spec-1.0.xsd` |
| installed with `pip install --user` | `<user base>/share/auger/specs/schema/auger-spec-1.0.xsd` |

`auger.py` resolves those candidates in that order (`spec_schema_candidates()` /
`spec_schema_path()`), and refuses with `spec error (xsd-schema-missing)` naming **every** candidate
when none of them holds a file. Shipping the grammar therefore did not soften the fail-closed rule
above: an install whose data files went missing still gets no verdict, never a green one.

A minimal example corpus ships beside the grammar (`<prefix>/share/auger/specs/examples/minimal.xml`,
authored in this repository at `examples/xml/minimal.xml`), so a fresh install has something real to
validate without a git checkout:

```bash
python3 -m venv /tmp/auger-fresh && /tmp/auger-fresh/bin/pip install 'auger[xsd]'
EXAMPLES="$(/tmp/auger-fresh/bin/python -c 'import pathlib,sysconfig;print(pathlib.Path(sysconfig.get_path("data"))/"share/auger/specs/examples")')"
/tmp/auger-fresh/bin/auger spec validate "$EXAMPLES" --repo-root "$(git rev-parse --show-toplevel)"
# spec: 1 document(s), 2 block(s), 1 reference(s) ok
```

`--repo-root` is explicit by design (AUG-127): the repository that `path`/`migration`/`fixture`
values are relative to is never guessed, so the command names one. The example declares no such
obligations at all, and the `auger[xsd]` extra carries the processor for the reason given above.
`tests/test_packaging_wheel.py` is the guard on this section: it builds the wheel from the declared
`data-files`, asserts the schema and example members byte for byte, and replays the command above
inside a throwaway venv installed offline from that wheel.

ch:trace row=AUG-129 spec=docs/XML-SPEC-SCHEMA.md#installed-layout test=tests/test_packaging_wheel.py doc=docs/XML-SPEC-SCHEMA.md

Example migration obligation — the shape `spec validate` and `spec check` both read (one loader reads
the root and the blocks for both). The paths below are illustrative: a corpus is only valid if they
exist in the repository and are tracked in git, and `tests/fixtures/xml/verification/migration.xml` is
the same shape with paths that resolve.

```xml
<spec xmlns="urn:auger:spec:1" id="EXAMPLE" version="1.0.0" schema-version="1.0">
  <requirement id="REQ-MIG-1" kind="requirement" status="active" version="1.0.0"
               verification="test" verifies="TEST-MIG-1">Add a marker row.</requirement>
  <verification id="TEST-MIG-1" kind="verification" status="active" version="1.0.0"
      method="test" verifies="REQ-MIG-1"
      runner="pytest" path="tests/test_migrations.py"
      selector="test_marker_migration"
      migration="migrations/001_marker.sql"
      fixture="tests/fixtures/prior_schema.sql"
      assertions="marker table exists; version is 1" />
</spec>
```

For automated tests, `runner` is an identifier in Auger's repository harness registry, not a command. The current registered identifier is `pytest`, serialized to an argv vector (`<current Python> -m pytest`). `path` and `selector` are tracked repository-relative test path and Python test function name. Validation confirms both exist and remain inside the repository. No shell string or arbitrary command is accepted or executed.

## The repository root

`path`, `migration` and `fixture` are **repository-relative**, and the repository is the one
that CONSUMES the spec — not Auger's own checkout. It is chosen explicitly, in this order:

1. `--repo-root <DIR>` on `auger spec validate`, `auger spec check` and `auger spec bundle`
   (from Python: the `repository_root=` keyword of `validate_xml_specs` /
   `assemble_xml_requirement`). It must BE a git work tree's top level: a subdirectory or a
   non-repository is refused, because "repository-relative" has to mean one thing.
2. Otherwise `git rev-parse --show-toplevel` resolved FROM THE SPEC ROOT — what a corpus
   committed inside the repository it describes gets for free.
3. Otherwise the run is refused (`unresolvable-repo-root`). There is no fallback to Auger's
   own directory; a corpus with no root is not graded.

Use `--repo-root` when the corpus does not live inside the repository it describes (a contract
drafted in a reports tree that names tests in a source repository, or the same corpus checked
from several repositories).

Before AUG-127 the root was the directory of the INSTALLED MODULE, so every obligation was
resolved, `git ls-files`-checked and AST-verified inside Auger's own tree. A consumer repository
holding a real, git-tracked `tests/test_marker.py` and a spec naming it was refused
`stale-test-ref ... (resolved <auger>/tests/test_marker.py)` — a file that exists nowhere in
Auger's tree and has nothing to do with the consumer.

ch:trace row=AUG-127 spec=docs/XML-SPEC-SCHEMA.md#the-repository-root test=tests/test_spec_repository_root.py doc=docs/XML-SPEC-SCHEMA.md

ch:trace row=AUG-123 spec=docs/prd/xml-spec-graph.md#R15 test=tests/test_verification_graph.py doc=docs/XML-SPEC-SCHEMA.md

Migration obligations require the exact repository-relative migration path, supported prior-schema fixture, tracked test path/selector, and explicit assertions. The selected requirement bundle carries those values verbatim. The test obligation itself must load and apply the named migration; a test that merely runs a package suite is insufficient. The XML validator checks that migration and fixture paths exist, while the regression test proves application behavior, including failure against a no-op migration.

Validation reports typed `SpecValidationError` identifiers including `duplicate-id`, `dangling-test-ref`, `dangling-requirement-ref`, `wrong-ref-type`, `invalid-test-ref`, `stale-test-ref`, `escaping-test-ref` (a path that leaves the repository root), `untracked-test-ref` (a file that exists but is not in git), `stale-migration-ref`, `stale-fixture-ref`, `incomplete-migration-obligation`, and `unresolvable-repo-root` (no repository to resolve in). Use:

```text
auger spec validate <spec-root> [--repo-root DIR]        # the whole verdict: block corpus + every obligation
auger spec check    <spec-root> [--repo-root DIR]        # the same verdict, obligation node ids as JSON
auger spec bundle   <spec-root> <requirement-id> [--repo-root DIR]   # one requirement's verification bundle as JSON
auger spec list     <spec-root>                          # blocks with kind, status, version, source
auger spec assemble <spec-root> --id <block-id>          # block selection + explicit dependency closure
auger spec assemble <spec-root> --id <block-id> --format json --measure   # + the measured report (AC-14)
auger spec render   <spec-root> --out <dir>              # every generated view (Markdown + HTML, AUG-139)
```

### The measured assembly report (AUG-140, PRD AC-14)

`assemble --format json --measure` adds a report **beside** the keys that were already there
(`schema_version`, `selection`, `counts`, `blocks`, `corpus`):

| key | what it measures |
| --- | --- |
| `report_schema_version` | the REPORT's own shape version — `1.1` today. Not the corpus `schema_version`: one describes the XML grammar, the other the JSON a consumer parses, and they change for different reasons. |
| `input_bytes` | `os.stat` sum of the corpus documents that were READ (every document is read to build the selection index). |
| `output_bytes` | the length of the emitted document itself, in bytes, settled to a fixpoint — the field is part of the bytes it measures, so the run re-serializes until the number and the length agree (a report that cannot settle refuses `unstable-report`). |
| `documents_read` | documents whose bytes were read. |
| `blocks_indexed` | blocks whose identity, lifecycle, method and typed edges were read. |
| `blocks_parsed` | blocks whose **body** (prose) was materialized: the matched blocks plus their explicit closure, **plus** any body the index pass itself extracted (zero by construction — the loader counts it, so a regression that re-introduces a full-corpus parse shows up as a number). This is what makes AC-14's "reads no unselected document bodies beyond the index needed for selection" measurable: `blocks_parsed < blocks_indexed` on any corpus larger than its selection. |
| `elapsed_ms` | wall-clock time around the real assembly (loader read → selection → first render), read through `auger._spec_clock`. |

Two consequences worth stating, because both are deliberate:

* **It is opt-in.** PRD AC-4 and `tests/test_xml_specs.py::test_selection_is_byte_identical_across_runs`
  pin two runs of the same `assemble --format json` to *byte-identical* output. `elapsed_ms` is a wall
  clock, so a report in the default document would make that contract unkeepable. Without `--measure`
  the document is byte-for-byte what it always was.
* **Corpus-wide verbs stay eager.** `validate`, `check`, `bundle`, `task-packet`, `plan-*` and the
  judge packet keep `load_spec_corpus`, which materializes every block body — they adjudicate the whole
  corpus, so laziness there would be a missing proof, not a saving. Only the selection-scoped
  `list`/`assemble` use `load_spec_corpus_indexed`, and `tests/test_spec_assembly_report.py` pins that
  the indexed path produces exactly the blocks the full loader produces.

`spec validate` and `spec check` are one verdict from one loader. The corpus root, the
`urn:auger:spec:1` namespace, the schema version, the document metadata and the
block/reference grammar are read once by `load_spec_corpus`; the obligation pass then resolves
every test obligation (tracked path, selector, migration, prior-schema fixture, assertions)
over those SAME documents. The verbs differ only in what they print. Before AUG-134 each verb
parsed the corpus itself and they disagreed — `validate` accepted a namespaced root that
`check` refused as `invalid-root`; before AUG-132 `validate` resolved no obligation at all, so
a corpus whose verification block named `tests/does-not-exist.py` reported `ok` with exit 0.
A block that only declares `type="test"`/`method="test"` states a METHOD (the authoring
grammar's `verification=`/`method=` shape), not a test location; an obligation is a block that
actually points at a test with `runner`/`selector`/`path`.

### What the obligation check keys on (AUG-150)

Two different checks read these blocks, and confusing them is how a corpus can look proven while
nothing is resolved:

| check | keys on | refuses with |
| --- | --- | --- |
| block KIND | the ELEMENT NAME (`<requirement>`, `<test>`, …) plus `kind=` matching it | `kind-mismatch` |
| obligation | a `<test>` block, or ANY block carrying a `runner`/`path`/`selector` POINTER | `invalid-test-ref`, `stale-test-ref`, `untracked-test-ref`, `escaping-test-ref` |
| method vocabulary | the VALUE of `type=` and/or `method=`, whichever are present | `invalid-verification-method` / `invalid-verification-type` |

Consequences, each pinned by `tests/test_spec_obligation_gate.py`:

* **Both spellings are accepted and both are checked.** `type="test"` and `method="test"` are two
  spellings of the METHOD vocabulary; a citation carrying either, or *neither*, is graded, because
  the POINTER is what makes it a citation. A fixture pair that differs only in which attribute
  carries the word `test`, plus a third variant carrying neither, is refused by both `spec
  validate` and `spec check` when its cited path does not exist — naming the document, the block
  and the resolved path.
* **`type="test"` is not required.** It is recommended (it says out loud how the obligation is
  meant to be verified, and an editor validates it against the grammar), but stripping it from
  every block of the authored `specs/` corpus leaves the corpus valid, so no author needs it as a
  workaround. The authored corpus keeps it; the proof that it is optional lives in the test.
* **A method-only block is not over-blocked.** `method="test"` (or `demo`, `analysis`,
  `inspection`) with no pointer is a verification PLAN, and it validates. Only a block that names
  a test location is resolved.

Assembly is deterministic JSON data and does not invoke tests. Consumers may pass the returned registered runner argv and tracked selector to the repository harness under their own policy; XML never executes code.

## Decision records: the ledger edge (AUG-138)

A block can state that it RESTS on a recorded decision, and the reference is checked. DuckBrain
stays authoritative for decisions; the corpus carries only the typed edge.

```xml
<requirement id="REQ-CACHE-1" kind="requirement" status="active" version="1.0.0"
             verification="test"
             decision-ref="D-014">
  Sessions are cached in the substrate, per decision D-014.
</requirement>
```

* `decision-ref` holds whitespace-separated decision RECORD ids (`D-014`, `DER-2` — the same
  stable-id shape a block id has, enforced by the grammar's `idRefList` facet).
* The ids resolve in a DuckBrain **namespace the caller names**: `--decision-namespace <ns>` on
  `spec validate`, `spec check` and `spec bundle` (from Python: the `decision_namespace=` keyword
  of `validate_xml_specs` / `assemble_xml_requirement`). A record id is namespace-scoped — a
  sibling project's `D-014` is a different decision — so the namespace is never guessed:
  `missing-decision-namespace` refuses instead.
* The lookup is **opt-in by the attribute**. A corpus that declares no `decision-ref` requires no
  namespace, makes no request against the substrate, and behaves exactly as it did before.
* This edge resolves OUTSIDE the corpus. It is a typed edge in the authoring vocabulary
  (`SPEC_EDGE_ATTRS`) but the corpus-resolving passes skip it by name, so a real record is never
  reported as `unresolved-reference`, and it never expands an assembly closure.

Refusals, each naming the document and the block id (and the record, or the successor):

| code | meaning |
| --- | --- |
| `missing-decision-namespace` | a `decision-ref` is declared and no `--decision-namespace` was given |
| `unresolved-decision-ref` | the namespace answered and holds no record with that id |
| `superseded-decision-ref` | the record exists with `status="superseded"` — reported with its successor when the namespace holds the supersedes edge (`auger answer --supersedes`) |
| `decision-ledger-unavailable` | the substrate could not answer: unreachable transport, no token, an auth refusal, or a namespace whose tables were never declared (`auger -n <ns> init`) |

"A lookup that could not run" and "the record does not exist" are deliberately different codes: the
first is an operational failure with no verdict, the second is a finding about the corpus.

**The read path never writes.** Every request is a `GET` of the namespace's `decision` table
filtered to one id; `validate`, `check` and `bundle` issue no mutating request and leave the corpus
tree byte-identical (`tests/test_spec_decision_ref.py`).

ch:trace row=AUG-138 spec=docs/XML-SPEC-SCHEMA.md#decision-records-the-ledger-edge test=tests/test_spec_decision_ref.py doc=docs/XML-SPEC-SCHEMA.md

## Rollback declarations (AUG-143)

A verification or test block declares what undoing its change costs. Without it the corpus says
nothing and a consumer assumes — the PRD requires support to be *tested* when declared, and requires
the absence of support to be *explicit*:

```xml
<verification id="REQ-MIG-1-ROLLBACK" kind="verification" status="active" version="1.0.0"
              method="test" rollback="tracked-test"
              runner="pytest" path="tests/test_migrations.py"
              selector="test_marker_migration_rolls_back">
  The down migration is applied from the post-migration schema and asserted.
</verification>

<verification id="REQ-READ-ONLY-1" kind="verification" status="active" version="1.0.0"
              method="inspection" rollback="unsupported">
  A read-only validation pass: there is no write to roll back.
</verification>
```

| value | meaning | refused when |
| --- | --- | --- |
| `unsupported` | the change is not rolled back — stated, not assumed | never (this is the honest "nothing to undo") |
| `tracked-test` | rollback IS exercised, and the proof is this block's own `runner`/`path`/`selector` | the block carries no such pointer (`rollback-undeclared-proof`) |

* The value set is closed in BOTH halves — `SPEC_ROLLBACK_VALUES` in the code and the
  `rollbackDeclaration` enumeration in the loaded grammar — so a free-text value ("best-effort")
  is refused with the code's typed `invalid-rollback-declaration`, naming the document, the block
  and the offending value (the code's rules run before the schema, AUG-125 ordering).
* A `tracked-test` claim goes through the SAME resolution every other test citation gets: the path
  must exist, be tracked in `git`, and define the selector in `--repo-root`. Declaring support is
  therefore exactly as strong as naming the test that proves it.
* **The declaration is readable, not inferable.** `spec check` reports it as a `rollback` map
  (`{"<block-id>": "unsupported" | "tracked-test"}`; always present, empty when the corpus declares
  none), and the generated `docs/SPEC-CORPUS-VIEW.md` renders one `- rollback:` line per declaring
  block.

ch:trace row=AUG-143 spec=docs/XML-SPEC-SCHEMA.md#rollback-declarations test=tests/test_spec_rollback.py doc=docs/XML-SPEC-SCHEMA.md

## Task scope and the one-query context (AUG-121 / AUG-122)

A **task is a block**, not a prose row: `<task>` is a `blockKind` like `requirement`, so it carries
stable identity, lifecycle and a mandatory verification method, and its scope is a set of typed ID
edges. `taskType` extends `blockType`, so the scope attributes cannot be written on any other kind.

```xml
<task id="AUG-121" kind="task" status="active" version="1.0.0" verification="test"
      in-scope="AUG-REQ-1 AUG-AC-1" acceptance="AUG-AC-1" out-of-scope="AUG-REQ-9"
      acknowledges="AUG-REQ-OLD" scope-version="1.0.0" plan-digest="<sha256 of the packet>">
  Export one approved task packet to the canonical board.
  <exclusion ref="AUG-REQ-9" reason="owned by a later wave"/>
</task>
```

| attribute | meaning | refused when |
| --- | --- | --- |
| `in-scope` | the blocks the task implements | empty (`task-scope-missing`) |
| `out-of-scope` | the blocks it explicitly does NOT touch | a member has no `<exclusion>` (`missing-exclusion-reason`) |
| `acceptance` | the `<acceptance>` criteria that decide it | a ref is not an `<acceptance>` block (`task-invalid-acceptance-ref`) |
| `acknowledges` | deliberate inclusion of a non-active block | an inactive ref is missing from it (`inactive-reference-unacknowledged`) |
| `scope-version` | pins the whole scope to one contract version | a ref has another version (`cross-version-reference`) |
| `plan-digest` | sha256 of the canonical packet the approval covers | shape is not 64 lower-case hex (`invalid-plan-digest`) |

A ref listed twice is `task-duplicate-reference`; a block listed both inside and outside the scope is
`task-scope-conflict`; an `<exclusion>` for a block that is not out of scope is
`unexpected-exclusion`; an unresolved ref is `unresolved-reference` naming the task **and** the block.
Every one of these refuses the whole corpus verdict and emits no partial packet.

**Verbs.** `spec task-packet <root> --task <ID>` prints `{packet, digest, trace}` — the packet, the
sha256 an approval binds to, and the generated `ch:trace` marker (contract in
[`docs/prd/xml-spec-graph.md`](prd/xml-spec-graph.md)). `spec task-export <root> --task <ID> --board
<dir> [--plan-digest <sha256>] [--boardctl <path>]` writes ONE canonical row through `boardctl`
`create` plus a `show` read-back, and refuses before invoking boardctl at all when the approval is
absent (`unapproved-plan-digest`), malformed (`invalid-plan-digest`) or stale
(`stale-plan-digest`). A re-export of an identical packet answers `unchanged`; a differing existing
row is `board-row-conflict` — updating a canonical row is its own approval-bound operation.

`spec context <root> (--task <ID> | --code <path> | --symbol <name>) [--search-root <dir>]
[--repo-root <dir>]` is the single query an agent runs to get the minimal context: the task and its
in-scope blocks plus declared closure, or the block a `ch:trace spec=` reference names, with
`source`/`version`/`status`/`method`/`edge` per block, the explicit exclusions with reasons, and
`counts.selected`/`counts.dependencies`/`counts.input_bytes`/`counts.output_bytes`.
`status` is closed: `ok | inactive | unscoped | ambiguous`, and anything other than `ok` carries a
`reason` (`no-trace`, `trace-without-spec`, `unknown-spec-id`, `unknown-symbol`, `unknown-task`,
`block-inactive`, `ambiguous-match`, `path-not-found`). It never substitutes a whole-document or
whole-corpus dump, and it reads: no board write, no dispatch, no LLM.

```bash
auger spec task-packet specs --task AUG-121 | jq -r .trace
auger spec context specs --task AUG-121 | jq '.counts, [.selected[].id]'
auger spec context specs --code auger.py --repo-root . | jq -r .status
```

ch:trace row=AUG-121,AUG-122 spec=docs/XML-SPEC-SCHEMA.md#task-scope-and-the-one-query-context
         test=tests/test_xml_task_contract.py,tests/test_spec_context.py doc=docs/XML-SPEC-SCHEMA.md

## The staged-diff checker (AUG-120 / AUG-135)

`auger spec align` is the checker GitReins already invokes. GitReins' half is built and shipped
(`engine.spec_alignment.run_checker`): it appends `--repo-root <workdir> --spec-root <root>
--protocol-version <n> --diff-stdin`, feeds the exact staged patch
(`git diff --cached --binary --no-ext-diff --no-renames`), and reads the LAST JSON LINE of stdout.
The command an operator configures is therefore:

```text
auger spec align
```

followed by the caller's four flags. The same verb takes `--diff-file PATH` (tests and replays) and
`--max-diff-bytes N` (default 8388608); feeding it neither a stdin nor a file is a typed refusal.

**Two command strings, one verdict.** `auger spec check [ROOT]` carries the same four flags, and
with `--diff-stdin` (or `--diff-file`) it routes into the SAME implementation and prints the SAME
one-line payload — with a positional ROOT, with `--spec-root`, or with both. Without a diff it stays
the obligation verdict it always was. The two strings exist because both are already in circulation:
the documentation names `auger spec align`, and the AUG-135 row text simulates the caller as
`... --diff-stdin check`. They agree by construction, and a test asserts the payloads are
byte-identical, so no board can be "complete" under one spelling and not the other.

**Handshake.** The payload carries `"protocol"` (the version the caller asked for, echoed) and
`"version"` — the CHECKER PROTOCOL version, currently `1.0.0`, never the product's `AUGER_VERSION`
(0.3), because GitReins compares `version` against `min_checker_version` (default `1.0.0`) and would
refuse the product version. This checker speaks protocol `1`; a request for any other version is
`spec error (unsupported-protocol-version)` on stderr with exit 2 and NO payload — a caller must
never read a verdict out of a contract we did not agree to speak.

**Payload** — one line, sorted keys, no indentation:

```text
{"checker":"auger","counts":{"fail":0,"info":0,"warn":0},
 "diff":{"added_lines":4,"binary_files":[],"changed_files":["tests/test_marker.py"],"files":1,"removed_lines":0},
 "findings":[],"protocol":"1","repo_root":"/abs/repo",
 "referenced":{"blocks":[{"id":"ALT-TEST-1","kind":"test","source":"contract.xml#ALT-TEST-1","status":"active","why":"cited by the staged change"}],"paths":["tests/test_marker.py"],"unresolved":[]},
 "scope":"scoped","spec_root":"/abs/repo/specs","status":"pass","version":"1.0.0"}
```

* `status` is `pass` (nothing to report), `warn` (advisory findings only) or `fail` (at least one
  `fail` severity finding). GitReins accepts `pass|warn|fail` (and `block` as a legacy alias).
* `scope` is `scoped` (a corpus block scopes a changed path, or the change cites a block),
  `unscoped` (`no block relates to the change — a NAMED state, never an automatic pass`: it carries
  a `warn` finding and makes `status` at least `warn`) or `empty` (nothing staged).
* `referenced.blocks` are the spec IDs/blocks the change touches, each with its `source`
  (`<file>#<id>`) — the evidence location. `referenced.unresolved` names `spec=` values that did
  not land anywhere.
* `findings[]` is `{code, severity, why, evidence}` with `evidence` a `file:line` or a
  `file#block-id` citation.

**Findings** (each one is a rule; `fail` blocks under a blocking policy):

| code | severity | meaning |
| --- | --- | --- |
| `unscoped-change` | warn | no corpus block scopes the change; reported as unscoped |
| `empty-diff` | info | nothing staged |
| `trace-missing` | fail | a scoped change with no `ch:trace` marker |
| `trace-malformed` | fail | `ch:trace` whose keys are all unrecognized (no doctrine field) |
| `trace-missing-row` | fail | the marker has no `row=` (mandatory) |
| `trace-missing-field` | fail | a contract field was omitted instead of stated as `none:<reason>` |
| `trace-absent-value` | fail | a field is empty, or `none` without a reason |
| `trace-unknown-field` | warn | a field outside the eleven-field doctrine |
| `trace-evidence-is-row` | fail | `evidence=` names a row/block instead of an artifact |
| `trace-spec-unresolved` | fail | `spec=` is neither a block id nor a path#anchor that lands |
| `trace-test-unresolved` | fail | `test=` names no file, or no selector the file defines |
| `trace-verdict-unbound` | fail | a real `verdict=` with no `commit=` |
| `trace-commit-unknown` | fail | `commit=` is not a commit in THIS repository |
| `trace-row-unknown` | warn | `tasks.jsonl` exists and does not carry that row |
| `spec-annotation-unresolved` | fail | an inline `spec=<ID>` the corpus does not define |
| `spec-section-superseded` | fail | the change cites a block whose status is not `active` |
| `spec-section-mismatch` | warn | the marker cites a block that does not scope the changed file |

**The trace contract.** The marker is the eleven-field closeout claim (`row`, `spec`, `wave`,
`test`, `doc`, `prompt`, `evidence`, `witness`, `verdict`, `commit`, `memory`). `row=` is mandatory;
the six at-minimum fields (`row`, `spec`, `test`, `evidence`, `verdict`, `commit`) must be PRESENT,
carrying a value or an explicit `none:<reason>`; `evidence=` must be an artifact or witness, never
another row; a real `verdict=` must be bound to a `commit=` that resolves in this repository. One
marker in a staged diff is a commit-level claim, so it covers every file in that diff.

**Two boundaries this checker states rather than hides.** Contradiction is checked STRUCTURALLY —
an annotation that contradicts the corpus (unresolvable, superseded, mismatched section), never "the
comment says X and the prose says not-X", which is not mechanically decidable. And scoping is what
the corpus DECLARES: a changed path no block cites (`path=`/`migration=`/`fixture=`) and no marker
names is `unscoped`, which is a report about the corpus' reach, not a statement that the change is
unspecified.

**Exit codes.** `0` pass or warn, `1` fail, `2` a typed refusal (`spec error (<code>)` on stderr,
nothing on stdout): `unsupported-protocol-version`, `diff-too-large`, `missing-diff-source`,
`ambiguous-diff-source`, `unreadable-diff-file`, `unresolvable-repo-root`, plus every corpus refusal
the loader already raises. The run is bounded — one capped stdin read, no subprocess chain, no
thread — so a refusal cannot leave a process behind for a hook decision to outlive.
