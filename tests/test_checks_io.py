import textwrap

import pytest

from dq_framework import checks_io, cli


def write(tmp_path, text):
    path = tmp_path / "checks.yml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(path)


GOOD = """
    - table: ${catalog}.s.t
      user_metadata: {owner: a@example.com, data_product: dp}
      checks:
        - name: id_unique
          check: {function: is_unique, arguments: {columns: [id]}}
        - name: email_not_null
          criticality: warn
          check: {function: is_not_null, arguments: {column: email}}
          user_metadata: {max_fail_rate: 0.02, owner: b@example.com}
"""


def test_load_substitutes_catalog_and_merges_metadata(tmp_path):
    by_table = checks_io.load_checks([write(tmp_path, GOOD)], "main")
    first, second = by_table["main.s.t"]
    assert first["user_metadata"] == {"owner": "a@example.com", "data_product": "dp"}
    assert second["user_metadata"] == {"owner": "b@example.com", "data_product": "dp", "max_fail_rate": "0.02"}


@pytest.mark.parametrize(
    "bad, message",
    [
        (GOOD.replace("name: id_unique", "name: email_not_null"), "duplicate name"),
        (GOOD.replace("          check: {function: is_unique", "          check: {function: nope"), "nope"),
        (GOOD.replace("user_metadata: {owner: a@example.com, data_product: dp}", "user_metadata: {}"), "owner is required"),
        (GOOD.replace("max_fail_rate: 0.02", "max_fail_rate: 2"), "between 0 and 1"),
        (GOOD.replace("- name: id_unique", "- {}\n        - name: id_unique"), "`name` is required"),
        (GOOD.replace("${catalog}.s.t", "main.t"), "invalid table"),
        (GOOD + GOOD, "defined more than once"),
    ],
)
def test_load_rejects_bad_config(tmp_path, bad, message):
    with pytest.raises(checks_io.ConfigError, match=message):
        checks_io.load_checks([write(tmp_path, bad)], "main")


def test_shipped_config_is_valid():
    cli.validate_config_main([])  # raises SystemExit on any problem


def test_validate_config_reports_problems(tmp_path):
    with pytest.raises(SystemExit, match="config validation failed"):
        cli.validate_config_main(["--check-files", write(tmp_path, "- {table: x}")])


def test_job_parameter_splitting():
    assert cli._split("a, b,c") == ["a", "b", "c"]
    assert cli._split("none") == [] and cli._split("") == [] and cli._split(None) == []


def test_seed_and_verify_refuse_to_run_in_prod():
    for main in (cli.seed_synthetic_main, cli.verify_synthetic_main):
        with pytest.raises(SystemExit, match="refusing"):
            main(["--catalog", "prod", "--environment", "prod"])
