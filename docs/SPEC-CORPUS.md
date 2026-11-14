# The authored spec corpus (`specs/`)

`specs/` used to hold only the grammar (`specs/schema/auger-spec-1.0.xsd`), so every XML document
in the repository was a test fixture and nothing exercised the machinery `auger spec` ships. This
directory is now the authored corpus: three documents about Auger's own XML-spec surface, written
against the same grammar a consumer would use, with every test obligation naming a tracked test in
this repository.

| Document | Document id | Blocks | Scope token | What it contracts |
|---|---|---|---|---|
| `specs/spec-graph.xml` | `AUGER-SPEC-GRAPH` | 3 | `auger.spec.load` | The corpus loader and the one-verdict rule shared by `spec validate` / `spec check`, including the XSD backstop. |
| `specs/spec-verbs.xml` | `AUGER-SPEC-VERBS` | 4 | `auger.spec.select` | Block selection (`--id/--kind/--applies-to/--status/--version`), assembly closure, `--no-deps`, and the `empty-selector` refusal. |
| `specs/verification.xml` | `AUGER-SPEC-OBLIGATIONS` | 5 | `auger.spec.obligation` | Where `path`/`selector` resolve (`--repo-root`, the consuming repository) and what `runner` means (a registry id, never a shell string). |

Twelve blocks in total: 4 requirements, 1 acceptance, 7 verifications. Block ids are stable and
never renumbered; `kind` always equals the element name.

## Validating and reading it

Run from the repository root. The XSD processor (`lxml`) is a `dev` extra, not a runtime
dependency, so a host without it refuses with `xsd-processor-missing` instead of passing.

```bash
python auger.py spec validate specs
# spec: 3 document(s), 12 block(s), 7 reference(s) ok

python auger.py spec check specs
# {"nodes": ["SPEC-GRAPH-CORPUS", ...], "status": "valid"}

python auger.py spec list specs
# one TSV line per block: id, kind, status, version, source, applies-to

python auger.py spec list specs --kind requirement
python auger.py spec bundle specs SPEC-OBL-RUNNER
# {"id":"SPEC-OBL-RUNNER","verifications":[{"path":"tests/test_verification_graph.py", ...}]}

python auger.py spec assemble specs --applies-to auger.spec.select --format json
# matched blocks + closure, each carrying its source citation (spec-verbs.xml#SPEC-VERBS-SELECTION)
```

`spec validate` and `spec check` are one verdict from one loader: same exit code, same diagnostic.
`spec assemble` never touches a test path, so it needs no `--repo-root`; `validate`, `check` and
`bundle` do resolve obligations and use `--repo-root DIR` when the corpus lives outside the
repository it describes. This corpus lives inside the repository it describes, so
`git rev-parse --show-toplevel` from `specs/` is the root and no flag is needed.

## The generated views

The XML is the source and the readable documents are views of it. Two views ship, rendered from the
SAME loaded corpus: [`SPEC-CORPUS-VIEW.md`](SPEC-CORPUS-VIEW.md) and
[`SPEC-CORPUS-VIEW.html`](SPEC-CORPUS-VIEW.html) — every document, every block with its prose and
attributes, the measured counts, and a reference index of every typed edge. Both are committed,
generated artifacts:

```bash
auger spec render specs --out docs         # rewrite both views (the CLI form, AUG-139)
python3 scripts/gen_spec_view.py           # the same renderer from a bare checkout
python3 scripts/gen_spec_view.py --check   # rc 1 when a committed view has drifted from specs/
```

`auger spec render` reads the corpus through `auger.load_spec_corpus` — the loader every other
`spec` verb uses — and writes `SPEC-CORPUS-VIEW.md` and `SPEC-CORPUS-VIEW.html` into `--out`, which
must be an existing directory OUTSIDE the corpus root (`render-into-corpus`): a view written back
into the source tree would be the second, editable copy this corpus exists to prevent.

`tests/test_spec_corpus_view.py` and `tests/test_spec_render.py` regenerate the views and assert
BYTE equality with the committed files, so a view cannot drift from its source; the same tests prove
the views are derived — an edit to ONE block's body moves that block's section in BOTH views and
leaves every untouched block byte-identical. Edit the XML, then regenerate; a hand edit of either
view fails those tests.

## The brief-context builder (AUG-165)

At dispatch time the factory foreman runs
`python3 scripts/brief_spec_context.py --row <ROW-ID> --spec-root specs [--repo-root <git-top>]`
and embeds what it prints into the worker brief. The builder resolves the row through the module's
own API (`auger.build_spec_context` — the same selection `spec context` serves) and emits the
selected blocks VERBATIM as a Markdown section, one heading per block carrying that block's own
byte count and a header carrying the measured `input_bytes`.

The exit-code contract is the seam's whole point:

* **0 with a section** — the row is scoped by a `<task>` block; the blocks are embedded byte for
  byte.
* **0 with NO section** — the row has no spec references (`unscoped`/`inactive`/zero selection):
  the brief gets **zero bytes, never an empty "SPEC CONTEXT" heading**, one honest line names the
  reason (`unknown-task`, …) on stderr, and `--status-json FILE` records the machine-readable
  outcome (`no-context`).
* **3** — the answer is `ambiguous`: nothing is emitted and the candidate ids go to stderr so the
  tick report surfaces the ambiguity instead of a brief that guessed.
* **2** — a typed auger refusal (missing spec root, unreadable corpus), with the auger error text
  unchanged on stderr.

A no-context answer is a normal outcome (the shipped corpus scopes no rows yet), not an error; an
ambiguous answer is never silent.

## What the corpus deliberately does not claim

* No block describes `init`, `start`, `ask`, `answer`, `check`, `status`, `toggle`, `dump` or
  `recall`. They are real verbs, but no requirement block here would be verified by a test
  obligation that this corpus can name honestly, so the corpus stays on the surface it can prove.
* The generated view reports only what the loader reads. It does not judge the corpus: `spec
  validate` / `spec check` remain the verdict, and the view is a rendering of the same data.

## Authoring rules this corpus follows

1. Read `specs/schema/auger-spec-1.0.xsd` and `docs/XML-SPEC-SCHEMA.md` first. The grammar is
   loaded by a real XSD 1.0 processor on every verb, so an undeclared attribute is a refusal
   (`schema-invalid`), not a warning.
2. Every block needs `id`, `kind`, `status` and `version`; ids match
   `^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$` and are unique corpus-wide; `kind` equals the element name.
3. `requirement` and `acceptance` carry `verification=`; `verification` carries `method=`.
   `applies-to` is a whitespace-separated list of scope tokens.
4. A verification block that points at a test carries `runner="pytest"`, `path=` a
   repository-relative **tracked** file, and `selector=` a function that exists in that file. The
   validator resolves the path in the consuming repository, runs `git ls-files`, and parses the
   file's AST.
5. Reference direction has one owner per edge. A requirement names its test obligations with
   `verifies=` (that is what `spec bundle` reads); a verification block must not name that
   requirement back on the same edge pair — the loader refuses the resulting cycle with
   `reference-cycle`, which is why no verification block here carries `verifies=`.

## Product behaviour worth knowing when editing the corpus

* `spec check` reports only `requirement`, `verification` and `test` nodes, so the acceptance
  block (`SPEC-VERBS-SELECTION-AC1`) is validated by the grammar and by selection but never
  appears in the `check` node list.
* Assembly closure follows forward `depends-on` plus inverse `verifies`. Selecting a requirement
  pulls the blocks that name it (its acceptance criteria); it does **not** pull the verification
  blocks the requirement's own `verifies=` names. Use `spec bundle <requirement>` for those.
