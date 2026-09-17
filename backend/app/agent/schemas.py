"""Structured outputs the agent requests from the model (validated with Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KPISpec(BaseModel):
    name: str = Field(description="Short KPI name, e.g. 'Revenue growth YoY'")
    definition: str = Field(description="How it is computed from the columns")


class AnalysisPlan(BaseModel):
    intent: Literal["analysis", "data_question", "clarify", "out_of_scope"]
    restated_question: str = Field(description="Precise, self-contained analytical question")
    steps: list[str] = Field(description="Concrete computational steps naming exact columns")
    kpis: list[KPISpec]
    charts: list[str] = Field(description="Chart specs: form, x, y, grouping and why")
    assumptions: list[str]
    direct_answer: str | None = Field(
        description="Complete markdown answer for data_question / clarify / out_of_scope; null for analysis"
    )


class GeneratedCode(BaseModel):
    approach: str = Field(description="One sentence describing the approach")
    code: str = Field(description="Complete Python program, no markdown fences")


class Insight(BaseModel):
    title: str
    detail: str
    sentiment: Literal["positive", "negative", "neutral"]


class AnalysisReport(BaseModel):
    headline: str
    answer_markdown: str
    insights: list[Insight]
    recommendations: list[str]
    caveats: list[str]
    follow_up_questions: list[str]


class ResearchStep(BaseModel):
    question: str = Field(description="A self-contained analytical question about this dataset")
    why: str = Field(description="What decision this step informs, in one sentence")


class ResearchPlan(BaseModel):
    """The scoping pass of an investigation: several questions, ordered so each builds."""

    objective: str = Field(description="The restated objective of the investigation")
    steps: list[ResearchStep] = Field(description="Ordered sub-questions to answer")
    out_of_scope: list[str] = Field(
        description="Things this dataset cannot answer, so the brief does not promise them"
    )


class ResearchFinding(BaseModel):
    title: str
    detail: str = Field(description="Two to three sentences citing figures from the sub-analyses")
    sentiment: Literal["positive", "negative", "neutral"]
    confidence: Literal["high", "medium", "low"] = Field(
        description="How well the computed evidence actually supports this finding"
    )


class ResearchBrief(BaseModel):
    """The synthesis pass: one brief over every sub-analysis that was actually executed."""

    title: str
    headline: str = Field(description="One sentence with the single most decision-relevant number")
    executive_summary: str = Field(description="Markdown, 2-4 short paragraphs or tight bullets")
    findings: list[ResearchFinding]
    recommendations: list[str]
    caveats: list[str]
    open_questions: list[str] = Field(description="What to investigate next, or with what data")
