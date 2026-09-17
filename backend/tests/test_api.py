from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
table(by_region, title="Revenue by region")
"""


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        events.append((name, data))
    return events


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan(), make_plan(intent="out_of_scope", direct_answer="I can only analyse this dataset.",
                                        steps=[], kpis=[], charts=[])],
        "code": [{"approach": "Group by region", "code": CODE}],
        "report": [REPORT],
    })


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield test_client


def test_full_chat_flow(client, llm, tiny_csv_bytes):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["limits"]["ai_monthly_budget_usd"] == 5.0

    upload = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")})
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    assert dataset["n_rows"] == 4
    assert dataset["cleaning"]["type_conversions"]["Revenue"] == "number"

    preview = client.get(f"/api/datasets/{dataset['id']}/preview?limit=2").json()
    assert preview["total_rows"] == 4 and len(preview["rows"]) == 2

    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()

    with client.stream("POST", f"/api/sessions/{session['id']}/chat", json={"message": "Revenue by region?"}) as r:
        assert r.status_code == 200
        events = _parse_sse(r.read().decode())
    names = [name for name, _ in events]
    assert names[0] == "session" and names[-1] == "done"
    assert "step" in names and "assistant_message" in names
    assistant = dict(events)["assistant_message"]
    assert assistant["payload"]["execution"]["kpis"][0]["value"] == 1750.0
    assert assistant["payload"]["report"]["headline"] == REPORT["headline"]

    # follow-up turn receives the earlier turn as history
    with client.stream("POST", f"/api/sessions/{session['id']}/chat", json={"message": "Tell me a joke"}) as r:
        _parse_sse(r.read().decode())
    history = llm.calls[-1]["messages"]
    assert history[0] == {"role": "user", "content": "Revenue by region?"}
    assert "North generates the most revenue." in history[1]["content"]

    detail = client.get(f"/api/sessions/{session['id']}").json()
    assert detail["title"] == "Revenue by region?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]

    exported = client.get(f"/api/sessions/{session['id']}/export")
    assert exported.status_code == 200 and "Revenue by region" in exported.text

    assert client.delete(f"/api/sessions/{session['id']}").status_code == 204
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404


def test_llm_failure_is_streamed_as_error_and_persisted(settings, tiny_csv_bytes):
    from app.core.errors import LLMNotConfiguredError

    llm = ScriptedLLM({"plan": [LLMNotConfiguredError("No OpenAI credentials found.")]})
    with TestClient(create_app(settings, llm=llm)) as client:
        dataset = client.post("/api/datasets", files={"file": ("s.csv", tiny_csv_bytes, "text/csv")}).json()
        session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
        with client.stream("POST", f"/api/sessions/{session['id']}/chat", json={"message": "hi"}) as r:
            events = dict(_parse_sse(r.read().decode()))
        assert events["error"]["code"] == "llm_not_configured"
        messages = client.get(f"/api/sessions/{session['id']}").json()["messages"]
        assert messages[-1]["status"] == "error"


def test_error_responses_are_structured(client):
    unsupported = client.post("/api/datasets", files={"file": ("notes.pdf", b"%PDF", "application/pdf")})
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_file"

    empty = client.post("/api/datasets", files={"file": ("empty.csv", b"", "text/csv")})
    assert empty.status_code == 422

    missing = client.post("/api/sessions/ses_nope/chat", json={"message": "hi"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

    invalid = client.post("/api/sessions", json={})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_input"


def test_sample_dataset_endpoint(client):
    response = client.post("/api/datasets/sample")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Retail Sales (Sample)"
    assert body["profile"]["suggested_questions"]
