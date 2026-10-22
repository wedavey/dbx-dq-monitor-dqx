import os
import sys
import urllib.parse
from unittest.mock import create_autospec

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

# Make Spark workers use the same interpreter as the test driver.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    builder = (
        SparkSession.builder.master("local[2]").appName("tests")
        .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("warehouse")))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.sources.default", "delta")  # the Databricks default
        .config("spark.sql.shuffle.partitions", "2").config("spark.ui.enabled", "false")
    )  # fmt: skip
    s = configure_spark_with_delta_pip(builder).getOrCreate()
    yield s
    s.stop()


@pytest.fixture
def ws(spark):
    """A WorkspaceClient stand-in. DQX only asks it whether a table exists."""
    client = create_autospec(WorkspaceClient)

    def get(full_name):
        if not spark.catalog.tableExists(urllib.parse.unquote(full_name)):
            raise NotFound(full_name)

    client.tables.get.side_effect = get
    return client
