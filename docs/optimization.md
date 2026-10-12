# Eval resource packing

Harbor applies each task's own `cpus` / `memory_mb` / `storage_mb`. mac-k3d's only job is to decide **how many** of those fit on this worker and to refuse a build that cannot run even one. Field meanings (`concurrency`, `cpus_each`, `tokens.total`) stay in [evaluation.md](evaluation.md).

## Declared, not overridden

`pipeline/lib/task_resources.py` reads the `[environment]` and `[verifier.environment]` blocks of every selected `task.toml` and takes the maximum across them, because a verifier can ask for more than the agent phase:

| Suite | `cpus` | `memory_mb` | `storage_mb` |
|---|---|---|---|
| DeepSWE | 2 | 8192 | 20480 |
| LoLBench | 4 | 7168–8192 | — |

The pipeline passes **no** `--override-cpus` and **no** `--override-memory-mb` on the normal path. A task gets what it asks for, so a score is comparable with anyone else's run of the same task.

## How many fit

```text
EVAL_SLOTS = max(1, min(CPU_LOCK_QTY / declared_cpus, mem_slots, disk_floor))
mem_slots  = floor((MemAvailable_GB - HOST_RESERVE_GB) / declared_memory_GB)
disk_floor = 1 when free disk at WORKDIR < MIN_DISK_GB, else unbounded
```

`HOST_RESERVE_GB` is 2 and `MIN_DISK_GB` is 40. `EVAL_SLOTS` becomes Harbor's `-n`, so Harbor keeps that many trials in flight and queues the rest itself.

Admission and throughput use different numbers on purpose:

- **Admission** (can this worker run one task at all?) uses `MemTotal - reserve`. A worker with enough RAM but a full page cache should still be allowed to start.
- **Throughput** (how many at once?) uses `MemAvailable - reserve`. Packing against total RAM would thrash.

If one declared task does not fit under either test, the build **fails before the first container starts** with the shortfall printed. That is deliberate: a half-resourced run produces numbers nobody can use.

After the question list is chosen, `evaluate/slots` prints the plan and `artifact.json` keeps it:

```json
"resources": {
  "declared": {"peak": {"cpus": 2, "memory_mb": 8192, "storage_mb": 20480}},
  "applied":  {"slots": 2, "cpus_each": 2}
}
```

Inspect a plan without running anything:

```bash
python3 pipeline/lib/task_resources.py plan \
  --tasks-dir eval-runs/deep-swe/tasks \
  --selected eval-runs/selected_tasks.txt \
  --cpu 8 --n-rollouts 4 --workdir eval-runs
```

On Jenkins, `CPU_LOCK_QTY` is not a parameter: the `Evaluate` stage locks every `<node>-core-N` resource of its worker and exports how many it got. That count is `jenkins_agent.cpu_cores` in the worker's `~/.config/mac-k3d/worker.yaml`: `mac-k3d config -c worker.yaml` creates `<node>-core-1..N` and deletes any `<node>-core-M` above N. A core that a running build holds is reported `busy` and kept until the next `config`.

`mac-k3d eval --local` and `mac-k3d eval --stage` plan with the same number, so a local run packs the same `EVAL_SLOTS` as a Jenkins build on that machine. They print one line before the phase starts, for example `CPU_LOCK_QTY=16 (from /home/you/.config/mac-k3d/worker.yaml jenkins_agent.cpu_cores; Jenkins locks the same on this node)`. The number comes from the first of these that is set:

1. `CPU_LOCK_QTY` already in the environment (must be an integer of at least 1)
2. `jenkins_agent.cpu_cores` in `~/.config/mac-k3d/worker.yaml`, when above 0
3. `jenkins_agent.cpu_cores` in the config `eval` loaded, when above 0
4. this host's logical CPU count

To use fewer cores on a shared host, lower `jenkins_agent.cpu_cores` in `worker.yaml` and re-run `mac-k3d config -c worker.yaml` (or `scripts/redeploy.sh`); Jenkins and local runs both follow. `bash pipeline/stages/run_all.sh` started by hand with `CPU_LOCK_QTY` unset still uses 1. Set `EVAL_RESOURCE_CAP=0` only for fixture tests that must ignore RAM.

Later stages do not re-derive any of this: `eval_parallel_degree` reads the `eval_resources.json` that `evaluate/slots` wrote, so the report describes the run that happened rather than a fresh guess about the current machine.

## Memory trace

A single `harbor run` has no one "active question", so the memory trace is per build rather than per question. The `evaluate/harbor_run` heartbeat appends a `docker stats` snapshot to `harness/container_mem.jsonl` for containers whose names contain `_icode_` or `__env-main-`, ignoring sidecars. The largest `peak_gb` is kept in `harness/container_mem_peak_gb`, and the next build feeds it back in as `--measured-peak-gb` so a worker learns its real ceiling over time.

```bash
python3 pipeline/lib/task_resources.py sample --harness-dir eval-runs/harness --slots 2
```

## Scaling out instead of up

Packing more trials onto one worker has a ceiling; adding workers does not. `<suite>_some_task` and `<suite>_full_suite_task` queue one `one_task` build per question and rollout. A worker runs `cpu_cores / cpus_per_executor` of them at once, each locking the CPUs that question declares, and takes the next when one finishes. Adding a worker is the whole change — see [evaluation.md](evaluation.md#which-job-to-run).

Time and token cost are reduced by `ICODE_REASONING_EFFORT=high` (less explore soft-stop), not by cutting rollouts.

## Retries

Harbor's `-r` (max retries) handles a trial that dies on infrastructure, so mac-k3d has no resume mode of its own. To re-run a suite that aborted, trigger the dispatcher again: within one dispatcher build, shards that fail do not stop the others (`propagate: false`), and the `Aggregate` stage reports on whatever finished, so a partial suite still yields a report.

## Disk and I/O

Free disk at `WORKDIR` below 40 GB forces one slot. Harbor keeps one jobs tree per run so rollouts of the same task reuse image layers. `PYTHONDONTWRITEBYTECODE=1`. The heartbeat does not scan huge logs.

## Not done

No cgroup I/O weights and no Harbor image-build changes.
