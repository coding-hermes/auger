"""Verdict-hygiene regression tests (AUG-158 / AUG-159).

Two tier1 reds from the 2026-11-08 GitReins verdict (b15d6edc) that had nothing to do
with the judged change but blocked EVERY verdict in this repo:

* AUG-158 — the repo gate's lint arm grades only ``auger.py``, while gitreins' tier1
  lint grades ALL tracked python. A committed evidence probe carrying an unused import
  (``evidence/sg8/arm_bite_probe.py:19``) turned every verdict's lint arm red.
* AUG-159 — the ``sk-api-key`` gitleaks rule had no left boundary, so the repo's own
  error-code literal ``task-invalid-acceptance-ref`` (whose tail reads ``sk-`` + 23
  chars) fired it five-plus times per scan.

Both tests FAIL on the pre-fix tree and PASS after it. They are offline by design:
no live DuckBrain substrate, and gitleaks itself is never shelled out to from the
unit test (the real-binary catch proof is a documented manual step in the row's
evidence dir, not a flaky network/tool dependency here).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "tests" / "gate.sh"
GITLEAKS_TOML = REPO_ROOT / ".gitleaks.toml"

# The error-code literal carried by auger.py / tests / the board JSONL. Its tail is
# exactly what the old rule matched; the boundary fix must keep refusing it.
ERROR_CODE_LITERAL = "task-invalid-acceptance-ref"
# A real-shaped key: "sk-" + 32 alphanumerics.
FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def _sk_api_key_rule_regex() -> str:
    """Return the regex of the sk-api-key rule from .gitleaks.toml."""
    data = tomllib.loads(GITLEAKS_TOML.read_text(encoding="utf-8"))
    rules = [r for r in data.get("rules", []) if r.get("id") == "sk-api-key"]
    assert rules, "the sk-api-key rule vanished from .gitleaks.toml — it must exist"
    return rules[0]["regex"]


class TestGitleaksLeftBoundary:
    """AUG-159: the sk-api-key rule needs a LEFT boundary, not a path allowlist."""

    def test_rule_regex_has_no_lookbehind(self):
        rx = _sk_api_key_rule_regex()
        # RE2 (gitleaks' engine) rejects lookbehinds/lookaheads outright — a Python
        # dialect pattern would make the whole config unloadable there. Non-capturing
        # groups (?:...) are fine and are in fact the fix's boundary.
        for dialect in ("(?<", "(?=", "(?!"):
            assert dialect not in rx, (
                f"Python/PCRE dialect {dialect} in sk-api-key regex: {rx}"
            )

    def test_error_code_literal_does_not_match(self):
        rx = _sk_api_key_rule_regex()
        # bare literal
        assert re.search(rx, ERROR_CODE_LITERAL) is None, (
            "sk-api-key still fires inside the repo's own error-code literal"
        )
        # the literal embedded in realistic contexts (JSONL line, python source)
        for context in (
            f'{{"code": "{ERROR_CODE_LITERAL}"}}',
            f'raise SpecError("{ERROR_CODE_LITERAL}")',
            f"# hint: {ERROR_CODE_LITERAL} means the acceptance ref is dangling",
        ):
            assert re.search(rx, context) is None, f"false positive in: {context!r}"

    def test_real_shaped_key_still_matches(self):
        rx = _sk_api_key_rule_regex()
        # at start of input (the ^ alternative)
        assert re.search(rx, FAKE_KEY) is not None
        # mid-text after a non-alphanumeric delimiter
        assert re.search(rx, f"OPENROUTER_API_KEY={FAKE_KEY}") is not None
        # and after whitespace
        assert re.search(rx, f"key {FAKE_KEY}") is not None

    def test_quoted_literal_is_allowlisted_but_key_is_not(self):
        # The board prose / evidence transcripts quote the literal after a boundary
        # char (e.g. "('.sk-invalid-acceptance-ref'"), which the left boundary alone
        # still flags. The config carries a value allowlist for that ONE literal;
        # simulate gitleaks' net verdict (rule match MINUS allowlist match).
        rx = _sk_api_key_rule_regex()
        quoted = "'.sk-invalid-acceptance-ref'"
        rule_match = re.search(rx, quoted)
        assert rule_match is not None, (
            "left boundary unexpectedly refuses the quoted form"
        )
        allowlist = tomllib.loads(GITLEAKS_TOML.read_text(encoding="utf-8")).get(
            "allowlist", {}
        )
        allowlist_rx = allowlist.get("regexes", [])
        assert allowlist_rx, "value allowlist for the error-code literal vanished"
        assert any(re.search(p, rule_match.group(0)) for p in allowlist_rx), (
            "the quoted error-code literal is no longer allowlisted — tier1 secrets goes red"
        )
        # and a real-shaped key is NEVER covered by the allowlist
        assert not any(re.search(p, FAKE_KEY) for p in allowlist_rx)


class TestLintScopeMatchesTier1:
    """AUG-158: everything gitreins' tier1 lints, the repo must lint too."""

    def test_gate_arm_names_verdict_hygiene(self):
        # The pytest arm list in tests/gate.sh is hand-maintained and re-narrows
        # silently; this file is only enforced if the arm names it.
        assert "tests/test_verdict_hygiene.py" in GATE.read_text(encoding="utf-8"), (
            "tests/gate.sh's pytest arm no longer names tests/test_verdict_hygiene.py"
        )

    def test_all_tracked_python_is_ruff_clean(self):
        ruff = shutil.which("ruff")
        if ruff is None:
            # ~/.local/bin may not be on PATH under every runner — try the pinned spot.
            pinned = Path("/home/kara/.local/bin/ruff")
            if pinned.exists():
                ruff = str(pinned)
        if ruff is None:
            import pytest

            pytest.skip(
                "ruff not found on PATH or ~/.local/bin — lint scope NOT checked"
            )
        tracked = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert tracked, (
            "git ls-files returned no python files — run from a repo checkout"
        )
        proc = subprocess.run(
            [ruff, "check", "--force-exclude", *tracked],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            "ruff over ALL tracked python is red (gitreins tier1 lints exactly this scope):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


if __name__ == "__main__":
    sys.exit(0)
