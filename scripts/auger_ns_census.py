#!/usr/bin/env python3
"""AUG-197 (AC4): a census of leftover `auger-pytest-*` namespaces.

Nothing in the old pipeline REPORTED the test namespaces a contended teardown
left behind: they accumulated on the substrate silently (189 rows measured on
2026-11-22, vs 0 expected in steady state). This script is the reporting leg —
it counts and names every leftover test namespace on the configured live
DuckBrain substrate, split across the two places one can linger (the registry
mapping and the on-disk directory tree), and labels each id registry-only /
disk-only / both. It DELETES NOTHING, by design and by absence of an option:
the reaping leg lives in the suite's `live_service` fixture
(`tests/conftest.py:reap_prior_session_namespaces`), gated by AUG-117's
delete-first consistency rule.

Exit codes: 0 = census printed; 2 = the substrate could not be read (or the
DuckBrain token is missing) — never a silent green.

Usage:
    python3 scripts/auger_ns_census.py [--prefix auger-pytest-] [--json] [--url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PREFIX = "auger-pytest-"
REMEDY = (
    "check DUCKBRAIN_URL (default http://127.0.0.1:3000), the DuckBrain token"
    " (~/.duckbrain/foreman-status.token or DUCKBRAIN_API_KEY), and that the"
    " service is answering before re-running this census"
)


def census(prefix: str, url: str | None = None) -> dict[str, object]:
    """Read the registry and the disk tree for `prefix` leftovers. Never writes.

    auger reads DUCKBRAIN_URL at IMPORT time, so the --url override must be set
    on the environment before the import inside this function.
    """
    if url:
        os.environ["DUCKBRAIN_URL"] = url.rstrip("/")
    import auger  # noqa: PLC0415  (after the env override, by contract)

    db_url = auger.DB_URL.rstrip("/")
    status, body, _ = auger.db(
        "/api/namespaces",
        timeout=int(os.environ.get("AUGER_CENSUS_TIMEOUT_S", "45")),
    )
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(
            f"census: GET /api/namespaces at {db_url} failed ({status}): {str(body)[:200]}."
            f" {REMEDY}."
        )
    registry = sorted(
        name
        for n in body.get("namespaces", [])
        if isinstance(n, dict) and (name := str(n.get("name", ""))).startswith(prefix)
    )
    base = Path(auger.namespaces_base())
    disk = (
        sorted(p.name for p in base.iterdir() if p.name.startswith(prefix))
        if base.is_dir()
        else []
    )
    reg_set, disk_set = set(registry), set(disk)
    return {
        "url": db_url,
        "prefix": prefix,
        "registry": registry,
        "disk": disk,
        "both": sorted(reg_set & disk_set),
        "registry_only": sorted(reg_set - disk_set),
        "disk_only": sorted(disk_set - reg_set),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AUG-197: report leftover auger-pytest-* namespaces (read-only)."
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"leftover prefix to census (default {DEFAULT_PREFIX!r})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the census as one JSON object instead of text",
    )
    parser.add_argument(
        "--url",
        default="",
        help="override the substrate URL (default auger.DB_URL / DUCKBRAIN_URL)",
    )
    args = parser.parse_args(argv)
    try:
        result = census(args.prefix, args.url or None)
    except SystemExit as exc:
        # auger._token refuses a missing token with SystemExit — a typed, loud
        # failure (exit 2), never a silent green.
        print(f"census: could not read the substrate: {exc}", file=sys.stderr)
        print(REMEDY, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"DuckBrain substrate: {result['url']}")
    print(f"census prefix: {result['prefix']}")
    print(f"leftover test namespaces in the REGISTRY: {len(result['registry'])}")
    print(f"leftover test directories on DISK:         {len(result['disk'])}")
    print(f"  present in BOTH (row + tree): {len(result['both'])}")
    print(f"  registry-only (ghost row):    {len(result['registry_only'])}")
    print(f"  disk-only (ghost tree):       {len(result['disk_only'])}")
    for label, key in (
        ("both", "both"),
        ("registry-only", "registry_only"),
        ("disk-only", "disk_only"),
    ):
        ids = result[key]
        if ids:
            print(f"{label} ids ({len(ids)}):")
            for ns in ids:
                print(f"  - {ns}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
