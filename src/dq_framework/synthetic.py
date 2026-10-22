"""Synthetic data for dev/test: tables with known defects and the outcomes we expect.

Defect rates are deterministic (every Nth row), so expected PASS/FAIL results are exact. The smoke
test job runs seed -> deploy-config -> run -> verify after each dev/test deployment. Never runs in prod.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from .naming import check_identifier

SYNTHETIC_SCHEMA = "dq_synthetic"
CUSTOMERS_N = 1000
ORDERS_N = 5000
OWNER = "synthetic-owner@example.com"

CUSTOMERS_DDL = "customer_id INT, email STRING, country_code STRING, loaded_at TIMESTAMP"
ORDERS_DDL = "order_id INT, amount DOUBLE, status STRING, loaded_at TIMESTAMP"


def customer_rows(now: datetime, n: int = CUSTOMERS_N) -> list[tuple]:
    """5% null emails, 2% invalid country codes, unique ids, data loaded 48h ago (stale)."""
    countries = ("AU", "US", "NZ", "GB")
    return [
        (
            i,
            None if i % 20 == 0 else f"user{i}@example.com",
            "XX1" if i % 50 == 0 else countries[i % 4],
            now - timedelta(hours=48),
        )
        for i in range(n)
    ]


def order_rows(now: datetime, n: int = ORDERS_N) -> list[tuple]:
    """Clean and fresh, but only 5,000 rows (the row_count_min check asks for 10,000)."""
    statuses = ("NEW", "PAID", "SHIPPED")
    return [(i, 10.0 + (i % 90), statuses[i % 3], now) for i in range(n)]


def expected_status(catalog: str) -> dict[tuple[str, str], str]:
    """{(table, check name): status} for the checks in config/dev/synthetic_checks.yml."""
    c, o = f"{catalog}.{SYNTHETIC_SCHEMA}.customers".lower(), f"{catalog}.{SYNTHETIC_SCHEMA}.orders".lower()
    return {
        (c, "customer_id_unique"): "PASS",
        (c, "email_not_null"): "FAIL",  # 5% null > 2% allowed
        (c, "email_format"): "PASS",  # nulls are not format failures
        (c, "country_code_format"): "FAIL",  # 2% bad > 1% allowed
        (c, "loaded_within_a_day"): "FAIL",  # ~48h old
        (c, "row_count_min"): "PASS",  # 1000 >= 500
        (o, "order_id_unique"): "PASS",
        (o, "order_id_not_null"): "PASS",
        (o, "amount_not_null"): "PASS",  # no nulls
        (o, "status_valid"): "PASS",
        (o, "loaded_within_a_day"): "PASS",
        (o, "row_count_min"): "FAIL",  # 5000 < 10000
    }


def verify(catalog: str, results: Iterable[Mapping[str, Any]]) -> list[str]:
    """Returns a list of mismatches; empty means the service behaved as designed."""
    prefix = f"{catalog}.{SYNTHETIC_SCHEMA}.".lower()
    latest = {(r["table_name"].lower(), r["check_name"]): r["status"] for r in results
              if r["table_name"].lower().startswith(prefix)}  # fmt: skip
    want = expected_status(catalog)
    problems = [f"{t} | {n}: expected {w}, got {latest.get((t, n))}" for (t, n), w in want.items()
                if latest.get((t, n)) != w]  # fmt: skip
    problems += [f"{t} | {n}: unexpected result (status {latest[(t, n)]})" for t, n in sorted(set(latest) - set(want))]
    return problems


def seed_synthetic(spark: Any, catalog: str, now: datetime) -> None:
    """Create/replace the synthetic tables (needs a SparkSession)."""
    check_identifier(catalog, "catalog")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{SYNTHETIC_SCHEMA}`")
    frames = {
        "customers": (customer_rows(now), CUSTOMERS_DDL),
        "orders": (order_rows(now), ORDERS_DDL),
    }
    for table, (rows, ddl) in frames.items():
        fq = f"`{catalog}`.`{SYNTHETIC_SCHEMA}`.`{table}`"
        (spark.createDataFrame(rows, ddl).write.mode("overwrite")
            .option("overwriteSchema", "true").saveAsTable(fq))  # fmt: skip
