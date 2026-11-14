"""AUG-142: the opt-in semantic finding path for `spec align` — evidence, uncertainty, no-write.

The checker's findings were location/marker findings only: `{code, severity, why, evidence}`
built by `_spec_finding`. This row adds an OPT-IN (`--semantic`, default OFF) semantic pass whose
findings carry a richer, machine-checkable shape — every one must carry ALL of
`{class, blocks[], rule_id, reason, evidence_excerpt, source_digest, uncertainty{band, score}}`,
because a semantic claim without the literal source text that justifies it is a fabricated
rationale, and one without an uncertainty statement reads as certainty it cannot have.

What each test proves:

  contradiction reported with every required field ... test_a_semantic_contradiction_is_reported_with_every_required_field
  consistent corpus -> no semantic finding ......... test_a_semantically_consistent_corpus_emits_no_semantic_finding
  missing evidence excerpt refused (typed) ......... test_a_semantic_finding_without_an_evidence_excerpt_is_refused
  the CLI refuses a corpus with no citable text .... test_the_cli_refuses_a_corpus_whose_cited_block_has_no_citable_text
  uncertainty is mandatory and validated ........... test_uncertainty_is_mandatory_and_validated
  --semantic OFF is byte-identical to legacy ....... test_semantic_off_by_default_matches_the_legacy_payload_byte_shape
  no-write: the corpus tree hash never changes ..... test_the_checker_writes_nothing_to_the_corpus
  never a pass verdict ............................. test_a_semantic_finding_never_carries_a_pass_verdict
  the arm runs this file .......................... test_gate_arm_runs_this_file

Fixtures: tests/fixtures/xml/semantic/{contradiction,consistent,no-evidence}.xml — the positive
pair (SEM-REQ-1 active depends-on SEM-OLD-1 superseded), the false-positive control (all active)
and the evidence-less corpus (SEM-OLD-2 superseded with an empty body, so no literal source text
exists to cite and the construction path must refuse rather than emit a fabricated rationale).

ch:trace row=AUG-142 spec=docs/XML-SPEC-SCHEMA.md test=tests/test_spec_semantic_findings.py evidence=evidence/AUG-142/ verdict=none:pending-judge commit=none:pre-commit
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (the single-module CLI; the constructor tests drive it directly)

AUGER = REPO_ROOT / "auger.py"
GATE = REPO_ROOT / "tests" / "gate.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "xml" / "semantic"

#: Isolate throwaway repositories from this host's global/system git config: a global
#: `core.hooksPath` (or `commit.gpgsign`) turns a scratch commit into someone else's hook.
SCRATCH_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

SEMANTIC_FIELDS = {
    "class",
    "blocks",
    "rule_id",
    "reason",
    "evidence_excerpt",
    "source_digest",
    "uncertainty",
}
LEGACY_FIELDS = {"code", "severity", "why", "evidence"}


# ------------------------------------------------------------------------------------- helpers
def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=SCRATCH_GIT_ENV,
        check=False,
    )


def build_repo(base: Path, fixture: str) -> Path:
    """A throwaway repository whose specs/ holds ONE of the semantic fixtures."""
    repo = base / "consumer"
    (repo / "specs").mkdir(parents=True)
    assert run_git(repo, "init", "-q").returncode == 0
    run_git(repo, "config", "user.email", "aug142@example.invalid")
    run_git(repo, "config", "user.name", "AUG-142")
    (repo / "specs" / "semantic.xml").write_text(
        (FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8"
    )
    staged = run_git(repo, "add", "--", ".")
    assert staged.returncode == 0, staged.stderr
    committed = run_git(repo, "commit", "-qm", "initial")
    assert committed.returncode == 0, committed.stderr
    return repo


def tree_digest(root: Path) -> str:
    """A stable digest of every file under `root` (names AND contents), for the no-write pin."""
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append(
                f"{path.relative_to(root)}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def run_cli(repo: Path, *extra: str) -> tuple[int, str, str]:
    """`spec align` with the diff source omitted? No — an empty staged diff (nothing staged)."""
    interpreter = os.path.abspath(sys.executable)
    argv = [
        interpreter,
        str(AUGER),
        "spec",
        "align",
        "--repo-root",
        str(repo.resolve()),
        "--spec-root",
        str((repo / "specs").resolve()),
        "--protocol-version",
        "1",
        "--diff-stdin",
        *extra,
    ]
    done = subprocess.run(
        argv,
        input="",
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=SCRATCH_GIT_ENV,
        check=False,
        timeout=120,
    )
    return done.returncode, done.stdout, done.stderr


def payload_of(out: str) -> dict:
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, (
        "the checker must emit exactly one JSON line: engine.spec_alignment._parse_payload "
        f"json.loads each LINE. Got {len(lines)} line(s):\n{out}"
    )
    return json.loads(lines[0])


def semantic_findings(payload: dict) -> list:
    return [f for f in payload["findings"] if f.get("class") == "semantic"]


# ------------------------------------------------------------------------- AC-1: the rich shape
def test_a_semantic_contradiction_is_reported_with_every_required_field(
    tmp_path: Path,
) -> None:
    """AC-1: an active block citing a superseded one is reported with the full semantic shape."""
    repo = build_repo(tmp_path, "contradiction.xml")
    code, out, err = run_cli(repo, "--semantic")
    assert code == 0, (
        f"a warn-only semantic finding must not fail the run: {code} {err}"
    )
    payload = payload_of(out)
    found = semantic_findings(payload)
    assert found, f"the contradiction fixture must emit a semantic finding: {payload}"

    finding = found[0]
    assert SEMANTIC_FIELDS <= set(finding), (
        f"the semantic finding is missing required fields: {sorted(finding)}"
    )
    assert finding["class"] == "semantic"
    assert finding["rule_id"], finding
    assert set(finding["blocks"]) == {"SEM-REQ-1", "SEM-OLD-1"}, finding
    assert "superseded" in finding["reason"], finding

    # The evidence excerpt is the LITERAL source text of the cited block, and the digest is
    # a hash OF that text — a caller can re-derive the digest from the excerpt.
    cited = auger.load_spec_corpus(repo / "specs")[2]
    cited_text = next(b["text"].strip() for b in cited if b["id"] == "SEM-OLD-1")
    assert finding["evidence_excerpt"].strip() == cited_text, finding
    assert (
        finding["source_digest"]
        == hashlib.sha256(cited_text.encode("utf-8")).hexdigest()
    ), finding

    uncertainty = finding["uncertainty"]
    assert uncertainty["band"] in {"high", "medium", "low"}, finding
    assert isinstance(uncertainty["score"], (int, float)), finding
    assert 0.0 <= uncertainty["score"] <= 1.0, finding
    # Legacy keys ride along so ONE sort/counts/exit-code path serves both classes.
    assert LEGACY_FIELDS <= set(finding), finding


def test_a_semantically_consistent_corpus_emits_no_semantic_finding(
    tmp_path: Path,
) -> None:
    """AC-5 (false-positive control): a corpus with consistent statuses emits nothing semantic."""
    repo = build_repo(tmp_path, "consistent.xml")
    code, out, err = run_cli(repo, "--semantic")
    assert code == 0, f"a consistent corpus must pass: {code} {err}"
    payload = payload_of(out)
    assert semantic_findings(payload) == [], (
        f"the consistent fixture must not be reported: {payload['findings']}"
    )


# ------------------------------------------------------------- AC-2: the fabricated-rationale guard
def test_a_semantic_finding_without_an_evidence_excerpt_is_refused() -> None:
    """AC-2: the CONSTRUCTION PATH refuses a semantic finding with no evidence excerpt."""
    base = dict(
        blocks=["A-1", "B-2"],
        rule_id="active-cites-deprecated-status",
        reason="A-1 is active but cites B-2, superseded",
        source_digest="0" * 64,
        uncertainty={"band": "high", "score": 1.0},
    )
    for blank in ("", "   ", "\n\t", None):
        with pytest.raises(auger.SpecError) as excinfo:
            auger._spec_semantic_finding(**base, evidence_excerpt=blank)
        assert excinfo.value.code == "finding-missing-evidence", (
            f"evidence={blank!r} must be refused as finding-missing-evidence, got "
            f"{excinfo.value.code}"
        )


def test_the_cli_refuses_a_corpus_whose_cited_block_has_no_citable_text(
    tmp_path: Path,
) -> None:
    """AC-2 end to end: no citable source text -> typed refusal, rc 2, no payload.

    The refusal lives in the construction path EVERY semantic finding passes through, so a corpus
    where the rule fires but the cited block carries no prose makes the whole run refuse rather
    than emit a finding whose evidence excerpt would be fabricated.
    """
    repo = build_repo(tmp_path, "no-evidence.xml")
    code, out, err = run_cli(repo, "--semantic")
    assert code == 2, (
        f"a fabricated-rationale run must refuse with rc 2, got {code}: {out}"
    )
    assert "spec error (finding-missing-evidence)" in err, err
    assert out.strip() == "", (
        "a refused run emits NO payload a caller could mistake for a verdict"
    )


# ----------------------------------------------------------------------- AC-3: uncertainty is mandatory
def test_uncertainty_is_mandatory_and_validated() -> None:
    """AC-3: a finding without band/score is refused; a bad band or an out-of-range score too."""
    base = dict(
        blocks=["A-1", "B-2"],
        rule_id="active-cites-deprecated-status",
        reason="A-1 is active but cites B-2, superseded",
        evidence_excerpt="Superseded requirement retained for history.",
        source_digest="0" * 64,
    )
    for missing in ({}, {"band": "high"}, {"score": 0.5}, None):
        with pytest.raises(auger.SpecError) as excinfo:
            auger._spec_semantic_finding(**base, uncertainty=missing)
        assert excinfo.value.code == "finding-missing-uncertainty", (
            f"uncertainty={missing!r} must be refused as finding-missing-uncertainty, got "
            f"{excinfo.value.code}"
        )
    for bad in (
        {"band": "certain", "score": 0.5},
        {"band": "high", "score": 1.5},
        {"band": "high", "score": -0.1},
        {"band": "high", "score": "high"},
    ):
        with pytest.raises(auger.SpecError) as excinfo:
            auger._spec_semantic_finding(**base, uncertainty=bad)
        assert excinfo.value.code == "finding-invalid-uncertainty", (
            f"uncertainty={bad!r} must be refused as finding-invalid-uncertainty, got "
            f"{excinfo.value.code}"
        )


# ------------------------------------------------------------- AC-1 (default): byte-identical legacy output
def test_semantic_off_by_default_matches_the_legacy_payload_byte_shape(
    tmp_path: Path,
) -> None:
    """Without --semantic the emitted JSON must be byte-identical to today's: legacy findings
    carry EXACTLY the four legacy keys (a new key would change the bytes), no semantic class
    appears, and two consecutive runs are byte-identical."""
    repo = build_repo(tmp_path, "contradiction.xml")
    code, out1, err = run_cli(repo)
    assert code == 0, err
    payload = payload_of(out1)
    for finding in payload["findings"]:
        assert set(finding) == LEGACY_FIELDS, (
            "a finding without --semantic must keep the exact legacy shape "
            f"{sorted(LEGACY_FIELDS)}; got {sorted(finding)}"
        )
        assert "semantic" not in json.dumps(finding), finding
    code, out2, _ = run_cli(repo)
    assert code == 0
    assert out1 == out2, "the legacy payload must be byte-identical across runs"


# ------------------------------------------------------------------------------- AC-4: no write
def test_the_checker_writes_nothing_to_the_corpus(tmp_path: Path) -> None:
    """AC-4: a run that emits semantic findings leaves the corpus tree byte-for-byte unchanged."""
    repo = build_repo(tmp_path, "contradiction.xml")
    specs = repo / "specs"
    before = tree_digest(specs)
    code, out, err = run_cli(repo, "--semantic")
    assert code == 0, err
    payload = payload_of(out)
    assert semantic_findings(payload), (
        "this pin is only meaningful on a run that actually emitted semantic findings"
    )
    assert tree_digest(specs) == before, (
        "the checker wrote to the corpus tree: `spec align` reads only, never writes"
    )
    after_all = tree_digest(repo)
    code2, out2, _ = run_cli(repo, "--semantic")
    assert code2 == 0 and out2 == out
    assert tree_digest(repo) == after_all


# --------------------------------------------------------------------- AC-5: findings never certify
def test_a_semantic_finding_never_carries_a_pass_verdict(tmp_path: Path) -> None:
    """AC-5: a finding reports a problem; it can never claim verification.

    No semantic finding carries a pass-ish verdict field, its severity is warn/info only, and the
    finding dict has no key a caller could read as a pass verdict.
    """
    repo = build_repo(tmp_path, "contradiction.xml")
    code, out, err = run_cli(repo, "--semantic")
    assert code == 0, err
    payload = payload_of(out)
    found = semantic_findings(payload)
    assert found, payload
    verdict_shaped = {"verdict", "verified", "outcome", "result", "status"}
    for finding in found:
        assert set(finding) & verdict_shaped == set(), (
            f"a finding must not carry a verdict-shaped field: {sorted(finding)}"
        )
        assert finding["severity"] in {"warn", "info"}, finding
    # And nowhere in the payload does any finding claim verification: findings report problems,
    # they never certify.
    for finding in payload["findings"]:
        for key in verdict_shaped & set(finding):
            value = str(finding[key]).strip().lower()
            assert value not in {"pass", "passing", "verified", "ok"}, (key, finding)


def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_semantic_findings.py" in arm[0], (
        "tests/test_spec_semantic_findings.py must be in tests/gate.sh's pytest arm — the "
        "guard's test command IS the gate, so a proof file outside it is never run"
    )
