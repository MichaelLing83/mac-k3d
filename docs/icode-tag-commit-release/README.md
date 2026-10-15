# Branch process: `feat/icode-tag-commit-release`

This folder is **branch process and re-test notes**, not the user start-here. User docs stay under [docs/](../) ([user-guide](../user-guide.md), [icode-harness-inputs](../icode-harness-inputs.md)). Lab E0–E8 table: [testing/testing-eval-pipeline.md](../testing/testing-eval-pipeline.md).

## What changed on this branch

1. **Git kinds** — Jenkins/CLI pick `branch` / `tag` / `commit` / `pr` (default `branch`). Kind `pr` takes the pull-request number and checks out that tip. Leftover `auto` still maps (7–40 hex → commit, else branch). P3 records `icode_git.json`; reports copy `icode_git`.
2. **No local source mode** — workers do not hold a developer checkout. `ICODE_MODE=source` is rejected. Inputs are git clone or a downloaded `*-full-*` / `icode` drop.
3. **Jenkins release = upload** — job XML has File Parameter `ICODE_RELEASE_FILE` (not a string worker path). **Release** mode exports `ICODE_RELEASE` + `ICODE_RELEASE_UPLOADED=1` when that file is non-empty. **Git** mode `--force-rm`s a leftover upload and ignores it. P3 unpacks gzip/tar by magic, else copies as `icode`. Empty release upload fails with a clear message.
4. **Git leftover clone** — P3 wipes `eval-runs/icode-src` before clone. Root/nobody files from a prior Pier/Harbor bind-mount are removed with Docker (no `sudo rm`). P5 chowns the bind-mount back to the agent user.
5. **Local CLI unchanged for paths** — `mac-k3d eval --local` / `--stage` still take `--icode-release PATH`, persist (`mac-k3d set --icode-release`), or `~/.local/share/mac-k3d/` discover.
6. **CLI `--yes` + release** — does **not** queue GET `buildWithParameters` (cannot attach a file). Prints: open Jenkins UI, set `ICODE_MODE=release`, upload `ICODE_RELEASE_FILE`. Git `--yes` still queues; product path is still **Build with Parameters**.
7. **SWE-bench Pro** — Jenkins job `swebenchpro_one_task` (`BENCHMARK=swebenchpro`). P2 materializes Harbor task dirs from the official instance list. P4 checks `icode_harbor_agent`. P5 runs Harbor against `jefzda/sweap-images`. The sweap image entrypoint is `/bin/bash`, so the task compose sets `entrypoint: ["sh", "-c", "sleep infinity"]` and the verifier uses `environment_mode = "shared"` so `/tests/test.sh` is uploaded into that container. Images are large; one `TASK` per build.
8. **One Harbor runner** — DeepSWE, LoLBench, and SWE-bench Pro all use `harbor run -a icode_harbor_agent:ICodeAgent`. Pier is no longer the eval runner. P1 installs Harbor for all three. A reward of 0 still writes `reward.json`; a missing file fails the stage.
9. **Score from this trial** — `c` / `n` / `pass_frac` come from this build’s `verifier/reward.json` files. `n_rollouts` is Jenkins `N_ROLLOUTS` (default 1). Harbor `-k` is the attempt count; `-n 1` is one container at a time. Baseline `reward` is null unless that arm wrote its own `reward.json` (a leftover `eval.json` does not count). `partial` is filled from F2P and P2P counts when the verifier omits it. Token totals prefer this trial’s iCode usage, including LoLBench.

## What a question’s numbers mean

One task is one question. F2P and P2P are that question’s own tests, not extra questions.

| Suite | What one question is | Example counted on this branch |
|-------|----------------------|--------------------------------|
| SWE-bench Pro | One bug, graded by the repo’s FAIL_TO_PASS and PASS_TO_PASS lists | NodeBB: 3 F2P and 288 P2P |
| DeepSWE | One Harbor task; reward 1 only when every F2P passes and no P2P fails | `abs-module-cache-flags`: 20 F2P and 3 P2P |
| LoLBench | One issue; union suite concatenates the original and augmented lists | `ruff_1`: 19 F2P and 51 P2P |

## How to re-test

1. Worker: extract pipeline (`mac-k3d eval --stage p0` or `mac-k3d config -c worker.yaml`).
2. Controller: rebuild/install CLI, then `mac-k3d config --skip-secrets` so job XML has `ICODE_RELEASE_FILE`.
3. Fixtures: `bash pipeline/stages/test_icode_input.sh` and `bash pipeline/stages/test_p3_icode.sh`.
4. Live Jenkins: **Build with Parameters** → `ICODE_MODE=release` → upload the official drop → look for console lines below.
5. Live git: same UI → `ICODE_MODE=git` → URL / ref / kind; leave the file picker as it is. Console: `OK iCode git kind=…`. Optional: `removing leftover … via docker`.

Step-by-step log: [testing.md](testing.md).
