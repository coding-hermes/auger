# SPEC-001 — the question/answer graph

> Status: **draft for agreement.** This is the schema and the propagation rules the engine needs.
> It is written to be executed by a worker without further conversation. Parts that depend on
> unresolved design decisions are marked `[BLOCKED: Qn]` and must not be implemented until the
> decision is recorded.

## Why this spec exists

`docs/ENGINE.md` states the engine as five claims (E1–E5). This spec turns the graph parts of
those claims into tables and rules. Without it, AUG-001 and AUG-002 cannot be built: there is
nothing to walk.

The governing insight: auger stores **rows**, but the engine reasons over **relationships**.
Every claim Bane stated — answers leading to questions, matching across the project, linking an
old answer as the answer to a new question, closing branches, resolving a chain — is an assertion
about two nodes. Today exactly one such field exists (`decision.question_id`) and nothing
populates it.

## 1. Node states

A `question` moves through a closed set of states. The state is what makes the graph walkable —
a question is not "pending", it is in one specific condition with a reason.

| State | Means | Set by |
|---|---|---|
| `open` | asked, no answer yet, branch live | ASK |
| `answered` | an answer closes it (`answered_by` edge exists) | ANSWER, or GATE-link |
| `linked` | an **existing** answer satisfies it, without a new answer being written (E2) | GATE |
| `moot` | a parent decision was overturned, so this question no longer matters | PROPAGATE |
| `budget_thin` | closed by hitting the branch ceiling, with the reason recorded | BUDGET |

`answered` and `linked` are deliberately different: `answered` cost a new answer and `linked`
cost only a gate call. The ratio of the two is the engine's honest score of how much repeated
work the gate is saving — and that ratio is worth reporting every run.

**A question is never deleted.** A moot question keeps its row and its reason, because "we
stopped caring about this, and here is why" is a finding.

## 2. The `edge` table

One generic edge table, not one per relationship. Rationale: DuckBrain declared tables require
explicit columns and never introspect, so a narrow fixed shape is ideal, and new edge kinds must
not require a schema migration mid-run.

```
edge
  id           varchar   E-000001        (fixed-width, per the project's own id law)
  project_id   varchar
  kind         varchar   opens | closes | satisfies | affects | breaks |
                         derives_from | references | blocks
  src_kind     varchar   decision | question | unknown | assumption
  src_id       varchar
  dst_kind     varchar
  dst_id       varchar
  confidence   double    the gate's noul where the edge came from a model call; -1 when
                         the edge was asserted directly by a person or a rule
  source       varchar   gate | impact | human | rule     (how the edge was born)
  note         varchar   one line: why this edge exists
  created_at   timestamp
```

Primary key `id`. Indexing is unnecessary at these volumes (a large project is thousands of
edges, and DuckBrain serves them over the declared-table API with filters and ordering).

### Edge semantics, one line each

- **`opens`** — answer → question. This answer raised this question. (E1)
- **`closes`** — answer → question. This answer *is* the answer to that question. (E2)
- **`satisfies`** — answer → question. An answer written for a *different* question happens to
  answer this one. Distinct from `closes` because the answer was not written *for* it — and the
  distinction is what tells a reader whether the fit was designed or discovered. (E2)
- **`affects`** — answer → decision. This answer changes or is incompatible with that decision. (E3)
- **`breaks`** — answer → decision. This answer *invalidates* that decision. Stronger than
  `affects`; an `affects` that turns out to force a change is upgraded to `breaks`. (E3)
- **`derives_from`** — question → question. This question exists only because that one was asked. (E1, E4)
- **`references`** — answer → answer. This answer cites that one as evidence. (E1)
- **`blocks`** — question → question. This question cannot be answered until that one is. This is
  the only edge that *prevents* progress, and it is what makes depth ordering meaningful. (E4)

## 3. The `facet` table (E5 — "many ways of looking")

```
facet
  id           varchar   F-000001
  project_id   varchar
  question_id  varchar
  facet        varchar   data | failure | ownership | cost | test | who_else
  status       varchar   open | closed
  closed_by    varchar   decision id, or empty
  note         varchar
```

Every open question carries one row per facet. A question is only fully resolved when its facets
are resolved — which is the operational meaning of "many ways of looking at the problem" instead
of one line of questioning. The facet set is **data, not code**, so a project can extend it.

`who_else` is not a perspective on the question; it is the trigger for the impact pass (E3). It
is listed with the others because it is asked the same way, but it produces `affects`/`breaks`
edges rather than an answer.

## 4. The four beats

### BEAT 1 — ASK

```
candidates = propose(graph)          # [BLOCKED: Q2] who proposes — see below
for Q in candidates, cheapest-first:
    evidence = recall(ns, Q.text, limit=N)        # embeddings, already proven
    verdict  = jev(noul "is Q already fully answered by evidence?")
    if verdict >= T_ANSWERED:
        write edge satisfies(best_evidence -> Q, confidence=verdict, source=gate)
        state(Q) = linked                    # CLOSED. cost: one gate call.
    else:
        write question row, state=open
        write facets (one per facet in the set)
```

**Engineering call — Q1 (gate everything or only cheap ones): gate everything.** A gate costs
~$0.00003 and one retrieval. A duplicate question costs a gate *plus* a full answer, an impact
pass, and a wrong-looking graph that a reader has to untangle. Gating is 4 orders of magnitude
cheaper than the failure it prevents. This is now recorded as concluded, not open.

### BEAT 2 — ANSWER

Unchanged from today: record the choice, the rejected alternatives, **the reason each lost**, the
reversal cost, the confidence. Plus: populate `decision.question_id` (today nothing does) and
write `closes(answer → question)`.

### BEAT 3 — IMPACT (E3)

The new beat, and the one that makes this an engine rather than a form.

```
neighbours = recall(ns, answer.summary, limit=N)     # what does this reach?
for D in neighbours, excluding the decision this answer belongs to:
    verdict = jev(choice "does this answer leave D unchanged | change D | invalidate D?")
    if 'unchanged'                -> nothing
    elif 'change'   and score>=T  -> write affects(answer -> D)
    elif 'invalidate' and score>=T-> write breaks(answer -> D)
```

**Engineering call — Q3 (how aggressive): two conditions, not one.** The pass fires when (a) the
answer *changed* a previously recorded decision (a human/rule assertion, no model call needed), or
(b) retrieval over the namespace returns a neighbour above a score floor `T_NEIGHBOUR`. Below the
floor nothing is asked. Without a floor the pass is quadratic and every answer "affects" every
other; with it, the pass is proportional to genuine semantic proximity. `T_NEIGHBOUR` starts at
the retrieval layer's own default and is tuned from the observed false-positive rate.

### BEAT 4 — PROPAGATE (closing branches, E2/E4)

This is the mechanic Bane described and the one the design was missing.

```
when a question becomes answered | linked | moot:
    for Q2 in questions where edge derives_from(Q2 -> Q):
        if all blockers of Q2 are resolved:
            if Q2's facets are now answerable from existing evidence:
                try GATE on Q2                    # link it, do not ask it
            else:
                Q2 becomes newly askable          # surface on the next ASK
    when a decision D is invalidated by a breaks edge:
        for Q2 in questions that D closed:
            state(Q2) = moot, reason = "D-0NN was invalidated by E-000NN"
            recurse: propagate Q2's change to its dependents
```

**Engineering call — Q4 (do closed branches reopen automatically): yes, with the reason
recorded.** A branch closed by an answer that has since been invalidated is not closed — it is
*stale*, and stale is worse than open because it silently reads as done. The reopen is automatic
and records why, so the graph never claims a settled state it no longer has.

## 5. Depth and the budget (E4)

- Every branch carries the triage integer `blast_radius × uncertainty × irreversibility`.
- The triage sets the branch's floor and ceiling in rings.
- **The program enforces the ceiling; the agent does not.** A program can refuse. That refusal is
  the reason this is software and not a skill.
- A branch that stops at its ceiling writes `budget_thin` with the reason. An honest shallow
  branch beats a silent one.
- `blocks` edges order the work: a question whose blocker is unresolved is not asked, so depth
  follows dependency rather than the order questions happened to arrive.

## 6. Termination

The run ends when **all three** hold, checked after a full pass:

1. no question is `open`;
2. no branch has budget left unspent;
3. the pass produced **no new edge**.

Condition 3 is the machine-checkable form of the skill's "fixed point": a pass that finds nothing
new. It is checked on edges rather than on questions, because an answer that closes a question and
opens nothing is the real signal that a branch is finished.

## 7. Open decisions this spec depends on

| | Decision | Leaning | Blocks |
|---|---|---|---|
| Q1 | gate every candidate question? | **CONCLUDED: yes** (see BEAT 1) | — |
| Q2 | who *proposes* candidates — JEV or a large model? | large model proposes, JEV gates and ranks | BEAT 1 |
| Q3 | how aggressive is the impact pass? | **CONCLUDED: two conditions** (see BEAT 3) | — |
| Q4 | do closed branches reopen automatically? | **CONCLUDED: yes, with the reason** (see BEAT 4) | — |
| Q5 | does the human sit inside the loop or at the ends? | runs unattended, stops only at a priority judgment, records an escalation with a default and continues on the default | BEAT 1, BEAT 4 |

**Q2 and Q5 are the two that still gate implementation.** Everything else in this spec is
settled and buildable.
