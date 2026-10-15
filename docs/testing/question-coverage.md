# Question coverage on this worker

Which questions have a Harbor run, and the Docker memory recorded for each. Product packing rules stay in [optimization.md](../optimization.md). Branch notes: [eval-comparison-report](../eval-comparison-report/README.md).

What each run did per question (pass, fail, the error that stopped it, and your fix notes) is in [question-log.md](question-log.md). `mac-k3d eval record` writes it and also sets `status` and `builds` here: a Jenkins build adds its number (a `some_task` or `full_suite_task` record adds the dispatcher build, not each shard), a local run adds `local` once. It never touches `peak_gb`, `slots` or `memory_mb`.

Jenkins build numbers are the `jenkins-<n>` trees under each job’s `eval-runs/harness/harbor_runs`. `peak_gb`, `slots`, and `memory_mb` come from that run’s `container_mem.jsonl` after the report phase (copied next to `artifact.json`). Historical builds finished before that file existed, so those cells stay empty.

A skip (`skip question=` / `skipped_questions.txt`) stays **not run**. An out-of-memory skip can still have a sampled `peak_gb` in that run’s `container_mem.jsonl`. After a finished report phase, fill the three memory cells and set status to `run`.

## Next DeepSWE TASKS

Paste into **TASKS** on `deepswe_some_task`, **N_ROLLOUTS=4**. `TASKS` replaces `N_TASKS`.

```text
eicrud-keyset-pagination-cursor,scc-bounded-memory-spilling,meriyah-explicit-resource-declarations
```

These three are not in jenkins-21 through jenkins-30. A build extracts its own copy of the pipeline in its first stage, so redeploying the worker binary mid-build does not change the scripts it runs. The one exception is a redeploy that changes `agent.jar`: worker `config` then restarts the agent, which drops the running build.

## DeepSWE

50 run, 63 not run, 113 task dirs.

| question | status | builds | peak_gb | slots | memory_mb |
|----------|--------|--------|---------|-------|-----------|
| `abs-module-cache-flags` | run | 21, 22, 23, 24, 25, 27, 28, 29, 30, local, 93 | | | |
| `abs-stepped-slices` | run | 23, 24, 25, 28, 29, 30 | | | |
| `actionlint-action-pinning-lint` | run | 23, 24, 25, 28, 29, 30 | | | |
| `adaptix-name-mapping-aliases` | run | 23, 24, 29, 30 | | | |
| `aiomonitor-task-snapshots-diff` | run | 23 | | | |
| `anko-default-function-arguments` | not run |  | | | |
| `anko-typed-variable-bindings` | run | 9 | | | |
| `arcane-drift-detection-baselines` | not run |  | | | |
| `arktype-json-schema-refs-dependencies` | not run |  | | | |
| `awilix-async-container-initialization` | run | 9 | | | |
| `bandit-incremental-cache-control` | run | 9 | | | |
| `bandit-interprocedural-taint-checks` | run | 9 | | | |
| `bandit-structured-nosec-directives` | run | 9 | | | |
| `boa-hierarchical-evaluation-cancellation` | not run |  | | | |
| `cattrs-partial-structuring-recovery` | not run |  | | | |
| `clack-async-autocomplete-options` | run | 9 | | | |
| `claude-code-by-agents-recursive-delegation` | not run |  | | | |
| `cliffy-config-file-parsing` | not run |  | | | |
| `csstree-shorthand-expansion-compression` | run | 9 | | | |
| `dasel-html-document-format` | run | 9 | | | |
| `dateutil-rfc5545-timezone-interop` | not run |  | | | |
| `drizzle-orm-window-function-builders` | not run |  | | | |
| `dynamodb-toolbox-conditional-attribute-requirements` | not run |  | | | |
| `dynamodb-toolbox-lazy-recursive-schemas` | not run |  | | | |
| `effect-sse-httpapi-streaming` | not run |  | | | |
| `eicrud-keyset-pagination-cursor` | not run |  | | | |
| `etree-xml-diff-patch` | run | 9 | | | |
| `expr-try-catch-errors` | not run |  | | | |
| `fastapi-deprecation-response-headers` | run | 9 | | | |
| `fastapi-implicit-head-options` | not run |  | | | |
| `fd-deterministic-multi-key-sorting` | run | 9 | | | |
| `geo-shapeindex-serialization` | run | 9 | | | |
| `go-critic-doc-link-checker` | run | 9 | | | |
| `go-genai-streamed-function-args` | run | 9 | | | |
| `go-git-worktree-merge-conflicts` | not run |  | | | |
| `goreleaser-retry-publish-auditing` | not run |  | | | |
| `gql-incremental-graphql-delivery` | run | 9 | | | |
| `happy-dom-abort-pending-body-reads` | not run |  | | | |
| `happy-dom-deterministic-intersectionobserver` | run | 9 | | | |
| `helm-array-merge-strategies` | not run |  | | | |
| `helm-unified-manifest-stream` | not run |  | | | |
| `httpx-deterministic-cookie-store` | not run |  | | | |
| `httpx-multipart-response-parsing` | not run |  | | | |
| `httpx-streaming-json-iteration` | run | 9 | | | |
| `igel-persist-feature-schema` | run | 10 | | | |
| `ink-grid-box-layout` | not run |  | | | |
| `ipython-session-bundle-replay` | run | 54 | | | |
| `katex-multicolumn-array-spans` | not run |  | | | |
| `kcp-go-multiplexed-kcp-streams` | run | 10 | | | |
| `kea-atomic-signal-selectors` | not run |  | | | |
| `kgateway-consistent-hash-policy` | not run |  | | | |
| `kombu-single-active-consumer-priority` | not run |  | | | |
| `kombu-virtual-queue-dead-lettering` | run | 9 | | | |
| `koota-composite-trait-aspects` | not run |  | | | |
| `koota-deferred-mutation-buffer` | run | 9 | | | |
| `koota-entity-snapshot-rollback` | run | 9 | | | |
| `koota-pair-relation-tracking` | not run |  | | | |
| `koota-query-predicates` | run | 9 | | | |
| `kysely-window-grouping-helpers` | not run |  | | | |
| `langchain-request-coalescing` | run | 9 | | | |
| `mashumaro-flattened-dataclass-fields` | not run |  | | | |
| `meriyah-explicit-resource-declarations` | not run |  | | | |
| `mnamer-daemon-watch-lifecycle` | not run |  | | | |
| `mobly-grouped-test-barriers` | not run |  | | | |
| `narwhals-rolling-window-suite` | not run |  | | | |
| `numba-stencil-boundary-modes` | not run |  | | | |
| `obsidian-linter-auto-table-of-contents` | run | 9 | | | |
| `obsidian-linter-link-format-conversion` | not run |  | | | |
| `obsidian-linter-scoped-ignore-markers` | run | 11 | | | |
| `ofetch-per-origin-circuit-breaker` | run | 9 | | | |
| `onedump-dump-encryption-pipeline` | run | 9 | | | |
| `opa-rego-rule-profiling` | run | 9 | | | |
| `opa-template-string-reconstruction` | not run |  | | | |
| `optique-conditional-option-dependencies` | not run |  | | | |
| `oxvg-structural-selector-preservation` | not run |  | | | |
| `participle-grammar-conflict-analysis` | run | 9 | | | |
| `pebble-durability-wait-apis` | not run |  | | | |
| `pest-character-class-coalescing` | run | 9 | | | |
| `prometheus-transactional-reload-status` | not run |  | | | |
| `prometheus-typed-label-sorting` | not run |  | | | |
| `psd-tools-blend-range-api` | run | 9 | | | |
| `pwntools-tube-multiplexing` | run | 11 | | | |
| `python-statemachine-state-data-scoping` | not run |  | | | |
| `query-persist-restored-query-state` | run | 9 | | | |
| `quill-shared-toolbar-focus` | not run |  | | | |
| `returns-validated-error-accumulation` | not run |  | | | |
| `scc-bounded-memory-spilling` | not run |  | | | |
| `scriggo-method-declarations` | not run |  | | | |
| `skrub-duration-encoding` | not run |  | | | |
| `sql-formatter-bigquery-pipe-formatting` | not run |  | | | |
| `sqlfmt-create-table-ddl-formatting` | not run |  | | | |
| `sqlite-utils-safe-import-checkpoints` | run | 9 | | | |
| `superjson-error-stack-serialization` | run | 9 | | | |
| `task-task-graph-export` | run | 9 | | | |
| `tengo-callable-instance-isolation` | not run |  | | | |
| `tengo-destructuring-bindings` | run | 9 | | | |
| `termenv-preserve-ansi-resets` | run | 9 | | | |
| `testem-bail-on-test-failure` | not run |  | | | |
| `testem-per-launcher-reports` | not run |  | | | |
| `textual-kitty-key-phases` | not run |  | | | |
| `textual-richlog-follow-state` | run | 9 | | | |
| `tomlkit-toml-table-converters` | run | 9 | | | |
| `true-myth-iterable-collection-combinators` | not run |  | | | |
| `ts-pattern-match-each` | not run |  | | | |
| `updo-policy-alerting` | run | 9 | | | |
| `valibot-recursive-schema-composition` | not run |  | | | |
| `vitest-duration-sharding` | not run |  | | | |
| `vulture-persistent-analysis-cache` | run | 9 | | | |
| `wasmi-trap-coredumps` | not run |  | | | |
| `wazero-multi-module-snapshots` | not run |  | | | |
| `yaegi-go-embed-directives` | not run |  | | | |
| `yjs-map-conflict-detection` | not run |  | | | |
| `ytt-jsonpath-query-api` | run | 9 | | | |

## LoLBench

1 run, 19 not run, 20 harbor tasks.

| question | status | builds | peak_gb | slots | memory_mb |
|----------|--------|--------|---------|-------|-----------|
| `cpython_1` | not run |  | | | |
| `cpython_10` | not run |  | | | |
| `cpython_11` | not run |  | | | |
| `cpython_12` | not run |  | | | |
| `cpython_2` | not run |  | | | |
| `cpython_3` | not run |  | | | |
| `cpython_4` | not run |  | | | |
| `cpython_5` | not run |  | | | |
| `cpython_6` | not run |  | | | |
| `cpython_8` | not run |  | | | |
| `cpython_9` | not run |  | | | |
| `fastapi_1` | not run |  | | | |
| `flink_1` | not run |  | | | |
| `flink_10` | not run |  | | | |
| `flink_11` | not run |  | | | |
| `flink_7` | not run |  | | | |
| `flink_9` | not run |  | | | |
| `kafka_1` | not run |  | | | |
| `kafka_2` | not run |  | | | |
| `ruff_1` | run | 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 | | | |

## SWE-bench Pro

Only the instance `tasks/benchmark` has materialized on this worker is listed. The rest of the dataset is omitted until a later run writes it under `eval-runs/swebenchpro/tasks`.

| question | status | builds | peak_gb | slots | memory_mb |
|----------|--------|--------|---------|-------|-----------|
| `instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan` | run | 13, 14, 15, 16 | | | |

