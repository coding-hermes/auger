"""Focused regression coverage for the auger usage skill's domain guidance."""

from __future__ import annotations

from pathlib import Path

import pytest

import auger


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "auger-usage" / "SKILL.md"


def _canonical_grid() -> list[dict[str, str]]:
    return [
        {"num": f"4.{number:02d}", "name": f"domain-{number:02d}"}
        for number in range(1, 45)
    ]


def test_usage_skill_describes_the_canonical_domain_cli_and_refusals():
    """The usage skill must not teach the retired free-form domain contract."""
    text = SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "unvalidated " + "free text" not in lowered
    assert "aug-038 closed" in lowered
    assert "canonical grid number" in lowered
    assert "4.01-4.44" in text
    assert "4.05=data" in text
    assert "are descriptive, not accepted cli syntax" in lowered
    assert "one-index-off" not in lowered
    assert "unknown or malformed values" in lowered


def test_usage_skill_describes_failed_not_reached_advances_as_drift():
    """The NOT-REACHED suffix is documented as a failed write and warning."""
    text = SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "aug-059-r2" in lowered
    assert "row status not-reached" in lowered
    assert "failed seed-row" in lowered
    assert "advance write" in lowered
    assert "surfaced warning" in lowered
    assert "drift signal" in lowered
    assert "do not dismiss or distrust" in lowered
    assert "display noise" in lowered
    assert "display quirk" not in lowered


def test_unknown_domain_forms_remain_refused(monkeypatch):
    """The forms called out by the skill are rejected before any write."""
    monkeypatch.setattr(auger, "read_domain_grid", _canonical_grid)

    assert auger.resolve_answer_domain("4.05") == ("4.05", "domain-05")
    for value in ("4.05-data", "4.05=data", "data", "4.9", "4.99"):
        with pytest.raises(SystemExit, match="unknown domain"):
            auger.resolve_answer_domain(value)


def test_status_lines_keep_grid_number_and_name_paired():
    """Status rendering must not shift a grid name onto a neighboring number."""
    for entry in _canonical_grid():
        line = auger.domain_line(
            {
                "num": entry["num"],
                "name": entry["name"],
                "row": {"status": auger.DOMAIN_SEED_STATUS, "terminating_ring": None},
                "grid": {"triage": 6, "ring_floor": 2, "terminating_ring": None},
                "evidence": 0,
                "off_grid": False,
            }
        )
        assert line.startswith(f"  {entry['num']}  {entry['name']:<22}"), line
