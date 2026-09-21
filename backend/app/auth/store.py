"""The control plane: who exists, who is signed in, and what has been done.

This is the *only* database shared between tenants, and it is deliberately small. It
holds accounts, live sessions, outstanding password resets, throttling counters and an
audit trail — and no analysis data of any kind. Every dataset, board, monitor and
message lives in a separate SQLite file belonging to exactly one user (see
`app.core.workspaces`), so a query written against a user's workspace cannot reach
another user's rows even if it forgets a `WHERE owner_id = ?`. There is no such clause
to forget.

Secrets are never stored in a usable form. Passwords are Argon2id digests and session
and reset tokens are keyed SHA-256 digests; a read of `auth.db` yields nothing that can
be replayed against the API.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    email                TEXT NOT NULL,
    name                 TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    -- Bumped to invalidate every live session for this user at once: a password change,
    -- a suspension, or an explicit "sign out everywhere".
    session_epoch        INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    failed_attempts      INTEGER NOT NULL DEFAULT 0,
    locked_until         TEXT,
    last_login_at        TEXT,
    password_changed_at  TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Server-side sessions rather than self-contained tokens: revocation has to be
-- immediate and honest. Suspending an account or signing out a stolen laptop must
-- take effect on the next request, not whenever a JWT happens to expire.
CREATE TABLE IF NOT EXISTS auth_sessions (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_digest   TEXT NOT NULL UNIQUE,
    epoch          INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    -- Idle deadline, slid forward as the session is used.
    expires_at     TEXT NOT NULL,
    -- Hard deadline, never extended: a session has a maximum life however active it is.
    absolute_expires_at TEXT NOT NULL,
    revoked_at     TEXT,
    ip             TEXT,
    user_agent     TEXT
);

CREATE TABLE IF NOT EXISTS password_resets (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_digest TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT,
    ip           TEXT
);

-- External sign-in identities (Google today). Keyed by the provider's stable subject
-- id, never by email: an address can be renamed or recycled, a subject cannot, so an
-- account is found by who Google says signed in rather than by what they are called.
CREATE TABLE IF NOT EXISTS oauth_identities (
    id           TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email        TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    UNIQUE (provider, subject),
    -- One identity per provider per account, so "disconnect Google" is unambiguous.
    UNIQUE (user_id, provider)
);

-- Append-only, bounded. Answers "who signed in, from where, and what did an admin
-- change" — the questions asked after something goes wrong, when nobody can add
-- logging retroactively.
CREATE TABLE IF NOT EXISTS auth_events (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    event      TEXT NOT NULL,
    outcome    TEXT NOT NULL DEFAULT 'success',
    user_id    TEXT,
    email      TEXT,
    actor_id   TEXT,
    ip         TEXT,
    user_agent TEXT,
    detail     TEXT
);

-- Fixed-window counters for login, registration and reset throttling. A window is a
-- row; expired rows are swept on write, so the table stays proportional to live traffic.
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket       TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0
);
"""

INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id, used_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON auth_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON auth_events(user_id, created_at DESC);
"""

AUDIT_HISTORY_LIMIT = 5_000

# Columns safe to return to a client. `password_hash` is not among them, and the query
# helpers below name this tuple rather than using `SELECT *`, so a column added later
# is opted in rather than leaked by default.
PUBLIC_USER_FIELDS = (
    "id", "email", "name", "role", "status", "must_change_password",
    "last_login_at", "password_changed_at", "created_at", "updated_at",
)

_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    """One lock per database *file*, not per object.

    Two `AuthStore` instances pointed at the same file — which happens in tests, and
    could happen across a reload — must serialise against each other, so the lock is
    keyed by the resolved path rather than held as instance state.
    """
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = _FILE_LOCKS[key] = threading.RLock()
        return lock


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat()


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AuthStore:
    """SQLite persistence for the control plane."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(INDEXES)

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

    # ------------------------------------------------------------------- users
    def count_users(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        role: str = "user",
        status: str = "active",
        must_change_password: bool = False,
    ) -> dict[str, Any]:
        now = stamp()
        record = {
            "id": new_id("usr"), "email": email, "name": name,
            "password_hash": password_hash, "role": role, "status": status,
            "session_epoch": 1, "must_change_password": int(must_change_password),
            "failed_attempts": 0, "locked_until": None, "last_login_at": None,
            "password_changed_at": now, "created_at": now, "updated_at": now,
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO users (id, email, name, password_hash, role, status,
                       session_epoch, must_change_password, failed_attempts, locked_until,
                       last_login_at, password_changed_at, created_at, updated_at)
                   VALUES (:id, :email, :name, :password_hash, :role, :status,
                       :session_epoch, :must_change_password, :failed_attempts, :locked_until,
                       :last_login_at, :password_changed_at, :created_at, :updated_at)""",
                record,
            )
        return _hydrate_user(record)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _hydrate_user(dict(row)) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return _hydrate_user(dict(row)) if row else None

    def list_users(self, *, limit: int = 200, offset: int = 0, query: str | None = None,
                   role: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {', '.join(PUBLIC_USER_FIELDS)} FROM users"
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("(email LIKE ? OR name LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if role:
            clauses.append("role = ?")
            params.append(role)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_hydrate_user(dict(r)) for r in rows]

    def list_user_ids(self) -> list[str]:
        """Every active account, for the background sweeps that visit each workspace."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM users WHERE status = 'active' ORDER BY created_at ASC"
            ).fetchall()
        return [r["id"] for r in rows]

    def update_user(self, user_id: str, **fields: Any) -> None:
        allowed = {"name", "email", "role", "status", "password_hash", "must_change_password",
                   "failed_attempts", "locked_until", "last_login_at", "password_changed_at",
                   "session_epoch"}
        # `locked_until` is cleared on a successful login, so None has to reach the column.
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        for flag in ("must_change_password",):
            if flag in updates and updates[flag] is not None:
                updates[flag] = int(bool(updates[flag]))
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE users SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**updates, "id": user_id, "updated_at": stamp()},
            )

    def bump_epoch(self, user_id: str) -> int:
        """Invalidate every live session for this user. Returns the new epoch."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET session_epoch = session_epoch + 1, updated_at = ? WHERE id = ?",
                (stamp(), user_id),
            )
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (stamp(), user_id),
            )
            row = conn.execute("SELECT session_epoch FROM users WHERE id = ?", (user_id,)).fetchone()
        return int(row["session_epoch"]) if row else 1

    def delete_user(self, user_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0

    def count_admins(self, *, excluding: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'"
        params: tuple[Any, ...] = ()
        if excluding:
            sql += " AND id != ?"
            params = (excluding,)
        with self.connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    # ---------------------------------------------------------------- sessions
    def create_session(self, *, user_id: str, token_digest: str, epoch: int,
                       idle_expires_at: datetime, absolute_expires_at: datetime,
                       ip: str | None, user_agent: str | None) -> dict[str, Any]:
        now = stamp()
        record = {
            "id": new_id("sess"), "user_id": user_id, "token_digest": token_digest,
            "epoch": epoch, "created_at": now, "last_seen_at": now,
            "expires_at": stamp(idle_expires_at),
            "absolute_expires_at": stamp(absolute_expires_at),
            "revoked_at": None, "ip": ip, "user_agent": (user_agent or "")[:400] or None,
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO auth_sessions (id, user_id, token_digest, epoch, created_at,
                       last_seen_at, expires_at, absolute_expires_at, revoked_at, ip, user_agent)
                   VALUES (:id, :user_id, :token_digest, :epoch, :created_at, :last_seen_at,
                       :expires_at, :absolute_expires_at, :revoked_at, :ip, :user_agent)""",
                record,
            )
        return record

    def find_session(self, token_digest: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token_digest = ?", (token_digest,)
            ).fetchone()
        return dict(row) if row else None

    def touch_session(self, session_id: str, idle_expires_at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (stamp(), stamp(idle_expires_at), session_id),
            )

    def revoke_session(self, session_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (stamp(), session_id),
            )
        return cur.rowcount > 0

    def revoke_session_by_digest(self, token_digest: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_digest = ? AND revoked_at IS NULL",
                (stamp(), token_digest),
            )
        return cur.rowcount > 0

    def list_sessions(self, user_id: str, *, include_ended: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM auth_sessions WHERE user_id = ?"
        params: list[Any] = [user_id]
        if not include_ended:
            sql += " AND revoked_at IS NULL AND expires_at > ?"
            params.append(stamp())
        sql += " ORDER BY last_seen_at DESC LIMIT 100"
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def purge_expired(self) -> int:
        """Drop sessions and reset tokens that can no longer authenticate anything."""
        now = stamp()
        with self.connect() as conn:
            sessions = conn.execute(
                "DELETE FROM auth_sessions WHERE absolute_expires_at < ? OR expires_at < ?",
                (now, now),
            ).rowcount
            conn.execute("DELETE FROM password_resets WHERE expires_at < ? OR used_at IS NOT NULL",
                         (now,))
            conn.execute("DELETE FROM rate_limits WHERE window_start < ?",
                         (stamp(utcnow() - timedelta(days=1)),))
        return sessions

    # ------------------------------------------------------- external identities
    def find_identity(self, provider: str, subject: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_identities WHERE provider = ? AND subject = ?",
                (provider, subject),
            ).fetchone()
        return dict(row) if row else None

    def list_identities(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM oauth_identities WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def link_identity(self, *, provider: str, subject: str, user_id: str,
                      email: str | None) -> dict[str, Any]:
        now = stamp()
        record = {"id": new_id("oid"), "provider": provider, "subject": subject,
                  "user_id": user_id, "email": email, "created_at": now, "last_used_at": now}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO oauth_identities (id, provider, subject, user_id, email,
                       created_at, last_used_at)
                   VALUES (:id, :provider, :subject, :user_id, :email, :created_at, :last_used_at)""",
                record,
            )
        return record

    def touch_identity(self, identity_id: str, *, email: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE oauth_identities SET last_used_at = ?, email = ? WHERE id = ?",
                (stamp(), email, identity_id),
            )

    def unlink_identity(self, user_id: str, provider: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM oauth_identities WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )
        return cur.rowcount > 0

    # --------------------------------------------------------- password resets
    def create_reset(self, *, user_id: str, token_digest: str, expires_at: datetime,
                     ip: str | None) -> dict[str, Any]:
        record = {
            "id": new_id("rst"), "user_id": user_id, "token_digest": token_digest,
            "created_at": stamp(), "expires_at": stamp(expires_at), "used_at": None, "ip": ip,
        }
        with self.connect() as conn:
            # One live link per account: issuing a new one silently retires the old, so a
            # forwarded or shoulder-surfed older email stops working the moment the real
            # owner asks again.
            conn.execute(
                "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                (stamp(), user_id),
            )
            conn.execute(
                """INSERT INTO password_resets (id, user_id, token_digest, created_at,
                       expires_at, used_at, ip)
                   VALUES (:id, :user_id, :token_digest, :created_at, :expires_at, :used_at, :ip)""",
                record,
            )
        return record

    def find_reset(self, token_digest: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM password_resets WHERE token_digest = ?", (token_digest,)
            ).fetchone()
        return dict(row) if row else None

    def consume_reset(self, reset_id: str) -> bool:
        """Mark a reset used. False when it was already spent — the single-use guarantee."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (stamp(), reset_id),
            )
        return cur.rowcount > 0

    # ----------------------------------------------------------------- limits
    def hit_rate_limit(self, bucket: str, *, limit: int, window_s: int) -> tuple[bool, int]:
        """Count one attempt against `bucket`. Returns (allowed, attempts_so_far)."""
        now = utcnow()
        cutoff = stamp(now - timedelta(seconds=window_s))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT window_start, count FROM rate_limits WHERE bucket = ?", (bucket,)
            ).fetchone()
            if row is None or row["window_start"] < cutoff:
                conn.execute(
                    """INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)
                       ON CONFLICT(bucket) DO UPDATE SET window_start = excluded.window_start,
                                                         count = 1""",
                    (bucket, stamp(now)),
                )
                return True, 1
            count = int(row["count"]) + 1
            conn.execute("UPDATE rate_limits SET count = ? WHERE bucket = ?", (count, bucket))
            return count <= limit, count

    def clear_rate_limit(self, bucket: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM rate_limits WHERE bucket = ?", (bucket,))

    # ------------------------------------------------------------------ audit
    def record_event(self, *, event: str, outcome: str = "success", user_id: str | None = None,
                     email: str | None = None, actor_id: str | None = None,
                     ip: str | None = None, user_agent: str | None = None,
                     detail: str | None = None) -> None:
        record = {
            "id": new_id("evt"), "created_at": stamp(), "event": event, "outcome": outcome,
            "user_id": user_id, "email": email, "actor_id": actor_id, "ip": ip,
            "user_agent": (user_agent or "")[:400] or None, "detail": detail,
        }
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO auth_events (id, created_at, event, outcome, user_id, email,
                       actor_id, ip, user_agent, detail)
                   VALUES (:id, :created_at, :event, :outcome, :user_id, :email, :actor_id,
                       :ip, :user_agent, :detail)""",
                record,
            )
            conn.execute(
                """DELETE FROM auth_events WHERE id NOT IN (
                       SELECT id FROM auth_events ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
                (AUDIT_HISTORY_LIMIT,),
            )

    def list_events(self, *, limit: int = 100, user_id: str | None = None,
                    event: str | None = None) -> list[dict[str, Any]]:
        sql = """SELECT e.*, u.email AS user_email, u.name AS user_name
                   FROM auth_events e LEFT JOIN users u ON u.id = e.user_id"""
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("e.user_id = ?")
            params.append(user_id)
        if event:
            clauses.append("e.event = ?")
            params.append(event)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # rowid breaks the tie so two events in the same microsecond keep a stable order.
        sql += " ORDER BY e.created_at DESC, e.rowid DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def event_counts(self, since: datetime) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event, COUNT(*) AS n FROM auth_events WHERE created_at >= ? GROUP BY event",
                (stamp(since),),
            ).fetchall()
        return {r["event"]: int(r["n"]) for r in rows}

    def active_session_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT user_id, COUNT(*) AS n FROM auth_sessions
                    WHERE revoked_at IS NULL AND expires_at > ? GROUP BY user_id""",
                (stamp(),),
            ).fetchall()
        return {r["user_id"]: int(r["n"]) for r in rows}


def _hydrate_user(record: dict[str, Any]) -> dict[str, Any]:
    if "must_change_password" in record:
        record["must_change_password"] = bool(record["must_change_password"])
    return record


def public_user(record: dict[str, Any]) -> dict[str, Any]:
    """Strip a user record down to what a client may see. Never includes the hash.

    `has_password` is derived, and only when the record came with its hash: an account
    created through Google has none until its owner sets one, and the account screen
    needs to know whether to ask for a "current password".
    """
    public = {key: record.get(key) for key in PUBLIC_USER_FIELDS}
    if "password_hash" in record:
        public["has_password"] = bool(record["password_hash"])
    return public


__all__ = ["AuthStore", "PUBLIC_USER_FIELDS", "new_id", "parse", "public_user", "stamp", "utcnow"]
