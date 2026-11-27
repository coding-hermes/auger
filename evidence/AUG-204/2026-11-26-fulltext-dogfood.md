# CHD full-text dogfood feedback — SG11 / AUG-204

**Status: owner-requested full-text coverage NOT complete.** Measured against Auger
`b993d358a310232eb5f052e03cbf230269c56cd2`. This is a scope escalation, not a
claim that the old AUG-204 AC1 was violated: its explicit rule classifies all
47 audit documents as provenance-only. The newer owner request covers the full
CHD documents; a filename, hash and attachment note do not make that text
retrievable. Preserve the older AC wording and historical verdict; extend the
existing SG11/AUG-204 execution path to resolve this mismatch.

## Measured read-only evidence

Freshly generated scratch corpus and original upstream source: 648 source Markdown
paths, 648 manifest paths, no missing or extra paths. Classes: 111 first-class,
411 unique handbook renders, 15 duplicate-of-canonical, 64 duplicate-of-handbook,
47 audit-provenance. The 522 XML documents have 7,314 section blocks; concatenating
sections per rendered document and comparing with its source after whitespace
normalization yielded zero mismatches. This compares rendered source documents;
it does **not** prove every duplicate pointer's content, nor audit text coverage,
byte identity, retrieval, or registration.

The independent authored CHD service corpus at `/home/kara/chd-service/specs/auger`
contained 337 verification elements and zero `runner`, `path`, or `selector`
attributes. This is an attribute census, not a finding that every verification is
supposed to be machine-executed. Service checkout HEAD was
`df2b6b535cfa54ba6e8bc7c33876e5cb8a636f7e`; its tree was clean.

The historical `08-battery-full-corpus.txt` and `09-battery-summary.txt` record
7/12 top-five target hits and zero registration failures, passing the **old**
7/12 baseline, not full-text completeness. This is not a new live recall run.
Five misses and their targets are in the summary. Registration is not equivalent
to an independent per-key readback. Historical tier-1 failure is not a current
passing gate. No registration or namespace mutation was performed for this audit.

## Reproduce without writing upstream or live substrate

From Auger root, select an empty scratch path under `$HOME/.hermes/cache/scratch`:

```sh
python3 scripts/ingest_markdown_specs.py coding-hermes-director --out "$HOME/.hermes/cache/scratch/chd-feedback-repro"
python3 evidence/AUG-204/chd-fulltext-audit.py "$HOME/.hermes/cache/scratch/chd-feedback-repro" /home/kara/specs/coding-hermes-director /home/kara/chd-service/specs/auger
```

The source tree is outside Git; results depend on its current contents. The
captured output and manifest hashes in `2026-11-26-fulltext-audit.txt` pin this
observation but do not make the upstream source immutable. The script compares
against `source_abs` from the generated manifest: use the named source tree and
inspect paths before comparing if the converter is reconfigured.

## SG11 driver handoff / acceptance delta

1. Reconcile the newer full-document request with the old audit exclusion, retaining
   deduplication and provenance; do not rewrite source documents or silently change
   the historical baseline. Render/index all 47 audit bodies or establish an
   owner-approved alternative that makes their actual text available to the same
   retrieval workflow. Show per-path coverage, no dangling pointers, and normalized
   source-to-retrievable-text parity, including negative/mutation controls.
2. Run an isolated fresh-prefix/namespace registration with readback and a
   representative audit-inclusive target battery. Report all queries and misses;
   distinguish the historical 7/12 baseline from the new acceptance decision.
   Do not repeat registration into a shared prefix (AUG-181).
3. Audit the 337 authored verification elements against their declared methods;
   add executable bindings only where appropriate, and explicitly enumerate
   analysis/manual obligations separately. Do not equate element count with tests.
4. SG11 driver cron `c524df93bdca`, goal `auger-xml-hardening` and existing
   `AUG-204` own follow-through; related `AUG-178` owns recall battery and
   `AUG-181` owns idempotent registration. No duplicate row is proposed. Keep
   AUG-204 pending until its existing gate/verdict and the new owner scope are
   reconciled and proved. Avoid overlapping broad substrate tests.
