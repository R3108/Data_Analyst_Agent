"""End-to-end HTTP coverage for drill-downs, data contracts and alert delivery."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import sign_in
from app.services.notifications import Alert


class RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], Alert]] = []

    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        if "fail" in channel["target"]:
            raise RuntimeError("connection refused")
        self.sent.append((channel, alert))
        return "HTTP 200"


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def client(settings, transport):
    with TestClient(create_app(settings, llm=object(), transport=transport)) as test_client:
        yield sign_in(test_client)


def csv_bytes(*, rows: int = 400, regions: tuple[str, ...] = ("North", "South"),
              scale: float = 1.0) -> bytes:
    dates = pd.date_range(pd.Timestamp.utcnow().normalize().tz_localize(None), periods=rows,
                          freq="-1D")[::-1]
    frame = pd.DataFrame({
        "order_id": [f"A{i:05d}" for i in range(rows)],
        "order_date": dates,
        "region": [regions[i % len(regions)] for i in range(rows)],
        "revenue": [
            (100.0 if regions[i % len(regions)] == "North" else 100.0 * scale) + (i % 7)
            for i in range(rows)
        ],
    })
    return frame.to_csv(index=False).encode("utf-8")


def upload(client: TestClient, payload: bytes, replaces: str | None = None) -> dict[str, Any]:
    data = {"replaces": replaces} if replaces else None
    response = client.post("/api/datasets", files={"file": ("sales.csv", payload, "text/csv")},
                           data=data)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- drivers


def test_driver_options_and_drill_down(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())

    options = client.get(f"/api/datasets/{dataset['id']}/drivers/options").json()
    assert options["available"] is True
    assert options["defaults"]["measure"] == "revenue"
    assert "region" in options["dimensions"]

    response = client.post(f"/api/datasets/{dataset['id']}/drivers", json={"measure": "revenue"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["best_dimension"] == "region"
    assert result["headline"].startswith("revenue")
    assert len(result["charts"]) == 2
    assert result["follow_up"]
    shift = result["shift_share"]
    assert sum(t["value"] for t in shift["terms"]) == pytest.approx(shift["total"], rel=1e-6)


def test_drill_down_rejects_an_unknown_measure(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())
    response = client.post(f"/api/datasets/{dataset['id']}/drivers", json={"measure": "profit"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_drill_down_markdown_export(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())
    response = client.post(f"/api/datasets/{dataset['id']}/drivers/export.md", json={})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Why revenue moved")


def test_a_waterfall_can_be_pinned_to_a_board(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())
    board = client.post("/api/boards", json={"title": "Drivers"}).json()
    response = client.post(f"/api/boards/{board['id']}/items", json={
        "kind": "chart", "source": "drivers", "dataset_id": dataset["id"],
        "index": 0, "params": {"measure": "revenue"},
    })
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["kind"] == "chart"
    assert item["content"]["figure"]["data"][0]["type"] == "waterfall"
    assert item["dataset_name"] == dataset["name"]
    # The board keeps the snapshot, not a live query.
    assert client.get(f"/api/boards/{board['id']}").json()["items"][0]["id"] == item["id"]

    # A waterfall cannot be drawn statically, so the exporters rebuild it from the digest.
    assert item["content"]["digest"]["traces"][0]["y"]
    pdf = client.get(f"/api/boards/{board['id']}/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    pptx = client.get(f"/api/boards/{board['id']}/export.pptx")
    assert pptx.status_code == 200 and pptx.content[:2] == b"PK"


def test_signals_link_to_the_drill_down(client: TestClient) -> None:
    dataset = upload(client, csv_bytes(rows=500, scale=0.2))
    signals = dataset["profile"]["signals"]
    explained = [s for s in signals if s.get("explain")]
    assert explained, [s["kind"] for s in signals]
    assert explained[0]["explain"]["measure"] == "revenue"


# --------------------------------------------------------------------------- contracts


def test_contract_lifecycle_and_enforcement(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())

    empty = client.get(f"/api/datasets/{dataset['id']}/contract").json()
    assert empty["contract"]["expectations"] == []
    assert empty["suggested"] is True

    suggested = client.post(f"/api/datasets/{dataset['id']}/contract/suggest").json()
    assert len(suggested["expectations"]) > 3

    saved = client.put(f"/api/datasets/{dataset['id']}/contract", json=suggested)
    assert saved.status_code == 200, saved.text
    assert saved.json()["result"]["status"] == "pass"

    # Next month's export drops a column and introduces an unknown region.
    next_version = upload(client, csv_bytes(regions=("North", "Nordics")).replace(
        b"order_id,", b"order_ref,"), replaces=dataset["id"])
    result = next_version["contract_result"]
    assert result["status"] == "fail"
    assert any(f["column"] == "order_id" for f in result["failures"])
    assert next_version["contract"]["expectations"]  # inherited, like the semantic layer


def test_contract_recheck_and_markdown(client: TestClient) -> None:
    dataset = upload(client, csv_bytes())
    suggested = client.post(f"/api/datasets/{dataset['id']}/contract/suggest").json()
    client.put(f"/api/datasets/{dataset['id']}/contract", json=suggested)

    rechecked = client.post(f"/api/datasets/{dataset['id']}/contract/check").json()
    assert rechecked["result"]["status"] == "pass"
    assert rechecked["result"]["score"] == 100

    markdown = client.get(f"/api/datasets/{dataset['id']}/contract.md")
    assert markdown.status_code == 200
    assert "# Data contract" in markdown.text

    listed = client.get("/api/datasets").json()
    assert listed[0]["contract_status"] == "pass"


def test_a_broken_contract_alerts_the_configured_channel(
    client: TestClient, transport: RecordingTransport
) -> None:
    client.post("/api/alerts/channels", json={
        "kind": "slack", "target": "https://hooks.slack.com/services/x", "events": ["contract"],
    })
    dataset = upload(client, csv_bytes())
    suggested = client.post(f"/api/datasets/{dataset['id']}/contract/suggest").json()
    client.put(f"/api/datasets/{dataset['id']}/contract", json=suggested)

    upload(client, csv_bytes(rows=20), replaces=dataset["id"])  # far too few rows
    events = [alert.event for _, alert in transport.sent]
    assert "contract" in events
    assert transport.sent[-1][1].severity == "critical"


# --------------------------------------------------------------------------- alerts


def test_alert_channel_crud_over_http(client: TestClient) -> None:
    created = client.post("/api/alerts/channels", json={
        "kind": "webhook", "target": "https://hooks.example.com/a", "name": "Ops",
    })
    assert created.status_code == 201, created.text
    channel = created.json()
    assert channel["events"] == ["breach", "failure", "contract"]

    settings = client.get("/api/alerts").json()
    assert settings["enabled"] is True
    assert settings["email_configured"] is False
    assert len(settings["channels"]) == 1

    patched = client.patch(f"/api/alerts/channels/{channel['id']}", json={"events": ["digest"]})
    assert patched.json()["events"] == ["digest"]

    assert client.delete(f"/api/alerts/channels/{channel['id']}").status_code == 204
    assert client.get("/api/alerts/channels").json() == []


def test_test_send_reports_a_bad_webhook(client: TestClient) -> None:
    channel = client.post("/api/alerts/channels", json={
        "kind": "webhook", "target": "https://fail.example.com/hook",
    }).json()
    response = client.post(f"/api/alerts/channels/{channel['id']}/test")
    assert response.status_code == 422
    assert "Delivery failed" in response.json()["error"]["message"]

    deliveries = client.get("/api/alerts/deliveries").json()
    assert deliveries[0]["status"] == "failed"


def test_digest_is_pushed_on_demand(client: TestClient, transport: RecordingTransport) -> None:
    client.post("/api/alerts/channels", json={
        "kind": "slack", "target": "https://hooks.slack.com/services/x", "events": ["digest"],
    })
    response = client.post("/api/alerts/digest")
    assert response.status_code == 200
    assert response.json()["sent"] == 1
    assert transport.sent[-1][1].event == "digest"


def test_invalid_channel_is_rejected_with_a_typed_error(client: TestClient) -> None:
    response = client.post("/api/alerts/channels", json={"kind": "email", "target": "nope"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
