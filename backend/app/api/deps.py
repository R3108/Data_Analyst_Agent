"""Request-scoped dependencies.

Every service a route can reach is resolved *through the caller's workspace*, never
from application state. That single indirection is what makes per-user isolation hold
for the whole API surface: a route asks for `Depends(get_datasets)` exactly as it did
when Numera was single-tenant, and gets a `DatasetService` bound to one account's
database file and one account's `datasets/` directory. There is no way to ask for
"the" dataset service, because there is no longer such a thing.

`get_auth`, `get_store` and `get_registry` are the exceptions: the control plane is
shared by definition, and the routes that use it are the auth and admin routers.
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.agent.investigations import InvestigationService
from app.agent.service import AnalystService
from app.auth.service import AuthService, RequestMeta
from app.auth.store import AuthStore
from app.core.auth import Identity, require_identity
from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.core.workspaces import Workspace, WorkspaceRegistry
from app.db import Database
from app.services.activity import ActivityService
from app.services.boards import BoardService
from app.services.briefings import BriefingService
from app.services.comments import CommentService
from app.services.datasets import DatasetService
from app.services.monitors import MonitorService
from app.services.notifications import NotificationService
from app.services.sharing import ShareService
from app.services.sources import SourceService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> WorkspaceRegistry:
    return request.app.state.workspaces


def get_store(request: Request) -> AuthStore:
    return request.app.state.auth_store


def get_auth(request: Request) -> AuthService:
    return request.app.state.auth


def get_identity(request: Request) -> Identity:
    """The signed-in caller. The middleware has already rejected anonymous requests."""
    return require_identity(request)


def require_admin(identity: Identity = Depends(get_identity)) -> Identity:
    """Gate a route on the admin role.

    403 rather than 404: the caller is authenticated and the route plainly exists, so
    hiding it would only make a legitimate user's permission problem harder to
    diagnose. Nothing about the resource is disclosed by saying "not you".
    """
    if not identity.is_admin:
        raise ForbiddenError(
            "This area is limited to administrators.", code="admin_required"
        )
    return identity


def get_meta(request: Request) -> RequestMeta:
    """Client address and user agent, for the audit trail and the throttles.

    `X-Forwarded-For` is trusted only when the deployment says to. Behind a reverse
    proxy it is the only way to see the real client; exposed directly it is a
    client-controlled string, and trusting it would let an attacker mint a fresh
    rate-limit bucket for every guess.
    """
    settings: Settings = request.app.state.settings
    client = request.client.host if request.client else None
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            client = forwarded.split(",")[0].strip() or client
    return RequestMeta(ip=client, user_agent=request.headers.get("user-agent"))


def get_workspace(request: Request, identity: Identity = Depends(get_identity)) -> Workspace:
    """The caller's private workspace — the root of every data dependency below."""
    registry: WorkspaceRegistry = request.app.state.workspaces
    return registry.for_user(identity.user_id)


def get_db(workspace: Workspace = Depends(get_workspace)) -> Database:
    return workspace.db


def get_datasets(workspace: Workspace = Depends(get_workspace)) -> DatasetService:
    return workspace.datasets


def get_analyst(workspace: Workspace = Depends(get_workspace)) -> AnalystService:
    return workspace.analyst


def get_boards(workspace: Workspace = Depends(get_workspace)) -> BoardService:
    return workspace.boards


def get_shares(workspace: Workspace = Depends(get_workspace)) -> ShareService:
    return workspace.shares


def get_monitors(workspace: Workspace = Depends(get_workspace)) -> MonitorService:
    return workspace.monitors


def get_notifier(workspace: Workspace = Depends(get_workspace)) -> NotificationService:
    return workspace.notifier


def get_sources(workspace: Workspace = Depends(get_workspace)) -> SourceService:
    return workspace.sources


def get_comments(workspace: Workspace = Depends(get_workspace)) -> CommentService:
    return workspace.comments


def get_activity(workspace: Workspace = Depends(get_workspace)) -> ActivityService:
    return workspace.activity


def get_briefings(workspace: Workspace = Depends(get_workspace)) -> BriefingService:
    return workspace.briefings


def get_investigations(workspace: Workspace = Depends(get_workspace)) -> InvestigationService:
    return workspace.investigations
