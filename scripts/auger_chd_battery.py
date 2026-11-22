#!/usr/bin/env python3
"""AUG-204 AC3 preflight + verdict helper for scripts/auger-chd-battery.sh.

Two modes, both fail closed with a named reason and a non-zero exit:

--check CORPUS      hermetic battery-readiness pass over the committed full corpus:
                    manifests parse and reconcile, the corpus loads through auger's own
                    loader (the same read the battery's register/run use), and every
                    AUG-176 battery target resolves to a block. NO substrate contact.

--verdict REPORT    judge a battery run report (scripts/recall_battery.py run --out):
                    PASS requires exactly BASELINE_QUERIES queries measured, zero
                    register failures, zero transport-failed (unmeasured) queries in the
                    run log, and a hit rate not below the AUG-176 distilled baseline
                    (BASELINE_HITS/BASELINE_QUERIES = 7/12,
                    evidence/AUG-176/06-recall-battery.md). Anything else prints the
                    failing clauses and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import auger  # noqa: E402  (repo-root import, after the sys.path seam)
import recall_battery  # noqa: E402

PROJECT = "coding-hermes-director"
#: The AUG-176 distilled battery measured 7/12 for this project; the full corpus must
#: not regress below that with every query measured.
BASELINE_HITS = 7
BASELINE_QUERIES = 12
#: stderr marker scripts/recall_battery.py prints when a query died after retries —
#: such a query is UNMEASURED, never a miss, and must fail the verdict.
TRANSPORT_MARKER = "query FAILED after retries"


def check(corpus: Path) -> int:
    manifest_path = corpus / "manifest.json"
    coverage_path = corpus / "coverage-manifest.json"
    for path in (manifest_path, coverage_path):
        if not path.exists():
            print(
                f"auger-chd-battery: corpus manifest missing: {path} — generate the "
                "full corpus first (scripts/ingest_markdown_specs.py "
                "coding-hermes-director)",
                file=sys.stderr,
            )
            return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if coverage.get("canonical_md_total") != len(coverage.get("entries", [])):
        print(
            "auger-chd-battery: coverage manifest does not reconcile "
            f"(canonical_md_total={coverage.get('canonical_md_total')} vs "
            f"{len(coverage.get('entries', []))} entries)",
            file=sys.stderr,
        )
        return 1
    _root, documents, blocks = auger.load_spec_corpus(corpus)
    targets = recall_battery.resolve_targets(PROJECT, recall_battery.QUERIES[PROJECT])
    print(
        f"{PROJECT} corpus: {len(documents)} document(s), {len(blocks)} block(s) "
        f"(coverage manifest: {coverage['canonical_md_total']} canonical md paths, "
        f"renders {manifest['totals']['files']})"
    )
    print(f"battery targets: {len(targets)}/{BASELINE_QUERIES} resolved")
    return 0


def verdict(
    report_path: Path, register_report: Path | None, run_log: Path | None
) -> int:
    reports = json.loads(report_path.read_text(encoding="utf-8"))
    mine = [r for r in reports if r.get("project") == PROJECT]
    if len(mine) != 1:
        print(
            f"auger-chd-battery: run report holds {len(mine)} {PROJECT} entries, "
            f"expected 1: {report_path}",
            file=sys.stderr,
        )
        return 1
    report = mine[0]
    clauses: list[str] = []
    ok = True
    if report["queries"] != BASELINE_QUERIES:
        ok = False
        clauses.append(
            f"measured {report['queries']} queries, expected {BASELINE_QUERIES}"
        )
    if report["hits"] < BASELINE_HITS:
        ok = False
        clauses.append(
            f"hit rate {report['hits']}/{report['queries']} below the AUG-176 "
            f"distilled baseline {BASELINE_HITS}/{BASELINE_QUERIES}"
        )
    unmeasured = [
        r["query"]
        for r in report["rows"]
        if r.get("rank") is None and r.get("distinct_in_window", 0) == 0
    ]
    if run_log is not None and TRANSPORT_MARKER in run_log.read_text(
        encoding="utf-8", errors="replace"
    ):
        ok = False
        clauses.append(
            f"unmeasured queries present: the run log carries '{TRANSPORT_MARKER}'"
        )
    if unmeasured and run_log is None:
        ok = False
        clauses.append(
            f"{len(unmeasured)} row(s) with no rank and no hit (target absent and "
            "window empty); pass --run-log to distinguish a real miss from a "
            "transport failure"
        )
    failed_rows: list = []
    if register_report is not None:
        reg = json.loads(register_report.read_text(encoding="utf-8"))
        entries = reg if isinstance(reg, list) else [reg]
        for entry in entries:
            if entry.get("project") == PROJECT:
                failed_rows = entry.get("failed_rows", [])
        if failed_rows:
            ok = False
            clauses.append(
                f"{len(failed_rows)} register row(s) failed after retries (unregistered "
                f"content, first: {failed_rows[0]})"
            )
    rate = f"{report['hits']}/{report['queries']}"
    if ok:
        print(
            f"verdict: PASS  hits {rate} >= baseline {BASELINE_HITS}/"
            f"{BASELINE_QUERIES}, every query measured, register failed_rows 0"
        )
        return 0
    print(f"verdict: FAIL  hits {rate} — " + "; ".join(clauses), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="hermetic readiness pass")
    parser.add_argument("--corpus", default=None, help="corpus root for --check")
    parser.add_argument("--verdict", default=None, help="run report JSON to judge")
    parser.add_argument(
        "--register-report", default=None, help="register stats JSON (failed_rows)"
    )
    parser.add_argument(
        "--run-log", default=None, help="run stderr log (transport-failure marker)"
    )
    args = parser.parse_args(argv)
    if args.check:
        if not args.corpus:
            parser.error("--check requires --corpus")
        return check(Path(args.corpus))
    if args.verdict:
        register = Path(args.register_report) if args.register_report else None
        run_log = Path(args.run_log) if args.run_log else None
        return verdict(Path(args.verdict), register, run_log)
    parser.error("one of --check or --verdict is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
