# auger dogfood integration report — 2026-09-23 (registers + ask→answer seam)

Third real-use run. Run 1 (09-22) proved the entry surface; run 2 (09-23 morning)
proved graph/bundles/verdict. This run took the surface NEITHER touched: the
**register surface** — can a real user record a `break`, an `assumption`, an
`unknown`, or an escalation without hand-writing HTTP? — plus the **ask→answer
seam** (the loop the README draws as its centerpiece: `ask` names the question,
`answer` records the decision and closes it).

The scenario: a genuine decision drill for a real problem — the N100 host's
backup rotation policy (what is backed up, how often, how many generations,
where, and how restore-readiness is proven). Scratch namespace
`auger-df-registers` on the production DuckBrain, JEV live.

## What WORKS (proven live at merge 8210d38)

- **init 3s, 14/14 tables, 44-domain grid seeded.** `start` embeds the seed in
  <1s. `answer` records a decision with its rejected alternatives in 1-2s.
- **`check` is sharp.** "Should the restore drill be automated or manual?" →
  ALREADY ANSWERED (noul 0.92, threshold 0.55); "What database engine should
  the web endpoint use?" → NOT YET ANSWERED (noul 0.02). 7-10s each, $0.00004.
- **The what-if engine is honest.** `dump --config D-002=D-002-O2` rendered the
  hypothesis, named every contradiction with the recorded reasons (the
  why-not text for the rejected rolling window came back quoted), and wrote
  nothing.
- **`verdict --ask-jev` judged the hypothesis BAD at noul 0.07** — the model
  saw the incoherence (rolling-30-day contradicts the recorded why-not) that
  the engine does not flag. Recorded V-000001 with the config it judged. 2s,
  $0.00006.
- **`feedback` is truthful:** no thin decisions → "proposed: 0", nothing invented.
- **`recall`** finds the right decision at 0.99 for a natural-language query, 4s warm.
- **`propagate`** runs the gate silently and correctly on a graph with nothing to moot (1s).
- **Fresh-box install leg re-run post-AUG-033** (bunker las-bunker-03, agent
  4b004b7a, destroyed): README substrate bootstrap (npm user-prefix pnpm →
  build 72s) is now accurate; daemon boots clean with `mkdir -p ~/dd-data`;
  the full drill loop (init→start→answer→status) works; smoke = 19/21 on the
  bare box (both fails = missing OpenRouter key, a documented requirement —
  control host at the same commit: 21/21). See AUG-039 for the one README gap
  that still blocks a fresh user.

## The findings (full rows on the board: AUG-034…039)

**AUG-034 (P1) — `answer --question-id` refusal is not atomic; the retry
duplicates the decision.** The design comment says the refusal is BY DESIGN
("a link to nothing reads as evidence that a question was answered") — correct
— but `cmd_answer` inserts the decision + options BEFORE
`close_question()` raises. The user sees "refused", assumes nothing happened,
re-runs without `--question-id`, and now two `D-001` rows exist (the option
table takes the count to 6). Every downstream render then duplicates: `dump`
printed the D-001 block twice, and "ACTIVE CONFIGURATION" and its
contradictions each ×4 after the toggle. A decision id is evidently not
unique-keyed, so nothing stopped the second insert. Live repro in
`auger-df-registers` (D-001 ×2, D-004 partial). Fix direction: validate the
question BEFORE the decision insert (one `node_exists` call earlier), and a
unique key on decision.id.

**AUG-035 (P1) — the ask→answer seam is broken: `ask` never persists the
question it proposes.** The README loop is `ask` → "JEV says what to drill
next" → `answer`. In use: `ask` printed "next question: how_is_it_tested
(0.73)" and stored NOTHING (the `question` table was empty after the call —
verified over the HTTP API); `answer --question-id Q-how_is_it_tested`
refused; no verb exists to store the question, so the answer can never close
the question ask proposed. The only writer of question rows is `feedback`,
which fires only on thin decisions (<0.6). A user following the README's own
loop cannot complete its central move — recording a decision AS the answer to
the question the engine proposed — except by hand-writing HTTP. (Same
refusal-shape family as AUG-034 but a different defect: here the write the
user needs does not exist anywhere.)

**AUG-036 (P2) — `toggle` reports success while writing nothing.**
`toggle --on O2` printed "toggled: O2->on (0)" and exited 0; the "(0)" (zero
rows patched) is the only hint. `dump` then showed "nothing active". The full
id form `D-001-O2` works ("(2)" — and the ×4 dupes from AUG-034). A bare
id must either resolve (document says nothing) or be refused by name; rc=0 +
"toggled" is a silent no-op that looks like a write.

**AUG-037 (P2) — `dump --config` help advertises a form the parser rejects.**
`dump --help` says `--config D-001=O2`; the real parser requires the full
option id (`D-001=D-001-O2`) and ignores the documented shorthand as an
"unparsed entry" — at rc=0. (The 09-23 run 2 report already corrected this
once — "re-checked: the flip is right" — for a full id; the SHORTHAND form is
what the help text promises.) Fix direction: parse the documented shorthand,
or fix the help string.

**AUG-038 (P2) — `answer --domain` accepts anything; no verb maps domain
names to numbers.** `--domain 4.99` recorded silently; and I recorded a real
encryption decision on 4.27 believing "controls" — 4.27 is *audio*. The grid
names are in `status` (with a one-index-off display bug — D-002 on 4.05
showed under the 4.27 row, so the mapping a user reads is wrong twice over:
once as display, once as acceptance). Fix direction: validate `--domain`
against the declared grid (or at least the 44-row namespace map) and print
the name in `answer`'s confirmation; fix the off-by-one display.

**AUG-039 (P2) — the README's token claim is false on a fresh box.**
"Requirements: a running DuckBrain on 127.0.0.1:3000 … with its token at
`~/.duckbrain/foreman-status.token`" — no such file is created by a fresh
boot (verified on the bunker: `~/.duckbrain/` does not exist after boot; the
daemon's auth default is `none`; keys live in `~/.duckbrain/auth.json` which
is only consulted in apikey mode). The fresh user's first auger command exits
"no DuckBrain token: set DUCKBRAIN_API_KEY or ~/.duckbrain/foreman-status.token"
and there is no documented step that creates either. `DUCKBRAIN_API_KEY=dummy`
works (auth=none passes anything through) — but that is undiscoverable from
the docs. Fix direction: README one-liner ("set `DUCKBRAIN_API_KEY` to any
value while DuckBrain runs with auth=none; create a real key via …" —
whatever the substrate's documented key bootstrap is).

**Registers verdict (the run's headline question):** still unreachable. The
four registers (`break`, `assumption`, `unknown`, `escalation`) have schema
(COLS) and readers (`status` counts unknowns, `ask` reads them) but NO CLI
write path — AUG-019 (open since the first sweep) says exactly this, and this
run hit it as a USER: my seed contained a real unknown ("restore has never
been tested") and the loop offered no way to record it; `ask` reported
"unknowns: 0". AUG-028's merged `answer --invalidates` gives breaks an EDGE
write path (a real improvement, verified in the diff), but the register ROWS
themselves still cannot be created by any verb. AUG-019 remains the owning
row; this run is its second confirming field-report.

## Verdict

🟡 PROMISING-BUT-ROUGH on the registers + seam surface. The read/derive verbs
(check/status/dump/recall/propagate/verdict/feedback) are excellent — fast,
honest, fail-closed, and the verdict-what-if flow genuinely works. The write
surface is where a real session breaks: the loop's own ask→answer seam cannot
complete (AUG-035), a documented refusal leaves half a write behind (AUG-034),
and two CLI contracts differ from their help text (AUG-036/037). None of it is
architectural — each is a small, local fix — which is why this is rough, not
broken.

## Costs measured (real use, control host)

- init (with 44-domain seed): 3s cold
- start (seed embed): <1s · answer: 1-2s · toggle/dump/propagate/feedback: ≤2s
- check: 7-10s (embedding + JEV round-trips, $0.000038/call) · ask: 8s (same shape)
- recall warm: 4s — AUG-032's cold-start row remains the only perf finding
  (re-confirmed here warm; nothing else was slow enough to be worth a row)
- fresh-box substrate build: 72s (README claims ~76s — accurate)
- fresh-box smoke: 6s, 19/21 (both fails = missing OpenRouter key, documented)
- install total (clone ×2 → smoke green): ~6 min, of which 4.5 min is substrate
  `pnpm install && pnpm build` — already documented from the 09-23 morning leg