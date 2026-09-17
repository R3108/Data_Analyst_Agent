"""LangGraph state for one analyst turn."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # inputs
    question: str
    dataset_name: str
    data_path: str
    dataset_context: str
    semantic_context: str
    profile: dict[str, Any]
    cleaning: dict[str, Any]
    semantics: dict[str, Any] | None
    history: list[dict[str, Any]]
    # Prior answers to a similar question — continuity context, not a source of figures.
    recall_context: str
    # working memory
    plan: dict[str, Any]
    code: str
    approach: str
    attempts: int
    attempt_log: list[dict[str, Any]]
    execution: dict[str, Any]
    usage: list[dict[str, Any]]
    # outputs
    report: dict[str, Any]
    report_error: dict[str, Any] | None
    verification: dict[str, Any]
