"""Runs the analyst graph for a chat session, streams progress and persists history."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.agent.graph import build_graph
from app.agent.llm import StructuredLLM
from app.agent.pricing import summarize_usage
from app.core.config import Settings
from app.core.errors import AppError, BudgetExceededError, InvalidInputError, NotFoundError
from app.db import Database
from app.sandbox.runner import SandboxRunner
from app.services import memory
from app.services.datasets import DatasetService
from app.services.profiling import dataset_context
from app.services.semantics import semantic_context
from app.services.usage import monthly_budget_status

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TITLE = "New analysis"
MAX_QUESTION_CHARS = 4000


@dataclass
class PreparedTurn:
    session: dict[str, Any]
    user_message: dict[str, Any]
    state: dict[str, Any]
    # Prior analyses close to this question, shown to the reader above the answer.
    recall: dict[str, Any] | None = None


class AnalystService:
    def __init__(self, settings: Settings, db: Database, datasets: DatasetService,
                 llm: StructuredLLM, runner: SandboxRunner) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets
        self.graph = build_graph(llm, runner, settings.max_repair_attempts)

    # ------------------------------------------------------------------------------
    def prepare(self, session_id: str, question: str) -> PreparedTurn:
        question = question.strip()
        if not question:
            raise InvalidInputError("Please enter a question.")
        if len(question) > MAX_QUESTION_CHARS:
            raise InvalidInputError(f"Questions are limited to {MAX_QUESTION_CHARS:,} characters.")

        session = self.db.get_session(session_id)
        if session is None:
            raise NotFoundError(f"Session '{session_id}' was not found.")
        dataset = self.datasets.get(session["dataset_id"])

        budget = monthly_budget_status(self.db, self.settings.ai_monthly_budget_usd)
        if budget["exhausted"]:
            raise BudgetExceededError(
                f"Your ${budget['limit_usd']:.2f} monthly AI budget has been reached. "
                f"It resets on {budget['reset_at']}; increase AI_MONTHLY_BUDGET_USD to continue sooner.",
                details={"budget": budget},
            )

        history = self._history(session_id)
        recalled = self._recall(dataset["id"], question)
        user_message = self.db.add_message(session_id, "user", question)
        if session["title"] == DEFAULT_SESSION_TITLE:
            title = question if len(question) <= 60 else question[:57].rstrip() + "…"
            self.db.update_session(session_id, title=title)
            session["title"] = title

        state = {
            "question": question,
            "dataset_name": dataset["name"],
            "data_path": str(self.datasets.data_path(dataset["id"])),
            "dataset_context": dataset_context(dataset["name"], dataset["profile"], dataset["cleaning"]),
            "semantic_context": semantic_context(dataset.get("semantics")),
            "profile": dataset["profile"],
            "cleaning": dataset["cleaning"],
            "semantics": dataset.get("semantics"),
            "history": history,
            "recall_context": memory.recall_block(recalled),
        }
        return PreparedTurn(session=session, user_message=user_message, state=state,
                            recall=memory.summarize(recalled))

    def _recall(self, dataset_id: str, question: str) -> list[dict[str, Any]]:
        """Prior answers to a similar question on this dataset. Never blocks a turn."""
        if not self.settings.recall_enabled:
            return []
        try:
            return memory.recall(self.db.recent_analyses(dataset_id), question,
                                 limit=self.settings.recall_limit)
        except Exception:  # noqa: BLE001 — memory is a convenience, not a prerequisite
            logger.warning("Analysis recall failed", exc_info=True)
            return []

    async def run(self, turn: PreparedTurn) -> AsyncIterator[dict[str, Any]]:
        session_id = turn.session["id"]
        yield {"event": "session", "data": {"id": session_id, "title": turn.session["title"]}}
        yield {"event": "user_message", "data": turn.user_message}
        if turn.recall:
            # Sent before the work starts: a reader who recognises the earlier answer can
            # stop the run instead of paying for it twice.
            yield {"event": "recall", "data": turn.recall}

        final: dict[str, Any] = dict(turn.state)
        steps: dict[str, dict[str, Any]] = {}
        try:
            async for mode, chunk in self.graph.astream(turn.state, stream_mode=["custom", "updates"]):
                if mode == "custom" and isinstance(chunk, dict) and chunk.get("type") == "step":
                    step = {k: v for k, v in chunk.items() if k != "type"}
                    steps[step["id"]] = {**steps.get(step["id"], {}), **step}
                    yield {"event": "step", "data": steps[step["id"]]}
                elif mode == "updates":
                    for update in chunk.values():
                        if update:
                            final.update(update)
        except asyncio.CancelledError:
            self.db.add_message(session_id, "assistant", "Analysis was cancelled.",
                                payload={"steps": list(steps.values())}, status="cancelled")
            raise
        except AppError as exc:
            yield self._fail(session_id, exc.to_dict(), steps)
            return
        except Exception:  # noqa: BLE001 — never leak internals to the client
            logger.exception("Analyst graph crashed")
            yield self._fail(session_id, {"code": "internal_error",
                                          "message": "Something went wrong while analysing. Please try again."},
                             steps)
            return

        payload = self._payload(final, list(steps.values()))
        if turn.recall:
            payload["recall"] = turn.recall
        report = payload.get("report") or {}
        content = report.get("answer_markdown") or report.get("headline") or ""
        status = "complete" if payload.get("execution", {}).get("ok", True) else "failed"
        message = self.db.add_message(session_id, "assistant", content, payload=payload, status=status)
        yield {"event": "assistant_message", "data": message}

    async def answer(self, session_id: str, question: str) -> dict[str, Any]:
        """Run one turn to completion and return the stored assistant message.

        The streaming path is the product's; this is for callers that need the result
        rather than the progress — an investigation's sub-analyses and a scheduled
        briefing both run the identical graph through here.
        """
        turn = self.prepare(session_id, question)
        message: dict[str, Any] | None = None
        async for event in self.run(turn):
            if event["event"] == "assistant_message":
                message = event["data"]
            elif event["event"] == "error":
                message = event["data"].get("message_record")
        if message is None:  # pragma: no cover — run() always persists a message
            raise AppError("The analysis produced no result.", code="internal_error")
        return message

    # ------------------------------------------------------------------------------
    def _fail(self, session_id: str, error: dict[str, Any], steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
        for step in steps.values():
            if step.get("status") == "running":
                step["status"] = "error"
        message = self.db.add_message(
            session_id, "assistant", error["message"],
            payload={"error": error, "steps": list(steps.values())}, status="error",
        )
        return {"event": "error", "data": {**error, "message_record": message}}

    @staticmethod
    def _payload(state: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        plan = state.get("plan") or {}
        payload: dict[str, Any] = {
            "intent": plan.get("intent"),
            "plan": plan,
            "report": state.get("report"),
            "steps": steps,
            "usage": summarize_usage(state.get("usage", [])),
        }
        if state.get("report_error"):
            payload["report_error"] = state["report_error"]
        if state.get("verification"):
            payload["verification"] = state["verification"]
        if "execution" in state:
            execution = state["execution"]
            payload.update(
                code=state.get("code"),
                approach=state.get("approach"),
                attempts=state.get("attempts", 1),
                attempt_log=state.get("attempt_log", []),
                execution={k: execution.get(k) for k in (
                    "ok", "stdout", "error", "error_type", "kpis", "charts", "tables", "warnings",
                    "duration_ms", "timed_out", "memory_exceeded",
                )},
            )
        return payload

    def _history(self, session_id: str) -> list[dict[str, Any]]:
        """Summarise recent turns so follow-up questions can refer to earlier results."""
        limit = self.settings.history_turns * 2
        if limit == 0:
            return []
        messages: list[dict[str, Any]] = []
        for record in self.db.list_messages(session_id, limit=limit):
            if record["role"] == "user":
                messages.append({"role": "user", "content": record["content"]})
                continue
            payload = record.get("payload") or {}
            report = payload.get("report") or {}
            lines = []
            if report.get("headline"):
                lines.append(report["headline"])
            elif record["content"]:
                lines.append(record["content"][:600])
            if payload.get("approach"):
                lines.append(f"Approach: {payload['approach']}")
            execution = payload.get("execution") or {}
            kpis = execution.get("kpis") or []
            if kpis:
                lines.append("KPIs: " + "; ".join(f"{k['label']}={k['value']}" for k in kpis[:8]))
            titles = [c["title"] for c in execution.get("charts") or []] + \
                     [t["title"] for t in execution.get("tables") or []]
            if titles:
                lines.append("Outputs: " + ", ".join(titles[:8]))
            if record["status"] in ("error", "failed"):
                lines.append("(This analysis did not complete successfully.)")
            messages.append({"role": "assistant", "content": "\n".join(lines) or "(no answer)"})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        return messages
