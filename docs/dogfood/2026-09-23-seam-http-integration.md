# auger dogfood integration report — 2026-09-23 (4th run: the ask→answer seam live, and the HTTP data layer as an integrator)

Two untouched angles after three prior runs: (a) the AUG-035 seam fix was in the
tree uncommitted — this run drove the loop the seam unblocks LIVE, end to end;
(b) no run had consumed auger's data through DuckBrain's HTTP API the way an
integrator would — the README's promise "queryable over HTTP, and searchable by
embedding … the spec is versioned in git" had never been user-tested.

## The run

Scratch namespace `dogfood-auger-20260923e`, control host, live DuckBrain
(apikey auth) + live JEV. A real micro-spec: a fleet log-triage CLI (retention,
state store, alerts channel, error codes).

- init --seed-domains: 0.3s, 14/14 tables, 44/44 domains.
- start: 0.08s. answer ×2: 0.1s each. status: 2.2s warm (σ 1.3 — see costs).
- ask: 6-8s, fail-closed store refusal printed correctly.
- feedback: proposed a question from the thin decision D-003 (0.3), JEV refused
  it as already-answered (noul 0.68) — and feedback RECORDED the refusal as a
  question row (Q-000001, qclass follow_up). Edges E-000001/2 written.
- answer --question-id Q-000001: worked — `closed Q-000001` printed, status
  updated, propagate clean. **The README's central loop now completes by hand.**

### The ask→answer seam itself (AUG-035, live)

Ran `ask` five times across the session. EVERY proposal was refused by the
gate: confidences 0.23/0.19/0.24/0.17/0.18, all below T_SUBJECT=0.45. The
fail-closed behavior is exactly to spec and each refusal prints its reason —
but the practical result is that **the seam the fix exists to provide has
never fired in live use**: the only JEV-proposed question that ever reached the
table in this run came from `feedback`, not `ask`. The proposed "question" is
also just the criterion slug (`how_does_it_fail`), not a sentence — had it
passed the gate, a slug would be the stored text. Findings AUG-041/AUG-042
below.

## The HTTP data layer (integrator view)

Written against `~/duckbrain` feat/native-s3, token auth, standard urllib —
no auger code, following only the README's data-location section:

- GET /api/ns/<ns>/tables → 200, full declared schema, 14 tables. ✅
- GET /tables/<t>?id=eq.D-001 / confidence=gt.0.85&order=confidence.desc /
  ring_floor=gte.5&order=num.asc&limit=5 → all 200, correct rows. ✅
- `text=like.%25canonical%25` LIKE filter → 200, correct. ✅
- POST question row → 201 `{"inserted":1}`; DELETE ?pk= → 200; readbacks
  correct; DELETE then re-read clean. ✅
- PATCH ?pk=eq.D-002 → 200 … **but the row was unchanged** (0.82 stayed 0.82;
  the disk JSONL never carried 0.88). A 200 on a write that wrote nothing —
  the same silent-no-op class as AUG-036, one layer down. AUG-043.
- `count=exact` / `count` params → 200 but **no Content-Range and no count
  anywhere in the body**; pagination params silently ignored. AUG-042.
- PATCH/DELETE without `?pk=` → 400 with a precise error naming the required
  filter shape. ✅ (good error design)
- GET bad-typed filter (`confidence=gt.banana`) → **500 INTERNAL_ERROR**, not
  400 with a message. AUG-044.
- POST a row with NO `id` → **201 accepted**, row stored with
  `id: null` — and the namespace's declared primary key is `id`. A keyless row
  is then unreachable through the API (every PATCH/DELETE requires `pk=eq.<v>`
  and there is no value) and had to be removed by editing the JSONL directly.
  AUG-045.
- The embedding store is NOT reachable over HTTP: `/memories` → 404
  ROUTE_NOT_FOUND and `memories` is not among the 14 declared tables. So the
  README's "searchable by embedding" is true only through auger's own verbs,
  not through the substrate's HTTP API an integrator would reach for. AUG-046.

### The git-versioned promise is false as shipped

README: "the spec is **versioned in git**, readable as plain files, queryable
over HTTP, and searchable by embedding — and the dump is a view of it." Verified
against the substrate repo: `namespaces/` is **gitignored in duckbrain itself**
(`/namespaces/`, root-anchored), the JSONL is not tracked, and there is no
snapshot/commit machinery running (`_audit/current.jsonl` is an append audit
log, not versioning). Nothing about auger's storage of record is in git. The
claim may describe the DuckBrain S3 branch's design intent, but a user checking
`git status` in ~/duckbrain sees their spec is untracked local files. AUG-044
(class: promise-vs-reality, docs).

## Costs measured (hyperfine, quiet-ish box, σ high from fleet load)

- `auger dump`: 191ms ± 196ms warm (min 93ms); `status`: 2.2s ± 1.3s warm —
  `status` is ~10× dump and the variance (1.1-4.3s) is real, not noise, on a
  4-decision namespace. Not worth a PERF row (a coverage report every few
  seconds is fine), recorded here as the number.
- `ask`: 6.5-8s per call (JEV round-trip, ~$0.00004); `check`: 1.7-14.2s
  (embedding + JEV); `recall` warm: 0.6-1.6s; `feedback`: 8.7s.
- HTTP probes: all sub-50ms.
- Cold-start re-check: no cold penalty found beyond fleet-load variance
  (AUG-032 recall cold-start row remains the only perf finding worth a row).

## Verdict

🟡 PROMISING-BUT-ROUGH — narrowed this run to 🟢-leaning on the CLI: the
documented ask→answer loop now COMPLETES (feedback → question row → answer
--question-id → closed, propagate clean), the read/derive verbs are excellent
and fast, and the verdict register is genuinely useful (JEV judged my what-if
BAD twice, on the record, with reasons). What keeps it rough: the AUG-035 seam
itself never fires live (every JEV proposal below T_SUBJECT, and the proposal
is a criterion slug, not a question), and the substrate layer under the
README's own promises has silent no-ops (PATCH 200-noop), 500s on bad typed
filters, keyless-row acceptance, no count/pagination, no HTTP path to the
embedding store — and "versioned in git" is false as shipped.

## Findings filed

- AUG-041 (P1): ask→store seam never fires live — 5/5 proposals below T_SUBJECT
  (0.15-0.24 vs 0.45); the fix's own gate may be unsatisfiable in practice.
- AUG-042 (P2): ask stores the JEV criterion SLUG, not a question sentence.
- AUG-043 (P2): DuckBrain PATCH ?pk= returns 200 and writes NOTHING.
- AUG-044 (P2): README/DESIGN "versioned in git" is false — namespaces/ is
  gitignored in duckbrain; bad typed filter → 500 INTERNAL_ERROR.
- AUG-045 (P2): POST without id accepted (201) → keyless row unreachable via
  API (no pk), only fixable by hand-editing JSONL.
- AUG-046 (P2): embedding store not exposed over HTTP ("searchable by
  embedding" only via auger verbs).
- AUG-047 (P3): status ~10× slower than dump (2.2s vs 0.19s warm, high σ);
  number recorded, not a perf row.