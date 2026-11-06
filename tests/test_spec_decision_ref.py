"""AUG-138 [R8]: a spec block can rest on a DuckBrain DECISION RECORD, and the claim is checked.

Before this module's feature, `grep -c 'decision-ref|record-ref' auger.py
specs/schema/auger-spec-1.0.xsd` returned 0 in both: a requirement could describe a decision in
prose and nothing resolved it. `SPEC_EDGE_ATTRS` held only edges that resolve INSIDE the corpus
(depends-on / verifies / refs / implements / supersedes), so "this requirement rests on decision
D-014" had no way to be stated, let alone refused when D-014 does not exist.

The seam, and why it is shaped this way:

* `decision-ref` is a typed edge that resolves in a DuckBrain NAMESPACE, named explicitly by
  `--decision-namespace` (or `decision_namespace=` from Python). A record id is never guessed and
  no namespace is inferred from an environment variable — the same rule `--repo-root` states for
  test paths (AUG-127).
* The lookup is OPT-IN by the attribute's presence. A corpus with no `decision-ref` needs no
  substrate, makes no request, and behaves exactly as it did before — which is why every hermetic
  XML test and the shipped `specs/` corpus are unchanged.
* The substrate's real signal is used, not invented: a decision row in the namespace's `decision`
  table carries `status`, and `superseded` is the value auger's own `record_supersessions` writes
  (and `supersession_for` reads back). So a superseded record is reported with its successor when
  the namespace holds the supersedes edge.
* The READ path never writes: every request this module makes is a GET of the decisions table, and
  the corpus tree is hashed before and after to prove the validator touches nothing else.

Arms, per acceptance line:

  AC1 attributes + schema ..... test_the_decision_ref_is_a_declared_typed_attribute
  AC2 resolve / refuse ........ test_a_decision_ref_is_resolved_in_the_named_namespace
                                test_a_missing_record_is_refused
                                test_live_resolves_a_real_record_and_refuses_a_missing_one
  AC3 superseded diagnostic ... test_a_superseded_record_is_a_typed_diagnostic
                                test_live_reports_a_superseded_record
  AC4 read path never writes .. test_the_read_path_never_writes
  AC5 substrate failure ....... test_an_unreachable_ledger_is_a_named_substrate_error
                                test_a_missing_namespace_is_a_named_substrate_error
  opt-in / hermetic ........... test_a_corpus_without_a_decision_ref_never_touches_the_ledger
                                test_a_decision_ref_without_a_namespace_is_refused

ch:trace row=AUG-138 spec=docs/XML-SPEC-SCHEMA.md#decision-records-the-ledger-edge
         test=tests/test_spec_decision_ref.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

AUGER = REPO_ROOT / "auger.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml"
NO_NAMESPACE = FIXTURES / "invalid" / "decision-ref-no-namespace"
SPECS = FIXTURES / "specs"
GATE = REPO_ROOT / "tests" / "gate.sh"
SCHEMA = REPO_ROOT / "specs" / "schema" / "auger-spec-1.0.xsd"
DOC = REPO_ROOT / "docs" / "XML-SPEC-SCHEMA.md"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

NAMESPACE = "auger-pytest-decision-ref"
REQ_ID = "AUG-REQ-DECISION-REF-1"
RECORD = "D-014"
MISSING = "D-999"


# ---------------------------------------------------------------- harness
def run_spec(*argv: str) -> tuple[int, str, str]:
    """Drive `auger spec ...` IN PROCESS, so the transport can be replaced per test."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = auger.main(["spec", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class Ledger:
    """A counting stand-in for DuckBrain's HTTP transport (`auger._req`).

    `db()` looks `_req` up at call time on the module, so patching that one attribute intercepts
    EVERY request the resolver makes — which is what makes "GET only" a measurement rather than
    an assumption about the code. `rows` maps a decision id to the `status` its row carries; an
    unknown id answers `[]`, exactly as a namespace holding no such row does.
    """

    def __init__(self, rows=None, successors=None, status=200):
        self.rows = dict(rows or {})
        self.successors = dict(successors or {})
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def mutating(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "GET"]

    def __call__(
        self, url, method="GET", body=None, headers=None, timeout=45, retries=0
    ):
        self.calls.append((method, url))
        if self.status == 0:
            return 0, {"error": "transport: [Errno 111] Connection refused"}, {}
        if self.status != 200:
            return self.status, {"error": f"HTTP {self.status}"}, {}
        edge = re.search(r"dst_id=eq\.([^&]+)", url)
        if "kind=eq.supersedes" in url and edge:
            successor = self.successors.get(urllib.parse.unquote(edge.group(1)), "")
            return 200, ([{"src_id": successor}] if successor else []), {}
        found = re.search(r"[?&]id=eq\.([^&,]+)", url)
        record = urllib.parse.unquote(found.group(1)) if found else ""
        if record in self.rows:
            return 200, [{"id": record, "status": self.rows[record]}], {}
        return 200, [], {}


@contextlib.contextmanager
def ledger(monkeypatch: pytest.MonkeyPatch, fake: Ledger):
    """Install the fake transport and a token, and hand the fake back to the caller."""
    monkeypatch.setattr(auger, "_req", fake)
    monkeypatch.setattr(auger, "_token", lambda: "test-token")
    yield fake


def corpus(tmp_path: Path, *record_ids: str, block: str = REQ_ID) -> Path:
    """A throwaway corpus whose requirement carries `decision-ref="<ids>"`."""
    root = tmp_path / "decisions"
    root.mkdir()
    refs = " ".join(record_ids)
    (root / "decisions.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spec xmlns="urn:auger:spec:1" id="AUGER-SPEC-DECISIONS" version="1.0.0" '
        'schema-version="1.0">\n'
        f'  <requirement id="{block}" kind="requirement" status="active" version="1.0.0"\n'
        f'               applies-to="auger.spec.obligation" verification="test"\n'
        f'               decision-ref="{refs}">\n'
        "    A requirement that rests on recorded decisions.\n"
        "  </requirement>\n"
        "</spec>\n",
        encoding="utf-8",
    )
    return root


def tree_digest(root: Path) -> str:
    """sha256 over every file's path and bytes — proof that a read wrote nothing."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------- opt-in / hermetic
def test_a_corpus_without_a_decision_ref_never_touches_the_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `decision-ref` anywhere ⇒ no namespace needed, no request made, same verdict as before.

    This is the property that keeps every existing corpus hermetic: the ledger is reached only by
    a block that asks for it.
    """

    def refuse(*args, **kwargs):
        pytest.fail(f"a corpus with no decision-ref made a request: {args[:2]}")

    monkeypatch.setattr(auger, "_req", refuse)
    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(SPECS), "--repo-root", str(REPO_ROOT))
        assert rc == 0, f"{verb}: a decision-ref-free corpus was refused: {err}"
        assert "ok" in out or '"status": "valid"' in out, out


def test_a_decision_ref_without_a_namespace_is_refused() -> None:
    """A record id with nowhere to resolve is NO VERDICT, not a skipped check."""
    assert NO_NAMESPACE.is_dir(), f"missing fixture: {NO_NAMESPACE}"
    for verb in ("validate", "check"):
        rc, out, err = run_spec(verb, str(NO_NAMESPACE), "--repo-root", str(REPO_ROOT))
        assert rc == 2, (
            f"{verb}: a decision-ref with no namespace must refuse, got {rc}: {out!r}"
        )
        assert out == "", f"{verb}: a refusal printed a verdict"
        assert "spec error (missing-decision-namespace)" in err, err
        assert "--decision-namespace" in err, (
            f"{verb}: the remedy is not named: {err!r}"
        )
        assert "decision-ref-no-namespace.xml" in err, err
        assert REQ_ID in err, err
        assert RECORD in err, err


# ---------------------------------------------------------------- resolve / refuse
def test_a_decision_ref_is_resolved_in_the_named_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record exists ⇒ the corpus validates, and the request names the namespace and the id."""
    root = corpus(tmp_path, RECORD)
    fake = Ledger(rows={RECORD: "active"})
    with ledger(monkeypatch, fake):
        for verb in ("validate", "check"):
            rc, out, err = run_spec(
                verb,
                str(root),
                "--repo-root",
                str(REPO_ROOT),
                "--decision-namespace",
                NAMESPACE,
            )
            assert rc == 0, f"{verb}: a resolvable decision-ref was refused: {err}"
            assert "ok" in out or '"status": "valid"' in out, out
    assert fake.calls, "the resolver made no request — the reference was not checked"
    segment = urllib.parse.quote(NAMESPACE, safe="")
    for method, url in fake.calls:
        assert method == "GET", f"the read path issued {method} {url}"
        assert f"/api/ns/{segment}/tables/decision" in url, url
        assert f"id=eq.{RECORD}" in url, url


def test_a_missing_record_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record the namespace does not hold refuses the corpus, naming doc + block + record."""
    root = corpus(tmp_path, MISSING)
    with ledger(monkeypatch, Ledger(rows={RECORD: "active"})):
        for verb in ("validate", "check"):
            rc, out, err = run_spec(
                verb,
                str(root),
                "--repo-root",
                str(REPO_ROOT),
                "--decision-namespace",
                NAMESPACE,
            )
            assert rc == 2, (
                f"{verb}: a nonexistent decision record must refuse, got {rc}: {out!r}"
            )
            assert out == "", f"{verb}: a refusal printed a verdict"
            assert "unresolved-decision-ref" in err, err
            assert "decisions.xml" in err, f"{verb}: the document is not named: {err!r}"
            assert REQ_ID in err, f"{verb}: the block id is not named: {err!r}"
            assert MISSING in err, (
                f"{verb}: the missing record id is not named: {err!r}"
            )
            assert NAMESPACE in err, f"{verb}: the namespace is not named: {err!r}"
            # The ledger edge must NOT be resolved against the corpus graph: a record id is not a
            # block id, and `unresolved-reference` would be the wrong diagnosis.
            assert "unresolved-reference" not in err, err


def test_a_superseded_record_is_a_typed_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retired decision is reported — with its successor when the namespace records one."""
    root = corpus(tmp_path, RECORD)
    fake = Ledger(rows={RECORD: "superseded"}, successors={RECORD: "D-021"})
    with ledger(monkeypatch, fake):
        rc, out, err = run_spec(
            "validate",
            str(root),
            "--repo-root",
            str(REPO_ROOT),
            "--decision-namespace",
            NAMESPACE,
        )
    assert rc == 2, f"a superseded record was silently accepted: rc={rc} {out!r}"
    assert out == "", "a refusal printed a verdict"
    assert "superseded-decision-ref" in err, err
    assert "decisions.xml" in err, err
    assert REQ_ID in err, err
    assert RECORD in err, err
    assert "D-021" in err, f"the successor the namespace records is not named: {err!r}"


# ---------------------------------------------------------------- substrate failures
def test_an_unreachable_ledger_is_a_named_substrate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No substrate ⇒ a NAMED transport refusal, never a silent pass and never "record missing"."""
    root = corpus(tmp_path, RECORD)
    with ledger(monkeypatch, Ledger(status=0)):
        rc, out, err = run_spec(
            "validate",
            str(root),
            "--repo-root",
            str(REPO_ROOT),
            "--decision-namespace",
            NAMESPACE,
        )
    assert rc == 2, f"an unreachable ledger produced rc={rc}: {out!r}"
    assert out == "", "a refusal printed a verdict"
    assert "spec error (decision-ledger-unavailable)" in err, err
    assert auger.DB_URL in err, f"the refusal does not name the substrate: {err!r}"
    assert "unresolved-decision-ref" not in err, (
        "an unreachable substrate was reported as a missing record — the two are different facts"
    )


def test_a_missing_namespace_is_a_named_substrate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace that does not exist says so, with the remedy — not "the record is missing"."""
    root = corpus(tmp_path, RECORD)
    with ledger(monkeypatch, Ledger(status=404)):
        rc, out, err = run_spec(
            "validate",
            str(root),
            "--repo-root",
            str(REPO_ROOT),
            "--decision-namespace",
            NAMESPACE,
        )
    assert rc == 2, f"a missing namespace produced rc={rc}: {out!r}"
    assert "spec error (decision-ledger-unavailable)" in err, err
    assert NAMESPACE in err, err
    assert "init" in err, f"the remedy (`auger -n <ns> init`) is not named: {err!r}"
    assert "unresolved-decision-ref" not in err, err


# ---------------------------------------------------------------- read path never writes
def test_the_read_path_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate / check / assemble issue NO mutating request and leave the corpus byte-identical."""
    root = corpus(tmp_path, RECORD)
    before = tree_digest(root)
    fake = Ledger(rows={RECORD: "active"})
    with ledger(monkeypatch, fake):
        for verb, extra in (
            (
                "validate",
                ("--repo-root", str(REPO_ROOT), "--decision-namespace", NAMESPACE),
            ),
            (
                "check",
                ("--repo-root", str(REPO_ROOT), "--decision-namespace", NAMESPACE),
            ),
            # `assemble` is a SELECTION verb: it resolves no obligation and therefore carries
            # neither flag. It is in this arm because the criterion names it — and the arm proves
            # the strongest form of the claim for it (it makes no request at all).
            ("assemble", ("--id", REQ_ID, "--format", "json")),
        ):
            rc, out, err = run_spec(verb, str(root), *extra)
            assert rc == 0, f"{verb}: rc={rc} out={out!r} err={err!r}"
    assert fake.calls, (
        "no request was made, so this arm proves nothing about the read path"
    )
    assert not fake.mutating(), (
        f"the read path issued a mutating request: {fake.mutating()}"
    )
    assert {method for method, _ in fake.calls} == {"GET"}, fake.calls
    assert tree_digest(root) == before, "validation modified the corpus tree"


# ---------------------------------------------------------------- the declared surface
def test_the_decision_ref_is_a_declared_typed_attribute() -> None:
    """Code + grammar agree on the attribute, and the grammar enforces its SHAPE.

    The XSD is loaded by every verb, so an undeclared attribute is a `schema-invalid` refusal on
    every document — that is why the declaration ships with the feature, and why the shape parity
    is asserted here rather than assumed: a record id that is not a stable id must be refused by
    the grammar the loader reads.
    """
    assert "decision-ref" in auger.SPEC_EDGE_ATTRS, (
        "the typed edge vocabulary does not carry decision-ref"
    )
    assert "decision-ref" in auger.SPEC_LEDGER_EDGE_ATTRS

    tree = ET.parse(SCHEMA)
    root = tree.getroot()
    block = next(
        node
        for node in root.iter(f"{XSD_NS}complexType")
        if node.get("name") == "blockType"
    )
    declared = {
        attribute.get("name"): attribute.get("type")
        for attribute in block.iter(f"{XSD_NS}attribute")
    }
    # taskType extends blockType, so a task inherits the declaration; assert that too.
    assert "decision-ref" in declared, (
        "specs/schema/auger-spec-1.0.xsd does not declare decision-ref on the block type, so any "
        "corpus that uses it is refused schema-invalid on every verb"
    )
    type_name = declared["decision-ref"].split(":")[-1]
    simple = next(
        node
        for node in root.iter(f"{XSD_NS}simpleType")
        if node.get("name") == type_name
    )
    assert simple.tag == f"{XSD_NS}simpleType"
    list_node = simple.find(f"{XSD_NS}list")
    assert list_node is not None, f"{type_name} is not a whitespace-separated list"
    assert list_node.get("itemType") == "auger:stableId", (
        f"{type_name} must hold stable block-style ids, got {list_node.get('itemType')!r}"
    )

    # Shape parity, measured through the loader's own validator: a value that is not a stable id
    # is refused by the grammar (the code deliberately defers the SHAPE to the XSD).
    problems = auger._spec_schema_violations(
        auger._spec_load_schema(),
        (
            '<spec xmlns="urn:auger:spec:1" id="AUGER-SPEC-DECISION-SHAPE" version="1.0.0" '
            'schema-version="1.0"><requirement id="AUG-REQ-SHAPE-1" kind="requirement" '
            'status="active" version="1.0.0" verification="test" '
            'decision-ref="no-such-record"/></spec>'
        ).encode(),
        "decision-shape.xml",
    )
    assert problems, (
        "the grammar accepts a decision-ref that is not a stable id — the shape facet is inert"
    )


def test_the_documented_contract_names_the_flag() -> None:
    """The seam is documented where an author looks, or nobody can supply a namespace."""
    text = DOC.read_text(encoding="utf-8")
    assert "decision-ref" in text, "docs/XML-SPEC-SCHEMA.md never names the attribute"
    assert "--decision-namespace" in text, (
        "docs/XML-SPEC-SCHEMA.md never names the flag that makes the reference resolvable"
    )
    for code in (
        "unresolved-decision-ref",
        "superseded-decision-ref",
        "decision-ledger-unavailable",
    ):
        assert code in text, f"the schema doc does not name the {code} refusal"


# ---------------------------------------------------------------- the live seam
def _live_corpus(tmp_path: Path, record: str, name: str = "live") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "live.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spec xmlns="urn:auger:spec:1" id="AUGER-SPEC-LIVE-DECISION" version="1.0.0" '
        'schema-version="1.0">\n'
        '  <requirement id="AUG-REQ-LIVE-DECISION-1" kind="requirement" status="active" '
        'version="1.0.0"\n'
        '               applies-to="auger.spec.obligation" verification="test"\n'
        f'               decision-ref="{record}">\n'
        "    A requirement resting on a decision that really exists in this namespace.\n"
        "  </requirement>\n"
        "</spec>\n",
        encoding="utf-8",
    )
    return root


def test_live_resolves_a_real_record_and_refuses_a_missing_one(
    tmp_path: Path, decided: dict
) -> None:
    """Against a REAL namespace: the stored decision resolves, an absent id refuses."""
    ns = decided["ns"]
    present = _live_corpus(tmp_path, decided["D001"]["did"], "present")
    absent = _live_corpus(tmp_path, "D-999", "absent")

    rc, out, err = run_spec(
        "validate",
        str(present),
        "--repo-root",
        str(REPO_ROOT),
        "--decision-namespace",
        ns,
    )
    assert rc == 0, f"a stored decision was refused: {err}"
    assert "ok" in out, out

    rc, out, err = run_spec(
        "validate",
        str(absent),
        "--repo-root",
        str(REPO_ROOT),
        "--decision-namespace",
        ns,
    )
    assert rc == 2, f"a missing decision record produced rc={rc}: {out!r}"
    assert "unresolved-decision-ref" in err, err
    assert "D-999" in err, err
    assert ns in err, err


def test_live_reports_a_superseded_record(tmp_path: Path, decided: dict) -> None:
    """A superseded decision is a typed diagnostic on the real substrate, with its successor."""
    ns = decided["ns"]
    old = decided["D001"]["did"]
    rc, out = _answer(
        ns,
        did="D-003",
        chosen="single Postgres instance",
        supersedes=old,
    )
    assert rc == 0 and "recorded" in out, f"could not supersede {old}: {out}"

    corpus_root = _live_corpus(tmp_path, old)
    rc, out, err = run_spec(
        "validate",
        str(corpus_root),
        "--repo-root",
        str(REPO_ROOT),
        "--decision-namespace",
        ns,
    )
    assert rc == 2, f"a superseded decision was accepted: rc={rc} {out!r}"
    assert "superseded-decision-ref" in err, err
    assert old in err, err
    assert "D-003" in err, f"the successor is not named: {err!r}"


def _answer(ns: str, *, did: str, chosen: str, supersedes: str) -> tuple[int, str]:
    """Record an answer through the CLI (the same path a person uses), in-process."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = auger.main(
            [
                "-n",
                ns,
                "answer",
                "--id",
                did,
                "--chosen",
                chosen,
                "--option",
                chosen,
                "--why-not",
                "the alternative needs a service the seed forbids",
                "--confidence",
                "0.7",
                "--supersedes",
                supersedes,
                "--supersedes-why",
                "measured replacement",
            ]
        )
    return rc, buf.getvalue()


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_decision_ref.py" in arm[0], (
        "tests/test_spec_decision_ref.py must be named in tests/gate.sh's pytest arm"
    )
