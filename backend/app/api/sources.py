from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import get_activity, get_monitors, get_notifier, get_sources
from app.core.auth import actor
from app.services.activity import ActivityService
from app.services.monitors import MonitorService
from app.services.notifications import NotificationService
from app.services.sources import DIALECTS, SourceService

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    dsn: str = Field(min_length=1, max_length=2000,
                     description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pw@host/db")
    query: str = Field(min_length=1, max_length=20000, description="A single read-only SELECT")
    refresh_minutes: int = Field(default=0, ge=0, le=10080)
    enabled: bool = True


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    # Omit to keep the stored credentials; the client only ever sees a redacted copy.
    dsn: str | None = Field(default=None, max_length=2000)
    query: str | None = Field(default=None, max_length=20000)
    refresh_minutes: int | None = Field(default=None, ge=0, le=10080)
    enabled: bool | None = None


@router.get("")
def list_sources(sources: SourceService = Depends(get_sources)) -> dict[str, Any]:
    return {
        "sources": sources.list(),
        "dialects": [{"kind": kind, **info} for kind, info in sorted(DIALECTS.items())],
    }


@router.post("/test")
async def test_source(body: SourceBody = Body(...),
                      sources: SourceService = Depends(get_sources)) -> dict[str, Any]:
    """Run the query with a small cap and report what came back, or the real error."""
    return await run_in_threadpool(sources.test, body.model_dump())


@router.post("", status_code=201)
def create_source(
    request: Request,
    body: SourceBody = Body(...),
    sources: SourceService = Depends(get_sources),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    source = sources.create(body.model_dump())
    activity.record("source.create", actor=actor(request), subject_kind="source",
                    subject_id=source["id"], subject_title=source["name"],
                    detail=f"{source['kind']} query")
    return source


@router.patch("/{source_id}")
def update_source(source_id: str, body: SourcePatch = Body(...),
                  sources: SourceService = Depends(get_sources)) -> dict[str, Any]:
    return sources.update(source_id, body.model_dump(exclude_none=True))


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str, sources: SourceService = Depends(get_sources)) -> Response:
    sources.delete(source_id)
    return Response(status_code=204)


@router.post("/{source_id}/sync")
async def sync_source(
    source_id: str,
    request: Request,
    background: BackgroundTasks,
    sources: SourceService = Depends(get_sources),
    monitors: MonitorService = Depends(get_monitors),
    notifier: NotificationService = Depends(get_notifier),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    """Pull the query now and file the result as the next version of its dataset."""
    result = await run_in_threadpool(sources.sync, source_id)
    dataset = result["dataset"]
    activity.record("source.sync", actor=actor(request), subject_kind="source",
                    subject_id=source_id, subject_title=result["source"]["name"],
                    detail=f"{dataset['n_rows']:,} rows → v{dataset.get('version', 1)}")
    # Fresh data is exactly when a watched metric should be re-checked and a broken
    # contract announced — the same treatment an uploaded file gets.
    if dataset.get("version", 1) > 1:
        background.add_task(monitors.run_for_lineage, dataset["id"])
    if dataset.get("contract_result"):
        background.add_task(_announce_contract, notifier, dataset)
    return result


def _announce_contract(notifier: NotificationService, dataset: dict[str, Any]) -> None:
    alert = notifier.contract_alert(dataset, dataset.get("contract_result") or {})
    if alert is not None:
        notifier.notify(alert)
