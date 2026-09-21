from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan, set_setting, sign_in

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
chart(px.bar(by_region, x="Region", y="Revenue"), title="Revenue by region")
table(by_region, title="Revenue by region")
"""


@pytest.fixture
def client(settings):
    llm = ScriptedLLM({"plan": [make_plan()], "code": [{"approach": "group", "code": CODE}], "report": [REPORT]})
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield sign_in(test_client)


@pytest.fixture
def analysed(client, tiny_csv_bytes):
    dataset = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat", json={"message": "Revenue by region?"}) as r:
        r.read()
    messages = client.get(f"/api/sessions/{session['id']}").json()["messages"]
    return session, messages[-1]


def test_assistant_messages_record_usage_and_cost(client, analysed):
    _, message = analysed
    usage = message["payload"]["usage"]
    assert usage["calls"] == 3  # plan, code, report
    assert usage["cost_usd"] == pytest.approx(3 * (10_000 * 0.2 + 1_000 * 1.2) / 1_000_000)
    report = client.get("/api/usage?days=7").json()
    assert report["analyses"] == 1 and report["cost_usd"] == pytest.approx(usage["cost_usd"], abs=1e-4)
    assert report["by_day"][0]["analyses"] == 1
    assert report["budget"]["limit_usd"] == 5.0
    assert report["budget"]["spent_usd"] == pytest.approx(usage["cost_usd"], abs=1e-4)
    assert report["budget"]["exhausted"] is False


def test_monthly_budget_blocks_new_model_calls(client, analysed):
    session, _ = analysed
    set_setting(client, ai_monthly_budget_usd=0.001)

    response = client.post(f"/api/sessions/{session['id']}/chat", json={"message": "Analyse this again"})

    assert response.status_code == 402
    error = response.json()["error"]
    assert error["code"] == "llm_budget_exceeded"
    assert error["details"]["budget"]["exhausted"] is True
    messages = client.get(f"/api/sessions/{session['id']}").json()["messages"]
    assert len(messages) == 2  # the rejected question is not persisted


def test_board_pinning_ordering_and_notes(client, analysed):
    session, message = analysed
    board = client.post("/api/boards", json={"title": "Exec dashboard"}).json()
    base = f"/api/boards/{board['id']}"

    kpi = client.post(f"{base}/items", json={"kind": "kpi", "message_id": message["id"], "index": 0}).json()
    chart = client.post(f"{base}/items", json={"kind": "chart", "message_id": message["id"], "index": 0}).json()
    insight = client.post(f"{base}/items", json={"kind": "insight", "message_id": message["id"], "index": 0}).json()
    note = client.post(f"{base}/items", json={"kind": "note", "text": "Review **weekly**."}).json()

    assert kpi["title"] == "Total revenue" and kpi["content"]["value"] == 1750.0
    assert chart["source_question"] == "Revenue by region?" and chart["source_session_id"] == session["id"]
    # The digest rides along: the board's PDF and PPTX exporters rebuild the chart from it.
    assert chart["content"]["figure"]["data"] and chart["content"]["digest"]["traces"]
    assert insight["content"]["title"] == "North leads"

    bad_index = client.post(f"{base}/items", json={"kind": "table", "message_id": message["id"], "index": 9})
    assert bad_index.status_code == 422
    assert client.post(f"{base}/items", json={"kind": "note", "text": "  "}).status_code == 422

    updated = client.patch(f"{base}/items/{chart['id']}", json={"wide": True, "title": "Regions"}).json()
    assert updated["wide"] is True and updated["title"] == "Regions"
    assert client.patch(f"{base}/items/{kpi['id']}", json={"text": "x"}).status_code == 422

    order = [note["id"], insight["id"], chart["id"], kpi["id"]]
    reordered = client.put(f"{base}/order", json={"item_ids": order}).json()
    assert [i["id"] for i in reordered["items"]] == order
    assert client.put(f"{base}/order", json={"item_ids": order[:2]}).status_code == 422

    assert client.delete(f"{base}/items/{note['id']}").status_code == 204
    summary = next(b for b in client.get("/api/boards").json() if b["id"] == board["id"])
    assert summary["item_count"] == 3

    # snapshots survive deletion of the source analysis
    client.delete(f"/api/sessions/{session['id']}")
    assert len(client.get(base).json()["items"]) == 3


def test_share_links_for_sessions_and_boards(client, analysed):
    session, _ = analysed
    token = client.post(f"/api/sessions/{session['id']}/share").json()["token"]
    assert client.post(f"/api/sessions/{session['id']}/share").json()["token"] == token  # idempotent

    shared = client.get(f"/api/share/{token}").json()
    assert shared["type"] == "session" and shared["title"] == "Revenue by region?"
    assert [m["role"] for m in shared["messages"]] == ["user", "assistant"]
    assert "session_id" not in shared["messages"][0]

    board = client.post("/api/boards", json={"title": "Public board"}).json()
    board_token = client.post(f"/api/boards/{board['id']}/share").json()["token"]
    assert client.get(f"/api/share/{board_token}").json()["type"] == "board"

    assert client.delete(f"/api/sessions/{session['id']}/share").status_code == 204
    revoked = client.get(f"/api/share/{token}")
    assert revoked.status_code == 404 and revoked.json()["error"]["code"] == "not_found"


def test_database_migrates_older_schema(tmp_path):
    """A database written by an older version must open, keep its rows and gain the new shape.

    The tables below are the *complete* pre-versioning schema on purpose: indexes over
    newly added columns must not be created before the migration adds those columns.
    """
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE datasets (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                               original_filename TEXT NOT NULL, file_type TEXT NOT NULL,
                               sheet_name TEXT, n_rows INTEGER NOT NULL, n_cols INTEGER NOT NULL,
                               size_bytes INTEGER NOT NULL, profile_json TEXT NOT NULL,
                               cleaning_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, dataset_id TEXT NOT NULL,
                               created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO datasets VALUES ('ds_legacy', 'Legacy', 'legacy.csv', 'csv', NULL, 10, 2, 99,
                                     '{"columns": []}', '{"quality_score": 100}', '2024-01-01T00:00:00');
    """)
    conn.commit()
    conn.close()

    db = Database(path)
    with db.connect() as c:
        session_columns = {row["name"] for row in c.execute("PRAGMA table_info(sessions)")}
        dataset_columns = {row["name"] for row in c.execute("PRAGMA table_info(datasets)")}
        tables = {row["name"] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row["name"] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert "share_token" in session_columns
    assert {"parent_dataset_id", "root_dataset_id", "version", "semantics_json",
            "version_diff_json"} <= dataset_columns
    assert {"boards", "board_items", "monitors", "monitor_runs"} <= tables
    assert {"idx_datasets_lineage", "idx_monitors_dataset", "idx_monitor_runs_monitor"} <= indexes

    # The existing row survives and becomes the root of its own version history.
    legacy = db.get_dataset("ds_legacy")
    assert legacy["version"] == 1
    assert legacy["root_dataset_id"] == "ds_legacy"
    assert legacy["semantics"] is None
    assert db.dataset_versions("ds_legacy")[0]["id"] == "ds_legacy"
    assert db.latest_dataset_version("ds_legacy")["id"] == "ds_legacy"


def test_dataset_detail_includes_signals(client):
    dataset = client.post("/api/datasets/sample").json()
    assert dataset["profile"]["signals"]
    detail = client.get(f"/api/datasets/{dataset['id']}").json()
    assert json.dumps(detail["profile"]["signals"]) == json.dumps(dataset["profile"]["signals"])
