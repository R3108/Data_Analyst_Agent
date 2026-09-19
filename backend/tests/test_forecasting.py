"""Forecasting: the projection is only worth what the backtest says it is."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services.forecasting import (
    METHODS,
    forecast_options,
    project,
    project_markdown,
    suggest_granularity,
)
from app.services.profiling import profile_dataframe


def make_frame(
    *,
    start: str = "2021-01-01",
    end: str = "2024-06-30",
    slope: float = 0.6,
    seasonal_amplitude: float = 120.0,
    noise: float = 20.0,
    seed: int = 4,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, end, freq="D")
    base = 500 + slope * np.arange(len(dates))
    season = seasonal_amplitude * np.sin(2 * np.pi * (dates.month - 1) / 12)
    values = np.maximum(base + season + rng.normal(0, noise, len(dates)), 0.0)
    return pd.DataFrame({"order_date": dates, "revenue": values, "region": "North"})


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture(scope="module")
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


@pytest.fixture(scope="module")
def result(frame: pd.DataFrame, profile: dict) -> dict:
    return project(frame, profile, horizon=6)


def test_options_pick_sensible_defaults(profile: dict) -> None:
    options = forecast_options(profile)
    assert options["available"] is True
    assert options["defaults"]["measure"] == "revenue"
    assert options["defaults"]["granularity"] == "month"
    assert options["defaults"]["method"] == "auto"
    assert {m["id"] for m in options["methods"]} == set(METHODS)


def test_granularity_follows_the_span() -> None:
    assert suggest_granularity(30) == "day"
    assert suggest_granularity(200) == "week"
    assert suggest_granularity(800) == "month"


def test_every_candidate_is_scored_on_held_out_data(result: dict) -> None:
    assert result["backtest"]["folds"] >= 2
    assert result["backtest"]["tested_points"] > 0
    keys = {s["method"] for s in result["accuracy"]}
    assert {"naive", "seasonal_naive", "trend", "trend_seasonal"} <= keys
    # Every complete method was scored on the same number of points, or the race is rigged.
    complete = [s["points"] for s in result["accuracy"] if s["complete"]]
    assert len(set(complete)) == 1


def test_a_seasonal_trend_is_recognised(result: dict) -> None:
    assert result["season_length"] == 12
    assert "seasonal" in result["method"]
    assert result["verdict"]["label"] == "useful"
    assert result["verdict"]["improvement"] > 0.1


def test_the_winner_beats_every_other_candidate(result: dict) -> None:
    chosen = next(s for s in result["accuracy"] if s["method"] == result["method"])
    others = [s for s in result["accuracy"] if s["method"] != result["method"] and s["complete"]]
    assert all(chosen["mae"] <= s["mae"] + 1e-9 for s in others)
    assert result["accuracy"][0]["method"] == result["method"]


def test_a_requested_method_overrides_the_backtest(frame: pd.DataFrame, profile: dict) -> None:
    result = project(frame, profile, horizon=4, method="naive")
    assert result["method"] == "naive"
    assert result["selection"] == "requested"
    # Its own forecast really is the last observation, repeated.
    values = {round(f["value"], 6) for f in result["forecast"]}
    assert len(values) == 1
    # And it is still scored honestly against the alternatives it lost to.
    assert result["accuracy"][0]["method"] != "naive"


def test_a_flat_series_falls_back_to_the_baseline_and_says_so() -> None:
    # Averaged, not summed: a constant daily value totalled by month still moves with
    # the length of the month, and a seasonal method would rightly pick that up.
    dates = pd.date_range("2022-01-01", "2024-06-30", freq="D")
    frame = pd.DataFrame({"order_date": dates, "revenue": np.full(len(dates), 42.0)})
    result = project(frame, profile_dataframe(frame), horizon=4, aggregation="mean")
    assert result["verdict"]["label"] in ("baseline", "no better")
    assert all(f["value"] == pytest.approx(42.0) for f in result["forecast"])


def test_intervals_come_from_the_backtest_and_widen(result: dict) -> None:
    assert result["backtest"]["interval_source"].startswith("backtest errors")
    widths = [f["upper"] - f["lower"] for f in result["forecast"]]
    assert all(width > 0 for width in widths)
    assert widths[-1] >= widths[0]
    for row in result["forecast"]:
        assert row["lower"] <= row["value"] <= row["upper"]


def test_a_wider_interval_is_a_wider_interval(frame: pd.DataFrame, profile: dict) -> None:
    narrow = project(frame, profile, horizon=4, interval=0.5)
    wide = project(frame, profile, horizon=4, interval=0.95)
    assert (wide["forecast"][0]["upper"] - wide["forecast"][0]["lower"]) >= (
        narrow["forecast"][0]["upper"] - narrow["forecast"][0]["lower"]
    )


def test_a_non_negative_series_never_forecasts_below_zero() -> None:
    dates = pd.date_range("2022-01-01", "2024-06-30", freq="D")
    values = np.maximum(np.linspace(400, 5, len(dates)), 0)
    frame = pd.DataFrame({"order_date": dates, "revenue": values})
    result = project(frame, profile_dataframe(frame), horizon=12)
    assert all(f["value"] >= 0 and f["lower"] >= 0 for f in result["forecast"])


def test_the_partial_final_month_is_dropped() -> None:
    frame = make_frame(end="2024-06-04")
    result = project(frame, profile_dataframe(frame), horizon=3)
    assert "last" in result["coverage"]["trimmed_partial"]
    assert result["coverage"]["last_period"][:7] == "2024-05"
    assert any("incomplete" in c for c in result["caveats"])


def test_a_short_history_disables_seasonality_and_discloses_it() -> None:
    frame = make_frame(start="2023-06-01", end="2024-06-30")
    result = project(frame, profile_dataframe(frame), horizon=3)
    assert result["season_length"] == 0
    assert "seasonal" not in result["method"]
    assert any("two full seasonal cycles" in c for c in result["caveats"])


def test_too_little_history_is_refused() -> None:
    frame = pd.DataFrame({
        "order_date": pd.date_range("2024-01-01", periods=40, freq="D"),
        "revenue": np.linspace(1, 40, 40),
    })
    with pytest.raises(InvalidInputError, match="at least"):
        project(frame, profile_dataframe(frame), granularity="month")


def test_unknown_inputs_are_rejected(frame: pd.DataFrame, profile: dict) -> None:
    with pytest.raises(InvalidInputError, match="not in this dataset"):
        project(frame, profile, measure="nope")
    with pytest.raises(InvalidInputError, match="not numeric"):
        project(frame, profile, measure="region")
    with pytest.raises(InvalidInputError, match="not a date"):
        project(frame, profile, date_column="region")
    with pytest.raises(InvalidInputError, match="method"):
        project(frame, profile, method="prophet")
    with pytest.raises(InvalidInputError, match="between 50%"):
        project(frame, profile, interval=0.2)


def test_horizon_and_totals_line_up(frame: pd.DataFrame, profile: dict) -> None:
    result = project(frame, profile, horizon=8)
    assert len(result["forecast"]) == 8
    assert [f["step"] for f in result["forecast"]] == list(range(1, 9))
    assert result["totals"]["projected"] == pytest.approx(
        sum(f["value"] for f in result["forecast"])
    )
    assert result["totals"]["next_period"] == pytest.approx(result["forecast"][0]["value"])


def test_forecast_periods_continue_the_history(result: dict) -> None:
    last = pd.Timestamp(result["history"][-1]["period"])
    first = pd.Timestamp(result["forecast"][0]["period"])
    assert first > last
    stamps = [pd.Timestamp(f["period"]) for f in result["forecast"]]
    assert stamps == sorted(stamps)


def test_charts_and_tables_use_the_shared_output_shapes(result: dict) -> None:
    assert len(result["charts"]) == 3
    for chart in result["charts"]:
        assert set(chart) == {"title", "caption", "figure", "digest"}
        for trace in chart["digest"]["traces"]:
            assert len(trace["x"]) == trace["x_count"]
            assert len(trace["y"]) == trace["y_count"]
    projection, scoreboard = result["tables"]
    assert projection["columns"][0]["name"] == "Month"
    assert len(projection["rows"]) == result["horizon"]
    assert len(scoreboard["rows"]) == len(result["accuracy"])
    assert all(len(row) == len(scoreboard["columns"]) for row in scoreboard["rows"])


def test_markdown_export_contains_the_scoreboard(result: dict) -> None:
    markdown = project_markdown(result, "Retail")
    assert markdown.startswith("# revenue forecast")
    assert "Retail" in markdown
    assert "## Backtest scoreboard" in markdown
    assert "walk-forward" in markdown


def test_result_is_json_serialisable(result: dict) -> None:
    assert json.loads(json.dumps(result))["measure"] == "revenue"
