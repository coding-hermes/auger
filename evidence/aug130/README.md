# AUG-130 evidence — a skipped required arm must fail the verdict

These three transcripts are the proof for `tests/gate.sh`'s skipped-arm policy. They were
captured on 2026-11-05 against base commit `fcb68b0`, by running the gate under a PATH shim
whose `python3` answers `-c 'import pytest'` with failure and delegates every other call to
the real interpreter (`run-gate-shim.sh` in this directory reproduces all three).

That shim is not a contrivance: it is the measured host condition. The `python3` on this
box's PATH is a Hermes venv interpreter with no pytest, so the gate skipped its entire
suite and still printed `GATE PASS`.

| # | transcript | tree | gate rc | verdict line |
|---|---|---|---|---|
| 1 | `01-unpatched-pass.txt` | a clean checkout of `fcb68b0` (pre-fix gate) | **0** | `GATE PASS` with `SKIPPED [pytest]` |
| 2 | `02-patched-fail.txt` | the fix (this branch) | **1** | `GATE FAIL: required arms did not run: pytest` |
| 3 | `03-patched-degraded.txt` | the fix + `AUGER_ALLOW_SKIPPED_ARMS=1` | **0** | `GATE PASS (DEGRADED)` naming the arm that did not run |

Direction 1 is the defect: an arm that did not run was scored as a pass. Direction 2 is the
fix: the default verdict fails and names the arm. Direction 3 is the one loud escape: it
still exits 0, but only by printing `GATE PASS (DEGRADED)` plus `required arms NOT run:
pytest` plus `NOT proof the suite ran`.

## Reproduce

```bash
# 1) pre-fix semantics (the defect): any checkout of fcb68b0
git archive fcb68b0 | tar -x -C /tmp/aug130-unpatched
bash evidence/aug130/run-gate-shim.sh /tmp/aug130 /tmp/aug130-unpatched /tmp/aug130/01.txt
grep -E 'SKIPPED \[pytest\]|GATE PASS|gate rc=' /tmp/aug130/01.txt

# 2) patched semantics: this branch, default policy
bash evidence/aug130/run-gate-shim.sh /tmp/aug130 "$PWD" /tmp/aug130/02.txt
grep -E 'GATE FAIL|gate rc=' /tmp/aug130/02.txt

# 3) patched semantics: the explicit escape
bash evidence/aug130/run-gate-shim.sh /tmp/aug130 "$PWD" /tmp/aug130/03.txt AUGER_ALLOW_SKIPPED_ARMS=1
grep -E 'GATE PASS \(DEGRADED\)|required arms NOT run|gate rc=' /tmp/aug130/03.txt
```

The driver exports `AUGER_CI=1 GITHUB_ACTIONS=true GATE_FULL=1` so the run is the FULL gate
with the live DuckBrain/JEV arms declared-skipped (they need a substrate this proof does not
depend on), which isolates the pytest arm as the only variable. The shim embeds the absolute
real interpreter path instead of reading a delegate from the environment: the gate's own
wrapper-regression test builds a second shim and resolves its delegate with
`command -v python3`, which is *this* shim when the gate runs under it — an env-var delegate
made the two shims exec each other in a loop (measured: a 3m33s CPU-bound hang that never
reached a verdict). `tests/test_gate_mode.sh` now strips its own shim dir from PATH before
resolving the delegate and refuses to run if it still lands on the shim.

The in-tree lock-in for this policy is the `Case AUG-130` block at the end of
`tests/test_gate_mode.sh`: it drives the gate as a subprocess (`AUGER_GATE_INNER=1` seam), and
asserts the three directions above with `env -u AUGER_ALLOW_SKIPPED_ARMS` so an outer
escape cannot make its "strict run must fail" assertion vacuous. `tests/gate.sh` runs that
file in its `wrapper regression (AUG-081)` arm, so the policy is enforced on every commit.
