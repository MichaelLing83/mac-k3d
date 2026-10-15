# Re-test log (`feat/eval-comparison-report`)

Not a second user guide. Field meanings: [evaluation](../evaluation.md). Packing rules: [optimization](../optimization.md). Unrun question ids: [question-coverage](../testing/question-coverage.md).

## Fixtures (no Jenkins)

```bash
python3 -m unittest pipeline/lib/test_report.py
```

The cases that cover this branch:

- `test_parallel_degree_four_lock_is_four_slots` — four open slots and no Docker memory cap
- `test_work_unit_finishes_one_question_before_the_next` — one question’s rollouts finish before the next question starts
- `test_question_memory_record_covers_every_benchmark` — `container_mem.jsonl` for DeepSWE, LoLBench, and SWE-bench Pro

## Live Jenkins

1. Worker already has this CLI. The job’s Prepare stage runs `mac-k3d eval --sync-pipeline` once. Do not rebuild or sync again while the build is inside P5. Bash reads `p5_harness.sh` as it runs; replacing that file mid-build exits 127.
2. Open `deepswe_some_task` → **Build with Parameters**.
3. Paste the **Next DeepSWE TASKS** line from [question-coverage](../testing/question-coverage.md) into **TASKS**. Set **N_ROLLOUTS=4**.

Console lines that show this branch:

- `P5 harbor: one question's rollouts together`
- the first wave is attempts 1–4 of the same question
- `OK unit=<question>:<attempt>`
- `P5 harbor: question=<id> rollouts=4/4 time=...` before the next question id

After P8, copy `peak_gb`, `slots`, and `memory_mb` from that run’s `container_mem.jsonl` into [question-coverage](../testing/question-coverage.md) and set those ids to `run`. Do not read `harness/container_mem_history.jsonl` for that table. A skipped id stays `not run`.
