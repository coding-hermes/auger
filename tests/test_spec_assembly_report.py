"""AUG-140 [PRD AC-14]: the assembly report is MEASURED — bytes, documents, parsed bodies, elapsed.

PRD AC-14, verbatim:

    "Given a representative Auger project selector, when `auger spec assemble` runs, then it reports
     selected-block count, dependency count, input bytes, output bytes, and elapsed time; proof:
     checked-in benchmark fixture produces a JSON report and reads no unselected document bodies
     beyond the index needed for selection."

At this base the assembly JSON carried `schema_version`, `selection`, `counts`, `blocks` and a
`corpus` counter — and NOTHING about bytes, elapsed time, or how many block BODIES were actually
materialized, so "the selection reads no unselected document body" was an assertion, not a number.

What this file pins:
  the report keys exist ...... test_the_measured_report_carries_the_ac14_keys
  real bytes ................. test_output_bytes_is_the_serialized_length
  real input ................. test_input_bytes_is_the_sum_of_the_documents_read
  real laziness .............. test_only_selected_blocks_have_their_body_materialized
  no behaviour change ........ test_the_indexed_path_agrees_with_the_full_loader
  no disguised change ........ test_the_shipped_fixture_still_assembles_unchanged
  measured clock ............. test_elapsed_ms_comes_from_the_clock_seam
                               test_elapsed_ms_is_positive_on_a_real_corpus
  AC-4 still holds ........... test_the_default_json_is_still_byte_identical_across_runs
  documented ................. test_the_report_shape_is_documented
  the arm itself ............. test_gate_arm_runs_this_file

WHY `--measure` IS OPT-IN (a decision, not an omission): PRD AC-4 and
`tests/test_xml_specs.py::test_selection_is_byte_identical_across_runs` pin TWO runs of the same
`assemble --format json` to byte-identical output. `elapsed_ms` is a wall-clock measurement, so a
default report would make that contract unkeepable — the gate would have to choose between AC-4 and
AC-14. The report is therefore one `--measure` flag away, the default document is byte-for-byte what
it always was, and `report_schema_version` names the shape explicitly (1.1) instead of leaving the
report's own version to be inferred from the corpus schema version.

ch:trace row=AUG-140 spec=docs/prd/xml-spec-graph.md#acceptance-criteria
         test=tests/test_spec_assembly_report.py doc=docs/XML-SPEC-SCHEMA.md
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if (
    str(REPO_ROOT) not in sys.path
):  # tests/conftest.py does this too; keep the file standalone
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml" / "specs"
GATE = REPO_ROOT / "tests" / "gate.sh"
SCHEMA_DOC = REPO_ROOT / "docs" / "XML-SPEC-SCHEMA.md"
TRACKED_TEST = "tests/test_spec_render.py"

REPORT_KEYS = (
    "input_bytes",
    "output_bytes",
    "documents_read",
    "blocks_parsed",
    "elapsed_ms",
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


# --------------------------------------------------------------- authored bulk corpus
def spec_document(document_id: str, *blocks: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<spec xmlns="urn:auger:spec:1" id="{document_id}" version="1.0.0" '
        f'schema-version="1.0">\n' + "".join(blocks) + "</spec>\n"
    )


def requirement_block(block_id: str, *, applies_to: str) -> str:
    return (
        f'  <requirement id="{block_id}" kind="requirement" status="active" '
        f'version="1.0.0" applies-to="{applies_to}" verification="test">\n'
        f"    {block_id} prose, deliberately a sentence long enough to measure a body by.\n"
        "  </requirement>\n"
    )


def verification_block(block_id: str, verifies: str) -> str:
    return (
        f'  <verification id="{block_id}" kind="verification" status="active" version="1.0.0" '
        f'applies-to="auger.bulk.verify" method="test" type="test" verifies="{verifies}" '
        f'runner="pytest" path="{TRACKED_TEST}" selector="test_gate_arm_runs_this_file">\n'
        f"    {block_id} proves the picked requirement.\n"
        "  </verification>\n"
    )


FILLER = 28
PICKED = 2


def write_bulk_corpus(root: Path) -> Path:
    """A corpus with far more blocks than the selector matches: 28 filler + 2 picked pairs."""
    root.mkdir(parents=True, exist_ok=True)
    for index in range(1, FILLER + 1):
        (root / f"filler-{index:02d}.xml").write_text(
            spec_document(
                f"BULK-FILLER-{index:02d}",
                requirement_block(
                    f"BULK-FILLER-{index:02d}", applies_to="auger.bulk.filler"
                ),
            ),
            encoding="utf-8",
        )
    for index in range(1, PICKED + 1):
        requirement = f"BULK-PICK-{index}"
        verification = f"BULK-PICK-{index}-V1"
        (root / f"pick-{index:02d}.xml").write_text(
            spec_document(
                f"BULK-DOC-{index}",
                requirement_block(requirement, applies_to="auger.bulk.pick"),
                verification_block(verification, requirement),
            ),
            encoding="utf-8",
        )
    return root


BULK_BLOCKS = FILLER + PICKED * 2


def assemble_measured(root: Path, *selector: str) -> dict:
    code, stdout, stderr = run_spec(
        "assemble", str(root), *selector, "--format", "json", "--measure"
    )
    assert code == 0, (stdout, stderr)
    return json.loads(stdout)


# ------------------------------------------------------------------ the report keys
def test_the_measured_report_carries_the_ac14_keys(tmp_path: Path) -> None:
    """AC-14's five numbers, beside the keys that were already there."""
    payload = assemble_measured(
        write_bulk_corpus(tmp_path / "corpus"), "--applies-to", "auger.bulk.pick"
    )
    for key in REPORT_KEYS:
        assert key in payload, f"the measured report has no {key}: {sorted(payload)}"
    assert payload["report_schema_version"] == auger.SPEC_ASSEMBLY_REPORT_VERSION
    assert payload["counts"] == {"matched": 2, "dependencies": 2, "total": 4}, payload[
        "counts"
    ]
    assert payload["selection"] == "applies-to=auger.bulk.pick"
    assert "blocks" in payload and "schema_version" in payload


def test_output_bytes_is_the_serialized_length(tmp_path: Path) -> None:
    """`output_bytes` is the document's own length — measured, self-referential, and exact."""
    corpus = write_bulk_corpus(tmp_path / "corpus")
    code, stdout, stderr = run_spec(
        "assemble",
        str(corpus),
        "--applies-to",
        "auger.bulk.pick",
        "--format",
        "json",
        "--measure",
    )
    assert code == 0, stderr
    payload = json.loads(stdout)
    assert payload["output_bytes"] == len(stdout.encode("utf-8")), (
        f"output_bytes={payload['output_bytes']} but the emitted document is "
        f"{len(stdout.encode('utf-8'))} bytes"
    )


def test_input_bytes_is_the_sum_of_the_documents_read(tmp_path: Path) -> None:
    """`input_bytes` is what was READ off disk — `os.stat` over the corpus, not a constant."""
    corpus = write_bulk_corpus(tmp_path / "corpus")
    documents = sorted(corpus.rglob("*.xml"))
    payload = assemble_measured(corpus, "--applies-to", "auger.bulk.pick")
    assert payload["documents_read"] == len(documents) == FILLER + PICKED
    assert payload["input_bytes"] == sum(path.stat().st_size for path in documents)


def test_only_selected_blocks_have_their_body_materialized(tmp_path: Path) -> None:
    """Criterion 3: a selection materializes matched + closure bodies, and nothing else.

    This is the arm that goes RED if a full-corpus parse is reintroduced into `assemble`: the corpus
    holds BULK_BLOCKS blocks, the selector matches 2 (plus their 2 obligations), so `blocks_parsed`
    is 4 against `blocks_indexed` of 32. A loader that built every block record would report 32.
    """
    corpus = write_bulk_corpus(tmp_path / "corpus")
    payload = assemble_measured(corpus, "--applies-to", "auger.bulk.pick")
    assert payload["blocks_indexed"] == BULK_BLOCKS, payload["blocks_indexed"]
    assert payload["blocks_parsed"] == payload["counts"]["total"] == 4
    assert (
        payload["blocks_parsed"]
        <= payload["counts"]["matched"] + payload["counts"]["dependencies"]
    )
    assert payload["blocks_parsed"] < payload["blocks_indexed"], (
        "every block in the corpus was parsed for a 4-block selection — the unselected bodies are "
        "not lazy"
    )


def test_the_indexed_path_agrees_with_the_full_loader() -> None:
    """The lazy path is an OPTIMISATION, not a second opinion: same ids, same fields, same order.

    `load_spec_corpus` stays the corpus-wide verdict every other verb uses. If the two paths could
    disagree, `assemble` would answer from a different corpus than `validate` approved — which is
    exactly the defect AUG-134 closed for `validate` vs `check`.
    """
    _root, _documents, eager = auger.load_spec_corpus(FIXTURES)
    indexed = auger.load_spec_corpus_indexed(FIXTURES)
    shallow = indexed["blocks"]
    assert [block["id"] for block in shallow] == [block["id"] for block in eager]
    assert [block["order"] for block in shallow] == [block["order"] for block in eager]
    for light, full in zip(shallow, eager):
        assert light["text"] == "", (
            f"the indexed load materialized {light['id']}'s body — the index must carry attributes, "
            "not prose"
        )
        for key in (
            "id",
            "kind",
            "status",
            "version",
            "applies_to",
            "method",
            "refs",
            "source",
        ):
            assert light[key] == full[key], f"{light['id']} disagrees on {key}"

    selection, records = auger.select_spec_blocks(
        indexed["blocks"], applies_to=("auger.export",), empty_ok=True
    )
    materialized = auger.materialize_spec_records(indexed, records)
    eager_selection, eager_records = auger.select_spec_blocks(
        eager, applies_to=("auger.export",), empty_ok=True
    )
    assert selection == eager_selection
    assert [record["block"] for record in materialized] == [
        record["block"] for record in eager_records
    ], "the materialized blocks are not the full loader's blocks"


def test_the_shipped_fixture_still_assembles_unchanged() -> None:
    """Criterion 5: no behaviour change disguised as instrumentation (no `--measure`)."""
    code, stdout, stderr = run_spec(
        "assemble", str(FIXTURES), "--applies-to", "auger.export", "--format", "json"
    )
    assert code == 0, stderr
    payload = json.loads(stdout)
    assert payload["counts"] == {"matched": 6, "dependencies": 0, "total": 6}, payload[
        "counts"
    ]
    assert sorted(payload) == [
        "blocks",
        "corpus",
        "counts",
        "schema_version",
        "selection",
    ], f"the default JSON gained keys: {sorted(payload)}"
    for key in REPORT_KEYS + ("report_schema_version", "blocks_indexed"):
        assert key not in payload, (
            f"{key} is in the DEFAULT document — measurement must not change the unmeasured shape"
        )


# ------------------------------------------------------------------------ the clock
def test_elapsed_ms_comes_from_the_clock_seam(monkeypatch) -> None:
    """`elapsed_ms` is MEASURED: with the clock pinned to a known delta the report carries it."""
    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(auger, "_spec_clock", lambda: next(ticks))
    code, stdout, stderr = run_spec(
        "assemble",
        str(FIXTURES),
        "--applies-to",
        "auger.export",
        "--format",
        "json",
        "--measure",
    )
    assert code == 0, stderr
    payload = json.loads(stdout)
    assert payload["elapsed_ms"] == 250.0, (
        f"elapsed_ms={payload['elapsed_ms']} — a constant, not the measured 250ms delta"
    )


def test_elapsed_ms_is_positive_on_a_real_corpus(tmp_path: Path) -> None:
    """A real assembly takes real time: the report is not a zero placeholder."""
    corpus = write_bulk_corpus(tmp_path / "corpus")
    payload = assemble_measured(corpus, "--applies-to", "auger.bulk.pick")
    assert isinstance(payload["elapsed_ms"], (int, float))
    assert payload["elapsed_ms"] > 0, (
        f"elapsed_ms={payload['elapsed_ms']} on a {BULK_BLOCKS}-block corpus"
    )


# ------------------------------------------------------------------------ AC-4 guard
def test_the_default_json_is_still_byte_identical_across_runs() -> None:
    """AC-4 wins over the instrumentation: the UNMEASURED document stays byte-reproducible."""
    args = (
        "assemble",
        str(FIXTURES),
        "--applies-to",
        "auger.export",
        "--format",
        "json",
    )
    first_code, first, first_err = run_spec(*args)
    second_code, second, second_err = run_spec(*args)
    assert (first_code, second_code) == (0, 0), (first_err, second_err)
    assert first == second, (
        "two runs of the same measured-free assembly differ — AC-4's determinism contract broke"
    )


# ------------------------------------------------------------------- documentation
def test_measure_without_json_refuses(tmp_path: Path) -> None:
    """A flag that cannot apply must refuse, not be silently ignored."""
    corpus = write_bulk_corpus(tmp_path / "corpus")
    code, stdout, stderr = run_spec(
        "assemble", str(corpus), "--applies-to", "auger.bulk.pick", "--measure"
    )
    assert code == 2, (stdout, stderr)
    assert "spec error (measure-requires-json)" in stderr, stderr
    assert stdout == "", "a refusal must not emit a document"


def test_the_report_shape_is_documented() -> None:
    """A report nobody documents is a shape a consumer guesses at."""
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "report_schema_version" in doc, (
        "docs/XML-SPEC-SCHEMA.md does not name the report version"
    )
    for key in REPORT_KEYS:
        assert key in doc, f"docs/XML-SPEC-SCHEMA.md does not document `{key}`"
    assert "--measure" in doc, "the docs must name the flag that emits the report"
    code, stdout, stderr = run_spec("assemble", "--help")
    assert code == 0, stderr
    assert "--measure" in stdout, stdout


# ------------------------------------------------------------------- the arm itself
def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_assembly_report.py" in arm[0], (
        "tests/test_spec_assembly_report.py is not in the gate's pytest arm — the AC-14 claim "
        "would be an unrun proof"
    )
