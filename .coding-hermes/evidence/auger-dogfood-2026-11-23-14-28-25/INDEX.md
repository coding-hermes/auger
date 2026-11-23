# Evidence index — tick auger-dogfood-2026-11-23-14-28-25 (19th auger dogfood run)

Measured tree: main @ a8bf275 (= origin/main at run time). All artifacts are
raw CLI outputs captured on this host (warm and cold single-process timings in
the integration report, duplicated here for convenience).

| File | What it proves |
|---|---|
| consumer-corpus-example.xml | the scratch `<spec>` with one `<task plan="TT-PLAN">` block authored as a consumer (loader-accepted) |
| task-packet.json | `spec task-packet` output: scope, acceptance, exclusions with reason, packet digest |
| plan-draft.json | `spec plan-draft` output: plan digest 3c4b3a6e…, counts |
| plan-approve-stale-digest.err | rc=2 typed refusal `stale-plan-digest` (wrong digest refused) |
| plan-approve-ok.out | rc=0 approval recorded, record echoed |
| plan-approvals-ledger.jsonl | append-only ledger file created under `<spec root>/.auger/` (note: empty `at` field when `--at` omitted — folded into AUG-206) |
| plan-export.out | rc=0 board row TT-TASK-1 created via boardctl, acceptance criteria verbatim, ch:trace marker in reasoning, read-back shown |
| judge-packet.out | rc=0 judge packet: id_mapping, resolved ch_trace (spec/test refs), per-block digests, verbatim board acceptance criteria |
| spec-context.json | `spec context --task` selection with exclusion reason and byte counts |
| brief-spec-context-section.md | `brief_spec_context.py` emitted brief section (AUG-165) |
| brief-unknown-row.err | unknown row → zero bytes + one honest stderr line |
| task-export-unapproved-digest.err | rc=2 `unapproved-plan-digest` (fail-closed) |
| task-packet-unknown-task.err | rc=2 `unknown-task` typed refusal |
| align-unsupported-protocol.err | rc=2 `unsupported-protocol-version` |
| align-diff-pass.json | `spec align` on real staged diff 5157273: status=pass, scope=scoped, 0.15 s |
| align-semantic.json | `spec align --semantic` same diff: status=pass, 0 findings, 0.12 s |
| as-of-unknown-ref.err | rc=2 `as-of-unknown-ref` |
| as-of-corpus-missing.err | rc=2 `as-of-corpus-missing` for --as-of v0.3.0 (specs/ did not exist at that tag) |
| 2026-11-23-spec-to-build-loop-integration.md | copy of the committed integration report |

Byte-identity proofs (run outputs, not files): `spec list --as-of HEAD` ==
plain selection (`cmp` clean); `spec render` twice byte-identical.
Install battery: agent 5e0a4289 @ bunker-mvp launched with --keep, evidence
/tmp/bunker-qa-evidence-20261123T144326Z-3485089.jsonl (sha256 ca1c6577…),
harvest recorded in .coding-hermes/dogfood-log.md entry of this fire.
