"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app import __version__
from app.agent.investigations import InvestigationService
from app.agent.llm import OpenAILLM, StructuredLLM
from app.agent.service import AnalystService
from app.api import (
    alerts,
    boards,
    briefings,
    comments,
    datasets,
    monitors,
    sessions,
    share,
    sources,
    system,
)
from app.core.auth import WorkspaceAuthMiddleware, parse_tokens
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
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


async def _monitor_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-check every enabled monitor on a fixed interval. Disabled when the interval is 0."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            results = await run_in_threadpool(app.state.monitors.run_all)
            breached = [r for r in results if r["run"]["status"] == "breached"]
            if results:
                logger.info("Monitor sweep: %s checked, %s breached", len(results), len(breached))
        except Exception:  # noqa: BLE001 — the sweep must survive any single failure
            logger.warning("Monitor sweep failed", exc_info=True)


async def _digest_scheduler(app: FastAPI, every_hours: int) -> None:
    """Push the monitor briefing to subscribed channels on a fixed cadence."""
    interval = max(every_hours, 1) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            notifier: NotificationService = app.state.notifier
            alert = notifier.digest_alert(app.state.monitors.digest())
            sent = await run_in_threadpool(notifier.notify, alert)
            logger.info("Monitor briefing sent to %s channel(s)", len(sent))
        except Exception:  # noqa: BLE001 — a failed briefing must not kill the loop
            logger.warning("Monitor briefing failed", exc_info=True)


async def _source_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-pull every source whose own refresh interval has elapsed."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            service: SourceService = app.state.sources
            due = [
                s for s in service.db.list_sources()
                if s["enabled"] and s["refresh_minutes"] and _elapsed(
                    s.get("last_synced_at"), s["refresh_minutes"] * 60)
            ]
            for source in due:
                try:
                    await run_in_threadpool(service.sync, source["id"])
                    app.state.activity.record(
                        "source.sync", actor="Scheduler", subject_kind="source",
                        subject_id=source["id"], subject_title=source["name"],
                        detail="scheduled refresh",
                    )
                except Exception:  # noqa: BLE001 — one dead connection is not all of them
                    logger.warning("Scheduled sync of source %s failed", source["id"], exc_info=True)
            if due:
                logger.info("Source refresh: %s source(s) due", len(due))
        except Exception:  # noqa: BLE001 — the sweep must survive any single failure
            logger.warning("Source refresh sweep failed", exc_info=True)


async def _briefing_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-ask every saved question whose cadence has elapsed. These cost model tokens."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            results = await app.state.briefings.run_due()
            if results:
                logger.info("Scheduled briefings: %s run", len(results))
        except Exception:  # noqa: BLE001 — never kill the loop
            logger.warning("Briefing sweep failed", exc_info=True)


def _elapsed(timestamp: str | None, seconds: float) -> bool:
    """True when `timestamp` is missing or older than `seconds` ago."""
    if not timestamp:
        return True
    from datetime import datetime, timezone

    try:
        stamp = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() >= seconds


def create_app(
    settings: Settings | None = None,
    llm: StructuredLLM | None = None,
    runner: SandboxRunner | None = None,
    transport: Transport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.database_path)
    dataset_service = DatasetService(settings, db)
    runner = runner or SandboxRunner(timeout_s=settings.sandbox_timeout_s, memory_mb=settings.sandbox_memory_mb)
    model = llm or OpenAILLM(settings)
    analyst = AnalystService(settings, db, dataset_service, model, runner)

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        if settings.monitor_interval_minutes:
            tasks.append(asyncio.create_task(
                _monitor_scheduler(instance, settings.monitor_interval_minutes)))
            logger.info("Monitor scheduler started (every %s min)", settings.monitor_interval_minutes)
        if settings.alerts_enabled and settings.alert_digest_hours:
            tasks.append(asyncio.create_task(
                _digest_scheduler(instance, settings.alert_digest_hours)))
            logger.info("Monitor briefing scheduled (every %s h)", settings.alert_digest_hours)
        if settings.source_sync_interval_minutes:
            tasks.append(asyncio.create_task(
                _source_scheduler(instance, settings.source_sync_interval_minutes)))
            logger.info("Source refresh scheduler started (every %s min)",
                        settings.source_sync_interval_minutes)
        if settings.briefing_interval_minutes:
            tasks.append(asyncio.create_task(
                _briefing_scheduler(instance, settings.briefing_interval_minutes)))
            logger.info("Briefing scheduler started (every %s min)",
                        settings.briefing_interval_minutes)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description="Agentic AI data analyst — upload data, ask questions, get analysis.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.datasets = dataset_service
    app.state.analyst = analyst
    app.state.boards = BoardService(db, dataset_service)
    app.state.shares = ShareService(db, app.state.boards)
    app.state.notifier = NotificationService(settings, db, transport)
    app.state.monitors = MonitorService(settings, db, dataset_service, runner, app.state.notifier)
    app.state.sources = SourceService(settings, db, dataset_service)
    app.state.comments = CommentService(db)
    app.state.activity = ActivityService(db)
    app.state.briefings = BriefingService(settings, db, dataset_service, analyst, app.state.notifier)
    app.state.investigations = InvestigationService(settings, db, dataset_service, analyst, model)

    # `add_middleware` prepends, so CORS is added last to sit outermost. That ordering
    # matters: a 401 from the auth layer still needs CORS headers, or the browser cannot
    # read the error and reports an opaque network failure instead.
    app.add_middleware(WorkspaceAuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"{location}: {first.get('msg')}" if location else str(first.get("msg", "Invalid request"))
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_input", "message": message,
            "details": {"errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in errors]},
        }})

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "An unexpected server error occurred.",
        }})

    for router in (system.router, datasets.router, sessions.router, boards.router,
                   monitors.router, alerts.router, share.router, sources.router,
                   comments.router, briefings.router):
        app.include_router(router, prefix="/api")

    if not settings.llm_credentials_detected:
        logger.warning("No OPENAI_API_KEY detected — analysis requests will fail until one is configured.")
    people = parse_tokens(settings.workspace_tokens)
    if people:
        logger.info("Workspace access control enabled for %s token(s)", len(people))
    return app


_default_app: FastAPI | None = None


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Lazy default ASGI app for ``uvicorn app.main:app``.

    Keeping construction lazy preserves the side-effect-free ``create_app`` import used
    by tests while supporting the conventional module path used by Uvicorn and IDEs.
    """
    global _default_app
    if _default_app is None:
        _default_app = create_app()
    await _default_app(scope, receive, send)
