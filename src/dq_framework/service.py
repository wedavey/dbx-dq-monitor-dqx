"""The service: deploy the checks table, then let DQX run every table and record summary metrics.

DQX does the heavy lifting: it loads each table's checks from the ``checks`` table, scans the table
once, and appends per-check failure counts to ``results`` (its summary-metrics table). Nothing is
quarantined or written back: this is monitoring only.
"""

from __future__ import annotations

from typing import Any

from databricks.labs.dqx.checks_storage import CHECKS_TABLE_SCHEMA
from databricks.labs.dqx.config import InputConfig, OutputConfig, RunConfig, TableChecksStorageConfig
from databricks.labs.dqx.engine import DQEngine
from databricks.labs.dqx.metrics_observer import OBSERVATION_TABLE_SCHEMA, DQMetricsObserver

from . import checks_io
from .naming import check_identifier, fq
from .sqlfiles import load_sql


def _create_if_missing(spark: Any, table: str, schema: str) -> None:
    # DataFrame API, not CREATE TABLE: the DQX checks table has a column called `check`.
    spark.createDataFrame([], schema).write.mode("ignore").saveAsTable(table)


def deploy_config(spark: Any, ws: Any, catalog: str, schema: str, check_files: list[str]) -> dict[str, int]:
    """Create the tables and views, then replace the contents of ``checks`` with the YAML. Idempotent.

    Returns {table: number of checks}. Invalid YAML raises ConfigError before anything changes.
    """
    checks_by_table = checks_io.load_checks(check_files, catalog)
    checks_table, results_table = fq(catalog, schema, "checks"), fq(catalog, schema, "results")

    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {check_identifier(catalog)}.{check_identifier(schema)}")
    _create_if_missing(spark, checks_table, CHECKS_TABLE_SCHEMA)
    _create_if_missing(spark, results_table, OBSERVATION_TABLE_SCHEMA)

    # DQX skips a save when the rule set's fingerprint is unchanged, and the fingerprint ignores
    # user_metadata (owner, max_fail_rate). Clearing first means every deploy writes what the YAML says.
    spark.sql(f"DELETE FROM {checks_table}")
    engine = DQEngine(ws, spark)
    for table, checks in checks_by_table.items():
        engine.save_checks(checks, TableChecksStorageConfig(location=checks_table, run_config_name=table, mode="append"))

    for statement in load_sql("views.sql", catalog, schema):
        spark.sql(statement)
    return {table: len(checks) for table, checks in checks_by_table.items()}


def run_checks(spark: Any, ws: Any, catalog: str, schema: str, max_workers: int = 4) -> int:
    """Run every table that has checks. Returns the number of tables run.

    DQX attempts every table even if one fails (unreadable table, dropped column), then re-raises the
    first error, so the job fails and ops is notified. A failing *check* is just a result.
    """
    checks_table, results_table = fq(catalog, schema, "checks"), fq(catalog, schema, "results")
    tables = [r[0] for r in spark.table(checks_table).select("run_config_name").distinct().collect()]
    if not tables:
        print("WARNING: no checks deployed; nothing to run")
        return 0
    engine = DQEngine(ws, spark, observer=DQMetricsObserver(name="dq_monitor"))
    engine.apply_checks_and_save_in_tables(
        [
            RunConfig(
                name=table,  # DQX loads the checks stored under this run_config_name
                input_config=InputConfig(location=table),
                metrics_config=OutputConfig(location=results_table, mode="append"),
                checks_location=checks_table,
            )
            for table in tables
        ],
        max_parallelism=max_workers,
    )
    return len(tables)
