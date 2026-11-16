AUG-174 — CI cannot run the boardctl-backed proofs (public-release install path)

WHAT CHANGED
  1. .github/workflows/ci.yml — new step "install boardctl (pinned release,
     checksum-verified)" between "install ruff and pytest" and
     "run the gate (tests/gate.sh)". It resolves the release asset from
     `uname -m` (x86_64 -> boardctl_linux_amd64, aarch64 -> boardctl_linux_arm64,
     loud `exit 1` for anything else), downloads the PINNED tag's asset plus its
     SHA256SUMS from https://github.com/coding-hermes/boardctl/releases/download/v0.5.0/,
     verifies with `sha256sum -c` under `set -euo pipefail` (fail closed), installs the
     binary under the EXACT name `boardctl` into
     "${BOARDCTL_INSTALL_DIR:-${HOME}/.local/bin}" (on the GitHub runner's PATH), and
     prints `boardctl version` so the job log names the tool it used. Public repo: no
     token, no credential.
  2. tests/test_ci_boardctl_install.py (new) — 8 tests: the real workflow satisfies
     every assertion; five NON-VACUITY legs each mutate a parsed copy of the real
     ci.yml in tmp_path (step moved after the gate / version -> latest / rename
     dropped / checksum line dropped / arch guard dropped) and require the
     corresponding finding code; two legs prove the fail-loud behaviour is UNCHANGED
     (boardctl_or_skip() raises pytest.fail with the original message when BOARDCTL is
     None, and its source contains no pytest.skip fallback). One commit message
     correction from the first draft: pytest.fail.Exception inherits from
     BaseException, not Exception, so the raises clause must name it explicitly —
     pytest.raises(Exception) does NOT catch it.
  3. tests/gate.sh line 218 — tests/test_ci_boardctl_install.py APPENDED to the pytest
     arm's file list (append only; no reorder, drop, or timeout change).

WHY RESOLUTION A AND NOT B
  The boardctl repo is PUBLIC with published release assets (v0.5.0 verified by the
  foreman and re-verified here by the sha256 -c run below), so no credentialed fetch
  and no owner gate is needed. Resolution B would add a required-skip arm and let CI
  print named-skip coverage for three proofs that run fine once the binary exists —
  strictly worse than installing the binary. The row explicitly forbids B here.

EVIDENCE (this directory)
  01-red-no-boardctl.txt   PATH scrubbed: 3 failed / 8 passed; failing node ids are
                           exactly test_an_approved_plan_digest_exports_one_board_row,
                           test_an_unapproved_or_stale_digest_leaves_the_board_untouched,
                           test_the_exporter_only_invokes_boardctl_create_and_readback —
                           matches the foreman's premise at 0218a6a.
  02-green-release-binary.txt  scratch PATH carrying the pinned v0.5.0 release binary
                           installed as `boardctl`: 11 passed, rc 0 (AC2 non-vacuity).
  03-install-step-simulated.txt  the ci.yml step's OWN run body, extracted verbatim
                           from the workflow by extract_install_step.py (yaml parse ->
                           step["run"] -> file) and executed with bash:
                           sha256sum -c prints "boardctl_linux_amd64: OK", installs as
                           `boardctl`, `boardctl version` -> v0.5.0, rc 0. The only
                           environment difference from CI is BOARDCTL_INSTALL_DIR
                           pointed at the scratch bin (the sandbox has no /usr/local
                           write and this row may not touch anything outside the
                           worktree); in CI the default ${HOME}/.local/bin is used and
                           is on the runner's PATH.
  04-ci-yml.diff           the exact workflow diff.
  05-gate-arm.txt          grep proving tests/test_ci_boardctl_install.py is on the
                           pytest-arm line in tests/gate.sh.
  06-new-test.txt          pytest tests/test_ci_boardctl_install.py -v: 8 passed,
                           including the five mutation legs (each mutated copy fails
                           its assertion — non-vacuity for every checker rule).

FOR THE FOREMAN
  - SIBLING WRITER LANDED ON TOP OF ME: while the guarded commit's gate was running
    (guard log 20261116T032618, tests arm 777 passed / 2 skipped), an unknown sibling
    writer first added, then COMMITTED (2fe06e7, 22:32:12 -0500) two edits to THIS
    worktree: a GITHUB_PATH export block in the install step (guarded for non-GitHub
    hosts so the same body runs locally) and a matching 9th test (path-not-exported
    finding + a mutation leg). They are correct and within this row's file scope, so
    they stand as committed. Verified on the post-2fe06e7 tree: pytest
    tests/test_ci_boardctl_install.py 9 passed; spec align fail:0; the install-step
    simulation re-run green (03-install-step-simulated.txt, 2061-char run body).
    2fe06e7's own guard failed DEGRADED — its tests arm exited 2 for a missing
    DUCKBRAIN_URL, the known commit-shell prerequisite, not a tree red. An amend of
    8acf84d was attempted and was BLOCKED by its own guard before landing (same
    missing-env cause); it was abandoned so the sibling's commit was never rewritten.
  - The full gate was NOT re-run on the 2fe06e7 tree locally (per the brief the
    foreman runs the full gate on the merged tree); the test file is hermetic
    (<1s: yaml parse + regex + one import of test_xml_task_contract.py, no network,
    no substrate) and adds no dependency CI lacks — pyyaml is already installed by the
    "install ruff and pytest" step this step sits behind.
  - The install destination is parameterized ONLY as an env override
    (BOARDCTL_INSTALL_DIR) for the local simulation; CI uses the default
    ${HOME}/.local/bin, which is on the ubuntu-latest runner PATH.
  - Pinned URL: https://github.com/coding-hermes/boardctl/releases/download/v0.5.0/
    (asset boardctl_linux_amd64, sha256
    7c81b528f8f04c3375823e250fb433ef717bda95839c03f7012c8142326e9418 — matches the
    foreman's SHA256SUMS verification).
