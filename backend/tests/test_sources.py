"""SQL sources: query validation, credential redaction and the sync-to-version path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.errors import InvalidInputError
from app.db import Database
from app.services.datasets import DatasetService
from app.services.sources import SourceService, redact, redact_dsn, validate_query


@pytest.fixture
def warehouse(tmp_path: Path) -> str:
    """A throwaway SQLite database standing in for a warehouse."""
    path = tmp_path / "warehouse.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER, region TEXT, revenue REAL, ordered_on TEXT)")
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?)",
        [(i, ["North", "South", "East"][i % 3], 100.0 + i, f"2024-01-{(i % 28) + 1:02d}")
         for i in range(60)],
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture
def service(settings) -> SourceService:
    db = Database(settings.database_path)
    return SourceService(settings, db, DatasetService(settings, db))


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("query", [
    "SELECT * FROM orders",
    "select id, revenue from orders where revenue > 100",
    "WITH recent AS (SELECT * FROM orders) SELECT region, SUM(revenue) FROM recent GROUP BY region",
    "  SELECT 1;  ",
    "(SELECT 1)",
])
def test_read_only_queries_are_accepted(query):
    assert validate_query(query)


@pytest.mark.parametrize("query", [
    "DROP TABLE orders",
    "SELECT 1; DROP TABLE orders",
    "INSERT INTO orders VALUES (1)",
    "UPDATE orders SET revenue = 0",
    "DELETE FROM orders",
    "SELECT * FROM orders; DELETE FROM orders",
    "CREATE TABLE evil (id INT)",
    "ATTACH DATABASE '/etc/passwd' AS pw",
    "COPY orders TO '/tmp/out.csv'",
    "GRANT ALL ON orders TO PUBLIC",
])
def test_writes_and_stacked_statements_are_rejected(query):
    with pytest.raises(InvalidInputError):
        validate_query(query)


def test_a_write_hidden_in_a_comment_does_not_slip_through():
    # The comment is stripped, so the DELETE after it is a second statement, not a comment.
    with pytest.raises(InvalidInputError):
        validate_query("SELECT * FROM orders -- harmless\n; DELETE FROM orders")


def test_a_forbidden_word_inside_a_string_literal_is_not_a_write():
    # 'delete' is data here, not a keyword — rejecting it would be a false positive.
    assert validate_query("SELECT * FROM events WHERE action = 'delete'")


def test_a_semicolon_inside_a_string_literal_is_not_a_second_statement():
    assert validate_query("SELECT * FROM t WHERE note = 'a; b'")


def test_an_empty_query_is_refused():
    with pytest.raises(InvalidInputError):
        validate_query("   ")
    with pytest.raises(InvalidInputError):
        validate_query(None)


# --------------------------------------------------------------------------- redaction


def test_a_password_never_survives_into_a_message_or_a_log():
    dsn = "postgresql+psycopg://analyst:s3cr3t-pw@warehouse.internal:5432/prod"
    assert "s3cr3t-pw" not in redact_dsn(dsn)
    assert "analyst" in redact_dsn(dsn)
    assert "warehouse.internal" in redact_dsn(dsn)

    error = f"could not connect to server using {dsn}"
    assert "s3cr3t-pw" not in redact(error)
    assert "s3cr3t" not in redact("Login failed: Password=s3cr3t;Server=db")


def test_redaction_leaves_a_credential_free_dsn_alone():
    assert redact_dsn("sqlite:///data/warehouse.db") == "sqlite:///data/warehouse.db"
    assert redact_dsn("") == ""


def test_the_stored_credentials_are_not_returned_to_a_client(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse, "query": "SELECT * FROM orders"})
    assert "dsn" not in source
    assert source["dsn_redacted"].startswith("sqlite://")

    listed = service.list()[0]
    assert "dsn" not in listed


def test_pasting_the_redacted_string_back_is_refused(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse, "query": "SELECT 1"})
    with pytest.raises(InvalidInputError, match="redacted"):
        service.update(source["id"], {"dsn": "postgresql://u:\u2022\u2022\u2022\u2022@h/db"})


def test_an_unsupported_dialect_is_named(service):
    with pytest.raises(InvalidInputError, match="Unsupported database"):
        service.create({"dsn": "cassandra://host/keyspace", "query": "SELECT 1"})
    with pytest.raises(InvalidInputError, match="must start with the database type"):
        service.create({"dsn": "just-a-host-name", "query": "SELECT 1"})


# --------------------------------------------------------------------------- connection


def test_test_connection_returns_columns_and_a_preview(service, warehouse):
    result = service.test({"name": "Orders", "dsn": warehouse,
                           "query": "SELECT region, revenue FROM orders"})
    assert result["ok"] is True
    assert result["columns"] == ["region", "revenue"]
    assert result["row_sample"] > 0
    assert result["preview"]["rows"]


def test_a_broken_query_reports_the_database_error(service, warehouse):
    with pytest.raises(InvalidInputError, match="rejected the query"):
        service.test({"dsn": warehouse, "query": "SELECT * FROM does_not_exist"})


def test_sync_creates_a_dataset_that_went_through_normal_cleaning(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse,
                             "query": "SELECT * FROM orders"})
    result = service.sync(source["id"])
    dataset = result["dataset"]

    assert dataset["n_rows"] == 60
    assert dataset["version"] == 1
    # The SQL pull takes the same route as an upload, so it has a cleaning audit trail
    # and a profile with inferred column roles.
    assert dataset["cleaning"]["actions"] is not None
    assert "ordered_on" in dataset["profile"]["roles"].get("datetime", [])
    assert result["source"]["last_status"] == "ok"
    assert result["source"]["last_row_count"] == 60


def test_a_second_sync_files_a_new_version_of_the_same_dataset(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse, "query": "SELECT * FROM orders"})
    first = service.sync(source["id"])["dataset"]
    second = service.sync(source["id"])["dataset"]

    assert second["version"] == 2
    assert second["root_dataset_id"] == first["id"]
    assert second["version_diff"] is not None


def test_sync_records_the_failure_without_losing_the_source(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse, "query": "SELECT * FROM orders"})
    service.update(source["id"], {"query": "SELECT * FROM vanished"})

    with pytest.raises(InvalidInputError):
        service.sync(source["id"])

    stored = service.db.get_source(source["id"])
    assert stored["last_status"] == "error"
    assert stored["last_error"]


def test_the_row_limit_bounds_the_pull(settings, warehouse):
    settings.max_rows = 10
    db = Database(settings.database_path)
    service = SourceService(settings, db, DatasetService(settings, db))
    source = service.create({"name": "Orders", "dsn": warehouse, "query": "SELECT * FROM orders"})
    assert service.sync(source["id"])["dataset"]["n_rows"] == 10


def test_a_query_returning_nothing_is_an_error_not_an_empty_dataset(service, warehouse):
    source = service.create({"name": "Orders", "dsn": warehouse,
                             "query": "SELECT * FROM orders WHERE revenue < 0"})
    with pytest.raises(InvalidInputError, match="no rows"):
        service.sync(source["id"])


def test_refresh_interval_is_validated(service, warehouse):
    with pytest.raises(InvalidInputError, match="between 0 and 10,080"):
        service.create({"dsn": warehouse, "query": "SELECT 1", "refresh_minutes": 99999})
