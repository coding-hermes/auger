#!/usr/bin/env python3
"""Prove the AUG-156 board edit is surgical: untouched lines byte-identical, touched lines differ
only in `evidence_run_id` (+ the `detail.evidence[]` label demotion).

    python3 evidence/auger-AUG-156/verify-surgical.py \
        --before <pre-fix board.jsonl> --after .coding-hermes/board/tasks.jsonl

Exit 0 when every invariant holds; exit 1 with the offending lines listed.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    args = parser.parse_args(argv)

    before = open(args.before, encoding="utf-8").read().splitlines()
    after = open(args.after, encoding="utf-8").read().splitlines()
    if len(before) != len(after):
        print(f"line count changed: {len(before)} -> {len(after)}")
        return 1

    same = changed = 0
    problems: list[tuple[int, str, str]] = []
    for n, (old, new) in enumerate(zip(before, after), start=1):
        if old == new:
            same += 1
            continue
        changed += 1
        o, a = json.loads(old), json.loads(new)
        if a.get("status") != "complete":
            problems.append((n, o.get("id"), "non-complete row changed"))
        if o.get("evidence_run_id") is not None:
            problems.append((n, o.get("id"), "row already had evidence_run_id"))
        if (
            not isinstance(a.get("evidence_run_id"), str)
            or not a["evidence_run_id"].strip()
        ):
            problems.append((n, o.get("id"), "missing evidence_run_id"))
        os_, as_ = dict(o), dict(a)
        for payload in (os_, as_):
            payload.pop("detail", None)
            payload.pop("evidence_run_id", None)
        if os_ != as_:
            problems.append(
                (n, o.get("id"), f"other fields moved: {sorted(set(as_) ^ set(os_))}")
            )
        if [k for k in o if k != "evidence_run_id"] != [
            k for k in a if k != "evidence_run_id"
        ]:
            problems.append((n, o.get("id"), "key order changed"))
        od, ad = o.get("detail"), a.get("detail")
        if od == ad:
            continue
        if not isinstance(od, dict) or not isinstance(ad, dict):
            problems.append((n, o.get("id"), "detail type changed"))
            continue
        od2 = {k: v for k, v in od.items() if k != "evidence"}
        ad2 = {k: v for k, v in ad.items() if k != "evidence"}
        if od2 != ad2:
            problems.append((n, o.get("id"), "detail keys other than evidence moved"))
        oe, ae = od.get("evidence") or [], ad.get("evidence") or []
        if len(oe) != len(ae):
            problems.append((n, o.get("id"), "evidence entry count changed"))
            continue
        for eo, ea in zip(oe, ae):
            if eo.get("run_id") == ea.get("run_id"):
                continue
            if isinstance(eo.get("run_id"), str) and eo["run_id"].startswith("none:"):
                problems.append((n, o.get("id"), "none: absence marker was rewritten"))
            if ea.get("run_id") is not None and ea.get("run_id") != a.get(
                "evidence_run_id"
            ):
                problems.append(
                    (
                        n,
                        o.get("id"),
                        "run_id rewritten to a value that is not the binding",
                    )
                )
            if ea.get("removed_label") != eo.get("run_id"):
                problems.append(
                    (n, o.get("id"), "removed_label does not hold the old value")
                )

    print(
        f"lines={len(before)} identical={same} changed={changed} problems={len(problems)}"
    )
    for problem in problems:
        print("  ", problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
