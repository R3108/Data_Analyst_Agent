"""Scheduled briefings: cadence, delivery and what happens when a run fails."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.errors import InvalidInputError, NotFoundError
from app.main import create_app
from app.services.briefings import _is_due, _next_run
from tests.conftest import REPORT, ScriptedLLM, make_plan

CODE = """
kpi("Total revenue", df["Revenue"].sum(), format="currency")
table(df.groupby("Region", as_index=False)["Revenue"].sum(), title="Revenue by region")
"""

CSV = (
    "Region,Revenue,Order Date\n"
    "North,1000,2024-01-01\nSouth,500,2024-02-01\n"
    "North,250,2024-03-01\nEast,100,2024-04-01\n"
).encode()


class RecordingTransport:
    """Captures alerts instead of sending them, so delivery is testable without a socket."""

    def __init__(self) -> None:
        self.sent: list[tuple[dict, object]] = []

    def send(self, channel, alert):
        self.sent.append((channel, alert))
        return "captured"


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan() for _ in range(6)],
        "code": [{"approach": "Sum revenue", "code": CODE} for _ in range(6)],
        "report": [REPORT for _ in range(6)],
    })


@pytest.fixture
def client(settings, llm, transport):
    with TestClient(create_app(settings, llm=llm, transport=transport)) as test_client:
        yield test_client


@pytest.fixture
def dataset_id(client) -> str:
    return client.post("/api/datasets", files={"file": ("sales.csv", CSV, "text/csv")}).json()["id"]


# --------------------------------------------------------------------------- cadence


def test_a_briefing_that_has_never_run_is_due():
    assert _is_due({"enabled": True, "schedule_hours": 24, "last_run_at": None}) is True
    assert _next_run({"last_run_at": None, "schedule_hours": 24}) is None


def test_a_briefing_is_not_due_again_until_its_cadence_elapses():
    recent = datetime.now(timezone.utc).isoformat()
    assert _is_due({"enabled": True, "schedule_hours": 24, "last_run_at": recent}) is False

    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert _is_due({"enabled": True, "schedule_hours": 24, "last_run_at": stale}) is True


def test_a_paused_briefing_is_never_due():
    assert _is_due({"enabled": False, "schedule_hours": 1, "last_run_at": None}) is False


def test_an_unparseable_timestamp_makes_it_due_rather_than_stuck():
    assert _is_due({"enabled": True, "schedule_hours": 24, "last_run_at": "not-a-date"}) is True


# --------------------------------------------------------------------------- CRUD


def test_creating_a_briefing_defaults_its_title_to_the_question(client, dataset_id):
    created = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "How is revenue tracking this week?",
    })
    assert created.status_code == 201, created.text
    briefing = created.json()
    assert briefing["title"] == "How is revenue tracking this week?"
    assert briefing["schedule_hours"] == 24
    assert briefing["due"] is True


def test_a_briefing_follows_the_dataset_lineage_not_one_version(client, dataset_id):
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?",
    }).json()
    # Pinned to the lineage root, so a re-upload does not orphan it.
    assert briefing["dataset_id"] == dataset_id

    replacement = client.post(
        "/api/datasets",
        files={"file": ("sales.csv", CSV, "text/csv")},
        data={"replaces": dataset_id},
    ).json()
    assert replacement["version"] == 2

    result = client.post(f"/api/briefings/{briefing['id']}/run?deliver=false").json()
    ran_against = client.get(f"/api/sessions/{result['session_id']}").json()["dataset_id"]
    assert ran_against == replacement["id"]


def test_briefings_validate_their_input(client, dataset_id):
    assert client.post("/api/briefings", json={"dataset_id": dataset_id, "question": ""}).status_code == 422
    assert client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?", "schedule_hours": 0,
    }).status_code == 422
    assert client.post("/api/briefings", json={
        "dataset_id": "ds_nope", "question": "Revenue?",
    }).status_code == 404


def test_a_briefing_can_be_paused_and_deleted(client, dataset_id):
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?",
    }).json()

    paused = client.patch(f"/api/briefings/{briefing['id']}", json={"enabled": False}).json()
    assert paused["enabled"] is False and paused["due"] is False

    assert client.delete(f"/api/briefings/{briefing['id']}").status_code == 204
    assert client.get(f"/api/briefings/{briefing['id']}").status_code == 404


# --------------------------------------------------------------------------- execution


def test_running_a_briefing_produces_a_full_analysis_to_link_to(client, dataset_id):
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "What is revenue by region?",
    }).json()

    result = client.post(f"/api/briefings/{briefing['id']}/run?deliver=false").json()
    assert result["briefing"]["last_status"] == "ok"
    assert result["briefing"]["run_count"] == 1
    assert result["briefing"]["last_headline"] == REPORT["headline"]

    # The delivered summary must always be backed by an inspectable analysis.
    session = client.get(f"/api/sessions/{result['session_id']}").json()
    assistant = [m for m in session["messages"] if m["role"] == "assistant"][0]
    assert assistant["payload"]["code"]
    assert assistant["payload"]["execution"]["kpis"]


def test_running_a_briefing_sets_its_next_run(client, dataset_id):
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?", "schedule_hours": 12,
    }).json()
    after = client.post(f"/api/briefings/{briefing['id']}/run?deliver=false").json()["briefing"]

    assert after["due"] is False
    assert after["next_run_at"] > after["last_run_at"]


def test_a_briefing_is_delivered_to_subscribed_channels_only(client, dataset_id, transport):
    client.post("/api/alerts/channels", json={
        "kind": "webhook", "target": "https://example.test/hook", "events": ["briefing"],
    })
    client.post("/api/alerts/channels", json={
        "kind": "webhook", "target": "https://example.test/other", "events": ["breach"],
    })
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?",
    }).json()

    result = client.post(f"/api/briefings/{briefing['id']}/run").json()
    assert len(result["deliveries"]) == 1
    _, alert = transport.sent[0]
    assert alert.event == "briefing"
    assert alert.summary == REPORT["headline"]
    # The alert links to the analysis behind the number, not just the number.
    assert alert.link and "session=" in alert.link


def test_delivery_can_be_suppressed_for_one_run(client, dataset_id, transport):
    client.post("/api/alerts/channels", json={
        "kind": "webhook", "target": "https://example.test/hook", "events": ["briefing"],
    })
    briefing = client.post("/api/briefings", json={
        "dataset_id": dataset_id, "question": "Revenue?",
    }).json()

    result = client.post(f"/api/briefings/{briefing['id']}/run?deliver=false").json()
    assert result["deliveries"] == []
    assert transport.sent == []


def test_the_sweep_runs_only_what_is_due(settings, llm, transport):
    app = create_app(settings, llm=llm, transport=transport)
    with TestClient(app) as client:
        record = client.post("/api/datasets",
                             files={"file": ("sales.csv", CSV, "text/csv")}).json()
        due = client.post("/api/briefings", json={
            "dataset_id": record["id"], "question": "Revenue?",
        }).json()
        client.post("/api/briefings", json={
            "dataset_id": record["id"], "question": "Paused?", "enabled": False,
        })

        results = asyncio.run(app.state.briefings.run_due())
        assert [r["briefing"]["id"] for r in results] == [due["id"]]

        # Nothing is due immediately afterwards.
        assert asyncio.run(app.state.briefings.run_due()) == []


def test_one_broken_briefing_does_not_stop_the_sweep(settings, transport):
    from app.core.errors import LLMError

    # The first briefing the sweep reaches hits a rate limit; the next one must still run.
    failing = ScriptedLLM({
        "plan": [LLMError("rate limited", code="llm_rate_limited"), make_plan()],
        "code": [{"approach": "Sum revenue", "code": CODE}],
        "report": [REPORT],
    })
    app = create_app(settings, llm=failing, transport=transport)
    with TestClient(app) as client:
        record = client.post("/api/datasets",
                             files={"file": ("sales.csv", CSV, "text/csv")}).json()
        for question in ("First?", "Second?"):
            client.post("/api/briefings", json={"dataset_id": record["id"], "question": question})

        results = asyncio.run(app.state.briefings.run_due())
        assert len(results) == 2

        statuses = sorted(b["last_status"] for b in app.state.briefings.list())
        # Both were attempted and both outcomes recorded — a failure is visible, not silent.
        assert statuses == ["error", "ok"]
        failed = next(b for b in app.state.briefings.list() if b["last_status"] == "error")
        assert "rate limited" in failed["last_error"]
        assert transport.sent == []  # nothing is delivered for a failed run


def test_service_errors_are_typed(client, dataset_id):
    service = client.app.state.briefings
    with pytest.raises(NotFoundError):
        service.get("brf_missing")
    with pytest.raises(InvalidInputError, match="cadence"):
        service.create({"dataset_id": dataset_id, "question": "Revenue?", "schedule_hours": 99999})
