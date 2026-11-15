# AUG-153: spec_alignment guard rollout

## Scope and candidate census

The enabled scheduler project census was run with:

```text
sqlite3 ~/.hermes/coding-hermes/scheduler.db 'select name, workdir from projects where enabled=1'
```

The scan required a repository workdir containing `specs/*.xml` (the Auger corpus shape) and a registry/index under `specs/`. It found exactly one qualifying repository:

- `/home/kara/auger` — `specs/spec-graph.xml`, `specs/spec-verbs.xml`, `specs/verification.xml`, plus `specs/schema/auger-spec-1.0.xsd`; registry/index file: none named `registry` or `index`, but the three XML documents are the shipped corpus and `auger spec validate specs` is the corpus validator.

The other enabled scheduler workdirs had no `specs/*.xml` corpus and were skipped. No separate rows were filed because the acceptance scope says to skip repos without the Auger XML corpus; the scan result is recorded here and in the AUG-153 note.

## Configuration

Enabled in `/home/kara/auger/.gitreins/config.yaml` only (repo-local configuration; no global `core.hooksPath` change):

```yaml
spec_alignment:
  enabled: true
  command: "/home/kara/auger/.venv/bin/python /home/kara/auger/auger.py spec align"
  endpoint: ""
  spec_root: specs
  mode: block
  missing_checker: warn
  timeout: 30
  protocol_version: "1"
  min_checker_version: "1.0.0"
```

The command supplies the GitReins adapter's required `--repo-root`, `--spec-root`, `--protocol-version`, and `--diff-stdin` arguments. The checker reports protocol `1`, checker version `1.0.0`, and the `auger` checker family.

## Live proof

Proof was performed in isolated worktree `/home/kara/aug153-live` on branch `wt/AUG-153`, with the real pre-commit guard configuration. A scratch line was added to the staged `tests/test_spec_repository_root.py` change without a trace marker. The exact command was:

```text
gitreins guard --scope working-tree
```

The guard log was `/home/kara/aug153-live/.gitreins/logs/guard-20261115T202824.191360Z.log`. The decisive refusal was:

```text
[FAIL] spec_alignment  passed=false  exit_code=1
error: {"checker":"auger","counts":{"fail":2,"info":0,"warn":0},..."findings":[{"code":"trace-missing","evidence":".gitreins/config.yaml:1","severity":"fail",...},{"code":"trace-missing","evidence":"tests/test_spec_repository_root.py:1","severity":"fail",...}],..."status":"fail","version":"1.0.0"}
Tier 1 Guards: FAIL
```

The first run also exposed unrelated baseline failures (formatter and the live DuckBrain test URL) in the isolated worktree; the `spec_alignment` arm itself independently refused the untraced change, proving the requested live invocation.

A second run with a valid `ch:trace` marker produced the checker payload with `status":"pass"`, `counts.fail=0`, and `findings=[]`; the `spec_alignment` arm reported `passed=true`. The full test arm remained independently blocked by the missing `DUCKBRAIN_URL` in the isolated proof environment, so this document does not claim the unrelated full battery passed.

The bounded missing-checker probe completed within the 30-second command budget and returned `spec_alignment` with a warning (`incompatible checker protocol (expected 1)`) rather than hanging or crashing; the configured `missing_checker: warn` policy was honored.

## Delivery

The Auger-side config and this document are intended to be committed and pushed from `/home/kara/auger` with the required identity:

```text
env -u SCHEDULER_OPERATOR_RESTART_APPROVED git -c user.name=totalwindupflightsystems -c user.email=totalwindupflightsystems@gmail.com commit ...
```

AUG-153 remains open for the parent agent to close after final verification.
