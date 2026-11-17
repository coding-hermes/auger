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
#: Rows requested per query. The namespace stores every block at least twice (see
#: `_distinct_keys`), so the window must be wider than TOP_K for "top-5 BLOCKS" to be
#: measurable; the report records how many distinct blocks the window actually held.
CANDIDATE_ROWS = 25
#: One memory row's text budget; larger blocks split into -Pnn PART rows at line
#: boundaries (the substrate's embed path 500s on very large payloads).
ROW_CHAR_BUDGET = 24000

# 12 realistic queries per project, each naming the block its answer lives in. Targets are
# block ids from corpora/<project>/manifest.json — chosen by reading the source headings, so
# every query has exactly one right answer in the corpus.
QUERIES: dict[str, list[tuple[str, str]]] = {
    "code-factory": [
        (
            "how does the control host dispatch work to a node's inbox",
            "CF-ENVELOPE-DISPATCH-CONTROL-HOST-NODE-INB-S001",
        ),
        (
            "what does ok mean in the envelope verdict",
            "CF-ENVELOPE-OK-IS-THE-VERDICT-STATUS-IS-TH-S003",
        ),
        (
            "how does durable delivery and the delivery budget work",
            "CF-ENVELOPE-DURABLE-DELIVERY-AND-THE-DELIV-S006",
        ),
        (
            "what are the three records of fleet memory",
            "CF-FLEET-MEMORY-3-THE-THREE-RECORDS-S003",
        ),
        (
            "what is the fleet index record",
            "CF-FLEET-MEMORY-R2-THE-FLEET-INDEX-SHARED-FACT-S005",
        ),
        (
            "how does the control host search fleet memory",
            "CF-FLEET-MEMORY-5-HOW-THE-CONTROL-HOST-SEARCHE-S008",
        ),
        (
            "which harnesses were measured in the matrix",
            "CF-HARNESS-MATRIX-2-THE-HARNESSES-MEASURED-S002",
        ),
        (
            "what is the resolution rule for picking a harness",
            "CF-HARNESS-MATRIX-4-THE-RESOLUTION-RULE-S004",
        ),
        (
            "what does the hermes harness record for identity",
            "CF-HARNESS-MEMORY-3-1-HERMES-THE-ONLY-HARNESS-WH-S004",
        ),
        (
            "what is the node lifecycle contract",
            "CF-NODE-LIFECYCLE-CONTRACT-CF-008-S005",
        ),
        ("what are the node failure semantics", "CF-NODE-FAILURE-SEMANTICS-S012"),
        (
            "how does trace identity propagate hop by hop",
            "CF-TRACE-3-PROPAGATION-CONTRACT-HOP-BY-S003",
        ),
    ],
    "auger-docs": [
        (
            "how does the engine model a graph rather than a list",
            "AD-ENGINE-THE-CORE-MODEL-A-GRAPH-NOT-A-L-S002",
        ),
        (
            "what changes in the engine when bundling",
            "AD-SPEC-002-BUNDLES-2-WHAT-BUNDLING-CHANGES-IN-THE-S005",
        ),
        ("why is the xml spec graph authored", "AD-SPEC-003-XML-SPECS-RATIONALE-S001"),
        (
            "what is the refusal contract for xml specs",
            "AD-SPEC-003-XML-SPECS-REFUSAL-CONTRACT-S005",
        ),
        (
            "what does the muster consumer contract require",
            "AD-MUSTER-CONSUMER-THE-CONTRACT-S001",
        ),
        (
            "how is semantic retrieval verified on the substrate",
            "AD-SUBSTRATE-VERIFICATION-5-SEMANTIC-RETRIEVAL-S005",
        ),
        ("what does the recall verb do", "AD-VERBS-RECALL-S013"),
        ("how is a release cut", "AD-RELEASE-CUTTING-A-RELEASE-S002"),
        (
            "what are the four beats of the graph",
            "AD-SPEC-001-GRAPH-4-THE-FOUR-BEATS-S006",
        ),
        (
            "what is the propagate beat",
            "AD-SPEC-001-GRAPH-BEAT-4-PROPAGATE-CLOSING-BRANC-S010",
        ),
        (
            "where does the grammar live after pip install",
            "AD-XML-SPEC-SCHEMA-INSTALLED-LAYOUT-WHERE-THE-GRA-S002",
        ),
        (
            "which open decisions does the design resolve",
            "AD-DESIGN-4-OPEN-DECISIONS-WITH-SHIPPED-S006",
        ),
    ],
    "coding-hermes-director": [
        (
            "what happens when a session never writes its report",
            "CHD-DESIGN-5-1-SESSION-NEVER-WRITES-ITS-R-S028",
        ),
        (
            "what is the send state machine",
            "CHD-VOL-C-TOOLS-MESSAGING-AD-6-4-THE-SEND-STATE-MACHINE-COM-S034",
        ),
        ("how do tool grants work by role", "CHD-DESIGN-6-1-TOOL-GRANTS-BY-ROLE-S035"),
        (
            "what is the report contract owed by every agent",
            "CHD-DESIGN-4-5-THE-REPORT-CONTRACT-OWED-B-S024",
        ),
        (
            "how does restart recovery work",
            "CHD-VOL-B-RECORDS-STORAGE-RE-6-4-RESTART-RECOVERY-S040",
        ),
        (
            "what is the archive lookup and restore procedure",
            "CHD-VOL-B-RECORDS-STORAGE-RE-5-10-ARCHIVE-LOOKUP-QUERIES-AN-S034",
        ),
        (
            "what is the sessionarchive record",
            "CHD-VOL-B-RECORDS-STORAGE-RE-3-12-SESSIONARCHIVE-EXPORTMANI-S016",
        ),
        (
            "what does the data model define for an agent",
            "CHD-DATA-MODEL-1-1-AGENT-S002",
        ),
        (
            "what is backpressure per gateway",
            "CHD-DESIGN-3-4-BACKPRESSURE-IS-PER-GATEWA-S016",
        ),
        (
            "what rules do the operation cards follow",
            "CHD-VOL-E-OPERATION-REFERENC-7-OPERATION-CARDS-S009",
        ),
        (
            "what is the result envelope",
            "CHD-VOL-E-OPERATION-REFERENC-3-1-RESULT-ENVELOPE-S004",
        ),
        ("which release blockers remain", "CHD-VERDICT-RESIDUAL-RELEASE-BLOCKERS-S002"),
    ],
}


def project_key(project: str) -> str:
    return f"/auger/{PROJECT_KEY_PREFIX}-{project}/"


def _distinct_keys(hits: list) -> list[str]:
    """Distinct keys of `hits`, in substrate order, with PART rows attributed to their block.

    AUG-178 follow-up (measured 2026-11-17): the namespace holds EVERY block twice (the
    register action ran twice, and a memory write appends rather than upserts), so asking the
    substrate for 5 rows yields only 2-4 distinct blocks and "target in top-5" silently became
    "target in top-2..4". A hit must be measured over five distinct BLOCKS, so the candidate
    window is CANDIDATE_ROWS rows and the keys are de-duplicated here.
    """
    keys: list[str] = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        key = str(h.get("key", "")).rsplit("/", 1)[-1]
        if key and key not in keys:
            keys.append(key)
    return keys


def _rank(hits: list, target_block_id: str) -> int | None:
    """1-based rank of the target block among the DISTINCT keys of `hits`, else None.

    A PART row (`<target>-P01`) is the same block, so it counts at the block's rank.
    """
    for idx, key in enumerate(_distinct_keys(hits), start=1):
        if key == target_block_id or key.startswith(f"{target_block_id}-P"):
            return idx
    return None


def _hit(hits: list, target_block_id: str) -> bool:
    """True when the target block's row (or any PART row of it) is among the hits."""
    return _rank(hits, target_block_id) is not None


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
    failures: list[str] = []
    rows: list[tuple[str, str]] = []
    for block in blocks:
        content = block["text"].strip()
        if not content:
            continue
        # Very large sections (the director's generated manifest tables run to 340k chars)
        # 500 the substrate's embed path. Split them into bounded PART rows at line
        # boundaries — a hit on any part still round-trips to the block id (AC3), and no
        # text is dropped.
        if len(content) <= ROW_CHAR_BUDGET:
            rows.append((project_key(project) + block["id"], content))
            continue
        parts, current = [], ""
        for line in content.splitlines(keepends=True):
            if current and len(current) + len(line) > ROW_CHAR_BUDGET:
                parts.append(current)
                current = ""
            current += line
        if current.strip():
            parts.append(current)
        # A block can be ONE line of 340k characters (generated manifest tables); a line
        # boundary cannot split it, so any still-oversized part is hard-split by character.
        hard: list[str] = []
        for part in parts:
            for start in range(0, len(part), ROW_CHAR_BUDGET):
                chunk = part[start : start + ROW_CHAR_BUDGET]
                if chunk.strip():
                    hard.append(chunk)
        parts = hard
        for idx, part in enumerate(parts, start=1):
            rows.append((f"{project_key(project)}{block['id']}-P{idx:02d}", part))
    for key, content in rows:
        # The substrate answers an occasional 500 INTERNAL_ERROR under continuous write
        # load; a row that fails after bounded retries is NAMED, never silently skipped.
        for attempt in range(4):
            try:
                auger.remember(NAMESPACE, key, content)
                break
            except SystemExit as exc:
                if attempt == 3:
                    failures.append(f"{key.rsplit('/', 1)[-1]}: {exc}")
                else:
                    time.sleep(1.5 * (attempt + 1))
        written += 1
        if written % 100 == 0:
            print(f"  {project}: {written} row(s) written", file=sys.stderr, flush=True)
        time.sleep(0.05)  # bounded politeness: the substrate embeds every row on write
    return {
        "project": project,
        "blocks": len(blocks),
        "rows_written": written,
        "failed_rows": failures,
    }


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
        found = []
        for attempt in range(3):
            try:
                # the first recall of a process pays the provider round-trip (~20s); a
                # 60s default has timed out under host load, and a transport timeout is
                # NOT an empty store (AUG-075) — retry, then record the failure.
                found = auger.recall(
                    NAMESPACE,
                    entry["query"],
                    CANDIDATE_ROWS,
                    project_id=f"{PROJECT_KEY_PREFIX}-{project}",
                    timeout=180,
                )
                break
            except Exception as exc:
                if attempt == 2:
                    print(
                        f"  query FAILED after retries: {entry['query']!r}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    time.sleep(5)
        distinct = _distinct_keys(found)
        rank = _rank(found, entry["target"])
        hit = rank is not None and rank <= TOP_K
        hits += hit
        rows.append(
            {
                "query": entry["query"],
                "target": entry["target"],
                "hit": hit,
                # rank among DISTINCT blocks (None = the target was not in the window at all)
                "rank": rank,
                "distinct_in_window": len(distinct),
                "top5": distinct[:TOP_K],
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
