"""End to end on local Spark + Delta: deploy checks, run DQX, read the views."""

from datetime import datetime, timezone

import pytest

from dq_framework import service, synthetic
from dq_framework.sqlfiles import load_sql

CATALOG, SCHEMA = "spark_catalog", "dq_t"
FILES = ["checks.yml", "dev/synthetic_checks.yml"]


def rows(spark, view):
    return [r.asDict() for r in spark.sql(f"SELECT * FROM {CATALOG}.{SCHEMA}.{view}").collect()]


@pytest.fixture
def deployed(spark, ws):
    for table in ("checks", "results"):
        spark.sql(f"DROP TABLE IF EXISTS {CATALOG}.{SCHEMA}.{table}")
    synthetic.seed_synthetic(spark, CATALOG, datetime.now(timezone.utc))
    service.deploy_config(spark, ws, CATALOG, SCHEMA, FILES)
    return spark


def test_synthetic_outcomes_match_expectations(deployed, ws):
    assert service.run_checks(deployed, ws, CATALOG, SCHEMA) == 2
    assert synthetic.verify(CATALOG, rows(deployed, "v_latest_results")) == []


def test_results_are_appended_and_views_report_them(deployed, ws):
    service.run_checks(deployed, ws, CATALOG, SCHEMA)
    service.run_checks(deployed, ws, CATALOG, SCHEMA)
    results = rows(deployed, "v_check_results")
    assert len(results) == 24  # 12 checks x 2 runs
    assert len({r["run_id"] for r in results}) == 2
    email = next(r for r in rows(deployed, "v_latest_results") if r["check_name"] == "email_not_null")
    assert (email["input_rows"], email["failed_rows"], email["max_fail_rate"]) == (1000, 50, 0.02)
    assert email["owner"] == "synthetic-owner@example.com" and email["criticality"] == "error"
    status = {r["table_name"].split(".")[-1]: r for r in rows(deployed, "v_onboarding_status")}
    assert (status["customers"]["failing"], status["orders"]["failing"]) == (3, 1)
    assert rows(deployed, "v_missing_results") == []
    assert rows(deployed, "v_daily_pass_rate")


def test_checks_without_a_run_are_reported_missing(deployed):
    assert len(rows(deployed, "v_missing_results")) == 12
    assert len(rows(deployed, "v_checks")) == 12


def test_deploy_is_idempotent_and_applies_metadata_only_changes(deployed, ws, tmp_path):
    service.deploy_config(deployed, ws, CATALOG, SCHEMA, FILES)
    assert deployed.table(f"{CATALOG}.{SCHEMA}.checks").count() == 12
    # DQX fingerprints ignore user_metadata, so prove an owner change still lands.
    changed = service.checks_io.read_text("dev/synthetic_checks.yml").replace("synthetic-owner@", "new-owner@")
    path = tmp_path / "c.yml"
    path.write_text(changed, encoding="utf-8")
    service.deploy_config(deployed, ws, CATALOG, SCHEMA, [str(path)])
    owners = {r["owner"] for r in rows(deployed, "v_onboarding_status")}
    assert owners == {"new-owner@example.com"}


def test_one_unreadable_table_does_not_stop_the_others(deployed, ws, tmp_path):
    broken = tmp_path / "broken.yml"
    broken.write_text(
        f"- table: {CATALOG}.dq_synthetic.no_such_table\n  user_metadata: {{owner: a@example.com}}\n"
        "  checks:\n    - {name: c, check: {function: is_not_null, arguments: {column: x}}}\n",
        encoding="utf-8",
    )
    service.deploy_config(deployed, ws, CATALOG, SCHEMA, FILES + [str(broken)])
    with pytest.raises(Exception):
        service.run_checks(deployed, ws, CATALOG, SCHEMA)
    assert synthetic.verify(CATALOG, rows(deployed, "v_latest_results")) == []
    assert [r["table_name"] for r in rows(deployed, "v_missing_results")] == [f"{CATALOG}.dq_synthetic.no_such_table"]


def test_alert_queries_run_against_the_views(deployed, ws):
    service.run_checks(deployed, ws, CATALOG, SCHEMA)
    counts = [deployed.sql(statement).first()["alert_count"] for statement in load_sql("alerts.sql", CATALOG, SCHEMA)]
    # 7a: 4 failing error checks; 7b: nothing missing or empty; 7c: only one run so no persistence
    assert counts == [4, 0, 0]
