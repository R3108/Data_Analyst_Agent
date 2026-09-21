"""Metric monitors: re-running a snapshotted analysis without touching the model."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan, sign_in

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
table(by_region, title="Revenue by region")
"""

# Same columns as `tiny_csv_bytes`, larger numbers — a plausible "next month" export.
V2_CSV = (
    "Region,Revenue,Order Date\n"
    "North,4000,2024-05-01\n"
    "South,1000,2024-06-01\n"
).encode("utf-8")


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan(), make_plan()],
        "code": [{"approach": "group", "code": CODE}, {"approach": "group", "code": CODE}],
        "report": [REPORT, REPORT],
    })


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield sign_in(test_client)


@pytest.fixture
def analysed(client, tiny_csv_bytes):
    """One completed analysis whose KPI "Total revenue" is 1750.0."""
    dataset = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                       json={"message": "Revenue by region?"}) as response:
        response.read()
    message = client.get(f"/api/sessions/{session['id']}").json()["messages"][-1]
    return dataset, session, message


def watch(client, message, **overrides):
    body = {"message_id": message["id"], "index": 0, "direction": "below", "threshold": 1000.0}
    body.update(overrides)
    return client.post("/api/monitors", json=body)


def test_watching_a_kpi_snapshots_its_code_and_baseline(client, analysed):
    dataset, session, message = analysed
    response = watch(client, message)
    assert response.status_code == 201, response.text
    monitor = response.json()

    assert monitor["kpi_label"] == "Total revenue"
    assert monitor["last_value"] == 1750.0
    assert monitor["baseline_value"] == 1750.0
    assert monitor["dataset_id"] == dataset["id"]
    assert monitor["source_session_id"] == session["id"]
    assert monitor["question"] == "Revenue by region?"
    assert monitor["rule"] == "alert below $1,000"
    assert monitor["enabled"] is True


def test_running_a_monitor_costs_no_model_calls(client, llm, analysed):
    _, _, message = analysed
    monitor = watch(client, message).json()
    calls_before = len(llm.calls)

    result = client.post(f"/api/monitors/{monitor['id']}/run").json()

    assert result["run"]["status"] == "ok"
    assert result["run"]["value"] == 1750.0
    assert result["run"]["breached"] is False
    assert result["run"]["detail"] == "1,750 is at or above 1,000."
    assert len(llm.calls) == calls_before  # the whole point: no tokens burned


def test_a_threshold_breach_is_recorded(client, analysed):
    _, _, message = analysed
    monitor = watch(client, message, direction="below", threshold=5000.0).json()

    result = client.post(f"/api/monitors/{monitor['id']}/run").json()
    assert result["run"]["status"] == "breached"
    assert result["run"]["breached"] is True
    assert "fallen below" in result["run"]["detail"]
    assert result["monitor"]["last_status"] == "breached"


def test_an_above_threshold_breach_is_recorded(client, analysed):
    _, _, message = analysed
    monitor = watch(client, message, direction="above", threshold=1000.0).json()
    assert client.post(f"/api/monitors/{monitor['id']}/run").json()["run"]["status"] == "breached"


def test_a_percentage_move_is_measured_against_the_previous_check(client, analysed):
    dataset, _, message = analysed
    monitor = watch(client, message, direction="change_pct", threshold=0.1).json()

    # Nothing changed yet, so there is no movement to report.
    assert client.post(f"/api/monitors/{monitor['id']}/run").json()["run"]["status"] == "ok"

    client.post("/api/datasets", files={"file": ("sales-may.csv", V2_CSV, "text/csv")},
                data={"replaces": dataset["id"]})

    latest = client.get(f"/api/monitors/{monitor['id']}").json()
    assert latest["last_value"] == 5000.0
    assert latest["last_status"] == "breached"
    assert "rose" in latest["last_detail"]


def test_a_new_dataset_version_re_checks_its_monitors(client, analysed):
    dataset, _, message = analysed
    monitor = watch(client, message, direction="below", threshold=2000.0).json()
    assert monitor["last_value"] == 1750.0

    client.post("/api/datasets", files={"file": ("sales-may.csv", V2_CSV, "text/csv")},
                data={"replaces": dataset["id"]})

    # Re-measured against the new version without anyone asking.
    refreshed = client.get(f"/api/monitors/{monitor['id']}").json()
    assert refreshed["last_value"] == 5000.0
    assert refreshed["last_status"] == "ok"
    assert len(refreshed["runs"]) == 1
    assert refreshed["history"] == [5000.0]


def test_monitors_can_be_paused_run_in_bulk_and_removed(client, analysed):
    _, _, message = analysed
    monitor = watch(client, message).json()

    paused = client.patch(f"/api/monitors/{monitor['id']}", json={"enabled": False}).json()
    assert paused["enabled"] is False

    # A paused monitor is skipped by sweeps.
    assert client.post("/api/monitors/run").json()["ran"] == 0

    resumed = client.patch(f"/api/monitors/{monitor['id']}",
                           json={"enabled": True, "threshold": 10.0, "title": "Revenue watch"}).json()
    assert resumed["enabled"] is True and resumed["title"] == "Revenue watch"
    assert client.post("/api/monitors/run").json()["ran"] == 1

    assert client.delete(f"/api/monitors/{monitor['id']}").status_code == 204
    assert client.get(f"/api/monitors/{monitor['id']}").status_code == 404
    assert client.get("/api/monitors").json() == []


def test_the_digest_summarises_every_watch(client, analysed):
    _, _, message = analysed
    watch(client, message, direction="below", threshold=5000.0, title="Revenue floor")
    client.post("/api/monitors/run")

    digest = client.get("/api/monitors/digest").json()
    assert digest["total"] == 1
    assert digest["counts"]["breached"] == 1
    assert digest["breached"][0]["title"] == "Revenue floor"

    briefing = client.get("/api/monitors/digest.md")
    assert briefing.status_code == 200
    assert "# Monitor briefing" in briefing.text
    assert "Needs attention" in briefing.text
    assert "Revenue floor" in briefing.text


def test_only_monitorable_kpis_are_accepted(client, analysed):
    _, _, message = analysed
    assert watch(client, message, index=9).status_code == 422
    assert watch(client, message, direction="sideways").status_code == 422
    assert watch(client, {"id": "msg_missing"}).status_code == 404


def test_a_monitor_reports_failure_instead_of_a_stale_number(client, analysed):
    dataset, _, message = analysed
    monitor = watch(client, message).json()
    client.delete(f"/api/datasets/{dataset['id']}")

    # Deleting the dataset cascades to its monitors rather than leaving them dangling.
    assert client.get(f"/api/monitors/{monitor['id']}").status_code == 404
