"""Extract the ci.yml boardctl install step's run body VERBATIM and save it as a script.

The simulation (evidence 03) then executes the shipped text, not a paraphrase.
"""

from pathlib import Path

import yaml

REPO = Path("/home/kara/worktrees/auger-AUG-174")
data = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
steps = data["jobs"]["gate"]["steps"]
step = next(
    s
    for s in steps
    if "github.com/coding-hermes/boardctl/releases/download" in (s.get("run") or "")
)
out = Path("/home/kara/.hermes/cache/scratch/aug174-install-step/run.sh")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(step["run"])
print("step name:", step["name"])
print("wrote", out, len(step["run"]), "chars")
