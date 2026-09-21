"""HTTP surface for cohorts, forecasting, the privacy guard and root-cause on breach.

These are the end-to-end paths: what the browser calls, what lands in SQLite, and what
survives a new version of the same dataset.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.notifications import Alert
from tests.conftest import REPORT, ScriptedLLM, make_plan, sign_in

CODE = 'kpi("Total revenue", df["Revenue"].sum(), format="currency")\n'


def build_csv(*, months: int = 20, customers_per_month: int = 8, collapse: bool = False) -> bytes:
    """Repeat customers over a long history — enough for cohorts and a forecast."""
    rows = ["Customer Email,Region,Revenue,Order Date"]
    identifier = 0
    start = pd.Timestamp("2023-01-01")
    for index in range(months):
        month = start + pd.DateOffset(months=index)
        for _ in range(customers_per_month):
            identifier += 1
            for offset in range(3):
                when = month + pd.DateOffset(months=offset)
                if when > start + pd.DateOffset(months=months - 1):
                    break
                region = "North" if identifier % 2 else "South"
                value = 100 + index
                if collapse and region == "South" and index >= months - 6:
                    value = 10
                rows.append(
                    f"person{identifier}@example.com,{region},{value},{when:%Y-%m-%d}"
                )
    return ("\n".join(rows) + "\n").encode("utf-8")


CSV = build_csv()


class Recorder:
    """Transport stand-in: keeps the alerts instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, channel: dict, alert: Alert) -> str:
        self.sent.append(alert)
        return "recorded"


@pytest.fixture
def transport() -> Recorder:
    return Recorder()


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan() for _ in range(4)],
        "code": [{"approach": "Sum revenue", "code": CODE} for _ in range(4)],
        "report": [REPORT for _ in range(4)],
    })


@pytest.fixture
def client(settings, llm, transport):
    with TestClient(create_app(settings, llm=llm, transport=transport)) as test_client:
        yield sign_in(test_client)


@pytest.fixture
def dataset(client) -> dict:
    return client.post("/api/datasets", files={"file": ("orders.csv", CSV, "text/csv")}).json()


# --------------------------------------------------------------------------- cohorts


def test_cohort_options_and_analysis(client, dataset):
    options = client.get(f"/api/datasets/{dataset['id']}/cohorts/options").json()
    assert options["available"] is True
    assert options["defaults"]["entity"] == "Customer Email"

    response = client.post(f"/api/datasets/{dataset['id']}/cohorts", json={"periods": 6})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["entity"] == "Customer Email"
    assert result["cohorts"]
    assert result["curve"]["retention"][0] == 1.0
    assert result["charts"] and result["tables"]
    assert any("not lived long enough" in c for c in result["caveats"])


def test_cohorts_reject_an_unknown_column(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/cohorts", json={"entity": "Nope"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_cohorts_export_markdown(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/cohorts/export.md", json={})
    assert response.status_code == 200
    assert response.text.startswith("# Retention of")
    assert "cohort-retention.md" in response.headers["Content-Disposition"]


def test_a_cohort_chart_can_be_pinned_to_a_board(client, dataset):
    board = client.post("/api/boards", json={"title": "Retention"}).json()
    response = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "chart", "source": "cohorts", "dataset_id": dataset["id"], "index": 0,
        "params": {"periods": 6},
    })
    assert response.status_code == 201, response.text
    assert response.json()["content"]["figure"]["data"][0]["type"] == "heatmap"


def test_pinned_cohort_and_forecast_tiles_survive_a_board_export(client, dataset):
    """A heat map and a fan chart cannot be drawn by a static exporter, so they travel
    with the numeric digest the PDF and PPTX writers rebuild them from."""
    board = client.post("/api/boards", json={"title": "Quarterly"}).json()
    for source, params in (("cohorts", {"periods": 6}), ("forecast", {"horizon": 3})):
        for kind in ("chart", "table"):
            created = client.post(f"/api/boards/{board['id']}/items", json={
                "kind": kind, "source": source, "dataset_id": dataset["id"], "index": 0,
                "params": params,
            })
            assert created.status_code == 201, created.text

    pdf = client.get(f"/api/boards/{board['id']}/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    pptx = client.get(f"/api/boards/{board['id']}/export.pptx")
    assert pptx.status_code == 200 and pptx.content[:2] == b"PK"


# --------------------------------------------------------------------------- forecast


def test_forecast_options_and_projection(client, dataset):
    options = client.get(f"/api/datasets/{dataset['id']}/forecast/options").json()
    assert options["available"] is True
    assert options["defaults"]["measure"] == "Revenue"

    response = client.post(f"/api/datasets/{dataset['id']}/forecast", json={"horizon": 3})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["forecast"]) == 3
    assert result["backtest"]["folds"] >= 2
    assert result["accuracy"]
    assert result["verdict"]["summary"]


def test_forecast_rejects_an_unknown_method(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/forecast", json={"method": "arima"})
    assert response.status_code == 422


def test_forecast_exports_markdown(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/forecast/export.md", json={"horizon": 3})
    assert response.status_code == 200
    assert "## Backtest scoreboard" in response.text


# --------------------------------------------------------------------------- privacy


def test_upload_detects_personal_data_and_withholds_it_from_the_profile(client, dataset):
    emails = [c for c in dataset["profile"]["columns"] if c["name"] == "Customer Email"]
    assert emails and emails[0]["sensitive"] is True
    assert emails[0]["sample_values"] == ["(withheld)"]
    assert "person1@example.com" not in str(dataset["profile"])

    state = client.get(f"/api/datasets/{dataset['id']}/privacy").json()
    assert state["scan"]["status"] == "sensitive"
    assert state["scan"]["suggested_policy"]["Customer Email"] == "hash"
    assert state["applied"] is False


def test_the_preview_still_shows_the_real_data_until_a_policy_is_applied(client, dataset):
    preview = client.get(f"/api/datasets/{dataset['id']}/preview").json()
    assert any("@example.com" in str(cell) for row in preview["rows"] for cell in row)


def test_setting_a_policy_does_not_touch_the_table(client, dataset):
    response = client.put(f"/api/datasets/{dataset['id']}/privacy",
                          json={"policy": {"Customer Email": "hash"}})
    assert response.status_code == 200
    assert response.json()["state"]["policy"] == {"Customer Email": "hash"}
    assert response.json()["applied"] is False
    preview = client.get(f"/api/datasets/{dataset['id']}/preview").json()
    assert any("@example.com" in str(cell) for row in preview["rows"] for cell in row)


def test_applying_a_policy_rewrites_the_table_everywhere(client, dataset):
    client.put(f"/api/datasets/{dataset['id']}/privacy", json={"policy": {"Customer Email": "hash"}})
    applied = client.post(f"/api/datasets/{dataset['id']}/privacy/apply")
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True

    preview = client.get(f"/api/datasets/{dataset['id']}/preview").json()
    assert not any("@example.com" in str(cell) for row in preview["rows"] for cell in row)
    assert any(str(cell).startswith("id_") for row in preview["rows"] for cell in row)
    download = client.get(f"/api/datasets/{dataset['id']}/download")
    assert "@example.com" not in download.text
    assert "dataset.redact" in str(client.get("/api/activity").json())


def test_a_hashed_entity_still_works_for_cohorts(client, dataset):
    before = client.post(f"/api/datasets/{dataset['id']}/cohorts", json={"periods": 4}).json()
    client.put(f"/api/datasets/{dataset['id']}/privacy", json={"policy": {"Customer Email": "hash"}})
    client.post(f"/api/datasets/{dataset['id']}/privacy/apply")
    after = client.post(f"/api/datasets/{dataset['id']}/cohorts", json={"periods": 4}).json()
    assert after["summary"]["entities"] == before["summary"]["entities"]
    assert after["curve"]["retention"] == before["curve"]["retention"]


def test_dropping_every_column_is_refused(client, dataset):
    columns = [c["name"] for c in dataset["profile"]["columns"]]
    client.put(f"/api/datasets/{dataset['id']}/privacy",
               json={"policy": {name: "drop" for name in columns}})
    response = client.post(f"/api/datasets/{dataset['id']}/privacy/apply")
    assert response.status_code == 422
    assert "every column" in response.json()["error"]["message"]


def test_applying_nothing_is_refused(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/privacy/apply")
    assert response.status_code == 422
    assert "nothing to redact" in response.json()["error"]["message"]


def test_an_unknown_column_in_a_policy_is_rejected(client, dataset):
    response = client.put(f"/api/datasets/{dataset['id']}/privacy",
                          json={"policy": {"Nope": "drop"}})
    assert response.status_code == 422


def test_the_policy_is_re_applied_to_the_next_version(client, dataset):
    """The whole point: next month's export cannot re-introduce what was removed."""
    client.put(f"/api/datasets/{dataset['id']}/privacy", json={"policy": {"Customer Email": "hash"}})
    client.post(f"/api/datasets/{dataset['id']}/privacy/apply")

    v2 = client.post("/api/datasets",
                     files={"file": ("orders.csv", CSV, "text/csv")},
                     data={"replaces": dataset["id"]}).json()
    assert v2["version"] == 2
    preview = client.get(f"/api/datasets/{v2['id']}/preview").json()
    assert not any("@example.com" in str(cell) for row in preview["rows"] for cell in row)
    assert v2["privacy"]["policy"] == {"Customer Email": "hash"}
    assert any(a["step"] == "redact_personal_data" for a in v2["cleaning"]["actions"])
    # Same salt, so a customer keeps the same pseudonym across versions and a cohort
    # built on the new upload is still the same population as the old one.
    assert v2["privacy"]["salt"]


def test_privacy_review_exports_markdown(client, dataset):
    response = client.get(f"/api/datasets/{dataset['id']}/privacy.md")
    assert response.status_code == 200
    assert response.text.startswith("# Privacy review")
    assert "person1@example.com" not in response.text


def test_rescanning_returns_the_same_verdict(client, dataset):
    response = client.post(f"/api/datasets/{dataset['id']}/privacy/scan")
    assert response.status_code == 200
    assert response.json()["scan"]["status"] == "sensitive"


# --------------------------------------------------------------------------- root cause


@pytest.fixture
def breached(client, transport):
    """A monitor whose metric has collapsed in the most recent period."""
    dataset = client.post(
        "/api/datasets",
        files={"file": ("collapse.csv", build_csv(collapse=True), "text/csv")},
    ).json()
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                       json={"message": "Total revenue?"}) as response:
        response.read()
    message = client.get(f"/api/sessions/{session['id']}").json()["messages"][-1]
    client.post("/api/alerts/channels",
                json={"kind": "webhook", "target": "https://example.test/hook",
                      "events": ["breach"]})
    monitor = client.post("/api/monitors", json={
        "message_id": message["id"], "index": 0, "direction": "below",
        "threshold": 10_000_000.0,
    }).json()
    return dataset, monitor


def test_a_breach_carries_its_own_drill_down(client, breached):
    _, monitor = breached
    run = client.post(f"/api/monitors/{monitor['id']}/run").json()
    assert run["run"]["status"] == "breached"
    cause = run["run"]["root_cause"]
    assert cause["status"] == "ok"
    assert cause["measure"] == "Revenue"
    assert cause["contributors"]
    assert cause["summary"]
    # And it is readable from the monitor itself, not only from the run that found it.
    assert client.get(f"/api/monitors/{monitor['id']}").json()["root_cause"]["status"] == "ok"


def test_the_breach_alert_names_the_cause(client, breached, transport):
    _, monitor = breached
    client.post(f"/api/monitors/{monitor['id']}/run")
    breaches = [a for a in transport.sent if a.event == "breach"]
    assert breaches, "no breach alert was delivered"
    labels = [label for label, _ in breaches[-1].facts]
    assert "Most likely driver" in labels
    assert any("Biggest mover" in label for label in labels)


def test_the_drill_down_can_be_requested_on_demand(client, breached):
    _, monitor = breached
    response = client.post(f"/api/monitors/{monitor['id']}/diagnose")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert response.json()["params"]["measure"] == "Revenue"


def test_the_digest_explains_every_breach(client, breached):
    _, monitor = breached
    client.post(f"/api/monitors/{monitor['id']}/run")
    markdown = client.get("/api/monitors/digest.md").text
    assert "Needs attention" in markdown
    assert "Why:" in markdown


def test_root_cause_can_be_switched_off(settings, llm, transport):
    settings = settings.model_copy(update={"monitor_root_cause": False})
    with TestClient(create_app(settings, llm=llm, transport=transport)) as client:
        sign_in(client)
        dataset = client.post(
            "/api/datasets",
            files={"file": ("collapse.csv", build_csv(collapse=True), "text/csv")},
        ).json()
        session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
        with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                           json={"message": "Total revenue?"}) as response:
            response.read()
        message = client.get(f"/api/sessions/{session['id']}").json()["messages"][-1]
        monitor = client.post("/api/monitors", json={
            "message_id": message["id"], "index": 0, "direction": "below",
            "threshold": 10_000_000.0,
        }).json()
        run = client.post(f"/api/monitors/{monitor['id']}/run").json()
        assert run["run"]["status"] == "breached"
        assert run["run"]["root_cause"] is None


def test_health_advertises_the_new_capabilities(client):
    health = client.get("/api/health").json()
    assert health["monitors"]["root_cause"] is True
    assert health["privacy"]["scan_enabled"] is True
