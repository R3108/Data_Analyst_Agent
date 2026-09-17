"""Discussion threads on an analysis, a board or a single pinned tile.

The decision an analysis feeds into is made in a conversation, and that conversation
currently happens somewhere the numbers are not — a chat window where nobody can see
which version of the dataset the figure came from. Anchoring it to the artifact keeps the
question and the number in the same place.

Threads are one level deep on purpose: a reply to a reply is a meeting, not a comment.
Resolving a thread resolves its replies, because a half-resolved thread is a to-do list
nobody trusts.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database

logger = logging.getLogger(__name__)

SUBJECTS = ("session", "board", "message", "board_item")
SubjectKind = Literal["session", "board", "message", "board_item"]
MAX_BODY_CHARS = 4000
MAX_THREADS = 200


class CommentService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, subject_kind: str, subject_id: str) -> dict[str, Any]:
        """Threads, oldest first, each with its replies nested underneath."""
        kind = _kind(subject_kind)
        flat = self.db.list_comments(kind, _subject_id(subject_id))
        by_parent: dict[str, list[dict[str, Any]]] = {}
        roots: list[dict[str, Any]] = []
        for comment in flat:
            if comment["parent_id"]:
                by_parent.setdefault(comment["parent_id"], []).append(comment)
            else:
                roots.append(comment)
        threads = [{**root, "replies": by_parent.get(root["id"], [])} for root in roots]
        return {
            "subject_kind": kind,
            "subject_id": subject_id,
            "threads": threads,
            "open_count": sum(1 for t in threads if not t["resolved"]),
            "total": len(flat),
        }

    def add(self, subject_kind: str, subject_id: str, body: str, author: str,
            parent_id: str | None = None) -> dict[str, Any]:
        kind = _kind(subject_kind)
        text = _body(body)
        if parent_id:
            parent = self.db.get_comment(parent_id)
            if parent is None:
                raise NotFoundError("The comment being replied to no longer exists.")
            if parent["parent_id"]:
                # Flatten rather than reject: the reader meant to reply to the thread.
                parent_id = parent["parent_id"]
            if parent["subject_kind"] != kind or parent["subject_id"] != subject_id:
                raise InvalidInputError("That reply belongs to a different discussion.")
        elif len(self.db.list_comments(kind, subject_id)) >= MAX_THREADS:
            raise InvalidInputError(
                f"This item already has {MAX_THREADS} comments. Resolve some before adding more."
            )
        return self.db.add_comment({
            "subject_kind": kind,
            "subject_id": _subject_id(subject_id),
            "parent_id": parent_id,
            "author": (author or "Anonymous").strip()[:80] or "Anonymous",
            "body": text,
        })

    def update(self, comment_id: str, *, body: str | None = None,
               resolved: bool | None = None) -> dict[str, Any]:
        comment = self.db.get_comment(comment_id)
        if comment is None:
            raise NotFoundError(f"Comment '{comment_id}' was not found.")
        if body is not None:
            self.db.update_comment(comment_id, body=_body(body))
        if resolved is not None:
            root = comment["parent_id"] or comment_id
            self.db.resolve_thread(root, bool(resolved))
        updated = self.db.get_comment(comment_id)
        if updated is None:  # pragma: no cover — deleted between read and write
            raise NotFoundError(f"Comment '{comment_id}' was not found.")
        return updated

    def delete(self, comment_id: str) -> None:
        """Deleting a thread root deletes its replies — the cascade is in the schema."""
        if not self.db.delete_comment(comment_id):
            raise NotFoundError(f"Comment '{comment_id}' was not found.")

    def counts(self, subject_kind: str, subject_ids: list[str]) -> dict[str, int]:
        return self.db.comment_counts(_kind(subject_kind), subject_ids)


def _kind(value: str) -> str:
    if value not in SUBJECTS:
        raise InvalidInputError(f"Comments attach to: {', '.join(SUBJECTS)}.")
    return value


def _subject_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidInputError("A comment needs something to attach to.")
    return text[:100]


def _body(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidInputError("A comment cannot be empty.")
    if len(text) > MAX_BODY_CHARS:
        raise InvalidInputError(f"Comments are limited to {MAX_BODY_CHARS:,} characters.")
    return text


__all__ = ["SUBJECTS", "CommentService"]
