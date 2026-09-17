"""The semantic layer (binding metric definitions) and dataset version lineage."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import semantics as semantics_lib
from app.services.versions import diff_datasets
from tests.conftest import REPORT, ScriptedLLM, make_plan

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
table(by_region, title="Revenue by region")
"""

V2_CSV = (
    "Region,Revenue,Order Date\n"
    "North,2000,2024-05-01\n"
    "South,1500,2024-06-01\n"
    "East,500,2024-07-01\n"
    "West,1000,2024-08-01\n"
).encode("utf-8")


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM({
        "plan": [make_plan()],
        "code": [{"approach": "group", "code": CODE}],
        "report": [REPORT],
    })


@pytest.fixture
def client(settings, llm):
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield test_client


# ------------------------------------------------------------------ normalisation


def test_normalize_keeps_usable_entries_and_drops_junk():
    result = semantics_lib.normalize({
        "metrics": [
            {"name": "  Net revenue ", "definition": "Gross  sales minus refunds", "format": "currency"},
            {"name": "Net Revenue", "definition": "duplicate name, ignored"},
            {"name": "", "definition": "no name"},
            {"name": "Churn", "definition": ""},
            {"name": "Margin", "definition": "Profit over revenue", "format": "nonsense"},
            "not a dict",
        ],
        "rules": ["Exclude test orders", "  ", "Exclude test orders"],
        "glossary": [{"term": "AOV", "definition": "Average order value"}, {"term": "x"}],
    })
    assert [m["name"] for m in result["metrics"]] == ["Net revenue", "Margin"]
    assert result["metrics"][0]["definition"] == "Gross sales minus refunds"
    assert result["metrics"][0]["format"] == "currency"
    assert result["metrics"][1]["format"] == "auto"  # unknown formats fall back
    assert result["rules"] == ["Exclude test orders"]
    assert result["glossary"] == [{"term": "AOV", "definition": "Average order value"}]


def test_normalize_tolerates_garbage_input():
    for value in (None, [], "text", 42):
        assert semantics_lib.normalize(value) == {"metrics": [], "rules": [], "glossary": []}


def test_semantic_context_is_empty_until_something_is_defined():
    assert semantics_lib.semantic_context(None) == ""
    assert semantics_lib.semantic_context({"metrics": [], "rules": [], "glossary": []}) == ""

    rendered = semantics_lib.semantic_context({
        "metrics": [{"name": "Net revenue", "definition": "Gross sales minus refunds",
                     "format": "currency"}],
        "rules": ["The fiscal year starts in April"],
        "glossary": [{"term": "AOV", "definition": "Average order value"}],
    })
    assert "authoritative" in rendered
    assert "Net revenue: Gross sales minus refunds" in rendered
    assert "The fiscal year starts in April" in rendered
    assert "AOV: Average order value" in rendered


def test_mentioned_metrics_matches_whole_words_only():
    semantics = {"metrics": [{"name": "Churn", "definition": "Cancellations over active"}],
                 "rules": [], "glossary": []}
    assert semantics_lib.mentioned_metrics(semantics, "What is churn by plan?")
    assert not semantics_lib.mentioned_metrics(semantics, "Explain churning butter")


# ------------------------------------------------------------------ API + prompt injection


def test_definitions_round_trip_and_reach_the_model(client, llm, tiny_csv_bytes):
    dataset = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
    assert dataset["semantics"] is None

    saved = client.put(f"/api/datasets/{dataset['id']}/semantics", json={
        "metrics": [{"name": "Net revenue", "definition": "Revenue minus refunds", "format": "currency"}],
        "rules": ["Exclude cancelled orders"],
        "glossary": [],
    })
    assert saved.status_code == 200
    assert saved.json()["metrics"][0]["name"] == "Net revenue"
    assert client.get(f"/api/datasets/{dataset['id']}/semantics").json()["rules"] == [
        "Exclude cancelled orders"
    ]

    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                       json={"message": "What is net revenue?"}) as response:
        response.read()

    # The schema card stays its own cacheable block; definitions arrive as an extra one.
    system_blocks = llm.calls[0]["system"]
    assert len(system_blocks) == 3
    assert "BUSINESS DEFINITIONS" in system_blocks[2]["text"]
    assert "Net revenue: Revenue minus refunds" in system_blocks[2]["text"]


def test_definitions_are_validated_not_trusted(client, tiny_csv_bytes):
    dataset = client.post("/api/datasets", files={"file": ("s.csv", tiny_csv_bytes, "text/csv")}).json()
    saved = client.put(f"/api/datasets/{dataset['id']}/semantics",
                       json={"metrics": "not a list", "rules": None}).json()
    assert saved == {"metrics": [], "rules": [], "glossary": []}
    assert client.put("/api/datasets/ds_missing/semantics", json={}).status_code == 404


# ------------------------------------------------------------------ versions


def test_diff_datasets_summarises_what_changed():
    previous = {
        "id": "ds_1", "version": 1,
        "profile": {
            "n_rows": 100,
            "columns": [
                {"name": "Region", "dtype": "text", "role": "dimension"},
                {"name": "Revenue", "dtype": "decimal", "role": "measure", "stats": {"sum": 1000.0}},
                {"name": "Legacy", "dtype": "text", "role": "text"},
            ],
            "roles": {"measure": ["Revenue"]},
            "date_range": {"column": "Order Date", "start": "2024-01-01", "end": "2024-04-01"},
        },
        "cleaning": {"quality_score": 90},
    }
    current = {
        "id": "ds_2", "version": 2,
        "profile": {
            "n_rows": 150,
            "columns": [
                {"name": "Region", "dtype": "text", "role": "dimension"},
                {"name": "Revenue", "dtype": "decimal", "role": "measure", "stats": {"sum": 1500.0}},
                {"name": "Channel", "dtype": "text", "role": "dimension"},
            ],
            "roles": {"measure": ["Revenue"]},
            "date_range": {"column": "Order Date", "start": "2024-01-01", "end": "2024-08-01"},
        },
        "cleaning": {"quality_score": 96},
    }

    diff = diff_datasets(previous, current)
    assert diff["rows"] == {"previous": 100, "current": 150, "change": 50, "change_pct": 0.5}
    assert diff["columns_added"] == ["Channel"]
    assert diff["columns_removed"] == ["Legacy"]
    assert diff["measures"][0] == {"column": "Revenue", "previous_total": 1000.0,
                                   "current_total": 1500.0, "change_pct": 0.5}
    assert diff["quality"]["change"] == 6
    assert diff["coverage"]["extended"] is True
    assert "Schema and data changed" in diff["headline"]
    assert any("Channel" in note for note in diff["notable"])


def test_diff_of_identical_profiles_is_quiet():
    record = {
        "id": "ds_1", "version": 1,
        "profile": {"n_rows": 10, "columns": [], "roles": {}, "date_range": None},
        "cleaning": {"quality_score": 100},
    }
    diff = diff_datasets(record, {**record, "id": "ds_2", "version": 2})
    assert diff["notable"] == []
    assert diff["headline"] == "No material changes from the previous version."


def test_uploading_a_new_version_builds_lineage_and_inherits_definitions(client, tiny_csv_bytes):
    first = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
    client.put(f"/api/datasets/{first['id']}/semantics", json={
        "metrics": [{"name": "Net revenue", "definition": "Revenue minus refunds"}],
        "rules": [], "glossary": [],
    })

    second = client.post(
        "/api/datasets",
        files={"file": ("sales-may.csv", V2_CSV, "text/csv")},
        data={"replaces": first["id"]},
    )
    assert second.status_code == 201, second.text
    v2 = second.json()

    assert v2["version"] == 2
    assert v2["parent_dataset_id"] == first["id"]
    assert v2["root_dataset_id"] == first["id"]
    # Definitions carry across versions; re-entering them per upload would be absurd.
    assert v2["semantics"]["metrics"][0]["name"] == "Net revenue"
    assert v2["version_diff"]["rows"] == {"previous": 4, "current": 4, "change": 0, "change_pct": 0.0}
    assert "West" not in str(v2["version_diff"]["columns_added"])

    versions = client.get(f"/api/datasets/{v2['id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["version_diff"] is None
    assert versions[1]["version_diff"]["current_version"] == 2

    # The dataset list shows one entry per lineage — the newest version.
    listed = client.get("/api/datasets").json()
    assert [d["id"] for d in listed] == [v2["id"]]
    assert listed[0]["version_count"] == 2


def test_cleaned_data_can_be_downloaded_for_reproducibility(client, tiny_csv_bytes):
    dataset = client.post("/api/datasets", files={"file": ("s.csv", tiny_csv_bytes, "text/csv")}).json()

    csv = client.get(f"/api/datasets/{dataset['id']}/download")
    assert csv.status_code == 200
    assert "attachment" in csv.headers["content-disposition"]
    assert "Region" in csv.text and "North" in csv.text

    parquet = client.get(f"/api/datasets/{dataset['id']}/download?format=parquet")
    assert parquet.status_code == 200 and parquet.content[:4] == b"PAR1"

    assert client.get(f"/api/datasets/{dataset['id']}/download?format=xls").status_code == 422
