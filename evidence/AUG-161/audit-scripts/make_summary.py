import re
from pathlib import Path

EV = Path("/home/kara/worktrees/auger-AUG-161/evidence/AUG-161")


def parse(path):
    out = {}
    cur = None
    for line in Path(path).read_text().splitlines():
        m = re.match(r"^== (\S+) (rel|abs) start", line)
        if m:
            cur = f"{m.group(1)}.{m.group(2)}"
            continue
        m = re.match(r"^rc=(\d+)$", line)
        if m:
            out.setdefault(cur, {})["rc"] = m.group(1)
            continue
        if line.startswith("   ") and cur:
            out.setdefault(cur, {})["counts"] = line.strip()
    return out


pre = parse(EV / "prefix" / "summary-prefix.txt")
post = parse(EV / "postfix" / "summary-postfix.txt")
fin = parse(EV / "summary-final.txt")
# merge final runs into post for re-run files
for k, v in fin.items():
    if k.startswith(("pin-green", "align-postfix2", "ac2", "full-selection")):
        post[k] = v

files = [
    ("test_auger", "test_auger.py"),
    ("test_board_evidence_audit", "test_board_evidence_audit.py"),
    ("test_packaging_wheel", "test_packaging_wheel.py"),
    ("test_prd_proofs", "test_prd_proofs.py"),
    ("test_spec_alignment_check", "test_spec_alignment_check.py"),
    ("test_spec_obligation_gate", "test_spec_obligation_gate.py"),
    ("test_spec_repository_root", "test_spec_repository_root.py"),
    ("test_spec_rollback", "test_spec_rollback.py"),
    ("test_spec_validate_obligations", "test_spec_validate_obligations.py"),
    ("test_spec_validators_agree", "test_spec_validators_agree.py"),
    ("test_task_packet", "test_task_packet.py"),
    ("test_trace_artifact", "test_trace_artifact.py"),
    ("test_xml_specs", "test_xml_specs.py"),
    ("test_executable_path_robustness", "test_executable_path_robustness.py"),
]

lines = [
    "# AUG-161 paired runs — relative vs absolute PATH, before and after the fixes",
    "",
    "rel  = `PATH=.venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin python3 -m pytest -q <file>`"
    " (identical directory set to the absolute run; only the venv ENTRY is relative — the AC2 spelling `PATH=.venv/bin:$PATH`)",
    "abs  = `PATH=/home/kara/worktrees/auger-AUG-161/.venv/bin:/home/kara/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest -q <file>`",
    "Pre-fix rel runs of the unchanged files doubled as their post-fix runs (files untouched between the two batteries).",
    "",
    "| file | rel BEFORE | abs BEFORE | rel AFTER | abs AFTER | verdict |",
    "|------|------------|------------|-----------|-----------|---------|",
]
for key, fname in files:
    p = pre.get(f"{key}.rel", {}).get("counts", "?")
    pa = pre.get(f"{key}.abs", {}).get("counts", "?")
    q = post.get(f"{key}.rel", {}).get("counts", "?")
    qa = post.get(f"{key}.abs", {}).get("counts", "?")
    if key == "test_spec_alignment_check":
        q = post.get("align-postfix2-rel", {}).get("counts", q)
        qa = post.get("align-postfix2-abs", {}).get("counts", qa)
    if key == "test_executable_path_robustness":
        # new file, no pre-fix run by design (it IS the pin); red proof = pin-red-prefix.log
        p = pa = "(new pin file — red proof: pin-red-prefix.log)"
    same = q == qa
    verdict = "pairs after fix" if same else "DIFFERS AFTER FIX"
    if key == "test_packaging_wheel":
        verdict = "pairs (2 pre-existing errors on BOTH spellings, unrelated to PATH)"
    lines.append(f"| {fname} | {p} | {pa} | {q} | {qa} | {verdict} |")

lines += [
    "",
    "Post-fix full audited selection, absolute PATH (`full-selection-abs`):",
    "  "
    + post.get("full-selection-abs", {}).get("counts", "?")
    + " (rc="
    + post.get("full-selection-abs", {}).get("rc", "?")
    + ")",
    "AC2 verbatim (`PATH=.venv/bin:$PATH python3 -m pytest -q tests/test_spec_repository_root.py`):",
    "  "
    + post.get("ac2-exact", {}).get("counts", "?")
    + " (rc="
    + post.get("ac2-exact", {}).get("rc", "?")
    + ")",
    "",
    "## Attribution of the pre-fix deltas",
    "",
    "- test_auger.py BEFORE: rel 4 failed / 332 passed / 1 skipped / 10 errors vs abs 336 passed / 1 skipped.",
    "  Decomposition from `prefix/test_auger.rel.log`: exactly ONE failure is this row's defect"
    " (`test_a_broken_verb_fails_exactly_one_named_test`, the `cwd=str(child)` spawn); the other 3 failures +"
    ' 10 teardown errors are `DELETE /api/namespaces -> 409 "Push in flight ... sync lock held by pid 3604651"`'
    " (the DuckBrain s3-sync push window), not PATH-related — the leak-audit failure is downstream of those 409s.",
    "- test_spec_alignment_check.py BEFORE: rel 20 failed / 2 passed vs abs 22 passed — 20 tests share the"
    " `run_cli` helper (site at base line 178/191), plus the `--diff-stdin` loop (313).",
    '- test_task_packet.py BEFORE: rel 1 passed / 6 skipped vs abs 7 passed — `shutil.which("boardctl")`'
    " finds nothing when PATH lacks `/home/kara/.local/bin` (composition, not the relative-entry defect;"
    " resolved by giving both run spellings the SAME directory set).",
    "- test_trace_artifact.py BEFORE: rel 1 failed / 42 passed vs abs 43 passed — the `cwd=str(tmp_path)` align call.",
    "- test_packaging_wheel.py: 2 errors on BOTH spellings before AND after (pre-existing, unrelated to this row; left alone).",
]

out = EV / "summary-paired.md"
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
