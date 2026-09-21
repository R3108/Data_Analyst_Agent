"""SQLite persistence for datasets, chat sessions, messages, boards and share links."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_type         TEXT NOT NULL,
    sheet_name        TEXT,
    n_rows            INTEGER NOT NULL,
    n_cols            INTEGER NOT NULL,
    size_bytes        INTEGER NOT NULL,
    profile_json      TEXT NOT NULL,
    cleaning_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    -- version lineage: root_dataset_id groups every upload of the same table
    parent_dataset_id TEXT,
    root_dataset_id   TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    semantics_json    TEXT,
    version_diff_json TEXT,
    -- data contract (inherited across versions) and the last evaluation of it
    contract_json        TEXT,
    contract_result_json TEXT,
    -- personal-data scan and the redaction policy, also inherited across versions
    privacy_json         TEXT,
    privacy_scan_json    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    dataset_id  TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    payload_json TEXT,
    status      TEXT NOT NULL DEFAULT 'complete',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boards (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    share_token TEXT UNIQUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Board items are snapshots: they survive deletion of the analysis they came from.
CREATE TABLE IF NOT EXISTS board_items (
    id                TEXT PRIMARY KEY,
    board_id          TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('kpi', 'chart', 'table', 'insight', 'note')),
    title             TEXT NOT NULL,
    content_json      TEXT NOT NULL,
    source_session_id TEXT,
    source_question   TEXT,
    dataset_name      TEXT,
    wide              INTEGER NOT NULL DEFAULT 0,
    position          INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

-- Metric monitors: a snapshot of analysis code plus a threshold, re-run on demand,
-- on a schedule, or automatically when a new version of the dataset is uploaded.
CREATE TABLE IF NOT EXISTS monitors (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    dataset_id        TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    source_session_id TEXT,
    question          TEXT,
    kpi_label         TEXT NOT NULL,
    kpi_index         INTEGER NOT NULL DEFAULT 0,
    kpi_format        TEXT NOT NULL DEFAULT 'auto',
    code              TEXT NOT NULL,
    direction         TEXT NOT NULL CHECK (direction IN ('above', 'below', 'change_pct')),
    threshold         REAL NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    baseline_value    REAL,
    last_value        REAL,
    last_status       TEXT,
    last_run_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Alert delivery: where a breach, a recovery or a failed data contract should land.
CREATE TABLE IF NOT EXISTS alert_channels (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('slack', 'webhook', 'email')),
    target       TEXT NOT NULL,
    events       TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_status  TEXT,
    last_error   TEXT,
    last_sent_at TEXT,
    sent_count   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id         TEXT PRIMARY KEY,
    channel_id TEXT REFERENCES alert_channels(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_runs (
    id             TEXT PRIMARY KEY,
    monitor_id     TEXT NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    dataset_id     TEXT,
    value          REAL,
    previous_value REAL,
    change_pct     REAL,
    status         TEXT NOT NULL,
    breached       INTEGER NOT NULL DEFAULT 0,
    detail         TEXT,
    duration_ms    INTEGER,
    -- deterministic drill-down attached to a breach: why the number moved
    root_cause_json TEXT,
    created_at     TEXT NOT NULL
);

-- A SQL warehouse or database a dataset is pulled from, and re-pulled on a schedule.
-- `dsn` holds credentials, so it is never returned to a client unredacted.
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    dsn             TEXT NOT NULL,
    query           TEXT NOT NULL,
    dataset_id      TEXT,
    refresh_minutes INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT,
    last_error      TEXT,
    last_synced_at  TEXT,
    last_row_count  INTEGER,
    sync_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Threaded discussion on an analysis, a board or a single pinned item.
CREATE TABLE IF NOT EXISTS comments (
    id           TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('session', 'board', 'message', 'board_item')),
    subject_id   TEXT NOT NULL,
    parent_id    TEXT REFERENCES comments(id) ON DELETE CASCADE,
    author       TEXT NOT NULL,
    body         TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Append-only record of who did what. Bounded; never a source of truth for state.
CREATE TABLE IF NOT EXISTS activity (
    id            TEXT PRIMARY KEY,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    subject_kind  TEXT,
    subject_id    TEXT,
    subject_title TEXT,
    detail        TEXT,
    created_at    TEXT NOT NULL
);

-- A saved question re-run on a cadence and delivered to the alert channels.
CREATE TABLE IF NOT EXISTS briefings (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    schedule_hours  INTEGER NOT NULL DEFAULT 24,
    enabled         INTEGER NOT NULL DEFAULT 1,
    deliver         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT,
    last_status     TEXT,
    last_session_id TEXT,
    last_message_id TEXT,
    last_headline   TEXT,
    last_error      TEXT,
    run_count       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

"""

# Applied only after MIGRATIONS: on a database created by an older version the tables
# above already exist (so CREATE TABLE IF NOT EXISTS is a no-op) and an index over a
# newly added column would fail until that column has actually been added.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(role, created_at);
CREATE INDEX IF NOT EXISTS idx_board_items_board ON board_items(board_id, position);
CREATE INDEX IF NOT EXISTS idx_datasets_lineage ON datasets(root_dataset_id, version);
CREATE INDEX IF NOT EXISTS idx_monitors_dataset ON monitors(dataset_id);
CREATE INDEX IF NOT EXISTS idx_monitor_runs_monitor ON monitor_runs(monitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries ON alert_deliveries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sources_dataset ON sources(dataset_id);
CREATE INDEX IF NOT EXISTS idx_comments_subject ON comments(subject_kind, subject_id, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_briefings_dataset ON briefings(dataset_id);
"""

# (table, column, DDL) — additive migrations for databases created by older versions.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("sessions", "share_token", "ALTER TABLE sessions ADD COLUMN share_token TEXT"),
    ("datasets", "parent_dataset_id", "ALTER TABLE datasets ADD COLUMN parent_dataset_id TEXT"),
    ("datasets", "root_dataset_id", "ALTER TABLE datasets ADD COLUMN root_dataset_id TEXT"),
    ("datasets", "version", "ALTER TABLE datasets ADD COLUMN version INTEGER NOT NULL DEFAULT 1"),
    ("datasets", "semantics_json", "ALTER TABLE datasets ADD COLUMN semantics_json TEXT"),
    ("datasets", "version_diff_json", "ALTER TABLE datasets ADD COLUMN version_diff_json TEXT"),
    ("datasets", "contract_json", "ALTER TABLE datasets ADD COLUMN contract_json TEXT"),
    ("datasets", "contract_result_json", "ALTER TABLE datasets ADD COLUMN contract_result_json TEXT"),
    ("datasets", "privacy_json", "ALTER TABLE datasets ADD COLUMN privacy_json TEXT"),
    ("datasets", "privacy_scan_json", "ALTER TABLE datasets ADD COLUMN privacy_scan_json TEXT"),
    ("monitor_runs", "root_cause_json", "ALTER TABLE monitor_runs ADD COLUMN root_cause_json TEXT"),
]
SHAREABLE_TABLES = {"session": "sessions", "board": "boards"}

# One write lock per database *file* rather than per `Database` object. Each account has
# its own file, so per-user work runs in parallel; but a workspace evicted from the
# in-memory cache and rebuilt on the next request yields a second `Database` pointed at
# the same file, and those two must still serialise against each other.
_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = _FILE_LOCKS[key] = threading.RLock()
        return lock


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.executescript(INDEXES)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, column, ddl in MIGRATIONS:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(ddl)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_share ON sessions(share_token)")
        # Datasets predating version lineage are each the root of their own history.
        conn.execute("UPDATE datasets SET root_dataset_id = id WHERE root_dataset_id IS NULL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # --- datasets --------------------------------------------------------------
    def insert_dataset(self, record: dict[str, Any]) -> None:
        payload = {
            "parent_dataset_id": None, "root_dataset_id": record["id"], "version": 1,
            "semantics_json": None, "version_diff_json": None, "contract_json": None,
            "contract_result_json": None, "privacy_json": None, "privacy_scan_json": None,
            **record,
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO datasets (id, name, original_filename, file_type, sheet_name,
                       n_rows, n_cols, size_bytes, profile_json, cleaning_json, created_at,
                       parent_dataset_id, root_dataset_id, version, semantics_json, version_diff_json,
                       contract_json, contract_result_json, privacy_json, privacy_scan_json)
                   VALUES (:id, :name, :original_filename, :file_type, :sheet_name,
                       :n_rows, :n_cols, :size_bytes, :profile_json, :cleaning_json, :created_at,
                       :parent_dataset_id, :root_dataset_id, :version, :semantics_json,
                       :version_diff_json, :contract_json, :contract_result_json,
                       :privacy_json, :privacy_scan_json)""",
                payload,
            )

    def list_datasets(self) -> list[dict[str, Any]]:
        """Latest version of each dataset lineage, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT d.id, d.name, d.original_filename, d.file_type, d.sheet_name, d.n_rows,
                          d.n_cols, d.size_bytes, d.created_at, d.version, d.root_dataset_id,
                          d.parent_dataset_id,
                          json_extract(d.contract_result_json, '$.status') AS contract_status,
                          (SELECT COUNT(*) FROM datasets v
                            WHERE v.root_dataset_id = d.root_dataset_id) AS version_count
                     FROM datasets d
                     JOIN (SELECT root_dataset_id, MAX(version) AS version
                             FROM datasets GROUP BY root_dataset_id) latest
                       ON latest.root_dataset_id = d.root_dataset_id
                      AND latest.version = d.version
                    ORDER BY d.created_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            return None
        return self._hydrate_dataset(dict(row))

    def update_dataset_profile(self, dataset_id: str, profile: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET profile_json = ? WHERE id = ?", (json.dumps(profile), dataset_id))

    def update_dataset_semantics(self, dataset_id: str, semantics: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET semantics_json = ? WHERE id = ?",
                         (json.dumps(semantics), dataset_id))

    def update_dataset_contract(self, dataset_id: str, contract: dict[str, Any] | None,
                                result: dict[str, Any] | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE datasets SET contract_json = ?, contract_result_json = ? WHERE id = ?",
                (json.dumps(contract) if contract else None,
                 json.dumps(result) if result else None, dataset_id),
            )

    def update_dataset_privacy(self, dataset_id: str, state: dict[str, Any] | None,
                               scan_result: dict[str, Any] | None = None) -> None:
        """Store the redaction policy and, when re-scanned, the findings behind it."""
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET privacy_json = ? WHERE id = ?",
                         (json.dumps(state) if state else None, dataset_id))
            if scan_result is not None:
                conn.execute("UPDATE datasets SET privacy_scan_json = ? WHERE id = ?",
                             (json.dumps(scan_result), dataset_id))

    def update_dataset_shape(self, dataset_id: str, *, n_rows: int, n_cols: int) -> None:
        """After a redaction drops a column the stored shape is stale, and it is shown."""
        with self.connect() as conn:
            conn.execute("UPDATE datasets SET n_rows = ?, n_cols = ? WHERE id = ?",
                         (int(n_rows), int(n_cols), dataset_id))

    def dataset_versions(self, root_dataset_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, name, original_filename, n_rows, n_cols, size_bytes, created_at,
                          version, parent_dataset_id, root_dataset_id, version_diff_json,
                          contract_result_json
                     FROM datasets WHERE root_dataset_id = ? ORDER BY version ASC""",
                (root_dataset_id,),
            ).fetchall()
        versions = []
        for row in rows:
            record = dict(row)
            raw = record.pop("version_diff_json", None)
            record["version_diff"] = json.loads(raw) if raw else None
            contract_result = record.pop("contract_result_json", None)
            record["contract_result"] = json.loads(contract_result) if contract_result else None
            versions.append(record)
        return versions

    def latest_dataset_version(self, dataset_id: str) -> dict[str, Any] | None:
        """The newest version in the same lineage as `dataset_id`."""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM datasets
                    WHERE root_dataset_id = (SELECT COALESCE(root_dataset_id, id) FROM datasets WHERE id = ?)
                    ORDER BY version DESC LIMIT 1""",
                (dataset_id,),
            ).fetchone()
        return self._hydrate_dataset(dict(row)) if row else None

    @staticmethod
    def _hydrate_dataset(record: dict[str, Any]) -> dict[str, Any]:
        record["profile"] = json.loads(record.pop("profile_json"))
        record["cleaning"] = json.loads(record.pop("cleaning_json"))
        semantics = record.pop("semantics_json", None)
        record["semantics"] = json.loads(semantics) if semantics else None
        diff = record.pop("version_diff_json", None)
        record["version_diff"] = json.loads(diff) if diff else None
        contract = record.pop("contract_json", None)
        record["contract"] = json.loads(contract) if contract else None
        contract_result = record.pop("contract_result_json", None)
        record["contract_result"] = json.loads(contract_result) if contract_result else None
        privacy = record.pop("privacy_json", None)
        record["privacy"] = json.loads(privacy) if privacy else None
        privacy_scan = record.pop("privacy_scan_json", None)
        record["privacy_scan"] = json.loads(privacy_scan) if privacy_scan else None
        record.setdefault("version", 1)
        record["root_dataset_id"] = record.get("root_dataset_id") or record["id"]
        return record

    def delete_dataset(self, dataset_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        return cur.rowcount > 0

    # --- sessions ----------------------------------------------------------------
    def create_session(self, dataset_id: str, title: str) -> dict[str, Any]:
        now = utcnow()
        record = {"id": new_id("ses"), "title": title, "dataset_id": dataset_id,
                  "created_at": now, "updated_at": now}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, dataset_id, created_at, updated_at) "
                "VALUES (:id, :title, :dataset_id, :created_at, :updated_at)",
                record,
            )
        return {**record, "share_token": None}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.id, s.title, s.dataset_id, s.created_at, s.updated_at, s.share_token,
                          d.name AS dataset_name,
                          (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
                   FROM sessions s JOIN datasets d ON d.id = s.dataset_id
                   ORDER BY s.updated_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT s.*, d.name AS dataset_name FROM sessions s
                   JOIN datasets d ON d.id = s.dataset_id WHERE s.id = ?""",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: str, *, title: str | None = None) -> None:
        with self.connect() as conn:
            if title is not None:
                conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title, utcnow(), session_id),
                )
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utcnow(), session_id))

    def delete_session(self, session_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0

    # --- messages ----------------------------------------------------------------
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
        status: str = "complete",
    ) -> dict[str, Any]:
        record = {
            "id": new_id("msg"),
            "session_id": session_id,
            "role": role,
            "content": content,
            "payload_json": json.dumps(payload) if payload is not None else None,
            "status": status,
            "created_at": utcnow(),
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO messages (id, session_id, role, content, payload_json, status, created_at)
                   VALUES (:id, :session_id, :role, :content, :payload_json, :status, :created_at)""",
                record,
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (record["created_at"], session_id))
        return self._hydrate_message(record)

    def list_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if limit:
                rows = conn.execute(
                    """SELECT * FROM (SELECT * FROM messages WHERE session_id = ?
                       ORDER BY created_at DESC LIMIT ?) ORDER BY created_at ASC""",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,),
                ).fetchall()
        return [self._hydrate_message(dict(r)) for r in rows]

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._hydrate_message(dict(row)) if row else None

    def question_before(self, message: dict[str, Any]) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND created_at <= ?
                   ORDER BY created_at DESC LIMIT 1""",
                (message["session_id"], message["created_at"]),
            ).fetchone()
        return row["content"] if row else None

    def assistant_payloads_since(self, since: str) -> list[tuple[str, dict[str, Any]]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT created_at, payload_json FROM messages
                   WHERE role = 'assistant' AND created_at >= ? AND payload_json IS NOT NULL""",
                (since,),
            ).fetchall()
        return [(r["created_at"], json.loads(r["payload_json"])) for r in rows]

    @staticmethod
    def _hydrate_message(record: dict[str, Any]) -> dict[str, Any]:
        raw = record.pop("payload_json", None)
        record["payload"] = json.loads(raw) if raw else None
        return record

    def recent_analyses(self, dataset_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Completed question/answer pairs, newest first — the corpus analysis memory searches.

        Scoped to the dataset's whole *lineage*: a question answered against last month's
        upload is still the same question about the same table.
        """
        query = """
            SELECT m.id AS message_id, m.session_id, m.created_at, m.payload_json,
                   s.title AS session_title, s.dataset_id, d.name AS dataset_name,
                   (SELECT u.content FROM messages u
                     WHERE u.session_id = m.session_id AND u.role = 'user'
                       AND u.created_at <= m.created_at
                     ORDER BY u.created_at DESC LIMIT 1) AS question
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
              JOIN datasets d ON d.id = s.dataset_id
             WHERE m.role = 'assistant' AND m.status = 'complete' AND m.payload_json IS NOT NULL
        """
        params: list[Any] = []
        if dataset_id:
            query += """ AND d.root_dataset_id = (
                SELECT COALESCE(root_dataset_id, id) FROM datasets WHERE id = ?)"""
            params.append(dataset_id)
        query += " ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        entries: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            payload = json.loads(record.pop("payload_json") or "{}")
            report = payload.get("report") or {}
            execution = payload.get("execution") or {}
            if not record.get("question"):
                continue
            entries.append({
                "message_id": record["message_id"],
                "session_id": record["session_id"],
                "session_title": record["session_title"],
                "dataset_id": record["dataset_id"],
                "dataset_name": record["dataset_name"],
                "created_at": record["created_at"],
                "question": record["question"],
                "headline": report.get("headline") or "",
                "kpis": [
                    {"label": k.get("label"), "value": k.get("value"), "format": k.get("format")}
                    for k in (execution.get("kpis") or [])[:6]
                ],
                "verification_score": (payload.get("verification") or {}).get("score"),
            })
        return entries

    # --- boards ------------------------------------------------------------------
    def create_board(self, title: str, description: str = "") -> dict[str, Any]:
        now = utcnow()
        record = {"id": new_id("brd"), "title": title, "description": description,
                  "created_at": now, "updated_at": now}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO boards (id, title, description, created_at, updated_at) "
                "VALUES (:id, :title, :description, :created_at, :updated_at)",
                record,
            )
        return {**record, "share_token": None, "item_count": 0}

    def list_boards(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT b.*, (SELECT COUNT(*) FROM board_items i WHERE i.board_id = b.id) AS item_count
                   FROM boards b ORDER BY b.updated_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_board(self, board_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT b.*, (SELECT COUNT(*) FROM board_items i WHERE i.board_id = b.id) AS item_count
                   FROM boards b WHERE b.id = ?""",
                (board_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_board(self, board_id: str, *, title: str | None = None, description: str | None = None) -> None:
        with self.connect() as conn:
            if title is not None:
                conn.execute("UPDATE boards SET title = ? WHERE id = ?", (title, board_id))
            if description is not None:
                conn.execute("UPDATE boards SET description = ? WHERE id = ?", (description, board_id))
            conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (utcnow(), board_id))

    def delete_board(self, board_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
        return cur.rowcount > 0

    def list_board_items(self, board_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM board_items WHERE board_id = ? ORDER BY position ASC, created_at ASC",
                (board_id,),
            ).fetchall()
        return [self._hydrate_item(dict(r)) for r in rows]

    def add_board_item(self, board_id: str, *, kind: str, title: str, content: dict[str, Any],
                       source_session_id: str | None = None, source_question: str | None = None,
                       dataset_name: str | None = None, wide: bool = False) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM board_items WHERE board_id = ?", (board_id,)
            ).fetchone()[0]
            record = {
                "id": new_id("itm"), "board_id": board_id, "kind": kind, "title": title,
                "content_json": json.dumps(content), "source_session_id": source_session_id,
                "source_question": source_question, "dataset_name": dataset_name, "wide": int(wide),
                "position": position, "created_at": now,
            }
            conn.execute(
                """INSERT INTO board_items (id, board_id, kind, title, content_json, source_session_id,
                       source_question, dataset_name, wide, position, created_at)
                   VALUES (:id, :board_id, :kind, :title, :content_json, :source_session_id,
                       :source_question, :dataset_name, :wide, :position, :created_at)""",
                record,
            )
            conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (now, board_id))
        return self._hydrate_item(record)

    def get_board_item(self, board_id: str, item_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM board_items WHERE board_id = ? AND id = ?", (board_id, item_id)
            ).fetchone()
        return self._hydrate_item(dict(row)) if row else None

    def update_board_item(self, board_id: str, item_id: str, *, title: str | None = None,
                          wide: bool | None = None, content: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            if title is not None:
                conn.execute("UPDATE board_items SET title = ? WHERE board_id = ? AND id = ?",
                             (title, board_id, item_id))
            if wide is not None:
                conn.execute("UPDATE board_items SET wide = ? WHERE board_id = ? AND id = ?",
                             (int(wide), board_id, item_id))
            if content is not None:
                conn.execute("UPDATE board_items SET content_json = ? WHERE board_id = ? AND id = ?",
                             (json.dumps(content), board_id, item_id))
            conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (utcnow(), board_id))

    def delete_board_item(self, board_id: str, item_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM board_items WHERE board_id = ? AND id = ?", (board_id, item_id))
            conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (utcnow(), board_id))
        return cur.rowcount > 0

    def reorder_board_items(self, board_id: str, item_ids: list[str]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "UPDATE board_items SET position = ? WHERE board_id = ? AND id = ?",
                [(index, board_id, item_id) for index, item_id in enumerate(item_ids)],
            )
            conn.execute("UPDATE boards SET updated_at = ? WHERE id = ?", (utcnow(), board_id))

    @staticmethod
    def _hydrate_item(record: dict[str, Any]) -> dict[str, Any]:
        record["content"] = json.loads(record.pop("content_json"))
        record["wide"] = bool(record["wide"])
        return record

    # --- monitors ------------------------------------------------------------------
    def create_monitor(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        payload = {"id": new_id("mon"), "enabled": 1, "baseline_value": None, "last_value": None,
                   "last_status": None, "last_run_at": None, "created_at": now, "updated_at": now,
                   **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO monitors (id, title, dataset_id, source_session_id, question,
                       kpi_label, kpi_index, kpi_format, code, direction, threshold, enabled,
                       baseline_value, last_value, last_status, last_run_at, created_at, updated_at)
                   VALUES (:id, :title, :dataset_id, :source_session_id, :question, :kpi_label,
                       :kpi_index, :kpi_format, :code, :direction, :threshold, :enabled,
                       :baseline_value, :last_value, :last_status, :last_run_at, :created_at,
                       :updated_at)""",
                payload,
            )
        return self._hydrate_monitor(payload)

    def list_monitors(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT m.*, d.name AS dataset_name, d.root_dataset_id
                     FROM monitors m LEFT JOIN datasets d ON d.id = m.dataset_id"""
        params: tuple[Any, ...] = ()
        if dataset_id:
            query += " WHERE m.dataset_id = ?"
            params = (dataset_id,)
        query += " ORDER BY m.created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._hydrate_monitor(dict(r)) for r in rows]

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT m.*, d.name AS dataset_name, d.root_dataset_id
                     FROM monitors m LEFT JOIN datasets d ON d.id = m.dataset_id
                    WHERE m.id = ?""",
                (monitor_id,),
            ).fetchone()
        return self._hydrate_monitor(dict(row)) if row else None

    def update_monitor(self, monitor_id: str, **fields: Any) -> None:
        allowed = {"title", "direction", "threshold", "enabled", "baseline_value",
                   "last_value", "last_status", "last_run_at"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE monitors SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": monitor_id, "updated_at": utcnow()},
            )

    def delete_monitor(self, monitor_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        return cur.rowcount > 0

    def add_monitor_run(self, monitor_id: str, record: dict[str, Any], history_limit: int = 60) -> dict[str, Any]:
        payload = {"id": new_id("run"), "monitor_id": monitor_id, "dataset_id": None, "value": None,
                   "previous_value": None, "change_pct": None, "breached": 0, "detail": None,
                   "duration_ms": None, "root_cause": None, "created_at": utcnow(), **record}
        payload["breached"] = int(bool(payload["breached"]))
        root_cause = payload.pop("root_cause", None)
        payload["root_cause_json"] = json.dumps(root_cause) if root_cause else None
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO monitor_runs (id, monitor_id, dataset_id, value, previous_value,
                       change_pct, status, breached, detail, duration_ms, root_cause_json, created_at)
                   VALUES (:id, :monitor_id, :dataset_id, :value, :previous_value, :change_pct,
                       :status, :breached, :detail, :duration_ms, :root_cause_json, :created_at)""",
                payload,
            )
            # Keep history bounded so a frequent schedule cannot grow the database without limit.
            conn.execute(
                """DELETE FROM monitor_runs WHERE monitor_id = ? AND id NOT IN (
                       SELECT id FROM monitor_runs WHERE monitor_id = ?
                        ORDER BY created_at DESC LIMIT ?)""",
                (monitor_id, monitor_id, history_limit),
            )
        payload["breached"] = bool(payload["breached"])
        payload.pop("root_cause_json", None)
        payload["root_cause"] = root_cause
        return payload

    def list_monitor_runs(self, monitor_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                # Same tie-break as the delivery log: the history is reversed into a
                # timeline, and "most recent run" has to mean the same thing every time.
                """SELECT * FROM monitor_runs WHERE monitor_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (monitor_id, limit),
            ).fetchall()
        runs = []
        for row in rows:
            run = dict(row)
            run["breached"] = bool(run["breached"])
            raw = run.pop("root_cause_json", None)
            run["root_cause"] = json.loads(raw) if raw else None
            runs.append(run)
        return list(reversed(runs))

    @staticmethod
    def _hydrate_monitor(record: dict[str, Any]) -> dict[str, Any]:
        record["enabled"] = bool(record.get("enabled", 1))
        return record

    # --- alert channels ---------------------------------------------------------------
    def create_channel(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        payload = {"id": new_id("ch"), "enabled": 1, "last_status": None, "last_error": None,
                   "last_sent_at": None, "sent_count": 0, "created_at": now, "updated_at": now,
                   **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO alert_channels (id, name, kind, target, events, enabled, last_status,
                       last_error, last_sent_at, sent_count, created_at, updated_at)
                   VALUES (:id, :name, :kind, :target, :events, :enabled, :last_status,
                       :last_error, :last_sent_at, :sent_count, :created_at, :updated_at)""",
                payload,
            )
        return self._hydrate_channel(payload)

    def list_channels(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_channels ORDER BY created_at ASC").fetchall()
        return [self._hydrate_channel(dict(r)) for r in rows]

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM alert_channels WHERE id = ?", (channel_id,)).fetchone()
        return self._hydrate_channel(dict(row)) if row else None

    def update_channel(self, channel_id: str, **fields: Any) -> None:
        allowed = {"name", "target", "events", "enabled", "last_status", "last_error",
                   "last_sent_at", "sent_count"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE alert_channels SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": channel_id, "updated_at": utcnow()},
            )

    def delete_channel(self, channel_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM alert_channels WHERE id = ?", (channel_id,))
        return cur.rowcount > 0

    def add_delivery(self, record: dict[str, Any], history_limit: int = 200) -> dict[str, Any]:
        payload = {"id": new_id("dlv"), "detail": None, "created_at": utcnow(), **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO alert_deliveries (id, channel_id, event, title, status, detail, created_at)
                   VALUES (:id, :channel_id, :event, :title, :status, :detail, :created_at)""",
                payload,
            )
            conn.execute(
                """DELETE FROM alert_deliveries WHERE id NOT IN (
                       SELECT id FROM alert_deliveries ORDER BY created_at DESC LIMIT ?)""",
                (history_limit,),
            )
        return payload

    def list_deliveries(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            # rowid breaks the tie: two alerts delivered in the same microsecond would
            # otherwise come back in an arbitrary order, and this log is read as a timeline.
            rows = conn.execute(
                """SELECT d.*, c.name AS channel_name, c.kind AS channel_kind
                     FROM alert_deliveries d LEFT JOIN alert_channels c ON c.id = d.channel_id
                    ORDER BY d.created_at DESC, d.rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _hydrate_channel(record: dict[str, Any]) -> dict[str, Any]:
        record["enabled"] = bool(record.get("enabled", 1))
        record["events"] = [e for e in str(record.get("events") or "").split(",") if e]
        return record

    # --- SQL sources ----------------------------------------------------------------
    def create_source(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        payload = {"id": new_id("src"), "dataset_id": None, "refresh_minutes": 0, "enabled": 1,
                   "last_status": None, "last_error": None, "last_synced_at": None,
                   "last_row_count": None, "sync_count": 0, "created_at": now, "updated_at": now,
                   **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sources (id, name, kind, dsn, query, dataset_id, refresh_minutes,
                       enabled, last_status, last_error, last_synced_at, last_row_count,
                       sync_count, created_at, updated_at)
                   VALUES (:id, :name, :kind, :dsn, :query, :dataset_id, :refresh_minutes,
                       :enabled, :last_status, :last_error, :last_synced_at, :last_row_count,
                       :sync_count, :created_at, :updated_at)""",
                payload,
            )
        return self._hydrate_source(payload)

    def list_sources(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.*, d.name AS dataset_name, d.n_rows AS dataset_rows, d.version
                     FROM sources s LEFT JOIN datasets d ON d.id = s.dataset_id
                    ORDER BY s.created_at DESC"""
            ).fetchall()
        return [self._hydrate_source(dict(r)) for r in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT s.*, d.name AS dataset_name, d.n_rows AS dataset_rows, d.version
                     FROM sources s LEFT JOIN datasets d ON d.id = s.dataset_id
                    WHERE s.id = ?""",
                (source_id,),
            ).fetchone()
        return self._hydrate_source(dict(row)) if row else None

    def update_source(self, source_id: str, **fields: Any) -> None:
        allowed = {"name", "kind", "dsn", "query", "dataset_id", "refresh_minutes", "enabled",
                   "last_status", "last_error", "last_synced_at", "last_row_count", "sync_count"}
        # `last_error` is cleared on a successful sync, so None is a meaningful value here.
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE sources SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": source_id, "updated_at": utcnow()},
            )

    def delete_source(self, source_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return cur.rowcount > 0

    @staticmethod
    def _hydrate_source(record: dict[str, Any]) -> dict[str, Any]:
        record["enabled"] = bool(record.get("enabled", 1))
        return record

    # --- comments ---------------------------------------------------------------------
    def add_comment(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        payload = {"id": new_id("cmt"), "parent_id": None, "resolved": 0,
                   "created_at": now, "updated_at": now, **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO comments (id, subject_kind, subject_id, parent_id, author, body,
                       resolved, created_at, updated_at)
                   VALUES (:id, :subject_kind, :subject_id, :parent_id, :author, :body,
                       :resolved, :created_at, :updated_at)""",
                payload,
            )
        return self._hydrate_comment(payload)

    def list_comments(self, subject_kind: str, subject_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM comments WHERE subject_kind = ? AND subject_id = ?
                    ORDER BY created_at ASC, rowid ASC""",
                (subject_kind, subject_id),
            ).fetchall()
        return [self._hydrate_comment(dict(r)) for r in rows]

    def comment_counts(self, subject_kind: str, subject_ids: list[str]) -> dict[str, int]:
        """Unresolved comment count per subject — for the badge, without loading bodies."""
        if not subject_ids:
            return {}
        placeholders = ",".join("?" for _ in subject_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT subject_id, COUNT(*) AS n FROM comments
                     WHERE subject_kind = ? AND resolved = 0 AND subject_id IN ({placeholders})
                     GROUP BY subject_id""",
                (subject_kind, *subject_ids),
            ).fetchall()
        return {r["subject_id"]: int(r["n"]) for r in rows}

    def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return self._hydrate_comment(dict(row)) if row else None

    def update_comment(self, comment_id: str, **fields: Any) -> None:
        allowed = {"body", "resolved"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        if "resolved" in updates:
            updates["resolved"] = int(bool(updates["resolved"]))
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE comments SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": comment_id, "updated_at": utcnow()},
            )

    def resolve_thread(self, root_id: str, resolved: bool) -> None:
        """Resolving a thread resolves its replies: a half-resolved thread is a to-do list."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE comments SET resolved = ?, updated_at = ? WHERE id = ? OR parent_id = ?",
                (int(resolved), utcnow(), root_id, root_id),
            )

    def delete_comment(self, comment_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        return cur.rowcount > 0

    @staticmethod
    def _hydrate_comment(record: dict[str, Any]) -> dict[str, Any]:
        record["resolved"] = bool(record.get("resolved", 0))
        return record

    # --- activity ---------------------------------------------------------------------
    def add_activity(self, record: dict[str, Any], history_limit: int = 500) -> dict[str, Any]:
        payload = {"id": new_id("act"), "subject_kind": None, "subject_id": None,
                   "subject_title": None, "detail": None, "created_at": utcnow(), **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO activity (id, actor, action, subject_kind, subject_id,
                       subject_title, detail, created_at)
                   VALUES (:id, :actor, :action, :subject_kind, :subject_id,
                       :subject_title, :detail, :created_at)""",
                payload,
            )
            conn.execute(
                """DELETE FROM activity WHERE id NOT IN (
                       SELECT id FROM activity ORDER BY created_at DESC LIMIT ?)""",
                (history_limit,),
            )
        return payload

    def list_activity(self, limit: int = 50, subject_id: str | None = None) -> list[dict[str, Any]]:
        # rowid breaks the tie: two events recorded in the same microsecond would
        # otherwise come back in an arbitrary order, which a feed cannot have.
        query = "SELECT * FROM activity"
        params: list[Any] = []
        if subject_id:
            query += " WHERE subject_id = ?"
            params.append(subject_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    # --- scheduled briefings ------------------------------------------------------------
    def create_briefing(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        payload = {"id": new_id("brf"), "schedule_hours": 24, "enabled": 1, "deliver": 1,
                   "last_run_at": None, "last_status": None, "last_session_id": None,
                   "last_message_id": None, "last_headline": None, "last_error": None,
                   "run_count": 0, "created_at": now, "updated_at": now, **record}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO briefings (id, title, dataset_id, question, schedule_hours, enabled,
                       deliver, last_run_at, last_status, last_session_id, last_message_id,
                       last_headline, last_error, run_count, created_at, updated_at)
                   VALUES (:id, :title, :dataset_id, :question, :schedule_hours, :enabled,
                       :deliver, :last_run_at, :last_status, :last_session_id, :last_message_id,
                       :last_headline, :last_error, :run_count, :created_at, :updated_at)""",
                payload,
            )
        return self._hydrate_briefing(payload)

    def list_briefings(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT b.*, d.name AS dataset_name FROM briefings b
                     LEFT JOIN datasets d ON d.id = b.dataset_id"""
        params: tuple[Any, ...] = ()
        if dataset_id:
            query += " WHERE b.dataset_id = ?"
            params = (dataset_id,)
        query += " ORDER BY b.created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._hydrate_briefing(dict(r)) for r in rows]

    def get_briefing(self, briefing_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT b.*, d.name AS dataset_name FROM briefings b
                     LEFT JOIN datasets d ON d.id = b.dataset_id WHERE b.id = ?""",
                (briefing_id,),
            ).fetchone()
        return self._hydrate_briefing(dict(row)) if row else None

    def update_briefing(self, briefing_id: str, **fields: Any) -> None:
        allowed = {"title", "question", "schedule_hours", "enabled", "deliver", "last_run_at",
                   "last_status", "last_session_id", "last_message_id", "last_headline",
                   "last_error", "run_count", "dataset_id"}
        # `last_error` is cleared on success, so None must reach the column.
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        for flag in ("enabled", "deliver"):
            if flag in updates and updates[flag] is not None:
                updates[flag] = int(bool(updates[flag]))
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE briefings SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": briefing_id, "updated_at": utcnow()},
            )

    def delete_briefing(self, briefing_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM briefings WHERE id = ?", (briefing_id,))
        return cur.rowcount > 0

    @staticmethod
    def _hydrate_briefing(record: dict[str, Any]) -> dict[str, Any]:
        record["enabled"] = bool(record.get("enabled", 1))
        record["deliver"] = bool(record.get("deliver", 1))
        return record

    # --- share links ---------------------------------------------------------------
    def set_share_token(self, kind: Literal["session", "board"], record_id: str, token: str | None) -> None:
        table = SHAREABLE_TABLES[kind]
        with self.connect() as conn:
            conn.execute(f"UPDATE {table} SET share_token = ? WHERE id = ?", (token, record_id))

    def resolve_share_token(self, token: str) -> tuple[str, str] | None:
        with self.connect() as conn:
            for kind, table in SHAREABLE_TABLES.items():
                row = conn.execute(f"SELECT id FROM {table} WHERE share_token = ?", (token,)).fetchone()
                if row:
                    return kind, row["id"]
        return None
