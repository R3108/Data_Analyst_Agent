from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import get_boards, get_shares
from app.services.boards import BoardService
from app.services.documents import board_to_pdf, board_to_pptx
from app.services.sharing import ShareService

router = APIRouter(prefix="/boards", tags=["boards"])


class CreateBoardBody(BaseModel):
    title: str = Field(default="Untitled board", min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class UpdateBoardBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class AddItemBody(BaseModel):
    kind: Literal["kpi", "chart", "table", "insight", "note"]
    message_id: str | None = None
    index: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=10_000)
    title: str | None = Field(default=None, max_length=200)
    # The deterministic views have no message behind them: name the dataset and the
    # parameters instead, and the server recomputes the result before snapshotting it.
    source: Literal["analysis", "drivers", "significance", "scenarios", "cohorts", "forecast"] = "analysis"
    dataset_id: str | None = None
    params: dict[str, Any] | None = None


class UpdateItemBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    wide: bool | None = None
    text: str | None = Field(default=None, max_length=10_000)


class ReorderBody(BaseModel):
    item_ids: list[str]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "board"


def _attachment(payload: bytes, filename: str, media_type: str) -> Response:
    return Response(content=payload, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("")
def list_boards(boards: BoardService = Depends(get_boards)) -> list[dict[str, Any]]:
    return boards.list()


@router.post("", status_code=201)
def create_board(body: CreateBoardBody, boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    return {**boards.create(body.title, body.description), "items": []}


@router.get("/{board_id}")
def get_board(board_id: str, boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    return boards.get(board_id)


@router.patch("/{board_id}")
def update_board(board_id: str, body: UpdateBoardBody, boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    return boards.update(board_id, body.title, body.description)


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: str, boards: BoardService = Depends(get_boards)) -> Response:
    boards.delete(board_id)
    return Response(status_code=204)


@router.post("/{board_id}/items", status_code=201)
def add_item(board_id: str, body: AddItemBody, boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    if body.kind == "note":
        return boards.add_note(board_id, body.text, body.title)
    if body.source != "analysis":
        return boards.pin_computed(board_id, body.source, dataset_id=body.dataset_id,
                                   params=body.params, kind=body.kind, index=body.index,
                                   title=body.title)
    return boards.pin(board_id, body.kind, body.message_id, body.index, body.title)


@router.patch("/{board_id}/items/{item_id}")
def update_item(board_id: str, item_id: str, body: UpdateItemBody,
                boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    return boards.update_item(board_id, item_id, title=body.title, wide=body.wide, text=body.text)


@router.delete("/{board_id}/items/{item_id}", status_code=204)
def remove_item(board_id: str, item_id: str, boards: BoardService = Depends(get_boards)) -> Response:
    boards.remove_item(board_id, item_id)
    return Response(status_code=204)


@router.put("/{board_id}/order")
def reorder_items(board_id: str, body: ReorderBody, boards: BoardService = Depends(get_boards)) -> dict[str, Any]:
    return boards.reorder(board_id, body.item_ids)


@router.get("/{board_id}/export.pdf")
async def export_board_pdf(board_id: str, boards: BoardService = Depends(get_boards)) -> Response:
    board = boards.get(board_id)
    payload = await run_in_threadpool(board_to_pdf, board)
    return _attachment(payload, f"{_slug(board['title'])}.pdf", "application/pdf")


@router.get("/{board_id}/export.pptx")
async def export_board_pptx(board_id: str, boards: BoardService = Depends(get_boards)) -> Response:
    board = boards.get(board_id)
    payload = await run_in_threadpool(board_to_pptx, board)
    return _attachment(
        payload, f"{_slug(board['title'])}.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.post("/{board_id}/share")
def share_board(board_id: str, shares: ShareService = Depends(get_shares)) -> dict[str, Any]:
    return shares.share_board(board_id)


@router.delete("/{board_id}/share", status_code=204)
def unshare_board(board_id: str, shares: ShareService = Depends(get_shares)) -> Response:
    shares.unshare_board(board_id)
    return Response(status_code=204)
