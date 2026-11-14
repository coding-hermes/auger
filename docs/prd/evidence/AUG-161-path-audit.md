# AUG-161 — Audit: `sys.executable` subprocess spawns under a relative PATH entry

Row: AUG-161 (P2). Branch `wt/AUG-161`, base `3df75c2`.

## The mechanism (measured)

With a relative PATH entry (`PATH=.venv/bin:$PATH python3 -m pytest`), CPython derives a
**relative** `sys.executable` (measured on this tree: `'.venv/bin/python3'`; with an
absolute PATH entry the same interpreter reports
`'/home/kara/worktrees/auger-AUG-161/.venv/bin/python'`). A test that spawns
`sys.executable` as a subprocess while passing `cwd=<scratch dir>` can no longer resolve
the interpreter — the child dies (FileNotFoundError / `No such file or directory`) **before
any assertion runs**, so the failure reads as a code red when the tree is fine.

Two properties decide whether a site is affected:

1. does the spawn keep the RAW `sys.executable` in argv (not an `os.path.abspath(...)`
   resolution), and
2. does the child's cwd resolve that relative spelling — i.e. is `cwd=` a scratch dir, or
   is it the repo root / inherited?

This suite never `chdir`s (no `os.chdir` anywhere under `tests/`), so a site with NO
`cwd=` kwarg inherits the pytest invocation dir (the repo root for every documented
invocation, including `tests/gate.sh` and the driver), and a relative `sys.executable`
still resolves there. The same holds for sites whose `cwd=` is explicitly `REPO_ROOT`.
Only sites whose `cwd=` is a scratch dir are broken.

## Method (mechanical, not memory)

- `grep -n 'sys\.executable' tests/*.py` → **49 occurrences across 14 files** (the board
  row said 18 files at filing; the mechanical count is 14, re-checkable with the grep —
  driver re-measured at base `3df75c2`: `git grep -o` = 49 occurrences over 14 `.py` files).
- An AST reader (`evidence/AUG-161/audit-scripts/audit_sites2.py`, run with the venv interpreter) classifies
  every occurrence: direct spawn (argument subtree of a `subprocess.run/Popen/
  check_output/check_call/call` node), helper-mediated spawn (passed to a local helper
  that spawns), or non-spawn (provenance assertions, display strings, runner-vector
  comparisons, comments/docstrings). 46 code occurrences + 3 comment/docstring lines = 49.
- Unaffected claims are pinned mechanically by
  `tests/test_executable_path_robustness.py` (fast, hermetic, named in the gate's pytest
  arm): a scratch-cwd spawn keeping raw `sys.executable` in argv fails the invariant.
  Red proof before the fix: `evidence/AUG-161/pin-red-prefix.log` (every invariant
  failing on the pristine base `3df75c2`, naming the scratch-cwd sites below).

## Section A — spawn sites (36 sites; one row per site)

Dispositions: **FIXED** (5), **already-absolute** (2), **unaffected** (29: cwd-repo-root
or inherited-cwd).

`subprocess.run(<argv VARIABLE>)` mediator sites are audited too: a variable built with
`sys.executable` and later spawned counts as a spawn site (found by a data-flow sweep,
`evidence/AUG-161/audit-scripts/find_mediators.py`).

| # | site | callable | cwd= | raw `sys.executable` in argv | disposition |
|---|------|----------|------|------------------------------|-------------|
| 1 | test_auger.py:2585 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root, the relative spelling resolves there |
| 2 | test_auger.py:2598-2601 | subprocess.run (sh -c) | `str(REPO_ROOT)` | yes (inside shell string) | unaffected — cwd is the repo root, the shell resolves the relative spelling there |
| 3 | test_auger.py:5977 | subprocess.run | none | yes | unaffected — cwd inherited from the repo root (no chdir in tests/), sys.executable resolves there |
| 4 | test_auger.py:5987 | subprocess.run | none | yes | unaffected — cwd inherited from the repo root (no chdir in tests/) |
| 5 | test_auger.py:6085 | subprocess.run | `str(child)` = tmp_path/childtests | yes | **FIXED** — absolute-resolved via `os.path.abspath(sys.executable)` |
| 6 | test_auger.py:11085 | subprocess.Popen | `REPO_ROOT` | yes | unaffected — cwd is the repo root |
| 7 | test_auger.py:11146 | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 8 | test_auger.py:11292 | subprocess.Popen | none | yes | unaffected — cwd inherited from the repo root |
| 9 | test_auger.py:11310 | subprocess.Popen | none | yes | unaffected — cwd inherited from the repo root |
| 10 | test_auger.py:11361 | subprocess.Popen | none | yes | unaffected — cwd inherited from the repo root |
| 11 | test_auger.py:11393 | subprocess.Popen | none | yes | unaffected — cwd inherited from the repo root |
| 12 | test_auger.py:11405 | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 13 | test_board_evidence_audit.py:46 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 14 | test_packaging_wheel.py:140 (via `_run`) | subprocess.run | none | yes | unaffected — helper passes no cwd, so the child inherits the repo-root cwd |
| 15 | test_packaging_wheel.py:263 (via `_run`) | subprocess.run | none | yes | unaffected — helper passes no cwd, so the child inherits the repo-root cwd |
| 16 | test_prd_proofs.py:260-262 (via `_check_with`) | subprocess.run | `REPO_ROOT` (inside helper) | passed as parameter | unaffected — helper cwd is the repo root |
| 17 | test_prd_proofs.py:306 | subprocess.run | `REPO_ROOT` | yes | unaffected — cwd is the repo root |
| 18 | test_prd_proofs.py:420 | subprocess.run | `REPO_ROOT` | yes | unaffected — cwd is the repo root |
| 19 | test_prd_proofs.py:449 | subprocess.run | `REPO_ROOT` | yes | unaffected — cwd is the repo root |
| 20 | test_prd_proofs.py:543 | subprocess.run | `REPO_ROOT` | yes | unaffected — cwd is the repo root |
| 21 | test_spec_alignment_check.py:176-178 (in `run_cli`, argv variable → spawn at :191) | subprocess.run | `str(repo)` = scratch consumer repo | yes | **FIXED** — absolute-resolved (this one helper drove 20 rel failures) |
| 22 | test_spec_alignment_check.py:313 | subprocess.run | `str(repo)` = scratch consumer repo | yes | **FIXED** — absolute-resolved |
| 23 | test_spec_alignment_check.py:574 (command STRING → gitreins `run_checker`, executed from the scratch repo cwd) | run_checker (external executor) | scratch repo (executor's cwd) | yes (f-string) | **FIXED** — absolute-resolved via the `checker_command()` helper + unit pin |
| 24 | test_spec_alignment_check.py:646 | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 25 | test_spec_obligation_gate.py:62 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 26 | test_spec_repository_root.py:71 (in `run_auger`) | subprocess.run | caller-supplied scratch consumer repo | no — `os.path.abspath(sys.executable)` | already-absolute (the previously fixed instance; AUG-127) |
| 27 | test_spec_repository_root.py:170 (runner-vector comparison) | (feeds `run_auger` spawn, #26) | — | no — `os.path.abspath(sys.executable)` | already-absolute |
| 28 | test_spec_rollback.py:62 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 29 | test_spec_validate_obligations.py:42 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 30 | test_spec_validators_agree.py:82 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 31 | test_task_packet.py:705-706 (via `harness` variable) | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 32 | test_task_packet.py:747-756 (via `harness` variable) | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 33 | test_trace_artifact.py:76 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |
| 34 | test_trace_artifact.py:459 | subprocess.run | `str(tmp_path)` | yes | **FIXED** — absolute-resolved |
| 35 | test_verification_graph.py:115 (via `harness` variable) | subprocess.run | none | yes | unaffected — cwd inherited from the repo root |
| 36 | test_xml_specs.py:896 | subprocess.run | `str(REPO_ROOT)` | yes | unaffected — cwd is the repo root |

Line numbers are the base-`3df75c2` positions of the `sys.executable` occurrence inside
the spawn's argv; the three fixes add 4-5 lines above some later sites, so post-fix line
numbers in those three files shift slightly (the pin test re-derives every site from the
current tree, so the invariant cannot drift).

## Section B — non-spawn occurrences (13; no child process, disposition n/a)

| occurrence | use |
|------------|-----|
| test_packaging_wheel.py:103 | `pytest.fail` message text ("not importable by {sys.executable}") |
| test_prd_proofs.py:191 | provenance set membership (`interpreters = {sys.executable, ...}`) |
| test_prd_proofs.py:192 | `str(Path(sys.executable).resolve())` — provenance set |
| test_prd_proofs.py:222 | `runner._recorded_runner((sys.executable, ...))` — normalization unit pin |
| test_prd_proofs.py:253 | docstring text |
| test_prd_proofs.py:260 | provenance set element (`sys.executable,` inside `interpreters = {...}`) |
| test_prd_proofs.py:268 | `Path(sys.executable).with_name("python")` — second-interpreter candidate |
| test_prd_proofs.py:289 | assertion message f-string |
| test_spec_repository_root.py:68 | comment |
| test_spec_repository_root.py:168 | comment |
| test_trace_artifact.py:310 | `assert sys.executable not in text` — provenance assertion |
| test_xml_specs.py:610 | runner vector in an in-process obligation fixture/assertion |
| test_xml_specs.py:685 | runner-vector equality assertion |

## Fix summary

Five sites fixed (four argv/argv-variable spawns + one command-string executor), all with
the in-repo pattern from `tests/test_spec_repository_root.py`
(`interpreter = os.path.abspath(sys.executable)` + comment):

- `tests/test_auger.py` — the child-pytest isolation spawn (`cwd=str(child)`).
- `tests/test_spec_alignment_check.py` — three shapes: the `run_cli` helper (argv
  variable → spawn with `cwd=str(repo)`; drove 20 rel failures through every test that
  calls it), the five-verb `--diff-stdin` loop (`cwd=str(repo)`, a scratch consumer
  repo), and the command STRING handed to gitreins'
  `engine.spec_alignment.run_checker` (executed from the scratch repo cwd; the rel-PATH
  handshake failed `[Errno 2] No such file or directory: '.venv/bin/python3'`).
- `tests/test_trace_artifact.py` — the `spec align --diff-file` call
  (`cwd=str(tmp_path)`).

No product code (`auger.py`) changed: every broken site was a test-layer spawn.

## Paired counts (relative vs absolute PATH), before / after

See `evidence/AUG-161/summary-paired.md` for the full per-file table and the transcripts
under `evidence/AUG-161/prefix/` and `evidence/AUG-161/postfix/`.

## Deliberately NOT changed

- The 29 unaffected spawn sites: each is proven safe by the audit (repo-root cwd or
  inherited cwd; no `os.chdir` anywhere in tests/). Converting them to abspath would be
  churn with no behavioural delta under either PATH spelling.
- The `cwd=REPO_ROOT` sites keep raw `sys.executable` by the same proof.
- `auger.py` (product code): untouched — this row is about the test layer.
