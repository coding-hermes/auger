# Dogfood run 16 — the released artifact as a consumer surface (2026-10-15)

Lane: auger-dogfood (df16). Environment: fresh ephemeral bunker agent `df16-auger` on
bunker-mvp (bunker-las-03/04 were down; mvp is the fleet's other sanctioned leg host).
Fresh Debian/Ubuntu user: python 3.12.3, git, pip — **no node, no pnpm, no sudo, no uv**.

## The promise under test

"An external consumer who installs auger from the **v0.2.0 release** (the thing the
README's status line advertises) can follow the README and reach the drilling loop."
Every previous dogfood run (1–15) tested HEAD. This run is the first that treats the
tagged artifact as the product.

## What the release actually ships (release delta, proven on the tag)

- `git show v0.2.0:pyproject.toml` → path not in tag. **The released tag cannot be
  pip-installed** — packaging (QA-AUGER-11, commit 22698ee) landed after the cut.
  A release consumer gets `auger.py` from `git checkout v0.2.0` and runs it directly.
  The pip path works only at HEAD (venv install rc=0, `auger v0.2` console script,
  module import OK — leg7 evidence).
- The tag still contains the P0 fresh-user token gap? No — the AUG-091 README auth
  section IS in the tag (fixed pre-release). But it is **half a fix** (AUG-103 below).
- HEAD carries 115 commits / 11,657 insertions since the tag (incl. AUG-092/094/095/
  100/101 fixes and the AUG-074 fail-closed init). The release predates all of them.

## The install path, timed (fresh box, userspace only)

| step | time | result |
|---|---|---|
| clone duckbrain + auger | 7s | OK |
| node v22.22.2 userspace tarball (README minimum) | 3s | OK |
| `npm i -g pnpm` (prefix ~/.npm-global) | 3s | OK |
| `pnpm install && pnpm build` | 7.6s + 4.8s | OK |
| scratch daemon boot (isolated data+config+auth+namespaces) | 4s | boots, but 401 on everything |
| **auth rescue (duckbrain token mint + restart + capture)** | **~8s** | loop unblocked |
| full loop init→start→answer→status→dump→check on the tag | ~6s | all rc=0, 14/14 tables |

Total ~45s for a knowing user; **unbounded for a README-only user** blocked at the auth
wall (AUG-103). DuckBrain build is fast and its README is good; the failing joint is the
auger↔duckbrain pairing docs.

## The loop on v0.2.0 — works

With the pairing repaired: init declares 14/14 tables, start embeds (keyword-tier,
embedding.healthy=false honestly labeled), answer records D-001 with confidence 0.82,
status prints the honest no-grid coverage caveat, dump renders the active configuration
with [x]/[ ] checkboxes, check honestly reports "no stored evidence matched".
All rc=0. **The released loop is SHIPPABLE once paired correctly.**

## Failure ladder on the tag (honest exit codes, no pipes)

| leg | v0.2.0 | HEAD 9c097c8 |
|---|---|---|
| init with mispaired namespaces path (0/14) | **rc=0 fail-open** (warning only) — AUG-105 | rc=1 fail-closed (auger.py:4110-4120) |
| start on a missing namespace | rc=1, honest message | same |
| status / dump with substrate dead | rc=1, transport error named | same (AUG-101 fix) |
| answer mismatched --chosen | (covered by run 5 AUG-055; not re-driven) | fixed |

## Perf (Step 2b)

Warm on the fresh agent, 3 runs each: status 0.35–0.39s, dump 0.27–0.30s
(/usr/bin/time -f). Nothing a user feels — **no PERF row filed**; cold-start cost is
dominated by the one-time substrate build (~15s) and node download (~3s), both one-time.

## Findings filed

- **AUG-102** (P2) released tag predates pyproject.toml — release cannot pip-install;
  closes via RELEASE-013.
- **AUG-103** (P1) README auth step is unsatisfiable: never says the key must be minted
  via `duckbrain token` (and the mint prints the key once; the store holds only a hash).
- **AUG-104** (P2) README's own config example writes a literal-`~` namespacesPath the
  substrate does not expand (census ENOENT; drifts the AUG-074-class path contract).
- **AUG-105** (P2) v0.2.0 init fail-open on 0/14 — the AUG-074 fix is HEAD-only; release
  sweep must verify the fix is IN the cut.
- **AUG-106** (P3) README pip path ignores PEP 668 (venv works first try; one line missing).

## Install-leg record

install_seconds≈45 (rescue included) · bunker=bunker-mvp · agent=df16-auger (TTL 3h,
destroyed after run) · smoke=pass (full loop rc=0 on the tag) · substitutes used:
bunker-las-03/04 were unreachable; mvp substitution is the documented sibling-host path.

## Verdict

🟡 PROMISING-BUT-ROUGH — but for the release, not the code: HEAD is a full release
candidate ahead of its tag. The released loop works; the consumer path (pip, auth
bootstrap, 0/14 honesty) is where a fresh user breaks.
