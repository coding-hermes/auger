# AUG-172 fix proof

Date: 2026-11-15

## Root cause and measured baseline

The API handler had no `specs` branch, so `GET /api/ns/auger/specs` returned 404 in 0.017072s. The check handler performed a DuckBrain semantic retrieval with its fixed 60-second client timeout, then a JEV call with a 90-second timeout; the first retrieval stalled at the substrate embedding leg. The pre-fix external probe from `/tmp` timed out after 25.002409s with no response, while `/health` was 0.005623s.

## Fix

- Added cached, validated active-corpus payloads at `/api/ns/{namespace}/specs` and stable block lookup at `/api/ns/{namespace}/specs/{blockId}`. Spec reads bypass project selection because the XML corpus is local to Auger, not a DuckBrain project.
- Bounded HTTP check retrieval to 3s and JEV to 10s, returning HTTP 200 with an explicit `UNKNOWN` / `jev_used: false` fail-closed verdict when retrieval cannot complete.
- Added OpenAPI registry/documentation and regression tests.

## After probe from `/tmp`

Server: `DUCKBRAIN_URL=http://localhost:3000 .venv/bin/python3 auger.py api --host 127.0.0.1 --port 38755`

```text
after health 200 0.004201
after specs 200 0.187123
after block 200 0.000684
after check 200 3.020544
```

The specs response contained `count: 12` active blocks. The block response contained `SPEC-GRAPH-CORPUS`. The check response was HTTP 200 with `verdict: UNKNOWN`, `verdict_decided_by: fail-closed`, and `jev_used: false` in 3.020544s.

Raw response bodies and the original transcript are in `/tmp/aug172-after-*.json`, `/tmp/aug172-fix.log`, and `/tmp/aug172-before.log`.
