from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_registry, get_store
from app.auth.store import AuthStore
from app.core.errors import NotFoundError
from app.core.workspaces import WorkspaceRegistry
from app.services.sharing import owner_of

router = APIRouter(prefix="/share", tags=["share"])

LINK_DEAD = "This link is invalid or has been revoked."


@router.get("/{token}")
def get_shared(
    token: str,
    request: Request,
    registry: WorkspaceRegistry = Depends(get_registry),
    store: AuthStore = Depends(get_store),
) -> dict[str, Any]:
    """Public, read-only view of a shared analysis or board.

    The one endpoint that reaches into a workspace without a session, so it is also the
    one that has to find the workspace itself: the owner's account id is the first half
    of the token. Every failure — a malformed token, an unknown owner, a suspended
    account, a revoked link — returns the same 404, so the endpoint cannot be used to
    probe which accounts exist.
    """
    owner_id = owner_of(token)
    if owner_id is None:
        raise NotFoundError(LINK_DEAD)
    owner = store.get_user(owner_id)
    if owner is None or owner["status"] != "active":
        # A suspended account's public links go dark with it.
        raise NotFoundError(LINK_DEAD)
    return registry.for_user(owner_id).shares.resolve(token)
