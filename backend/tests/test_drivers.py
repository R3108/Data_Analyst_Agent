"""Driver analysis: the decomposition must reconcile exactly, or it is worthless."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services.drivers import default_aggregation, driver_options, explain, explain_markdown
from app.services.profiling import profile_dataframe


def make_frame(
    *,
    days: int = 800,
    regions: tuple[str, ...] = ("North", "South", "East"),
    seed: int = 7,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=days, freq="D")
    rows = []
    for day in dates:
        for region in regions:
            # South collapses in the most recent year; North grows.
            recent = day >= dates[-1] - pd.Timedelta(days=364)
            base = {"North": 100.0, "South": 100.0, "East": 60.0}[region]
            if recent:
                base *= {"North": 1.4, "South": 0.4, "East": 1.0}[region]
            rows.append({
                "order_date": day,
                "region": region,
                "channel": "Online" if (day.dayofyear + len(region)) % 2 else "Retail",
                "revenue": float(base + rng.normal(0, 3)),
                "margin_pct": float(0.3 + rng.normal(0, 0.01)),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture(scope="module")
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


def test_options_pick_sensible_defaults(profile: dict) -> None:
    options = driver_options(profile)
    assert options["available"] is True
    assert options["defaults"]["measure"] == "revenue"
    assert options["defaults"]["date_column"] == "order_date"
    assert options["defaults"]["aggregation"] == "sum"
    assert "region" in options["dimensions"]


def test_non_additive_measures_are_averaged() -> None:
    assert default_aggregation("margin_pct") == "mean"
    assert default_aggregation("revenue") == "sum"


def test_contributions_sum_to_the_total_change(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue")
    total = result["total"]["change"]
    for breakdown in result["dimensions"]:
        # "Other" buckets are folded in, so summing the contributors still closes.
        assert sum(c["change"] for c in breakdown["contributors"]) == pytest.approx(total, rel=1e-6)


def test_shift_share_terms_reconcile(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue")
    for breakdown in result["dimensions"]:
        shift = breakdown["shift_share"]
        assert shift["closes"] is True
        assert sum(t["value"] for t in shift["terms"]) == pytest.approx(shift["total"], rel=1e-6)
        assert {t["key"] for t in shift["terms"]} == {"volume", "mix", "rate"}


def test_mean_aggregation_drops_the_volume_term(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="margin_pct", aggregation="mean")
    assert result["aggregation"] == "mean"
    shift = result["shift_share"]
    assert {t["key"] for t in shift["terms"]} == {"mix", "rate"}
    assert sum(t["value"] for t in shift["terms"]) == pytest.approx(shift["total"], abs=1e-9)
    assert any("averaged, not summed" in c for c in result["caveats"])


def test_the_collapsing_segment_is_named_as_the_driver(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue", period="yoy")
    assert result["best_dimension"] == "region"
    top = result["dimensions"][0]["contributors"][0]
    assert top["label"] == "South"
    assert top["change"] < 0
    assert top["share_change"] < 0  # it lost share of revenue too
    assert "South" in " ".join(result["narrative"])


def test_dimension_ranking_prefers_the_differentiated_one(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue")
    scores = {d["column"]: d["score"] for d in result["dimensions"]}
    # Channel splits evenly, so its categories move in proportion and it scores near zero.
    assert scores["region"] > scores["channel"]
    assert result["dimensions"] == sorted(result["dimensions"], key=lambda d: -d["score"])


def test_new_and_lost_categories_are_surfaced() -> None:
    frame = make_frame(days=800)
    # "West" only exists in the most recent 100 days.
    tail = frame[frame["order_date"] >= frame["order_date"].max() - pd.Timedelta(days=99)].copy()
    tail["region"] = "West"
    tail["revenue"] = 500.0
    combined = pd.concat([frame, tail], ignore_index=True)
    result = explain(combined, profile_dataframe(combined), measure="revenue", period="yoy")
    region = next(d for d in result["dimensions"] if d["column"] == "region")
    assert [c["label"] for c in region["gained"]] == ["West"]
    assert any("New in this period" in line for line in result["narrative"])


def test_focus_overrides_the_ranking_without_hiding_it(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue", focus="channel")
    assert result["best_dimension"] == "channel"
    assert result["shift_share"]["dimension"] == "channel"
    assert result["tables"][0]["title"].endswith("by channel")
    # region still scores higher and is still offered.
    assert result["dimensions"][0]["column"] == "region"
    assert "region" in " ".join(result["narrative"])


def test_an_unknown_focus_falls_back_to_the_best(frame: pd.DataFrame, profile: dict) -> None:
    assert explain(frame, profile, measure="revenue", focus="nope")["best_dimension"] == "region"


def test_custom_windows_are_honoured(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(
        frame, profile, measure="revenue",
        baseline_start="2023-01-01", baseline_end="2023-06-30",
        current_start="2023-07-01", current_end="2023-12-31",
    )
    assert result["period"]["mode"] == "custom"
    assert result["period"]["baseline"]["start"].startswith("2023-01-01")
    assert result["period"]["current"]["end"].startswith("2023-12-31")


def test_partial_custom_window_is_rejected(frame: pd.DataFrame, profile: dict) -> None:
    with pytest.raises(InvalidInputError, match="all four dates"):
        explain(frame, profile, measure="revenue", baseline_start="2023-01-01")


def test_short_history_falls_back_to_halves() -> None:
    frame = make_frame(days=20)
    result = explain(frame, profile_dataframe(frame), measure="revenue", period="yoy")
    assert result["period"]["mode"] == "halves"
    assert any("split in half" in c for c in result["caveats"])


def test_periods_without_enough_rows_are_refused() -> None:
    frame = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-06-01"]),
        "region": ["North", "South"],
        "revenue": [10.0, 20.0],
    })
    with pytest.raises(InvalidInputError, match="at least"):
        explain(frame, profile_dataframe(frame), measure="revenue")


def test_unknown_columns_are_rejected(frame: pd.DataFrame, profile: dict) -> None:
    with pytest.raises(InvalidInputError, match="not in this dataset"):
        explain(frame, profile, measure="nope")
    with pytest.raises(InvalidInputError, match="not in this dataset"):
        explain(frame, profile, measure="revenue", dimensions=["nope"])


def test_text_measure_is_rejected(frame: pd.DataFrame, profile: dict) -> None:
    with pytest.raises(InvalidInputError, match="not numeric"):
        explain(frame, profile, measure="region", date_column="order_date")


def test_rows_missing_the_date_are_excluded_and_disclosed(profile: dict) -> None:
    frame = make_frame()
    frame.loc[frame.index[:200], "order_date"] = pd.NaT
    result = explain(frame, profile_dataframe(frame), measure="revenue")
    assert any("excluded" in c for c in result["caveats"])


def test_charts_and_tables_use_the_shared_output_shapes(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue")
    assert len(result["charts"]) == 2
    for chart in result["charts"]:
        assert set(chart) == {"title", "caption", "figure", "digest"}
        trace = chart["figure"]["data"][0]
        assert trace["type"] == "waterfall"
        assert len(trace["x"]) == len(trace["y"]) == len(trace["measure"])
        assert trace["measure"][0] == "absolute" and trace["measure"][-1] == "total"
        # The digest keeps only the contributions — the anchors are not a bar chart.
        digest = chart["digest"]["traces"][0]
        assert digest["x_count"] == len(trace["x"]) - 2
        assert len(digest["x"]) == len(digest["y"]) == digest["y_count"]
    table = result["tables"][0]
    assert [c["name"] for c in table["columns"]][0] == "region"
    assert len(table["rows"]) == table["total_rows"]
    assert all(len(row) == len(table["columns"]) for row in table["rows"])


def test_markdown_export_contains_the_numbers(frame: pd.DataFrame, profile: dict) -> None:
    result = explain(frame, profile, measure="revenue")
    markdown = explain_markdown(result, "Retail")
    assert markdown.startswith("# Why revenue moved")
    assert "Retail" in markdown
    assert "## revenue by region" in markdown
    assert "no model call" in markdown


def test_result_is_json_serialisable(frame: pd.DataFrame, profile: dict) -> None:
    import json

    result = explain(frame, profile, measure="revenue")
    assert json.loads(json.dumps(result))["measure"] == "revenue"
