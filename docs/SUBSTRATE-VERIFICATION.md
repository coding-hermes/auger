# Substrate verification — 2026-09-20

Every claim in this file was produced by a live call, not read from documentation. The purpose
is to record *what the substrate actually does*, so the design does not rest on assumption.

## 1. DuckBrain HTTP — health

```
GET http://127.0.0.1:3000/health                      -> 200
{"status":"healthy","uptime":18135.5,
 "embedding":{"provider":"openai","model":"qwen/qwen3-embedding-8b","healthy":true}}
```

## 2. Declared tables exist and are live immediately

A table is declared by writing one JSON file. Confirmed against a real namespace in the wild:

```json
{"name":"launches","format":"jsonl-objects","primary":"id","glob":"tables/launches.jsonl",
 "columns":[{"name":"id","type":"integer"},{"name":"vehicle","type":"varchar"},
            {"name":"status","type":"varchar"},{"name":"attempt","type":"integer"}]}
```

Declaring two SDM-shaped tables on a fresh namespace made them visible with no restart:

```
GET /api/ns/sdm-substrate-probe/tables
{"namespace":"sdm-substrate-probe","tables":[{"name":"sdm_decision", ...}, {"name":"sdm_option", ...}]}
```

Supported column types map 1:1 onto DuckDB: `varchar, integer, bigint, double, boolean,
timestamp, json`. Unknown tables are a plain 404 — there is deliberately **no auto-introspection
fallback** (bare `read_json_auto` on heterogeneous JSONL has SIGABRT'd the process before).

## 3. CRUD, filters, counts, ordering

| Call | Result |
|---|---|
| `POST /api/ns/<ns>/tables/sdm_decision` (array of 3) | `{"inserted":3}` |
| `GET …?select=id` with `Prefer: count=exact` | `X-Total-Count: 3` |
| `GET …?domain=eq.4.05&order=confidence.desc` | 2 rows, correctly ordered |
| `GET …?status=in.(provisional)` | 1 row |
| `GET …?confidence=lt.0.6` | 1 row — *this query is the "what needs drilling" engine* |

## 4. The toggle is a PATCH by primary key

```
PATCH /api/ns/<ns>/tables/sdm_option?pk=eq.O-003   {"active": true}
-> {"updated":1}
GET …?decision_id=eq.D-001&active=eq.true&select=id,label
-> [{"id":"O-001","label":"single sqlite file","active":true},
    {"id":"O-003","label":"flat jsonl only","active":true}]
```

Booleans round-trip. Row-level writes land in the git-backed JSONL on disk:

```json
{"id":"O-002","decision_id":"D-001","label":"postgres","costs":"extra service + ops",
 "breaks":"seed's one-box rule","active":false}
```

## 5. Semantic retrieval

```
POST /api/memories {"key":"/sdm/D-001","namespace":"<ns>","domain":"concept",
                    "content":"Observation state lives in a single SQLite file. Chosen over
                    Postgres because the seed forbids extra services..."}
-> {"id":"c6d02160-...","key":"/sdm/D-001", ...}

GET /api/memories?namespace=<ns>&q=why did we reject postgres for storing state&limit=3
-> items[0] /sdm/D-001 score 1.0, items[1] /sdm/D-003 score 0.976, with highlighted snippets
```

Note: the write endpoint takes `content` (the field stored on disk is `embedding_text`); `q` is
served by embeddings with cosine scoring and a min-score floor.

## 6. JEV at `/api/alpha/decisions`

First key in `~/.hermes/.env` was **expired** (`401 API key expired`) — the failover across the
key set is not optional. Second attempt succeeded.

One request, four questions, `$0.0000274` total:

```json
{"model":"typesafe/jev-1.13-20260917",
 "answers":{
   "already_answered":{"type":"noul","noul":0.47},
   "confidence_in_evidence":{"type":"noul","noul":0.54},
   "completeness":{"type":"score","score":1.58,
      "legend":{"0":"nothing decided","1":"partial, major gaps","2":"mostly decided, minor gaps",
                "3":"decided enough to build","4":"complete and verified"},
      "probabilities":{"0":0,"1":0.57,"2":0.29,"3":0.14,"4":0},"confidence":0.51},
   "next_subject":{"type":"choice","choice":"deployment_and_alerts",
      "probabilities":{"retention_and_privacy":0.01,"testability":0.12,
                       "deployment_and_alerts":0.85,"concurrency_and_scaling":0.01,"none_needed":0.01},
      "confidence":0.82}},
 "usage":{"input_tokens":653,"output_tokens":130,"cost":2.7426e-05}}
```

The read is accurate: the probe project had its storage and durability decided and nothing about
boot survival or failure notification — and JEV put 0.85 on `deployment_and_alerts` as the next
subject, with completeness at "partial, major gaps".

All questions ride one request for one flat cost (ask 6, pay ~1).

## 7. Bugs the substrate work exposed (all fixed)

1. **Shell interpolation of JSON silently truncates.** An apostrophe in `"seed's one-box rule"`
   inside a single-quoted `curl -d '{...}'` terminated the string: the request produced no output
   at all and inserted nothing. Only `--data-binary @file` is reliable. `auger.py` sends bytes
   from memory via urllib and never builds a body as a string.
2. **A validator that has never failed has not been tested.** An early coverage check demanded
   exact `4.01`–`4.44` filenames while the skill's own headings used unpadded numbers — it
   false-failed correct work.
3. **Contradictory evidence poisons the confidence read.** `auger answer` first embedded
   "chose X. Rejected: X, Y, Z" — listing the *chosen* option as rejected. JEV then scored a
   genuinely-answered question `noul 0.11` (NOT ANSWERED). Fixed to record only the *other*
   options as rejected; the **same question then scored 0.78 (ALREADY ANSWERED)**. Verified
   before/after on the same question text.
