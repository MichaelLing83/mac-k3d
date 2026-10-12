# Re-test log (`int/PF-harbor-delegation`)

Not a second user guide. Field meanings: [evaluation](../evaluation.md). Job parameters: [commands](../commands.md). Unrun question ids: [question-coverage](../testing/question-coverage.md).

## Fixtures (no Jenkins)

```bash
cargo test
python3 -m unittest discover -s pipeline/lib -p 'test_*.py'
for f in $(git ls-files 'pipeline/*.sh' 'scripts/*.sh'); do bash -n "$f"; done
scripts/check_docs.sh
```

The cases that cover this branch:

- `every_suite_gets_exactly_three_jobs` / `shapes_get_their_own_defaults` — nine eval jobs and no `eval_aggregate`; `one_task` pins 1 question x 1 rollout, `some_task` takes the configured list or N, `full_suite_task` the whole suite
- `some_task_dispatches_to_one_task` / `full_suite_dispatches_shards_then_aggregates` — the dispatchers take no executor and no Docker until the merge; they queue one `<suite>_one_task` build per question and rollout (`MAX_IN_FLIGHT`) and merge the final build of each unit in their own `Aggregate` stage by child build number
- `each_shape_shows_only_its_params` / `user_profile_hides_dev_params` / `developer_profile_shows_dev_params` / `harness_llm_benchmark_always_visible` / `groovy_and_xml_param_sets_match` — the per-shape parameter lists, the `ui_profile` switch, and Groovy and XML rendering from one table
- `agent_registers_with_one_executor` — one eval build per worker
- `bootstrap_extracts_pipeline_from_installed_binary` / `no_mac_k3d_git_params` — every phase uses `mac-k3d pipeline --extract-to $WORKSPACE/mac-k3d-pipeline --require-clean`, only the first stage (Environment) clears it, and no job carries a `MAC_K3D_GIT_*` parameter, a `git fetch` or a `mac-k3d-src` clone
- `extract_writes_build_json` / `require_clean_refuses_dirty_build` / `version_string_has_commit` / `pipeline_extract_needs_no_config` — `BUILD.json` carries the baked commit, dirty flag and pipeline hash; a dirty or unknown build is refused; `--version` names the commit
- `agent_restart_only_when_jar_or_script_changed` — `agent.jar` is swapped by rename only when its bytes differ, and the agent restarts only when the jar or launch script changed
- `test_pipeline_facts_reads_build_json` / `test_pipeline_facts_falls_back_to_a_git_checkout` / `test_shards_from_different_pipeline_builds_are_marked_mixed` — `eval_protocol.pipeline` comes from `BUILD.json` (`source: binary`) or git (`source: checkout`); an aggregate of two different builds says `pipeline_status: mixed`
- `test_report_renders_when_the_task_declares_float_cpus` / `test_render_report_accepts_a_float_cpus_each` / `test_a_whole_float_cpu_count_is_an_int` — build #51's report crash: `cpus = 2.0` becomes `cpus_each: 2`
- `eval_job_runs_one_stage_per_phase_and_locks_only_evaluate` — seven stages in phase order, each running `run_all.sh` with its `MAC_K3D_PHASE`, and exactly one `lock(label: env.NODE_NAME, variable: 'HELD_CORES')` with no quantity, inside `Evaluate`
- `test_harbor_run_uses_import_path_not_bare_agent_icode` / `test_shared_harbor_command_for_every_benchmark` — exactly one `harbor run` per build, carrying `-k $N_ROLLOUTS` and `-n $EVAL_SLOTS`
- `pipeline/lib/test_task_resources.py` (17 cases) — declared cpus/memory/storage are read from `task.toml`, slots are planned from them, and a worker that cannot fit one task fails early
- `pipeline/lib/test_aggregate.py` (21 cases) — two shards' trials merge into one artifact with `tasks x rollouts` records; verdict counts add; mixed pipeline versions are flagged, not averaged; an empty patch keeps its F2P, and its P2P is kept as the grader's counts (`grader_p2p_pass` / `grader_p2p_total`) but not averaged; `eval_protocol.shards` keeps every shard's worker, resources, model params and iCode version, the merged protocol comes from the shard's own report, and shards with different `max_tokens` are `model_params_status: mixed` with a WARNING
- `pipeline/lib/test_empty_patch.py` (23 cases) — an empty patch's cause (`infra` unscored, `cut_off`, `no_edit`), provider-error markers only from iCode's error lines and JSON `error`, `cut_off_reply` against the run's `max_tokens`, notes as `empty model.patch (k/n)`, the `excluded (grader k/n)` P2P cell, and the Empty patch section in `summary.md` and `report.html`
- `test_a_trial_that_started_off_the_declared_base_is_flagged` — the `anticheat` phase compares each receipt's base SHA against `task.toml` on the host, since the agent env no longer carries it
- `test_parallel_degree_does_not_guess_when_evaluate_never_ran` — the report phases read the plan `evaluate/slots` wrote instead of re-probing the machine
- `pipeline/lib/test_pipeline_layout.py` — every phase and step exists and is listed; only evaluate steps source `harbor_cmd.sh`; Harbor flags and `harbor run` appear only there; no literal agent host in a script; no `pN_*.sh` name anywhere; an unknown phase is refused; `CANARY=only` skips the later phases; `archive` needs a report and carries the cost analysis
- `phases_match_run_all_and_are_embedded` / `stage_takes_phase_names` / `old_stage_names_point_at_their_phase` — the Rust phase list equals `run_all.sh`, and `eval --stage p5` names its replacement

## Live Jenkins: the commit, push, redeploy loop

Every test names a commit, so a bad change is revertible and a good result is reproducible.

1. Commit, push, and put that build on every host:

   ```bash
   git commit -am "fix: <what>"
   git push
   bash scripts/redeploy.sh --controller <user>@<controller> --worker <user>@<worker>
   ```

   It must end with `All hosts run mac-k3d 0.5.2 (<sha>).` and each worker's `config` should say `Jenkins agent unchanged, left running` unless the controller's `agent.jar` changed.
2. Open `deepswe_one_task` -> **Build with Parameters**. There is no pipeline field: the build runs the worker's binary.
3. To re-run an exact past build, check out the `pipeline.commit` from that build's `artifact.json`, redeploy, and rebuild. Every build refuses a dirty binary (`--require-clean`).

Console signals, in order:

- `mac-k3d pipeline <sha> (mac-k3d 0.5.2, /home/<user>/.local/bin/mac-k3d)` in the Environment stage — the SHA actually under test
- `PROGRESS 45% evaluate: canary + harbor run (holding $CPU_LOCK_QTY cores on $NODE_NAME)` — the lock is per node
- one `harbor run` line, with `-k` equal to `N_ROLLOUTS` and `-n` equal to the planned slots
- `declared cpus=2 memory_mb=8192` (DeepSWE) or `cpus=4` (LoLBench) and **no** `--override-cpus`
- after the Report stage, `artifact.json` has `eval_protocol.pipeline` = `{source: binary, version, commit, dirty, pipeline_hash}`, `cpus_each: 2` on DeepSWE, and `resources.declared` vs `resources.applied`

## Live Jenkins: the two-worker proof

The run that has to pass before anyone else uses this.

1. `bash scripts/redeploy.sh --controller <user>@<controller> --worker <user>@<worker> --start` (`--start` once, for the plugins: it Helm-upgrades Jenkins with `copyartifact`, `pipeline-utility-steps` and `hidden-parameter`, rewrites the nine jobs and deletes `eval_aggregate`). Do not install plugins in the UI. Workers stay on `config` only — never `start -c worker.yaml`.
2. Both workers on the same binary (the script's version table) and online in **Manage Jenkins -> Nodes** with **1 executor** each.
3. Open `deepswe_full_suite_task` -> **Build with Parameters**. Set `N_TASKS=10` and `N_ROLLOUTS=1` (ten builds, one per question).
4. While it runs, check:
   - two `deepswe_one_task` builds run at once, one per worker, and the other three wait in the queue; as each finishes, its worker takes the next
   - each shard build's description reads `shard i/5 of deepswe_full_suite_task #<n>`
   - **Lockable Resources** shows every `<agent>-core-N` of a busy worker held by that worker's build, and no build holding a token whose prefix is not its own node
   - each shard's console prints a different `TASK_OFFSET`, and the five `selected_tasks.txt` files are disjoint
5. When the dispatcher's `Aggregate` stage finishes, open `aggregate/artifact.json` on the `deepswe_full_suite_task` build and confirm `shards: 5`, `n_tasks: 10`, `pipeline_status: same`, that the rollout records total `10 x N_ROLLOUTS`, that `eval_protocol.shards` names both workers with `model_params_status: same`, and that `summary.md` prints one `Worker (shard N ...)` line per shard.
6. Record the build numbers and the outcome as a new row in [../integration-log.md](../integration-log.md).

## Status

Fixtures: **green** (141 Rust unit + 19 CLI, 242 Python, 2026-10-06 after the phase refactor). Live two-worker proof: **not yet run** — it needs the second worker (`toby@47.84.16.18`) online, so it is the one item on this branch I have not verified myself. `scripts/redeploy.sh` was exercised only against a fake `ssh`/`scp`.
