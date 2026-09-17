from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm import LLMResult  # noqa: E402
from app.core.config import Settings  # noqa: E402


class ScriptedLLM:
    """Deterministic stand-in for the model: returns queued responses per purpose."""

    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def generate(self, *, system, messages, schema, purpose):
        self.calls.append({"purpose": purpose, "system": system, "messages": messages})
        queue = self.responses.get(purpose)
        if not queue:
            raise AssertionError(f"Unexpected LLM call for purpose '{purpose}'")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        usage = {"purpose": purpose, "model": "gpt-5.6-luna", "input_tokens": 10_000,
                 "output_tokens": 1_000, "cache_read_tokens": 0, "cache_write_tokens": 0}
        return LLMResult(output=schema.model_validate(item), usage=usage)

    @property
    def purposes(self) -> list[str]:
        return [c["purpose"] for c in self.calls]


def make_plan(**overrides: Any) -> dict[str, Any]:
    plan = {
        "intent": "analysis",
        "restated_question": "What is total revenue by region?",
        "steps": ["Sum revenue by region", "Rank regions"],
        "kpis": [{"name": "Total revenue", "definition": "sum of revenue"}],
        "charts": ["Bar chart of revenue by region"],
        "assumptions": [],
        "direct_answer": None,
    }
    plan.update(overrides)
    return plan


GOOD_CODE = """
by_region = df.groupby("region", as_index=False)["revenue"].sum().sort_values("revenue", ascending=False)
kpi("Total revenue", df["revenue"].sum(), format="currency")
table(by_region, title="Revenue by region")
chart(px.bar(by_region, x="region", y="revenue"), title="Revenue by region", caption="North leads")
print("rows analysed:", len(df))
"""

REPORT = {
    "headline": "North generates the most revenue.",
    "answer_markdown": "**North** leads with the highest revenue.",
    "insights": [{"title": "North leads", "detail": "North has the most revenue.", "sentiment": "positive"}],
    "recommendations": ["Invest in North."],
    "caveats": [],
    "follow_up_questions": ["How has North trended?", "What drives North?", "Which products sell in North?"],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path / "data",
        openai_api_key="test-key",
        sandbox_timeout_s=60,
        sandbox_memory_mb=2048,
        max_repair_attempts=2,
    )


@pytest.fixture
def tiny_parquet(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.parquet"
    pd.DataFrame({
        "region": ["North", "South", "North", "East"],
        "revenue": [100.0, 50.0, 25.0, 10.0],
        "order_date": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]),
    }).to_parquet(path)
    return path


@pytest.fixture
def tiny_csv_bytes() -> bytes:
    return (
        "Region,Revenue,Order Date\n"
        "North,\"$1,000\",2024-01-01\n"
        "South,500,2024-02-01\n"
        " north ,250,2024-03-01\n"
        "East,N/A,2024-04-01\n"
    ).encode("utf-8")
