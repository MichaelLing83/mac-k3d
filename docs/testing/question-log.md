# Question run log

One row per question per eval run, newest first. `mac-k3d eval record --job <job> --build <n>` adds a Jenkins build. On `some_task` or `full_suite_task` that build is the dispatcher: the merged report's `run_id` is `<job>-<n>`, and every question in it is recorded. A `one_task` report is `jenkins-<n>`. Every `mac-k3d eval --local` run adds itself when it ends, passed or failed. Coverage per question (which ones ever ran) stays in [question-coverage.md](question-coverage.md).

Only **fix** is yours: write what you changed or found. Re-recording a run rewrites the other cells and keeps **fix**. Problem text is masked for keys and tokens and cut to 200 characters; open the build's console or `summary.md` for the rest.

Results: `pass c/n` (every rollout resolved), `fail c/n` (scored, not all resolved), `unscored` (no verifier result), `skipped`, `failed` (the run stopped before scoring this question), `canary pass` / `no score` (a `CANARY=only` run; Open problems skips it). A `(run)` row is a run that stopped before it selected any question.

## Open problems

The latest result of each question that is not `pass`, with the newest fix note written for it.

<!-- question-log:open:begin -->
| question | benchmark | last run | worker | result | stage | problem | fix |
| --- | --- | --- | --- | --- | --- | --- | --- |
| anko-typed-variable-bindings | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| awilix-async-container-initialization | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| bandit-incremental-cache-control | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| bandit-interprocedural-taint-checks | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| bandit-structured-nosec-directives | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | - | |
| clack-async-autocomplete-options | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | - | |
| csstree-shorthand-expansion-compression | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | - | |
| dasel-html-document-format | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| etree-xml-diff-patch | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| fastapi-deprecation-response-headers | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| fd-deterministic-multi-key-sorting | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| geo-shapeindex-serialization | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| go-critic-doc-link-checker | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| gql-incremental-graphql-delivery | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 0/4 (reward 0) | done | - | |
| happy-dom-deterministic-intersectionobserver | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 0/4 (reward 0) | done | - | |
| httpx-streaming-json-iteration | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| kcp-go-multiplexed-kcp-streams | deepswe | deepswe_some_task #10 (2026-10-09 03:09) | mac-Michael-Ubuntu (docker 29.1.3) | fail 0/1 (reward 0) | done | - | |
| kombu-virtual-queue-dead-lettering | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| koota-deferred-mutation-buffer | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | empty model.patch (1/4) | |
| koota-entity-snapshot-rollback | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| koota-query-predicates | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| langchain-request-coalescing | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| obsidian-linter-auto-table-of-contents | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | iCode killed (exit 137, likely out of memory) (4/4) | |
| onedump-dump-encryption-pipeline | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| opa-rego-rule-profiling | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | - | |
| participle-grammar-conflict-analysis | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| pest-character-class-coalescing | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 0/4 (reward 0) | done | - | |
| psd-tools-blend-range-api | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| sqlite-utils-safe-import-checkpoints | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| superjson-error-stack-serialization | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 0/4 (reward 0) | done | - | |
| termenv-preserve-ansi-resets | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 0/4 (reward 0) | done | - | |
| textual-richlog-follow-state | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 2/4 (reward 1) | done | - | |
| updo-policy-alerting | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 1/4 (reward 1) | done | - | |
| vulture-persistent-analysis-cache | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
| ytt-jsonpath-query-api | deepswe | deepswe_some_task #9 (2026-10-08 10:02) | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | fail 3/4 (reward 1) | done | - | |
<!-- question-log:open:end -->

## Runs

| date (UTC) | run | worker | benchmark | question | result | stage | problem | fix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-09 03:14 | deepswe_some_task #11 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) · egress probe substituted | deepswe | obsidian-linter-scoped-ignore-markers | pass 1/1 | done | - | |
| 2026-10-09 03:14 | deepswe_some_task #11 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) · egress probe substituted | deepswe | pwntools-tube-multiplexing | pass 1/1 | done | - | |
| 2026-10-09 03:09 | deepswe_some_task #10 | mac-Michael-Ubuntu (docker 29.1.3) | deepswe | igel-persist-feature-schema | pass 1/1 | done | - | |
| 2026-10-09 03:09 | deepswe_some_task #10 | mac-Michael-Ubuntu (docker 29.1.3) | deepswe | kcp-go-multiplexed-kcp-streams | fail 0/1 (reward 0) | done | - | |
| 2026-10-09 03:06 | deepswe_one_task #93 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) · egress probe substituted | deepswe | abs-module-cache-flags | pass 1/1 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | anko-typed-variable-bindings | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | awilix-async-container-initialization | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | bandit-incremental-cache-control | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | bandit-interprocedural-taint-checks | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | bandit-structured-nosec-directives | fail 1/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | clack-async-autocomplete-options | fail 1/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | csstree-shorthand-expansion-compression | fail 1/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | dasel-html-document-format | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | etree-xml-diff-patch | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | fastapi-deprecation-response-headers | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | fd-deterministic-multi-key-sorting | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | geo-shapeindex-serialization | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | go-critic-doc-link-checker | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | go-genai-streamed-function-args | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | gql-incremental-graphql-delivery | fail 0/4 (reward 0) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | happy-dom-deterministic-intersectionobserver | fail 0/4 (reward 0) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | httpx-streaming-json-iteration | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | kombu-virtual-queue-dead-lettering | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | koota-deferred-mutation-buffer | fail 1/4 (reward 1) | done | empty model.patch (1/4) | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | koota-entity-snapshot-rollback | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | koota-query-predicates | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | langchain-request-coalescing | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | obsidian-linter-auto-table-of-contents | fail 1/4 (reward 1) | done | iCode killed (exit 137, likely out of memory) (4/4) | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | ofetch-per-origin-circuit-breaker | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | onedump-dump-encryption-pipeline | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | opa-rego-rule-profiling | fail 1/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | participle-grammar-conflict-analysis | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | pest-character-class-coalescing | fail 0/4 (reward 0) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | psd-tools-blend-range-api | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | query-persist-restored-query-state | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | sqlite-utils-safe-import-checkpoints | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | superjson-error-stack-serialization | fail 0/4 (reward 0) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | task-task-graph-export | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | tengo-destructuring-bindings | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | termenv-preserve-ansi-resets | fail 0/4 (reward 0) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | textual-richlog-follow-state | fail 2/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | tomlkit-toml-table-converters | pass 4/4 | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | updo-policy-alerting | fail 1/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | vulture-persistent-analysis-cache | fail 3/4 (reward 1) | done | - | |
| 2026-10-08 10:02 | deepswe_some_task #9 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) | deepswe | ytt-jsonpath-query-api | fail 3/4 (reward 1) | done | - | |
| 2026-10-07 02:08 | deepswe_one_task #54 | mac-iZt4ndd2dff7gqjta7mppaZ (docker 29.6.2) · egress probe substituted | deepswe | ipython-session-bundle-replay | pass 1/1 | done | - | |
| 2026-10-06 08:56 | local 20261006T085615Z | Michael-Ubuntu (docker 29.1.3) | deepswe | abs-module-cache-flags | pass 1/1 | done | - | |
