"""Scenario planning: levers, the mix identity, goal seek and what it refuses to do."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services import scenarios as sc


def profile_for(measures: list[str], dimensions: list[str], dates: list[str],
                uniques: dict[str, int]) -> dict:
    return {
        "roles": {"measure": measures, "dimension": dimensions, "datetime": dates},
        "columns": [{"name": name, "unique": count} for name, count in uniques.items()],
    }


SPAN_DAYS = 300


@pytest.fixture
def book() -> tuple[pd.DataFrame, dict]:
    """A flat book with segments of clearly different average value per record.

    Each segment is spread evenly across the whole date span, because the baseline is a
    trailing window: a segment bunched into one month would simply be absent from it.
    """
    rows = []
    for label, count, average in (("Enterprise", 100, 500.0), ("Mid", 200, 150.0),
                                  ("SMB", 400, 40.0)):
        days = np.linspace(0, SPAN_DAYS - 1, count).round().astype(int)
        rows += [{"Revenue": average, "Segment": label, "Day": int(day)} for day in days]
    df = pd.DataFrame(rows)
    df["Order Date"] = pd.to_datetime("2024-01-01") + pd.to_timedelta(df.pop("Day"), "D")
    profile = profile_for(["Revenue"], ["Segment"], ["Order Date"],
                          {"Segment": 3, "Revenue": 3, "Order Date": SPAN_DAYS})
    return df, profile


# --------------------------------------------------------------------------- levers


def test_no_levers_leaves_the_measure_unchanged(book):
    df, profile = book
    result = sc.simulate(df, profile)
    assert result["change"]["absolute"] == pytest.approx(0.0, abs=1e-9)
    assert result["scenario"]["value"] == pytest.approx(result["baseline"]["value"])
    assert "Nothing is moved yet" in result["narrative"][0]


def test_a_global_rate_lever_scales_the_total_exactly(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"global": {"rate_pct": 0.10}})
    assert result["scenario"]["value"] == pytest.approx(result["baseline"]["value"] * 1.10)
    assert result["change"]["pct"] == pytest.approx(0.10)


def test_a_global_volume_lever_scales_a_sum_and_not_a_mean(book):
    df, profile = book
    summed = sc.simulate(df, profile, levers={"global": {"volume_pct": 0.25}})
    assert summed["scenario"]["value"] == pytest.approx(summed["baseline"]["value"] * 1.25)

    averaged = sc.simulate(df, profile, aggregation="mean",
                           levers={"global": {"volume_pct": 0.25}})
    # More records of the same shape does not move an average.
    assert averaged["scenario"]["value"] == pytest.approx(averaged["baseline"]["value"])


def test_segment_levers_only_move_their_own_segment(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"segments": {"Enterprise": {"rate_pct": 0.20}}})
    moved = {s["label"]: s for s in result["segments"]}
    assert moved["Enterprise"]["change"] > 0
    assert moved["Mid"]["change"] == pytest.approx(0.0)
    assert moved["SMB"]["change"] == pytest.approx(0.0)


def test_a_mix_shift_keeps_the_shares_summing_to_one(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"segments": {"Enterprise": {"share_points": 0.15}}})
    shares = {s["label"]: s["scenario_share"] for s in result["segments"]}
    baseline = {s["label"]: s["baseline_share"] for s in result["segments"]}

    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["Enterprise"] == pytest.approx(baseline["Enterprise"] + 0.15)
    # The remaining share is redistributed in proportion, not taken from one neighbour.
    assert shares["Mid"] / shares["SMB"] == pytest.approx(baseline["Mid"] / baseline["SMB"])


def test_mix_shift_towards_a_richer_segment_raises_the_total(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"segments": {"Enterprise": {"share_points": 0.15}}})
    # Enterprise carries the highest value per record, so buying its share must add revenue.
    assert result["change"]["absolute"] > 0
    assert result["shift_share"]["largest"] == "mix"


def test_impossible_mix_requests_are_scaled_back_not_clipped(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"segments": {
        "Enterprise": {"share_points": 0.8}, "Mid": {"share_points": 0.8},
    }})
    shares = {s["label"]: s["scenario_share"] for s in result["segments"]}
    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["SMB"] == pytest.approx(0.0)
    # Two requests that cannot both be met are reduced in proportion to each other.
    assert shares["Enterprise"] == pytest.approx(shares["Mid"], rel=0.2)


def test_levers_outside_the_allowed_range_are_rejected(book):
    df, profile = book
    with pytest.raises(InvalidInputError, match="Levers are limited"):
        sc.simulate(df, profile, levers={"global": {"rate_pct": 50}})
    with pytest.raises(InvalidInputError, match="share shift is a fraction"):
        sc.simulate(df, profile, levers={"segments": {"Enterprise": {"share_points": 4}}})
    with pytest.raises(InvalidInputError, match="not a Segment"):
        sc.simulate(df, profile, levers={"segments": {"Atlantis": {"rate_pct": 0.1}}})


# --------------------------------------------------------------------------- decomposition


def test_volume_mix_rate_reconciles_to_the_projected_change(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={
        "global": {"volume_pct": 0.05},
        "segments": {"Enterprise": {"rate_pct": 0.2, "share_points": 0.05},
                     "SMB": {"volume_pct": -0.1}},
    })
    shift = result["shift_share"]
    assert shift["closes"] is True
    assert sum(t["value"] for t in shift["terms"]) == pytest.approx(shift["total"], abs=1e-6)
    assert abs(shift["residual"]) < 1e-6


def test_the_decomposition_matches_the_drill_down_shape(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"global": {"rate_pct": 0.1}})
    keys = [t["key"] for t in result["shift_share"]["terms"]]
    assert keys == ["volume", "mix", "rate"]
    # A pure rate move must land entirely in the rate term.
    terms = {t["key"]: t["value"] for t in result["shift_share"]["terms"]}
    assert terms["rate"] == pytest.approx(result["change"]["absolute"])
    assert terms["volume"] == pytest.approx(0.0, abs=1e-6)
    assert terms["mix"] == pytest.approx(0.0, abs=1e-6)


def test_sensitivity_ranks_by_leverage_not_by_size(book):
    df, profile = book
    result = sc.simulate(df, profile)
    by_label = {s["label"]: s for s in result["sensitivity"]}

    # Enterprise is the smallest by record count and the largest by revenue, so a 1%
    # lift there is worth more than its share of records implies.
    assert by_label["Enterprise"]["share"] < by_label["SMB"]["share"]
    assert by_label["Enterprise"]["leverage"] > by_label["SMB"]["leverage"]
    assert [row["rank"] for row in result["sensitivity"]] == [1, 2, 3]


# --------------------------------------------------------------------------- goal seek


def test_goal_seek_finds_the_global_rate_that_hits_a_target(book):
    df, profile = book
    baseline = sc.simulate(df, profile)["baseline"]["value"]
    goal = sc.goal_seek(df, profile, target=baseline * 1.3, lever="rate")

    assert goal["achievable"] is True
    assert goal["required_pct"] == pytest.approx(0.30, abs=1e-4)
    assert goal["achieved"] == pytest.approx(baseline * 1.3, rel=1e-6)
    assert "+30" in goal["message"]


def test_goal_seek_on_one_segment_needs_a_bigger_move(book):
    df, profile = book
    baseline = sc.simulate(df, profile)["baseline"]["value"]
    everywhere = sc.goal_seek(df, profile, target=baseline * 1.1, lever="rate")
    one = sc.goal_seek(df, profile, target=baseline * 1.1, lever="rate", segment="Enterprise")

    assert one["achievable"] is True
    assert one["required_pct"] > everywhere["required_pct"]
    assert one["achieved"] == pytest.approx(baseline * 1.1, rel=1e-6)


def test_goal_seek_says_so_when_no_lever_value_reaches_the_target(book):
    df, profile = book
    baseline = sc.simulate(df, profile)["baseline"]["value"]
    goal = sc.goal_seek(df, profile, target=baseline * 500, lever="rate")

    assert goal["achievable"] is False
    assert "reaches" in goal["message"]
    assert goal["reachable_range"]["max"] < baseline * 500
    # Even an unreachable target returns the unchanged scenario to look at.
    assert goal["scenario"]["baseline"]["value"] == pytest.approx(baseline)


def test_goal_seek_can_target_a_decrease(book):
    df, profile = book
    baseline = sc.simulate(df, profile)["baseline"]["value"]
    goal = sc.goal_seek(df, profile, target=baseline * 0.8, lever="rate")

    assert goal["achievable"] is True
    assert goal["required_pct"] == pytest.approx(-0.20, abs=1e-4)


def test_goal_seek_refuses_a_lever_that_cannot_move_an_average(book):
    df, profile = book
    with pytest.raises(InvalidInputError, match="averaged per record"):
        sc.goal_seek(df, profile, target=100.0, lever="volume", aggregation="mean")


def test_goal_seek_does_not_mutate_the_levers_it_was_given(book):
    df, profile = book
    levers = {"global": {"rate_pct": 0.0}, "segments": {"Enterprise": {"rate_pct": 0.05}}}
    baseline = sc.simulate(df, profile)["baseline"]["value"]
    sc.goal_seek(df, profile, target=baseline * 1.2, lever="rate", levers=levers)
    assert levers == {"global": {"rate_pct": 0.0}, "segments": {"Enterprise": {"rate_pct": 0.05}}}


# --------------------------------------------------------------------------- artifacts


def test_the_waterfall_adds_up_to_the_projected_change(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"segments": {"Enterprise": {"rate_pct": 0.3}}})
    waterfall = result["charts"][0]["figure"]["data"][0]
    steps = [v for v, kind in zip(waterfall["y"], waterfall["measure"]) if kind == "relative"]
    assert sum(steps) == pytest.approx(result["change"]["absolute"], rel=1e-6)


def test_markdown_briefing_publishes_the_residual(book):
    df, profile = book
    result = sc.simulate(df, profile, levers={"global": {"rate_pct": 0.1}})
    markdown = sc.simulate_markdown(result, "Demo")

    assert "Volume · mix · rate" in markdown
    assert "Residual" in markdown
    assert "Leverage" in markdown
    assert "no model call" in markdown


def test_options_mirror_the_drill_down(book):
    df, profile = book
    options = sc.scenario_options(profile)
    assert options["dimensions"] == ["Segment"]
    assert options["levers"] == ["rate", "volume"]
    assert options["defaults"]["measure"] == "Revenue"
    _ = df


def test_a_dataset_with_no_dimension_is_refused():
    df = pd.DataFrame({"Revenue": [1.0, 2.0, 3.0]})
    profile = profile_for(["Revenue"], [], [], {"Revenue": 3})
    with pytest.raises(InvalidInputError, match="categorical column"):
        sc.simulate(df, profile)
