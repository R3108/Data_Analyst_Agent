from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_shares
from app.services.sharing import ShareService

router = APIRouter(prefix="/share", tags=["share"])


@router.get("/{token}")
def get_shared(token: str, shares: ShareService = Depends(get_shares)) -> dict[str, Any]:
    """Public, read-only view of a shared analysis or board."""
    return shares.resolve(token)
