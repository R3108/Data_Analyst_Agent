"""Scheduled briefings: a question that answers itself every Monday morning.

A monitor watches one number and tells you when it leaves a range — cheap, deterministic,
and deliberately narrow. A briefing is the other half: a question in English, re-asked on
a cadence against whatever data has arrived since, with the written answer delivered to
Slack or an inbox.

That costs model tokens on every run, which is exactly why monitors exist and why this is
not the default. The distinction is honest in the UI and in the docs: watch a number with
a monitor, ask a question with a briefing.

Each run is an ordinary analyst turn in a session of its own, so the delivered summary
always links back to a full analysis with its code, charts and verification verdict
attached — nobody has to take the Slack message on faith.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agent.service import AnalystService
from app.core.config import Settings
from app.core.errors import AppError, InvalidInputError, NotFoundError
from app.db import Database
from app.services.datasets import DatasetService
from app.services.notifications import Alert, NotificationService

logger = logging.getLogger(__name__)

MAX_BRIEFINGS = 25
MAX_QUESTION_CHARS = 1000
MIN_HOURS = 1
MAX_HOURS = 24 * 30
# A briefing is a whole analysis; a very short cadence is almost always a mistake.
DEFAULT_HOURS = 24


class BriefingService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        datasets: DatasetService,
        analyst: AnalystService,
        notifier: NotificationService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets
        self.analyst = analyst
        self.notifier = notifier

    # ------------------------------------------------------------------ CRUD
    def list(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        return [self._decorate(b) for b in self.db.list_briefings(dataset_id)]

    def get(self, briefing_id: str) -> dict[str, Any]:
        record = self.db.get_briefing(briefing_id)
        if record is None:
            raise NotFoundError(f"Briefing '{briefing_id}' was not found.")
        return self._decorate(record)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if not dataset_id:
            raise InvalidInputError("A briefing needs a dataset.")
        dataset = self.datasets.get(dataset_id)
        if len(self.db.list_briefings()) >= MAX_BRIEFINGS:
            raise InvalidInputError(
                f"You already have {MAX_BRIEFINGS} briefings. Delete one before adding another."
            )
        question = _question(payload.get("question"))
        record = self.db.create_briefing({
            "title": _title(payload.get("title") or question),
            # Pinned to the lineage root, so the briefing follows the table across
            # re-uploads instead of freezing on the version it was created against.
            "dataset_id": dataset.get("root_dataset_id") or dataset["id"],
            "question": question,
            "schedule_hours": _hours(payload.get("schedule_hours")),
            "enabled": int(bool(payload.get("enabled", True))),
            "deliver": int(bool(payload.get("deliver", True))),
        })
        return self._decorate(record)

    def update(self, briefing_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.get(briefing_id)
        fields: dict[str, Any] = {}
        if patch.get("title") is not None:
            fields["title"] = _title(patch["title"])
        if patch.get("question") is not None:
            fields["question"] = _question(patch["question"])
        if patch.get("schedule_hours") is not None:
            fields["schedule_hours"] = _hours(patch["schedule_hours"])
        if patch.get("enabled") is not None:
            fields["enabled"] = bool(patch["enabled"])
        if patch.get("deliver") is not None:
            fields["deliver"] = bool(patch["deliver"])
        self.db.update_briefing(briefing_id, **fields)
        return self.get(briefing_id)

    def delete(self, briefing_id: str) -> None:
        if not self.db.delete_briefing(briefing_id):
            raise NotFoundError(f"Briefing '{briefing_id}' was not found.")

    # ------------------------------------------------------------------ execution
    async def run(self, briefing_id: str, *, deliver: bool | None = None) -> dict[str, Any]:
        """Ask the question against the newest data and record — and deliver — the answer."""
        briefing = self.get(briefing_id)
        try:
            dataset = self.datasets.latest_version(briefing["dataset_id"])
        except NotFoundError as exc:
            self.db.update_briefing(briefing_id, last_status="error", last_run_at=_now(),
                                    last_error=exc.message)
            raise

        session = self.db.create_session(dataset["id"], briefing["title"][:120])
        try:
            message = await self.analyst.answer(session["id"], briefing["question"])
        except AppError as exc:
            self.db.delete_session(session["id"])
            self.db.update_briefing(briefing_id, last_status="error", last_run_at=_now(),
                                    last_error=exc.message)
            logger.warning("Briefing %s failed: %s", briefing_id, exc.message)
            raise

        payload = message.get("payload") or {}
        report = payload.get("report") or {}
        ok = message.get("status") == "complete"
        headline = report.get("headline") or message.get("content") or ""
        self.db.update_briefing(
            briefing_id,
            last_run_at=_now(),
            last_status="ok" if ok else "error",
            last_session_id=session["id"],
            last_message_id=message["id"],
            last_headline=headline[:400],
            last_error=None if ok else (payload.get("error") or {}).get("message", "The analysis failed."),
            run_count=int(briefing.get("run_count") or 0) + 1,
        )

        deliveries: list[dict[str, Any]] = []
        should_deliver = briefing["deliver"] if deliver is None else deliver
        if should_deliver and ok:
            deliveries = self.notifier.notify(
                self.alert(briefing, dataset, session["id"], report, payload)
            )
        return {
            "briefing": self.get(briefing_id),
            "session_id": session["id"],
            "message": message,
            "deliveries": deliveries,
        }

    async def run_due(self) -> list[dict[str, Any]]:
        """Every enabled briefing whose cadence has elapsed. Never raises."""
        results: list[dict[str, Any]] = []
        for briefing in self.db.list_briefings():
            if not briefing["enabled"] or not _is_due(briefing):
                continue
            try:
                results.append(await self.run(briefing["id"]))
            except Exception:  # noqa: BLE001 — one bad briefing must not stop the rest
                logger.warning("Scheduled briefing %s failed", briefing["id"], exc_info=True)
        return results

    def alert(self, briefing: dict[str, Any], dataset: dict[str, Any], session_id: str,
              report: dict[str, Any], payload: dict[str, Any]) -> Alert:
        verification = payload.get("verification") or {}
        kpis = ((payload.get("execution") or {}).get("kpis") or [])[:4]
        facts: list[tuple[str, str]] = [
            (k.get("label") or "KPI", str(k.get("formatted") or k.get("value"))) for k in kpis
        ]
        facts.append(("Dataset", f"{dataset.get('name', 'dataset')} v{dataset.get('version', 1)}"))
        if verification.get("score") is not None:
            facts.append(("Verification", f"{verification['score']}/100 — {verification.get('confidence', '')}"))
        base = (self.settings.public_base_url or "").rstrip("/")
        return Alert(
            event="briefing",
            title=briefing["title"],
            summary=report.get("headline") or "The scheduled analysis completed.",
            severity="info",
            facts=facts,
            link=f"{base}?session={session_id}" if base else None,
            link_label="Open the full analysis",
        )

    # ------------------------------------------------------------------ helpers
    def _decorate(self, briefing: dict[str, Any]) -> dict[str, Any]:
        return {**briefing, "next_run_at": _next_run(briefing), "due": _is_due(briefing)}


def _is_due(briefing: dict[str, Any]) -> bool:
    if not briefing.get("enabled"):
        return False
    next_run = _next_run(briefing)
    return next_run is None or next_run <= _now()


def _next_run(briefing: dict[str, Any]) -> str | None:
    """None means "never run, so it is due now"."""
    last = briefing.get("last_run_at")
    if not last:
        return None
    try:
        stamp = datetime.fromisoformat(last)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    hours = int(briefing.get("schedule_hours") or DEFAULT_HOURS)
    return (stamp + timedelta(hours=hours)).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidInputError("A briefing needs a title.")
    return text[:120]


def _question(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidInputError("A briefing needs a question to ask.")
    if len(text) > MAX_QUESTION_CHARS:
        raise InvalidInputError(f"The question is limited to {MAX_QUESTION_CHARS:,} characters.")
    return text


def _hours(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_HOURS
    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("The cadence must be a whole number of hours.") from exc
    if not MIN_HOURS <= hours <= MAX_HOURS:
        raise InvalidInputError(
            f"The cadence must be between {MIN_HOURS} and {MAX_HOURS} hours."
        )
    return hours


__all__ = ["BriefingService", "DEFAULT_HOURS", "MAX_BRIEFINGS"]
