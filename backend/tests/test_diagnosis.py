"""Root cause on breach: the alert has to name a cause, not just a number."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.services.diagnosis import alert_facts, diagnose
from app.services.profiling import profile_dataframe


def make_frame(seed: int = 3) -> pd.DataFrame:
    """South collapses in the most recent year; North grows. Revenue falls overall."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=800, freq="D")
    rows = []
    for day in dates:
        recent = day >= dates[-1] - pd.Timedelta(days=364)
        for region in ("North", "South", "East"):
            base = {"North": 100.0, "South": 300.0, "East": 60.0}[region]
            if recent:
                base *= {"North": 1.1, "South": 0.3, "East": 1.0}[region]
            rows.append({
                "order_date": day,
                "region": region,
                "channel": "Online" if (day.dayofyear + len(region)) % 2 else "Retail",
                "revenue": float(base + rng.normal(0, 3)),
                "units": float(10 + rng.normal(0, 1)),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture(scope="module")
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


def monitor(**overrides) -> dict:
    base = {
        "id": "mon_1",
        "title": "Total revenue",
        "kpi_label": "Total revenue",
        "question": "What is total revenue this year?",
        "code": 'kpi("Total revenue", df["revenue"].sum(), format="currency")',
    }
    base.update(overrides)
    return base


def test_the_collapsing_segment_is_named(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(), profile, frame)
    assert result["status"] == "ok"
    assert result["measure"] == "revenue"
    assert result["dimension"] == "region"
    assert result["contributors"][0]["label"] == "South"
    assert result["contributors"][0]["change"] < 0
    assert "South" in result["summary"]


def test_the_measure_is_matched_from_the_kpi_label(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(kpi_label="Units shipped", title="Units shipped",
                              question="How many units did we ship?",
                              code='kpi("Units shipped", df["units"].sum())'), profile, frame)
    assert result["measure"] == "units"
    assert result["matched_on"] == "the KPI's name"


def test_the_code_is_the_last_resort_for_matching(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(kpi_label="Headline figure", title="Headline figure",
                              question="Give me the headline figure.",
                              code='kpi("Headline figure", df["units"].sum())'), profile, frame)
    assert result["measure"] == "units"
    assert result["matched_on"] == "the analysis code"


def test_an_explicit_measure_overrides_the_guess(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(), profile, frame, measure="units")
    assert result["measure"] == "units"


def test_the_summary_fits_in_an_alert(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(), profile, frame)
    assert len(result["summary"]) < 400
    assert result["summary"].count("\n") == 0


def test_alert_facts_name_the_driver(frame: pd.DataFrame, profile: dict) -> None:
    facts = alert_facts(diagnose(monitor(), profile, frame))
    labels = [label for label, _ in facts]
    assert labels[0] == "Most likely driver"
    assert any(label.startswith("Biggest mover") for label in labels)
    assert any("South" in value for _, value in facts)


def test_alert_facts_are_empty_when_there_is_no_diagnosis() -> None:
    assert alert_facts(None) == []
    assert alert_facts({"status": "unavailable", "reason": "no dates"}) == []


def test_the_params_reopen_the_full_drill_down(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(), profile, frame)
    assert result["params"]["measure"] == "revenue"
    assert result["params"]["focus"] == result["dimension"]


def test_a_dataset_with_no_dates_degrades_gracefully() -> None:
    frame = pd.DataFrame({"region": ["North", "South"] * 30, "revenue": list(range(60))})
    result = diagnose(monitor(), profile_dataframe(frame), frame)
    assert result["status"] == "unavailable"
    assert result["reason"]
    assert result["contributors"] == []


def test_a_dataset_with_no_measure_degrades_gracefully() -> None:
    frame = pd.DataFrame({
        "order_date": pd.date_range("2024-01-01", periods=60, freq="D"),
        "region": ["North", "South"] * 30,
    })
    result = diagnose(monitor(), profile_dataframe(frame), frame)
    assert result["status"] == "unavailable"


def test_a_failing_drill_down_never_raises() -> None:
    """Too few rows per period: drivers refuses, and the diagnosis reports rather than throws."""
    frame = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-01"]),
        "region": ["North", "South", "East"],
        "revenue": [10.0, 20.0, 30.0],
        "channel": ["Online", "Retail", "Online"],
    })
    result = diagnose(monitor(), profile_dataframe(frame), frame)
    assert result["status"] == "unavailable"
    assert result["reason"]


def test_result_is_json_serialisable(frame: pd.DataFrame, profile: dict) -> None:
    result = diagnose(monitor(), profile, frame)
    assert json.loads(json.dumps(result))["measure"] == "revenue"
