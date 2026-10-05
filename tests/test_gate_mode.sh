#!/usr/bin/env bash
# Hermetic regression coverage for tests/gate.sh mode selection.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$HERE/tests/gate.sh"

assert_contains() {
  local haystack="$1"
  local needle="$2"
  [[ "$haystack" == *"$needle"* ]] || {
    printf 'expected output to contain %q, got:\n%s\n' "$needle" "$haystack" >&2
    exit 1
  }
}

# A leaked AUGER_CI must fail before syntax/tests on a development host.
set +e
leaked_output="$(env -u GITHUB_ACTIONS -u CI AUGER_CI=1 GATE_MODE_ONLY=1 bash "$GATE" 2>&1)"
leaked_status=$?
set -e
[[ "$leaked_status" -ne 0 ]] || {
  printf 'leaked AUGER_CI unexpectedly selected hosted skip mode:\n%s\n' "$leaked_output" >&2
  exit 1
}
assert_contains "$leaked_output" "non-runner"
assert_contains "$leaked_output" "downgrade"
[[ "$leaked_output" != *"== syntax =="* ]] || {
  printf 'leaked AUGER_CI was not rejected before the gate arms:\n%s\n' "$leaked_output" >&2
  exit 1
}

# A genuine GitHub marker permits the hosted-CI skip mode without running the gate.
github_output="$(env -u CI AUGER_CI=1 GITHUB_ACTIONS=true GATE_MODE_ONLY=1 bash "$GATE" 2>&1)"
assert_contains "$github_output" "gate mode: hosted-ci-skip"
[[ "$github_output" != *"== syntax =="* ]] || {
  printf 'mode probe ran the full gate for GitHub Actions:\n%s\n' "$github_output" >&2
  exit 1
}

# The generic CI marker is also a valid runner marker.
ci_output="$(env -u GITHUB_ACTIONS AUGER_CI=1 CI=1 GATE_MODE_ONLY=1 bash "$GATE" 2>&1)"
assert_contains "$ci_output" "gate mode: hosted-ci-skip"

printf 'gate mode regression: PASS\n'

# =====================================================================
# AUG-081: the gate's pytest wrapper must kill a HUNG process group.
#
# The gate arm is `setsid timeout --kill-after=<kill_after> <budget>
# python3 -m pytest ...`: pytest runs as the leader of its own session,
# so the wrapper's TERM/SIGKILL reaches pytest itself no matter how the
# parent (hook, ssh drop, tick driver) died. These cases re-run the
# wrapper shape against a stub that stands in for a hung pytest:
# TERM-deaf (a mid-write pytest can block in a syscall or ignore TERM),
# holding a child that outlives it, and never exiting on its own.
#
# Hermetic by construction: no live DuckBrain, no network, no pytest
# import, so `AUGER_CI=1 bash tests/gate.sh` runs this file unchanged.
# =====================================================================

# tools required by the arm under test; the gate itself skips arms when a
# tool is missing, so the regression does the same instead of failing.
if ! command -v setsid >/dev/null 2>&1 || ! command -v timeout >/dev/null 2>&1; then
  printf 'setsid and timeout(1) are required for the AUG-081 wrapper regression: SKIPPED (arm NOT run, NOT passed)\n'
  exit 0
fi

# ---------------------------------------------------------------------
# Case W1: the wrapper kills a TERM-deaf stub that ignores SIGTERM and
# holds a child. SIGKILL escalation (--kill-after) must take the stub
# down within the kill-after window and the wrapper must exit non-zero.
# ---------------------------------------------------------------------
d="$(mktemp -d "${TMPDIR:-/tmp}/aug081-XXXXXX")"
stub_cleanup() { rm -rf "$d"; }
trap stub_cleanup EXIT

cat > "$d/stub.sh" <<'STUB'
# A hung pytest stand-in: TERM-deaf, holds a child, never exits on its own.
trap '' TERM
sleep 30 &
child=$!
printf '%s\n' "$child" > "$1.child"
printf '%s\n' "$$" > "$1.pid"
while kill -0 "$child" 2>/dev/null; do sleep 1; done
exec sleep 30
STUB

t0=$SECONDS
set +e
setsid timeout --kill-after=3s 3s bash "$d/stub.sh" "$d/pids"
w1_rc=$?
set -e
w1_elapsed=$((SECONDS - t0))

[[ "$w1_rc" -ne 0 ]] || {
  printf 'wrapper exited 0 against a hung stub (expected non-zero)\n' >&2
  exit 1
}
[[ "$w1_elapsed" -lt 10 ]] || {
  printf 'wrapper took %ss to deal with the hung stub (expected <10s: 3s budget + 3s kill-after)\n' "$w1_elapsed" >&2
  exit 1
}

# The stub itself must be DEAD within the window (it is the direct child:
# SIGTERM at budget, SIGKILL after --kill-after — either kills it).
stub_pid="$(cat "$d/pids.pid")"
sleep 1
if kill -0 "$stub_pid" 2>/dev/null; then
  printf 'hung stub pid %s is STILL ALIVE after the wrapper returned (rc=%s) — the kill did not fire\n' "$stub_pid" "$w1_rc" >&2
  exit 1
fi

# The escalation arm must be reachable: against a TERM-deaf stub the exit
# must be 137 (SIGKILL), proving --kill-after fired rather than the run
# ending early by luck. 124 is also accepted: it is timeout's own overrun
# code and both mean the wrapper killed the hung arm.
case "$w1_rc" in
  124|137) ;;
  *)
    printf 'unexpected wrapper rc=%s (want 124 or 137 against a TERM-deaf hung stub)\n' "$w1_rc" >&2
    exit 1
    ;;
esac

# The stub's child may outlive the SIGKILL'd leader on some util-linux
# builds (signals are delivered to the direct child, not every group
# member — measured 2026-10-04). Reap it so the temp dir is clean; this is
# the measured nuance, not a pass/fail condition of the wrapper contract.
if [ -f "$d/pids.child" ]; then
  child_pid="$(cat "$d/pids.child")"
  kill -9 "$child_pid" 2>/dev/null || true
fi

# ---------------------------------------------------------------------
# Case W2: the exact exit-code mapping the gate arm performs. A wrapper
# overrun (124) must map to a loud, arm-naming failure with exit 1; a
# plain failure must propagate as a failure too. The mapping runs in a
# SUBSHELL (gate.sh runs it at top level; `exit 1` inside a function
# would kill this whole test script, measured 2026-10-04).
# ---------------------------------------------------------------------
map_out="$(mktemp "${TMPDIR:-/tmp}/aug081-map-XXXXXX")"
map_cleanup() { rm -f "$map_out"; }
trap 'stub_cleanup; map_cleanup' EXIT

# The 124 branch, driven by construction: a command that surely overruns.
set +e
(
  rc_probe=0
  setsid timeout --kill-after=1s 1s sleep 5 >/dev/null 2>&1 || rc_probe=$?
  if [ "$rc_probe" -eq 124 ]; then
    echo "GATE TIMEOUT: the pytest arm (AUG-081 wrapper) exceeded 1200s and was killed — investigate hung tests; failing the gate" >&2
    exit 1
  fi
  exit "$rc_probe"
) >"$map_out" 2>&1
map_rc=$?
set -e
[[ "$map_rc" -eq 1 ]] || {
  printf 'overrun (124) was not mapped to exit 1 (got %s)\n' "$map_rc" >&2
  exit 1
}
assert_contains "$(cat "$map_out")" "GATE TIMEOUT"
assert_contains "$(cat "$map_out")" "pytest arm"

# A clean wrapper command propagates rc=0.
set +e
(
  rc_probe=0
  setsid timeout --kill-after=1s 1s true >/dev/null 2>&1 || rc_probe=$?
  if [ "$rc_probe" -eq 124 ]; then
    echo "GATE TIMEOUT: the pytest arm (AUG-081 wrapper) exceeded 1200s and was killed — investigate hung tests; failing the gate" >&2
    exit 1
  fi
  exit "$rc_probe"
) >/dev/null 2>&1
map_rc=$?
set -e
[[ "$map_rc" -eq 0 ]] || {
  printf 'a clean wrapper command did not propagate rc=0 (got %s)\n' "$map_rc" >&2
  exit 1
}

# A plain non-124 failure keeps its code (still non-zero → gate fails);
# the mapping is 124-only.
set +e
(
  rc_probe=0
  setsid timeout --kill-after=1s 1s sh -c 'exit 7' >/dev/null 2>&1 || rc_probe=$?
  if [ "$rc_probe" -eq 124 ]; then
    echo "GATE TIMEOUT: the pytest arm (AUG-081 wrapper) exceeded 1200s and was killed — investigate hung tests; failing the gate" >&2
    exit 1
  fi
  exit "$rc_probe"
) >/dev/null 2>&1
map_rc=$?
set -e
[[ "$map_rc" -eq 7 ]] || {
  printf 'plain wrapper failure (rc=7) was swallowed or mapped to exit 1 (got %s; mapping is 124-only)\n' "$map_rc" >&2
  exit 1
}

# ---------------------------------------------------------------------
# Case W3: the gate's pytest arms actually carry the wrapper. Reads the
# gate source (source-derived, not a hand-copied string): the single
# pytest invocation must live INSIDE the wrapped helper, and BOTH gate
# arms (the hosted-ci skip-summary line and the live line) must call that
# helper — never a bare pytest.
# ---------------------------------------------------------------------
gate_src="$(cat "$GATE")"
raw_count="$(printf '%s\n' "$gate_src" | grep -cE 'python3 -m pytest tests/test_auger\.py' || true)"
[[ "$raw_count" -eq 1 ]] || {
  printf 'expected exactly 1 raw pytest invocation in gate.sh (inside run_pytest_arm), found %s\n' "$raw_count" >&2
  exit 1
}
wrapped_count="$(printf '%s\n' "$gate_src" | grep -cE 'setsid timeout --kill-after=.*python3 -m pytest tests/test_auger\.py' || true)"
[[ "$wrapped_count" -eq 1 ]] || {
  printf 'the pytest invocation is not wrapped with "setsid timeout --kill-after=..." on one line\n' >&2
  printf 'gate.sh pytest lines:\n'
  printf '%s\n' "$gate_src" | grep -nE 'python3 -m pytest tests/test_auger\.py' >&2
  exit 1
}
call_count="$(printf '%s\n' "$gate_src" | grep -cE '(^|[[:space:]])run_pytest_arm -q' || true)"
[[ "$call_count" -eq 2 ]] || {
  printf 'expected exactly 2 run_pytest_arm call sites (both gate arms), found %s\n' "$call_count" >&2
  printf 'gate.sh arm lines:\n'
  printf '%s\n' "$gate_src" | grep -nE 'run_pytest_arm|python3 -m pytest' >&2
  exit 1
}

printf 'AUG-081 wrapper regression: PASS\n'
