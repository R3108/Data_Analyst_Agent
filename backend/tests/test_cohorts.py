"""Cohort retention: the grid has to respect censoring, or it measures the calendar."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services.cohorts import (
    MAX_COHORT_ROWS,
    analyze,
    cohort_markdown,
    cohort_options,
    suggest_granularity,
)
from app.services.profiling import profile_dataframe


def make_frame(
    *,
    months: int = 18,
    per_month: int = 30,
    survival: float = 0.6,
    end: str = "2024-06-30",
    seed: int = 5,
) -> pd.DataFrame:
    """Customers join each month and stay active with probability `survival` per month."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    identifier = 0
    last = pd.Timestamp(end)
    for cohort_start in pd.date_range(end=last.replace(day=1), periods=months, freq="MS"):
        for _ in range(per_month):
            identifier += 1
            customer = f"C{identifier:05d}"
            offset = 0
            while True:
                when = cohort_start + pd.DateOffset(months=offset)
                if when > last:
                    break
                rows.append({
                    "customer_id": customer,
                    "order_date": when + pd.Timedelta(days=int(rng.integers(0, 26))),
                    "revenue": float(80 + rng.normal(0, 8)),
                    "channel": "Online" if identifier % 2 else "Retail",
                })
                if rng.random() > survival:
                    break
                offset += 1
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture(scope="module")
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


def test_options_pick_the_repeating_column(profile: dict) -> None:
    options = cohort_options(profile)
    assert options["available"] is True
    assert options["defaults"]["entity"] == "customer_id"
    assert options["defaults"]["date_column"] == "order_date"
    assert options["defaults"]["measure"] == "revenue"
    assert options["defaults"]["granularity"] == "month"
    assert options["reason"] is None


def test_granularity_follows_the_span() -> None:
    assert suggest_granularity(10) == "day"
    assert suggest_granularity(60) == "week"
    assert suggest_granularity(300) == "month"
    assert suggest_granularity(3000) == "quarter"


def test_a_column_with_no_repeats_is_not_an_entity() -> None:
    frame = pd.DataFrame({
        "order_id": [f"O{i}" for i in range(200)],
        "order_date": pd.date_range("2024-01-01", periods=200, freq="D"),
        "revenue": np.linspace(10, 200, 200),
    })
    options = cohort_options(profile_dataframe(frame))
    assert options["available"] is False
    assert "repeats" in (options["reason"] or "")
    with pytest.raises(InvalidInputError, match="repeats"):
        analyze(frame, profile_dataframe(frame))


def test_offset_zero_is_always_the_whole_cohort(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    for cohort in result["cohorts"]:
        assert cohort["retention"][0] == pytest.approx(1.0)
        assert cohort["active"][0] == cohort["size"]


def test_unobserved_cells_are_empty_not_zero(frame: pd.DataFrame, profile: dict) -> None:
    """The newest cohort cannot have a twelve-month retention rate. It must not read 0%."""
    result = analyze(frame, profile, measure="revenue", periods=12)
    newest = result["cohorts"][-1]
    assert newest["observed_offsets"] < 12
    beyond = newest["retention"][newest["observed_offsets"] + 1:]
    assert beyond and all(value is None for value in beyond)
    assert all(value is not None for value in newest["retention"][: newest["observed_offsets"] + 1])


def test_the_pooled_curve_only_counts_cohorts_old_enough(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue", periods=12)
    curve = result["curve"]
    # Cohorts counted must fall away as the horizon grows — that is censoring, handled.
    assert curve["cohorts_observed"][0] == len(result["cohorts"])
    assert curve["cohorts_observed"] == sorted(curve["cohorts_observed"], reverse=True)
    for offset, value in enumerate(curve["retention"]):
        if value is None:
            continue
        eligible = [c for c in result["cohorts"] if c["retention"][offset] is not None]
        population = sum(c["size"] for c in eligible)
        expected = sum(c["active"][offset] for c in eligible) / population
        assert value == pytest.approx(expected)


def test_retention_decays_for_a_decaying_population(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    curve = [v for v in result["curve"]["retention"] if v is not None]
    assert curve[0] == pytest.approx(1.0)
    assert curve[1] < curve[0]
    assert curve[-1] < curve[1]
    # 60% monthly survival, so month one should land near it rather than anywhere at all.
    assert 0.45 <= result["summary"]["retention_1"] <= 0.75


def test_a_loyal_population_retains_better_than_a_churning_one() -> None:
    loyal = make_frame(survival=0.85, seed=1)
    churny = make_frame(survival=0.25, seed=1)
    high = analyze(loyal, profile_dataframe(loyal))["summary"]["retention_1"]
    low = analyze(churny, profile_dataframe(churny))["summary"]["retention_1"]
    assert high > low + 0.3


def test_repeat_rate_matches_the_raw_data(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile)
    periods = frame.assign(month=frame["order_date"].dt.to_period("M"))
    by_customer = periods.groupby("customer_id")["month"].nunique()
    # The final partial month is dropped by the analysis, so compare on the same basis.
    assert result["summary"]["repeat_rate"] == pytest.approx(
        float((by_customer > 1).mean()), abs=0.08
    )


def test_a_measure_produces_a_value_curve(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    cumulative = [v for v in result["curve"]["cumulative_value_per_entity"] if v is not None]
    assert len(cumulative) >= 2
    assert cumulative == sorted(cumulative)  # cumulative value never goes down
    assert result["summary"]["value_per_entity_total"] == pytest.approx(cumulative[-1])
    assert any("Cumulative" in c["title"] for c in result["charts"])


def test_without_a_measure_there_is_no_value_curve(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure=None)
    assert result["summary"]["value_per_entity_total"] is None
    assert all(v is None for v in result["curve"]["cumulative_value_per_entity"])
    assert not any("Cumulative" in c["title"] for c in result["charts"])


def test_tiny_cohorts_are_excluded(frame: pd.DataFrame, profile: dict) -> None:
    generous = analyze(frame, profile, min_cohort_size=1)
    strict = analyze(frame, profile, min_cohort_size=25)
    assert len(strict["cohorts"]) <= len(generous["cohorts"])
    assert all(c["size"] >= 25 for c in strict["cohorts"])
    assert any("smaller than 25" in c for c in strict["caveats"])


def test_long_histories_fold_the_oldest_cohorts() -> None:
    frame = make_frame(months=30, per_month=8)
    result = analyze(frame, profile_dataframe(frame))
    # Every cohort is accounted for: the grid keeps the newest and says how many it hid.
    assert len(result["cohorts"]) == MAX_COHORT_ROWS
    assert len(result["cohorts"]) + result["folded"] >= 29
    assert any("oldest cohort" in c for c in result["caveats"])
    assert result["tables"][0]["truncated"] is True
    # The ones it kept are the recent ones, which are the ones anyone can still act on.
    assert result["cohorts"][-1]["start"][:7] >= "2024-01"


def test_the_partial_final_period_is_dropped_and_disclosed() -> None:
    # Data stops on the 3rd, so the last month is three days long.
    frame = make_frame(end="2024-06-03")
    result = analyze(frame, profile_dataframe(frame))
    assert result["coverage"]["final_period_complete"] is False
    assert any("still in progress" in c for c in result["caveats"])
    assert result["coverage"]["last_period"][:7] == "2024-05"


def test_censoring_is_always_disclosed(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile)
    assert any("not lived long enough" in c for c in result["caveats"])
    assert any("appears under two values" in c or "two values" in c for c in result["caveats"])


def test_weekly_grain_is_honoured(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, granularity="week", periods=6)
    assert result["granularity"] == "week"
    assert result["period_noun"] == "week"
    assert result["cohorts"][0]["label"].startswith("w/c ")


def test_unknown_columns_are_rejected(frame: pd.DataFrame, profile: dict) -> None:
    with pytest.raises(InvalidInputError, match="not in this dataset"):
        analyze(frame, profile, entity="nope")
    with pytest.raises(InvalidInputError, match="not in this dataset"):
        analyze(frame, profile, measure="nope")
    with pytest.raises(InvalidInputError, match="not a date"):
        analyze(frame, profile, date_column="channel")
    with pytest.raises(InvalidInputError, match="not numeric"):
        analyze(frame, profile, measure="channel")
    with pytest.raises(InvalidInputError, match="granularity"):
        analyze(frame, profile, granularity="fortnight")


def test_charts_and_tables_use_the_shared_output_shapes(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    assert [c["title"] for c in result["charts"]][0].startswith("Retention by cohort")
    for chart in result["charts"]:
        assert set(chart) == {"title", "caption", "figure", "digest"}
        digest = chart["digest"]["traces"][0]
        assert len(digest["x"]) == digest["x_count"]
        assert len(digest["y"]) == digest["y_count"]
    grid = result["tables"][0]
    assert grid["columns"][0]["name"] == "Cohort"
    assert all(len(row) == len(grid["columns"]) for row in grid["rows"])
    assert len(result["tables"][1]["rows"]) == len(result["offsets"])


def test_markdown_export_contains_the_grid(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    markdown = cohort_markdown(result, "Subscriptions")
    assert markdown.startswith("# Retention of customer_id")
    assert "Subscriptions" in markdown
    assert "## Retention grid" in markdown
    assert "no model call" in markdown


def test_result_is_json_serialisable(frame: pd.DataFrame, profile: dict) -> None:
    result = analyze(frame, profile, measure="revenue")
    assert json.loads(json.dumps(result))["entity"] == "customer_id"
