# AUG-166 external-agent consumption

Date: 2026-11-15

## Acceptance result

**BLOCKED: product surface does not expose spec-corpus blocks/build decisions over serve/api.**
The required external-agent flow was attempted from `/tmp` using `curl` only. The active
corpus could not be resolved through the live HTTP surface, no block could be fetched, and
no build decision could be returned. Defect filed as AUG-172. AUG-166 remains open.

## Live transcript

Server command (repo root):
`DUCKBRAIN_URL=http://localhost:3000 .venv/bin/python3 auger.py api --host 127.0.0.1 --port 38755`

| Request | HTTP status | bytes | round-trip |
|---|---:|---:|---:|
| `GET /health` | 200 | 105 | 6.232194s |
| `GET /api/ns/auger/status` | 409 | 200 | 0.031584s |
| `GET /api/ns/auger/specs` (spec lookup attempt) | 404 | 47 | 0.016590s |
| `POST /api/ns/auger/check` (build decision attempt) | 000 (curl timeout) | 0 | 20.002399s |

`/health` body: `{"tier": "semantic", "embedding_healthy": true, "provider": "openai", "model": "qwen/qwen3-embedding-8b"}`

`/api/ns/auger/status` body: namespace has multiple projects and requires an explicit project id.
`/api/ns/auger/specs` body: `{"error": "no such route: /api/ns/auger/specs"}`.
The POST timed out with no response body after 20 seconds.

The separately attempted documented `serve` route
`GET /api/ns/auger/embeddings/search?q=SPEC-GRAPH-CORPUS&limit=5` also produced an
empty reply after the server logged `embedding query...`; curl reported status 000 and
received 0 bytes (the read path did not yield a usable block response).

Raw curl transcript: `/tmp/aug166-api-transcript.log`.
Server logs: `/tmp/aug166-api.log` and `/tmp/aug166-serve.log`.
