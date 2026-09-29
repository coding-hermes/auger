#!/usr/bin/env bash
# docs_check.sh — README-003: execute the README's claims against the code instead of
# reading them. Three arms:
#
#   A) init-tally  — the README's "one command checks this section against the code"
#      procedure (README "The tables" section) runs `init` and prints
#      "declared: N/N tables -> ...". We do NOT run init here: it needs a live DuckBrain,
#      and tests/smoke.sh already exercises a real init against a throwaway namespace.
#      Instead we diff the README's own recorded tally (the count word, the N/N sample
#      line, and the bare table-name block) against COLS in auger.py — the section's
#      stated source of truth. Same drift, no live service.
#   B) loop verbs  — every verb in the README "The loop" fenced list must be a real
#      `python3 auger.py --help` subparser choice (fail on a renamed/removed verb), and
#      every shipped verb must be documented in the README's own registry-of-record,
#      docs/VERBS.md (same set, same order, same count — VERBS.md claims all three),
#      or in the README loop itself. The loop list is a quickstart narrative, not the
#      full registry: `bundle` and `export` intentionally live only in VERBS.md today.
#   C) env/paths   — every path/env var the README names as something auger reads must
#      have a real reference in auger.py. Fail loudly naming the missing one.
#
# Quiet on success (one PASS line), loud on drift (which arm, which item).
# AUGER_README=<path> checks a copy of the README instead of the tree's (red-proof a
# mutation without touching the working tree).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
README_FILE="${AUGER_README:-README.md}"

[ -f "$README_FILE" ] || { echo "FAIL [readme]: README not found at $README_FILE" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL [prereq]: python3 not on PATH" >&2; exit 2; }

DRIFT=0
fail() { echo "FAIL [$1]: $2" >&2; DRIFT=1; }

# --------------------------------------------------- arms A + B (python, no deps)
SUMMARY=""
PYSTATUS=0
SUMMARY="$(python3 - "$README_FILE" <<'PY'
import ast, re, subprocess, sys

readme_path = sys.argv[1]
readme = open(readme_path, encoding="utf-8").read()
src = open("auger.py", encoding="utf-8").read()

def fail(arm, msg):
    global drift
    drift = True
    print(f"FAIL [{arm}]: {msg}", file=sys.stderr)

drift = False

fences = re.findall(r"```[^\n]*\n(.*?)```", readme, re.S)

# --- COLS: the tally section's stated source of truth (auger.py) ------------------
cols = None
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Assign) and any(
        getattr(t, "id", None) == "COLS" for t in node.targets
    ):
        cols = [k.value for k in node.value.keys]
        break
if not cols or len(cols) < 5:
    fail("tally", "could not parse COLS out of auger.py — check script is stale")
    sys.exit(1)

# --- Arm A: the init tally ---------------------------------------------------------
tally_fences = [f for f in fences if "declared:" in f and "tables" in f]
if len(tally_fences) != 1:
    fail("tally", f"expected exactly 1 fenced init-tally sample in the README, found {len(tally_fences)}")
else:
    joined = " ".join(re.sub(r"^\s*#\s?", "", ln) for ln in tally_fences[0].splitlines())
    m = re.search(r"declared:\s*(\d+)\s*/\s*(\d+)\s*tables\s*->\s*(.+)", joined)
    if not m:
        fail("tally", "init-tally sample no longer matches 'declared: N/N tables -> names'")
    else:
        got_n, got_d, nameblob = int(m.group(1)), int(m.group(2)), m.group(3)
        names = re.findall(r"[a-z_][a-z_0-9]*", nameblob)
        if got_n != len(cols) or got_d != len(cols):
            fail("tally", f"README tally says {got_n}/{got_d} tables but COLS in auger.py has {len(cols)}")
        if set(names) != set(cols) or len(names) != len(cols):
            only_r = sorted(set(names) - set(cols))
            only_c = sorted(set(cols) - set(names))
            fail("tally", f"table name drift: README-only={only_r or 'none'} COLS-only={only_c or 'none'}")

WORDS = dict(zip(range(1, 21),
    "one two three four five six seven eight nine ten "
    "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()))
word = WORDS.get(len(cols))
if word is None:
    fail("tally", f"count word for {len(cols)} not mapped in docs_check.sh — add it")
elif not re.search(rf"\b{word}\b", readme, re.I):
    fail("tally", f"README no longer says '{word}' ({len(cols)} tables) — prose count drifted")

name_blocks = [f for f in fences
               if "bundle_member" in f and "declared:" not in f
               and set(re.findall(r"[a-z_][a-z_0-9]*", f)) == set(cols)]
if len(name_blocks) < 1:
    fail("tally", "the bare table-name block in 'The tables' no longer matches COLS exactly")

# --- Arm B: the loop verb list vs the real subparsers -------------------------------
help_out = subprocess.run([sys.executable, "auger.py", "--help"],
                          capture_output=True, text=True)
if help_out.returncode != 0:
    fail("verbs", f"auger.py --help exited {help_out.returncode}")
    sys.exit(1)
m = re.search(r"\{([a-z_,]+)\}", help_out.stdout)
if not m:
    fail("verbs", "could not parse subparser choices from `auger.py --help` usage line")
    sys.exit(1)
choices = m.group(1).split(",")

loop_verbs = []
for f in fences:
    for ln in f.splitlines():
        if ln.startswith("auger "):
            loop_verbs.append(ln.split()[1])
if not loop_verbs:
    fail("verbs", "no 'auger <verb>' fenced block found in the README — loop section moved?")
for v in loop_verbs:
    if v not in choices:
        fail("verbs", f"README loop verb '{v}' is not an auger subparser (choices: {', '.join(choices)})")

try:
    verbs_md = open("docs/VERBS.md", encoding="utf-8").read()
except FileNotFoundError:
    fail("verbs", "docs/VERBS.md missing — the README points every verb at it")
else:
    reg = re.search(r"```[^\n]*\n(.*?)```", verbs_md, re.S)
    reg_verbs = reg.group(1).split() if reg else []
    mc = re.search(r"The (\d+) top-level verbs", verbs_md)
    if not mc:
        fail("verbs", "docs/VERBS.md lost its 'The N top-level verbs' count sentence")
    elif int(mc.group(1)) != len(choices):
        fail("verbs", f"docs/VERBS.md says {mc.group(1)} top-level verbs, --help ships {len(choices)}")
    if reg_verbs != choices:
        fail("verbs", f"docs/VERBS.md registry {reg_verbs} != --help order/set {choices} "
                      f"(VERBS.md promises the same verbs in the same order)")
    for c in choices:
        if c not in loop_verbs and c not in reg_verbs:
            fail("verbs", f"CLI verb '{c}' is in neither the README loop nor docs/VERBS.md")

print(f"loop verbs ok ({len(loop_verbs)} listed, {len(choices)} shipped), "
      f"VERBS.md registry ok, tally ok ({len(cols)} tables)")
sys.exit(1 if drift else 0)
PY
)" || PYSTATUS=$?
if [ "$PYSTATUS" -ne 0 ]; then
  DRIFT=1
else
  :  # summary captured above
fi

# ------------------------------------------------------------------- arm C (bash)
# README-mentioned things auger must actually read. (README paths/env that belong to
# the DuckBrain daemon, e.g. DUCKBRAIN_DATA_DIR, are deliberately not here — auger.py
# does not consume those and must not.)
ENVREFS=0
check_ref() { # $1 label  $2 README-fixed-string  $3 code-fixed-string
  local rd_line
  if ! grep -nFq -- "$2" "$README_FILE"; then
    fail "env-paths" "README no longer mentions $1 — update docs_check.sh"
    return
  fi
  if ! grep -nFq -- "$3" auger.py; then
    rd_line="$(grep -nFm1 -- "$2" "$README_FILE" | head -1 | cut -d: -f1)"
    fail "env-paths" "'$3' (README line $rd_line) has no reference in auger.py"
  fi
  ENVREFS=$((ENVREFS + 1))
}
check_ref "AUGER_DOMAIN_GRID"                 "AUGER_DOMAIN_GRID"     "AUGER_DOMAIN_GRID"
check_ref "DUCKBRAIN_API_KEY"                 "DUCKBRAIN_API_KEY"     "DUCKBRAIN_API_KEY"
check_ref "~/.duckbrain/foreman-status.token" "foreman-status.token"  "foreman-status.token"
check_ref "~/.duckbrain/token"                "~/.duckbrain/token"    "duckbrain/token"
check_ref "~/.hermes/.env (OpenRouter key)"   "~/.hermes/.env"        ".hermes/.env"

# -------------------------------------------------------------------------------
if [ "$DRIFT" -ne 0 ]; then
  echo "docs_check: DRIFT between README and code — see FAIL lines above" >&2
  exit 1
fi
echo "PASS: README claims match code — $SUMMARY, $ENVREFS env/path refs ok"
