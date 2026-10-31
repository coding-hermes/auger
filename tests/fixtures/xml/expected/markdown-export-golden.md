# Auger spec export — namespace golden-ns

Read-only deterministic rendering of every declared table.
Scope: whole namespace (no rows are written).

## Projects (1 rows)
### P-000001
- id: P-000001
- name: golden-project
- seed: one line of seed text
- core_statement: the thing this project is about
- status: active
- created_at: 2026-01-01T00:00:00Z

## Domains (0 rows)
### (none stored)

## Questions (0 rows)
### (none stored)

## Decisions (2 rows)
### D-001
- id: D-001
- project_id: P-000001
- domain: 4.01
- question_id: Q-000001
- chosen: ship the additive feature
- why_not: {"O2": "breaks the export contract"}
- reversal_cost: cheap
- confidence: 0.8
- status: answered
- evidence_key: /P-000001/decision/D-001
- scope: —
### D-002
- id: D-002
- project_id: P-000001
- domain: 4.02
- question_id: —
- chosen: keep Markdown untouched
- why_not: —
- reversal_cost: —
- confidence: 0.55
- status: answered
- evidence_key: —
- scope: bundle

## Options (2 rows)
### D-001-O1
- id: D-001-O1
- decision_id: D-001
- label: additive
- costs: one new module
- breaks: nothing
- active: true
### D-001-O2
- id: D-001-O2
- decision_id: D-001
- label: replace
- costs: migration
- breaks: every existing consumer
- active: false

## Break records (0 rows)
### (none stored)

## Escalations (0 rows)
### (none stored)

## Assumptions (0 rows)
### (none stored)

## Unknowns (1 rows)
### U-001
- id: U-001
- project_id: P-000001
- text: does the substrate page at scale?
- owner: unassigned
- trigger: first 10k rows
- containment: page the reads

## Graph edges (0 rows)
### (none stored)

## Question facets (0 rows)
### (none stored)

## Bundles (0 rows)
### (none stored)

## Bundle members (0 rows)
### (none stored)

## Verdicts (0 rows)
### (none stored)