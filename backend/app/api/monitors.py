from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import get_monitors
from app.services.monitors import MonitorService

router = APIRouter(prefix="/monitors", tags=["monitors"])


class CreateMonitorBody(BaseModel):
    message_id: str
    index: int = Field(default=0, ge=0)
    direction: Literal["above", "below", "change_pct"]
    threshold: float
    title: str | None = Field(default=None, max_length=200)


class UpdateMonitorBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    direction: Literal["above", "below", "change_pct"] | None = None
    threshold: float | None = None
    enabled: bool | None = None


@router.get("")
def list_monitors(
    dataset_id: str | None = Query(default=None),
    monitors: MonitorService = Depends(get_monitors),
) -> list[dict[str, Any]]:
    return monitors.list(dataset_id)


@router.post("", status_code=201)
def create_monitor(body: CreateMonitorBody,
                   monitors: MonitorService = Depends(get_monitors)) -> dict[str, Any]:
    return monitors.create(
        message_id=body.message_id, index=body.index, direction=body.direction,
        threshold=body.threshold, title=body.title,
    )


@router.get("/digest")
def monitor_digest(monitors: MonitorService = Depends(get_monitors)) -> dict[str, Any]:
    """Current state of every monitor — the briefing a stakeholder wants each morning."""
    return monitors.digest()


@router.get("/digest.md", response_class=PlainTextResponse)
def monitor_digest_markdown(monitors: MonitorService = Depends(get_monitors)) -> PlainTextResponse:
    return PlainTextResponse(
        monitors.digest_markdown(),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="monitor-briefing.md"'},
    )


@router.post("/run")
async def run_all_monitors(
    dataset_id: str | None = Query(default=None),
    monitors: MonitorService = Depends(get_monitors),
) -> dict[str, Any]:
    # Each evaluation is a blocking sandbox run, so keep it off the event loop.
    results = await run_in_threadpool(monitors.run_all, dataset_id)
    return {"ran": len(results), "results": results}


@router.get("/{monitor_id}")
def get_monitor(monitor_id: str, monitors: MonitorService = Depends(get_monitors)) -> dict[str, Any]:
    return monitors.get(monitor_id)


@router.patch("/{monitor_id}")
def update_monitor(monitor_id: str, body: UpdateMonitorBody,
                   monitors: MonitorService = Depends(get_monitors)) -> dict[str, Any]:
    return monitors.update(monitor_id, title=body.title, direction=body.direction,
                           threshold=body.threshold, enabled=body.enabled)


@router.delete("/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: str, monitors: MonitorService = Depends(get_monitors)) -> Response:
    monitors.delete(monitor_id)
    return Response(status_code=204)


@router.post("/{monitor_id}/run")
async def run_monitor(monitor_id: str,
                      monitors: MonitorService = Depends(get_monitors)) -> dict[str, Any]:
    monitors.require(monitor_id)
    return await run_in_threadpool(monitors.run, monitor_id)
