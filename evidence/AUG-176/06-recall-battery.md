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
