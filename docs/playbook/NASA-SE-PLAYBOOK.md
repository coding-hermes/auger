# The NASA school of specification — auger house style

This repo's spec is a **spec of choices**: every decision, the alternatives that lost, why they
lost, and what would make the choice wrong. NASA writes requirements exactly this way, and the
practice transfers cleanly — this playbook names the mapping and the rules the renderer follows.

Sources: **NASA/SP-2016-6105 Rev 2** (Systems Engineering Handbook), **NPR 7150.2** (NASA
Software Engineering Requirements), **NASA SWEHB** checklists.

## 1. The mapping

| NASA artifact | auger row |
|---|---|
| Requirement (unique id + one `shall` statement) | `decision` row — `D-00N`, with its `chosen` option |
| Alternatives analysis | `option` rows — `D-00N-OY`, each with its `costs` / `breaks` |
| Rationale / why the losers lost | `decision.why_not` |
| Verification method (T / D / A / I) | a `verdict` row (an independent judgement) → `D`; otherwise `I` |
| TBD / TBR register | `unknown` row, with `owner` and `trigger` |
| Assumptions with falsifiers | `assumption` row, with `falsifier` |
| Traceability / bidirectional links | `edge` rows (`derives_from`, cross-project) |
| Document generation | `auger export` (deterministic) and `auger export --profile nasa` |

## 2. Rules the renderer enforces

- **AUG-NS-001** One requirement per decision; the statement is the chosen option's label, never a
  compound of several options. `[V: I]` by inspection of the record unless a verdict exists.
- **AUG-NS-002** A requirement always carries an ID (`REQ-D-00N`), derived from the stored
  decision id. IDs are stable; a superseded decision keeps its row and gains a successor.
- **AUG-NS-003** The losing options are rendered with the same prominence as the winner, because an
  unexamined alternative is the cheapest way to be confidently wrong.
- **AUG-NS-004** The verification method is `D` (demonstration) only when a `verdict` row judged
  that decision; `I` (inspection) otherwise. The renderer must never claim `T` — a test would
  require an executed check, which auger does not run.
- **AUG-NS-005** Every `unknown` renders into the TBD register with its owner and closing trigger.
  An unknown with neither is a finding, not a note.
- **AUG-NS-006** Every `assumption` renders with its falsifier, so a reader knows what would
  overturn it.

## 3. Writing rules for humans and agents

- Decision text is a noun phrase a reader can evaluate without context ("single SQLite file", not
  "we use the standard storage"). The option labels are the requirement statements.
- `why_not` states the *reason the alternative lost*, not a restatement of the winner.
- `reversal_cost` is `low | medium | high` — it is the price of changing the answer later, and it
  is what makes a spec of choices actionable rather than descriptive.
- Never edit a decided row to mean something new. Supersede it (`--supersedes D-00N`), which keeps
  the trace of what was believed and why.
- No unmeasurable adjectives in a statement. "fast" is not a decision; "p95 under 200 ms on the
  reference host" is.

## 4. Ready gate

A namespace is ready to hand to another agent when:

```bash
auger -n <ns> status                      # no drift, no zero-or-multi active options
auger -n <ns> export --profile nasa       # renders every decision as a requirement
```

and the rendered document shows: no decision without a rationale, no unknown without an owner and
trigger, no option list with a single alternative that was never weighed, no `[V: T]` claim.

## 5. What this does not do

It does not add NASA programme process (phase reviews, CDRLs, programme boards). It takes the
specification discipline — identifiable, atomic, traced, verifiable, rationale-carrying — and
applies it to the register auger already stores.
