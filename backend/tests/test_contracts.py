"""Data contracts: expectations suggested from a profile, enforced on every later upload."""

from __future__ import annotations

import pandas as pd
import pytest

from app.services import contracts
from app.services.profiling import profile_dataframe


def make_frame(rows: int = 60) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id": [f"A{i:04d}" for i in range(rows)],
        "region": ["North", "South", "East", "West"] * (rows // 4),
        "revenue": [100.0 + i for i in range(rows)],
        "order_date": pd.date_range(pd.Timestamp.utcnow().normalize().tz_localize(None),
                                    periods=rows, freq="-1D")[::-1],
    })


@pytest.fixture
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


@pytest.fixture
def contract(profile: dict) -> dict:
    return contracts.suggest(profile)


def kinds(contract: dict, column: str) -> set[str]:
    return {e["kind"] for e in contract["expectations"] if e["column"] == column}


def test_suggestions_cover_the_obvious_promises(contract: dict) -> None:
    assert "schema" in kinds(contract, "revenue")
    assert "not_null" in kinds(contract, "revenue")
    assert "range" in kinds(contract, "revenue")          # currently non-negative
    assert "allowed_values" in kinds(contract, "region")  # small, closed category set
    assert "unique" in kinds(contract, "order_id")
    assert "freshness" in kinds(contract, "order_date")
    assert any(e["kind"] == "row_count" for e in contract["expectations"])


def test_a_suggested_contract_passes_against_its_own_data(
    contract: dict, frame: pd.DataFrame, profile: dict
) -> None:
    result = contracts.evaluate(contract, frame, profile)
    assert result["status"] == "pass", [r for r in result["results"] if r["status"] != "pass"]
    assert result["score"] == 100
    assert result["counts"]["fail"] == 0
    assert "All" in result["headline"]


def test_a_dropped_column_fails_the_gate(contract: dict, frame: pd.DataFrame, profile: dict) -> None:
    result = contracts.evaluate(contract, frame.drop(columns=["revenue"]), profile)
    assert result["status"] == "fail"
    failed = [r for r in result["results"] if r["status"] == "fail"]
    assert any(r["kind"] == "schema" and r["column"] == "revenue" for r in failed)
    assert "missing" in next(r["detail"] for r in failed if r["kind"] == "schema")


def test_a_new_category_is_caught(contract: dict, frame: pd.DataFrame, profile: dict) -> None:
    changed = frame.copy()
    changed.loc[changed.index[0], "region"] = "Nordics"
    result = contracts.evaluate(contract, changed, profile)
    unexpected = next(r for r in result["results"] if r["kind"] == "allowed_values")
    assert unexpected["status"] == "warn"
    assert "Nordics" in unexpected["detail"]
    assert result["status"] == "warn"


def test_negative_values_break_the_range(contract: dict, frame: pd.DataFrame, profile: dict) -> None:
    changed = frame.copy()
    changed.loc[changed.index[:3], "revenue"] = -5.0
    result = contracts.evaluate(contract, changed, profile)
    row = next(r for r in result["results"] if r["kind"] == "range")
    assert row["status"] == "warn"
    assert "3 value(s) outside" in row["detail"]


def test_new_nulls_break_the_completeness_promise(
    contract: dict, frame: pd.DataFrame, profile: dict
) -> None:
    changed = frame.copy()
    changed.loc[changed.index[:30], "revenue"] = None
    row = next(
        r for r in contracts.evaluate(contract, changed, profile)["results"]
        if r["kind"] == "not_null" and r["column"] == "revenue"
    )
    assert row["status"] == "warn"
    assert "50.00%" in row["detail"]


def test_duplicate_identifiers_fail(contract: dict, frame: pd.DataFrame, profile: dict) -> None:
    changed = frame.copy()
    changed.loc[changed.index[1], "order_id"] = changed.loc[changed.index[0], "order_id"]
    row = next(r for r in contracts.evaluate(contract, changed, profile)["results"]
               if r["kind"] == "unique")
    assert row["status"] == "fail"
    assert "1 duplicate" in row["detail"]


def test_a_truncated_file_fails_the_row_floor(
    contract: dict, frame: pd.DataFrame, profile: dict
) -> None:
    row = next(r for r in contracts.evaluate(contract, frame.head(4), profile)["results"]
               if r["kind"] == "row_count")
    assert row["status"] == "fail"
    assert "below the agreed floor" in row["detail"]


def test_stale_data_trips_freshness(profile: dict) -> None:
    contract = contracts.normalize({"expectations": [
        {"kind": "freshness", "column": "order_date", "params": {"max_age_days": 7}},
    ]})
    stale = make_frame()
    stale["order_date"] = stale["order_date"] - pd.Timedelta(days=400)
    row = contracts.evaluate(contract, stale, profile)["results"][0]
    assert row["status"] == "warn"
    assert "past the 7-day limit" in row["detail"]


def test_type_drift_is_reported(frame: pd.DataFrame, profile: dict) -> None:
    contract = contracts.normalize({"expectations": [
        {"kind": "schema", "column": "revenue", "params": {"dtype": "decimal"}, "severity": "fail"},
    ]})
    changed = frame.copy()
    changed["revenue"] = changed["revenue"].astype(str)
    row = contracts.evaluate(contract, changed, profile)["results"][0]
    assert row["status"] == "fail"
    assert "decimal to text" in row["detail"]


def test_normalisation_drops_nonsense_and_dedupes() -> None:
    contract = contracts.normalize({"expectations": [
        {"kind": "not_a_kind", "column": "x"},
        {"kind": "schema"},                                   # no column
        {"kind": "range", "column": "x", "params": {}},       # no bounds
        {"kind": "range", "column": "x", "params": {"min": 10, "max": 1}},  # inverted
        {"kind": "allowed_values", "column": "x", "params": {"values": []}},
        {"kind": "schema", "column": "x", "params": {"dtype": "integer"}},
        {"kind": "schema", "column": "x", "params": {"dtype": "text"}},     # duplicate id
        "not a dict",
    ]})
    assert [e["kind"] for e in contract["expectations"]] == ["schema"]
    assert contract["expectations"][0]["params"]["dtype"] == "integer"


def test_severity_defaults_reflect_blast_radius() -> None:
    contract = contracts.normalize({"expectations": [
        {"kind": "schema", "column": "x", "params": {"dtype": "text"}},
        {"kind": "not_null", "column": "x", "params": {"max_missing_pct": 1}},
        {"kind": "not_null", "column": "y", "params": {"max_missing_pct": 1}, "severity": "fail"},
    ]})
    severities = {(e["kind"], e["column"]): e["severity"] for e in contract["expectations"]}
    assert severities[("schema", "x")] == "fail"
    assert severities[("not_null", "x")] == "warn"
    assert severities[("not_null", "y")] == "fail"


def test_disabled_expectations_are_skipped(frame: pd.DataFrame, profile: dict) -> None:
    contract = contracts.normalize({"expectations": [
        {"kind": "row_count", "column": None, "params": {"min": 10_000}, "enabled": False},
    ]})
    assert contracts.evaluate(contract, frame, profile)["status"] == "empty"


def test_an_empty_contract_is_not_a_failure(frame: pd.DataFrame, profile: dict) -> None:
    result = contracts.evaluate(None, frame, profile)
    assert result["status"] == "empty"
    assert result["score"] is None
    assert result["results"] == []


def test_descriptions_are_human_readable(contract: dict) -> None:
    described = {e["kind"]: e["description"] for e in contract["expectations"]}
    assert described["unique"] == "`order_id` has no duplicate values"
    assert described["range"].startswith("`revenue` is never below")
    assert "rows" in described["row_count"]


def test_markdown_renders_the_verdict(contract: dict, frame: pd.DataFrame, profile: dict) -> None:
    result = contracts.evaluate(contract, frame, profile)
    markdown = contracts.contract_markdown(contract, result, "Retail")
    assert markdown.startswith("# Data contract — Retail")
    assert "✅" in markdown
    assert "| Status | Expectation | Observed |" in markdown
