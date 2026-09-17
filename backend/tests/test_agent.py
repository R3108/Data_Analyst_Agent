from __future__ import annotations

from app.agent.graph import build_graph
from app.core.errors import LLMError
from app.sandbox.runner import SandboxRunner
from tests.conftest import GOOD_CODE, REPORT, ScriptedLLM, make_plan


def _state(tiny_parquet):
    return {
        "question": "Revenue by region?",
        "dataset_name": "Tiny",
        "data_path": str(tiny_parquet),
        "dataset_context": "DATASET: Tiny",
        "history": [],
    }


def test_agent_repairs_failing_code(tiny_parquet):
    llm = ScriptedLLM({
        "plan": [make_plan()],
        "code": [{"approach": "Sum by region", "code": "x = df['Revenue'].sum()"}],
        "repair": [{"approach": "Fixed column name", "code": f"```python\n{GOOD_CODE}\n```"}],
        "report": [REPORT],
    })
    graph = build_graph(llm, SandboxRunner(timeout_s=60), max_repair_attempts=2)
    final = graph.invoke(_state(tiny_parquet))

    assert llm.purposes == ["plan", "code", "repair", "report"]
    assert final["attempts"] == 2
    assert [a["ok"] for a in final["attempt_log"]] == [False, True]
    assert final["execution"]["ok"]
    assert final["report"]["headline"] == REPORT["headline"]
    # the repair prompt must include the previous error
    assert "KeyError" in llm.calls[2]["messages"][0]["content"]
    assert not final["code"].startswith("```")


def test_agent_answers_schema_questions_without_code(tiny_parquet):
    llm = ScriptedLLM({"plan": [make_plan(intent="data_question", direct_answer="There are **3 columns**.",
                                          steps=[], kpis=[], charts=[])]})
    final = build_graph(llm, SandboxRunner(timeout_s=60)).invoke(_state(tiny_parquet))
    assert llm.purposes == ["plan"]
    assert "execution" not in final
    assert final["report"]["answer_markdown"] == "There are **3 columns**."


def test_agent_gives_up_gracefully_after_max_attempts(tiny_parquet):
    bad = {"approach": "bad", "code": "import os"}
    llm = ScriptedLLM({"plan": [make_plan()], "code": [bad], "repair": [bad]})
    final = build_graph(llm, SandboxRunner(timeout_s=60), max_repair_attempts=1).invoke(_state(tiny_parquet))
    assert llm.purposes == ["plan", "code", "repair"]
    assert not final["execution"]["ok"]
    assert final["report"]["headline"] == "I couldn't complete this analysis."


def test_agent_keeps_results_when_report_generation_fails(tiny_parquet):
    llm = ScriptedLLM({
        "plan": [make_plan()],
        "code": [{"approach": "ok", "code": GOOD_CODE}],
        "report": [LLMError("rate limited", code="llm_rate_limited")],
    })
    final = build_graph(llm, SandboxRunner(timeout_s=60)).invoke(_state(tiny_parquet))
    assert final["execution"]["ok"]
    assert final["report_error"]["code"] == "llm_rate_limited"
    assert final["execution"]["kpis"]
