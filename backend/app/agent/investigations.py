"""Deep research: several analyses, executed in order, synthesised into one brief.

A single question gets a single answer, and a single answer is rarely the thing anyone
needed. "Why is retention falling" is four analyses — what the level and trend actually
are, which cohorts moved, whether that movement is distinguishable from noise, and what
it would take to reverse it — plus the work of reconciling them.

This runs that loop:

    scope (one model call) → analyse ×N (the ordinary agent graph, per question)
                           → synthesise (one model call)

The important design choice is that the middle is *not* special. Each sub-question goes
through the same `plan → code → execute → repair → report → verify` graph and is persisted
as an ordinary assistant message, so every sub-analysis keeps its code, its charts, its
verification verdict, and every existing capability — pin to a board, export a notebook,
watch a KPI, share a link — works on it unchanged. The brief on top is also stored in the
standard report shape, so PDF, PowerPoint and the share page render it with no special case.

The synthesis is told which steps failed or were flagged by the verifier, and instructed
not to launder them into confident prose. An investigation that quietly builds on a broken
step is worse than one that admits it.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from app.agent.llm import StructuredLLM
from app.agent.prompts import (
    SCOPE_INSTRUCTIONS,
    SYNTHESIS_INSTRUCTIONS,
    SYSTEM_BASE,
)
from app.agent.pricing import merge_usage, summarize_usage
from app.agent.schemas import ResearchBrief, ResearchPlan
from app.agent.service import AnalystService
from app.core.config import Settings
from app.core.errors import AppError, InvalidInputError, LLMError, NotFoundError
from app.db import Database
from app.services.datasets import DatasetService
from app.services.profiling import dataset_context
from app.services.semantics import semantic_context

logger = logging.getLogger(__name__)

MIN_STEPS = 3
MAX_OBJECTIVE_CHARS = 2000
MAX_TABLE_ROWS_FOR_SYNTHESIS = 8
MAX_KPIS_FOR_SYNTHESIS = 8

DEFAULT_OBJECTIVE = (
    "Give me a complete picture of this dataset: what the headline numbers are, how they "
    "are trending, what is driving the movement, and what I should do about it."
)


class InvestigationService:
    """Orchestrates a multi-analysis investigation and streams its progress."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        datasets: DatasetService,
        analyst: AnalystService,
        llm: StructuredLLM,
    ) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets
        self.analyst = analyst
        self.llm = llm

    # ------------------------------------------------------------------ public
    async def run(self, session_id: str, objective: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Scope, analyse and synthesise. Yields the same SSE events as an ordinary chat."""
        session = self.db.get_session(session_id)
        if session is None:
            raise NotFoundError(f"Session '{session_id}' was not found.")
        objective = (objective or "").strip() or DEFAULT_OBJECTIVE
        if len(objective) > MAX_OBJECTIVE_CHARS:
            raise InvalidInputError(
                f"An objective is limited to {MAX_OBJECTIVE_CHARS:,} characters."
            )

        dataset = self.datasets.get(session["dataset_id"])
        # This service's own model calls, and the already-summarised cost of each
        # sub-analysis, kept apart because they are aggregated differently.
        own_usage: list[dict[str, Any]] = []
        child_usage: list[dict[str, Any]] = []
        yield {"event": "session", "data": {"id": session_id, "title": session["title"]}}

        # The objective is persisted as the user's turn, so the thread reads normally.
        user_message = self.db.add_message(session_id, "user", objective)
        yield {"event": "user_message", "data": user_message}

        try:
            plan, plan_usage = self._scope(dataset, objective)
        except AppError as exc:
            yield self._fail(session_id, exc)
            return
        own_usage += plan_usage
        steps = plan.steps[: self.settings.investigation_max_steps]
        if not steps:
            yield self._fail(session_id, InvalidInputError(
                "The investigation could not be scoped into questions this dataset can answer."
            ))
            return

        yield {"event": "step", "data": {
            "id": "scope", "status": "done", "label": "Scoping the investigation",
            "detail": plan.objective, "items": [s.question for s in steps],
        }}

        results: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            step_id = f"investigate-{index}"
            label = f"Analysis {index} of {len(steps)}"
            yield {"event": "step", "data": {
                "id": step_id, "status": "running", "label": label, "detail": step.question,
            }}
            message = await self._answer(session_id, step.question)
            outcome = _outcome(message)
            results.append({"question": step.question, "why": step.why, **outcome})
            child_usage.append(((message.get("payload") or {}).get("usage")) or {})
            # Each sub-analysis lands in the thread as it completes: a reader watching can
            # already inspect its code and charts while the next one runs.
            yield {"event": "assistant_message", "data": message}
            yield {"event": "step", "data": {
                "id": step_id, "status": "done" if outcome["ok"] else "error", "label": label,
                "detail": outcome["headline"] or outcome["error"] or step.question,
            }}

        yield {"event": "step", "data": {
            "id": "synthesis", "status": "running", "label": "Writing the brief",
        }}
        try:
            brief, synthesis_usage = self._synthesise(dataset, plan, results)
        except AppError as exc:
            # The sub-analyses are already persisted and visible; losing the brief is a
            # degraded result, not a failed investigation.
            logger.warning("Investigation synthesis failed: %s", exc.message)
            yield {"event": "step", "data": {
                "id": "synthesis", "status": "error", "label": "Writing the brief",
                "detail": exc.message,
            }}
            brief, synthesis_usage = None, []
        own_usage += synthesis_usage

        usage = merge_usage([summarize_usage(own_usage), *child_usage])
        payload = self._payload(plan, results, brief, usage, degraded=brief is None)
        message = self.db.add_message(
            session_id, "assistant", payload["report"]["answer_markdown"], payload=payload,
            status="complete" if brief is not None else "failed",
        )
        if brief is not None:
            yield {"event": "step", "data": {
                "id": "synthesis", "status": "done", "label": "Writing the brief",
                "detail": brief.headline,
            }}
        yield {"event": "assistant_message", "data": message}

    # ------------------------------------------------------------------ phases
    def _scope(self, dataset: dict[str, Any], objective: str) -> tuple[ResearchPlan, list[dict[str, Any]]]:
        instructions = SCOPE_INSTRUCTIONS.format(
            min_steps=MIN_STEPS, max_steps=self.settings.investigation_max_steps,
        )
        result = self.llm.generate(
            system=self._system(instructions, dataset),
            messages=[{"role": "user", "content": f"OBJECTIVE:\n{objective}"}],
            schema=ResearchPlan,
            purpose="scope",
        )
        return result.output, [result.usage] if result.usage else []

    async def _answer(self, session_id: str, question: str) -> dict[str, Any]:
        """Run one sub-question through the ordinary graph and return its stored message."""
        return await self.analyst.answer(session_id, question)

    def _synthesise(
        self, dataset: dict[str, Any], plan: ResearchPlan, results: list[dict[str, Any]],
    ) -> tuple[ResearchBrief, list[dict[str, Any]]]:
        result = self.llm.generate(
            system=self._system(SYNTHESIS_INSTRUCTIONS, dataset),
            messages=[{"role": "user", "content": build_synthesis_input(plan, results)}],
            schema=ResearchBrief,
            purpose="synthesis",
        )
        return result.output, [result.usage] if result.usage else []

    def _system(self, instructions: str, dataset: dict[str, Any]) -> list[dict[str, Any]]:
        # Same block order as the analyst nodes, so the cached prefix keeps hitting.
        blocks = [
            {"type": "text", "text": f"{SYSTEM_BASE}\n\n{instructions}"},
            {"type": "text", "text": dataset_context(
                dataset["name"], dataset["profile"], dataset["cleaning"])},
        ]
        semantics = semantic_context(dataset.get("semantics"))
        if semantics:
            blocks.append({"type": "text", "text": semantics})
        return blocks

    # ------------------------------------------------------------------ persistence
    def _fail(self, session_id: str, exc: AppError) -> dict[str, Any]:
        error = exc.to_dict()
        message = self.db.add_message(
            session_id, "assistant", error["message"], payload={"error": error}, status="error",
        )
        return {"event": "error", "data": {**error, "message_record": message}}

    @staticmethod
    def _payload(
        plan: ResearchPlan,
        results: list[dict[str, Any]],
        brief: ResearchBrief | None,
        usage: dict[str, Any],
        degraded: bool,
    ) -> dict[str, Any]:
        """Stored in the ordinary report shape, so every exporter already understands it."""
        succeeded = [r for r in results if r["ok"]]
        investigation = {
            "objective": plan.objective,
            "out_of_scope": plan.out_of_scope,
            "steps": [
                {
                    "question": r["question"],
                    "why": r["why"],
                    "ok": r["ok"],
                    "headline": r["headline"],
                    "message_id": r["message_id"],
                    "verification_score": r["verification_score"],
                    "error": r["error"],
                }
                for r in results
            ],
            "completed": len(succeeded),
            "total": len(results),
            "open_questions": brief.open_questions if brief else [],
        }
        if brief is None:
            report = {
                "headline": "The investigation ran, but the brief could not be written.",
                "answer_markdown": (
                    f"{len(succeeded)} of {len(results)} analyses completed and are shown above "
                    "with their code, charts and verification. The synthesis step failed, so "
                    "there is no combined write-up."
                ),
                "insights": [], "recommendations": [], "caveats": [], "follow_up_questions": [],
            }
        else:
            report = {
                "headline": brief.headline,
                "answer_markdown": brief.executive_summary,
                "insights": [
                    {"title": f.title, "detail": f.detail, "sentiment": f.sentiment,
                     "confidence": f.confidence}
                    for f in brief.findings
                ],
                "recommendations": brief.recommendations,
                "caveats": brief.caveats + [
                    f"Step {i + 1} ({r['question']}) did not complete, so nothing here rests on it."
                    for i, r in enumerate(results) if not r["ok"]
                ],
                "follow_up_questions": brief.open_questions[:3],
            }
        return {
            "intent": "investigation",
            "investigation": investigation,
            "title": brief.title if brief else plan.objective,
            "report": report,
            "degraded": degraded,
            "steps": [],
            "usage": usage,
        }


# --------------------------------------------------------------------------- helpers


def _outcome(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload") or {}
    report = payload.get("report") or {}
    execution = payload.get("execution") or {}
    verification = payload.get("verification") or {}
    ok = bool(execution.get("ok")) and message.get("status") == "complete"
    return {
        "message_id": message["id"],
        "ok": ok,
        "headline": report.get("headline") or "",
        "answer": report.get("answer_markdown") or "",
        "kpis": (execution.get("kpis") or [])[:MAX_KPIS_FOR_SYNTHESIS],
        "tables": execution.get("tables") or [],
        "verification_score": verification.get("score"),
        "verification_findings": [
            f["title"] for f in (verification.get("findings") or [])
            if f.get("severity") in ("high", "medium")
        ],
        "error": (payload.get("error") or {}).get("message") or execution.get("error"),
    }


def build_synthesis_input(plan: ResearchPlan, results: list[dict[str, Any]]) -> str:
    parts = [f"OBJECTIVE: {plan.objective}"]
    if plan.out_of_scope:
        parts.append("KNOWN LIMITS OF THIS DATASET:\n"
                     + "\n".join(f"- {item}" for item in plan.out_of_scope))

    for index, result in enumerate(results, start=1):
        section = [f"### ANALYSIS {index}: {result['question']}", f"Purpose: {result['why']}"]
        if not result["ok"]:
            section.append(
                f"OUTCOME: FAILED — {result['error'] or 'the analysis did not complete'}. "
                "Do not use any figure from this step."
            )
            parts.append("\n".join(section))
            continue

        section.append(f"HEADLINE: {result['headline']}")
        if result["answer"]:
            section.append(f"WRITE-UP: {result['answer'][:1200]}")
        if result["kpis"]:
            section.append("KPIS:\n" + "\n".join(
                f"- {k['label']}: {k['value']} (format={k.get('format')})" for k in result["kpis"]
            ))
        for table in result["tables"][:2]:
            header = " | ".join(c["name"] for c in table["columns"])
            rows = "\n".join(
                " | ".join("" if v is None else str(v) for v in row)
                for row in table["rows"][:MAX_TABLE_ROWS_FOR_SYNTHESIS]
            )
            section.append(
                f"TABLE — {table['title']} "
                f"(showing {min(len(table['rows']), MAX_TABLE_ROWS_FOR_SYNTHESIS)} of "
                f"{table['total_rows']} rows)\n{header}\n{rows}"
            )
        if result["verification_score"] is not None:
            line = f"VERIFICATION: {result['verification_score']}/100"
            if result["verification_findings"]:
                line += " — flagged: " + "; ".join(result["verification_findings"][:4])
                line += ". Treat the flagged figures with caution or leave them out."
            section.append(line)
        parts.append("\n".join(section))

    parts.append(
        "Write the brief over these results only. Connect them: the value of a brief is the "
        "finding no single analysis above states on its own."
    )
    return "\n\n".join(parts)


__all__ = ["DEFAULT_OBJECTIVE", "InvestigationService", "build_synthesis_input"]
