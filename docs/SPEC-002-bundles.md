# SPEC-002 — project bundles

> Status: **model proposed, membership needs Bane's confirmation.** The model is settleable now;
> which projects belong to which bundle is Bane's architectural judgement, not an engineering
> call. Membership below is evidence-based and flagged as confirmed or inferred.

## Why this exists

This is the unfinished subgoal. Bane's own words, verbatim:

> "one thing i want to take into account for this stuff right now is how to bundle the projects
> we have for example crier bunker terminal jail are all things that go together — duckbrain hilo
> and coding hermes are all related in most times — there is a series of games and benchmarks
> that we have built pretty much to group these projects"

It matters to auger specifically because it changes what IMPACT means. `SPEC-001` BEAT 3 asks
"what does this answer affect?" and searches **one namespace**. If projects come in bundles, that
search is too narrow: a decision about the message envelope in crier is a decision that bunker
and the harnesses must honour, and tanking it silently breaks siblings.

## The finding: bundle specs already exist, in the wrong place

The fleet already writes spanning contracts — they just live **inside one member's repo**:

| Spec | Lives in | Spans | Precedent of |
|---|---|---|---|
| `specs/AGENT-ECOSYSTEM.md` | **crier** | crier ↔ bunker ↔ agent harnesses (Pi Agent, OpenCode, Hermes) | CR-SPEC-003 |
| `specs/WEBHOOK-DELIVERY.md` | **crier** | the delivery contract every consumer implements | CR-SPEC-001 |
| `specs/LLM-MESSAGE-GUARD.md` | **crier** | the verdict contract | CR-SPEC-002 |

So Bane's bundle #1 is not a hunch — it is **already documented**, with CI proving the coupling:
`crier/.github/workflows/bunker-e2e.yml` runs crier's tests against bunker.

The structural problem: **bunker cannot see crier's spec from bunker's repo.** A contract that
three projects must honour is stored where only one of them reads it. That is the gap auger
closes — a bundle-scoped decision belongs somewhere every member reads, which is exactly what
DuckBrain is for.

Evidence of coupling beyond the documented case (grep of each project's own docs):

```
crier        -> bunker, coding-hermes
warpfs       -> bunker
task-router  -> coding-hermes, coding-hermes-scheduler, duckbrain
bunker       -> coding-hermes
terminal-jail-> coding-hermes
```

## 1. The model

Two tables, plus a scope on decisions. Deliberately minimal.

```
bundle
  id           varchar   B-01
  name         varchar   agent-ecosystem
  description  varchar   one line: what the members share
  contract     varchar   where the spanning spec lives today (a path), or empty
  status       varchar   proposed | confirmed | retired

bundle_member
  id           varchar   BM-0001
  bundle_id    varchar   B-01
  project      varchar   bunker
  role         varchar   owner | consumer | test-target
  note         varchar   one line: what this member owes or expects
```

And on `decision`, one new field:

```
scope        varchar   project | bundle    (-1/empty = project)
```

- **`scope: project`** — the decision binds only its own project. Default. Unchanged behaviour.
- **`scope: bundle`** — the decision binds **every** member of the decision's bundle. It is a
  contract, and a member that violates it is broken, not merely different.

A project may belong to several bundles (a harness is both an ecosystem member and its own
project), which is why membership is a table and not a column.

### 1.1 Membership addressing and CLI

**A member's rows live in the namespace named after the member.** A `bundle_member.project` value
of `bunker` therefore means auger reads that member's decisions and evidence from namespace
`bunker`; putting bunker's rows in the bundle owner's namespace will not make them reachable.

Create and populate a bundle without raw API calls:

```
auger -n crier bundle add agent-ecosystem --contract crier/specs/AGENT-ECOSYSTEM.md
auger -n crier bundle member B-000001 crier owner
auger -n crier bundle member B-000001 bunker test-target
```

Membership validates project names against the scheduler's read-only `projects` table by default.
For standalone scratch projects only, set `AUGER_ALLOW_SCRATCH_MEMBERS=1`; auger skips that scheduler
check and prints a visible warning repeating the namespace convention. The escape does not bypass
role, bundle-existence, or duplicate-membership checks.

## 2. What bundling changes in the engine

### 2.1 The GATE searches the whole bundle (the biggest win)

`SPEC-001` BEAT 1 asks "is this question already answered?" against one namespace. With bundles it
searches **every member's namespace**:

```
evidence = recall_many([ns(m) for m in bundle_members(project)], Q.text)
```

Because the members are related "in most times", a question one member already answered is
usually the same question. This is **cross-project decision dedupe**: if terminal-jail already
decided how a sandbox isolates the filesystem, bunker should LINK that answer rather than
re-decide it and drift. Bane's own framing — "duckbrain hilo and coding hermes are all related in
most times" — is precisely this.

The `satisfies` edge gains a `src_project`, so a reader can see the answer came from a sibling.

### 2.2 The IMPACT pass crosses the boundary

`SPEC-001` BEAT 3 fires when an answer changes a decision. With bundles, an answer to a
**bundle-scoped** decision additionally walks every member:

```
for m in bundle_members(bundle_of(answer)):
    for D in decisions(m) where D.scope == 'bundle' and D.shared_contract == answer's contract:
        ask JEV: does this answer leave m's decision unchanged | change | invalidate?
        write affects/breaks(answer -> D) with dst_project = m
```

Two rules keep it finite and honest:

- Only **bundle-scoped** decisions cross the boundary. A project-local decision never does — or
  every answer reaches every project and the pass is quadratic again.
- A `breaks` edge into a sibling is an **escalation, not a silent change**. The engine does not
  rewrite another project's spec; it records the break and surfaces it.

### 2.3 Depth triage inherits from the bundle

A bundle-scoped decision's triage takes the **maximum** blast radius across its members —
breaking a contract three projects consume is not the same as breaking one. This is the honest
reading of `blast_radius × uncertainty × irreversibility` at bundle scale.

### 2.4 Bundle specs become readable by every member

The spanning spec stops being a file in one repo. `contract` points at where it lives today (for
migration), and auger's `export` renders the bundle's decisions as one document any member's
agent can read. A member's agent asks DuckBrain, not its own filesystem.

## 3. Proposed membership

`[confirmed]` = Bane stated it. `[inferred]` = evidence (docs, cross-refs, shared CI) but not
stated by him. **All of this needs his ruling** — membership is architecture, not engineering.

### B-01 · agent-ecosystem `[confirmed + documented]`

Members: `crier` (owner — the bus), `bunker` (test-target — the deployment host),
`terminal-jail` (consumer — the isolation the harnesses run inside), and the harness consumers
`pi-agent`, `opencode`, `hermes`.

Contract: `crier/specs/AGENT-ECOSYSTEM.md` (CR-SPEC-003) + `crier/specs/WEBHOOK-DELIVERY.md`.
Evidence: the spec itself; `bunker-e2e.yml` CI in crier; crier's docs referencing bunker.

### B-02 · memory-core `[confirmed by Bane]`

Members: `duckbrain` (owner), `warpfs` (hilo), `coding-hermes`, `coding-hermes-scheduler`,
`task-router`, `boardctl`.

Contract: none documented yet — this bundle is real in Bane's head and in the code
(task-router reads DuckBrain namespaces; the scheduler pins DuckBrain; boardctl writes JSONL
boards that foremen read) but **has no spanning spec**. That absence is itself a finding: three
of the fleet's core services share a storage/wire contract that nothing writes down.

Evidence: `task-router` → mentions coding-hermes, coding-hermes-scheduler, duckbrain;
`warpfs` → bunker. Bane: "duckbrain hilo and coding hermes are all related in most times".

### B-03 · games-and-benchmarks `[confirmed by Bane, membership inferred]`

Members: `Kobayashi-Maru`, `ai-plays-poke`, `temple-runner`, `mafia-ai-benchmark`, `off-by-one`,
`ring-runner`, `bankai`.

What they share: an agent is pitted against a task and **scored**. They are benchmarks that
happen to be games and games that happen to be benchmarks — which is why Bane groups them.

Contract: none. Candidate: a shared "scoring + run-record" contract (every one of these needs a
run record and a score, and each re-invents it).

### Named by Bane but not in his three examples

`h3` / `h3-protocol` / `h3-sdk-{go,python,typescript}` / `h3-shim` / `h3-umbrella` clearly form a
bundle (7 projects, one protocol, several SDKs) — **inferred, not stated**. Same for the
`dexdat` product family and the `mythos` family. **Question for Bane: are those bundles too, or
just naming conventions?**

## 4. Open questions

1. **Confirm B-01/B-02/B-03 membership**, and rule on whether `h3`, `dexdat`, `mythos` are
   bundles.
2. **Should a bundle get its own DuckBrain namespace** (e.g. `bundle-agent-ecosystem`) holding
   the shared contracts, with members reading it? Leaning: **yes** — it is the only place a
   contract is visible to every member, and it is how the "spec lives where one member reads it"
   problem actually gets fixed.
3. **Who owns a bundle-scoped decision** — the owner member, or the bundle? Affects who answers
   the escalation when a sibling breaks.
4. **Does a bundle-scoped decision block all members' ticks** until answered, or proceed on a
   default? Feeds the same autonomy question as `SPEC-001` Q5.
