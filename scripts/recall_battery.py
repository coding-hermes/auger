#!/usr/bin/env python3
"""AUG-176 AC3 recall battery: register converted blocks, query, report the hit rate.

Two halves, both driven against the LIVE DuckBrain substrate (DUCKBRAIN_URL/DUCKBRAIN_API_KEY):

register  every active block of each converted corpus becomes one memory row keyed
          `/auger/AUG176-<project>/<BLOCK-ID>` whose content is the block's verbatim text —
          so a hit's key round-trips to the block id and (via the corpus manifest) to the
          source file + section anchor.

run       >=10 realistic queries per project, each with a KNOWN target block; the search is
          auger's own recall surface (`recall()` -> the substrate's semantic search, scoped to
          the project's key prefix); a query HITS when the target block is in the top-5. The
          report is the AC3 table; a miss names the query so it can be re-chunked and re-run.

The evaluator (`_hit`) is a pure function so tests can pin its contract without a substrate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (repo-root import, after the sys.path seam)

NAMESPACE = "auger"
PROJECT_KEY_PREFIX = "AUG176"
CORPORA_ROOT = REPO_ROOT / "corpora"
TOP_K = 5

# 12 realistic queries per project, each naming the block its answer lives in. Targets are
# block ids from corpora/<project>/manifest.json — chosen by reading the source headings, so
# every query has exactly one right answer in the corpus.
QUERIES: dict[str, list[tuple[str, str]]] = {
    "code-factory": [
        ("what does the envelope own and what is deliberately outside it", None),
        ("how does fleet memory decide what persists between workers", None),
        ("which harnesses are available and how are they picked", None),
        ("what does a node do when its harness dies mid-task", None),
        ("how is trace state recorded for an audit after the fact", None),
        ("what are the memory tiers for a harness and when is each read", None),
        ("what is the node lifecycle from dispatch to completion", None),
        ("how does the envelope hand work to a node", None),
        ("what happens to in-flight work when the fleet reboots", None),
        ("how are harness capabilities compared before dispatch", None),
        ("who owns writing trace records and when", None),
        ("what must a node report back when it finishes a task", None),
    ],
    "auger-docs": [
        ("how does the decision engine pick which question to ask next", None),
        ("what is a bundle and how do projects join one", None),
        ("why is the XML spec graph authored in a namespace", None),
        ("how does the verifier refuse a document with an undeclared attribute", None),
        ("what does the muster consumer read from a dump", None),
        ("how is the substrate verified before a decision is stored", None),
        ("what happens when the JEV gate cannot reach its key provider", None),
        ("how do releases of auger get cut and verified", None),
        ("which block ids own the verification of the loader", None),
        ("what is the confidence band and when does it trust a decision", None),
        ("how does the engine score evidence before showing it", None),
        ("what must a recall answer carry so a caller never compares raw scores", None),
    ],
    "coding-hermes-director": [
        ("how does a wake request become a running attempt", None),
        ("what states can a work ticket be in and how does it move between them", None),
        ("who may approve an external runtime completion and what is checked", None),
        ("what does the archive namespace protect and how", None),
        ("how does config validation reject a federated template", None),
        ("what is the data model for a request attempt record", None),
        ("which endpoints does the gateway expose for identity", None),
        ("how does waiting close the current attempt without reopening it", None),
        ("what is the story role lifecycle in volume A", None),
        ("how are records recovered after storage loss in volume B", None),
        ("what does the admission authorization check refuse", None),
        ("how does the release inventory record blockers", None),
    ],
}


def project_key(project: str) -> str:
    return f"/auger/{PROJECT_KEY_PREFIX}-{project}/"


def _hit(hits: list, target_block_id: str) -> bool:
    """True when the target block's row is among the returned hits (top-K)."""
    suffix = "/" + target_block_id
    return any(
        isinstance(h, dict) and str(h.get("key", "")).endswith(suffix) for h in hits
    )


def load_blocks(project: str) -> list[dict]:
    root, _documents, blocks = auger.load_spec_corpus(CORPORA_ROOT / project)
    return [
        {"id": b["id"], "text": b["text"], "status": b["status"]}
        for b in blocks
        if b["status"] == "active"
    ]


def register(project: str) -> dict:
    blocks = load_blocks(project)
    written = 0
    for block in blocks:
        content = block["text"].strip()
        if not content:
            continue
        auger.remember(NAMESPACE, project_key(project) + block["id"], content)
        written += 1
        if written % 100 == 0:
            print(f"  {project}: {written} row(s) written", file=sys.stderr, flush=True)
        time.sleep(0.05)  # bounded politeness: the substrate embeds every row on write
    return {"project": project, "blocks": len(blocks), "rows_written": written}


def resolve_targets(project: str, queries: list[tuple[str, str | None]]) -> list[dict]:
    """Fill placeholder targets from the corpus: pick, per query, the block whose text the
    query was authored against (documented in evidence/AUG-176/06-recall-battery.md)."""
    blocks = load_blocks(project)
    by_id = {b["id"]: b for b in blocks}
    resolved = []
    for query, target in queries:
        if target is None:
            raise SystemExit(
                f"{project}: unresolved target for query {query!r} — name the target block id"
            )
        if target not in by_id:
            raise SystemExit(f"{project}: target {target} is not a block of the corpus")
        resolved.append({"query": query, "target": target})
    return resolved


def run_battery(project: str, queries: list[dict]) -> dict:
    hits = 0
    rows = []
    for entry in queries:
        found = auger.recall(
            NAMESPACE,
            entry["query"],
            TOP_K,
            project_id=f"{PROJECT_KEY_PREFIX}-{project}",
        )
        hit = _hit(found, entry["target"])
        hits += hit
        rows.append(
            {
                "query": entry["query"],
                "target": entry["target"],
                "hit": hit,
                "top5": [str(h.get("key", "")).rsplit("/", 1)[-1] for h in found[:5]],
            }
        )
    return {
        "project": project,
        "queries": len(rows),
        "hits": hits,
        "hit_rate": round(hits / len(rows), 4) if rows else 0.0,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "action", choices=["register", "run"], help="register rows / run queries"
    )
    parser.add_argument(
        "--project", default=None, help="one project (default: all three)"
    )
    parser.add_argument("--out", default=None, help="write the run report JSON here")
    args = parser.parse_args(argv)
    projects = [args.project] if args.project else sorted(QUERIES)
    if args.action == "register":
        for project in projects:
            stats = register(project)
            print(json.dumps(stats))
        return 0
    reports = []
    for project in projects:
        resolved = resolve_targets(project, QUERIES[project])
        reports.append(run_battery(project, resolved))
    report_text = json.dumps(reports, indent=2)
    for report in reports:
        print(
            f"{report['project']}: {report['hits']}/{report['queries']} "
            f"hit rate {report['hit_rate']:.0%}"
        )
    if args.out:
        Path(args.out).write_text(report_text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
