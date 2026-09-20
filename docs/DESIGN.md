# Auger — design (v1 working concept)

> This file is the spec. It is deliberately written so that the next session can pick it up
> cold: the ask, the architecture, what has been *proven* versus what is *assumed*, and the
> decisions still open.

## 1. The ask, in Bane's words

> "i think this started as a skill and needs to turn into a small program... we have the core
> idea we want a spec driven system but we dont want it to be a skill the agent walks away from
> we want it to be something that auto drills down and drills in to things"

> "it is a cli the agent opens the cli they point it to a duckbrain namespace that can or might
> not already have the needed tables for this. the script is asking the agent some base line
> questions and then as the agent answers questions new questions are coming"

> "we will use the new jev agent and we will keep doing a loop we get details and we ask
> questions back the questions come from another ai model that is looking at the tree and train
> of data and at the same time we need an embeding model for emebeded search retraval... then
> we use that with jev to do some comfirance scores so we keep getting some string or thread of
> data then finding other related data then asking jev and another model then taking that data
> to ask new questions"

> "for example the model might want to ask a question but jev will check if that question is
> already answered by the data in the system and returning a convdrance score so we use jev as
> also a rag part of the system"

> "the goal is that the way the skill is the sdm software development manager skill and all the
> parts of the skill the skill is given to both the agent built into the cli application and the
> agent filling in the project details then we end up with the duckbrain pre loaded with the
> specs and chocies and why not that choie and able to toggle on and off choices to see what it
> looks like so there will be a script to dump it so you can then get output with option 1 4 95
> 234 11 and then see what the system looks like if it is good or bad send that to an agent"

> "togglable specs with a feedback eninge that is for asking more questions to get more detailed
> answers based on what we have and something like jev to tell us the conidance of the data
> amount of data nd the range of data the subjecst that need to be cocvered next and an embeding
> model for the retrival"

### Decomposed into requirements

| # | Requirement | Where it lives now |
|---|---|---|
| R1 | A CLI, not a skill — persistent, not walked away from | `auger.py`, verbs below |
| R2 | Point it at a DuckBrain namespace that may or may not have the tables | `auger init` — declares if absent (idempotent) |
| R3 | Baseline questions, then new questions as answers arrive | `auger ask` (JEV) ← `auger answer` → `auger ask` … |
| R4 | Questions come from a model looking at the tree/train of stored data | `ask` builds state from decisions + retrieval + counts, sends to JEV |
| R5 | An embedding model for retrieval | DuckBrain's own index (`qwen3-embedding-8b`) via `/api/memories?q=` |
| R6 | JEV supplies confidence scores in the loop | `noul` for boolean confidence, `score` for completeness, `choice` for next-subject |
| R7 | JEV also acts as the RAG part: is this question already answered? | `auger check "Q"` → retrieval + `noul already_answered` |
| R8 | DuckBrain pre-loaded with specs, choices, and why-not-that-choice | `decision.why_not` + `option` rows + embedded evidence per decision |
| R9 | Toggle choices on/off to see what the system looks like | `auger toggle` (PATCH by primary key) |
| R10 | A dump script: "give me output with option 1 4 95 234 11" | `auger dump --config D-001=O2 D-003=O1 …` (hypothetical, non-mutating) |
| R11 | Send a good/bad configuration to an agent for judgement | `dump` renders text; piping it to a model is the next step |
| R12 | A feedback engine that asks deeper questions based on what we have | `ask` + low-confidence surfacing; the engine proper is v0.2 |
| R13 | JEV to tell us confidence, amount of data, range of data, subjects next | `ask` reports completeness + next_subject + `status` reports spread |
| R14 | The SDM skill given to both the CLI's agent and the answering agent | the skill is the method; this program is its memory and its driver |

## 2. What has been proven (not assumed)

Everything below was executed live on 2026-09-20. Transcript: `docs/SUBSTRATE-VERIFICATION.md`.

**DuckBrain is a better substrate than expected.** It already ships a *declared table registry*
(DB-SUPA-3): write `namespaces/<ns>/tables/<t>.table.json` and the namespace immediately exposes
a PostgREST-style resource at `/api/ns/<ns>/tables/<t>` — select with `eq/ne/gt/gte/lt/lte/like/in`,
`order`, `limit/offset`, `select=` projection, `Prefer: count=exact` → `X-Total-Count`, insert as
object/array/NDJSON, and **PATCH/DELETE by primary key**. Plus a per-namespace generated OpenAPI
at `/api/ns/<ns>/openapi.json`.

That is exactly R2 and R9: the "tables it might not have yet" are *declarations*, and toggling is
a PATCH by primary key. No database of our own is needed.

**Semantic retrieval works.** `POST /api/memories {key, namespace, domain, content}` embeds via
`qwen/qwen3-embedding-8b`; `GET /api/memories?namespace=<ns>&q=…` returns scored items with
snippets (observed 1.000 / 0.976 / 0.984 cosine on a probe query).

**JEV works, and in exactly the three shapes this design needs.** One request, four questions,
`$0.0000274`:

```json
{ "already_answered":     {"type":"noul",  "noul": 0.47},
  "confidence_in_evidence":{"type":"noul", "noul": 0.54},
  "completeness":         {"type":"score", "score": 1.58, "probabilities": {"1":0.57,"2":0.29,"3":0.14}},
  "next_subject":         {"type":"choice","choice": "deployment_and_alerts", "confidence": 0.82} }
```

Its answers were *correct* about the state of the probe project: storage was decided, so it said
completeness was partial and the next subject was deployment/alerts — the actual hole.

## 3. Architecture

```
        ┌──────────── auger (CLI, stdlib only) ────────────┐
        │                                                   │
   init │ start │ ask │ check │ answer │ status │ toggle │ dump
        └───┬──────────────┬───────────────────┬────────────┘
            │              │                   │
   DuckBrain HTTP      JEV decisions       DuckBrain embedding
   :3000                OpenRouter          index (qwen3-8b)
   ├ /api/namespaces    /api/alpha/decisions ├ POST /api/memories
   ├ /api/ns/:ns/tables {noul|choice|score}  └ GET  /api/memories?q=
   │   (declared, PostgREST)
   └ git-backed JSONL  <- storage of record
```

Storage is file-first, which the design leans on deliberately: the spec is readable as plain
JSONL, versioned by git, queryable over HTTP, and searchable by embedding, all from one copy.
The dump is a projection of it, never a parallel artifact.

### The data model (nine tables)

| Table | Role |
|---|---|
| `project` | the seed, the core statement, status |
| `domain` | the 44-domain grid: triage, ring floor, terminating ring, NOT-REACHED reason |
| `question` | asked questions with ring, class, and JEV's already-answered score |
| `decision` | the choice, **`why_not`**, reversal cost, confidence, status |
| `option` | every candidate for a decision, with `active` — the toggle |
| `break` | what each decision breaks and what it changed |
| `escalation` | questions only the human can weight, with a default if unanswered |
| `assumption` | what we are resting on, with a falsifier |
| `unknown` | what we know we do not know, with owner/trigger/containment |

## 4. Open decisions

1. **Name.** Working name is `auger`. One word to change. Alternatives considered: `strata`,
   `taproot`, `bore`, `quarry`.
2. **Language for v1 proper.** v0.1 is Python (stdlib only) so the loop could be proven the same
   day. The fleet convention for durable daemons is a single Go binary + systemd + a loopback
   port. Recommend: keep Python while the shape is moving, port once the verbs stop changing.
3. **Who asks the questions — JEV alone?** JEV gives calibrated choice, which is what we want for
   *confidence*, but a large-context model may propose better *questions*. Proposal for v0.2: a
   strong model proposes candidate questions, JEV scores them (dedupe via `already_answered`,
   rank via `choice`, completeness via `score`). That splits generation from judgement, which is
   also what the skill's own doctrine demands.
4. **The feedback engine (R12).** Should it auto-loop without the human (bounded by a budget and
   a stopping rule), or propose the next batch and wait? The skill's governor says the budget is
   real; the program should enforce it rather than trust the agent to.
5. **Sending a dump to an agent for judgement (R11).** What is the verdict shape — a JEV `score`
   with a rubric, or a free-text critique from a large model, or both?

## 5. v0.2 candidate work

- `auger feedback` — the loop that turns low-confidence decisions into the next question batch.
- Budget governor — a per-run question ceiling with an explicit `budget-thin` marker, mirroring
  the skill's rule.
- `auger verdict` — pipe a dump to a model (or JEV) and record good/bad with reasons.
- Domain grid seeding — the 44 domains from the skill as `domain` rows, with triage scores.
- `auger export` — render the whole namespace to a single readable spec document.
- A pytest suite that runs against an ephemeral namespace and cleans up.
