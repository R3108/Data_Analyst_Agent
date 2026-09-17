"""HTTP surface for significance, scenarios, recall, routing, activity and comments."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan

# Two segments with clearly different averages, spread across the whole date span so a
# trailing comparison window contains both.
ROWS = []
for index in range(120):
    ROWS.append(("North", 100 + (index % 5), f"2024-{(index % 12) + 1:02d}-05"))
    ROWS.append(("South", 150 + (index % 5), f"2024-{(index % 12) + 1:02d}-06"))
CSV = ("Region,Revenue,Order Date\n"
       + "\n".join(f"{r},{v},{d}" for r, v, d in ROWS)).encode()

CODE = 'kpi("Total revenue", df["Revenue"].sum(), format="currency")\n'


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan() for _ in range(4)],
        "code": [{"approach": "Sum revenue", "code": CODE} for _ in range(4)],
        "report": [REPORT for _ in range(4)],
    })


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield test_client


@pytest.fixture
def dataset(client) -> dict:
    return client.post("/api/datasets", files={"file": ("sales.csv", CSV, "text/csv")}).json()


# --------------------------------------------------------------------------- significance


def test_significance_options_and_test(client, dataset):
    options = client.get(f"/api/datasets/{dataset['id']}/significance/options").json()
    assert options["available"] is True
    assert "Revenue" in [m["name"] for m in options["measures"]]

    response = client.post(f"/api/datasets/{dataset['id']}/significance",
                           json={"measure": "Revenue", "dimension": "Region"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verdict"]["label"] == "real"
    assert result["scan"]["method"].startswith("Benjamini")
    assert result["charts"] and result["tables"]


def test_significance_rejects_an_unknown_column(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/significance",
                           json={"measure": "Nope", "dimension": "Region"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_significance_exports_markdown(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/significance/export.md",
                           json={"measure": "Revenue", "dimension": "Region"})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "Welch's t-test" in response.text


def test_an_invalid_alpha_is_refused_by_the_schema(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/significance", json={"alpha": 0.9})
    assert response.status_code == 422


# --------------------------------------------------------------------------- scenarios


def test_scenario_options_and_simulation(client, dataset):
    options = client.get(f"/api/datasets/{dataset['id']}/scenarios/options").json()
    assert options["defaults"]["measure"] == "Revenue"

    response = client.post(f"/api/datasets/{dataset['id']}/scenarios",
                           json={"levers": {"global": {"rate_pct": 0.1}}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["change"]["pct"] == pytest.approx(0.1)
    assert result["shift_share"]["closes"] is True
    assert result["sensitivity"]


def test_a_segment_lever_travels_through_the_api(client, dataset):
    result = client.post(f"/api/datasets/{dataset['id']}/scenarios", json={
        "levers": {"segments": {"North": {"rate_pct": 0.5, "share_points": 0.05}}},
    }).json()
    north = next(s for s in result["segments"] if s["label"] == "North")
    assert north["change"] > 0
    assert result["levers"]["segments"]["North"]["rate_pct"] == 0.5


def test_goal_seek_over_http(client, dataset):
    baseline = client.post(f"/api/datasets/{dataset['id']}/scenarios", json={}).json()
    target = baseline["baseline"]["value"] * 1.2

    response = client.post(f"/api/datasets/{dataset['id']}/scenarios/goal-seek",
                           json={"target": target, "lever": "rate"})
    assert response.status_code == 200, response.text
    goal = response.json()
    assert goal["achievable"] is True
    assert goal["required_pct"] == pytest.approx(0.2, abs=1e-4)
    assert goal["scenario"]["scenario"]["value"] == pytest.approx(target, rel=1e-6)


def test_an_out_of_range_lever_is_rejected(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/scenarios",
                           json={"levers": {"global": {"rate_pct": 99}}})
    assert response.status_code == 422


def test_scenario_exports_markdown(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/scenarios/export.md",
                           json={"levers": {"global": {"rate_pct": 0.1}}})
    assert response.status_code == 200
    assert "Volume · mix · rate" in response.text


# --------------------------------------------------------------------------- memory


def test_recall_finds_an_earlier_analysis_in_the_same_workspace(client, dataset):
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                       json={"message": "What is total revenue by region?"}) as response:
        response.read()

    recalled = client.get("/api/recall", params={
        "q": "total revenue broken down by region", "dataset_id": dataset["id"],
    }).json()
    assert recalled["matches"]
    assert recalled["matches"][0]["question"] == "What is total revenue by region?"
    assert recalled["summary"] is not None


def test_a_repeat_question_is_flagged_before_the_work_starts(client, dataset):
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    question = {"message": "What is total revenue by region?"}
    with client.stream("POST", f"/api/sessions/{session['id']}/chat", json=question) as response:
        response.read()

    from tests.test_api import _parse_sse

    with client.stream("POST", f"/api/sessions/{session['id']}/chat", json=question) as response:
        events = _parse_sse(response.read().decode())

    names = [name for name, _ in events]
    assert "recall" in names
    # The warning arrives before any model work, so it can still be acted on.
    assert names.index("recall") < names.index("step")
    recall = dict(events)["recall"]
    assert recall["duplicate"] is True


def test_routing_picks_the_right_dataset(client, dataset):
    other = (
        "employee_id,department,salary\n"
        "1,Engineering,100000\n2,Sales,90000\n3,Engineering,110000\n"
    ).encode()
    client.post("/api/datasets", files={"file": ("headcount.csv", other, "text/csv")})

    result = client.post("/api/route", json={"question": "what is revenue by region?"}).json()
    assert result["best"]["name"].lower().startswith("sales")
    assert result["confident"] is True

    result = client.post("/api/route", json={"question": "average salary by department"}).json()
    assert result["best"]["name"].lower().startswith("headcount")


# --------------------------------------------------------------------------- collaboration


def test_uploading_and_deleting_a_dataset_is_recorded(client, dataset):
    entries = client.get("/api/activity").json()
    assert entries[0]["action"] == "dataset.upload"
    assert dataset["name"] in entries[0]["summary"]

    client.delete(f"/api/datasets/{dataset['id']}")
    assert client.get("/api/activity").json()[0]["action"] == "dataset.delete"


def test_comments_round_trip_over_http(client, dataset):
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    created = client.post("/api/comments", json={
        "subject_kind": "session", "subject_id": session["id"],
        "body": "Is this net of refunds?", "author": "Ada",
    })
    assert created.status_code == 201, created.text
    comment = created.json()

    client.post("/api/comments", json={
        "subject_kind": "session", "subject_id": session["id"],
        "body": "Yes.", "author": "Grace", "parent_id": comment["id"],
    })

    listing = client.get("/api/comments", params={
        "subject_kind": "session", "subject_id": session["id"],
    }).json()
    assert listing["open_count"] == 1
    assert len(listing["threads"][0]["replies"]) == 1

    counts = client.get("/api/comments/counts", params={
        "subject_kind": "session", "ids": f"{session['id']},ses_other",
    }).json()
    assert counts == {session["id"]: 2}

    client.patch(f"/api/comments/{comment['id']}", json={"resolved": True})
    assert client.get("/api/comments", params={
        "subject_kind": "session", "subject_id": session["id"],
    }).json()["open_count"] == 0

    assert client.delete(f"/api/comments/{comment['id']}").status_code == 204


# --------------------------------------------------------------------------- pinning


def test_a_significance_chart_is_recomputed_before_it_is_pinned(client, dataset):
    board = client.post("/api/boards", json={"title": "Evidence"}).json()
    params = {"measure": "Revenue", "dimension": "Region"}

    pinned = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "chart", "source": "significance", "dataset_id": dataset["id"],
        "index": 0, "params": params,
    })
    assert pinned.status_code == 201, pinned.text
    item = pinned.json()
    assert item["kind"] == "chart"
    # The snapshot carries the digest the PDF and PowerPoint exporters rebuild from.
    assert item["content"]["digest"]["traces"]
    # And the verdict travels with it, so a board tile says what it means.
    assert "differ" in item["source_question"] or "distinguishable" in item["source_question"]


def test_a_scenario_table_can_be_pinned(client, dataset):
    board = client.post("/api/boards", json={"title": "Plan"}).json()
    pinned = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "table", "source": "scenarios", "dataset_id": dataset["id"],
        "index": 0, "params": {"levers": {"global": {"rate_pct": 0.1}}},
    })
    assert pinned.status_code == 201, pinned.text
    assert pinned.json()["content"]["rows"]


def test_a_client_cannot_pin_content_of_its_own_invention(client, dataset):
    board = client.post("/api/boards", json={"title": "Evidence"}).json()
    # No content field is accepted at all: a pin names provenance, never a payload.
    response = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "kpi", "source": "significance", "dataset_id": dataset["id"],
        "index": 0, "params": {}, "content": {"label": "Made up", "value": 999},
    })
    assert response.status_code == 422
    assert "cannot be pinned" in response.json()["error"]["message"]


def test_pinning_a_result_that_does_not_exist_is_refused(client, dataset):
    board = client.post("/api/boards", json={"title": "Evidence"}).json()
    response = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "chart", "source": "scenarios", "dataset_id": dataset["id"], "index": 99,
        "params": {},
    })
    assert response.status_code == 422


def test_health_advertises_the_new_capabilities(client):
    health = client.get("/api/health").json()
    assert health["recall"]["enabled"] is True
    assert health["investigations"]["max_steps"] >= 1
    assert "sqlite" in health["sources"]["dialects"]
    assert health["workspace"]["protected"] is False


def test_sources_endpoint_lists_the_supported_dialects(client):
    payload = client.get("/api/sources").json()
    assert payload["sources"] == []
    kinds = {d["kind"] for d in payload["dialects"]}
    assert {"postgresql", "sqlite", "duckdb"} <= kinds
    # Each optional dialect names the package a user would have to install.
    postgres = next(d for d in payload["dialects"] if d["kind"] == "postgresql")
    assert postgres["package"] == "psycopg[binary]"
