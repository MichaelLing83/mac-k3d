# Re-test log (`feat/icode-tag-commit-release`)

Not a second user guide. User steps: [user-guide](../user-guide.md). Lab E0–E8 table: [testing-eval-pipeline](../testing/testing-eval-pipeline.md).

## Fixtures (no Jenkins)

```bash
bash pipeline/stages/test_icode_input.sh
bash pipeline/stages/test_p3_icode.sh
```

Expect: unnamed gzip named `ICODE_RELEASE_FILE` unpacks when `ICODE_RELEASE_UPLOADED=1`; unnamed stub copies as `icode`; empty upload does not fall back to persist; random non-archive still rejected without the flag.

```bash
cargo test --lib prepare::jenkins_job
```

Expect: XML has `ICODE_RELEASE_FILE` / `StashedFileParameterDefinition`; no `<name>ICODE_RELEASE</name>` string param; Jenkinsfile `--force-rm`s leftover `ICODE_RELEASE_FILE` when `ICODE_MODE=git`; release mode exports `ICODE_RELEASE_UPLOADED` and dies with `upload ICODE_RELEASE_FILE` when that file is empty.

## Live Jenkins release (product path)

1. Controller: `mac-k3d config --skip-secrets` after this CLI is installed.
2. Open `deepswe_one_task`, `lolbench_one_task`, or `swebenchpro_one_task` → **Build with Parameters**.
3. Set `ICODE_MODE=release`. Upload official `icode-*-full-*` (or standalone `icode`) as **ICODE_RELEASE_FILE**. Leave git controls as they are.
4. Build. Agent must be this worker.

Console lines to look for:

- `Jenkins upload ICODE_RELEASE_FILE (archive magic):` or `Jenkins upload (unnamed) as icode binary:`
- `resolving iCode (release)`
- `PROGRESS` lines through P0–P8
- **not** `OK iCode git kind=`
- **not** leftover `icode_git.json` / report `icode_git`

Failure without a file: `ICODE_MODE=release: upload ICODE_RELEASE_FILE on Jenkins Build with Parameters (not a worker path).`

## Live local path (not Jenkins)

```bash
mac-k3d eval --local --stage p3 --icode-mode release --icode-release /path/to/icode-*-full-* --yes
# or persist / discover:
mac-k3d set --icode-release /path/to/icode-*-full-*
mac-k3d eval --local --stage p3 --icode-mode release --yes
```

## Live git (Jenkins UI)

Product path: **Build with Parameters** on `deepswe_one_task`, `lolbench_one_task`, or `swebenchpro_one_task`. `mac-k3d eval --yes` only queues the same job.

1. `ICODE_MODE=git`. Fill `ICODE_GIT_URL` / `ICODE_GIT_REF` / `ICODE_GIT_REF_KIND` (`branch`, `tag`, `commit`, or `pr`). For `pr`, `ICODE_GIT_REF` is the pull-request number. Leave **ICODE_RELEASE_FILE** as it is.
2. Build on this worker.

Console: `OK iCode git kind=branch` (or `tag` / `commit` / `pr`); report includes `icode_git`. A leftover clone may print `removing leftover …/icode-src via docker` then continue — that is expected, not a failure.

## `--yes` release must not queue

```bash
mac-k3d eval --benchmark deepswe --icode-mode release --yes
```

Expect an error telling you to open `{jenkins}/job/deepswe_one_task/build` and upload `ICODE_RELEASE_FILE`. No GET `buildWithParameters` with a worker path.

## Live checks on this worker

Release upload, model `deepseek-flash`, one question, one rollout. `Finished: SUCCESS` means `reward.json` exists. The score is inside that file.

| Job | Build | Question | Result |
|-----|-------|----------|--------|
| `swebenchpro_one_task` | 16 | NodeBB `04998908…` | Reward 1.0. F2P 3/3. P2P 288/288. About 30 minutes. |
| `deepswe_one_task` | 21 | `abs-module-cache-flags` | Reward 1.0. F2P 20/20. P2P 3/3. About 5 minutes. |
| `lolbench_one_task` | 15 | `ruff_1` | Pipeline success. Reward 0. F2P 13/19 (0.684). P2P 51/51. About 16 minutes. |

LoLBench reward 0 is the model missing 6 of 19 new tests, not a missing artifact. Baseline `reward` stays null: that arm writes a patch and does not run the verifier.
