"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app import __version__
from app.agent.llm import OpenAILLM, StructuredLLM
from app.api import (
    admin,
    alerts,
    auth as auth_routes,
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
from app.auth.service import AuthService, Mailer
from app.auth.store import AuthStore
from app.core import security
from app.core.auth import AuthMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.workspaces import Workspace, WorkspaceRegistry, adopt_legacy_workspace
from app.sandbox.runner import SandboxRunner
from app.services.notifications import NotificationService, Transport

logger = logging.getLogger(__name__)


async def _for_each_workspace(app: FastAPI, work: Callable[[Workspace], object], label: str) -> int:
    """Run `work` against every active account's workspace, isolating failures.

    The background sweeps used to walk tables; with a database per account they walk
    accounts instead. One tenant's corrupt monitor or dead SQL connection must not stop
    the sweep reaching the next tenant, so each is caught individually.
    """
    store: AuthStore = app.state.auth_store
    registry: WorkspaceRegistry = app.state.workspaces
    touched = 0
    for user_id in await run_in_threadpool(store.list_user_ids):
        try:
            workspace = registry.for_user(user_id)
            await run_in_threadpool(work, workspace)
            touched += 1
        except Exception:  # noqa: BLE001 — one account's failure is not the sweep's
            logger.warning("%s failed for %s", label, user_id, exc_info=True)
    return touched


async def _monitor_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-check every enabled monitor, for every account. Disabled when the interval is 0."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            breached = 0
            checked = 0

            def sweep(workspace: Workspace) -> None:
                nonlocal breached, checked
                results = workspace.monitors.run_all()
                checked += len(results)
                breached += sum(1 for r in results if r["run"]["status"] == "breached")

            await _for_each_workspace(app, sweep, "Monitor sweep")
            if checked:
                logger.info("Monitor sweep: %s checked, %s breached", checked, breached)
        except Exception:  # noqa: BLE001 — the sweep must survive any single failure
            logger.warning("Monitor sweep failed", exc_info=True)


async def _digest_scheduler(app: FastAPI, every_hours: int) -> None:
    """Push each account's monitor briefing to its own channels on a fixed cadence."""
    interval = max(every_hours, 1) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            def send(workspace: Workspace) -> None:
                notifier: NotificationService = workspace.notifier
                notifier.notify(notifier.digest_alert(workspace.monitors.digest()))

            await _for_each_workspace(app, send, "Monitor briefing")
        except Exception:  # noqa: BLE001 — a failed briefing must not kill the loop
            logger.warning("Monitor briefing failed", exc_info=True)


async def _source_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-pull every source whose own refresh interval has elapsed, for every account."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            def refresh(workspace: Workspace) -> None:
                service = workspace.sources
                due = [
                    s for s in service.db.list_sources()
                    if s["enabled"] and s["refresh_minutes"] and _elapsed(
                        s.get("last_synced_at"), s["refresh_minutes"] * 60)
                ]
                for source in due:
                    try:
                        service.sync(source["id"])
                        workspace.activity.record(
                            "source.sync", actor="Scheduler", subject_kind="source",
                            subject_id=source["id"], subject_title=source["name"],
                            detail="scheduled refresh",
                        )
                    except Exception:  # noqa: BLE001 — one dead connection is not all of them
                        logger.warning("Scheduled sync of source %s failed", source["id"],
                                       exc_info=True)

            await _for_each_workspace(app, refresh, "Source refresh")
        except Exception:  # noqa: BLE001 — the sweep must survive any single failure
            logger.warning("Source refresh sweep failed", exc_info=True)


async def _briefing_scheduler(app: FastAPI, interval_minutes: int) -> None:
    """Re-ask every saved question whose cadence has elapsed. These cost model tokens."""
    interval = max(interval_minutes, 1) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            store: AuthStore = app.state.auth_store
            registry: WorkspaceRegistry = app.state.workspaces
            ran = 0
            for user_id in await run_in_threadpool(store.list_user_ids):
                try:
                    ran += len(await registry.for_user(user_id).briefings.run_due())
                except Exception:  # noqa: BLE001
                    logger.warning("Briefing sweep failed for %s", user_id, exc_info=True)
            if ran:
                logger.info("Scheduled briefings: %s run", ran)
        except Exception:  # noqa: BLE001 — never kill the loop
            logger.warning("Briefing sweep failed", exc_info=True)


async def _session_janitor(app: FastAPI) -> None:
    """Drop expired sessions, spent reset links and stale throttle counters, hourly.

    None of these can authenticate anything once they lapse — `resolve_session` checks
    the deadlines on every request — so this is hygiene rather than enforcement. It
    keeps the control database proportional to live traffic instead of to all traffic
    that ever happened.
    """
    while True:
        await asyncio.sleep(3600)
        try:
            removed = await run_in_threadpool(app.state.auth_store.purge_expired)
            if removed:
                logger.info("Session janitor removed %s expired session(s)", removed)
        except Exception:  # noqa: BLE001
            logger.warning("Session janitor failed", exc_info=True)


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


def _bootstrap_admin(settings: Settings, auth: AuthService, store: AuthStore) -> None:
    """Make sure a fresh deployment has a way in, exactly once.

    With `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` set, the first start
    creates that administrator and adopts any pre-existing single-user database as
    theirs. Without them, the first person to register becomes the administrator
    instead — the right default for a self-hosted install, where the person running
    `docker compose up` is the owner.

    Re-running is a no-op: the account is only created when there are no accounts at
    all, so rotating the variable later does not quietly mint a second admin.
    """
    if store.count_users() > 0:
        return
    email = (settings.bootstrap_admin_email or "").strip()
    password = settings.bootstrap_admin_password or ""
    if not email or not password:
        logger.info("No accounts yet — the first person to register becomes the administrator.")
        return
    try:
        # `create_account` fires the first-account hook itself, which is what moves any
        # existing single-user database into this administrator's workspace.
        user = auth.create_account(email=email, password=password, name="Administrator",
                                   role="admin", must_change_password=False)
    except AppError as exc:
        logger.error("Could not create the bootstrap administrator: %s", exc.message)
        return
    logger.info("Created the bootstrap administrator %s", user["email"])


def create_app(
    settings: Settings | None = None,
    llm: StructuredLLM | None = None,
    runner: SandboxRunner | None = None,
    transport: Transport | None = None,
    mailer: Mailer | None = None,
    google_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.users_dir.mkdir(parents=True, exist_ok=True)

    runner = runner or SandboxRunner(timeout_s=settings.sandbox_timeout_s,
                                     memory_mb=settings.sandbox_memory_mb)
    model = llm or OpenAILLM(settings)

    auth_store = AuthStore(settings.control_database_path)

    def adopt(user_id: str) -> None:
        """Hand a pre-multi-user `data/numera.db` to the first account created."""
        if adopt_legacy_workspace(settings, user_id):
            logger.info("Existing single-user data now belongs to account %s", user_id)

    auth_service = AuthService(settings, auth_store, mailer=mailer, on_first_account=adopt)
    workspaces = WorkspaceRegistry(settings, llm=model, runner=runner, transport=transport)
    _bootstrap_admin(settings, auth_service, auth_store)

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = [asyncio.create_task(_session_janitor(instance))]
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

    # The interactive docs enumerate every route and every request shape. That is a gift
    # to a developer and a map to an attacker, so production serves neither them nor the
    # schema they are generated from.
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description="Agentic AI data analyst — upload data, ask questions, get analysis.",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.auth_store = auth_store
    app.state.auth = auth_service
    app.state.workspaces = workspaces
    app.state.runner = runner
    app.state.llm = model
    # Tests hand in a mock transport; production talks to Google over the real network.
    app.state.google_transport = google_transport

    # `add_middleware` prepends, so CORS is added last to sit outermost. That ordering
    # matters: a 401 from the auth layer still needs CORS headers, or the browser cannot
    # read the error and reports an opaque network failure instead.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(AuthMiddleware, settings=settings, auth=auth_service,
                       docs_enabled=docs_enabled)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # Cookies are the session, so the browser has to be allowed to send them — which
        # also means the allowed origins above must stay an explicit list. A wildcard is
        # rejected by the browser in credentialed mode, and would be wrong if it were not.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Retry-After"],
        max_age=600,
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        response = JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})
        if getattr(exc, "retry_after_s", None):
            response.headers["Retry-After"] = str(exc.retry_after_s)
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"{location}: {first.get('msg')}" if location else str(first.get("msg", "Invalid request"))
        # `errors()` echoes the offending input, which for an auth route is a password.
        # Only the location and the reason are returned.
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

    for router in (system.router, auth_routes.router, admin.router, datasets.router,
                   sessions.router, boards.router, monitors.router, alerts.router,
                   share.router, sources.router, comments.router, briefings.router):
        app.include_router(router, prefix="/api")

    if not settings.llm_credentials_detected:
        logger.warning("No OPENAI_API_KEY detected — analysis requests will fail until one is configured.")
    if not security.ARGON2_AVAILABLE:
        logger.warning(
            "argon2-cffi is not installed; passwords fall back to PBKDF2-SHA256. "
            "Install it (pip install -r requirements.txt) for the stronger hash."
        )
    if settings.google_enabled:
        logger.info("Sign in with Google enabled; redirect URI %s",
                    settings.resolved_google_redirect_uri)
    if settings.is_production and not settings.secure_cookies:
        logger.warning("ENVIRONMENT=production with COOKIE_SECURE=false — sessions will "
                       "travel over plain HTTP. Put a TLS terminator in front of this.")
    logger.info("Accounts: %s registered, data isolated per user under %s",
                auth_store.count_users(), settings.users_dir)
    return app


class SecurityHeadersMiddleware:
    """Response headers that cost nothing and close whole classes of attack.

    This is an API, not a page, but browsers still act on these: `nosniff` stops a JSON
    error being executed as script if one is ever rendered directly, the frame and
    referrer rules keep a hostile page from embedding or fingerprinting responses, and
    HSTS makes the first redirect to HTTPS the last plain-HTTP request a browser makes.
    """

    def __init__(self, app, settings: Settings) -> None:  # noqa: ANN001 - ASGI app
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:  # noqa: ANN001 - ASGI message
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append((b"cross-origin-opener-policy", b"same-origin"))
                # An API response is never a document worth caching in a shared proxy,
                # and several of them carry another person's business data.
                headers.append((b"cache-control", b"no-store"))
                if self.settings.secure_cookies:
                    headers.append((b"strict-transport-security",
                                    b"max-age=31536000; includeSubDomains"))
            await send(message)

        await self.app(scope, receive, send_with_headers)


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
