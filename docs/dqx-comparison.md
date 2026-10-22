# Custom engine vs DQX

The service was first built with its own metric catalog, SQL renderer and runner. It now uses
[DQX](https://databrickslabs.github.io/dqx/) (`databricks-labs-dqx` 0.16) for check definitions, execution and result
metrics. The design principles are unchanged: a configuration layer with one table that defines every check, one
results table, and monitoring only (no gating, no quarantine).

Line counts are `wc -l` of the committed code before and after, comments and blank lines included. They show where the
code went, not a precise complexity measure.

## Where the code went

| Area | Before | After | Change | What happened |
|---|---:|---:|---:|---|
| **Compute engine**: `engine.py`, `rendering.py`, `evaluation.py`, `models.py` | 352 | 0 | **−352** | DQX owns check functions, SQL generation, one scan per table, parallel runs and per-check failure counts. Pass/fail is now a view |
| **Config layer**: `config_io.py`, `assignments_sync.py`, `spark_io.py` → `checks_io.py`, `service.py` | 391 | 177 | **−214** | No metric catalog, assignment merging or hand-written table I/O. DQX reads and writes the checks table and appends the metrics |
| `cli.py` | 199 | 107 | −92 | Five entry points instead of seven (setup, load-metrics and sync-assignments became `dq-deploy-config`) |
| `naming.py` | 43 | 23 | −20 | One helper and no `TableNames` class |
| `synthetic.py` | 102 | 85 | −17 | Same seed data, simpler expected-outcome table |
| `sqlfiles.py`, `__init__.py` | 24 | 24 | 0 | Unchanged |
| **Python source total** | **1,111** | **416** | **−695 (−63%)** | |
| YAML config (metric catalog, assignments, synthetic) | 126 | 77 | −49 | `metric_catalog.yml` (77) is gone. Checks are more verbose than the old one-line assignments (+28) |
| SQL (`ddl.sql`, `views.sql`, `alerts.sql`) | 181 | 137 | −44 | `ddl.sql` (78) is gone because DQX defines the tables. `views.sql` grew by 47 lines to flatten DQX's long-format metrics |
| Tests | 821 | 191 | −630 | Tests for the engine, renderer, evaluator and sync went with that code. One end-to-end test file replaces them |
| Bundle (`databricks.yml`, `resources/`, `pyproject.toml`) | 223 | 195 | −28 | `dq_deploy_config` is one task instead of three, `dq_daily_run` one instead of two |
| **Code, config, SQL and tests** | **2,239** | **821** | **−1,418 (−63%)** | |

The largest reductions, in order: tests (−630), the compute engine (−352), the config layer (−214), the CLI (−92).
The engine is the only area that disappeared completely. SQL views are the one place that grew, because they now do
work the old code did in Python.

## Concept by concept

| Concept | Custom build | With DQX |
|---|---|---|
| Check vocabulary | Governance-reviewed `metric_catalog` with SQL templates | DQX check functions (`is_not_null`, `is_unique`, `regex_match`, `is_aggr_*`, …) |
| Assignment config | Own YAML, merged and validated, then written to `assignments` | DQX metadata checks in YAML, validated by `DQEngine.validate_checks`, saved to the DQX `checks` table |
| Row-level filter | `params.filter` with a keyword blocklist | Native `filter` on the check |
| Threshold | `threshold` with `direction` MAX or MIN | `user_metadata.max_fail_rate` (share of failing rows allowed) |
| Severity | LOW, MEDIUM, HIGH, CRITICAL | `criticality`: `error` or `warn` |
| Execution | Batched SQL, retry per check, thread pool | `apply_checks_and_save_in_tables` |
| Results | Own table with a status per assignment | DQX summary-metrics table, flattened by `v_check_results` |
| Config problems | `assignment_issues` table and alert | CI `dq-validate-config` and a failing deploy job |
| Check that cannot run | `ERROR` row, other checks on the table still run | The table's scan fails and no row is written. Other tables still run, the job then fails, and `v_missing_results` lists the checks |
| SQL injection controls | Validated identifiers, escaped parameters | Table names validated. Check arguments are DQX's own |

## Behaviour that is different

- **No metric catalog.** Governance no longer reviews metric definitions. If it needs to restrict which DQX functions
  are allowed, add an allowlist to `checks_io.py`.
- **Thresholds are not snapshotted.** DQX's rule fingerprint ignores `user_metadata`, so views show owner, criticality
  and `max_fail_rate` of the currently deployed checks. The deploy therefore clears the `checks` table before saving.
- **Coarser failure model.** One bad column fails every check on that table for the run, where before only that check
  errored.
- **Different semantics for some checks.** Failure rate is failed rows divided by rows read. `is_unique` counts every
  row in a duplicate group, where `duplicate_pct` was one minus distinct over non-null. Dataset-level checks fail all
  or nothing, so use `max_fail_rate: 0` for them.
- **Fewer severities and statuses.** Two criticality levels, and `ERROR` now only means the table had zero rows.

## Costs of depending on DQX

- **Version coupling.** DQX is pinned to `>=0.16,<0.17` and its table formats are part of this service's contract.
  Re-run the tests when bumping it.
- **Slower tests.** The suite starts a local Spark session with Delta and takes about three minutes, against seconds
  for the old pure-Python tests. It needs Java 17 and network access for the Delta jars.
- **Sharp edges found while building.** `is_in_list` reads bare strings as column names, `user_metadata` values are
  stored JSON-encoded, and DQX skips saves whose fingerprint is unchanged. Each is handled in the code and covered by a
  test.
- **Databricks deployment verified.** The dev bundle validated and deployed successfully.
  `dq_deploy_config` deployed 12 checks across two tables, `dq_synthetic_smoke_test` passed its
  synthetic outcome verification, and a manual `dq_daily_run` completed successfully. These remote
  runs validate the deployment workflow; the local pytest suite was not run in that environment.
