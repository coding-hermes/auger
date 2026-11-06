#!/usr/bin/env python3
"""AUG-137: replay the PRD's eight success-criteria proofs and record what actually ran.

`docs/prd/xml-spec-graph.md` carries a "Success criteria — replayable proofs" section whose bullets
are the PRD's own definition of "done". Nothing replayed them: the board inherited a prose list.
This script is the runner. For every bullet it records id, claim, disposition, status, the command
it ran, measured corpus/bundle bytes and the wall-clock latency of the run itself, and it writes one
checked-in artifact (`docs/prd/evidence/AUG-137-prd-proofs.json`).

Three properties make it a gate instead of a report:

* **Coverage is reconciled, not assumed.** The bullets are PARSED from the PRD and matched against
  `docs/prd/xml-spec-graph-proofs.json` by sha256 of the normalized bullet text. A bullet nobody
  classified, a stale allowlist entry, or a reordered list is a hard failure — so quiet narrowing
  (deleting a bullet, or adding a deferral without editing the reviewed allowlist) cannot happen.
* **`not-run` is never a pass.** A deferred proof is recorded as `not-run` with the board row that
  owns the missing capability; a required proof whose prerequisite is absent is also `not-run`; and
  the overall verdict is `fail` if any REQUIRED proof failed OR did not run.
* **Numbers come from the run.** Corpus bytes are `os.stat` over the corpus documents, bundle bytes
  are the assembler's own output file, and latency is measured around the run — no constants.

Deferrals are a reviewed decision: the allowlist names, for every non-required proof, the board row
id that owns the missing capability and why it is not reachable from this tree today.

Usage:
    python3 scripts/replay_prd_proofs.py             # replay, print, rewrite the artifact
    python3 scripts/replay_prd_proofs.py --check     # replay and require the committed artifact's
                                                     # verdict structure to agree (the gate arm)
    python3 scripts/replay_prd_proofs.py --print-prd-proofs
                                                     # dump the parsed bullets + sha256, for
                                                     # maintaining the allowlist
Exit codes: 0 all required proofs passed and the artifact agrees; 1 verdict failure / coverage gap /
artifact drift; 2 the PRD or the allowlist could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run from a checkout without installing auger
    sys.path.insert(0, str(REPO_ROOT))

import auger  # noqa: E402  (needs REPO_ROOT on sys.path first)
from scripts import gen_spec_view  # noqa: E402  (the AUG-136 generated view)

PRD_PATH = REPO_ROOT / "docs" / "prd" / "xml-spec-graph.md"
SECTION_TITLE = "Success criteria — replayable proofs"
ALLOWLIST_PATH = REPO_ROOT / "docs" / "prd" / "xml-spec-graph-proofs.json"
ARTIFACT_PATH = REPO_ROOT / "docs" / "prd" / "evidence" / "AUG-137-prd-proofs.json"

#: The selection proof 7 measures. One scope token of the authored corpus: a real selection that
#: must exclude the blocks outside it, not a hand-trimmed blob.
BUNDLE_SCOPE = "auger.spec.select"
#: "Materially smaller" as a MEASURED floor, not a slogan. Measured on this corpus 2026-11-05:
#: 2661 bytes of bundle against 8889 bytes of corpus (ratio 0.299). The floor is set well above the
#: measurement (0.60) so ordinary corpus growth does not make the proof flaky, while a regression
#: that stopped excluding unrelated blocks lands far above it.
SIZE_FRACTION_FLOOR = 0.60
#: The generated views the shared-block proof must update. Enumerated so "every view" is a fact.
GENERATED_VIEWS = ("docs/SPEC-CORPUS-VIEW.md",)

PROOF_BULLET_PREFIX = "- "


class ProofNotRun(Exception):
    """A required proof could not be exercised (prerequisite absent) — recorded as `not-run`."""


class CoverageError(Exception):
    """The PRD's proof list and the reviewed allowlist disagree — nothing is replayed."""


# --------------------------------------------------------------------------- the PRD's proofs
def parse_prd_proofs(
    prd_path: Path = PRD_PATH, section_title: str = SECTION_TITLE
) -> list:
    """Every bullet of the PRD's success-criteria section, with a sha256 of its normalized text."""
    path = Path(prd_path)
    if not path.is_file():
        raise CoverageError(f"{path}: the PRD is missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {section_title}":
            start = index + 1
            break
    if start is None:
        raise CoverageError(f"{path}: no '## {section_title}' section")
    proofs = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped.startswith(PROOF_BULLET_PREFIX):
            continue
        normalized = " ".join(stripped[len(PROOF_BULLET_PREFIX) :].split())
        proofs.append(
            {
                "id": f"P{len(proofs) + 1}",
                "text": normalized,
                "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            }
        )
    if not proofs:
        raise CoverageError(f"{path}: the '## {section_title}' section has no bullets")
    return proofs


def load_allowlist(allowlist_path: Path = ALLOWLIST_PATH) -> dict:
    path = Path(allowlist_path)
    if not path.is_file():
        raise CoverageError(f"{path}: the reviewed allowlist is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoverageError(f"{path}: not valid JSON ({exc})") from None
    entries = payload.get("proofs")
    if not isinstance(entries, list) or not entries:
        raise CoverageError(f"{path}: `proofs` must be a non-empty list")
    return payload


def reconcile(parsed: list, allowlist: dict) -> list:
    """Pair each parsed bullet with its reviewed disposition, or refuse the whole run."""
    by_sha = {}
    for entry in allowlist["proofs"]:
        for field in ("id", "claim", "disposition", "prd_sha256"):
            if not str(entry.get(field, "")).strip():
                raise CoverageError(
                    f"allowlist entry {entry.get('id')!r}: `{field}` is required"
                )
        if entry["disposition"] not in ("required", "deferred"):
            raise CoverageError(
                f"allowlist entry {entry['id']}: disposition {entry['disposition']!r} "
                "(required or deferred)"
            )
        if (
            entry["disposition"] == "deferred"
            and not str(entry.get("owner", "")).strip()
        ):
            raise CoverageError(
                f"allowlist entry {entry['id']}: a deferral must name the board row (`owner`) "
                "that owns the missing capability"
            )
        by_sha[entry["prd_sha256"]] = entry
    problems = []
    rows = []
    for proof in parsed:
        entry = by_sha.get(proof["sha256"])
        if entry is None:
            problems.append(
                f"{proof['id']}: the PRD bullet is in no allowlist entry "
                f"(sha256 {proof['sha256'][:12]}…): {proof['text'][:90]}…"
            )
            continue
        if entry["id"] != proof["id"]:
            problems.append(
                f"{proof['id']}: bullet {proof['id']} is classified as {entry['id']!r} — "
                "the allowlist is out of order with the PRD"
            )
        rows.append({**proof, **entry, "prd_text": proof["text"]})
    seen = {row["prd_sha256"] for row in rows}
    for sha, entry in by_sha.items():
        if sha not in seen:
            problems.append(
                f"{entry['id']}: allowlist entry matches NO PRD bullet (stale entry, "
                "sha256 {sha}[:12]…)"
            )
    if problems:
        raise CoverageError(
            "the PRD's proof list and the reviewed allowlist disagree:\n  "
            + "\n  ".join(problems)
            + "\n  fix: python3 scripts/replay_prd_proofs.py --print-prd-proofs, then re-review "
            "docs/prd/xml-spec-graph-proofs.json"
        )
    return rows


# --------------------------------------------------------------------------- shared measurement
def _corpus_bytes(specs_root: Path) -> int:
    """Bytes of every authored document the loader reads (the corpus, not the schema)."""
    return sum(
        path.stat().st_size
        for path in sorted(specs_root.rglob("*.xml"))
        if path.is_file()
    )


def _run(argv, cwd=None, timeout=600) -> subprocess.CompletedProcess:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
    }
    env["AUGER_TEST_LOCK_INHERITED"] = (
        "1"  # a nested pytest must not flock against its parent
    )
    return subprocess.run(
        [str(arg) for arg in argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _staged_view_corpus(workdir: Path, specs_root: Path) -> Path:
    corpus = workdir / "specs"
    shutil.copytree(specs_root, corpus)
    return corpus


def _edit_one_block(corpus: Path) -> tuple:
    """One XML source edit, in the style a reviewer would make: prose in a single block."""
    target = corpus / "spec-graph.xml"
    if not target.is_file():
        raise ProofNotRun(
            f"{target} is absent, so a shared-block edit cannot be staged"
        )
    text = target.read_text(encoding="utf-8")
    needle = "and a document the published XSD rejects is refused"
    if needle not in text:
        raise ProofNotRun(
            "the sentence this proof edits is no longer in specs/spec-graph.xml — update the proof"
        )
    replacement = "and any authored document that breaks the published grammar is turned away at once"
    target.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    return needle, replacement


# --------------------------------------------------------------------------- the replays
def replay_p2(ctx: dict) -> dict:
    """P2 — one XML source edit updates every generated view; no duplicate source text remains."""
    corpus = _staged_view_corpus(ctx["workdir"], ctx["specs_root"])
    corpus_bytes = _corpus_bytes(corpus)
    started = time.perf_counter()
    before = gen_spec_view.build_view(corpus)
    old, new = _edit_one_block(corpus)
    after = gen_spec_view.build_view(corpus)
    latency_ms = (time.perf_counter() - started) * 1000

    assert after != before, "the generated view did not change when the corpus did"
    assert new in after, "the edited prose is absent from the regenerated view"
    assert old not in after, "the pre-edit prose survived in the regenerated view"
    assert after.count(new) == 1, (
        f"the edited sentence appears {after.count(new)} times in the view — a shared block must "
        "be rendered once"
    )
    _root, _documents, blocks = auger.load_spec_corpus(corpus)
    for block in blocks:
        if block["text"]:
            assert after.count(block["text"]) == 1, (
                f"block {block['id']}'s prose appears {after.count(block['text'])} times in the "
                "view — source text is being maintained twice"
            )
    committed = REPO_ROOT / GENERATED_VIEWS[0]
    assert committed.is_file(), f"{GENERATED_VIEWS[0]} is not committed"
    assert committed.read_text(encoding="utf-8") == gen_spec_view.build_view(
        ctx["specs_root"]
    ), (
        f"{GENERATED_VIEWS[0]} is not what the generator emits for specs/ — regenerate it: "
        f"{gen_spec_view.REGENERATE}"
    )
    for other in GENERATED_VIEWS[1:]:
        assert (REPO_ROOT / other).is_file(), f"{other} is named as a view but missing"
    return {
        "command": (
            f"python3 {Path(gen_spec_view.__file__).name} (build_view before/after one prose edit "
            "in a copy of specs/)"
        ),
        "corpus_bytes": corpus_bytes,
        "bundle_bytes": len(after.encode("utf-8")),
        "bytes_meaning": "corpus = the copied *.xml documents; bundle = the regenerated view",
        "latency_ms": round(latency_ms, 1),
        "detail": (
            f"{len(GENERATED_VIEWS)} generated view(s) verified byte-identical to the generator's "
            f"output for specs/; one block edit propagated to the view and every block's prose "
            f"appears exactly once ({len(blocks)} blocks)"
        ),
    }


def _copy_obligation_repo(workdir: Path) -> Path:
    """A git work tree holding what the migration obligation needs (its `path` must be tracked)."""
    copy = workdir / "repo"
    copy.mkdir(parents=True)
    for rel in (
        "auger.py",
        "pytest.ini",
        "tests/conftest.py",
        "tests/test_xml_specs.py",
        "tests/fixtures/xml/verification",
    ):
        source = REPO_ROOT / rel
        target = copy / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise ProofNotRun(f"{rel} is missing from the checkout")
    git = ["git", "-c", "user.email=proof@example.invalid", "-c", "user.name=proof"]
    for argv in (
        [*git, "init", "-q"],
        [*git, "add", "-A"],
        [*git, "commit", "-qm", "proof"],
    ):
        result = _run(argv, cwd=copy)
        if result.returncode != 0:
            raise ProofNotRun(
                f"`{' '.join(argv[:2])}` failed in the staged copy: "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
    return copy


def replay_p5(ctx: dict) -> dict:
    """P5 — one query returns the whole migration obligation; a no-op replacement fails its test."""
    corpus = REPO_ROOT / "tests" / "fixtures" / "xml" / "verification"
    if not corpus.is_dir():
        raise ProofNotRun(
            f"{corpus} is missing, so the migration obligation cannot be queried"
        )
    started = time.perf_counter()
    bundle_path = ctx["workdir"] / "migration-bundle.json"
    queried = _run(
        [
            sys.executable,
            REPO_ROOT / "auger.py",
            "spec",
            "bundle",
            corpus,
            "REQ-MIG-1",
        ],
        cwd=REPO_ROOT,
    )
    if queried.returncode != 0:
        raise ProofNotRun(
            "`spec bundle tests/fixtures/xml/verification REQ-MIG-1` refused: "
            f"{(queried.stderr or queried.stdout).strip()[:300]}"
        )
    bundle_path.write_text(queried.stdout, encoding="utf-8")
    payload = json.loads(queried.stdout)
    assert payload["status"] == "ok", f"the bundle query answered {payload['status']!r}"
    obligations = payload["verifications"]
    assert len(obligations) == 1, f"expected one obligation, got {len(obligations)}"
    obligation = obligations[0]
    for field in ("migration", "fixture", "path", "selector", "runner", "assertions"):
        assert obligation.get(field), f"the bundle is missing `{field}`: {obligation}"

    copy = _copy_obligation_repo(ctx["workdir"])
    node = f"{obligation['path']}::{obligation['selector']}"
    applied = _run(
        [*obligation["runner"], node, "-q", "-p", "no:cacheprovider"], cwd=copy
    )
    assert applied.returncode == 0, (
        f"the named apply test does not pass on the committed migration: "
        f"{(applied.stdout or applied.stderr).strip()[-600:]}"
    )
    migration = copy / obligation["migration"]
    assert migration.is_file(), f"{obligation['migration']} is not in the staged copy"
    migration.write_text("", encoding="utf-8")
    no_op = _run(
        [*obligation["runner"], node, "-q", "-p", "no:cacheprovider"], cwd=copy
    )
    latency_ms = (time.perf_counter() - started) * 1000
    assert no_op.returncode != 0, (
        "replacing the named migration with a no-op left the required test PASSING — the "
        "obligation is not load-bearing"
    )
    return {
        "command": (
            f"python3 auger.py spec bundle tests/fixtures/xml/verification REQ-MIG-1; then "
            f"{' '.join(obligation['runner'])} {node} in a git copy of the repo, and the same "
            "after the named migration is replaced by a no-op"
        ),
        "corpus_bytes": _corpus_bytes(corpus),
        "bundle_bytes": len(queried.stdout.encode("utf-8")),
        "bytes_meaning": "corpus = the fixture corpus documents; bundle = the `spec bundle` JSON",
        "latency_ms": round(latency_ms, 1),
        "detail": (
            f"one query returned migration={obligation['migration']}, "
            f"fixture={obligation['fixture']}, test={obligation['path']}::"
            f"{obligation['selector']}; the named test passes on the committed migration and "
            f"FAILS (rc {no_op.returncode}) when that file is replaced by a no-op"
        ),
    }


def replay_p7(ctx: dict) -> dict:
    """P7 — corpus bytes, selected-bundle bytes and lookup latency, measured from the run."""
    specs_root = ctx["specs_root"]
    corpus_bytes = _corpus_bytes(specs_root)
    if corpus_bytes == 0:
        raise ProofNotRun(
            f"no authored documents under {specs_root}, so there is nothing to size"
        )
    out = ctx["workdir"] / "bundle.json"
    started = time.perf_counter()
    assembled = _run(
        [
            sys.executable,
            REPO_ROOT / "auger.py",
            "spec",
            "assemble",
            specs_root,
            "--applies-to",
            BUNDLE_SCOPE,
            "--format",
            "json",
            "--out",
            out,
        ],
        cwd=REPO_ROOT,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    if assembled.returncode != 0:
        raise ProofNotRun(
            f"`spec assemble --applies-to {BUNDLE_SCOPE}` refused: "
            f"{(assembled.stderr or assembled.stdout).strip()[:300]}"
        )
    assert out.is_file(), f"the assembler wrote no bundle to {out}"
    bundle_bytes = out.stat().st_size
    payload = json.loads(out.read_text(encoding="utf-8"))
    _root, _documents, blocks = auger.load_spec_corpus(specs_root)
    selected = payload["counts"]["total"]
    assert selected > 0, (
        f"the selection `applies-to {BUNDLE_SCOPE}` matched NO block — measuring an empty bundle "
        "proves nothing about excluding unrelated blocks"
    )
    assert selected < len(blocks), (
        f"the selection covers {selected} of {len(blocks)} blocks — it excludes nothing, so the "
        "bundle-vs-corpus claim would be meaningless"
    )
    ratio = bundle_bytes / corpus_bytes
    assert ratio <= SIZE_FRACTION_FLOOR, (
        f"the selected bundle is {bundle_bytes} bytes of a {corpus_bytes}-byte corpus "
        f"(ratio {ratio:.3f}) — not materially smaller than the {SIZE_FRACTION_FLOOR:.2f} floor"
    )
    return {
        "command": (
            f"python3 auger.py spec assemble specs --applies-to {BUNDLE_SCOPE} --format json "
            "--out <bundle>"
        ),
        "corpus_bytes": corpus_bytes,
        "bundle_bytes": bundle_bytes,
        "bytes_meaning": "corpus = the *.xml documents under specs/; bundle = the assembled JSON",
        "latency_ms": round(latency_ms, 1),
        "detail": (
            f"{selected}/{len(blocks)} blocks selected by `applies-to {BUNDLE_SCOPE}`; bundle is "
            f"{ratio:.1%} of the corpus ({bundle_bytes} of {corpus_bytes} bytes), floor "
            f"{SIZE_FRACTION_FLOOR:.0%}"
        ),
    }


REPLAYS = {"P2": replay_p2, "P5": replay_p5, "P7": replay_p7}


# --------------------------------------------------------------------------- the run
def _counts(rows: list) -> dict:
    return {
        "proofs": len(rows),
        "required": sum(1 for row in rows if row["disposition"] == "required"),
        "deferred": sum(1 for row in rows if row["disposition"] == "deferred"),
        "pass": sum(1 for row in rows if row["status"] == "pass"),
        "fail": sum(1 for row in rows if row["status"] == "fail"),
        "not_run": sum(1 for row in rows if row["status"] == "not-run"),
    }


def run_proofs(rows: list, workdir: Path, specs_root: Path) -> list:
    results = []
    for row in rows:
        entry = {
            "id": row["id"],
            "claim": row["claim"],
            "prd_text": row["prd_text"],
            "disposition": row["disposition"],
            "owner": row.get("owner") or None,
            "reason": row.get("reason") or None,
            "status": "not-run",
            "command": "none: not replayed",
            "corpus_bytes": None,
            "bundle_bytes": None,
            "bytes_meaning": None,
            "latency_ms": None,
            "detail": "",
        }
        if row["disposition"] == "deferred":
            entry["command"] = "none: deferred"
            entry["detail"] = (
                f"not-run (deferred to {row.get('owner')}): {row.get('reason') or 'no reason'}"
            )
            results.append(entry)
            continue
        replay = REPLAYS.get(row["id"])
        if replay is None:
            entry["detail"] = (
                "not-run: this proof is REQUIRED but the runner implements no replay for it — "
                "either implement it or record a reviewed deferral with its owner"
            )
            results.append(entry)
            continue
        try:
            measured = replay({"workdir": workdir, "specs_root": specs_root})
        except ProofNotRun as exc:
            entry["detail"] = f"not-run: {exc}"
        except AssertionError as exc:
            entry["status"] = "fail"
            entry["detail"] = f"failed: {exc}"
        except auger.SpecError as exc:
            entry["detail"] = f"not-run: the loader refused ({exc.code}): {exc.detail}"
        else:
            entry["status"] = "pass"
            entry.update(
                {
                    "command": measured["command"],
                    "corpus_bytes": measured["corpus_bytes"],
                    "bundle_bytes": measured["bundle_bytes"],
                    "bytes_meaning": measured["bytes_meaning"],
                    "latency_ms": measured["latency_ms"],
                    "detail": measured["detail"],
                }
            )
        results.append(entry)
    return results


def build_report(rows: list, workdir: Path, prd_path: Path, specs_root: Path) -> dict:
    results = run_proofs(rows, workdir, specs_root)
    counts = _counts(results)
    required_bad = [
        result["id"]
        for result in results
        if result["disposition"] == "required" and result["status"] != "pass"
    ]
    return {
        "generated_by": "scripts/replay_prd_proofs.py",
        "prd": str(Path(prd_path).resolve().relative_to(REPO_ROOT)),
        "section": SECTION_TITLE,
        "contract": (
            "corpus_bytes/bundle_bytes/latency_ms are null when a proof did not run; the reason is "
            "in its `detail`. `--check` compares this structure (verdict, counts, disposition, "
            "status, command) with the committed artifact and ignores the timings, which are a "
            "snapshot of the run that recorded them."
        ),
        "verdict": "fail" if required_bad else "pass",
        "counts": counts,
        "required_not_passing": required_bad,
        "proofs": results,
    }


def _signature(report: dict) -> dict:
    return {
        "verdict": report["verdict"],
        "counts": report["counts"],
        "required_not_passing": report["required_not_passing"],
        "proofs": [
            {
                "id": proof["id"],
                "disposition": proof["disposition"],
                "status": proof["status"],
                "owner": proof["owner"],
                "command": proof["command"],
            }
            for proof in report["proofs"]
        ],
    }


def _print(report: dict) -> None:
    counts = report["counts"]
    print(
        f"prd proofs: {counts['proofs']} total · {counts['required']} required · "
        f"{counts['deferred']} deferred → {report['verdict'].upper()}"
    )
    for proof in report["proofs"]:
        numbers = ""
        if proof["status"] == "pass":
            numbers = (
                f"  corpus={proof['corpus_bytes']}B bundle={proof['bundle_bytes']}B "
                f"latency={proof['latency_ms']}ms"
            )
        print(
            f"  {proof['id']:>3}  {proof['disposition']:<8} {proof['status']:<8}"
            f"{numbers}  {proof['detail'][:110]}"
        )


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="replay the PRD's success-criteria proofs and record what ran"
    )
    parser.add_argument(
        "--check", action="store_true", help="compare with the committed artifact"
    )
    parser.add_argument(
        "--print-prd-proofs",
        action="store_true",
        help="print the parsed bullets + sha256",
    )
    parser.add_argument(
        "--prd", default=str(PRD_PATH), help="the PRD to read the proofs from"
    )
    parser.add_argument(
        "--allowlist", default=str(ALLOWLIST_PATH), help="the reviewed disposition file"
    )
    parser.add_argument(
        "--artifact", default=str(ARTIFACT_PATH), help="where the run is recorded"
    )
    parser.add_argument(
        "--specs-root",
        default=str(REPO_ROOT / "specs"),
        help="the corpus the proofs measure",
    )
    args = parser.parse_args(argv)

    try:
        parsed = parse_prd_proofs(Path(args.prd))
    except CoverageError as exc:
        print(f"prd-proofs: {exc}", file=sys.stderr)
        return 2

    if args.print_prd_proofs:
        print(f"{len(parsed)} proof(s) in {args.prd} — '## {SECTION_TITLE}':")
        for proof in parsed:
            print(f"{proof['id']}  sha256={proof['sha256']}")
            print(f'    "{proof["text"]}"')
        return 0

    try:
        allowlist = load_allowlist(Path(args.allowlist))
        rows = reconcile(parsed, allowlist)
    except CoverageError as exc:
        print(f"prd-proofs: {exc}", file=sys.stderr)
        return 1

    specs_root = Path(args.specs_root).expanduser().resolve()
    workdir = Path(tempfile.mkdtemp(prefix="prd-proofs-", dir=os.environ.get("TMPDIR")))
    report = build_report(rows, workdir, args.prd, specs_root)
    _print(report)

    artifact = Path(args.artifact)
    if args.check:
        committed = None
        if artifact.is_file():
            try:
                committed = json.loads(artifact.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(
                    f"prd-proofs: {artifact} is not valid JSON ({exc})", file=sys.stderr
                )
                return 1
        if committed is None:
            print(
                f"prd-proofs: {artifact} is missing — record a run: "
                "python3 scripts/replay_prd_proofs.py",
                file=sys.stderr,
            )
            return 1
        if _signature(committed) != _signature(report):
            print(
                f"prd-proofs: {artifact} disagrees with this run — a proof regressed or the "
                "allowlist changed after the artifact was recorded. Re-record: "
                "python3 scripts/replay_prd_proofs.py",
                file=sys.stderr,
            )
            print(
                f"  committed: {json.dumps(_signature(committed), sort_keys=True)}",
                file=sys.stderr,
            )
            print(
                f"  this run:  {json.dumps(_signature(report), sort_keys=True)}",
                file=sys.stderr,
            )
            return 1
        print(f"prd-proofs: {artifact} agrees with this run")
    else:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"prd-proofs: recorded {artifact}")

    if report["verdict"] != "pass":
        print(
            "prd-proofs: VERDICT FAIL — required proofs not passing: "
            + ", ".join(report["required_not_passing"] or ["(none listed)"]),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
