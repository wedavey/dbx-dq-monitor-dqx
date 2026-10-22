-- Views the dashboard and alerts read (never the raw results table).
-- ${catalog} and ${schema} are substituted by `dq-deploy-config`.
-- Avoid semicolons inside comments and strings: the deploy task splits this file on them.
--
-- `results` is DQX's summary-metrics table: per run and table it holds row counts and one JSON
-- `check_metrics` entry per check (rows that failed it). `checks` is the DQX checks table.

-- The deployed checks, flattened. DQX stores user_metadata values JSON-encoded, hence the trim.
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_checks AS
SELECT run_config_name                                                      AS table_name,
       name                                                                 AS check_name,
       `check`.function                                                     AS check_function,
       criticality,
       trim(BOTH '"' FROM user_metadata['owner'])                           AS owner,
       trim(BOTH '"' FROM user_metadata['data_product'])                    AS data_product,
       coalesce(try_cast(trim(BOTH '"' FROM user_metadata['max_fail_rate']) AS DOUBLE), 0.0) AS max_fail_rate
FROM ${catalog}.${schema}.checks;

-- One row per run, table and check. fail_rate = share of rows that failed the check. The check's
-- criticality, owner and max_fail_rate come from the current `checks` rows. A run that read zero
-- rows is ERROR: nothing could be evaluated.
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_check_results AS
WITH runs AS (
  SELECT run_id, run_time, input_location AS table_name,
         max(CASE WHEN metric_name = 'input_row_count' THEN CAST(metric_value AS BIGINT) END) AS input_rows,
         max(CASE WHEN metric_name = 'check_metrics' THEN metric_value END)                   AS check_metrics
  FROM ${catalog}.${schema}.results
  GROUP BY run_id, run_time, input_location
), per_check AS (
  SELECT r.run_id, r.run_time, r.table_name, r.input_rows,
         c.check_name, c.error_count + c.warning_count AS failed_rows
  FROM runs r
  LATERAL VIEW explode(from_json(r.check_metrics,
    'array<struct<check_name:string,error_count:bigint,warning_count:bigint>>')) x AS c
), scored AS (
  SELECT p.run_id, p.run_time, CAST(p.run_time AS DATE) AS run_date, p.table_name, p.check_name,
         k.check_function, coalesce(k.criticality, 'error') AS criticality, k.owner, k.data_product,
         p.input_rows, p.failed_rows,
         p.failed_rows / nullif(p.input_rows, 0)            AS fail_rate,
         coalesce(k.max_fail_rate, 0.0)                     AS max_fail_rate
  FROM per_check p
  LEFT JOIN ${catalog}.${schema}.v_checks k
    ON k.table_name = p.table_name AND k.check_name = p.check_name
)
SELECT *,
       CASE WHEN input_rows = 0 THEN 'ERROR'
            WHEN fail_rate > max_fail_rate + 1e-9 THEN 'FAIL'
            ELSE 'PASS' END AS status
FROM scored;

CREATE OR REPLACE VIEW ${catalog}.${schema}.v_latest_results AS
SELECT * EXCEPT (rn)
FROM (
  SELECT r.*, row_number() OVER (PARTITION BY table_name, check_name ORDER BY run_time DESC) AS rn
  FROM ${catalog}.${schema}.v_check_results r
)
WHERE rn = 1;

CREATE OR REPLACE VIEW ${catalog}.${schema}.v_daily_pass_rate AS
SELECT run_date,
       data_product,
       check_function,
       count(*)                                                       AS checks,
       sum(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END)               AS passed,
       round(avg(CASE WHEN status = 'PASS' THEN 1.0 ELSE 0.0 END), 4) AS pass_rate
FROM ${catalog}.${schema}.v_check_results
GROUP BY run_date, data_product, check_function;

-- Checks with no result in the last 26 hours (silent runner failure, a table that cannot be read,
-- new onboarding before the first run).
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_missing_results AS
SELECT k.table_name, k.check_name, k.owner
FROM ${catalog}.${schema}.v_checks k
LEFT JOIN (
  SELECT DISTINCT table_name, check_name
  FROM ${catalog}.${schema}.v_check_results
  WHERE run_time >= current_timestamp() - INTERVAL 26 HOURS
) r ON r.table_name = k.table_name AND r.check_name = k.check_name
WHERE r.check_name IS NULL;

-- One row per monitored table: lets governance see that their checks took effect.
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_onboarding_status AS
SELECT k.table_name,
       any_value(k.data_product)                                   AS data_product,
       any_value(k.owner)                                          AS owner,
       count(*)                                                    AS checks,
       sum(CASE WHEN r.status = 'FAIL' THEN 1 ELSE 0 END)          AS failing,
       sum(CASE WHEN r.status = 'ERROR' THEN 1 ELSE 0 END)         AS errored,
       max(r.run_time)                                             AS last_run
FROM ${catalog}.${schema}.v_checks k
LEFT JOIN ${catalog}.${schema}.v_latest_results r
  ON r.table_name = k.table_name AND r.check_name = k.check_name
GROUP BY k.table_name;
