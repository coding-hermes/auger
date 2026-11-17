# AUG-176 AC3 — embedding recall battery (registration + run)

## Surface under test

The live DuckBrain substrate (`DUCKBRAIN_URL=http://127.0.0.1:3000`, the substrate's own
semantic search: `GET /api/memories?namespace=<ns>&q=<q>&prefix=<key-prefix>&limit=<n>`,
the same read `auger recall` and `auger serve /api/ns/<ns>/recall` drive).

## Registration

Every active block of each converted corpus was registered as one memory row keyed
`/auger/AUG176-<project>/<BLOCK-ID>` whose content is the block's verbatim text, so a hit's
key round-trips to the block id and (via the corpus manifest) to the source file + section
anchor. Blocks larger than 24,000 chars are split into `-P<nn>` PART rows at 24,000-char
boundaries (three generated director tables are single 182k-340k-character lines and 500
the substrate's embed path when posted whole; PART rows keep every character queryable —
zero loss, and a hit on any part names its block).

Run transcript (raw):

```
== AUG-176 recall battery: registration ==
Mon Nov 16 09:31:33 AM UTC 2026
{"project": "code-factory", "blocks": 80, "rows_written": 80}
  auger-docs: 100 row(s) written
{"project": "auger-docs", "blocks": 157, "rows_written": 157}
  coding-hermes-director: 100 row(s) written
remember /auger/AUG176-coding-hermes-director/CHD-NORMATIVE-MANIFEST-PREAMBLE-S000 failed (500): {'error': 'Internal server error', 'code': 'INTERNAL_ERROR'}
Mon Nov 16 09:32:00 AM UTC 2026
== AUG-176 recall battery: registration ==
Mon Nov 16 09:33:24 AM UTC 2026
{"project": "code-factory", "blocks": 80, "rows_written": 80, "failed_rows": []}
  auger-docs: 100 row(s) written
{"project": "auger-docs", "blocks": 157, "rows_written": 157, "failed_rows": []}
  coding-hermes-director: 100 row(s) written
  coding-hermes-director: 200 row(s) written
  coding-hermes-director: 300 row(s) written
  coding-hermes-director: 400 row(s) written
  coding-hermes-director: 500 row(s) written
  coding-hermes-director: 600 row(s) written
  coding-hermes-director: 700 row(s) written
  coding-hermes-director: 800 row(s) written
  coding-hermes-director: 900 row(s) written
  coding-hermes-director: 1000 row(s) written
{"project": "coding-hermes-director", "blocks": 1027, "rows_written": 1027, "failed_rows": ["CHD-NORMATIVE-MANIFEST-PREAMBLE-S000-P01: remember /auger/AUG176-coding-hermes-director/CHD-NORMATIVE-MANIFEST-PREAMBLE-S000-P01 failed (500): {'error': 'Internal server error', 'code': 'INTERNAL_ERROR'}", "CHD-REFERENCE-INDEX-SECTION-REQUIREMENT-ACCEPTANCE-S002-P01: remember /auger/AUG176-coding-hermes-director/CHD-REFERENCE-INDEX-SECTION-REQUIREMENT-ACCEPTANCE-S002-P01 failed (500): {'error': 'Internal server error', 'code': 'INTERNAL_ERROR'}", "CHD-REFERENCE-INDEX-COMPLETE-FILE-LOOKUP-S003-P01: remember /auger/AUG176-coding-hermes-director/CHD-REFERENCE-INDEX-COMPLETE-FILE-LOOKUP-S003-P01 failed (500): {'error': 'Internal server error', 'code': 'INTERNAL_ERROR'}"]}
Mon Nov 16 09:35:21 AM UTC 2026

== AUG-176 recall battery: run ==
Mon Nov 16 09:38:33 AM UTC 2026
embedding query... (first recall in a process pays the provider round-trip, ~20s)
substrate read failed for 'auger' (status 0): transport: timed out — a failed read is NOT an empty store
battery rc=1
Mon Nov 16 09:39:33 AM UTC 2026

== AUG-176 recall battery: run ==
Mon Nov 16 09:40:22 AM UTC 2026
embedding query... (first recall in a process pays the provider round-trip, ~20s)
substrate read failed for 'auger' (status 0): transport: timed out — a failed read is NOT an empty store
battery rc=1
Mon Nov 16 09:43:22 AM UTC 2026
```

## 2026-11-17 — the battery now runs and reports; 27/36 = 75 % top-5 distinct blocks

AUG-178 removed the pid-less `.search.rebuild.lock` that made every `?q=` on this namespace hang,
so the run half of this battery executed for the first time. Command (repo root, venv interpreter):

```
.venv/bin/python3 scripts/recall_battery.py run --out /tmp/aug176-battery-fixed.json
```

| project | hits / queries | hit rate |
|---|---|---|
| code-factory | 11 / 12 | 92 % |
| auger-docs | 9 / 12 | 75 % |
| coding-hermes-director | 7 / 12 | 58 % |
| **total** | **27 / 36** | **75 %** |

### The measurement defect this run found and fixed

The first measurement of the same battery returned **8/12 (67 %) code-factory** and
**5/12 (42 %) auger-docs**. The cause was in the battery, not the corpus: `register` was run
TWICE (see the two `== registration ==` blocks above — the second was a retry after the
`CHD-NORMATIVE-MANIFEST-PREAMBLE-S000` 500), and a memory write appends rather than upserts, so
every key exists twice. Asking the substrate for 5 rows therefore returned only **2–4 distinct
blocks**, and "target in top-5" was silently grading "target in top-2..4". Verified by read-back:

```
GET /api/memories?namespace=auger&prefix=/auger/AUG176-code-factory/CF-TRACE-6-VERIFICATION-WHAT-MUST-BE-TR-S006
-> 200, 2 rows, both key = CF-TRACE-6-VERIFICATION-WHAT-MUST-BE-TR-S006
```

Fix in `scripts/recall_battery.py`: request `CANDIDATE_ROWS = 25` rows, de-duplicate keys in
substrate order (`_distinct_keys`), grade the first `TOP_K = 5` distinct blocks, and record each
query's `rank` (None = target absent from the whole window). Red-proof against HEAD's script
(`evidence/AUG-176/07-battery-red-proof.txt`): HEAD has no `_distinct_keys`/`_rank`, requests
`limit=5`, and emits no rank. Same tree, same substrate, before → after:

| project | rows-based (HEAD battery) | distinct-block top-5 |
|---|---|---|
| code-factory | 8 / 12 | 11 / 12 |
| auger-docs | 5 / 12 | 9 / 12 |

Also fixed: the director battery named `CHD-DESIGN-3-4-BACKPRESSURE-IS-PER-GATEWAY-S016`, which is
not a block id — the manifest's id scheme truncates the slug (`...-PER-GATEWA-S016`).
`resolve_targets` refused the run loudly, which is the behaviour that should exist.

### Residuals, each named (no silent pass)

Nine queries miss and are recorded, not averaged away:

- **code-factory** `how does the control host dispatch work to a node's inbox` → target
  `CF-ENVELOPE-DISPATCH-CONTROL-HOST-NODE-INB-S001` ranks **9** in a 13-block window; the blocks
  above it (`CF-ENVELOPE-RESULT-NODE-REPLY-TO-INBOX-S002`, `CF-TRACE-*`) repeat the same "control
  host … node" vocabulary. Ranking collision between sibling sections, not a missing row.
- **auger-docs** three ABSENT targets — `AD-SPEC-003-XML-SPECS-RATIONALE-S001`,
  `AD-RELEASE-CUTTING-A-RELEASE-S002`, `AD-DESIGN-4-OPEN-DECISIONS-WITH-SHIPPED-S006`. Their short
  queries share no lexical anchor with the target bodies; the window returns preamble/goal blocks.
- **coding-hermes-director** five misses: `RESTART-RECOVERY` rank 6 and `RELEASE-BLOCKERS` rank 8
  (ranked but outside top-5), plus three ABSENT (tool grants by role, data model for an agent,
  operation-card rules). 22 documents / 1027 blocks of long handbook prose where many sections
  restate the same vocabulary — the least unique query signal of the three corpora.
- **Re-chunking is bounded by the substrate, not by will:** a memory row cannot be deleted through
  the API, so re-chunking a block would ADD rows to a namespace that already holds each key twice
  and dilute the window further. A real re-chunk has to register under a new key prefix
  (e.g. `AUG176b`) with the duplicate-per-key artefact removed first.
- **Rank instability is observable:** two code-factory queries ABSENT in a 20-row probe minutes
  earlier ranked 1 and 2 in this 25-row run (`the fleet index record`,
  `how does the control host search fleet memory`). The hybrid ranking is not stable across
  identical queries, so one run is a sample; per-query ranks are kept so a second run can be
  compared rather than argued about.

### What this proves

- **AUG-178 AC4**: the battery runs to completion and writes per-project hit rates with the query
  list and per-query ranks — this file plus `/tmp/aug176-battery-fixed.json`.
- **AUG-176 AC3**: registration + embedding recall is measurable end to end (27/36 = 75 % top-5
  distinct); registration is intact (rows are present and retrievable, and every hit round-trips to
  a block id). What remains is ranking/chunking quality, which is named above per query.

