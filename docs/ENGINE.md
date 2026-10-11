# Auger — the engine (working model, v0.1 thinking)

> This is the part Bane flagged as "we need to keep talking about this". It states the engine as
> a concrete mechanism so it can be argued with, rather than as an intention. Written as v0.1
> thinking, it is now largely SHIPPED: the edge table and question linking (AUG-007/AUG-008),
> the impact pass (AUG-010), the feedback engine (AUG-001), and the verdict verb (AUG-003) are
> in `auger.py`. `docs/VERBS.md` is the registry of record for how the verbs actually behave;
> the historical framing below is kept because the reasoning still holds.

## The requirement, in the words it came in

> "the question will answers will lead to questions and following the chain and tree and trying
> to match things across the project that can be affected and continue to drive to the end
> following many branches going across and handling the depth correctly and being able to link
> that this answer that i made before is the answer to this question pretty much closing the
> branches and then the answer might reference answer and use that to see if there is another
> question that comes by resolving that chain"

> "dive down with many ways of looking in the problem — that internal agent and jev harness is
> going to be powerful for sure. the ideas have to be deep"

Distilled, that is five claims about the engine:

| | Claim |
|---|---|
| E1 | Answers **generate** questions — the growth is a chain, and a chain is a tree |
| E2 | An answer can **be** the answer to a question already asked — that CLOSES the branch |
| E3 | An answer has **reach** — it affects things elsewhere in the project, and the engine must find them |
| E4 | Depth must be **handled**, not hoped for — many branches, driven to the end, at the right depth |
| E5 | There are **many ways of looking** at the same problem, not one line of questioning |

## The core model: a graph, not a list

Auger started as rows only. The engine reasons over **edges**, because every claim above is a
statement about a relationship between two nodes.

**Nodes** (already exist as tables): `question`, `decision` (an answer), `unknown`,
`assumption`, `escalation`, `break`.

**Edges** (SHIPPED as of AUG-007/AUG-008 — the `edge` table and question linking are in the
CLI; see `docs/SPEC-001-graph.md` section 2 for the table and `docs/VERBS.md` for what each
verb writes):

| Edge | From → To | Means | Serves |
|---|---|---|---|
| `opens` | answer → question | this answer raised this question | E1 |
| `closes` | answer → question | this answer **is** the answer to that question | E2 |
| `satisfies` | answer → question | an answer recorded for a *different* question happens to answer this one | E2 |
| `affects` | answer → decision | this answer changes/is incompatible with that decision | E3 |
| `breaks` | answer → decision | this answer invalidates that decision | E3 |
| `derives_from` | question → question | this question exists only because that one was asked | E1, E4 |
| `perspective` | question → facet | the way of looking this question came from | E5 |

Only `decision.question_id` existed when this was written and nothing populated it. That gap is
closed (AUG-007/AUG-008): `auger answer --question-id Q-…` populates the field and writes the
`closes` edge in the same write — see `docs/VERBS.md` → `answer`.

### Why edges are the whole thing

Without `closes`/`satisfies`, the same question is asked repeatedly in different words and the
run grows linearly while looking busy. With them, the single most valuable behaviour becomes
possible: **before asking, check whether we already hold the answer** — and if we do, link
instead of ask. That is exactly what Bane described, and the substrate for it is already proven:

```
candidate question Q
   → retrieval over the namespace (embeddings)  → nearest N evidence rows scored
   → JEV noul "is Q already fully answered by this evidence?"
        >= 0.55  → LINK: write satisfies(answer → Q), close it, ask nothing
        <  0.55  → ASK: write question + opens(answer → Q)
```

That check is in the CLI today (`auger check`) and it **works** — verified: the same question
scored `noul 0.11` against self-contradictory evidence and `0.78` once the evidence was made
consistent. The write-back shipped with the graph (AUG-007/AUG-008): `feedback` links gate-answered
questions (`satisfies` + `linked`, never asked) and `answer --question-id` writes `closes`.

## The loop, concretely

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                          │
   ┌────▼─────┐   generate    ┌──────────┐  JEV gate   ┌───────┐   │
   │  ANSWER  │──────────────▶│ CANDIDATE│────────────▶│ ASKED │   │
   │ recorded │               │ QUESTION │   (E2)      │ open  │   │
   └────┬─────┘               └────┬─────┘             └───┬───┘   │
        │                          │ answered by          │       │
        │                          │ existing answer      │       │
        │  IMPACT PASS (E3)        ▼                      │       │
        │  retrieval + JEV   ┌──────────┐                 │       │
        └───────────────────▶│  LINKED  │◀────────────────┘       │
             what does this  │  closed  │   answered                │
             break?          └──────────┘                           │
        │                                                          │
        └──────────── new questions from impact ───────────────────┘
```

Four beats, and each already has a proven substrate:

1. **ASK** — pick the thinnest open branch. *Where:* `auger ask`. *Model:* JEV `choice` for the
   subject, `score` for completeness. *Proven.*
2. **GATE** — before anything is asked: is it already answered? *Where:* `auger check`. *Model:*
   retrieval + JEV `noul already_answered`. *Proven.*
3. **ANSWER** — record the choice, the rejected alternatives, the reason, the confidence.
   *Where:* `auger answer`. *Proven.*
4. **IMPACT** — the beat that makes it an engine, and it is SHIPPED (AUG-010). When an answer
   lands, find what it touches: retrieve neighbours, ask JEV what it affects/breaks, and record
   `affects`/`breaks` edges naming each sibling's bundle-scoped decision — the bundle impact
   pass runs on `auger answer --scope bundle` (JEV `choice`, confidence-floored, fail-closed),
   with cross-boundary edges carrying `dst_project` and every break recorded as an escalation
   with a default. See `docs/VERBS.md` → `answer` (scope bundle).

## E4 — depth, handled rather than hoped for

A branch is a chain of questions descending from one decision. Depth is a budget, not a mood.

- **Every branch carries a triage score** — `blast_radius × uncertainty × irreversibility`, the
  skill's own integer — and the score sets the branch's ring floor and ceiling.
- **The budget is enforced by the program, not the agent.** The skill trusts an agent to respect
  a ceiling. A program can refuse. That is the whole reason this is becoming software.
- **A branch closes** when (a) its questions are all `closes`/`satisfies`-linked, or (b) it hits
  its ceiling, and then it records `budget-thin` with the reason. An honest shallow branch beats
  a silent one.
- **The run ends** when no branch is open, no branch has budget left, and one full pass produces
  no new edge. That is the machine-checkable form of the skill's "fixed point".

## E5 — many ways of looking

One question asked once gets one answer and looks complete. The same question asked from several
economic perspectives does not. Concretely, every open question is asked from a fixed set of
**facets**, and a question is only closed when its facets are closed:

- **data** — what does this mean, and where does it live?
- **failure** — how does this stop working, and who finds out?
- **ownership** — who owns it after delivery, and what is the trigger to revisit?
- **cost** — what does this make more expensive over time?
- **test** — what proves it works, and what proves it stopped?
- **who-else** — what else in the project does this touch? (feeds E3)

This is the concrete answer to "many ways of looking". The facet set is data, not code, so it can
be extended per project.

## Open questions — from agenda to settled

When this was written these were the actual agenda. Every leaning below was later recorded as a
concluded engineering call in `docs/SPEC-001-graph.md` section 7 and is implemented in
`auger.py`; they are kept here in their original wording as the rationale record.

1. **Does the gate run on every candidate question, or only the cheap ones?** Every question
   costs ~$0.00003 to gate. Gating everything is affordable; not gating produces duplicate work.
   Leaning: gate everything, because the duplicate-question failure is the expensive one.
   → **Settled: gate everything (Q1).** The gate runs on every candidate in `feedback` and
   `propagate`'s gate walk.
2. **Who generates the candidate questions — JEV or a large model?** JEV *judges* superbly
   (calibrated choice/score/noul) but it does not generate prose well. Leaning: a large-context
   model proposes candidates from the graph, JEV gates and ranks them. That splits generation
   from judgement, which the skill's own doctrine requires.
   → **Shipped:** `feedback` proposes follow-ups with a large model (`AUGER_PROPOSER_MODEL`,
   default `deepseek/deepseek-v3.2`) and JEV gates each proposal — see `docs/VERBS.md` →
   `feedback`.
3. **How aggressive is the `affects` pass?** Every answer could touch everything, so the pass
   needs a stopping rule or it becomes quadratic. Leaning: only make the pass when the answer
   *changes* a previously recorded decision, or when retrieval scores above a floor.
   → **Settled: two conditions (Q3).** Implemented as the neighbour floor and the impact
   confidence floor (`T_NEIGHBOUR` / `T_IMPACT_FLOOR` in `auger.py`).
4. **Is a closed branch reopened automatically?** If a new answer invalidates an old one, the old
   branch is no longer closed. The skill has an invalidation walk for exactly this. Leaning:
   automatic reopen, with the reason recorded, because a stale closed branch is worse than an
   open one.
   → **Settled: yes, with the reason (Q4).** `auger propagate` runs the rule walk (moot +
   reopen + `derives_from` cascade) and the gate walk — see `docs/VERBS.md` → `propagate`.
5. **Does the human sit inside the loop or at the ends?** Your skill reserves priority judgments
   for the human. Leaning: the engine runs unattended but *stops* at a priority judgment, records
   an escalation with a default and a risk, and continues on the default — so it never blocks and
   never pretends to have decided for you.
   → **Shipped:** escalation rows with a default action are written by the impact pass and
   `feedback` (per decision and run-level `budget-thin`).

## What this means for the board — historical note

The two rows this section pointed at are built: **AUG-007/AUG-008** landed the edge table and
question linking first (edges, or nothing can be walked), then **AUG-001** built the feedback
loop over those edges, with AUG-010 (impact) and AUG-003 (verdict) completing the set. Kept for
the sequencing rationale — graph before loop — which still governs how new engine features land.
