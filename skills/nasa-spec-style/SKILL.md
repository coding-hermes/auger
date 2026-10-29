---
name: nasa-spec-style
description: Write auger decisions in NASA requirement form.
---

# NASA spec style — auger

Companion to `docs/playbook/NASA-SE-PLAYBOOK.md`. Use it whenever you record a decision, write
docs for this repo, or review a spec of choices.

## The renderer

```bash
auger -n <ns> export --profile nasa
```

Emits every decision as `REQ-D-00N` with: the chosen statement, an ID, `[V: I]` (or `[V: D]` when
a verdict judged it), the `why_not` rationale, all alternatives marked active/inactive, and a TBD
register built from `unknown` rows. The default `export` output is unchanged — the profile is
additive.

## How to record a decision so it reads well

1. **Option labels are requirements.** Write them as noun phrases a stranger can evaluate:
   `single SQLite file`, not `standard storage`.
2. **At least two options.** A decision with one alternative was never a decision.
3. **`--why-not` explains why the loser lost**, not why the winner won.
4. **`--reversal-cost low|medium|high`** — the price of changing the answer later.
5. **Unknowns carry `--owner` and `--trigger`**; an unknown without both is an undocumented
   assumption.
6. **Never re-purpose a decided row.** Supersede it: `--supersedes D-00N --supersedes-why "..."`.

## Review checklist

- [ ] every decision has a rationale (`why_not` non-empty)
- [ ] every unknown has an owner and a trigger
- [ ] no unmeasurable adjective in an option label without a number and unit
- [ ] no `[V: T]` claim — auger never runs the test that would justify it
- [ ] `auger -n <ns> status` shows no option-count drift
- [ ] the nasa profile renders without an empty rationale or an unowned TBD

## Anti-patterns

- A decision recorded with the option text and no `why_not` — the alternative is then
  unrecoverable and the spec becomes a description, not a choice.
- Deleting a superseded decision to "clean up" — it destroys the trace of what was believed.
- Writing `[V: T]` by hand in a doc: the renderer downgrades to `I` because no executed check
  backs it, and a doc that disagrees with the renderer is drift.
