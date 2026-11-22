"""AUG-204: full coding-hermes-director corpus coverage, dedupe and battery.

The row upgrades the AUG-176 distilled subset (22 documents) to the FULL canonical
tree: every one of the 648 canonical .md paths is accounted by
corpora/coding-hermes-director/coverage-manifest.json in exactly one class:

  first-class            spec-bearing source (build-spec, contracts, review, sections,
                         DESIGN.md) — rendered as its own XML document
  handbook-unique        handbook file whose normalized content matches no canonical
                         render — unique normative content, rendered ONCE
  duplicate-of-canonical handbook file whose normalized content equals a canonical
                         first-class source — provenance pointers, NO render of its own
  duplicate-of-handbook  handbook file whose normalized content equals ANOTHER
                         handbook file's render — pointer to that render, NO own render
  audit-provenance       audit transcript — provenance/attachment record, no render

Test legs (mirroring tests/test_spec_ingest.py's convention):
  * PORTABLE — reads only committed artifacts; runs on any host, CI included.
  * HOST — enumerates the canonical source tree (AUGER_CHD_SOURCE_ROOT override, or the
    row's /home/kara/specs/coding-hermes-director); a missing source root is a NAMED
    skip, never a vacuous pass.

AC3's battery wrapper (scripts/auger-chd-battery.sh) is tested as a CONTRACT:
exists/executable, fails closed on a missing/empty corpus fixture and without the
live substrate, passes --check (hermetic battery-readiness) on the real corpus, and
its PASS verdict boundary is unit-pinned — the full-corpus hit rate must not regress
below the AUG-176 distilled baseline (7/12, evidence/AUG-176/06-recall-battery.md)
with every query measured and zero register failures. The live register+run itself
stays the manual verification (evidence/AUG-204/), because a full-corpus register is
a ~30-minute live-substrate operation that must not run inside the gate.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

PROJECT = "coding-hermes-director"
EXPECTED_TOTAL = 648
EXPECTED_AUDIT = 47
#: AC2's ceiling: fewer than one render per handbook file must exist.
HANDBOOK_DOC_LIMIT = 490
#: The committed AUG-176 distilled battery measured 7/12 for this project.
BASELINE_HITS = 7
BASELINE_QUERIES = 12
CLASSES = {
    "first-class",
    "handbook-unique",
    "duplicate-of-canonical",
    "duplicate-of-handbook",
    "audit-provenance",
}
#: Entries of these classes carry an XML render pointer of their own.
RENDER_CLASSES = {"first-class", "handbook-unique"}
#: Entries of these classes are duplicates: pointer, never their own render.
DUPLICATE_CLASSES = {"duplicate-of-canonical", "duplicate-of-handbook"}

ingest = importlib.import_module("ingest_markdown_specs")

BATTERY_SCRIPT = REPO_ROOT / "scripts" / "auger-chd-battery.sh"


def _preflight():
    return importlib.import_module("auger_chd_battery")


def _corpora_root() -> Path:
    return Path(os.environ.get("AUGER_CORPORA_ROOT", str(REPO_ROOT / "corpora")))


def _chd_corpus() -> Path:
    return _corpora_root() / PROJECT


def _coverage_manifest() -> dict:
    path = _chd_corpus() / "coverage-manifest.json"
    if not path.exists():
        pytest.fail(
            f"{path} missing — the AUG-204 full-tree conversion has not been "
            "generated (scripts/ingest_markdown_specs.py coding-hermes-director)"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _source_root() -> Path:
    return Path(
        os.environ.get(
            "AUGER_CHD_SOURCE_ROOT", "/home/kara/specs/coding-hermes-director"
        )
    )


def _script_env(extra: dict[str, str] | None = None, strip_substrate=False) -> dict:
    """Env for driving the battery script: pin python3 to THIS suite interpreter."""
    env = dict(os.environ)
    if strip_substrate:
        env.pop("DUCKBRAIN_URL", None)
        env.pop("DUCKBRAIN_API_KEY", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.pop("AUGER_CORPORA_ROOT", None)  # default: this repo's corpora
    if extra:
        env.update(extra)
    return env


def _run_battery(args: list[str], env: dict, timeout: int = 120):
    return subprocess.run(
        ["bash", str(BATTERY_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------- coverage (AC1)


def test_gate_pytest_arm_names_this_file():
    gate = (REPO_ROOT / "tests" / "gate.sh").read_text(encoding="utf-8")
    assert "tests/test_chd_full_corpus.py" in gate, (
        "the gate's pytest arm does not name tests/test_chd_full_corpus.py — "
        "an unnamed file is unenforced (AUG-204 AC4)"
    )


def test_coverage_manifest_accounts_for_every_canonical_path():
    """PORTABLE: entries == 648, totals reconcile, class vocabulary closed."""
    manifest = _coverage_manifest()
    entries = manifest["entries"]
    assert len(entries) == EXPECTED_TOTAL, (
        f"coverage manifest holds {len(entries)} entries, expected {EXPECTED_TOTAL}"
    )
    paths = [e["path"] for e in entries]
    assert len(set(paths)) == len(paths), "duplicate paths in the coverage manifest"
    for entry in entries:
        assert entry["class"] in CLASSES, entry
    totals = manifest["totals"]
    assert totals["total"] == EXPECTED_TOTAL
    per_class = {c: 0 for c in CLASSES}
    for entry in entries:
        per_class[entry["class"]] += 1
    for name, count in per_class.items():
        assert totals[name] == count, f"totals[{name}]={totals[name]} counted {count}"
    assert sum(per_class.values()) == EXPECTED_TOTAL


def test_every_render_pointer_resolves_and_duplicates_point_only():
    """PORTABLE (AC1): zero dangling pointers; non-duplicates render, duplicates point."""
    manifest = _coverage_manifest()
    corpus = _chd_corpus()
    by_path = {e["path"]: e for e in manifest["entries"]}
    for entry in manifest["entries"]:
        cls = entry["class"]
        if cls in RENDER_CLASSES:
            render = entry["render"]
            assert (corpus / render).is_file(), (
                f"{entry['path']}: dangling render {render}"
            )
        elif cls in DUPLICATE_CLASSES:
            assert "render" not in entry, (
                f"{entry['path']}: a duplicate must never carry its own render"
            )
            canonical_render = entry["canonical_render"]
            assert (corpus / canonical_render).is_file(), (
                f"{entry['path']}: dangling canonical_render {canonical_render}"
            )
            canonical = by_path.get(entry["canonical_source"])
            assert canonical is not None, (
                f"{entry['path']}: canonical_source {entry['canonical_source']} is not "
                "accounted by the manifest itself"
            )
            assert canonical.get("render") == canonical_render, (
                f"{entry['path']}: canonical_render does not match the canonical entry"
            )
            assert entry["sha256"] == canonical["sha256"], (
                f"{entry['path']}: duplicate content hash differs from its canonical "
                "source — the dedupe pointer is not content-true"
            )
        else:  # audit-provenance
            assert "render" not in entry, entry["path"]
            record = entry["record"]
            assert record.get("sha256") and record.get("bytes"), entry["path"]
    audit = [e for e in manifest["entries"] if e["class"] == "audit-provenance"]
    assert len(audit) == EXPECTED_AUDIT, (
        f"{len(audit)} audit-provenance records, expected {EXPECTED_AUDIT}"
    )


def test_non_audit_non_duplicate_paths_all_render():
    """PORTABLE (AC1): every non-duplicate canonical md carries an XML render pointer."""
    manifest = _coverage_manifest()
    for entry in manifest["entries"]:
        if entry["class"] in {"first-class", "handbook-unique"}:
            assert entry.get("render"), f"{entry['path']} has no render pointer"


# ---------------------------------------------------------------- dedupe (AC2)


def test_handbook_renders_held_below_490():
    """PORTABLE (AC2): handbook-derived XML documents < 490, counted two ways."""
    manifest = _coverage_manifest()
    totals = manifest["totals"]
    assert totals["handbook-unique"] < HANDBOOK_DOC_LIMIT, (
        f"{totals['handbook-unique']} handbook-derived renders violates AC2's "
        f"< {HANDBOOK_DOC_LIMIT} ceiling — the dedupe regressed"
    )
    handbook_dir = _chd_corpus() / "handbook"
    on_disk = len(list(handbook_dir.rglob("*.xml"))) if handbook_dir.is_dir() else 0
    assert on_disk == totals["handbook-unique"], (
        f"{on_disk} handbook renders on disk vs {totals['handbook-unique']} in the "
        "coverage manifest"
    )
    from_manifest = sum(
        1
        for e in manifest["entries"]
        if e["class"] == "handbook-unique" and e["path"].startswith("handbook/")
    )
    assert from_manifest == totals["handbook-unique"]


def test_dedupe_counts_reconcile_per_handbook_path():
    """PORTABLE (AC2): every handbook path is unique-render, canonical-dupe or sibling-dupe."""
    manifest = _coverage_manifest()
    handbook = [e for e in manifest["entries"] if e["path"].startswith("handbook/")]
    assert len(handbook) == 490, (
        f"{len(handbook)} handbook paths accounted, expected 490"
    )
    for entry in handbook:
        if entry["class"] == "duplicate-of-canonical":
            assert entry["canonical_source"].startswith("handbook/") is False
        if entry["class"] == "duplicate-of-handbook":
            assert entry["canonical_source"].startswith("handbook/"), entry["path"]


# ---------------------------------------------------------------- corpus (AC5)


def test_full_corpus_validates_and_checks():
    """PORTABLE (AC5): the whole committed corpus passes both verbs."""
    from test_spec_ingest import _run_auger

    corpus = _chd_corpus()
    if not (corpus / "manifest.json").exists():
        pytest.fail(
            f"{corpus}/manifest.json missing — full corpus not generated (AUG-204)"
        )
    for verb in ("validate", "check"):
        run = _run_auger("spec", verb, str(corpus))
        assert run.returncode == 0, (
            f"spec {verb} corpora/{PROJECT} rc={run.returncode}:\n{run.stderr}"
        )


# ---------------------------------------------------------------- determinism


def test_reingest_is_byte_identical():
    """HOST: re-running the converter over the same sources reproduces the corpus."""
    source_root = _source_root()
    if not source_root.is_dir():
        pytest.skip(
            f"canonical source tree not available on this host ({source_root}); "
            "the committed corpus is still verified by its own digests"
        )
    config = ingest.PROJECTS[PROJECT].get("full_tree")
    if config is None:
        pytest.fail(
            "ingest_markdown_specs.PROJECTS['coding-hermes-director'] has no "
            "'full_tree' configuration — AUG-204's converter extension is missing"
        )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "corpus"
        ingest.convert_project(PROJECT, out)
        committed = _chd_corpus()

        # Compare the converter's OUTPUTS only: rendered documents plus the two
        # manifests. Companion documentation (COVERAGE.md) is authored, not
        # generated, so it must not enter the byte-identity contract.
        def corpus_outputs(root: Path) -> list[str]:
            return sorted(
                p.relative_to(root).as_posix()
                for p in root.rglob("*")
                if p.is_file()
                and (
                    p.suffix == ".xml"
                    or p.name in ("manifest.json", "coverage-manifest.json")
                )
            )

        committed_files = corpus_outputs(committed)
        regenerated = corpus_outputs(out)
        assert committed_files == regenerated, (
            "re-ingest produced a different file set than the committed corpus"
        )
        for rel in committed_files:
            assert (committed / rel).read_bytes() == (out / rel).read_bytes(), (
                f"{rel} is not byte-identical across re-ingest"
            )


def test_canonical_md_enumeration_matches_manifest():
    """HOST (AC1): zero unaccounted paths, proven by enumerating the source tree."""
    source_root = _source_root()
    if not source_root.is_dir():
        pytest.skip(f"canonical source tree not available on this host ({source_root})")
    manifest = _coverage_manifest()
    accounted = {e["path"] for e in manifest["entries"]}
    canonical = {
        p.relative_to(source_root).as_posix()
        for p in source_root.rglob("*.md")
        if p.is_file()
    }
    assert len(canonical) == EXPECTED_TOTAL, (
        f"source tree holds {len(canonical)} canonical .md paths, expected "
        f"{EXPECTED_TOTAL} — the row's measured set changed"
    )
    assert canonical == accounted, (
        f"unaccounted paths: {sorted(canonical - accounted)[:5]}...; "
        f"phantom paths: {sorted(accounted - canonical)[:5]}..."
    )


# ---------------------------------------------------------------- battery (AC3)


def test_battery_script_exists_and_parses():
    assert BATTERY_SCRIPT.is_file(), f"{BATTERY_SCRIPT} missing (AUG-204 AC3)"
    assert os.access(BATTERY_SCRIPT, os.X_OK), f"{BATTERY_SCRIPT} is not executable"
    parse = subprocess.run(
        ["bash", "-n", str(BATTERY_SCRIPT)], capture_output=True, text=True
    )
    assert parse.returncode == 0, parse.stderr


def test_battery_wraps_the_aug176_battery():
    text = BATTERY_SCRIPT.read_text(encoding="utf-8")
    assert "recall_battery.py" in text, (
        "wrapper does not drive scripts/recall_battery.py"
    )
    assert PROJECT in text, "wrapper does not name the coding-hermes-director project"


def test_battery_refuses_missing_corpus_fixture(tmp_path):
    """Missing/empty corpus fixture: both modes fail closed with a named reason."""
    empty = tmp_path / "corpora"
    empty.mkdir()
    env = _script_env({"AUGER_CORPORA_ROOT": str(empty)}, strip_substrate=True)
    for args in (["--check"], []):
        run = _run_battery(args, env)
        assert run.returncode != 0, f"{args or ['run']}: empty corpus must fail"
        assert "manifest" in (run.stdout + run.stderr).lower(), (
            f"{args or ['run']}: refusal must name the missing corpus manifest"
        )


def test_battery_check_passes_on_real_corpus():
    env = _script_env(strip_substrate=True)
    run = _run_battery(["--check"], env, timeout=300)
    assert run.returncode == 0, (
        f"--check refused the real corpus:\n{run.stdout}\n{run.stderr}"
    )
    assert "block" in run.stdout, "--check must print the corpus document/block counts"


def test_battery_run_fails_closed_without_substrate():
    env = _script_env(strip_substrate=True)
    run = _run_battery([], env, timeout=120)
    assert run.returncode != 0, "run mode without DUCKBRAIN_URL must fail closed"
    combined = run.stdout + run.stderr
    assert "DUCKBRAIN_URL" in combined, combined


def test_battery_verdict_boundary(tmp_path):
    """PASS boundary unit-pinned: 7/12 passes, 6/12 fails; unmeasured queries fail."""

    def report(hits: int, queries: int = BASELINE_QUERIES) -> str:
        rows = [
            {
                "query": f"q{i}",
                "target": f"T{i}",
                "hit": i < hits,
                "rank": 1 if i < hits else None,
                "distinct_in_window": 5,
                "top5": [],
            }
            for i in range(queries)
        ]
        return json.dumps(
            [
                {
                    "project": PROJECT,
                    "queries": queries,
                    "hits": hits,
                    "hit_rate": hits / queries if queries else 0.0,
                    "rows": rows,
                }
            ]
        )

    clean_log = tmp_path / "run.log"
    clean_log.write_text("all good\n", encoding="utf-8")
    reg_ok = tmp_path / "reg.json"
    reg_ok.write_text(
        json.dumps(
            {"project": PROJECT, "blocks": 1, "rows_written": 1, "failed_rows": []}
        ),
        encoding="utf-8",
    )

    def verdict(hits: int, queries: int = BASELINE_QUERIES, log: Path = clean_log):
        rpt = tmp_path / "report.json"
        rpt.write_text(report(hits, queries), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "auger_chd_battery.py"),
                "--verdict",
                str(rpt),
                "--register-report",
                str(reg_ok),
                "--run-log",
                str(log),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert verdict(BASELINE_QUERIES).returncode == 0, "12/12 must PASS"
    assert verdict(BASELINE_HITS).returncode == 0, (
        "7/12 (the AUG-176 baseline) must PASS"
    )
    assert verdict(BASELINE_HITS - 1).returncode != 0, "6/12 must FAIL the baseline"
    assert verdict(BASELINE_HITS, queries=BASELINE_QUERIES - 1).returncode != 0, (
        "a run that measured fewer queries must FAIL"
    )

    flaky_log = tmp_path / "flaky.log"
    flaky_log.write_text(
        "query FAILED after retries: 'x': transport: timed out\n", encoding="utf-8"
    )
    assert verdict(BASELINE_QUERIES, log=flaky_log).returncode != 0, (
        "an unmeasured (transport-failed) query must FAIL even at 12/12 reported"
    )

    reg_bad = tmp_path / "reg-bad.json"
    reg_bad.write_text(
        json.dumps(
            {"project": PROJECT, "blocks": 2, "rows_written": 1, "failed_rows": ["K"]}
        ),
        encoding="utf-8",
    )
    rpt = tmp_path / "report.json"
    rpt.write_text(report(BASELINE_QUERIES), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "auger_chd_battery.py"),
            "--verdict",
            str(rpt),
            "--register-report",
            str(reg_bad),
            "--run-log",
            str(clean_log),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode != 0, "register failed_rows must FAIL the verdict"
