"""Graph nodes: plan → generate code → execute (→ repair loop) → report."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

from app.agent.llm import LLMResult, StructuredLLM
from app.agent.prompts import (
    CODE_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
    REPAIR_TEMPLATE,
    REPORT_INSTRUCTIONS,
    SYSTEM_BASE,
)
from app.agent.schemas import AnalysisPlan, AnalysisReport, GeneratedCode
from app.agent.state import AgentState
from app.core.errors import LLMError
from app.sandbox.runner import SandboxRunner
from app.services.verification import audit_execution, data_check_block, verify_analysis

logger = logging.getLogger(__name__)

FENCE_RE = re.compile(r"^\s*```(?:python|py)?\s*\n(.*?)\n\s*```\s*$", re.S)
MAX_STDOUT_FOR_REPORT = 6000
MAX_TABLE_ROWS_FOR_REPORT = 25


class AnalystNodes:
    def __init__(self, llm: StructuredLLM, runner: SandboxRunner, max_repair_attempts: int) -> None:
        self.llm = llm
        self.runner = runner
        self.max_attempts = 1 + max_repair_attempts

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _emit(**event: Any) -> None:
        get_stream_writer()({"type": "step", **event})

    @staticmethod
    def _system(instructions: str, state: AgentState) -> list[dict[str, Any]]:
        # Blocks are ordered most-stable first so the provider's prompt cache keeps hitting.
        blocks = [
            {"type": "text", "text": f"{SYSTEM_BASE}\n\n{instructions}"},
            {"type": "text", "text": state["dataset_context"]},
        ]
        if state.get("semantic_context"):
            blocks.append({"type": "text", "text": state["semantic_context"]})
        # Last, because it is the only block that changes from turn to turn: everything
        # above it stays byte-identical and keeps hitting the provider's prompt cache.
        if state.get("recall_context"):
            blocks.append({"type": "text", "text": state["recall_context"]})
        return blocks

    @staticmethod
    def _with_usage(state: AgentState, result: LLMResult) -> list[dict[str, Any]]:
        return [*state.get("usage", []), result.usage] if result.usage else list(state.get("usage", []))

    # ------------------------------------------------------------------ nodes
    def plan(self, state: AgentState) -> dict[str, Any]:
        self._emit(id="plan", status="running", label="Understanding the question")
        messages = [*state.get("history", []),
                    {"role": "user", "content": f"Latest user message:\n{state['question']}"}]
        result = self.llm.generate(
            system=self._system(PLAN_INSTRUCTIONS, state),
            messages=messages,
            schema=AnalysisPlan,
            purpose="plan",
        )
        plan = result.output
        detail = plan.restated_question if plan.intent == "analysis" else {
            "data_question": "Answering from the dataset profile",
            "clarify": "Needs a quick clarification",
            "out_of_scope": "Outside the scope of this dataset",
        }[plan.intent]
        self._emit(id="plan", status="done", label="Understanding the question", detail=detail,
                   items=plan.steps)
        return {"plan": plan.model_dump(), "attempts": 0, "attempt_log": [],
                "usage": self._with_usage(state, result)}

    def generate_code(self, state: AgentState) -> dict[str, Any]:
        attempt = state.get("attempts", 0) + 1
        step_id, label = ("code", "Writing analysis code") if attempt == 1 else (
            f"repair-{attempt}", "Fixing the analysis code")
        self._emit(id=step_id, status="running", label=label, attempt=attempt)

        plan = state["plan"]
        content = (
            f"QUESTION: {plan['restated_question']}\n\n"
            f"PLAN:\n{json.dumps({k: plan[k] for k in ('steps', 'kpis', 'charts', 'assumptions')}, indent=2)}"
        )
        if attempt > 1:
            last = state["attempt_log"][-1]
            content += "\n\n" + REPAIR_TEMPLATE.format(attempt=attempt - 1, code=state["code"], error=last["error"])

        result = self.llm.generate(
            system=self._system(CODE_INSTRUCTIONS, state),
            messages=[{"role": "user", "content": content}],
            schema=GeneratedCode,
            purpose="code" if attempt == 1 else "repair",
        )
        generated = result.output
        self._emit(id=step_id, status="done", label=label, detail=generated.approach, attempt=attempt)
        return {"code": strip_fences(generated.code), "approach": generated.approach, "attempts": attempt,
                "usage": self._with_usage(state, result)}

    def execute(self, state: AgentState) -> dict[str, Any]:
        attempt = state["attempts"]
        step_id = f"execute-{attempt}"
        label = "Running in the secure sandbox"
        self._emit(id=step_id, status="running", label=label, attempt=attempt)

        result = self.runner.run(state["code"], Path(state["data_path"]))
        log = [*state.get("attempt_log", []), {
            "attempt": attempt, "ok": result.ok, "error": result.error, "error_type": result.error_type,
            "duration_ms": result.duration_ms,
        }]
        if result.ok:
            detail = (f"{len(result.kpis)} KPIs · {len(result.charts)} charts · "
                      f"{len(result.tables)} tables in {result.duration_ms / 1000:.1f}s")
            self._emit(id=step_id, status="done", label=label, detail=detail, attempt=attempt)
        else:
            first_line = (result.error or "Unknown error").splitlines()[0]
            will_retry = attempt < self.max_attempts
            self._emit(id=step_id, status="error", label=label, attempt=attempt,
                       detail=first_line + (" — retrying" if will_retry else ""))
        return {"execution": result.to_dict(), "attempt_log": log}

    def report(self, state: AgentState) -> dict[str, Any]:
        execution = state["execution"]
        if not execution["ok"]:
            self._emit(id="report", status="done", label="Summarising what went wrong")
            return {"report": failure_report(state)}

        self._emit(id="report", status="running", label="Writing insights")
        try:
            result = self.llm.generate(
                system=self._system(REPORT_INSTRUCTIONS, state),
                messages=[{"role": "user", "content": build_report_input(state)}],
                schema=AnalysisReport,
                purpose="report",
            )
        except LLMError as exc:
            # The numbers are already computed — degrade gracefully rather than lose them.
            logger.warning("Report generation failed: %s", exc.message)
            self._emit(id="report", status="error", label="Writing insights", detail=exc.message)
            return {
                "report": {
                    "headline": "The analysis ran successfully, but the written summary could not be generated.",
                    "answer_markdown": f"The computed results are shown below. ({exc.message})",
                    "insights": [], "recommendations": [], "caveats": [], "follow_up_questions": [],
                },
                "report_error": exc.to_dict(),
            }
        self._emit(id="report", status="done", label="Writing insights", detail=result.output.headline)
        return {"report": result.output.model_dump(), "usage": self._with_usage(state, result)}

    def verify(self, state: AgentState) -> dict[str, Any]:
        """Audit the finished answer against what was actually computed. No model call."""
        execution = state.get("execution") or {}
        if not execution.get("ok"):
            return {}

        label = "Verifying the answer"
        self._emit(id="verify", status="running", label=label)
        verification = verify_analysis(
            plan=state.get("plan"),
            code=state.get("code"),
            execution=execution,
            report=state.get("report"),
            profile=state.get("profile"),
            cleaning=state.get("cleaning"),
            semantics=state.get("semantics"),
            attempt_log=state.get("attempt_log"),
            question=state.get("question", ""),
        )
        findings = verification["findings"]
        status = "error" if any(f["severity"] == "high" for f in findings) else "done"
        self._emit(id="verify", status=status, label=label, detail=verification["summary"],
                   items=[f"{f['title']} ({f['severity']})" for f in findings])
        return {"verification": verification}

    def respond_direct(self, state: AgentState) -> dict[str, Any]:
        plan = state["plan"]
        self._emit(id="respond", status="done", label="Answering")
        return {"report": {
            "headline": "",
            "answer_markdown": plan.get("direct_answer") or plan["restated_question"],
            "insights": [], "recommendations": [], "caveats": [], "follow_up_questions": [],
        }}

    # ------------------------------------------------------------------ routing
    @staticmethod
    def route_after_plan(state: AgentState) -> str:
        return "analysis" if state["plan"]["intent"] == "analysis" else "direct"

    def route_after_execute(self, state: AgentState) -> str:
        if state["execution"]["ok"] or state["attempts"] >= self.max_attempts:
            return "report"
        return "retry"


# ---------------------------------------------------------------------- pure helpers


def strip_fences(code: str) -> str:
    match = FENCE_RE.match(code)
    return (match.group(1) if match else code).strip() + "\n"


def build_report_input(state: AgentState) -> str:
    plan, execution = state["plan"], state["execution"]
    parts = [f"QUESTION: {plan['restated_question']}", f"ORIGINAL WORDING: {state['question']}"]
    if plan.get("assumptions"):
        parts.append("ASSUMPTIONS:\n" + "\n".join(f"- {a}" for a in plan["assumptions"]))
    parts.append(f"APPROACH: {state.get('approach', '')}")

    if execution["kpis"]:
        lines = []
        for k in execution["kpis"]:
            line = f"- {k['label']}: {k['value']} (format={k['format']})"
            if k.get("delta") is not None:
                line += f", delta={k['delta']} {k.get('delta_label') or ''}".rstrip()
            lines.append(line)
        parts.append("KPIS:\n" + "\n".join(lines))

    for table in execution["tables"]:
        header = " | ".join(c["name"] for c in table["columns"])
        rows = "\n".join(" | ".join("" if v is None else str(v) for v in row)
                         for row in table["rows"][:MAX_TABLE_ROWS_FOR_REPORT])
        shown = min(len(table["rows"]), MAX_TABLE_ROWS_FOR_REPORT)
        parts.append(f"TABLE — {table['title']} (showing {shown} of {table['total_rows']} rows)\n{header}\n{rows}")

    for chart in execution["charts"]:
        parts.append(f"CHART — {chart['title']}: {chart.get('caption') or ''}\n"
                     f"data digest: {json.dumps(chart.get('digest', {}), default=str)[:3000]}")

    if execution.get("stdout", "").strip():
        parts.append("PRINTED OUTPUT:\n" + execution["stdout"][:MAX_STDOUT_FOR_REPORT])
    if execution.get("warnings"):
        parts.append("WARNINGS:\n" + "\n".join(execution["warnings"]))
    if len(state.get("attempt_log", [])) > 1:
        parts.append(f"NOTE: the code needed {len(state['attempt_log']) - 1} automatic fix(es) before succeeding.")

    # Deterministic checks on the computation, so the write-up can own the caveats.
    checks = data_check_block(audit_execution(
        plan=plan, code=state.get("code"), execution=execution, report=None,
        profile=state.get("profile"), cleaning=state.get("cleaning"),
        semantics=state.get("semantics"), attempt_log=state.get("attempt_log"),
        question=state.get("question", ""),
    ))
    if checks:
        parts.append(checks)
    return "\n\n".join(parts)


def failure_report(state: AgentState) -> dict[str, Any]:
    execution = state["execution"]
    error = (execution.get("error") or "Unknown error").splitlines()[0]
    attempts = state.get("attempts", 1)
    if execution.get("error_type") == "PolicyViolation":
        reason = "the generated code tried to use an operation the secure sandbox does not allow"
    elif execution.get("timed_out"):
        reason = "the computation took longer than the sandbox time limit"
    elif execution.get("memory_exceeded"):
        reason = "the computation needed more memory than the sandbox allows"
    else:
        reason = "the generated code kept failing"
    return {
        "headline": "I couldn't complete this analysis.",
        "answer_markdown": (
            f"After {attempts} attempt{'s' if attempts != 1 else ''}, {reason}.\n\n"
            f"**Last error:** `{error}`\n\n"
            "Try rephrasing the question more specifically — for example, name the exact columns, "
            "the time period or the metric you care about."
        ),
        "insights": [],
        "recommendations": [],
        "caveats": [],
        "follow_up_questions": [],
    }
