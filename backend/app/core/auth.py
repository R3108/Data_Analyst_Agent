"""Request authentication: who is calling, and may they.

Authentication is a *middleware* rather than a dependency on each route, on purpose.
A dependency protects the routes that remember to declare it; a middleware protects
the ones added next year by somebody who did not read this file. The default is
closed: every path under `/api` requires a session unless it appears in
`OPEN_PREFIXES`, and that list is short, explicit, and right here.

Two checks run before a write is allowed through:

1. **Origin verification.** Browsers always send `Origin` on a state-changing request.
   If one is present and is not a configured app origin, the request is refused — it
   came from a page we do not control. Non-browser clients send no `Origin` and are
   unaffected, because they also carry no ambient cookie to abuse.
2. **The double-submit CSRF token**, for requests that authenticate with a cookie.
   See `app.auth.cookies`.

Authorization beyond "is signed in" lives in the route dependencies in
`app.api.deps` — `require_admin` and friends — because it is per-endpoint by nature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.cookies import SAFE_METHODS, read_session_token, verify_csrf
from app.auth.service import AuthService
from app.core.config import Settings
from app.core.errors import AppError, ForbiddenError, UnauthorizedError

logger = logging.getLogger(__name__)

ANONYMOUS = "You"

# Reachable without a session. Each entry is either public information, its own
# credential exchange, or already gated by an unguessable token of its own.
OPEN_PREFIXES = (
    "/api/health",     # liveness, and the "is the backend up" banner
    "/api/auth/",      # register, sign in, forgot/reset password
    "/api/share/",     # read-only share links, which carry their own secret
)
# Exposed only when the docs are enabled at all, which production turns off.
DOC_PREFIXES = ("/docs", "/redoc", "/openapi.json")


@dataclass(frozen=True)
class Identity:
    """The authenticated caller, attached to `request.state.identity`."""

    user_id: str
    email: str
    name: str
    role: str
    session_id: str | None = None
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "must_change_password": self.must_change_password,
        }


def is_open_path(path: str, *, docs_enabled: bool) -> bool:
    if path.startswith(OPEN_PREFIXES):
        return True
    if docs_enabled and path.startswith(DOC_PREFIXES):
        return True
    return not path.startswith("/api")


def allowed_origin(request: Request, settings: Settings) -> bool:
    """True when a state-changing request came from somewhere we trust.

    A missing `Origin` is allowed: that is a non-browser client (curl, a CI job, the
    Python SDK), which cannot be induced to send our cookies by a hostile page. A
    *present* `Origin` is checked against the configured app origins and against the
    request's own host, so a single-origin deployment needs no extra configuration.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin in settings.trusted_origins:
        return True
    host = request.headers.get("host")
    return bool(host) and urlsplit(origin).netloc == host


class AuthMiddleware:
    """Resolves the caller, or rejects the request before any route sees it."""

    def __init__(self, app: ASGIApp, settings: Settings, auth: AuthService,
                 docs_enabled: bool = True) -> None:
        self.app = app
        self.settings = settings
        self.auth = auth
        self.docs_enabled = docs_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = scope.get("path", "")
        method = str(scope.get("method", "GET")).upper()

        # A CORS preflight carries no credentials by design and must never be answered
        # with a 401, or the browser reports an opaque network failure instead of the
        # real error.
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        try:
            if method not in SAFE_METHODS and not allowed_origin(request, self.settings):
                raise ForbiddenError(
                    "This request came from an origin this server does not trust.",
                    code="origin_rejected",
                )
            # CSRF is checked on every write that arrives with a session cookie, open
            # path or not. `/api/auth/login` is reachable without a session, but a
            # browser that already has one must still prove the request came from us.
            verify_csrf(request, self.settings)

            identity = self._identify_optional(request)
            if not is_open_path(path, docs_enabled=self.docs_enabled):
                if identity is None:
                    raise UnauthorizedError("Sign in to continue.", code="not_authenticated")
                # An account whose password an admin has reset is allowed exactly one
                # journey: read who it is, then set a new one. Letting it browse first
                # would leave a temporary password usable for as long as the prompt is
                # ignored. `/api/auth/*` stays reachable because it is an open path.
                if identity.must_change_password and path != "/api/me":
                    raise ForbiddenError(
                        "Choose a new password before continuing.",
                        code="password_change_required",
                    )
            scope.setdefault("state", {})["identity"] = identity
        except AppError as exc:
            response = JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})
            if exc.status_code == 429 and getattr(exc, "retry_after_s", None):
                response.headers["Retry-After"] = str(exc.retry_after_s)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _identify_optional(self, request: Request) -> Identity | None:
        token = read_session_token(request, self.settings)
        resolved = self.auth.resolve_session(token)
        if resolved is None:
            return None
        user, session = resolved
        return Identity(
            user_id=user["id"],
            email=user["email"],
            name=user["name"],
            role=user["role"],
            session_id=session["id"],
            must_change_password=bool(user.get("must_change_password")),
        )


def identity_of(request: Request) -> Identity | None:
    value = getattr(request.state, "identity", None)
    return value if isinstance(value, Identity) else None


def require_identity(request: Request) -> Identity:
    """The caller, or a 401. The middleware has normally already guaranteed this."""
    identity = identity_of(request)
    if identity is None:
        raise UnauthorizedError("Sign in to continue.", code="not_authenticated")
    return identity


def actor(request: Request) -> str:
    """The display name to attribute an action to, for comments and the activity feed."""
    identity = identity_of(request)
    return identity.name if identity else ANONYMOUS


__all__ = [
    "ANONYMOUS",
    "AuthMiddleware",
    "DOC_PREFIXES",
    "Identity",
    "OPEN_PREFIXES",
    "actor",
    "allowed_origin",
    "identity_of",
    "is_open_path",
    "require_identity",
]
