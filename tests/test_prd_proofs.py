"""AUG-137: the PRD's replayable proofs have a runner, and the runner cannot be quietly narrowed.

The replays themselves run in the gate's own `== prd proofs (AUG-137) ==` arm
(`python3 scripts/replay_prd_proofs.py --check`), so this file holds the CONTRACT arms — the
properties that make that arm trustworthy and that fail fast (sub-second, no subprocess replays):

  coverage ......... the reviewed allowlist classifies every PRD bullet, by sha256 of its text
  no quiet narrowing a required proof without an implemented replay fails; a deferral needs an owner
  no quiet dropping a dropped allowlist entry is refused (the PRD still lists the proof)
  artifact .......... the checked-in artifact records every proof, and its verdict is pass
  deferrals ......... every deferred proof is recorded `not-run`, never `pass`
  the arm itself .... tests/gate.sh runs the proof runner

ch:trace row=AUG-137 spec=docs/prd/xml-spec-graph.md#success-criteria--replayable-proofs test=tests/test_prd_proofs.py doc=docs/prd/evidence/AUG-137-prd-proofs.json
"""

from __future__ import annotations

import json

import pytest

from conftest import REPO_ROOT  # tests/conftest.py defines and exports it

from scripts import replay_prd_proofs as runner  # noqa: E402  (needs REPO_ROOT on sys.path first)

ARTIFACT = REPO_ROOT / "docs" / "prd" / "evidence" / "AUG-137-prd-proofs.json"
GATE = REPO_ROOT / "tests" / "gate.sh"


def _parsed() -> list:
    return runner.parse_prd_proofs(runner.PRD_PATH)


def _allowlist() -> dict:
    return runner.load_allowlist(runner.ALLOWLIST_PATH)


def _artifact() -> dict:
    assert ARTIFACT.is_file(), (
        f"{ARTIFACT} is missing — record a run: python3 scripts/replay_prd_proofs.py"
    )
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_the_prd_still_lists_eight_replayable_proofs() -> None:
    """Non-vacuous: the section is found and carries the bullets the row is about."""
    parsed = _parsed()
    assert len(parsed) == 8, (
        f"the PRD's '## {runner.SECTION_TITLE}' section has {len(parsed)} bullet(s), not 8 — if "
        "the PRD changed, re-review the allowlist: "
        "python3 scripts/replay_prd_proofs.py --print-prd-proofs"
    )
    assert [proof["id"] for proof in parsed] == [f"P{n}" for n in range(1, 9)]


def test_the_allowlist_classifies_every_prd_bullet() -> None:
    """Coverage is reconciled by sha256, in both directions (uncovered bullet OR stale entry)."""
    rows = runner.reconcile(_parsed(), _allowlist())
    assert len(rows) == 8
    assert {row["prd_sha256"] for row in rows} == {
        proof["sha256"] for proof in _parsed()
    }
    for row in rows:
        assert row["disposition"] in ("required", "deferred")
        if row["disposition"] == "deferred":
            assert row["owner"].strip(), (
                f"{row['id']}: a deferral must name its owning row"
            )
            assert row["reason"].strip(), f"{row['id']}: a deferral must carry a reason"


def test_every_required_proof_has_an_implemented_replay() -> None:
    """The quiet-narrowing guard: a REQUIRED proof with no replay is a hole, not a green."""
    required = [
        row["id"]
        for row in runner.reconcile(_parsed(), _allowlist())
        if row["disposition"] == "required"
    ]
    assert required, (
        "no proof is REQUIRED — the arm would pass without replaying anything"
    )
    missing = [proof_id for proof_id in required if proof_id not in runner.REPLAYS]
    assert not missing, f"REQUIRED proof(s) with no implemented replay: {missing}"


def test_dropping_an_allowlist_entry_is_refused() -> None:
    """Deleting a classification cannot silently retire a proof."""
    allowlist = _allowlist()
    allowlist["proofs"] = allowlist["proofs"][:-1]
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(_parsed(), allowlist)
    assert "in no allowlist entry" in str(caught.value), caught.value


def test_a_deferral_without_an_owner_is_refused() -> None:
    """A deferral is a reviewed decision: it must name the row that owns the missing capability."""
    allowlist = _allowlist()
    deferred = next(
        entry for entry in allowlist["proofs"] if entry["disposition"] == "deferred"
    )
    deferred.pop("owner", None)
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(_parsed(), allowlist)
    assert "must name the board row" in str(caught.value), caught.value


def test_an_edited_prd_bullet_is_refused() -> None:
    """The sha pin: rewriting a bullet's text forces a re-review of its disposition."""
    parsed = _parsed()
    parsed[0] = {**parsed[0], "sha256": "0" * 64}
    with pytest.raises(runner.CoverageError) as caught:
        runner.reconcile(parsed, _allowlist())
    assert "in no allowlist entry" in str(caught.value), caught.value


def test_the_check_artifact_records_every_proof_and_passes() -> None:
    """The checked-in artifact is the record of a real run: 8 entries, verdict pass."""
    artifact = _artifact()
    assert artifact["generated_by"] == "scripts/replay_prd_proofs.py"
    proofs = artifact["proofs"]
    assert [proof["id"] for proof in proofs] == [f"P{n}" for n in range(1, 9)]
    assert artifact["verdict"] == "pass", artifact["required_not_passing"]
    assert artifact["required_not_passing"] == []
    for proof in proofs:
        assert proof["status"] in ("pass", "fail", "not-run")
        assert proof["command"].strip(), f"{proof['id']}: no command recorded"
        if proof["status"] == "pass":
            assert proof["corpus_bytes"] and proof["bundle_bytes"], (
                f"{proof['id']}: a passing proof must carry measured bytes"
            )
            assert proof["latency_ms"] and proof["latency_ms"] > 0, (
                f"{proof['id']}: a passing proof must carry a measured latency"
            )
        else:
            assert proof["corpus_bytes"] is None and proof["latency_ms"] is None, (
                f"{proof['id']}: {proof['status']} must not carry measurements"
            )
            assert proof["detail"].strip(), f"{proof['id']}: a non-pass needs a reason"


def test_every_deferred_proof_is_recorded_not_run() -> None:
    """A deferral is never a pass — that is the whole point of the status vocabulary."""
    artifact = {proof["id"]: proof for proof in _artifact()["proofs"]}
    for entry in _allowlist()["proofs"]:
        if entry["disposition"] != "deferred":
            continue
        recorded = artifact[entry["id"]]
        assert recorded["status"] == "not-run", (
            f"{entry['id']} is deferred to {entry.get('owner')} but the artifact records "
            f"{recorded['status']!r}"
        )
        assert recorded["detail"].startswith("not-run (deferred to"), recorded["detail"]


def test_the_recorded_bytes_obey_the_documented_size_floor() -> None:
    """Proof 7's numbers come from the run and are held to the same floor the replay asserts."""
    artifact = {proof["id"]: proof for proof in _artifact()["proofs"]}
    proof = artifact["P7"]
    assert proof["status"] == "pass"
    assert proof["bundle_bytes"] < proof["corpus_bytes"], (
        "the recorded bundle is not smaller than the corpus it was selected from"
    )
    ratio = proof["bundle_bytes"] / proof["corpus_bytes"]
    assert ratio <= runner.SIZE_FRACTION_FLOOR, (
        f"the recorded bundle is {ratio:.1%} of the corpus, above the "
        f"{runner.SIZE_FRACTION_FLOOR:.0%} floor — the artifact and the replay disagree"
    )


def test_gate_arm_runs_the_proof_runner() -> None:
    """The runner must run on every gate, or a regressed proof breaks nothing."""
    text = GATE.read_text(encoding="utf-8")
    assert "scripts/replay_prd_proofs.py --check" in text, (
        "tests/gate.sh does not run the PRD proof runner — the proofs would be unrun"
    )
