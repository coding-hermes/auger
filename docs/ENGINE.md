# Auger — the engine (working model, v0.1 thinking)

> This is the part Bane flagged as "we need to keep talking about this". It states the engine as
> a concrete mechanism so it can be argued with, rather than as an intention. Nothing here is
> final; the open questions at the bottom are the actual agenda.

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

Today auger has rows. The engine needs **edges**, because every claim above is a statement about
a relationship between two nodes.

**Nodes** (already exist as tables): `question`, `decision` (an answer), `unknown`,
`assumption`, `escalation`, `break`.

**Edges** (missing — this is the actual gap):

| Edge | From → To | Means | Serves |
|---|---|---|---|
| `opens` | answer → question | this answer raised this question | E1 |
| `closes` | answer → question | this answer **is** the answer to that question | E2 |
| `satisfies` | answer → question | an answer recorded for a *different* question happens to answer this one | E2 |
| `affects` | answer → decision | this answer changes/is incompatible with that decision | E3 |
| `breaks` | answer → decision | this answer invalidates that decision | E3 |
| `derives_from` | question → question | this question exists only because that one was asked | E1, E4 |
| `perspective` | question → facet | the way of looking this question came from | E5 |

Only `decision.question_id` exists today and **nothing populates it** — see board row AUG-002.

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
consistent. The engine adds the write-back: today `check` only prints a verdict.

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
4. **IMPACT** — the new beat, and the one that makes it an engine. When an answer lands, find
   what it touches: retrieve neighbours, ask JEV what it affects/breaks, and either record the
   break or open the follow-up question. *This is E3, and it does not exist yet.*

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

## Open questions — the actual agenda

1. **Does the gate run on every candidate question, or only the cheap ones?** Every question
   costs ~$0.00003 to gate. Gating everything is affordable; not gating produces duplicate work.
   Leaning: gate everything, because the duplicate-question failure is the expensive one.
2. **Who generates the candidate questions — JEV or a large model?** JEV *judges* superbly
   (calibrated choice/score/noul) but it does not generate prose well. Leaning: a large-context
   model proposes candidates from the graph, JEV gates and ranks them. That splits generation
   from judgement, which the skill's own doctrine requires.
3. **How aggressive is the `affects` pass?** Every answer could touch everything, so the pass
   needs a stopping rule or it becomes quadratic. Leaning: only make the pass when the answer
   *changes* a previously recorded decision, or when retrieval scores above a floor.
4. **Is a closed branch reopened automatically?** If a new answer invalidates an old one, the old
   branch is no longer closed. The skill has an invalidation walk for exactly this. Leaning:
   automatic reopen, with the reason recorded, because a stale closed branch is worse than an
   open one.
5. **Does the human sit inside the loop or at the ends?** Your skill reserves priority judgments
   for the human. Leaning: the engine runs unattended but *stops* at a priority judgment, records
   an escalation with a default and a risk, and continues on the default — so it never blocks and
   never pretends to have decided for you.

## What this means for the board

AUG-001 (feedback engine) and AUG-002 (branch linking) are the two rows that build this. The
engine cannot be built in one pass: **AUG-002 first** (edges, or nothing can be walked), then
AUG-001 (the loop over those edges). Everything else is downstream.
