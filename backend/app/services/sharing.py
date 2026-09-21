"""Read-only share links for analyses and boards.

A share token carries the owner's account id as a prefix — `usr_1a2b….<secret>`. The
public endpoint is unauthenticated by design, so it has to find *which* of the per-user
databases holds the link before it can resolve it, and the alternative is a second
cross-tenant index that has to stay in sync with two per-user tables. The prefix keeps
one source of truth.

The prefix discloses nothing: an account id is an opaque random string that appears in
no other public surface. The secret half is still 144 bits of entropy, and it is what
the lookup actually matches on — a caller who guesses a real account id and a wrong
secret gets the same 404 as one who guesses neither.
"""

from __future__ import annotations

import secrets
from typing import Any

from app.core.errors import NotFoundError
from app.db import Database
from app.services.boards import BoardService

SHARED_MESSAGE_FIELDS = ("id", "role", "content", "payload", "status", "created_at")
TOKEN_SEPARATOR = "."


def owner_of(token: str) -> str | None:
    """The account a share token belongs to, or None when it is not one of ours."""
    owner, separator, secret = (token or "").partition(TOKEN_SEPARATOR)
    if not separator or not secret or not owner.startswith("usr_"):
        return None
    return owner


class ShareService:
    def __init__(self, db: Database, boards: BoardService, *, owner_id: str = "") -> None:
        self.db = db
        self.boards = boards
        self.owner_id = owner_id

    def _mint(self) -> str:
        secret = secrets.token_urlsafe(18)
        return f"{self.owner_id}{TOKEN_SEPARATOR}{secret}" if self.owner_id else secret

    def share_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        token = session.get("share_token") or self._mint()
        self.db.set_share_token("session", session_id, token)
        return {"token": token}

    def unshare_session(self, session_id: str) -> None:
        self._require_session(session_id)
        self.db.set_share_token("session", session_id, None)

    def share_board(self, board_id: str) -> dict[str, Any]:
        board = self.boards.require(board_id)
        token = board.get("share_token") or self._mint()
        self.db.set_share_token("board", board_id, token)
        return {"token": token}

    def unshare_board(self, board_id: str) -> None:
        self.boards.require(board_id)
        self.db.set_share_token("board", board_id, None)

    def resolve(self, token: str) -> dict[str, Any]:
        resolved = self.db.resolve_share_token(token) if token else None
        if resolved is None:
            raise NotFoundError("This link is invalid or has been revoked.")
        kind, record_id = resolved
        if kind == "session":
            session = self._require_session(record_id)
            messages = [{k: m[k] for k in SHARED_MESSAGE_FIELDS} for m in self.db.list_messages(record_id)]
            return {"type": "session", "title": session["title"], "dataset_name": session["dataset_name"],
                    "updated_at": session["updated_at"], "messages": messages}
        board = self.boards.get(record_id)
        return {"type": "board", "title": board["title"], "description": board["description"],
                "updated_at": board["updated_at"], "items": board["items"]}

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.db.get_session(session_id)
        if session is None:
            raise NotFoundError(f"Session '{session_id}' was not found.")
        return session
