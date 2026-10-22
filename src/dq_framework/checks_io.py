"""Load and validate the check YAML. No Spark session needed.

The YAML holds plain DQX metadata checks grouped by table. This module only adds the conventions the
service relies on (unique names, an owner, a numeric ``max_fail_rate``) and returns, per table, the
list of check dicts that ``DQEngine.save_checks`` stores unchanged.
"""

from __future__ import annotations

import os
from importlib import resources
from typing import Any

import yaml
from databricks.labs.dqx.engine import DQEngine

from .naming import is_valid_table_name

_TABLE_KEYS = {"table", "user_metadata", "checks"}


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def read_text(ref: str) -> str:
    """``ref`` is a filesystem path (absolute, ./ or ../) or a file shipped inside the wheel."""
    if os.path.isabs(ref) or ref.startswith(("./", "../")):
        with open(ref, encoding="utf-8") as handle:
            return handle.read()
    return resources.files("dq_framework").joinpath("config", *ref.split("/")).read_text(encoding="utf-8")


def _metadata(*layers: Any) -> dict[str, str]:
    """Merge user_metadata layers (later wins). DQX stores metadata as map<string, string>."""
    merged: dict[str, str] = {}
    for layer in layers:
        merged.update({str(k): str(v) for k, v in (layer or {}).items()})
    return merged


def load_checks(refs: list[str], catalog: str) -> dict[str, list[dict]]:
    """{table: [DQX check dicts]} from the YAML files. Raises ConfigError listing every problem."""
    errors: list[str] = []
    by_table: dict[str, list[dict]] = {}
    for ref in refs:
        try:
            text = read_text(ref).replace("${catalog}", catalog)
            blocks = yaml.safe_load(text) or []
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{ref}: cannot read: {exc}")
            continue
        if not isinstance(blocks, list):
            errors.append(f"{ref}: expected a list of table blocks")
            continue
        for block in blocks:
            if not isinstance(block, dict) or set(block) - _TABLE_KEYS:
                errors.append(f"{ref}: table block must be a mapping with keys {sorted(_TABLE_KEYS)}")
                continue
            table = block.get("table")
            if not is_valid_table_name(table):
                errors.append(f"{ref}: invalid table {table!r}; expected catalog.schema.table")
                continue
            if table.lower() in {t.lower() for t in by_table}:
                errors.append(f"{table}: defined more than once")
                continue
            checks = [dict(c, user_metadata=_metadata(block.get("user_metadata"), c.get("user_metadata")))
                      for c in block.get("checks") or [] if isinstance(c, dict)]  # fmt: skip
            by_table[table] = checks
    for table, checks in by_table.items():
        errors += _validate(table, checks)
    if errors:
        raise ConfigError(errors)
    return by_table


def _validate(table: str, checks: list[dict]) -> list[str]:
    errors: list[str] = []
    if not checks:
        errors.append(f"{table}: no checks")
    seen: set[str] = set()
    for i, check in enumerate(checks):
        name = check.get("name")
        label = f"{table}: check {name or i + 1}"
        if not name:
            errors.append(f"{label}: `name` is required")
        elif name in seen:
            errors.append(f"{label}: duplicate name")
        seen.add(name)
        meta = check["user_metadata"]
        if not meta.get("owner"):
            errors.append(f"{label}: user_metadata.owner is required (on the check or the table)")
        try:
            if not 0 <= float(meta.get("max_fail_rate", 0)) <= 1:
                raise ValueError
        except ValueError:
            errors.append(f"{label}: max_fail_rate must be a number between 0 and 1")
    status = DQEngine.validate_checks(checks)
    errors += [f"{table}: {e}" for e in status.errors]
    return errors
