"""AUG-135 / AUG-120: the staged-diff checker GitReins actually invokes.

The GitReins half of this seam is BUILT and shipped: ``engine.spec_alignment.run_checker``
spawns ``<command> --repo-root <workdir> --spec-root <spec_root> --protocol-version <n>
--diff-stdin`` (measured at /home/kara/gitreins/engine/spec_alignment.py lines 122-132), feeds it
``git diff --cached --binary --no-ext-diff --no-renames`` on stdin, and reads back ONE JSON object
(``_parse_payload`` reads the LAST JSON *line*, so the payload must be a single line) carrying
``protocol``, ``version`` and ``status`` in ``pass|warn|fail|block``. Until this row the Auger half
did not exist: ``grep -c diff-stdin auger.py`` was 0 and every GitReins test drove a synthetic stub
script, so the seam had never actually been executed.

What each test proves:

  the four flags are accepted, one JSON line ....... test_the_cli_accepts_the_four_checker_flags_and_emits_one_json_line
  AUG-120 AC-5 aligned change -> pass ............. test_an_aligned_change_passes_with_resolved_spec_ids
  AUG-120 AC-5 missing trace -> fail ............... test_a_missing_trace_is_a_named_failure
  AUG-120 AC-5 wrong spec section -> warn .......... test_a_wrong_spec_section_is_reported
  AUG-120 AC-5 contradictory comment -> fail ....... test_a_contradictory_comment_citing_a_superseded_section_fails
  AUG-120 AC-5 no related spec -> unscoped, not pass test_a_change_with_no_related_spec_is_unscoped_not_pass
  AUG-120 AC-3 ch:trace contract .................. test_the_ch_trace_contract_violations_are_distinct_findings
  AUG-120 AC-4 no writes, deterministic ........... test_the_checker_writes_nothing_and_is_deterministic
  AUG-135 AC-3 protocol mismatch fails closed ..... test_a_protocol_version_mismatch_fails_closed_without_orphans
  bounded run ..................................... test_an_oversized_diff_is_refused_with_a_typed_error
  AUG-135 AC-2 GitReins' REAL argv, real binary ... test_gitreins_real_invocation_against_the_real_auger_binary
  an UNREADABLE checkout is ABSENT, not an error . test_the_cross_repo_guard_treats_an_unreadable_checkout_as_absent
  ... and the real-argv leg then SKIPS ............ test_the_cross_repo_leg_skips_on_an_unreadable_checkout
  a readable checkout is NOT treated as absent ... test_the_cross_repo_guard_lets_a_readable_checkout_run
  the probe is a READ, not a bare existence test . test_the_readability_probe_asks_os_access_for_r_ok
  absence keeps its OWN reason, distinct ......... test_the_cross_repo_guard_reports_an_absent_checkout_as_absent
  a REAL mode-000 module skips too (not uid 0) ... test_a_real_chmod_to_zero_is_reported_as_unreadable
  AUG-120 AC-1 documented machine-readable surface . test_the_checker_contract_is_documented
  the arm runs this file .......................... test_gate_arm_runs_this_file

ch:trace row=AUG-135,AUG-120 spec=docs/prd/xml-spec-graph.md#R11 test=tests/test_spec_alignment_check.py doc=docs/XML-SPEC-SCHEMA.md evidence=~/.hermes/goals/evidence/auger-SG5/ verdict=none:pending-judge commit=none:pre-commit
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUGER = REPO_ROOT / "auger.py"
GATE = REPO_ROOT / "tests" / "gate.sh"
SCHEMA_DOC = REPO_ROOT / "docs" / "XML-SPEC-SCHEMA.md"
VERBS_DOC = REPO_ROOT / "docs" / "VERBS.md"
#: The peer checkout whose real argv builder this file drives. Override on a host where the
#: GitReins repository lives elsewhere; absent, that ONE integration test skips with its reason
#: (it is a cross-repo proof, not something a wheel install can have).
GITREINS = Path(os.environ.get("GITREINS_REPO", "/home/kara/gitreins"))

#: Isolate throwaway repositories from this host's global/system git config: a global
#: `core.hooksPath` (or `commit.gpgsign`) turns a scratch commit into someone else's hook.
SCRATCH_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

# ------------------------------------------------------- the cross-repo availability guard
#: The one module the cross-repo leg imports, relative to the checkout root. Named once so the
#: guard and the leg cannot drift apart.
GITREINS_MODULE = Path("engine") / "spec_alignment.py"


def _readable(path: Path) -> bool:
    """Can the RUNNING user actually READ ``path``?

    ``Path.is_file()`` proves existence, not usability: a checkout owned by another user (a
    root-owned ``~/gitreins`` on a bunker agent, a umask-077 directory) passes it, and then
    ``from engine import spec_alignment`` raises ``PermissionError`` at IMPORT time — an ERROR in
    three battery cells (tests, ci-pass, chaos-resource) for one root cause. This probe is the
    real read check, not a stat.
    """
    try:
        return os.access(path, os.R_OK)
    except OSError:  # a path this process cannot even resolve is not readable
        return False


def gitreins_cross_repo_skip_reason(checkout: Path) -> str:
    """``""`` when the real-argv leg can run; otherwise the reason it must SKIP.

    The reason names the path AND which condition failed — absent or unreadable — because it is
    quoted verbatim in QA evidence, and a skip with no cause is indistinguishable from a proof
    that was quietly disabled.
    """
    module_path = checkout / GITREINS_MODULE
    if not module_path.is_file():
        return (
            f"cross-repo proof: no GitReins checkout at {checkout} (set GITREINS_REPO); "
            f"{module_path} is ABSENT, so the real-argv leg runs only on a host that has both "
            "repositories"
        )
    if not _readable(module_path):
        return (
            f"cross-repo proof: the GitReins checkout at {checkout} is present but "
            f"{module_path} is UNREADABLE by uid {os.geteuid()}; the real-argv leg skips instead "
            "of raising PermissionError on import"
        )
    return ""


MARKER_TEST = '"""Marker test cited by the contract corpus."""\n\n\ndef test_marker():\n    assert True\n'

#: A corpus in the shipped grammar with four blocks:
#:
#:   ALT-REQ-1  requirement, verifies ALT-TEST-1
#:   ALT-TEST-1 <test> block whose path= is tests/test_marker.py  -> scopes that file
#:   ALT-VER-2  <verification> whose path= is src/alpha.py        -> scopes a DIFFERENT file
#:   ALT-OLD-1  requirement with status="superseded"              -> a lifecycle contradiction
#:
#: src/lonely.py is cited by nothing, which is what "no related spec" means.
CORPUS = (
    '<spec xmlns="urn:auger:spec:1" id="ALT-CONTRACT" version="1.0.0" schema-version="1.0">\n'
    '  <requirement id="ALT-REQ-1" kind="requirement" status="active" version="1.0.0"\n'
    '               verification="test" verifies="ALT-TEST-1" applies-to="alt.tests">\n'
    "    The marker test is the proof of this requirement.\n"
    "  </requirement>\n"
    '  <test id="ALT-TEST-1" kind="test" status="active" version="1.0.0" type="test"\n'
    '        runner="pytest" selector="test_marker" path="tests/test_marker.py">\n'
    "    The tracked marker test.\n"
    "  </test>\n"
    '  <verification id="ALT-VER-2" kind="verification" status="active" version="1.0.0"\n'
    '                method="inspection" path="src/alpha.py">\n'
    "    Inspection of the alpha source.\n"
    "  </verification>\n"
    '  <requirement id="ALT-OLD-1" kind="requirement" status="superseded" version="1.0.0"\n'
    '               verification="analysis" applies-to="alt.legacy">\n'
    "    Superseded requirement retained for history.\n"
    "  </requirement>\n"
    "</spec>\n"
)


# ------------------------------------------------------------------------------------- helpers
def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=SCRATCH_GIT_ENV,
        check=False,
    )


def build_repo(base: Path) -> Path:
    """A throwaway repository holding the contract corpus and the files it scopes."""
    repo = base / "consumer"
    for directory in ("specs", "tests", "src", "docs"):
        (repo / directory).mkdir(parents=True)
    assert run_git(repo, "init", "-q").returncode == 0
    run_git(repo, "config", "user.email", "sg5@example.invalid")
    run_git(repo, "config", "user.name", "SG5 Consumer")
    (repo / "specs" / "contract.xml").write_text(CORPUS, encoding="utf-8")
    (repo / "tests" / "test_marker.py").write_text(MARKER_TEST, encoding="utf-8")
    (repo / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
    (repo / "src" / "lonely.py").write_text("LONELY = 1\n", encoding="utf-8")
    (repo / "docs" / "note.md").write_text(
        "# Evidence\n\nan artifact\n", encoding="utf-8"
    )
    staged = run_git(repo, "add", "--", ".")
    assert staged.returncode == 0, staged.stderr
    committed = run_git(repo, "commit", "-qm", "initial")
    assert committed.returncode == 0, committed.stderr
    return repo


def stage_append(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    added = run_git(repo, "add", "--", relative)
    assert added.returncode == 0, added.stderr


def staged_diff(repo: Path) -> str:
    """Exactly what GitReins feeds the checker (engine/spec_alignment.py staged_diff)."""
    done = run_git(
        repo,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--no-renames",
        "--",
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def marker(**overrides: str | None) -> str:
    """One ch:trace marker line, with the six at-minimum doctrine fields."""
    fields: dict[str, str | None] = {
        "row": "ALT-135",
        "spec": "ALT-TEST-1",
        "test": "tests/test_marker.py::test_marker",
        "evidence": "docs/note.md",
        "verdict": "none:pending-judge",
        "commit": "none:pre-commit",
    }
    fields.update(overrides)
    rendered = " ".join(
        f"{key}={value}" for key, value in fields.items() if value is not None
    )
    return f"# ch:trace {rendered}\n"


def checker_command() -> str:
    """The command string GitReins' `run_checker` executes FROM the scratch repo cwd.

    The interpreter token must be ABSOLUTE: a relative PATH entry makes `sys.executable`
    relative (`.venv/bin/python3`), and a command string executed from a scratch consumer
    repo cannot resolve it (FileNotFoundError) before the checker runs.
    """
    return f"{os.path.abspath(sys.executable)} {AUGER} spec align"


def test_the_checker_command_resolves_the_interpreter_absolutely() -> None:
    """Pin for the command-string executor shape (AUG-161)."""
    interpreter, _, rest = checker_command().partition(" ")
    assert os.path.isabs(interpreter), checker_command()
    assert rest == f"{AUGER} spec align"


def run_cli(
    repo: Path,
    diff: str,
    *extra: str,
    protocol: str = "1",
    spec_root: Path | None = None,
) -> tuple[int, str, str]:
    """The exact argv GitReins builds, with the real auger binary and the diff on stdin."""
    # Absolute interpreter: a RELATIVE PATH entry makes sys.executable relative too, and this
    # call runs with cwd set to a scratch consumer repo, so a relative argv[0] resolves
    # against the wrong directory (FileNotFoundError) before any assertion executes.
    interpreter = os.path.abspath(sys.executable)
    argv = [
        interpreter,
        str(AUGER),
        "spec",
        "align",
        "--repo-root",
        str(repo.resolve()),
        "--spec-root",
        str((spec_root or (repo / "specs")).resolve()),
        "--protocol-version",
        protocol,
        "--diff-stdin",
        *extra,
    ]
    done = subprocess.run(
        argv,
        input=diff,
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=SCRATCH_GIT_ENV,
        check=False,
        timeout=120,
    )
    return done.returncode, done.stdout, done.stderr


def payload_of(out: str) -> dict:
    """Parse the ONE JSON line GitReins' `_parse_payload` would read (it takes the last line)."""
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, (
        "the checker must emit exactly one JSON line: engine.spec_alignment._parse_payload "
        f"json.loads each LINE and a pretty-printed object parses as nothing. Got {len(lines)} "
        f"line(s):\n{out}"
    )
    return json.loads(lines[0])


def check(repo: Path, diff: str) -> dict:
    code, out, err = run_cli(repo, diff)
    assert "unrecognized arguments" not in err, (
        "argparse rejected one of --repo-root/--spec-root/--protocol-version/--diff-stdin: "
        f"{err}"
    )
    assert code in (0, 1), f"the checker refused a valid invocation (rc {code}): {err}"
    return payload_of(out)


def codes(payload: dict) -> set[str]:
    return {finding["code"] for finding in payload["findings"]}


# ----------------------------------------------------------------- AUG-135 AC1: the four flags
def test_the_cli_accepts_the_four_checker_flags_and_emits_one_json_line(
    tmp_path: Path,
) -> None:
    """AUG-135 AC1: the four flags argparse must not reject, and a machine-readable payload.

    Before the fix `auger spec align` did not exist and the same argv exited 2 with
    `invalid choice: 'align'`; a `spec check ROOT` shape would refuse the four flags as
    unrecognized arguments. This test fails with that assertion text until the verb exists.
    """
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    code, out, err = run_cli(repo, staged_diff(repo))
    assert "invalid choice" not in err, f"the checker verb is missing: {err}"
    assert "unrecognized arguments" not in err, f"a required flag is missing: {err}"
    assert code == 0, f"an aligned change must exit 0, got {code}: {err}"

    payload = payload_of(out)
    # The handshake fields GitReins validates, plus the findings channel.
    assert payload["protocol"] == "1", payload
    assert payload["version"] and payload["version"] != "0.3", (
        "the payload `version` is the CHECKER PROTOCOL version GitReins compares against "
        f"min_checker_version=1.0.0; AUGER_VERSION (0.3) would be refused: {payload.get('version')!r}"
    )
    assert payload["status"] in {"pass", "warn", "fail"}, payload
    assert isinstance(payload["findings"], list), payload
    assert set(payload) >= {
        "protocol",
        "version",
        "status",
        "scope",
        "referenced",
        "findings",
        "counts",
    }, sorted(payload)


def test_both_documented_checker_commands_agree(tmp_path: Path) -> None:
    """AUG-135 AC4: two command strings are in circulation and they must not diverge.

    The commit/docs name `auger spec align`; the AUG-135 row text simulates the caller as
    `... --diff-stdin check`. Both verbs must reach the SAME implementation and emit byte-identical
    payloads, otherwise the two boards really could disagree about what "complete" means.
    """
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    diff = staged_diff(repo)
    repo_root = str(repo.resolve())
    spec_root = str((repo / "specs").resolve())

    commands = {
        "align": [
            "align",
            "--repo-root",
            repo_root,
            "--spec-root",
            spec_root,
            "--protocol-version",
            "1",
            "--diff-stdin",
        ],
        "check with a positional ROOT": [
            "check",
            str(repo / "specs"),
            "--repo-root",
            repo_root,
            "--protocol-version",
            "1",
            "--diff-stdin",
        ],
        "check with --spec-root only (the shape GitReins appends flags to)": [
            "check",
            "--repo-root",
            repo_root,
            "--spec-root",
            spec_root,
            "--protocol-version",
            "1",
            "--diff-stdin",
        ],
    }
    outputs = {}
    for label, verb_args in commands.items():
        # Absolute interpreter: a RELATIVE PATH entry makes sys.executable relative too, and this
        # call runs with cwd set to a scratch consumer repo, so a relative argv[0] resolves
        # against the wrong directory (FileNotFoundError) before any assertion executes.
        interpreter = os.path.abspath(sys.executable)
        done = subprocess.run(
            [interpreter, str(AUGER), "spec", *verb_args],
            input=diff,
            capture_output=True,
            text=True,
            cwd=str(repo),
            env=SCRATCH_GIT_ENV,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, (label, done.returncode, done.stderr)
        outputs[label] = done.stdout
    first = payload_of(outputs["align"])
    for label, out in outputs.items():
        assert out == outputs["align"], f"{label} disagrees with `spec align`:\n{out}"
    assert first["status"] == "pass", first


# ------------------------------------------------------------------- AUG-120 AC-5: five fixtures
def test_an_aligned_change_passes_with_resolved_spec_ids(tmp_path: Path) -> None:
    """AC-5 aligned change, PRD AC-8: status pass with resolved spec ids and provenance."""
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    payload = check(repo, staged_diff(repo))

    assert payload["status"] == "pass", payload["findings"]
    assert payload["scope"] == "scoped", payload
    assert payload["counts"]["fail"] == 0, payload["findings"]
    ids = {block["id"] for block in payload["referenced"]["blocks"]}
    assert "ALT-TEST-1" in ids, payload["referenced"]
    block = next(b for b in payload["referenced"]["blocks"] if b["id"] == "ALT-TEST-1")
    assert block["source"] == "contract.xml#ALT-TEST-1" or block["source"].endswith(
        "contract.xml#ALT-TEST-1"
    ), block
    assert payload["diff"]["changed_files"] == ["tests/test_marker.py"], payload["diff"]


def test_a_missing_trace_is_a_named_failure(tmp_path: Path) -> None:
    """AC-5 missing trace: a scoped change with no ch:trace marker is a FAIL, not silence.

    The staged line MENTIONS the marker without claiming one, which must not be read as a malformed
    marker: prose about `ch:trace` is prose, and the finding is the missing trace.
    """
    repo = build_repo(tmp_path)
    stage_append(
        repo,
        "tests/test_marker.py",
        "\n# the ch:trace marker must carry row= and spec= before this lands\n",
    )
    payload = check(repo, staged_diff(repo))

    assert payload["status"] == "fail", payload
    finding = next(f for f in payload["findings"] if f["code"] == "trace-missing")
    assert finding["severity"] == "fail", finding
    assert "tests/test_marker.py" in finding["evidence"], finding
    assert finding["why"], finding
    assert "ALT-TEST-1" in finding["why"], finding
    assert "trace-malformed" not in codes(payload), (
        "a comment that describes the marker was graded as a malformed marker"
    )


def test_a_wrong_spec_section_is_reported(tmp_path: Path) -> None:
    """AC-5 wrong spec section: the marker names a block that does not scope this file."""
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker(spec="ALT-VER-2"))
    payload = check(repo, staged_diff(repo))

    assert payload["status"] == "warn", payload
    finding = next(
        f for f in payload["findings"] if f["code"] == "spec-section-mismatch"
    )
    assert finding["severity"] == "warn", finding
    assert "ALT-VER-2" in finding["why"] and "ALT-TEST-1" in finding["why"], finding
    assert "tests/test_marker.py" in finding["evidence"], finding


def test_a_contradictory_comment_citing_a_superseded_section_fails(
    tmp_path: Path,
) -> None:
    """AC-5 contradictory comment: the change cites a section the corpus RETIRED.

    Contradiction is checked STRUCTURALLY — an annotation that contradicts the corpus lifecycle
    or the corpus itself. "This comment says X, the prose says not-X" is not mechanically
    decidable, and the checker does not claim it.
    """
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker(spec="ALT-OLD-1"))
    payload = check(repo, staged_diff(repo))

    assert payload["status"] == "fail", payload
    finding = next(
        f for f in payload["findings"] if f["code"] == "spec-section-superseded"
    )
    assert finding["severity"] == "fail", finding
    assert "superseded" in finding["why"], finding
    assert "ALT-OLD-1" in finding["evidence"], finding


def test_a_change_with_no_related_spec_is_unscoped_not_pass(tmp_path: Path) -> None:
    """AC-5 no related spec: `unscoped` is a NAMED state, never an automatic pass."""
    repo = build_repo(tmp_path)
    stage_append(repo, "src/lonely.py", "\n# nothing in the corpus scopes this file\n")
    payload = check(repo, staged_diff(repo))

    assert payload["scope"] == "unscoped", payload
    assert payload["status"] != "pass", payload
    assert payload["status"] == "warn", payload
    finding = next(f for f in payload["findings"] if f["code"] == "unscoped-change")
    assert finding["severity"] == "warn", finding
    assert payload["referenced"]["blocks"] == [], payload["referenced"]


# --------------------------------------------------------- AUG-120 AC-3 / PRD AC-9: the contract
@pytest.mark.parametrize(
    ("case", "marker_kwargs", "expected"),
    (
        ("row-missing", {"row": None}, "trace-missing-row"),
        ("spec-unresolved", {"spec": "ALT-NOPE-9"}, "trace-spec-unresolved"),
        ("evidence-is-another-row", {"evidence": "ALT-135"}, "trace-evidence-is-row"),
        ("absent-value-without-reason", {"verdict": "none"}, "trace-absent-value"),
        ("field-absent-entirely", {"test": None}, "trace-missing-field"),
        ("verdict-unbound-from-commit", {"verdict": "pass"}, "trace-verdict-unbound"),
        (
            "verdict-bound-to-a-foreign-commit",
            {"verdict": "pass", "commit": "deadbeefdeadbeef"},
            "trace-commit-unknown",
        ),
        (
            "test-pointer-unresolved",
            {"test": "tests/does-not-exist.py::test_x"},
            "trace-test-unresolved",
        ),
        ("unknown-field", {"tests": "tests/test_marker.py"}, "trace-unknown-field"),
    ),
)
def test_the_ch_trace_contract_violations_are_distinct_findings(
    tmp_path: Path, case: str, marker_kwargs: dict, expected: str
) -> None:
    """AC-3 + PRD AC-9: each violation is its OWN finding with rule, location and reason."""
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker(**marker_kwargs))
    payload = check(repo, staged_diff(repo))

    finding = next(
        (f for f in payload["findings"] if f["code"] == expected),
        None,
    )
    assert finding is not None, (
        f"{case}: expected {expected}, got {sorted(codes(payload))}"
    )
    assert finding["why"], finding
    assert finding["evidence"], finding
    assert finding["severity"] in {"fail", "warn"}, finding
    if expected != "trace-unknown-field":
        assert payload["status"] == "fail", (case, payload)
    assert payload["counts"]["fail"] == sum(
        1 for f in payload["findings"] if f["severity"] == "fail"
    ), payload["counts"]


# ------------------------------------------------------------- AUG-120 AC-4: no writes, stable
def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_the_checker_writes_nothing_and_is_deterministic(tmp_path: Path) -> None:
    """AC-4: no writes to source/specs/boards, and identical input gives identical JSON."""
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    diff = staged_diff(repo)

    before_fingerprint = _tree_fingerprint(repo)
    before_status = run_git(repo, "status", "--porcelain").stdout
    first = run_cli(repo, diff)
    second = run_cli(repo, diff)

    assert first == second, "the checker is not deterministic for identical input"
    assert first[0] == 0, first[2]
    assert _tree_fingerprint(repo) == before_fingerprint, (
        "the checker wrote into the repository"
    )
    assert run_git(repo, "status", "--porcelain").stdout == before_status
    payload = payload_of(first[1])
    # Ordering is part of determinism: sorted findings and blocks, not hash order.
    assert payload["findings"] == sorted(
        payload["findings"],
        key=lambda f: (f["code"], f["evidence"], f["why"]),
    ), payload["findings"]
    block_ids = [block["id"] for block in payload["referenced"]["blocks"]]
    assert block_ids == sorted(block_ids), block_ids


# ------------------------------------------------------- AUG-135 AC3: fail closed, no orphans
def test_a_protocol_version_mismatch_fails_closed_without_orphans(
    tmp_path: Path,
) -> None:
    """AUG-135 AC3: a version this checker does not speak is a TYPED refusal, not a pass.

    The refusal must be loud (exit 2, a `spec error (<code>)` line) and must not emit a payload a
    caller could mistake for a verdict — GitReins maps "no parseable payload" to its
    missing/invalid-checker policy instead of reading a status out of a contract we did not speak.
    """
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    started = time.monotonic()
    code, out, err = run_cli(repo, staged_diff(repo), protocol="2")
    elapsed = time.monotonic() - started

    assert code == 2, f"a protocol mismatch must exit 2, got {code}: {out!r} {err!r}"
    assert "unsupported-protocol-version" in err, err
    assert out.strip() == "", "a refused handshake must not print a payload"
    assert elapsed < 20, f"the refusal took {elapsed:.1f}s — it is not bounded"

    # No orphan: nothing left behind carrying this run's unique repository path.
    listing = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False
    ).stdout
    leftovers = [
        line
        for line in listing.splitlines()
        if str(repo) in line and "spec align" in line and "ps -eo" not in line
    ]
    assert leftovers == [], f"checker processes survived the refusal: {leftovers}"


def test_an_oversized_diff_is_refused_with_a_typed_error(tmp_path: Path) -> None:
    """Bounded run: a diff larger than the cap is refused instead of buffered without limit."""
    repo = build_repo(tmp_path)
    stage_append(repo, "tests/test_marker.py", "\n" + "# pad\n" * 200 + marker())
    code, out, err = run_cli(repo, staged_diff(repo), "--max-diff-bytes", "64")
    assert code == 2, (code, out, err)
    assert "diff-too-large" in err, err
    assert out.strip() == "", out


# -------------------------------------------------------------- AUG-135 AC2: the REAL invocation
def test_gitreins_real_invocation_against_the_real_auger_binary(tmp_path: Path) -> None:
    """AC2: GitReins' own argv builder + stdio protocol, against the real auger binary.

    Not a stub: `engine.spec_alignment.run_checker` reads the staged diff itself, appends the four
    flags itself, runs the checker in its own session with a bounded budget, and validates the
    handshake and status. This test imports that module from the GitReins checkout and asserts
    both directions — a valid marker passes, a markerless scoped change FAILS the guard.
    """
    skip_reason = gitreins_cross_repo_skip_reason(GITREINS)
    if skip_reason:
        pytest.skip(skip_reason)
    if str(GITREINS) not in sys.path:
        sys.path.insert(0, str(GITREINS))
    from engine import spec_alignment

    repo = build_repo(tmp_path)
    command = checker_command()

    stage_append(repo, "tests/test_marker.py", "\n" + marker())
    aligned = spec_alignment.run_checker(
        str(repo),
        command=command,
        spec_root="specs",
        mode="block",
        missing_checker="block",
        protocol_version="1",
        min_checker_version="1.0.0",
        timeout=60.0,
    )
    assert aligned.missing_checker is False, aligned.output
    assert "incompatible checker protocol" not in aligned.output, aligned.output
    assert aligned.passed is True, aligned.output
    assert aligned.status == "pass", aligned.output
    payload = payload_of(aligned.output)
    assert payload["protocol"] == "1", payload
    assert {block["id"] for block in payload["referenced"]["blocks"]} >= {
        "ALT-TEST-1"
    }, payload

    # The other direction: the guard must BLOCK on our `fail`, not on an unavailable checker. The
    # index is reset first so this diff genuinely carries NO marker — one marker in a staged diff is
    # a commit-level claim, so leaving the previous one staged would (correctly) still pass.
    assert run_git(repo, "reset", "--hard", "-q", "HEAD").returncode == 0
    stage_append(repo, "src/alpha.py", "\n# scoped by ALT-VER-2, no marker\n")
    blocked = spec_alignment.run_checker(
        str(repo),
        command=command,
        spec_root="specs",
        mode="block",
        missing_checker="block",
        protocol_version="1",
        min_checker_version="1.0.0",
        timeout=60.0,
    )
    assert blocked.missing_checker is False, blocked.output
    assert blocked.passed is False, blocked.output
    assert blocked.status == "fail", blocked.output
    assert blocked.exit_code == 1, blocked.output
    # The finding, not an "unavailable checker" line, is what the guard reports.
    assert "trace-missing" in blocked.error, blocked.error


# ------------------------------------------------- QA-AUGER-17: an unreadable checkout is ABSENT
def fake_checkout(base: Path) -> Path:
    """A directory that LOOKS like a GitReins checkout: the guarded module exists."""
    checkout = base / "gitreins"
    (checkout / "engine").mkdir(parents=True)
    (checkout / GITREINS_MODULE).write_text("", encoding="utf-8")
    return checkout


def test_the_cross_repo_guard_treats_an_unreadable_checkout_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-AUGER-17: a PRESENT but UNREADABLE checkout must be a SKIP, never a PermissionError.

    uid-independent by construction: the readability probe is forced to False instead of relying
    on a chmod, which root ignores (root passes every mode bit, so a chmod-only arm silently
    proves nothing on a root-run battery).
    """
    checkout = fake_checkout(tmp_path)
    assert (checkout / GITREINS_MODULE).is_file(), (
        "premise: the module EXISTS — this arm is about readability, not absence"
    )
    assert os.access(checkout / GITREINS_MODULE, os.R_OK) is True, (
        "premise: unpatched, this checkout IS readable — the skip below comes from the injection"
    )

    monkeypatch.setattr(sys.modules[__name__], "_readable", lambda path: False)

    reason = gitreins_cross_repo_skip_reason(checkout)
    assert reason, (
        "an unreadable checkout must produce a skip reason, not an empty string"
    )
    assert str(checkout) in reason, f"the skip reason must name the path: {reason}"
    assert "UNREADABLE" in reason, f"the skip reason must say unreadable: {reason}"
    assert "PermissionError" in reason, (
        f"the reason must name what it prevents: {reason}"
    )


def test_the_cross_repo_guard_reports_an_absent_checkout_as_absent(
    tmp_path: Path,
) -> None:
    """The absent arm keeps its own distinct reason — the two causes must not collapse."""
    missing = tmp_path / "nonexistent-checkout"
    reason = gitreins_cross_repo_skip_reason(missing)
    assert str(missing) in reason, f"the skip reason must name the path: {reason}"
    assert "ABSENT" in reason, f"the skip reason must say absent: {reason}"
    assert "UNREADABLE" not in reason, reason


def test_the_cross_repo_guard_lets_a_readable_checkout_run(tmp_path: Path) -> None:
    """The other direction: a READABLE checkout must NOT be reported as unavailable.

    Without this arm a guard that always skips would satisfy every unreadable-checkout assertion.
    """
    checkout = fake_checkout(tmp_path)
    assert gitreins_cross_repo_skip_reason(checkout) == "", (
        "a readable checkout must run — a guard that skips here silently disables the proof"
    )


def test_the_cross_repo_leg_skips_on_an_unreadable_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real-argv leg ITSELF skips (with the reason) instead of raising PermissionError."""
    checkout = fake_checkout(tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "GITREINS", checkout)
    monkeypatch.setattr(sys.modules[__name__], "_readable", lambda path: False)

    with pytest.raises(pytest.skip.Exception) as raised:
        test_gitreins_real_invocation_against_the_real_auger_binary(tmp_path)
    reason = str(raised.value)
    assert str(checkout) in reason, f"the leg's skip must name the path: {reason}"
    assert "UNREADABLE" in reason, f"the leg's skip must say unreadable: {reason}"


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root passes every mode bit, so a chmod arm can prove nothing on uid 0",
)
def test_a_real_chmod_to_zero_is_reported_as_unreadable(tmp_path: Path) -> None:
    """A REAL unreadable file (not an injection) also skips — never errors. Guarded off uid 0."""
    checkout = fake_checkout(tmp_path)
    module = checkout / GITREINS_MODULE
    module.chmod(0o000)
    try:
        reason = gitreins_cross_repo_skip_reason(checkout)
    finally:
        module.chmod(0o600)
    assert "UNREADABLE" in reason, (
        f"a mode-000 module must skip as unreadable: {reason!r}"
    )


def test_the_readability_probe_asks_os_access_for_r_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the probe's SHAPE: it is a READ check (os.R_OK), not a bare existence test."""
    seen: list[tuple[Path, int]] = []

    def fake_access(path, mode):
        seen.append((Path(path), mode))
        return False

    monkeypatch.setattr(os, "access", fake_access)
    target = fake_checkout(tmp_path) / GITREINS_MODULE
    assert _readable(target) is False
    assert seen == [(target, os.R_OK)], (
        f"the probe must ask os.access(path, os.R_OK) — got {seen}"
    )


# ---------------------------------------------------------------- AUG-120 AC-1: documented
def test_the_checker_contract_is_documented() -> None:
    """AC-1: the machine-readable surface is DOCUMENTED where a consumer reads it."""
    schema = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "staged-diff checker" in schema.lower(), (
        "docs/XML-SPEC-SCHEMA.md must document the staged-diff checker contract"
    )
    for token in (
        "--diff-stdin",
        "--repo-root",
        "--spec-root",
        "--protocol-version",
        '"protocol"',
        '"findings"',
        "unscoped",
        "auger spec align",
        "auger spec check",
    ):
        assert token in schema, f"the checker contract does not name {token}"
    verbs = VERBS_DOC.read_text(encoding="utf-8")
    spec_section = verbs.split("\n## spec\n", 1)[1]
    assert "auger spec align" in spec_section, (
        "docs/VERBS.md must list the checker verb"
    )

    helped = subprocess.run(
        [sys.executable, str(AUGER), "spec", "align", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert helped.returncode == 0, helped.stderr
    for flag in ("--repo-root", "--spec-root", "--protocol-version", "--diff-stdin"):
        assert flag in helped.stdout, f"`auger spec align --help` does not name {flag}"


# ------------------------------------------------------------------------ the gate arm
def test_gate_arm_runs_this_file() -> None:
    """A proof outside tests/gate.sh's pytest arm is never executed by CI or the guard."""
    arm = [
        line
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if "python3 -m pytest" in line
    ]
    assert arm, "tests/gate.sh must have a pytest arm"
    assert "tests/test_spec_alignment_check.py" in arm[0], (
        "tests/test_spec_alignment_check.py must be in tests/gate.sh's pytest arm — the guard's "
        "test command IS the gate, so a proof file outside it is never run"
    )
