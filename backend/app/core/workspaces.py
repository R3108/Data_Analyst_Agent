"""One private workspace per account — the mechanism behind per-user data isolation.

Numera used to run single-tenant: one `numera.db`, one `datasets/` directory, one set
of services built at startup. Multi-user turns that into *one of each per account*:

    data/
      auth.db                      ← accounts, sessions, audit. The only shared store.
      users/
        usr_a1b2…/
          numera.db                ← that person's datasets, boards, monitors, messages
          datasets/ds_…/clean.parquet
        usr_c3d4…/
          numera.db
          datasets/…

Isolation is physical rather than a `WHERE owner_id = ?` on every query. That choice is
deliberate. A row-level scheme is only as strong as its least careful query, and this
codebase has thousands of lines of analytics SQL, a code sandbox that reads Parquet
files by path, and exporters that stream them back out; one forgotten predicate
anywhere in that surface is a cross-tenant leak. Here there is no predicate to forget —
a request is bound to a `Database` pointed at one file and a `datasets_dir` pointed at
one directory, and a query that "forgets" to scope itself simply cannot see anything
else. It also makes the operational questions trivial: exporting one customer's data is
a `tar` of a directory, and deleting it is an `rm -rf`, with no risk of catching a
neighbour's rows.

The cost is that the background sweeps have to walk accounts rather than tables, and
that cross-tenant reporting is an aggregation rather than a `GROUP BY`. Both are paid
in `app.main` and `app.api.admin`, and they are worth it.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.investigations import InvestigationService
from app.agent.llm import StructuredLLM
from app.agent.service import AnalystService
from app.core.config import Settings
from app.db import Database
from app.sandbox.runner import SandboxRunner
from app.services.activity import ActivityService
from app.services.boards import BoardService
from app.services.briefings import BriefingService
from app.services.comments import CommentService
from app.services.datasets import DatasetService
from app.services.monitors import MonitorService
from app.services.notifications import NotificationService, Transport
from app.services.sharing import ShareService
from app.services.sources import SourceService

logger = logging.getLogger(__name__)

LEGACY_DB_NAME = "numera.db"


@dataclass
class Workspace:
    """Everything one account's requests are served from.

    Construction is cheap — the services hold references, and `Database` opens a
    connection per call rather than keeping one — so a workspace can be dropped from
    the cache and rebuilt without anything being lost or any handle leaking.
    """

    user_id: str
    settings: Settings
    db: Database
    datasets: DatasetService
    analyst: AnalystService
    boards: BoardService
    shares: ShareService
    notifier: NotificationService
    monitors: MonitorService
    sources: SourceService
    comments: CommentService
    activity: ActivityService
    briefings: BriefingService
    investigations: InvestigationService

    @property
    def root(self) -> Path:
        return self.settings.data_dir

    def storage_bytes(self) -> int:
        """How much disk this account is using, database and Parquet files together."""
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:  # a file being rewritten underneath the walk
                continue
        return total


class WorkspaceRegistry:
    """Builds, caches and disposes of per-account workspaces."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: StructuredLLM,
        runner: SandboxRunner,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.runner = runner
        self.transport = transport
        self._cache: OrderedDict[str, Workspace] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ access
    def for_user(self, user_id: str) -> Workspace:
        """The workspace for `user_id`, building it on first use.

        The build happens outside the registry lock. It touches the filesystem — a
        `mkdir`, and a schema check against a SQLite file — and holding a global lock
        across that would serialise every first request in the process behind the
        slowest one. Two threads racing on the same new user both build, and the loser's
        copy is discarded; the per-file write lock in `app.db` keeps that safe, and the
        schema creation is `IF NOT EXISTS` throughout.
        """
        with self._lock:
            existing = self._cache.get(user_id)
            if existing is not None:
                self._cache.move_to_end(user_id)
                return existing

        built = self._build(user_id)

        with self._lock:
            winner = self._cache.get(user_id)
            if winner is not None:
                self._cache.move_to_end(user_id)
                return winner
            self._cache[user_id] = built
            while len(self._cache) > self.settings.workspace_cache_size:
                evicted, _ = self._cache.popitem(last=False)
                logger.debug("Workspace cache evicted %s", evicted)
            return built

    def evict(self, user_id: str) -> None:
        with self._lock:
            self._cache.pop(user_id, None)

    def apply_settings(self, **changes: Any) -> None:
        """Change a runtime setting everywhere at once.

        Each workspace holds its *own* copy of `Settings`, differing only in `data_dir`,
        so mutating the process-wide object no longer reaches the services that read it.
        This is the one supported way to change a setting after startup: it updates the
        shared object and every live copy together, so the two cannot drift apart.
        """
        for key, value in changes.items():
            setattr(self.settings, key, value)
        with self._lock:
            for workspace in self._cache.values():
                for key, value in changes.items():
                    setattr(workspace.settings, key, value)

    def cached_ids(self) -> list[str]:
        with self._lock:
            return list(self._cache)

    def _build(self, user_id: str) -> Workspace:
        scoped = self.settings.model_copy(update={"data_dir": self.settings.workspace_dir(user_id)})
        scoped.data_dir.mkdir(parents=True, exist_ok=True)

        db = Database(scoped.database_path)
        datasets = DatasetService(scoped, db)
        analyst = AnalystService(scoped, db, datasets, self.llm, self.runner)
        boards = BoardService(db, datasets)
        notifier = NotificationService(scoped, db, self.transport)
        return Workspace(
            user_id=user_id,
            settings=scoped,
            db=db,
            datasets=datasets,
            analyst=analyst,
            boards=boards,
            shares=ShareService(db, boards, owner_id=user_id),
            notifier=notifier,
            monitors=MonitorService(scoped, db, datasets, self.runner, notifier),
            sources=SourceService(scoped, db, datasets),
            comments=CommentService(db),
            activity=ActivityService(db),
            briefings=BriefingService(scoped, db, datasets, analyst, notifier),
            investigations=InvestigationService(scoped, db, datasets, analyst, self.llm),
        )

    # ---------------------------------------------------------------- lifecycle
    def destroy(self, user_id: str) -> None:
        """Erase an account's data directory. Irreversible, and called on account deletion.

        Nothing is shared with another tenant, so there is no reference counting to do
        and no orphan to leave behind — which is the operational payoff of keeping each
        account in its own directory in the first place.
        """
        self.evict(user_id)
        root = self.settings.workspace_dir(user_id)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
            logger.info("Removed workspace directory for %s", user_id)

    def summary(self, user_id: str) -> dict[str, Any]:
        """Counts and disk usage for one account, for the admin dashboard."""
        workspace = self.for_user(user_id)
        db = workspace.db
        with db.connect() as conn:
            def count(table: str) -> int:
                return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

            counts = {
                "datasets": count("datasets"),
                "sessions": count("sessions"),
                "messages": count("messages"),
                "boards": count("boards"),
                "monitors": count("monitors"),
                "sources": count("sources"),
            }
            rows = conn.execute("SELECT COALESCE(SUM(n_rows), 0) FROM datasets").fetchone()[0]
        return {**counts, "rows": int(rows or 0), "storage_bytes": workspace.storage_bytes()}


def adopt_legacy_workspace(settings: Settings, user_id: str) -> bool:
    """Move a pre-multi-user `data/numera.db` and `data/datasets/` into an account.

    Upgrading an existing single-tenant install should not silently orphan the work
    already in it. The legacy files are moved (not copied) into the first admin's
    directory the one time that directory is empty, so the upgrade is a restart rather
    than a migration script, and re-running it is a no-op.
    """
    legacy_db = settings.data_dir / LEGACY_DB_NAME
    legacy_datasets = settings.data_dir / "datasets"
    if not legacy_db.exists():
        return False

    target = settings.workspace_dir(user_id)
    if (target / LEGACY_DB_NAME).exists():
        return False
    target.mkdir(parents=True, exist_ok=True)

    shutil.move(str(legacy_db), str(target / LEGACY_DB_NAME))
    for sidecar in ("numera.db-wal", "numera.db-shm"):
        source = settings.data_dir / sidecar
        if source.exists():
            shutil.move(str(source), str(target / sidecar))
    if legacy_datasets.is_dir():
        shutil.move(str(legacy_datasets), str(target / "datasets"))
    logger.info("Adopted the existing single-user workspace into account %s", user_id)
    return True


__all__ = ["Workspace", "WorkspaceRegistry", "adopt_legacy_workspace"]
