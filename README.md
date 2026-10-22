# dbx-dq-monitor

A Databricks Asset Bundle for **Databricks Free Edition** (serverless only; dev/test/prod are
separated by schema in one workspace). It contains a config-driven **DQ monitoring service**
(`src/dq_framework`) built on [DQX](https://databrickslabs.github.io/dqx/), which does the check
definitions, the execution and the result metrics. This repo adds the thin layer around it.

## DQ monitoring service

Checks are DQX checks written in YAML (`src/dq_framework/config/checks.yml`, changed by pull
request) and deployed to a `checks` table. A scheduled job has DQX run every table in that table once
and append per-check failure counts to one `results` table that views, dashboards and alerts read.
Monitor and alert only: nothing gates a pipeline and no rows are quarantined.

| Job | Targets | Does |
|---|---|---|
| `dq_deploy_config` | all | Create tables and views, load the check YAML into the `checks` table |
| `dq_daily_run` | all | DQX runs every deployed check (05:30 Sydney; unpaused only in prod) |
| `dq_synthetic_smoke_test` | dev, test | Seed synthetic tables with known defects, deploy, run, assert outcomes |

Tables live in `<catalog>.<dq_schema>` (`dq_dev`, `dq_test`, `dq_prod`): `checks` and `results`
(both DQX's own formats) and views `v_checks`, `v_check_results`, `v_latest_results`,
`v_daily_pass_rate`, `v_missing_results`, `v_onboarding_status`. Example DBSQL alerts are in
`src/dq_framework/sql/alerts.sql`. See [docs/checks.md](docs/checks.md) for onboarding.

Design notes: one scan per table; a check passes while the share of failing rows stays within its
`max_fail_rate`; one broken table does not stop the others (the job fails afterwards so ops hears
about it); identifiers are validated.

Open points for governance: who reviews check PRs in prod (there is no metric catalog any more, so
the DQX function list is the vocabulary); the run identity needs SELECT on monitored tables.

## Develop and test

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
.venv/bin/python -m pytest      # needs Java 17+ and network access (Delta jars)
.venv/bin/dq-validate-config
```

## Deploy

Requires the Databricks CLI (>=0.250) and `uv`.

```bash
databricks auth login --host <free-edition-workspace-url>
databricks bundle validate -t dev
databricks bundle deploy   -t dev                       # or test / prod
databricks bundle run dq_deploy_config -t dev
databricks bundle run dq_synthetic_smoke_test -t dev    # dev/test only
databricks bundle destroy  -t dev
```

Promote by deploying the same commit to `test`, then `prod`. Set `ops_email` (failure
notifications, default is a placeholder) per target as needed.
