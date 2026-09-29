# Dogfood: Fresh Install on Ephemeral Bunker (2026-09-29)

## Surface Tested

The **install path** — the untouched surface. All prior runs (09-22 through 09-26) installed from the source checkout or re-proven the same path. This run tests what a real fresh user experiences when they follow the README Quick start on a bare machine.

## Environment

- **Host**: bunker-las-03 (ephemeral agent 7c6ccb7b, TTL 2h, destroyed after test)
- **OS**: Debian 12 (bare user, no sudo, no project toolchains preinstalled)
- **Python**: 3.13.5
- **Node**: 22.23.2
- **npm**: 10.9.8
- **Transfer method**: tar-over-ssh (git clone failed — repo is private, no credential in agent)

## Steps Executed (Documented Path)

1. `npm config set prefix ~/.npm-global` → rc=0
2. `npm i -g pnpm` → rc=0 (pnpm 12.6.0)
3. `git clone https://github.com/wojons/duckbrain ~/duckbrain` → **FAILED** (private repo, no credential)
   - Workaround: tar-over-ssh transfer of local checkout (262 files, ~30s)
4. `cd ~/duckbrain && pnpm install` → rc=0 (32.2s)
5. `pnpm build` → rc=0 (2.67s, vite build)
6. `mkdir -p ~/dd-data` → rc=0
7. `DUCKBRAIN_DATA_DIR=~/dd-data nohup node bin/duckbrain.js http > ~/duckbrain.log 2>&1 &` → pid=423789
8. `curl -s http://localhost:3000/health` → **degraded status** (no embedding providers configured)
9. `cd ~/auger && python3 auger.py -n testproj init` → **FAILED rc=1** "no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token"
10. `python3 auger.py -n testproj start ...` → **FAILED rc=1** (same error)
11. `python3 auger.py -n testproj ask` → **FAILED rc=1** (same error)

## Total Install Time

**77 seconds** from first npm command to final auger failure.

## Findings

### AUG-091 (P0): README omits DUCKBRAIN_API_KEY requirement

**What happened**: The README Quick start shows auger commands (init, start, ask, answer, status, dump) but never mentions that auger requires a DuckBrain API token. A user following the documented steps gets stuck at step 9 with the error:

```
no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token
```

**Why it matters**: This is a **complete blocker** for fresh-user onboarding. The substrate builds and runs (degraded, but functional), but the CLI itself cannot execute a single command without a token. The README gives no guidance on:
- Where to get the token
- How to configure it (env var vs file)
- Whether it's optional or required
- What happens if you skip it

**Evidence**: Verbatim error from step 9:
```
$ python3 auger.py -n testproj init
no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token
```

**Fix direction**: Add a "Prerequisites" or "Configuration" section to the README before the Quick start, explaining:
1. DuckBrain API token is required
2. Two ways to provide it: `export DUCKBRAIN_API_KEY=<token>` OR write it to `~/.duckbrain/foreman-status.token`
3. Where to get a token (link to DuckBrain docs or setup instructions)
4. Example: `echo "your-token-here" > ~/.duckbrain/foreman-status.token`

### Secondary: DuckBrain health is "degraded" out of the box

The substrate starts, but `/health` reports:
```json
{
  "status": "degraded",
  "embedding": {
    "provider": "",
    "model": "text-embedding-qwen3-embedding-0.6b",
    "healthy": false,
    "providers": [
      {"id": "lmstudio", "healthy": false, "note": "unreachable"},
      {"id": "ollama", "healthy": false, "note": "unreachable"},
      {"id": "openai", "healthy": false, "note": "missing API key (DUCKBRAIN_EMBEDDING_API_KEY)"}
    ]
  }
}
```

This is expected (no embedding provider configured), but the README doesn't warn users that auger's semantic search features (recall, check) will fail or degrade without embedding setup. Not a blocker for init/start/ask/answer, but a gap in the docs.

### Tertiary: Private repo blocks git clone

The auger repo is private, so `git clone` on a fresh machine fails with "could not read Username". This is not a bug (the repo is intentionally private), but it means the documented install path (`git clone https://github.com/wojons/auger`) doesn't work for external users. If auger is intended for internal use only, that's fine — but the README should say so, or provide an alternative (download tarball, use SSH clone with credential, etc.).

## Verdict

🟡 **PROMISING-BUT-ROUGH** — the substrate builds and runs, but the CLI is completely blocked by an undocumented prerequisite. A fresh user following the README gets 77 seconds in and hits a wall with no guidance.

**Time-to-first-success**: Never reached. The first auger command (init) fails immediately.

**Friction count**: 1 critical blocker (AUG-091), 2 secondary gaps (embedding docs, private repo).

**What works**:
- Substrate build (pnpm install + build) — 35s, clean
- DuckBrain daemon startup — 3s, responds to /health
- File transfer (tar-over-ssh workaround) — 30s, all 262 files

**What doesn't work**:
- `auger init` — fails immediately (no token)
- `auger start` — fails immediately (no token)
- `auger ask` — fails immediately (no token)
- Semantic search features (recall, check) — would fail even with token (no embedding provider)

## What I Left Behind

- **Board row**: AUG-091 (P0, filed in tasks.jsonl)
- **Dogfood log entry**: 2026-09-29 in .coding-hermes/dogfood-log.md
- **Integration doc**: This file (docs/dogfood/2026-09-29-fresh-install-integration.md)
- **Ephemeral agent**: Destroyed (7c6ccb7b, TTL backstop)

## Reproduction

To reproduce this finding:

```bash
# On a fresh Debian 12 machine (or bunker agent)
npm config set prefix ~/.npm-global
export PATH=~/.npm-global/bin:$PATH
npm i -g pnpm

# Transfer auger + duckbrain source (or clone if you have access)
# ...

cd ~/duckbrain
pnpm install && pnpm build
mkdir -p ~/dd-data
DUCKBRAIN_DATA_DIR=~/dd-data node bin/duckbrain.js http &

cd ~/auger
python3 auger.py -n testproj init
# Expected: "no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token"
```

## Next Steps

1. **Fix AUG-091**: Add DUCKBRAIN_API_KEY documentation to README
2. **Consider AUG-092**: Document embedding provider setup (or mark semantic search as optional)
3. **Consider AUG-093**: Clarify repo access (private vs public, how to clone)

The core loop (init → start → ask → answer → dump) is proven working on the control host with proper credentials. The substrate is solid. The gap is purely documentation — a 5-minute fix that unblocks every fresh user.