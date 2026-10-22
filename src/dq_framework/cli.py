"""Entry points, one per Lakeflow Job task (see [project.scripts] in pyproject.toml).

Each parses sys.argv, so the bundle passes job ``parameters`` as command-line arguments.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from . import checks_io, service, synthetic
from .naming import fq


def _split(value: Optional[str]) -> list[str]:
    """Comma-separated job parameter -> list. 'none' means empty (job parameters cannot be blank)."""
    if value is None or value.strip().lower() in {"", "none", "-"}:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse(
    description: str,
    argv: Optional[Sequence[str]],
    extra: Optional[Callable[[argparse.ArgumentParser], None]] = None,
    needs_catalog: bool = True,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    if needs_catalog:
        parser.add_argument("--catalog", required=True, help="Unity Catalog catalog for the DQ schema")
        parser.add_argument("--schema", default="dq", help="schema holding the framework tables")
    if extra:
        extra(parser)
    return parser.parse_args(argv)


def _spark():
    from pyspark.sql import SparkSession  # imported lazily so config validation needs no session

    return SparkSession.builder.getOrCreate()


def _workspace_client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def deploy_config_main(argv: Optional[Sequence[str]] = None) -> None:
    """Create/update the tables and views and load the check YAML into the checks table."""
    a = _parse(
        "Create DQ tables and views, then load the check YAML", argv,
        lambda p: p.add_argument("--check-files", default="checks.yml",
                                 help="comma-separated YAML files shipped in the wheel (or absolute paths)"),
    )  # fmt: skip
    counts = service.deploy_config(_spark(), _workspace_client(), a.catalog, a.schema, _split(a.check_files))
    print(f"deployed {sum(counts.values())} checks on {len(counts)} tables to {a.catalog}.{a.schema}")


def run_main(argv: Optional[Sequence[str]] = None) -> None:
    """Run every deployed check and append the summary metrics to results. Failing checks do not fail the job."""
    a = _parse("Run all DQ checks", argv, lambda p: p.add_argument("--max-workers", type=int, default=4))
    n = service.run_checks(_spark(), _workspace_client(), a.catalog, a.schema, a.max_workers)
    print(f"ran checks on {n} tables")


def validate_config_main(argv: Optional[Sequence[str]] = None) -> None:
    """Static validation of the shipped YAML. No Spark session or workspace needed, so it runs in CI."""
    a = _parse(
        "Validate check files", argv,
        lambda p: p.add_argument("--check-files", default="checks.yml,dev/synthetic_checks.yml"),
        needs_catalog=False,
    )  # fmt: skip
    try:
        by_table = checks_io.load_checks(_split(a.check_files), "validation_catalog")
    except checks_io.ConfigError as exc:
        raise SystemExit("config validation failed:\n  " + "\n  ".join(exc.errors))
    print(f"config OK: {sum(map(len, by_table.values()))} checks on {len(by_table)} tables")


# --------------------------------------------------------------------------- dev/test only


def seed_synthetic_main(argv: Optional[Sequence[str]] = None) -> None:
    """Create synthetic tables with known defects. Refuses to run outside dev/test."""
    a = _parse(
        "Seed synthetic data (dev/test only)", argv,
        lambda p: p.add_argument("--environment", required=True),
    )  # fmt: skip
    if a.environment not in ("dev", "test"):
        raise SystemExit(f"refusing to seed synthetic data in environment '{a.environment}'")
    synthetic.seed_synthetic(_spark(), a.catalog, datetime.now(timezone.utc))
    print(f"seeded {a.catalog}.{synthetic.SYNTHETIC_SCHEMA}")


def verify_synthetic_main(argv: Optional[Sequence[str]] = None) -> None:
    """Assert the latest run produced exactly the expected outcomes for the synthetic tables."""
    a = _parse("Verify synthetic outcomes (dev/test only)", argv,
               lambda p: p.add_argument("--environment", required=True))  # fmt: skip
    if a.environment not in ("dev", "test"):
        raise SystemExit(f"refusing to verify synthetic data in environment '{a.environment}'")
    rows = _spark().sql(f"SELECT table_name, check_name, status FROM {fq(a.catalog, a.schema, 'v_latest_results')}")
    problems = synthetic.verify(a.catalog, [r.asDict() for r in rows.collect()])
    if problems:
        raise SystemExit("synthetic verification failed:\n  " + "\n  ".join(problems))
    print("synthetic verification passed")
