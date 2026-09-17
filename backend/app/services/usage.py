"""Usage aggregation and configurable OpenAI spend guardrails."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database


def aggregate_usage(db: Database, since: datetime) -> dict[str, Any]:
    """Aggregate persisted assistant usage from ``since`` through now."""
    totals: dict[str, Any] = {
        "analyses": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
    }
    by_day: dict[str, dict[str, Any]] = {}
    for created_at, payload in db.assistant_payloads_since(since.isoformat()):
        summary = payload.get("usage")
        if not summary:
            continue
        cost = float(summary.get("cost_usd") or 0.0)
        totals["analyses"] += 1
        totals["cost_usd"] += cost
        for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
            totals[key] += int(summary.get(key) or 0)
        day = by_day.setdefault(
            created_at[:10],
            {"date": created_at[:10], "analyses": 0, "cost_usd": 0.0},
        )
        day["analyses"] += 1
        day["cost_usd"] += cost
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["by_day"] = [
        {**day, "cost_usd": round(day["cost_usd"], 6)}
        for day in sorted(by_day.values(), key=lambda item: item["date"])
    ]
    return totals


def monthly_budget_status(
    db: Database,
    limit_usd: float,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return current calendar-month usage against the configured budget."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    period_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        reset_at = period_start.replace(year=period_start.year + 1, month=1)
    else:
        reset_at = period_start.replace(month=period_start.month + 1)

    spent = float(aggregate_usage(db, period_start)["cost_usd"])
    enabled = limit_usd > 0
    remaining = max(limit_usd - spent, 0.0) if enabled else None
    used_pct = (spent / limit_usd * 100) if enabled else None
    return {
        "enabled": enabled,
        "limit_usd": round(limit_usd, 2) if enabled else None,
        "spent_usd": round(spent, 6),
        "remaining_usd": round(remaining, 6) if remaining is not None else None,
        "used_pct": round(used_pct, 1) if used_pct is not None else None,
        "exhausted": enabled and spent >= limit_usd,
        "period_start": period_start.date().isoformat(),
        "reset_at": reset_at.date().isoformat(),
    }


def usage_report(db: Database, days: int, monthly_budget_usd: float) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return {
        "days": days,
        **aggregate_usage(db, since),
        "budget": monthly_budget_status(db, monthly_budget_usd),
    }
