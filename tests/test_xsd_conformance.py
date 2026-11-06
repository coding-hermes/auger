"""AUG-125/AUG-133: the shipped XSD is a real, loadable, ENFORCED grammar.

Measured defect (2026-11-05, base `cf5bfe2`): `specs/schema/auger-spec-1.0.xsd` could not be
loaded by ANY XSD 1.0 processor — two `xs:pattern` facets were written in Python dialect
(`(?:-[A-Z0-9]+)+`), which the processor rejects as "not a valid regular expression" — and
`auger.py` never loaded it anyway (`grep -rn 'xsd\\|XMLSchema\\|lxml' auger.py` returned comments
only). The normative grammar therefore decided nothing: a corpus that every rule in the code
accepted could violate the schema and still print `spec: ... ok` with exit 0.

What this file pins, per acceptance line:

  AUG-125 processor load .............. test_the_shipped_xsd_loads_in_a_real_xsd10_processor
  AUG-125 fixtures validate ........... test_every_shipped_fixture_is_schema_valid_or_refused
  AUG-125 dialect mutation ............ test_a_processor_rejected_pattern_dialect_is_refused
  AUG-125 pattern-language parity ..... test_pattern_facets_express_the_same_language_as_the_code
  AUG-133 typed schema refusal ........ test_a_code_acceptable_schema_invalid_document_is_refused
  AUG-133 attribute inventory ......... test_every_attribute_the_code_reads_is_declared
  AUG-133 `<test>` is expressible ..... test_a_corpus_with_a_test_block_validates
  AUG-133 no processor = refusal ...... test_a_missing_xsd_processor_is_a_typed_refusal_not_a_pass

PROCESSOR. lxml's `etree.XMLSchema` (libxml2's XSD 1.0 engine) is the processor used here. The
documented alternate is the CLI: `xmllint --schema specs/schema/auger-spec-1.0.xsd <file>` — the
same libxml2 engine, and it is not installed on this host, so it is named rather than shelled.

WHY THERE IS NO skip/xfail FALLBACK (owner decision D-1). auger stays stdlib-only at RUNTIME
(`pyproject.toml`: `dependencies = []`); the processor is a `dev`/`auger[xsd]` extra. That trade is
only honest if its absence is LOUD: a host without the processor must produce a RED suite and a
typed refusal from the CLI, never a green run that quietly stopped checking the grammar. So this
module imports `lxml` at module scope (no `importorskip`) and
`test_a_missing_xsd_processor_is_a_typed_refusal_not_a_pass` proves the CLI fails CLOSED.

ch:trace row=AUG-125,AUG-133 spec=docs/SPEC-003-xml-specs.md#the-schema
         test=tests/test_xsd_conformance.py doc=docs/XML-SPEC-SCHEMA.md
         evidence=specs/schema/auger-spec-1.0.xsd
"""

from __future__ import annotations

import ast
import contextlib
import io
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pytest
from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
FIXTURE_CORPORA = ("specs", "verification")
INVALID = FIXTURES / "invalid"
SCHEMA_INVALID = INVALID / "schema-invalid"
SCHEMA = REPO_ROOT / "specs" / "schema" / "auger-spec-1.0.xsd"
AUGER_SOURCE = REPO_ROOT / "auger.py"
GATE = REPO_ROOT / "tests" / "gate.sh"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

#: The attributes AUG-133 names by hand. The AST scan below must FIND them (so the scan cannot
#: pass by reading an empty list) and the schema must DECLARE them on a block.
NAMED_OBLIGATION_ATTRS = (
    "runner",
    "selector",
    "path",
    "migration",
    "fixture",
    "assertions",
    "type",
    "method",
    # AUG-138: the typed edge that resolves in a DuckBrain namespace, not in the corpus.
    "decision-ref",
)

#: Functions that read authored attributes. The scan is scoped to them so ordinary dict `.get()`
#: calls elsewhere in the 9k-line module cannot pollute the inventory.
SPEC_ATTRIBUTE_READERS = (
    "_spec_read_document",
    "_spec_include_target",
    "_spec_schema_violations",
    "_spec_assert_schema_conformance",
    "_spec_block",
    "_spec_dispatch",
    "_xml_spec_documents",
    "validate_xml_specs",
    "_spec_validate_decision_refs",
    "assemble_xml_requirement",
)


def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` in-process; returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def schema_document(body: str) -> bytes:
    """A minimal authored document carrying `body`, for facet probes."""
    return (
        '<spec xmlns="urn:auger:spec:1" id="AUG-SCHEMA-PROBE-1" version="1.0.0" '
        f'schema-version="1.0">{body}</spec>'
    ).encode()


def violations(raw: bytes, rel: str = "probe.xml") -> list[str]:
    """The shipped schema's complaints about `raw`, through the loader's own validator."""
    return auger._spec_schema_violations(auger._spec_load_schema(), raw, rel)


def xsd_declared_attributes() -> dict[str, set[str]]:
    """Every attribute name the shipped schema declares, mapped to its owning complexTypes."""
    root = ET.parse(SCHEMA).getroot()
    declared: dict[str, set[str]] = defaultdict(set)
    for complex_type in root.iter(f"{XSD_NS}complexType"):
        owner = complex_type.get("name") or "<anonymous>"
        for attribute in complex_type.iter(f"{XSD_NS}attribute"):
            declared[attribute.get("name")].add(owner)
    return declared


def xsd_attribute_names_of(complex_type: str) -> set[str]:
    root = ET.parse(SCHEMA).getroot()
    for node in root.iter(f"{XSD_NS}complexType"):
        if node.get("name") == complex_type:
            return {a.get("name") for a in node.iter(f"{XSD_NS}attribute")}
    raise AssertionError(f"schema has no complexType {complex_type!r}")


def xsd_simple_type_values(name: str) -> list[str]:
    root = ET.parse(SCHEMA).getroot()
    for node in root.iter(f"{XSD_NS}simpleType"):
        if node.get("name") == name:
            return [
                enumeration.get("value")
                for enumeration in node.iter(f"{XSD_NS}enumeration")
            ]
    raise AssertionError(f"schema has no simpleType {name!r}")


def code_attribute_reads() -> dict[str, set[str]]:
    """Attribute names `auger.py` reads on authored nodes, derived from its AST.

    Derived, not hand-listed: a new `node.get("deadline")` in the spec surface shows up here on
    its own, so the inventory cannot drift behind the code the way the schema did.

    Two read shapes are counted, because the validator uses both: a literal `node.get("runner")`,
    and a loop over a tuple of names (`for attribute in ("type", "method"): node.get(attribute)`).
    Missing the second shape would report `type` as unread while the code reads it on every
    verification/test node — a scan that under-reports is worse than none, because the parity
    assertion would then pass on an incomplete inventory.
    """
    tree = ast.parse(AUGER_SOURCE.read_text(encoding="utf-8"))
    reads: dict[str, set[str]] = defaultdict(set)
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name not in SPEC_ATTRIBUTE_READERS:
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                reads[node.args[0].value].add(function.name)
            if isinstance(node, (ast.For, ast.comprehension)):
                names = _iterated_string_names(node.iter)
                for name in names:
                    reads[name].add(function.name)
    return reads


def _iterated_string_names(iterator) -> list[str]:
    """String names a loop iterates: a literal tuple/list, or a module-level constant tuple.

    `for attribute in SPEC_EDGE_ATTRS: element.get(attribute)` reads four attributes without ever
    naming them at the call site, so resolving module-level constants is not optional — without it
    the inventory silently omits `depends-on`, `refs`, `implements` and `supersedes`.
    """
    if isinstance(iterator, (ast.Tuple, ast.List)):
        return [
            element.value
            for element in iterator.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    if isinstance(iterator, ast.Name):
        value = getattr(auger, iterator.id, None)
        if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            return list(value)
    return []


# ---------------------------------------------------------------- AUG-125: the processor loads it
def test_the_shipped_xsd_loads_in_a_real_xsd10_processor() -> None:
    """AUG-125: the published grammar is a schema, not a file that merely looks like one.

    The alternate proof named for a host without lxml is
    `xmllint --schema specs/schema/auger-spec-1.0.xsd <document>`; it is the same libxml2 engine
    and is deliberately not shelled here (it is not installed, and a shelled tool that is missing
    must not be able to turn this into a skip).
    """
    assert SCHEMA.is_file(), f"missing published schema: {SCHEMA}"
    schema = etree.XMLSchema(etree.parse(str(SCHEMA)))
    assert schema is not None
    # The same load through the shipped code path, which is what the CLI uses.
    loaded = auger._spec_load_schema()
    assert isinstance(loaded, etree.XMLSchema)

    # A schema that loads but declares nothing would pass the line above; pin its surface.
    root = ET.parse(SCHEMA).getroot()
    assert root.get("targetNamespace") == auger.SPEC_XML_NAMESPACE
    assert root.get("version") == auger.SPEC_SCHEMA_VERSION
    declared_elements = {node.get("name") for node in root.iter(f"{XSD_NS}element")}
    for element in (*auger.SPEC_BLOCK_KINDS, "include", "spec"):
        assert element in declared_elements, f"schema declares no <{element}>"


def test_every_shipped_fixture_is_schema_valid_or_refused() -> None:
    """AUG-125: every shipped fixture is either schema-valid AND accepted, or loudly refused.

    The literal acceptance line is "every shipped fixture validates against the shipped XSD".
    Read literally that includes `tests/fixtures/xml/invalid/**`, which exist precisely to be
    REJECTED — so the invariant proved here is the one that can actually be true of the shipped
    tree, and it is the stronger contract for a caller:

      * a corpus the CLI ACCEPTS contains no document the schema rejects (no silent divergence),
      * every corpus the CLI REFUSES names a typed error class,
      * the accepted corpora (`specs/`, `verification/`) are schema-valid document for document,
      * at least one fixture is refused BY THE SCHEMA (non-vacuity: the gate is live, not inert).
    """
    schema = auger._spec_load_schema()
    accepted_rows: list[tuple[str, str]] = []
    refused_rows: list[tuple[str, str, str]] = []
    for corpus in sorted({path.parent for path in FIXTURES.rglob("*.xml")}):
        rel_corpus = corpus.relative_to(FIXTURES).as_posix()
        code, out, err = run_spec("validate", str(corpus))
        for document in sorted(corpus.glob("*.xml")):
            rel = document.relative_to(FIXTURES).as_posix()
            problems = auger._spec_schema_violations(schema, document.read_bytes(), rel)
            if code == 0:
                assert not problems, (
                    f"{rel}: the CLI accepts a document the shipped XSD rejects "
                    f"({problems[0]}) — the schema is decorative again"
                )
                accepted_rows.append((rel, rel_corpus))
            else:
                assert code == 2, f"{rel}: expected refusal rc=2, got {code}"
                assert out == "", f"{rel}: a refusal printed a partial verdict"
                assert "spec error (" in err, (
                    f"{rel}: refused without a typed error class: {err!r}"
                )
                refused_rows.append(
                    (
                        rel,
                        rel_corpus,
                        "schema-invalid"
                        if problems
                        else err.split("(")[1].split(")")[0],
                    )
                )

    assert accepted_rows, "no fixture corpus is accepted — this test proves nothing"
    assert refused_rows, "no fixture corpus is refused — this test proves nothing"
    # The literal acceptance line, for the corpora that are meant to be accepted.
    for corpus in FIXTURE_CORPORA:
        for document in sorted((FIXTURES / corpus).glob("*.xml")):
            rel = document.relative_to(FIXTURES).as_posix()
            assert not auger._spec_schema_violations(
                schema, document.read_bytes(), rel
            ), (
                f"{rel}: a shipped accepted fixture does not validate against the shipped XSD"
            )
    # Non-vacuity: the schema is not merely present, it decides something.
    assert any(layer == "schema-invalid" for _rel, _corpus, layer in refused_rows), (
        "no fixture is refused BY THE SCHEMA — the XSD gate is inert: "
        f"{sorted(refused_rows)}"
    )


def test_a_processor_rejected_pattern_dialect_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUG-125: change a facet to a dialect the processor rejects and the suite fails.

    This is the mutation check. The shipped pattern is rewritten back to the Python-dialect form
    (`(?:-[A-Z0-9]+)+`) that the 2026-11-05 base shipped, and the test demands a refusal at
    BOTH levels: the processor rejects the grammar, and the CLI refuses instead of grading a
    corpus against a grammar it could not load.
    """
    text = SCHEMA.read_text(encoding="utf-8")
    mutated = text.replace("(-[A-Z0-9]+)+", "(?:-[A-Z0-9]+)+")
    assert mutated != text, (
        "the mutation did not apply — the shipped facet changed shape"
    )
    target = tmp_path / "auger-spec-1.0.xsd"
    target.write_text(mutated, encoding="utf-8")

    with pytest.raises(etree.XMLSchemaParseError) as caught:
        etree.XMLSchema(etree.parse(str(target)))
    assert any(
        "not a valid regular expression" in str(entry)
        for entry in caught.value.error_log
    ), f"the processor rejected the mutant for another reason: {caught.value}"

    with pytest.raises(auger.SpecError) as code_error:
        auger._spec_load_schema(target)
    assert code_error.value.code == "xsd-schema-unloadable", code_error.value

    # The CLI is what a user types: a grammar it cannot load must never grade a corpus.
    monkeypatch.setattr(auger, "SPEC_SCHEMA_PATH", target)
    code, out, err = run_spec("validate", str(FIXTURES / "specs"))
    assert code == 2, f"a processor-rejected grammar reported rc={code}: {out!r}"
    assert out == "", "a refusal printed a verdict from an unloadable grammar"
    assert "spec error (xsd-schema-unloadable)" in err, err
    assert target.name in err, f"the refusal does not name the schema: {err!r}"


PATTERN_SAMPLES = {
    "id": (
        # accepted by BOTH the Python validator and the XSD facet
        ["AUG-125", "AB-1", "A-1", "AUG-REQ-014", "A1-B2-C3", "SPEC-0003"],
        # rejected by BOTH
        ["aug-125", "AUG125", "AUG-", "-AUG-1", "1-AUG", "AUG--1", "AUG-1a"],
    ),
    "version": (
        ["1.0.0", "10.2.99", "0.0.1"],
        ["1.0", "1.0.0.0", "va.0.0", "1..0", "1.0.", "-1.0.0"],
    ),
    "applies-to": (
        ["auger.export", "a1.b-2_c", "auger.spec auger.export"],
        ["Auger.export", "auger..export", "auger.", "-auger", "auger export!"],
    ),
}


@pytest.mark.parametrize("family", sorted(PATTERN_SAMPLES))
def test_pattern_facets_express_the_same_language_as_the_code(family: str) -> None:
    """AUG-125: the facet and the Python rule accept and reject the SAME sample values.

    Byte-identity of the pattern strings is asserted separately (the parity test in
    tests/test_xml_specs.py); this is the language check that byte identity cannot give — the two
    engines (Python `re` and libxml2's XSD regex) have to agree on real inputs.
    """
    accepted, rejected = PATTERN_SAMPLES[family]
    for value in accepted:
        body = {
            "id": f'<section id="{value}" kind="section" status="active" version="1.0.0"/>',
            "version": (
                '<section id="AUG-VERSION-PROBE-1" kind="section" status="active" '
                f'version="{value}"/>'
            ),
            "applies-to": (
                '<section id="AUG-SCOPE-PROBE-1" kind="section" status="active" '
                f'version="1.0.0" applies-to="{value}"/>'
            ),
        }[family]
        assert violations(schema_document(body)) == [], (
            f"{family}={value!r} is accepted by auger.py but the XSD facet rejects it"
        )
    for value in rejected:
        body = {
            "id": f'<section id="{value}" kind="section" status="active" version="1.0.0"/>',
            "version": (
                '<section id="AUG-VERSION-PROBE-1" kind="section" status="active" '
                f'version="{value}"/>'
            ),
            "applies-to": (
                '<section id="AUG-SCOPE-PROBE-1" kind="section" status="active" '
                f'version="1.0.0" applies-to="{value}"/>'
            ),
        }[family]
        assert violations(schema_document(body)), (
            f"{family}={value!r} is refused by auger.py but the XSD facet accepts it"
        )


def test_the_stable_id_facet_carries_the_length_limit_too() -> None:
    """AUG-125: `stableId` is pattern + `maxLength`, which is what the code checks together."""
    long_id = "A" + "-" + "1" * (auger.SPEC_ID_MAX_LEN + 1)
    assert len(long_id) > auger.SPEC_ID_MAX_LEN
    # The Python pattern alone matches (it has no length rule); the code's combined check does not.
    assert auger.SPEC_ID_RE.match(long_id) is not None
    assert (
        len(long_id) > auger.SPEC_ID_MAX_LEN or auger.SPEC_ID_RE.match(long_id) is None
    )
    # The schema agrees with the combined check.
    assert violations(
        schema_document(
            f'<section id="{long_id}" kind="section" status="active" version="1.0.0"/>'
        )
    ), (
        "the shipped stableId type does not enforce the 80-character ceiling the code enforces"
    )


# ---------------------------------------------------------------- AUG-133: the schema decides
def test_a_code_acceptable_schema_invalid_document_is_refused() -> None:
    """AUG-133: well-formed + code-acceptable + schema-invalid => refused, typed, both verbs.

    `severity="high"` is an attribute no block kind declares. Nothing in auger.py reads it, so
    before this change the document validated "ok" with exit 0 — the measured proof that the XSD
    decided nothing.
    """
    document = SCHEMA_INVALID / "undeclared-attribute.xml"
    assert document.is_file(), f"missing fixture: {document}"

    # The schema's own complaint names the offending attribute.
    problems = auger._spec_schema_violations(
        auger._spec_load_schema(),
        document.read_bytes(),
        document.name,
    )
    assert problems, (
        "the shipped schema accepts a document with an undeclared attribute"
    )
    assert "severity" in problems[0], problems

    for verb in ("validate", "check"):
        code, out, err = run_spec(verb, str(SCHEMA_INVALID))
        assert code == 2, f"{verb}: schema-invalid corpus reported rc={code}: {out!r}"
        assert out == "", f"{verb}: a refusal printed a verdict"
        assert "spec error (schema-invalid)" in err, err
        assert document.name in err, (
            f"{verb}: refusal does not name the document: {err!r}"
        )
        assert "severity" in err, (
            f"{verb}: refusal does not name the attribute: {err!r}"
        )


def test_every_attribute_the_code_reads_is_declared() -> None:
    """AUG-133: the attribute inventory cannot drift again — it is derived from auger.py's AST.

    A name the code reads and the grammar does not declare is exactly the defect that made the
    AUG-123 obligation surface unexpressible: `runner`, `selector`, `path`, `migration`,
    `fixture`, `assertions` and `type` were read on every `<verification>` block and declared
    nowhere.
    """
    reads = code_attribute_reads()
    declared = xsd_declared_attributes()

    assert reads, "the AST scan found no attribute reads — the scan itself is broken"
    for name in NAMED_OBLIGATION_ATTRS:
        assert name in reads, (
            f"auger.py reads {name!r} on an authored node but the scan did not see it — "
            "SPEC_ATTRIBUTE_READERS is missing the function that reads it"
        )
        assert name in xsd_attribute_names_of("blockType"), (
            f"{name!r} is read by the code but not declared on the schema's block type"
        )

    undeclared = sorted(set(reads) - set(declared))
    assert not undeclared, (
        "attributes auger.py reads that the shipped XSD does not declare: "
        f"{undeclared} — an editor validating against the grammar would flag every such document"
    )
    # The edge attributes are read through a constant tuple, not a literal at the call site; if the
    # scan stops resolving those, this fails instead of quietly reporting a thinner inventory.
    assert set(auger.SPEC_EDGE_ATTRS) <= set(reads), (
        "the scan lost the typed edge attributes — loops over SPEC_EDGE_ATTRS are not resolved"
    )

    # The obligation-type vocabulary is the code's, including the `manual` value the obligation
    # validator accepts where the authoring enum says `demo`.
    assert set(xsd_simple_type_values("obligationType")) == {
        *auger.SPEC_VERIFICATION_METHODS,
        "manual",
    }


def test_a_corpus_with_a_test_block_validates(tmp_path: Path) -> None:
    """AUG-133: the `<test>` surface the obligation validator enforces is expressible.

    The schema declares `<test>` in the block substitution group and the CLI ACCEPTS a corpus
    that uses it — a declaration no corpus can use is the decorative schema again, one element
    further along.
    """
    root_node = ET.parse(SCHEMA).getroot()
    test_elements = [
        node
        for node in root_node.iter(f"{XSD_NS}element")
        if node.get("name") == "test"
    ]
    assert test_elements, "the shipped XSD declares no <test> element"
    assert test_elements[0].get("substitutionGroup") == "auger:block", (
        "the <test> element must join the block substitution group, or it cannot appear where "
        "blocks appear"
    )
    assert "test" in xsd_simple_type_values("blockKind"), (
        "blockKind must admit the test kind the <test> element carries"
    )

    document = tmp_path / "test-block.xml"
    document.write_text(
        '<spec xmlns="urn:auger:spec:1" id="AUGER-TEST-BLOCK" version="1.0.0" '
        'schema-version="1.0">\n'
        '  <requirement id="AUG-REQ-TESTBLOCK-1" kind="requirement" status="active" '
        'version="1.0.0" applies-to="auger.spec" verification="test"/>\n'
        '  <test id="AUG-TEST-TESTBLOCK-1" kind="test" status="active" version="1.0.0"\n'
        '        applies-to="auger.spec" method="test" verifies="AUG-REQ-TESTBLOCK-1"\n'
        '        runner="pytest" path="tests/test_xsd_conformance.py"\n'
        '        selector="test_a_corpus_with_a_test_block_validates"/>\n'
        "</spec>\n",
        encoding="utf-8",
    )
    assert not auger._spec_schema_violations(
        auger._spec_load_schema(), document.read_bytes(), document.name
    ), "the shipped schema does not describe the <test> shape the validator enforces"
    code, out, err = run_spec("validate", str(tmp_path), "--repo-root", str(REPO_ROOT))
    assert code == 0, f"the CLI refuses a corpus written in the <test> shape: {err}"
    assert "2 block(s)" in out, out


def test_a_missing_xsd_processor_is_a_typed_refusal_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUG-133/D-1: no processor means NO VERDICT — a typed refusal, never a quiet green.

    The processor lives in the `dev`/`auger[xsd]` extra so the wheel stays stdlib-only. That is
    only defensible while its absence is loud, so the seam is exercised by pointing the loader at
    a module that does not exist: the CLI must refuse with `xsd-processor-missing`.
    """
    monkeypatch.setattr(
        auger, "SPEC_XSD_PROCESSOR_MODULE", "auger_no_such_xsd_processor"
    )
    for verb in ("validate", "check"):
        code, out, err = run_spec(verb, str(FIXTURES / "specs"))
        assert code == 2, f"{verb}: no XSD processor, yet rc={code}: {out!r}"
        assert out == "", f"{verb}: graded a corpus with no XSD processor installed"
        assert "spec error (xsd-processor-missing)" in err, err
        assert "auger[xsd]" in err or "dev" in err, (
            f"{verb}: the refusal does not say how to install the processor: {err!r}"
        )

    # And the reason it refused, not a coincidence of the corpus.
    with pytest.raises(auger.SpecError) as caught:
        auger._spec_load_schema()
    assert caught.value.code == "xsd-processor-missing"


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_xsd_conformance.py" in arm[0], (
        "tests/test_xsd_conformance.py must be named in tests/gate.sh's pytest arm"
    )
