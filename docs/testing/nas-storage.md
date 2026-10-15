# Store eval runs on a NAS

The NAS keeps finished runs. Harbor, Docker, and the Jenkins workspace stay on the worker's own disk.

Copy only after the build has already saved its Jenkins artifacts. If the NAS is down, the eval still stands.

## Two copies

| When | From | Put on the NAS |
|---|---|---|
| A shard's `archive` phase has printed `compress: N transcripts gzipped and masked` | That `deepswe_one_task` workspace | The gzipped transcripts and the small files next to them |
| The dispatcher `Aggregate` stage has written `backup/<suite>/<RUN_GROUP>.tar.gz` | The worker that ran Aggregate | The merged report and that tar |

Do not copy during `env`, `tasks`, `evaluate`, `anticheat`, `score`, or `report`.

## Shard copy

From `deepswe_one_task` build `N` only. Skip older builds left in the same workspace.

- `eval-runs/harness/harbor_runs/jenkins-<N>/**/events.jsonl.gz`
- `agent.patch` and `model.patch` in that same tree
- `eval-runs/harness/anticheat/**`
- `eval-runs/selected_tasks.txt`
- `eval-runs/skipped_tasks.txt`
- `eval-runs/eval_protocol_inputs.json`
- `eval-runs/eval_resources.json`

On this PC the workspace is `/home/Toby/jenkins-agent/workspace/deepswe_one_task`. On the cloud worker it is `/home/toby/jenkins-agent/workspace/deepswe_one_task`.

## Report copy

From the dispatcher workspace (`deepswe_some_task` or `deepswe_full_suite_task`):

- `backup/<suite>/<RUN_GROUP>.tar.gz`
- `aggregate/artifact.json`
- `aggregate/summary.md`
- `aggregate/report.html`
- `aggregate/cost-token-report.md`

The tar has the scores, patches, and anti-cheat verdicts. It does not have `events.jsonl.gz`. Those are the shard copy.

## Leave these on the worker

- Docker's data directory
- `eval-runs/deep-swe/`, `eval-runs/lolbench/`, `eval-runs/icode-bin/`
- `mac-k3d-pipeline/`
- `.harbor-env` and any API key
- A plain `events.jsonl` (the archive step has already replaced it with `.gz`)
- `aggregate/harness/` (a second copy of the trials)

## Layout

```text
/mnt/mac-k3d-archive/
  reports/<suite>/<icode-sha>/<RUN_GROUP>/
    artifact.json
    summary.md
    report.html
    cost-token-report.md
    <RUN_GROUP>.tar.gz
  transcripts/<suite>/<icode-sha>/<RUN_GROUP>/shard-<build>/
    events.jsonl.gz
    *.patch
    anticheat/
    selected_tasks.txt
    eval_protocol_inputs.json
    eval_resources.json
```

`<RUN_GROUP>` is the value the dispatcher prints, such as `deepswe_full_suite_task-12`. `<icode-sha>` is the iCode commit in that run's report. One directory per commit. Read `artifact.json` to compare loop iterations. Do not merge trials from two commits into one folder.

## Prepare the NAS

The NAS is a third copy, after Jenkins has the artifacts. Both workers must see the same export. A disk plugged into this PC is not a store for the cloud worker, and a disk on the controller VM is not a store for this PC.

1. Put the NAS on the controller's private network, the one both workers already use to reach Jenkins. Do not expose the export on the public internet.
2. Install two disks and make them one RAID 1 volume, then turn snapshots on. RAID 1 keeps the files if one disk dies. Usable space is the size of one disk. The second disk is the mirror, not extra capacity. Snapshots use part of that usable space, so the volume has to be larger than the folders below.
3. Export one share. Mount that same share on each worker at `/mnt/mac-k3d-archive`.
4. Mount with `nofail`. If the NAS is down or the network path is gone, the worker still boots and the Jenkins agent still starts. The eval writes its Jenkins artifacts on local disk either way. Copy to the NAS only when the mount is actually there.
5. Let the agent user write: `Toby` on this PC, `toby` on the cloud worker. Mode `0750`. No guest access, and no write for anyone else.
6. Leave the mount as an archive directory only. Do not make it the Jenkins workspace, and do not make it Docker's data root. Harbor, image pulls, and the build workspace stay on the worker's own disk.
7. Do not point `MAC_K3D_BACKUP_ROOT` at the NAS yet. A backup written outside the workspace is not offered on the Jenkins build page. Jenkins keeps the copy that must succeed. The NAS copy comes after that, by hand, from the paths in [Shard copy](#shard-copy) and [Report copy](#report-copy).

## How much disk the NAS needs

This is disk size, not the model bill. `cost-token-report.md` is a list-price estimate of DeepSeek tokens and is one of the small files in `reports/`. It does not price the disks.

The sizes below are the ones the pipeline already records for a DeepSWE full suite (113 questions, 4 rollouts, 452 trials):

| What | Per full suite | Where it lives |
|---|---|---|
| One trial, almost all of it the plain transcript | about 57 MB, of which about 56 MB is `events.jsonl` | Worker disk, until `archive` gzips it |
| One uncompressed suite | about 26 GB | Worker disk. The NAS does not hold this |
| Gzipped transcripts (about 7× smaller) | about 26 GB / 7 ≈ 3.7 GB | NAS `transcripts/` |
| Merged report, tar, and the other small files, with no transcripts | about 0.2 GB | NAS `reports/` |

A shorter run scales with trials. Four questions at 4 rollouts is 16 trials, 16/452 of a full suite: about 0.13 GB of gzipped transcripts plus a report well under 0.2 GB.

What you keep changes the total:

```text
reports     = 0.2 GB × (every full suite you store)
transcripts = 3.7 GB × (copies you still keep)
```

`reports/` is every run. `transcripts/` is 90 days, or the last three runs of that iCode commit, whichever you apply. Three kept full-suite transcript trees are about 3 × 3.7 GB ≈ 11 GB. A year of weekly full-suite reports is about 52 × 0.2 GB ≈ 10 GB. Together that is about 21 GB of files. Buy the RAID 1 pair so one disk's usable space covers that, plus the snapshots of it. The mirror disk is the second purchase and adds no usable gigabytes.

## How long to keep it

| Folder | Keep |
|---|---|
| `reports/` | Every run |
| `transcripts/` | 90 days, or the last three runs of that iCode commit |

Jenkins still stores every shard, including the transcripts. After a shard's transcript copy is on the NAS and the size matches, that `.gz` can be dropped from the controller later. Keep the dispatcher report.

## After each copy

- `artifact.json` opens, and its iCode sha matches the directory name.
- Every shard named on the report's `Transcripts:` line has a `shard-<build>/` directory.
- One opened `events.jsonl.gz` shows `***` in place of a key, and there is no `.harbor-env`.
- The worker still has local room for one uncompressed suite (about 26 GB) plus images. The NAS does not replace that disk.
