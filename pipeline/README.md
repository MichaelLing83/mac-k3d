# Eval pipeline

| Path | What |
|------|------|
| [`stages/`](stages/) | `run_all.sh`, one script per phase (`env.sh` … `archive.sh`) and its single-purpose steps (`env/`, `tasks/`, `evaluate/`, `anticheat/`, `score/`, `report/`, `archive/`) |
| [`lib/`](lib/) | Harbor agents, isolation and anti-cheat checks, scoring, report, archive, testdata |
| [`config/`](config/) | `toolchain.env` (Harbor, compose, buildx pins), `network-allowlist-v1.json`, `anticheat-v1.json`, `canary-v1.json`, harness labels |
| [`tools/`](tools/) | Manual scripts that are not phases: canary only, the LLM-only baseline, report check, the `mac-k3d eval prepare` engine (`prewarm_task_images.sh`), fixture tests |

A build runs seven phases: `env`, `tasks`, `evaluate`, `anticheat`, `score`, `report`, `archive`. Only `evaluate` holds the worker's CPU lock; it runs the isolation canary and one `harbor run` over the selected questions, with `-n` slots planned from the cores the build locked ([optimization.md](../docs/optimization.md)). The phase map, every Harbor flag and the anti-cheat layers are in [docs/pipeline.md](../docs/pipeline.md).

Runtime (gitignored): **`eval-runs/`** and **`output/`**. `report` writes `eval-runs/output/<benchmark>/<run>/artifact.json` plus `summary.md`, `report.html` and `provenance.md`; `archive` adds `cost-token-report.md` and stores the run as `output/<benchmark>/<run>.tar.gz`. A full eval does not run the no-harness baseline (`tools/baseline.sh`).

A Release binary embeds this tree; Jenkins extracts it per build to `$WORKSPACE/mac-k3d-pipeline`, and `mac-k3d eval --stage <phase>` uses `~/.local/share/mac-k3d/pipeline`. Users provide iCode via a `*-full-*` drop or a git clone — [icode-harness-inputs.md](../docs/icode-harness-inputs.md).

Operator commands: [docs/user-guide.md](../docs/user-guide.md).
