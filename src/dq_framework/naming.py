"""Validated names for the framework's own tables."""

from __future__ import annotations

import re

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_TABLE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def check_identifier(value: str, what: str = "identifier") -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ValueError(f"invalid {what}: {value!r} (letters, digits and underscore only)")
    return value


def is_valid_table_name(name: object) -> bool:
    return isinstance(name, str) and bool(_TABLE.match(name))


def fq(catalog: str, schema: str, name: str) -> str:
    """Fully qualified, validated table name. Unquoted: DQX takes plain ``catalog.schema.table``."""
    return ".".join(check_identifier(p, "name") for p in (catalog, schema, name))
