from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import get_monitors, get_notifier, get_settings_dep
from app.core.config import Settings
from app.services.monitors import MonitorService
from app.services.notifications import EVENTS, NotificationService

router = APIRouter(prefix="/alerts", tags=["alerts"])

Event = Literal["breach", "recovery", "failure", "contract", "digest", "briefing"]


class CreateChannelBody(BaseModel):
    kind: Literal["slack", "webhook", "email"]
    target: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=120)
    events: list[Event] | None = None


class UpdateChannelBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target: str | None = Field(default=None, min_length=1, max_length=2000)
    events: list[Event] | None = None
    enabled: bool | None = None


@router.get("")
def alert_settings(
    notifier: NotificationService = Depends(get_notifier),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Channels, recent deliveries and what the server can actually send."""
    return {
        "enabled": settings.alerts_enabled,
        "events": list(EVENTS),
        "email_configured": bool(settings.smtp_host),
        "digest_hours": settings.alert_digest_hours,
        "channels": notifier.list(),
        "deliveries": notifier.deliveries(20),
    }


@router.get("/channels")
def list_channels(notifier: NotificationService = Depends(get_notifier)) -> list[dict[str, Any]]:
    return notifier.list()


@router.post("/channels", status_code=201)
def create_channel(body: CreateChannelBody,
                   notifier: NotificationService = Depends(get_notifier)) -> dict[str, Any]:
    return notifier.create(name=body.name or "", kind=body.kind, target=body.target,
                           events=list(body.events) if body.events else None)


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: str, body: UpdateChannelBody,
                   notifier: NotificationService = Depends(get_notifier)) -> dict[str, Any]:
    return notifier.update(channel_id, name=body.name, target=body.target,
                           events=list(body.events) if body.events is not None else None,
                           enabled=body.enabled)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str,
                   notifier: NotificationService = Depends(get_notifier)) -> Response:
    notifier.delete(channel_id)
    return Response(status_code=204)


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str,
                       notifier: NotificationService = Depends(get_notifier)) -> dict[str, Any]:
    """Send a real alert now, so a misconfigured webhook is found before it matters."""
    notifier.require(channel_id)
    return await run_in_threadpool(notifier.test, channel_id)


@router.get("/deliveries")
def list_deliveries(
    limit: int = Query(default=30, ge=1, le=200),
    notifier: NotificationService = Depends(get_notifier),
) -> list[dict[str, Any]]:
    return notifier.deliveries(limit)


@router.post("/digest")
async def send_digest(
    notifier: NotificationService = Depends(get_notifier),
    monitors: MonitorService = Depends(get_monitors),
) -> dict[str, Any]:
    """Push the current monitor briefing to every channel subscribed to `digest`."""
    alert = notifier.digest_alert(monitors.digest())
    deliveries = await run_in_threadpool(notifier.notify, alert)
    return {"sent": len(deliveries), "deliveries": deliveries}
