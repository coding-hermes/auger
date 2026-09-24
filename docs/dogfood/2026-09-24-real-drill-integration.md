# auger dogfood integration report — 2026-09-24 (5th run: a REAL drill, judged as a product)

Four prior runs proved the machinery: the core loop (run 1), the graph + bundles +
verdict (run 2), the ask→answer seam and the registers (run 3), and the raw HTTP
data layer (run 4). Every one of them asked *does the verb work*. None asked the
question a user asks first: **does drilling a real decision set through auger
produce something worth reading?**

This run is that. One real, non-toy problem was drilled end to end and the output
was judged as a product: a decision the fleet actually had to make.

## The promise under test

> "A user can point auger at a real decision, let it ask what is missing, answer
> with the alternatives that lost, and get back a configuration (and a coverage
> map) that another person can read and act on."

## The run

Namespace `df5-real` on the control host, live DuckBrain (`feat/native-s3`) + live
JEV (`typesafe/jev-1.13-20260917`). The real problem:

> *Where should the fleet's heavy multiarch docker builds run?* The 2026-08-31
> rethinkdb image build pegged 4+ cores for hours under qemu-aarch64 emulation and
> starved every other lane; the fleet has a provisioned CI box (`bunker-las-03`)
> with qemu binfmt and a buildx container builder.

Real alternatives, real rejected reasons — not placeholders.

### What happened, in order

| step | command | result | cost |
|---|---|---|---|
| init | `init --seed-domains` | 14/14 tables declared, 44/44 domain rows, grid read from the skill dir | 0.4s |
| start | `start --name ... --seed ...` | project `P-20260924032050`, seed stored + embedded (431 ch) | 0.08s |
| ask #1 | `ask` | JEV picked `concurrency_and_scaling` (0.75) → stored `Q-000001  What test proves this works?` | 6.4s |
| answer | `answer --question-id Q-000001 --chosen "The CI box smoke test" --option "CI smoke battery: ..." --why-not ... --why-not ...` | `D-001 recorded … closed Q-000001` — **and the configuration came back EMPTY** | 0.1s |
| ask #2 | `ask` | re-proposed the **same question text** → `Q-000002  What test proves this works?` | 5.0s |
| answer | `answer --question-id Q-000002 …` | `D-002 recorded` — the duplicate the gate should have prevented | 0.1s |
| ask #3 | `ask` | `question not stored: JEV proposed none: nothing left worth asking` — self-heals after the waste | 5.0s |
| answer | the real placement decision, `--confidence 0.9 --reversal-cost 2` | `D-003 recorded` | 0.1s |
| dump | `dump` | `ACTIVE CONFIGURATION: (nothing active)` + a warning that D-001/D-002/D-003 each contribute nothing | 0.11s |
| toggle | `toggle --on D-003-O1` | `toggled: D-003-O1->on (1)` — repair works | 0.1s |
| dump --config | `dump --config D-003=O1` | hypothesis renders **and** names the contradiction against the record | 0.1s |
| verdict | `verdict --good "D-001=O1;D-002=O1;D-003=O1" --reasons ...` | `V-000001 recorded (verdict good, judged_by human)` | 0.1s |
| status | `status` | coverage map: 44 seeded, 2 answered, 42 NOT-REACHED | 0.20s |

### The user-visible outcome

The good news is real and worth stating plainly: **the product concept works.** A
blank namespace became a structured, readable record of a genuine decision in about
90 seconds of wall clock, and `dump` renders it as a document a colleague could
read — chosen option, the alternatives that lost, the reason each lost, the
confidence, the reversal cost. `status` then shows what is *still undecided* across
44 domains, which is the part that is hard to get any other way. `dump --config`
(what-if) is the standout: it rendered the hypothesis **and** flagged that the
hypothesis contradicts the recorded choice, quoting the on-file reason. That is a
genuinely useful behaviour and no test asserts it.

The bad news is that the loop's *last* step silently does not do the thing it
appears to do — see AUG-055 — and the gate that decides "is this already answered"
wasted a full round — see AUG-056.

## Cost of using it (measured)

`hyperfine --warmup 2 --runs 10` on the same 3-decision namespace:

| verb | mean ± σ | note |
|---|---|---|
| `status` | **199 ms ± 12 ms** | run 3 measured 2.2s ± 1.3s on a 4-decision namespace — see AUG-047 note |
| `dump` | **111 ms ± 9 ms** | |
| `recall` | **3.9 s** | run 3 measured cold 25s / warm 2s (AUG-032) |
| `ask` | 5.0–6.4 s | dominated by the JEV call; ~$0.00004 |

Nothing here is slow enough to be worth a new perf row: the local verbs are
sub-second, and `ask`'s 5–6s is a model call a user expects to wait for. The one
number worth carrying forward is that `status` now measures **~10x faster than run 3
reported** on a comparable namespace, which is evidence on AUG-047 rather than a
new row.

## Time to first success

~90 seconds from empty namespace to a rendered configuration, of which ~17s was
model calls. That is fast — faster than the prior runs' ~12 min, because the
substrate was already up and no write path refused.

## Fresh-machine install leg (ephemeral bunker, required)

Host `bunker-las-03` (100.69.3.13), agent `d1a530b6`, destroyed at the end.

| leg | measured | result |
|---|---|---|
| host reachable, `bunkerd` active, docker 26.1.5 | — | ok |
| substrate clone `feat/native-s3` (`680568f`) + user-prefix pnpm + `pnpm install && pnpm build` | **142 s** | ok |
| auger clone from the documented origin | ok | ok |
| **documented loop on the fresh box** (`init` → `start` → `answer` → `dump`) | **1 s** | **ok — `ACTIVE CONFIGURATION: D-001=bunker for heavy builds`** |

Two things the fresh box taught us that the docs do not say:

1. **The README's fixed port assumption is unsafe on a shared host.** The quickstart
   says "a running DuckBrain on `127.0.0.1:3000`". On the bunker, `:3000` was already
   held by *another user's* daemon (`kara`), and the box additionally had three
   orphaned daemons belonging to **already-destroyed** dogfood agents
   (`4b004b7a`, `864a37ab`) still bound to `:3100`/`:3210`/`:3311` and still answering.
   Following the docs verbatim, auger's first write returned
   `could not list namespaces (500): Cannot determine the duckbrain root … walking up
   from /home/bunker-864a37ab/duckbrain/…` — a **500 naming a deleted home directory**,
   with nothing telling the user they had reached a stranger's substrate. Isolating on
   a verified-free port and pointing the substrate explicitly (`DUCKBRAIN_URL`) made the
   loop pass in 1s. Filed as AUG-060.

2. **`~/.duckbrain/foreman-status.token` is never created by a fresh boot.** The README
   requires it; the fresh box had no `~/.duckbrain` at all, and auger failed closed with
   `no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token`. The
   working move — undocumented in the README — is to mint one:
   `node bin/duckbrain.js token --name=<you>` (prints a 64-hex token on line 2). This is
   live confirmation of the existing row **AUG-039**, not a new finding.

Neither of these needed a repo visibility or permission change: the clone used existing
access and the agent was destroyed at the end of the leg.

## Findings, judged

- **Does it work?** Yes — with one silent hole. Every verb ran; the drill produced a
  correct, readable record. But `answer` accepts a `--chosen` that matches no `--option`
  and stores a configuration that selects *nothing*, exiting 0 and printing success
  (AUG-055). A user who words the choice differently from the option — the natural thing
  to do, since `--option` is a sentence — gets a silent empty configuration until they
  happen to render it.
- **Is it useful?** Yes, and more than the prior runs suggested. `dump --config` naming
  a contradiction against the record is the single most valuable behaviour seen in five
  runs; `status`'s 44-domain coverage map answers "what have we not decided" directly.
- **Is it usable?** Mostly, with one costly defect and several dead controls. The
  `--why-not` flag silently keeps only its last value while `--option` repeats
  (AUG-057); the declared `option.costs` / `option.breaks` columns are written empty by
  `answer` and no verb can ever set them, so `dump` prints `costs=— breaks=—` forever
  (AUG-058); `status` contradicts itself on its own line (AUG-059).
- **Is it trustworthy?** Mixed at the edges, solid in the core. No data was lost and
  nothing corrupted; `toggle` correctly refused an ambiguous token by name. But two
  verbs reported success over a state that did not match the report (AUG-055, AUG-056),
  and the fresh-box failure mode was a 500 from a *foreign* server (AUG-060).
- **Is it fast enough?** Yes. Local verbs 111–200 ms, `ask` 5–6 s for a model call.

**Verdict: 🟡 PROMISING-BUT-ROUGH** — the value is real and the output is genuinely
useful; the trust surface (a write that reports success and stores an empty
configuration) is the blocker, and it is a small, well-localised fix.

## What this run did NOT do

- No code was changed: the dogfood lane files findings, the project's foreman fixes them.
- Run 5's own install leg **passed** (unlike run 4's explicit skip AUG-048), so no
  SKIPPED-install-bunker row is filed for this run.
- No repo visibility, permission or clone setting was touched.
