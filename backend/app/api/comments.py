from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import get_activity, get_comments
from app.core.auth import actor
from app.services.activity import ActivityService
from app.services.comments import CommentService

router = APIRouter(prefix="/comments", tags=["comments"])


class CommentBody(BaseModel):
    subject_kind: Literal["session", "board", "message", "board_item"]
    subject_id: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=4000)
    parent_id: str | None = None
    # Only used when the workspace is open; a protected one takes the name from the token.
    author: str | None = Field(default=None, max_length=80)


class CommentPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    resolved: bool | None = None


@router.get("")
def list_comments(
    subject_kind: Literal["session", "board", "message", "board_item"] = Query(...),
    subject_id: str = Query(min_length=1),
    comments: CommentService = Depends(get_comments),
) -> dict[str, Any]:
    """Threads on one artifact, oldest first, replies nested."""
    return comments.list(subject_kind, subject_id)


@router.get("/counts")
def comment_counts(
    subject_kind: Literal["session", "board", "message", "board_item"] = Query(...),
    ids: str = Query(description="Comma-separated subject ids"),
    comments: CommentService = Depends(get_comments),
) -> dict[str, int]:
    """Unresolved counts for a list of subjects — one request for a whole board."""
    subject_ids = [value for value in (i.strip() for i in ids.split(",")) if value][:200]
    return comments.counts(subject_kind, subject_ids)


@router.post("", status_code=201)
def add_comment(
    request: Request,
    body: CommentBody = Body(...),
    comments: CommentService = Depends(get_comments),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    author = actor(request)
    # In an open workspace nobody is authenticated, so an offered name is the only signal.
    if author == "You" and body.author:
        author = body.author
    comment = comments.add(body.subject_kind, body.subject_id, body.body, author, body.parent_id)
    activity.record("comment.add", actor=author, subject_kind=body.subject_kind,
                    subject_id=body.subject_id, subject_title=f"a {body.subject_kind}",
                    detail=body.body[:160])
    return comment


@router.patch("/{comment_id}")
def update_comment(comment_id: str, body: CommentPatch = Body(...),
                   comments: CommentService = Depends(get_comments)) -> dict[str, Any]:
    """Edit the text, or resolve the whole thread this comment belongs to."""
    return comments.update(comment_id, body=body.body, resolved=body.resolved)


@router.delete("/{comment_id}", status_code=204)
def delete_comment(comment_id: str, comments: CommentService = Depends(get_comments)) -> Response:
    comments.delete(comment_id)
    return Response(status_code=204)
