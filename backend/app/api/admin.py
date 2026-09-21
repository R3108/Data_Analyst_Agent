"""Administration: accounts, sessions, the audit trail and a view of the deployment.

Every route here is gated by `require_admin`, which the router declares once as a
dependency rather than leaving to each handler — the same reasoning as the
authentication middleware. Adding an endpoint to this file cannot accidentally ship it
unguarded.

An administrator manages *accounts*, not their contents. There is no route that reads
another person's datasets, messages or boards, and adding one would mean reaching into
a workspace this API deliberately never opens. What an admin can see is metadata:
how many datasets exist, how much disk they take, when the account last signed in.
That is enough to run the service and to answer a support ticket, and not enough to
read a customer's revenue figures over their shoulder.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import __version__
from app.api.deps import (
    get_auth,
    get_meta,
    get_registry,
    get_settings_dep,
    get_store,
    require_admin,
)
from app.auth.service import AuthService, RequestMeta
from app.auth.store import AuthStore, public_user, utcnow
from app.core import security
from app.core.auth import Identity
from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError
from app.core.workspaces import WorkspaceRegistry

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateUserBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=80)
    role: str = Field(default="user", pattern="^(admin|user)$")
    must_change_password: bool = True


class UpdateUserBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    status: str | None = Field(default=None, pattern="^(active|suspended)$")


class SetPasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.get("/overview")
def overview(
    store: AuthStore = Depends(get_store),
    registry: WorkspaceRegistry = Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """The dashboard's headline numbers and the deployment's security posture."""
    users = store.list_users(limit=1000)
    sessions = store.active_session_counts()
    week_ago = utcnow() - timedelta(days=7)
    events = store.event_counts(week_ago)

    storage = 0
    datasets = 0
    for user in users:
        try:
            summary = registry.summary(user["id"])
        except Exception:  # noqa: BLE001 — one unreadable workspace must not blank the page
            continue
        storage += summary["storage_bytes"]
        datasets += summary["datasets"]

    return {
        "version": __version__,
        "users": {
            "total": len(users),
            "admins": sum(1 for u in users if u["role"] == "admin"),
            "suspended": sum(1 for u in users if u["status"] != "active"),
            "new_this_week": sum(1 for u in users if u["created_at"] >= week_ago.isoformat()),
        },
        "sessions": {"active": sum(sessions.values()), "signed_in_users": len(sessions)},
        "activity_7d": {
            "sign_ins": events.get("login", 0),
            "registrations": events.get("register", 0),
            "password_resets": events.get("password.reset.confirm", 0),
        },
        "storage": {"total_bytes": storage, "datasets": datasets},
        "security": {
            "password_algorithm": security.hasher.algorithm,
            "argon2_available": security.ARGON2_AVAILABLE,
            "secure_cookies": settings.secure_cookies,
            "same_site": settings.cookie_samesite,
            "auth_secret_configured": bool(settings.auth_secret),
            "environment": settings.environment,
            "registration_enabled": settings.registration_enabled,
            "allowed_domains": settings.allowed_signup_domains,
            "session_idle_days": settings.session_idle_days,
            "session_absolute_days": settings.session_absolute_days,
            "email_delivery": bool(settings.smtp_host),
            "trust_forwarded_for": settings.trust_forwarded_for,
        },
    }


@router.get("/users")
def list_users(
    q: str | None = Query(default=None, max_length=120),
    role: str | None = Query(default=None, pattern="^(admin|user)$"),
    status: str | None = Query(default=None, pattern="^(active|suspended)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: AuthStore = Depends(get_store),
    registry: WorkspaceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Accounts with a usage summary each. Metadata only — never workspace contents."""
    sessions = store.active_session_counts()
    users = store.list_users(limit=limit, offset=offset, query=q, role=role, status=status)
    enriched = []
    for user in users:
        try:
            summary = registry.summary(user["id"])
        except Exception:  # noqa: BLE001 — a corrupt workspace still has a manageable account
            summary = {"datasets": 0, "sessions": 0, "messages": 0, "boards": 0,
                       "monitors": 0, "sources": 0, "rows": 0, "storage_bytes": 0}
        enriched.append({**user, "usage": summary, "active_sessions": sessions.get(user["id"], 0)})
    return enriched


@router.post("/users", status_code=201)
def create_user(
    body: CreateUserBody,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(require_admin),
    meta: RequestMeta = Depends(get_meta),
) -> dict[str, Any]:
    """Invite somebody by creating their account with a temporary password.

    They are required to change it on first sign-in, so the password typed here never
    becomes a long-lived shared secret between an admin and a user.
    """
    user = auth.create_account(
        email=body.email, password=body.password, name=body.name, role=body.role,
        must_change_password=body.must_change_password, actor_id=identity.user_id, meta=meta,
    )
    return public_user(user)


@router.get("/users/{user_id}")
def get_user(
    user_id: str,
    auth: AuthService = Depends(get_auth),
    store: AuthStore = Depends(get_store),
    registry: WorkspaceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    user = auth.require_user(user_id)
    return {
        **public_user(user),
        "usage": registry.summary(user_id),
        "sessions": auth.list_sessions(user_id),
        "recent_events": store.list_events(limit=25, user_id=user_id),
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserBody,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(require_admin),
    meta: RequestMeta = Depends(get_meta),
) -> dict[str, Any]:
    auth.require_user(user_id)
    if body.name is not None:
        auth.update_profile(user_id, name=body.name)
    if body.role is not None:
        auth.set_role(user_id=user_id, role=body.role, actor_id=identity.user_id, meta=meta)
    if body.status is not None:
        auth.set_status(user_id=user_id, status=body.status, actor_id=identity.user_id, meta=meta)
    return public_user(auth.require_user(user_id))


@router.post("/users/{user_id}/password")
def set_password(
    user_id: str,
    body: SetPasswordBody,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(require_admin),
    meta: RequestMeta = Depends(get_meta),
) -> dict[str, Any]:
    """Set a temporary password. Signs the account out everywhere and forces a change."""
    auth.admin_reset_password(user_id=user_id, new_password=body.password,
                              actor_id=identity.user_id, meta=meta)
    return {"ok": True, "must_change_password": True}


@router.post("/users/{user_id}/sessions/revoke-all")
def revoke_sessions(
    user_id: str,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(require_admin),
    meta: RequestMeta = Depends(get_meta),
) -> dict[str, Any]:
    auth.require_user(user_id)
    auth.sign_out_everywhere(user_id, actor_id=identity.user_id, meta=meta)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    confirm_email: str = Query(..., max_length=254,
                               description="The account's email, typed to confirm."),
    auth: AuthService = Depends(get_auth),
    store: AuthStore = Depends(get_store),
    registry: WorkspaceRegistry = Depends(get_registry),
    identity: Identity = Depends(require_admin),
    meta: RequestMeta = Depends(get_meta),
) -> JSONResponse:
    """Delete an account and erase its workspace. Irreversible.

    The email has to be typed back. This removes a directory of somebody's work, and a
    misplaced click on a table row is not consent for that.
    """
    user = auth.require_user(user_id)
    if user_id == identity.user_id:
        raise ForbiddenError("You cannot delete your own account here.", code="self_delete")
    if user["role"] == "admin" and store.count_admins(excluding=user_id) == 0:
        raise ForbiddenError("This is the last administrator.", code="last_admin")
    if (confirm_email or "").strip().lower() != user["email"]:
        raise ForbiddenError(
            "Type the account's email address to confirm the deletion.",
            code="confirmation_mismatch",
        )

    # Data first: an account row without its files is a recoverable inconsistency, a
    # directory of files with no owner is an orphan nobody will ever find again.
    registry.destroy(user_id)
    if not store.delete_user(user_id):
        raise NotFoundError("That account was not found.")
    store.record_event(event="admin.user.delete", email=user["email"], actor_id=identity.user_id,
                       ip=meta.ip, user_agent=meta.user_agent, detail="workspace erased")
    return JSONResponse(status_code=200, content={"ok": True})


@router.get("/audit")
def audit(
    limit: int = Query(default=100, ge=1, le=500),
    user_id: str | None = Query(default=None),
    event: str | None = Query(default=None, max_length=64),
    store: AuthStore = Depends(get_store),
) -> list[dict[str, Any]]:
    """Authentication and administration events, newest first."""
    return store.list_events(limit=limit, user_id=user_id, event=event)


@router.post("/maintenance/purge-sessions")
def purge_sessions(store: AuthStore = Depends(get_store)) -> dict[str, Any]:
    """Drop expired sessions, spent reset tokens and stale throttle counters."""
    return {"removed_sessions": store.purge_expired()}


@router.post("/password-check")
def password_check(password: str = Body(embed=True, max_length=256)) -> dict[str, Any]:
    """Score a password an admin is about to set for somebody, before setting it."""
    result = security.strength(password)
    return {"score": result.score, "label": result.label, "suggestions": list(result.suggestions)}
