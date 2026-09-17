"""Optional workspace access control and caller identity.

Numera runs single-tenant by default: start it, open it, use it. That is the right
default for a tool you point at your own laptop, and the wrong one the moment a board
with a revenue breakdown on it is reachable from a colleague's browser.

Setting `WORKSPACE_TOKENS` turns the API into a shared workspace without introducing a
user database: each entry is `token:Display Name`, every `/api` request must present one
as `Authorization: Bearer …`, and the matched name becomes the actor recorded against
comments, activity and anything else attributable. Unset, every caller is the local user
and nothing changes.

Deliberately *not* a full identity system — there are no passwords to leak, no sessions
to fixate and no roles to get wrong. It is a shared secret per person, which is honest
about what it protects: who can reach this workspace, and whose name is on an action.
Public share links keep working either way, because they carry their own unguessable
token and are read-only by construction.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

ANONYMOUS = "You"
HEADER = "authorization"
# Reachable without a token: liveness, the OpenAPI docs and read-only share links, each of
# which is either public information or already gated by its own unguessable token.
OPEN_PREFIXES = ("/api/health", "/api/share/", "/docs", "/redoc", "/openapi.json")


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


@dataclass(frozen=True)
class Identity:
    name: str
    authenticated: bool


def parse_tokens(raw: str) -> dict[str, str]:
    """`token:Name, other:Other Name` → {token: name}. Blank entries are ignored."""
    tokens: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        token, _, name = entry.partition(":")
        token = token.strip()
        if token:
            tokens[token] = name.strip() or "Teammate"
    return tokens


def identify(request: Request, settings: Settings) -> Identity:
    """Who is calling. Raises when the workspace is protected and the token is wrong."""
    tokens = parse_tokens(settings.workspace_tokens)
    if not tokens:
        return Identity(ANONYMOUS, authenticated=False)

    header = request.headers.get(HEADER, "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise UnauthorizedError(
            "This workspace requires an access token. Send it as 'Authorization: Bearer <token>'."
        )
    presented = presented.strip()
    # Compare against every configured token in constant time, so a wrong token cannot be
    # narrowed down by how long the rejection took.
    matched: str | None = None
    for token, name in tokens.items():
        if hmac.compare_digest(token, presented):
            matched = name
    if matched is None:
        raise UnauthorizedError("That access token is not valid for this workspace.")
    return Identity(matched, authenticated=True)


class WorkspaceAuthMiddleware:
    """Rejects unauthenticated API calls before they reach a route.

    A middleware rather than a dependency so that a route added later cannot forget it.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not parse_tokens(self.settings.workspace_tokens):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # CORS preflight carries no Authorization header by design.
        if scope.get("method") == "OPTIONS" or path.startswith(OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        try:
            identity = identify(Request(scope), self.settings)
        except UnauthorizedError as exc:
            from fastapi.responses import JSONResponse

            response = JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, send)


def actor(request: Request) -> str:
    """The display name to attribute an action to. Never raises — auth already ran."""
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, Identity):
        return identity.name
    return ANONYMOUS


__all__ = [
    "ANONYMOUS",
    "Identity",
    "UnauthorizedError",
    "WorkspaceAuthMiddleware",
    "actor",
    "identify",
    "parse_tokens",
]
