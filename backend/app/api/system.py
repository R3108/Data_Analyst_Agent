from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field

from app import __version__
from app.api.deps import get_activity, get_db, get_settings_dep
from app.core.auth import actor, parse_tokens
from app.core.config import Settings
from app.db import Database
from app.services import memory
from app.services.activity import ActivityService
from app.services.sources import DIALECTS
from app.services.usage import usage_report

router = APIRouter(tags=["system"])


class RouteBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=3, ge=1, le=10)


@router.get("/health")
def health(settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "model": settings.openai_model,
        "llm_credentials_detected": settings.llm_credentials_detected,
        "sandbox": {"timeout_s": settings.sandbox_timeout_s, "memory_mb": settings.sandbox_memory_mb},
        "monitors": {
            "interval_minutes": settings.monitor_interval_minutes,
            "root_cause": settings.monitor_root_cause,
        },
        "privacy": {"scan_enabled": settings.privacy_scan_enabled},
        "alerts": {
            "enabled": settings.alerts_enabled,
            "email_configured": bool(settings.smtp_host),
            "digest_hours": settings.alert_digest_hours,
        },
        "briefings": {"interval_minutes": settings.briefing_interval_minutes},
        "investigations": {"max_steps": settings.investigation_max_steps},
        "recall": {"enabled": settings.recall_enabled, "limit": settings.recall_limit},
        "sources": {
            "dialects": sorted(DIALECTS),
            "sync_interval_minutes": settings.source_sync_interval_minutes,
        },
        "workspace": {"protected": bool(parse_tokens(settings.workspace_tokens))},
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_rows": settings.max_rows,
            "ai_monthly_budget_usd": settings.ai_monthly_budget_usd,
        },
    }


@router.get("/me")
def me(request: Request, settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    """Who this token says you are — used to attribute comments in the UI."""
    return {"name": actor(request), "protected": bool(parse_tokens(settings.workspace_tokens))}


@router.get("/usage")
def usage(
    days: int = Query(default=30, ge=1, le=365),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Token usage and estimated model spend over the last `days` days."""
    return usage_report(db, days, settings.ai_monthly_budget_usd)


@router.get("/activity")
def activity(
    limit: int = Query(default=50, ge=1, le=200),
    subject_id: str | None = Query(default=None),
    service: ActivityService = Depends(get_activity),
) -> list[dict[str, Any]]:
    """What has happened in this workspace, newest first."""
    return service.list(limit=limit, subject_id=subject_id)


@router.post("/route")
def route_question(
    body: RouteBody = Body(...),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Which dataset can answer this question? Deterministic term matching, no model call."""
    # Only the newest version of each lineage: routing to a superseded upload is never right.
    candidates: list[dict[str, Any]] = []
    for summary in db.list_datasets():
        record = db.get_dataset(summary["id"])
        if record is not None:
            candidates.append(record)
    ranked = memory.route(candidates, body.question, limit=body.limit)
    return {
        "question": body.question,
        "matches": ranked,
        "best": ranked[0] if ranked else None,
        "confident": bool(ranked and ranked[0].get("confident")),
    }


@router.get("/recall")
def recall(
    q: str = Query(min_length=1, max_length=2000),
    dataset_id: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Prior analyses close to `q`. The same search the agent gets before it plans."""
    matches = memory.recall(db.recent_analyses(dataset_id), q, limit=limit)
    return {"question": q, "matches": matches, "summary": memory.summarize(matches)}
