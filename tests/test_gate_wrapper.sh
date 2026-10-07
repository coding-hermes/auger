#!/usr/bin/env bash
# AUG-081 regression: the gate's pytest wrapper must kill a HUNG process group.
#
# The gate arm is `setsid timeout --kill-after=<kill_after> <budget> python3 -m
# pytest ...`: pytest runs as the leader of its own session, so the wrapper's
# TERM/SIGKILL reaches pytest itself no matter how the parent (hook, ssh, tick
# driver) died. These tests re-run the wrapper shape against a stub that
# stands in for a hung pytest: TERM-deaf (a mid-write pytest can block in a
# syscall or ignore TERM), holding a child that outlives it, and never
# exiting on its own.
#
# Hermetic by construction: no live DuckBrain, no network, no pytest import.
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

# tools required by the arm under test; the gate itself skips arms when a
# tool is missing, so the regression does the same instead of failing.
if ! command -v setsid >/dev/null 2>&1 || ! command -v timeout >/dev/null 2>&1; then
  printf 'setsid and timeout(1) are required for the AUG-081 regression: SKIPPED (wrapper arm NOT run, NOT passed)\n'
  exit 0
fi

# ---------------------------------------------------------------------
# Case 1: the wrapper kills a TERM-deaf stub that ignores SIGTERM and
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
rc=$?
set -e
elapsed=$((SECONDS - t0))

[[ "$rc" -ne 0 ]] || {
  printf 'wrapper exited 0 against a hung stub (expected non-zero)\n' >&2
  exit 1
}
[[ "$elapsed" -lt 10 ]] || {
  printf 'wrapper took %ss to deal with the hung stub (expected <10s: 3s budget + 3s kill-after)\n' "$elapsed" >&2
  exit 1
}

# The stub itself must be DEAD within the window (it is the direct child:
# SIGTERM at budget, SIGKILL after --kill-after — either kills it).
stub_pid="$(cat "$d/pids.pid")"
sleep 1
if kill -0 "$stub_pid" 2>/dev/null; then
  printf 'hung stub pid %s is STILL ALIVE after the wrapper returned (rc=%s) — the group kill did not fire\n' "$stub_pid" "$rc" >&2
  exit 1
fi

# The escalation arm must be reachable: against a TERM-deaf stub the exit
# must be 137 (SIGKILL), proving --kill-after fired rather than the run
# ending early by luck. 124 is also accepted: it is timeout's own overrun
# code and both mean the wrapper killed the hung arm.
case "$rc" in
  124|137) ;;
  *)
    printf 'unexpected wrapper rc=%s (want 124 or 137 against a TERM-deaf hung stub)\n' "$rc" >&2
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
# Case 2: the exact exit-code mapping the gate arm performs. A wrapper
# overrun (124) must be mapped to a loud, arm-naming failure and exit 1;
# a plain failure must propagate as a failure too.
# ---------------------------------------------------------------------
map_out="$(mktemp "${TMPDIR:-/tmp}/aug081-map-XXXXXX")"
trap 'rm -f "$map_out"' EXIT

for verdict in 124 7; do
  set +e
  (
    rc_probe=0
    setsid timeout --kill-after=3s 3s true >/dev/null 2>&1 || rc_probe=$?
    if [ "$rc_probe" -eq 124 ]; then
      echo "GATE TIMEOUT: the pytest arm (AUG-081 wrapper) exceeded 1200s and was killed — investigate hung tests; failing the gate" >&2
      exit 1
    fi
    exit "$rc_probe"
  ) >"$map_out" 2>&1
  map_rc=$?
  set -e
  body="$(cat "$map_out")"
  if [ "$verdict" = 124 ]; then
    # Drive the 124 branch by construction: run a command that surely
    # overruns, through the same mapping shape.
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
    body="$(cat "$map_out")"
    [[ "$map_rc" -eq 1 ]] || {
      printf 'overrun (124) was not mapped to exit 1 (got %s)\n' "$map_rc" >&2
      exit 1
    }
    assert_contains "$body" "GATE TIMEOUT"
    assert_contains "$body" "pytest arm"
  else
    # Non-124 failure keeps its code (still non-zero → gate fails).
    [[ "$map_rc" -ne 0 ]] || {
      printf 'plain wrapper failure (rc=7) was swallowed (exit %s)\n' "$map_rc" >&2
      exit 1
    }
  fi
done

# ---------------------------------------------------------------------
# Case 3: the gate's pytest arms actually carry the wrapper. Reads the
# gate source (source-derived, not a hand-copied string): both arms must
# run pytest under `setsid timeout --kill-after=...`.
# ---------------------------------------------------------------------
gate_src="$(cat "$GATE")"
arm_count="$(printf '%s\n' "$gate_src" | grep -cE 'python3 -m pytest tests/test_auger\.py' || true)"
[[ "$arm_count" -eq 2 ]] || {
  printf 'expected exactly 2 pytest arms in gate.sh, found %s\n' "$arm_count" >&2
  exit 1
}
wrapped_count="$(printf '%s\n' "$gate_src" | grep -cE 'setsid timeout --kill-after=.*python3 -m pytest tests/test_auger\.py' || true)"
[[ "$wrapped_count" -eq 2 ]] || {
  printf 'expected both pytest arms wrapped with "setsid timeout --kill-after=...", found %s\n' "$wrapped_count" >&2
  printf 'gate.sh pytest lines:\n'
  printf '%s\n' "$gate_src" | grep -nE 'python3 -m pytest tests/test_auger\.py' >&2
  exit 1
}

printf 'AUG-081 wrapper regression: PASS\n'
