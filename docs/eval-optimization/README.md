# Branch process: `feat/eval-optimization`

This folder is **branch process and re-test notes**, not the user start-here. User docs stay under [docs/](../) ([evaluation](../evaluation.md), [optimization](../optimization.md)). Packing and same-question waves from the prior branch: [eval-comparison-report](../eval-comparison-report/README.md).

## What changed on this branch

1. **DeepSeek + high defaults** — Harbor and Pier set `ICODE_PROVIDER=DeepSeek`, `ICODE_REASONING_EFFORT=high`, `ICODE_API_BASE=https://api.deepseek.com/v1`, `ICODE_MAX_TOKENS=65536` and `ICODE_MAX_ITERATIONS=500` unless those env vars are already set. The two limits are the iCode developers' eval settings; without them iCode cuts each reply at 8192 output tokens. The catalog model id (`deepseek-flash` or `deepseek-v4-pro`) is unchanged. The artifact `model` field stays `openai/<catalog id>`; the real provider and reasoning effort live under `eval_protocol`.
2. **Force-commit after `icode run`** — the Harbor agent commits a dirty git tree (`icode solution`) so DeepSWE can grade `git diff <base> HEAD` and LoLBench submit sees the same tree. That commit does not start another model call.
3. **`eval_protocol` provenance** — P8 stores iCode mode/version/source, model params (`provider`, `reasoning_effort`, `thinking.type`), concurrency, `cpus_each`, and the timeout note on `artifact.json`, and repeats them in `summary.md` and `report.html`. `check_report.py` validates the object.
4. **LoLBench fix-rewards patch** — P2 runs `lolbench_fix_rewards.py` so Harbor LoLBench tasks get corrected reward wiring before the run.
5. **Active-question memory sampling** — P5 writes `harness/active_question.txt` for the live question. The memory sampler attributes `peak_gb` to that id instead of guessing from container names alone.
6. **Cost-token report CLI** — after a run, `python3 pipeline/lib/cost_token_report.py` writes `cost-token-report.md` from that run’s `artifact.json` (list-price estimate, not DeepSeek’s invoice). This is not part of P8.

## How to re-test

Step-by-step log: [testing.md](testing.md).
