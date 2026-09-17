"""Live SQL sources: a dataset that is pulled from a database, not uploaded from a laptop.

An analyst product that only reads files is a product people stop using the moment the
numbers matter, because the numbers live in a warehouse. A source here is a connection
string plus one read-only query; syncing it runs that query and files the result as the
next **version** of a dataset — which means every existing guarantee applies to it
unchanged: the same deterministic cleaning, the same profile, the same data contract
checked on arrival, the same monitors re-run, the same version diff.

Multi-table analysis comes free with the shape: the join lives in the query, where the
database can actually optimise it, and Numera receives one clean rectangle.

Safety, because a query string is user input that reaches a production database:

* **Read-only by construction.** The statement must be a single `SELECT` or `WITH`; a
  second statement, a DDL/DML keyword or a stacked query is rejected before a connection
  is opened. Where the driver supports it the session is opened read-only as well.
* **Bounded.** Rows are streamed in chunks and the pull stops at the configured row
  limit, so a mistyped `FROM` cannot pull a billion rows into memory.
* **Credentials stay server-side.** The DSN is stored for reuse and never returned to a
  client unredacted, and its password is stripped from error text before it is logged or
  shown — a failing connection must not become a way to read the password.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from app.core.config import Settings
from app.core.errors import AppError, InvalidInputError, NotFoundError
from app.core.serialization import frame_to_records
from app.db import Database
from app.services.datasets import DatasetService

logger = logging.getLogger(__name__)

# Dialect → the pip package that provides its driver, named in the error when missing.
DIALECTS: dict[str, dict[str, str]] = {
    "postgresql": {"driver": "psycopg", "package": "psycopg[binary]", "label": "PostgreSQL"},
    "mysql": {"driver": "pymysql", "package": "pymysql", "label": "MySQL / MariaDB"},
    "mssql": {"driver": "pyodbc", "package": "pyodbc", "label": "SQL Server"},
    "duckdb": {"driver": "duckdb_engine", "package": "duckdb-engine", "label": "DuckDB"},
    # sqlite needs nothing: the dialect ships with SQLAlchemy and the driver with Python.
    "sqlite": {"driver": "", "package": "", "label": "SQLite"},
}

# Anything that is not a read. Checked as whole words on a comment-stripped statement.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|merge|replace|"
    r"attach|detach|copy|vacuum|call|execute|exec|pragma|set|commit|rollback|begin|"
    r"savepoint|lock|load|install)\b",
    re.I,
)
LINE_COMMENT = re.compile(r"--[^\n]*")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
MAX_QUERY_CHARS = 20000
CHUNK_ROWS = 50_000
PREVIEW_ROWS = 20


class SourceService:
    """CRUD plus sync for SQL-backed datasets."""

    def __init__(self, settings: Settings, db: Database, datasets: DatasetService) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets

    # ------------------------------------------------------------------ CRUD
    def list(self) -> list[dict[str, Any]]:
        return [public(s) for s in self.db.list_sources()]

    def get(self, source_id: str) -> dict[str, Any]:
        record = self.db.get_source(source_id)
        if record is None:
            raise NotFoundError(f"Source '{source_id}' was not found.")
        return record

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.db.create_source(self._validate(payload))
        return public(record)

    def update(self, source_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get(source_id)
        fields: dict[str, Any] = {}
        if patch.get("name") is not None:
            fields["name"] = _name(patch["name"])
        if patch.get("query") is not None:
            fields["query"] = validate_query(patch["query"])
        if patch.get("dsn"):
            # An unchanged DSN arrives redacted; only a real new one replaces the stored one.
            fields["dsn"], fields["kind"] = _parse_dsn(patch["dsn"])
        if patch.get("refresh_minutes") is not None:
            fields["refresh_minutes"] = _refresh(patch["refresh_minutes"])
        if patch.get("enabled") is not None:
            fields["enabled"] = int(bool(patch["enabled"]))
        self.db.update_source(source_id, **fields)
        return public(self.db.get_source(source_id) or current)

    def delete(self, source_id: str) -> None:
        if not self.db.delete_source(source_id):
            raise NotFoundError(f"Source '{source_id}' was not found.")

    def _validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        dsn = payload.get("dsn")
        if not dsn or not str(dsn).strip():
            raise InvalidInputError("A connection string is required.")
        parsed_dsn, kind = _parse_dsn(str(dsn))
        query = validate_query(payload.get("query"))
        return {
            "name": _name(payload.get("name") or f"{DIALECTS[kind]['label']} query"),
            "kind": kind,
            "dsn": parsed_dsn,
            "query": query,
            "refresh_minutes": _refresh(payload.get("refresh_minutes")),
            "enabled": int(bool(payload.get("enabled", True))),
        }

    # ------------------------------------------------------------------ connection
    def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the query with a tiny cap and report what came back — or the real error."""
        config = self._validate(payload)
        frame = self._fetch(config["dsn"], config["query"], limit=PREVIEW_ROWS)
        preview = frame_to_records(frame.head(PREVIEW_ROWS))
        return {
            "ok": True,
            "kind": config["kind"],
            "columns": [str(c) for c in frame.columns],
            "row_sample": int(len(frame)),
            "preview": preview,
        }

    def sync(self, source_id: str) -> dict[str, Any]:
        """Pull the query and file the result as the next version of this source's dataset."""
        source = self.get(source_id)
        try:
            frame = self._fetch(source["dsn"], source["query"], limit=self.settings.max_rows)
            if frame.empty:
                raise InvalidInputError("The query returned no rows.")
            dataset = self._ingest(source, frame)
        except AppError as exc:
            self.db.update_source(
                source_id, last_status="error", last_error=redact(exc.message),
                last_synced_at=_now(),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced to the user as a typed error
            message = redact(str(exc))
            logger.warning("Source %s sync failed: %s", source_id, message)
            self.db.update_source(source_id, last_status="error", last_error=message,
                                  last_synced_at=_now())
            raise InvalidInputError(f"The sync failed: {message}") from exc

        self.db.update_source(
            source_id,
            dataset_id=dataset["id"],
            last_status="ok",
            last_error=None,
            last_synced_at=_now(),
            last_row_count=int(dataset["n_rows"]),
            sync_count=int(source.get("sync_count") or 0) + 1,
        )
        return {"source": public(self.db.get_source(source_id) or source), "dataset": dataset}

    def _ingest(self, source: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
        """Hand the pulled rows to the ordinary ingestion path.

        Deliberately via CSV: a SQL pull then goes through exactly the same type
        inference, cleaning audit trail and profiling as an uploaded file, instead of
        quietly taking a second route with different rules.
        """
        existing = source.get("dataset_id")
        replaces = existing if existing and self.db.get_dataset(existing) else None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{_slug(source['name'])}.csv"
            frame.to_csv(path, index=False)
            return self.datasets.ingest_path(
                path, path.name, name=source["name"], replaces=replaces,
            )

    def _fetch(self, dsn: str, query: str, *, limit: int) -> pd.DataFrame:
        """Stream the query in chunks and stop at `limit` rows."""
        engine = self._engine(dsn)
        chunks: list[pd.DataFrame] = []
        collected = 0
        try:
            with engine.connect() as connection:
                for chunk in pd.read_sql_query(_text(query), connection, chunksize=CHUNK_ROWS):
                    remaining = limit - collected
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk.head(remaining)
                    chunks.append(chunk)
                    collected += len(chunk)
                    if collected >= limit:
                        logger.info("Source query hit the %s row limit", f"{limit:,}")
                        break
        except InvalidInputError:
            raise
        except Exception as exc:  # noqa: BLE001 — every driver raises its own family
            raise InvalidInputError(f"The database rejected the query: {redact(str(exc))}") from exc
        finally:
            engine.dispose()
        if not chunks:
            return pd.DataFrame()
        return pd.concat(chunks, ignore_index=True)

    def _engine(self, dsn: str) -> Any:
        sqlalchemy = _sqlalchemy()
        kind = _dialect_of(dsn)
        _require_driver(kind)
        connect_args: dict[str, Any] = {}
        if kind == "postgresql":
            # Belt and braces: the statement check already forbids writes.
            connect_args = {"connect_timeout": 10, "options": "-c default_transaction_read_only=on"}
        elif kind == "mysql":
            connect_args = {"connect_timeout": 10}
        elif kind == "sqlite":
            connect_args = {"timeout": 10}
        try:
            return sqlalchemy.create_engine(dsn, connect_args=connect_args, pool_pre_ping=True)
        except Exception as exc:  # noqa: BLE001
            raise InvalidInputError(f"Could not open the connection: {redact(str(exc))}") from exc


# ----------------------------------------------------------------------------- validation


def validate_query(query: Any) -> str:
    """Accept one read-only statement and nothing else."""
    if not query or not str(query).strip():
        raise InvalidInputError("A SQL query is required.")
    text = str(query).strip()
    if len(text) > MAX_QUERY_CHARS:
        raise InvalidInputError(f"The query is limited to {MAX_QUERY_CHARS:,} characters.")

    # Comments and string literals can legitimately contain any word, so they are removed
    # before the keyword check rather than being allowed to trigger or mask it.
    stripped = STRING_LITERAL.sub("''", BLOCK_COMMENT.sub(" ", LINE_COMMENT.sub(" ", text)))
    body = stripped.strip().rstrip(";").strip()
    if not body:
        raise InvalidInputError("A SQL query is required.")
    if ";" in body:
        raise InvalidInputError(
            "Only one statement is allowed. Remove the ';' and everything after it."
        )
    if not re.match(r"^\(*\s*(select|with)\b", body, re.I):
        raise InvalidInputError("Only SELECT (or WITH … SELECT) queries can be used as a source.")
    forbidden = FORBIDDEN.search(body)
    if forbidden:
        raise InvalidInputError(
            f"'{forbidden.group(0).upper()}' is not allowed — a source query must only read data."
        )
    return text.rstrip().rstrip(";")


def _parse_dsn(dsn: str) -> tuple[str, str]:
    value = dsn.strip()
    if REDACTED in value:
        raise InvalidInputError(
            "That connection string is the redacted one shown in the UI. Paste the real "
            "connection string, or leave the field empty to keep the stored one."
        )
    kind = _dialect_of(value)
    if kind not in DIALECTS:
        supported = ", ".join(sorted(DIALECTS))
        raise InvalidInputError(f"Unsupported database '{kind}'. Supported: {supported}.")
    return value, kind


def _dialect_of(dsn: str) -> str:
    scheme = urlsplit(dsn).scheme
    if not scheme:
        raise InvalidInputError(
            "The connection string must start with the database type, e.g. "
            "postgresql+psycopg://user:password@host:5432/database"
        )
    return scheme.split("+")[0].lower()


def _require_driver(kind: str) -> None:
    info = DIALECTS[kind]
    if not info["driver"]:
        return
    import importlib.util

    if importlib.util.find_spec(info["driver"]) is None:
        raise InvalidInputError(
            f"{info['label']} support needs the '{info['package']}' package. "
            f"Install it in the backend environment: pip install '{info['package']}'"
        )


def _sqlalchemy() -> Any:
    try:
        import sqlalchemy
    except ImportError as exc:  # pragma: no cover — declared in requirements.txt
        raise InvalidInputError(
            "SQL sources need SQLAlchemy. Install it with: pip install 'sqlalchemy>=2.0'"
        ) from exc
    return sqlalchemy


def _text(query: str) -> Any:
    return _sqlalchemy().text(query)


def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise InvalidInputError("A name is required.")
    return name[:120]


def _refresh(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("The refresh interval must be a whole number of minutes.") from exc
    if not 0 <= minutes <= 10080:
        raise InvalidInputError("The refresh interval must be between 0 and 10,080 minutes (7 days).")
    return minutes


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "source"


def _now() -> str:
    from app.db import utcnow

    return utcnow()


# ----------------------------------------------------------------------------- redaction

REDACTED = "••••"


def redact(text: str) -> str:
    """Strip passwords out of anything that might be shown or logged.

    A driver's connection error usually quotes the whole DSN back at you. That is exactly
    the string that must not reach a delivery log, a browser or a log file.
    """
    def scrub(match: re.Match[str]) -> str:
        return match.group(0).replace(match.group("password"), REDACTED)

    text = re.sub(r"(?P<scheme>[a-z0-9+]+)://[^:/\s]+:(?P<password>[^@\s]+)@", scrub, text, flags=re.I)
    text = re.sub(r"(?i)\b(password|pwd|passwd)\s*=\s*[^;\s,)]+", r"\1=" + REDACTED, text)
    return text[:500]


def public(record: dict[str, Any]) -> dict[str, Any]:
    """The client-facing shape: everything except the credentials."""
    safe = {k: v for k, v in record.items() if k != "dsn"}
    safe["dsn_redacted"] = redact_dsn(record.get("dsn") or "")
    return safe


def redact_dsn(dsn: str) -> str:
    """postgresql://u:secret@host/db → postgresql://u:••••@host/db"""
    if not dsn:
        return ""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return REDACTED
    if not parts.netloc or "@" not in parts.netloc:
        return dsn
    credentials, _, host = parts.netloc.rpartition("@")
    user, _, password = credentials.partition(":")
    netloc = f"{user}:{REDACTED}@{host}" if password else f"{user}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


__all__ = ["DIALECTS", "SourceService", "public", "redact", "redact_dsn", "validate_query"]
