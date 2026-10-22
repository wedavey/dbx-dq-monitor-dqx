# Onboarding tables: checks in YAML

Checks are plain [DQX](https://databrickslabs.github.io/dqx/) checks. They live in YAML files shipped in the wheel
and change by pull request:

```
 config/checks.yml (PR) ─> dq-validate-config (CI) ─> dq-deploy-config ─> checks table ─> DQX run ─> results ─> views, alerts
```

`dq-deploy-config` (the `dq_deploy_config` job, run after every deployment) validates the files named in the
`check_files` bundle variable and replaces the contents of the `checks` table. The daily run reads checks from that
table. The dev and test targets also load `dev/synthetic_checks.yml`.

## Onboard a table

Add a block to `src/dq_framework/config/checks.yml`:

```yaml
- table: ${catalog}.sales.orders          # ${catalog} is replaced per environment
  user_metadata: {owner: team@example.com, data_product: sales_orders}   # default for every check below
  checks:
    - name: order_id_unique
      check: {function: is_unique, arguments: {columns: [order_id]}}
    - name: email_not_null
      criticality: warn
      check: {function: is_not_null, arguments: {column: email}}
      user_metadata: {max_fail_rate: 0.02}
    - name: country_format
      check: {function: regex_match, arguments: {column: country, regex: '^[A-Z]{2}$'}}
    - name: row_count_min
      check: {function: is_aggr_not_less_than, arguments: {column: '*', aggr_type: count, limit: 1000}}
    - name: loaded_within_a_day
      check:
        function: is_aggr_not_less_than
        arguments: {column: loaded_at, aggr_type: max, limit: "current_timestamp() - INTERVAL 24 HOURS"}
    - name: recent_ids_not_null
      filter: "load_date >= date_sub(current_date(), 1)"     # scope a big table to recent data
      check: {function: is_not_null, arguments: {column: id}}
```

Then run `.venv/bin/dq-validate-config`, open the PR, and deploy. Check `v_onboarding_status` for your table.

| Key | Where | Notes |
|---|---|---|
| `table` | block | `catalog.schema.table` |
| `name` | check | required, unique within the table. This is the check's id in the results |
| `check` | check | any DQX check function and its `arguments`, see the [DQX reference](https://databrickslabs.github.io/dqx/docs/reference/quality_checks/) |
| `criticality` | check | `error` (default): alerts the owner. `warn`: only alerts when it fails three runs in a row |
| `filter` | check | optional SQL predicate (native DQX) |
| `user_metadata.owner` | table or check | required. Who the alert is routed to |
| `user_metadata.data_product` | table or check | grouping for dashboards |
| `user_metadata.max_fail_rate` | check | share of rows allowed to fail, 0 to 1. Default 0 |

The old metric catalog is gone: the DQX check functions are the vocabulary. Mapping from the old metrics:
`null_pct` → `is_not_null`, `blank_pct` → `is_not_empty`, `duplicate_pct` / `distinct_ratio` → `is_unique`,
`regex_fail_pct` → `regex_match`, `row_count` → `is_aggr_not_less_than` on `*` with `aggr_type: count`,
`hours_since_load` → `is_aggr_not_less_than` on the column with `aggr_type: max`.

## How a result is judged

DQX counts the rows that failed each check. `fail_rate = failed rows / rows read`, and the check is `FAIL` when
`fail_rate > max_fail_rate`. Dataset-level checks (`is_unique`, `is_aggr_*`) mark every row they flag, so for those
the rate is the share of rows involved, and `max_fail_rate: 0` is the natural setting. A table that returns zero rows
gives `ERROR`.

## What gets rejected

Malformed YAML, an unknown check function or arguments, a missing `name` or `owner`, duplicate names and a bad
`max_fail_rate` fail `dq-validate-config` and the deploy job, before the `checks` table is touched.

A check that is valid but cannot run (a column that does not exist, no permission on the table) fails the whole
table's scan in DQX. The run still attempts every other table, then fails the job so ops is notified, and the table's
checks show up in `v_missing_results` (alert 7b).

## What governance sees

| Question | Where to look |
|---|---|
| Did my change take effect, and how many checks do I have? | `v_onboarding_status` |
| Which checks never produced a result? | `v_missing_results` (alert 7b) |
| Is the table passing? | dashboard on `v_latest_results` / `v_daily_pass_rate` |

Owner, criticality and `max_fail_rate` in the views are those of the currently deployed checks, not a snapshot taken
when the run happened.
