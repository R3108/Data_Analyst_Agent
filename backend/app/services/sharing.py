"""Read-only share links for analyses and boards."""

from __future__ import annotations

import secrets
from typing import Any

from app.core.errors import NotFoundError
from app.db import Database
from app.services.boards import BoardService

SHARED_MESSAGE_FIELDS = ("id", "role", "content", "payload", "status", "created_at")


class ShareService:
    def __init__(self, db: Database, boards: BoardService) -> None:
        self.db = db
        self.boards = boards

    def share_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        token = session.get("share_token") or secrets.token_urlsafe(18)
        self.db.set_share_token("session", session_id, token)
        return {"token": token}

    def unshare_session(self, session_id: str) -> None:
        self._require_session(session_id)
        self.db.set_share_token("session", session_id, None)

    def share_board(self, board_id: str) -> dict[str, Any]:
        board = self.boards.require(board_id)
        token = board.get("share_token") or secrets.token_urlsafe(18)
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
