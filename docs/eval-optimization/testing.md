# Re-test log (`feat/eval-optimization`)

Not a second user guide. Field meanings: [evaluation](../evaluation.md). Packing rules: [optimization](../optimization.md). Unrun question ids: [question-coverage](../testing/question-coverage.md).

## Fixtures (no Jenkins)

```bash
python3 -m unittest pipeline/lib/test_report.py
```

The cases that cover this branch:

- `test_p5_uses_import_path_not_bare_agent_icode` / `test_shared_harbor_command_for_every_benchmark` — DeepSeek provider, `ICODE_REASONING_EFFORT=high`, and force-commit before LoLBench submit
- `test_parallel_degree_four_lock_is_four_slots` — `build_eval_protocol` fills provider, reasoning effort, concurrency, and `cpus_each`; P5 writes `active_question.txt`
- `test_question_memory_record_covers_every_benchmark` — active-question sampler attributes peak mem to the live id
- `test_lolbench_fix_rewards_patcher` / `test_p2_lolbench_applies_fix_rewards` — LoLBench reward patch in P2
- `CostTokenReportTests.test_render_and_build_locator` — cost markdown totals and `--suite` / `--build` run locator

## Live Jenkins

1. Worker already has this CLI. The job’s Prepare stage runs `mac-k3d eval --sync-pipeline` once. Do not rebuild or sync again while the build is inside P5. Bash reads `p5_harness.sh` as it runs; replacing that file mid-build exits 127.
2. Open `deepswe_some_task` (or `lolbench_some_task`) → **Build with Parameters**.
3. Paste the next TASKS line from [question-coverage](../testing/question-coverage.md) into **TASKS**. Set **N_ROLLOUTS=4**. Leave model at the catalog default (`deepseek-flash`).

Console / artifact signals for this branch:

- P5 passes `ICODE_PROVIDER=DeepSeek` and `ICODE_REASONING_EFFORT=high` into Harbor
- `harness/active_question.txt` tracks the live question during a wave
- after P8, `artifact.json` has `eval_protocol` with `provider`, `reasoning_effort`, `thinking.type`, concurrency, and `cpus_each`
- `summary.md` / `report.html` repeat that protocol block

After P8, optional cost report (not archived by Jenkins unless you copy it in):

```bash
python3 pipeline/lib/cost_token_report.py --suite deepswe --build <BUILD_NUMBER>
```

Default output is `<run-dir>/cost-token-report.md`.
