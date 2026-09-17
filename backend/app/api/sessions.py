from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.investigations import InvestigationService
from app.agent.service import DEFAULT_SESSION_TITLE, AnalystService
from app.api.deps import (
    get_activity,
    get_analyst,
    get_datasets,
    get_db,
    get_investigations,
    get_shares,
)
from app.core.auth import actor
from app.core.errors import NotFoundError
from app.db import Database
from app.services.activity import ActivityService
from app.services.datasets import DatasetService
from app.services.documents import session_to_pdf, session_to_pptx
from app.services.export import session_to_markdown
from app.services.notebook import build_notebook, notebook_bytes
from app.services.sharing import ShareService

router = APIRouter(prefix="/sessions", tags=["sessions"])
HEARTBEAT_S = 15


class CreateSessionBody(BaseModel):
    dataset_id: str
    title: str | None = Field(default=None, max_length=120)


class UpdateSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class InvestigateBody(BaseModel):
    objective: str | None = Field(default=None, max_length=2000)


def _require_session(db: Database, session_id: str) -> dict[str, Any]:
    session = db.get_session(session_id)
    if session is None:
        raise NotFoundError(f"Session '{session_id}' was not found.")
    return session


@router.get("")
def list_sessions(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.list_sessions()


@router.post("", status_code=201)
def create_session(body: CreateSessionBody, db: Database = Depends(get_db),
                   datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    dataset = datasets.get(body.dataset_id)
    session = db.create_session(dataset["id"], (body.title or "").strip() or DEFAULT_SESSION_TITLE)
    return {**session, "dataset_name": dataset["name"], "messages": []}


@router.get("/{session_id}")
def get_session(session_id: str, db: Database = Depends(get_db)) -> dict[str, Any]:
    session = _require_session(db, session_id)
    return {**session, "messages": db.list_messages(session_id)}


@router.patch("/{session_id}")
def rename_session(session_id: str, body: UpdateSessionBody, db: Database = Depends(get_db)) -> dict[str, Any]:
    _require_session(db, session_id)
    db.update_session(session_id, title=body.title.strip())
    return _require_session(db, session_id)


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, db: Database = Depends(get_db)) -> Response:
    if not db.delete_session(session_id):
        raise NotFoundError(f"Session '{session_id}' was not found.")
    return Response(status_code=204)


@router.post("/{session_id}/share")
def share_session(session_id: str, shares: ShareService = Depends(get_shares)) -> dict[str, Any]:
    return shares.share_session(session_id)


@router.delete("/{session_id}/share", status_code=204)
def unshare_session(session_id: str, shares: ShareService = Depends(get_shares)) -> Response:
    shares.unshare_session(session_id)
    return Response(status_code=204)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "analysis"


def _attachment(payload: bytes, filename: str, media_type: str) -> Response:
    return Response(content=payload, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{session_id}/export", response_class=PlainTextResponse)
def export_session(session_id: str, db: Database = Depends(get_db)) -> PlainTextResponse:
    session = _require_session(db, session_id)
    markdown = session_to_markdown(session, db.list_messages(session_id))
    slug = _slug(session["title"])
    return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{slug}.md"'})


@router.get("/{session_id}/notebook")
def export_session_notebook(
    session_id: str,
    db: Database = Depends(get_db),
    datasets: DatasetService = Depends(get_datasets),
) -> Response:
    """A runnable Jupyter notebook: the agent's code, its helpers and the write-up."""
    session = _require_session(db, session_id)
    try:
        dataset = datasets.get(session["dataset_id"])
    except NotFoundError:
        dataset = None
    notebook = build_notebook(session, db.list_messages(session_id), dataset)
    return _attachment(notebook_bytes(notebook), f"{_slug(session['title'])}.ipynb",
                       "application/x-ipynb+json")


@router.get("/{session_id}/export.pdf")
async def export_session_pdf(session_id: str, db: Database = Depends(get_db)) -> Response:
    session = _require_session(db, session_id)
    messages = db.list_messages(session_id)
    payload = await run_in_threadpool(session_to_pdf, session, messages)
    return _attachment(payload, f"{_slug(session['title'])}.pdf", "application/pdf")


@router.get("/{session_id}/export.pptx")
async def export_session_pptx(session_id: str, db: Database = Depends(get_db)) -> Response:
    session = _require_session(db, session_id)
    messages = db.list_messages(session_id)
    payload = await run_in_threadpool(session_to_pptx, session, messages)
    return _attachment(
        payload, f"{_slug(session['title'])}.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ChatBody,
               analyst: AnalystService = Depends(get_analyst)) -> StreamingResponse:
    # Validation errors surface as normal JSON errors before the stream opens.
    turn = await run_in_threadpool(analyst.prepare, session_id, body.message)
    return StreamingResponse(
        _sse(analyst.run(turn)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/{session_id}/investigate")
async def investigate(
    session_id: str,
    request: Request,
    body: InvestigateBody,
    investigations: InvestigationService = Depends(get_investigations),
    db: Database = Depends(get_db),
    activity: ActivityService = Depends(get_activity),
) -> StreamingResponse:
    """Deep research: scope an objective, run several analyses, synthesise a brief.

    Streams the same SSE events as `/chat`, plus one `assistant_message` per sub-analysis
    as it completes, so each is inspectable while the next one runs.
    """
    session = _require_session(db, session_id)
    activity.record("investigation.run", actor=actor(request), subject_kind="session",
                    subject_id=session_id, subject_title=session["title"],
                    detail=(body.objective or "")[:160] or "a full picture of the dataset")
    return StreamingResponse(
        _sse(investigations.run(session_id, body.objective)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], default=str)}\n\n"
        yield "event: done\ndata: {}\n\n"
    finally:
        if not task.done():
            task.cancel()
