#!/usr/bin/env bash
# Drive `bash tests/gate.sh` under a PATH shim whose `python3` cannot import pytest,
# while every other call goes to the real interpreter. This is the host condition that
# made the gate report PASS with its whole suite never run (AUG-130).
#
#   run-gate-shim.sh <scratch-dir> <tree> <transcript> [ENV=VAL ...]
#
# The shim embeds the ABSOLUTE real interpreter path at generation time. It deliberately
# does not read a delegate out of the environment: the gate's own wrapper-regression test
# builds a second shim and resolves its delegate with `command -v python3`, which is THIS
# shim when the gate runs under it — an env-var delegate would make the two shims exec each
# other in a loop (measured: a 3m33s CPU-bound hang that never reached the verdict).
#
# The gate's exit code is written into the transcript as `gate rc=<n>` so the evidence file
# carries the verdict, not just the log text.
set -uo pipefail
scratch="$1"
tree="$2"
out="$3"
shift 3
export PATH="$HOME/.local/bin:$PATH"
real_python="$(command -v python3)"
case "$real_python" in
  */shim-no-pytest/python3)
    printf 'refusing to generate a shim that delegates to a shim (%s)\n' "$real_python" >&2
    exit 9
    ;;
esac
shim="$scratch/shim-no-pytest"
mkdir -p "$shim"
cat >"$shim/python3" <<SHIM
#!/usr/bin/env bash
# A python3 that cannot import pytest — everything else is the real interpreter.
if [ "\${1:-}" = "-c" ] && [ "\${2:-}" = "import pytest" ]; then
  exit 1
fi
exec $real_python "\$@"
SHIM
chmod +x "$shim/python3"

# Negative control: if the shim still answered the probe, the run below would prove nothing.
if "$shim/python3" -c 'import pytest' 2>/dev/null; then
  printf 'CONTROL FAILED: the no-pytest shim still imports pytest\n' >&2
  exit 9
fi

cd "$tree" || exit 8
{
  printf '# tree: %s\n' "$tree"
  printf '# shim: %s/python3 (answers `-c "import pytest"` with failure, delegates everything else)\n' "$shim"
  printf '# shim delegates to: %s\n' "$real_python"
  printf '# control: %s python3 -c "import pytest" -> nonzero (shim does not import pytest)\n' "$shim"
  printf '# command: env -u AUGER_ALLOW_SKIPPED_ARMS AUGER_CI=1 GITHUB_ACTIONS=true GATE_FULL=1 %s bash tests/gate.sh\n' "$*"
  printf '# ---\n'
} >"$out"

env -u AUGER_ALLOW_SKIPPED_ARMS AUGER_CI=1 GITHUB_ACTIONS=true GATE_FULL=1 \
  PATH="$shim:$PATH" "$@" \
  bash tests/gate.sh >>"$out" 2>&1
rc=$?
printf '# ---\ngate rc=%s\n' "$rc" >>"$out"
exit 0
