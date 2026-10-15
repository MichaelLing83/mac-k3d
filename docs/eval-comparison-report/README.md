# Branch process: `feat/eval-comparison-report`

This folder is **branch process and re-test notes**, not the user start-here. User docs stay under [docs/](../) ([evaluation](../evaluation.md), [optimization](../optimization.md)). Which questions this worker has already run: [testing/question-coverage.md](../testing/question-coverage.md).

## What changed on this branch

1. **Comparison report** — P8 writes `summary.md` and `report.html` from the scored JSON. The harness arm is `icode`. The summary includes pass@k, macro pass@1, tokens, and duration.
2. **Same-question waves** — one question’s rollouts share the slots. A free slot waits while that question still has a container running. The next question starts only after those containers exit.
3. **Open slots** — `EVAL_SLOTS` stays at the number of cores the build locked (`CPU_LOCK_QTY`, all of the worker's on Jenkins). A measured peak is recorded and does not set a Docker memory cap or reduce the slot count. Free disk below 40 GB forces one slot. A question is skipped only after an out-of-memory kill in its Harbor log; finished rewards stay on disk and P8 still scores them.
4. **Per-question memory record** — P5 clears `harness/container_mem.jsonl` at the start of the build, then appends one row per finished question (`question`, `peak_gb`, `slots`, `memory_mb`, `benchmark`). P8 copies that file and `skipped_questions.txt` next to `artifact.json`. The same row plus the build number is appended to `harness/container_mem_history.jsonl` on the worker and is not copied into the report folder. `summary.md` names the max peak and the skipped ids.

## How to re-test

Step-by-step log: [testing.md](testing.md).
