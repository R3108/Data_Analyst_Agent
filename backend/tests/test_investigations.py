"""Deep research: scoping, running sub-analyses, and refusing to launder a failed step."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.investigations import build_synthesis_input
from app.agent.schemas import ResearchPlan
from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan, sign_in
from tests.test_api import _parse_sse

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
table(by_region, title="Revenue by region")
"""
BROKEN_CODE = 'df["NotAColumn"].sum()\n'

SCOPE = {
    "objective": "Understand what drives revenue and what to do about it",
    "steps": [
        {"question": "What is total revenue by region?", "why": "Establish the level"},
        {"question": "How has revenue trended month over month?", "why": "Establish the trend"},
    ],
    "out_of_scope": ["Customer acquisition cost — this dataset has no cost column"],
}

BRIEF = {
    "title": "Revenue drivers",
    "headline": "North drives most of the revenue and is still growing.",
    "executive_summary": "**North** leads and is growing faster than the rest.",
    "findings": [
        {"title": "North dominates", "detail": "North holds the largest share.",
         "sentiment": "positive", "confidence": "high"},
    ],
    "recommendations": ["Keep investing in North."],
    "caveats": ["Only two periods were compared."],
    "open_questions": ["What is the margin by region?"],
}


def scripted(steps: int = 2, broken_step: int | None = None) -> ScriptedLLM:
    codes = []
    for index in range(steps):
        codes.append({"approach": "Group by region",
                      "code": BROKEN_CODE if index == broken_step else CODE})
    # Each failing step is retried by the repair loop, which needs its own code responses.
    if broken_step is not None:
        codes += [{"approach": "Try again", "code": BROKEN_CODE} for _ in range(2)]
    return ScriptedLLM({
        "scope": [SCOPE],
        "plan": [make_plan() for _ in range(steps)],
        "code": codes,
        "repair": [{"approach": "Try again", "code": BROKEN_CODE} for _ in range(4)],
        "report": [REPORT for _ in range(steps)],
        "synthesis": [BRIEF],
    })


@pytest.fixture
def dataset(settings):
    """Builds a client with one uploaded dataset and a session on it."""
    opened: list[TestClient] = []

    def build(llm):
        client = TestClient(create_app(settings, llm=llm))
        client.__enter__()
        opened.append(client)
        sign_in(client)
        csv = (
            "Region,Revenue,Order Date\n"
            "North,1000,2024-01-01\nSouth,500,2024-02-01\n"
            "North,250,2024-03-01\nEast,100,2024-04-01\n"
        ).encode()
        record = client.post("/api/datasets",
                             files={"file": ("sales.csv", csv, "text/csv")}).json()
        session = client.post("/api/sessions", json={"dataset_id": record["id"]}).json()
        return client, record, session

    yield build
    for client in opened:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- happy path


def test_an_investigation_runs_every_step_and_writes_a_brief(dataset):
    llm = scripted()
    client, _, session = dataset(llm)
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    names = [name for name, _ in events]
    assert names[0] == "session" and names[-1] == "done"
    # One message per sub-analysis, plus the brief itself.
    assert names.count("assistant_message") == 3
    assert llm.purposes.count("scope") == 1
    assert llm.purposes.count("synthesis") == 1
    assert llm.purposes.count("plan") == 2

    brief = [data for name, data in events if name == "assistant_message"][-1]
    payload = brief["payload"]
    assert payload["intent"] == "investigation"
    assert payload["report"]["headline"] == BRIEF["headline"]
    assert payload["investigation"]["completed"] == 2
    assert payload["investigation"]["total"] == 2
    assert payload["investigation"]["out_of_scope"] == SCOPE["out_of_scope"]
    # Each step links back to the full analysis it came from.
    assert all(step["message_id"] for step in payload["investigation"]["steps"])


def test_sub_analyses_are_ordinary_messages_with_code_and_verification(dataset):
    client, _, session = dataset(scripted())
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    messages = [data for name, data in events if name == "assistant_message"]
    first = messages[0]["payload"]
    assert first["code"]
    assert first["execution"]["ok"] is True
    assert first["verification"]["score"] is not None

    # And they are persisted, so every export and pin already works on them.
    stored = client.get(f"/api/sessions/{session['id']}").json()["messages"]
    assert len([m for m in stored if m["role"] == "assistant"]) == 3


def test_the_investigation_reports_its_true_combined_cost(dataset):
    client, _, session = dataset(scripted())
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    messages = [data for name, data in events if name == "assistant_message"]
    child_calls = sum(m["payload"]["usage"]["calls"] for m in messages[:-1])
    brief_usage = messages[-1]["payload"]["usage"]
    # Its own scope and synthesis calls plus everything the sub-analyses spent.
    assert brief_usage["calls"] == child_calls + 2
    assert brief_usage["cost_usd"] > 0


def test_the_step_budget_is_respected(settings, dataset):
    settings.investigation_max_steps = 1
    client, _, session = dataset(scripted(steps=1))
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    brief = [data for name, data in events if name == "assistant_message"][-1]
    assert brief["payload"]["investigation"]["total"] == 1


def test_an_empty_objective_falls_back_to_a_default(dataset):
    client, _, session = dataset(scripted())
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={}) as response:
        events = _parse_sse(response.read().decode())
    question = next(data for name, data in events if name == "user_message")
    assert "complete picture" in question["content"]


# --------------------------------------------------------------------------- failure paths


def test_a_failed_step_is_named_in_the_caveats_not_hidden(dataset):
    client, _, session = dataset(scripted(broken_step=0))
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    brief = [data for name, data in events if name == "assistant_message"][-1]
    investigation = brief["payload"]["investigation"]
    assert investigation["completed"] == 1
    assert investigation["total"] == 2
    assert investigation["steps"][0]["ok"] is False
    assert any("did not complete" in caveat for caveat in brief["payload"]["report"]["caveats"])


def test_the_synthesis_prompt_tells_the_model_to_discard_a_failed_step():
    plan = ResearchPlan.model_validate(SCOPE)
    results = [
        {"question": "Q1", "why": "W1", "ok": False, "headline": "", "answer": "",
         "kpis": [], "tables": [], "verification_score": None,
         "verification_findings": [], "error": "KeyError: 'NotAColumn'"},
        {"question": "Q2", "why": "W2", "ok": True, "headline": "Revenue grew 8%.",
         "answer": "It grew.", "kpis": [{"label": "Growth", "value": 0.08, "format": "percent"}],
         "tables": [], "verification_score": 62,
         "verification_findings": ["Unverified figure"], "error": None},
    ]
    prompt = build_synthesis_input(plan, results)

    assert "OUTCOME: FAILED" in prompt
    assert "Do not use any figure from this step" in prompt
    assert "VERIFICATION: 62/100" in prompt
    assert "flagged: Unverified figure" in prompt
    assert "Customer acquisition cost" in prompt  # the scope's stated limits


def test_a_failed_synthesis_still_leaves_every_analysis_behind(dataset):
    from app.core.errors import LLMError

    llm = scripted()
    llm.responses["synthesis"] = [LLMError("The model is rate limited right now.",
                                           code="llm_rate_limited")]
    client, _, session = dataset(llm)
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    brief = [data for name, data in events if name == "assistant_message"][-1]
    assert brief["payload"]["degraded"] is True
    assert brief["payload"]["investigation"]["completed"] == 2
    assert "2 of 2 analyses completed" in brief["payload"]["report"]["answer_markdown"]


def test_a_failed_scope_reports_an_error_and_runs_nothing(dataset):
    from app.core.errors import LLMError

    llm = scripted()
    llm.responses["scope"] = [LLMError("No credentials.", code="llm_not_configured")]
    client, _, session = dataset(llm)
    with client.stream("POST", f"/api/sessions/{session['id']}/investigate",
                       json={"objective": "Why is revenue moving?"}) as response:
        events = _parse_sse(response.read().decode())

    names = [name for name, _ in events]
    assert "error" in names
    assert "plan" not in llm.purposes


def test_investigating_an_unknown_session_is_a_404(settings):
    with TestClient(create_app(settings, llm=scripted())) as client:
        sign_in(client)
        response = client.post("/api/sessions/ses_missing/investigate", json={})
        assert response.status_code == 404
