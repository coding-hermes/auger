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
