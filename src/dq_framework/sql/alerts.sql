-- DBSQL alert queries. Not run by the framework: save each as a query in Databricks SQL, then create
-- an Alert on it (condition: first row, column alert_count > 0). Replace ${catalog}.${schema}.
-- Route by audience: 7a, 7c go to data owners. 7b goes to the platform team.

-- 7a. Failing `error` checks from the latest run
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(table_name, ' | ', check_name, ' | ', round(fail_rate, 4), ' of rows failed (allowed ',
                max_fail_rate, ') | owner: ', owner))) AS details
FROM ${catalog}.${schema}.v_latest_results
WHERE status = 'FAIL'
  AND criticality = 'error'
  AND run_time >= current_timestamp() - INTERVAL 26 HOURS;

-- 7b. Runner health: checks with no recent result (dropped column, permissions, job failure) and
-- checks that read zero rows
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(details)) AS details
FROM (
  SELECT concat(table_name, ' | ', check_name, ' | no result in 26 hours') AS details
  FROM ${catalog}.${schema}.v_missing_results
  UNION ALL
  SELECT concat(table_name, ' | ', check_name, ' | table had no rows') AS details
  FROM ${catalog}.${schema}.v_latest_results
  WHERE status = 'ERROR' AND run_time >= current_timestamp() - INTERVAL 26 HOURS
);

-- 7c. Persistent failures: `warn` checks that failed three runs in a row
WITH ranked AS (
  SELECT table_name, check_name, status, owner,
         row_number() OVER (PARTITION BY table_name, check_name ORDER BY run_time DESC) AS rn
  FROM ${catalog}.${schema}.v_check_results
  WHERE criticality = 'warn'
), persistent AS (
  SELECT table_name, check_name, any_value(owner) AS owner
  FROM ranked
  WHERE rn <= 3
  GROUP BY table_name, check_name
  HAVING count(*) = 3 AND sum(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) = 3
)
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(table_name, ' | ', check_name, ' | owner: ', owner))) AS details
FROM persistent;
