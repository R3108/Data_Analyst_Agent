from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import get_activity, get_briefings
from app.core.auth import actor
from app.services.activity import ActivityService
from app.services.briefings import BriefingService

router = APIRouter(prefix="/briefings", tags=["briefings"])


class BriefingBody(BaseModel):
    dataset_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=120)
    schedule_hours: int = Field(default=24, ge=1, le=720)
    enabled: bool = True
    deliver: bool = Field(default=True, description="Push the answer to subscribed alert channels")


class BriefingPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    question: str | None = Field(default=None, max_length=1000)
    schedule_hours: int | None = Field(default=None, ge=1, le=720)
    enabled: bool | None = None
    deliver: bool | None = None


@router.get("")
def list_briefings(dataset_id: str | None = Query(default=None),
                   briefings: BriefingService = Depends(get_briefings)) -> list[dict[str, Any]]:
    return briefings.list(dataset_id)


@router.post("", status_code=201)
def create_briefing(
    request: Request,
    body: BriefingBody = Body(...),
    briefings: BriefingService = Depends(get_briefings),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    """Save a question and re-ask it on a cadence. Unlike a monitor, each run costs tokens."""
    briefing = briefings.create(body.model_dump())
    activity.record("briefing.create", actor=actor(request), subject_kind="briefing",
                    subject_id=briefing["id"], subject_title=briefing["title"],
                    detail=f"every {briefing['schedule_hours']}h")
    return briefing


@router.get("/{briefing_id}")
def get_briefing(briefing_id: str,
                 briefings: BriefingService = Depends(get_briefings)) -> dict[str, Any]:
    return briefings.get(briefing_id)


@router.patch("/{briefing_id}")
def update_briefing(briefing_id: str, body: BriefingPatch = Body(...),
                    briefings: BriefingService = Depends(get_briefings)) -> dict[str, Any]:
    return briefings.update(briefing_id, body.model_dump(exclude_none=True))


@router.delete("/{briefing_id}", status_code=204)
def delete_briefing(briefing_id: str,
                    briefings: BriefingService = Depends(get_briefings)) -> Response:
    briefings.delete(briefing_id)
    return Response(status_code=204)


@router.post("/{briefing_id}/run")
async def run_briefing(
    briefing_id: str,
    request: Request,
    deliver: bool | None = Query(default=None, description="Override the saved delivery setting"),
    briefings: BriefingService = Depends(get_briefings),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    """Ask the question now against the newest version of its dataset."""
    result = await briefings.run(briefing_id, deliver=deliver)
    briefing = result["briefing"]
    activity.record("briefing.run", actor=actor(request), subject_kind="briefing",
                    subject_id=briefing_id, subject_title=briefing["title"],
                    detail=briefing.get("last_headline") or "")
    return result
