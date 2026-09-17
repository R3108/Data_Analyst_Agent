"""Significance testing: the distributions, the tests, the correction and the verdicts."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services import statistics as st


def profile_for(df: pd.DataFrame, measures: list[str], dimensions: list[str],
                dates: list[str] | None = None, booleans: list[str] | None = None) -> dict:
    columns = []
    for name in df.columns:
        info = {"name": name, "unique": int(df[name].nunique())}
        if name in (dimensions or []) and pd.api.types.is_integer_dtype(df[name]):
            info["dtype"] = "integer"
            info["stats"] = {"min": int(df[name].min()), "max": int(df[name].max())}
        columns.append(info)
    return {
        "roles": {"measure": measures, "dimension": dimensions,
                  "datetime": dates or [], "boolean": booleans or []},
        "columns": columns,
    }


@pytest.fixture
def two_groups() -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(11)
    n = 300
    df = pd.DataFrame({
        "Revenue": np.concatenate([rng.normal(100, 15, n), rng.normal(118, 15, n)]),
        "Region": ["North"] * n + ["South"] * n,
        "Converted": np.concatenate([rng.binomial(1, 0.12, n), rng.binomial(1, 0.22, n)]),
        "Order Date": pd.to_datetime("2023-01-01") + pd.to_timedelta(np.arange(2 * n), "D"),
    })
    return df, profile_for(df, ["Revenue"], ["Region", "Converted"], ["Order Date"])


# --------------------------------------------------------------------------- distributions


def test_t_distribution_matches_published_values():
    # Two-sided p for t = 2.0 on 10 df is 0.073388 to six places.
    assert st.t_two_sided_p(2.0, 10) == pytest.approx(0.073388, abs=1e-6)
    assert st.t_two_sided_p(3.169, 10) == pytest.approx(0.01, abs=1e-3)
    # With many degrees of freedom it converges to the normal.
    assert st.t_two_sided_p(1.959964, 1_000_000) == pytest.approx(0.05, abs=1e-4)
    assert st.t_two_sided_p(0.0, 5) == pytest.approx(1.0)


def test_normal_quantile_round_trips():
    for p in (0.025, 0.05, 0.5, 0.9, 0.975, 0.999):
        assert st._norm_cdf(st._norm_ppf(p)) == pytest.approx(p, abs=1e-6)
    assert st._norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)


def test_benjamini_hochberg_is_monotone_and_bounded():
    adjusted = st.benjamini_hochberg([0.001, 0.008, 0.039, 0.041, 0.9])
    assert adjusted == sorted(adjusted)  # step-up enforces monotonicity
    assert all(0 <= q <= 1 for q in adjusted)
    # Each q is at least its raw p: correction can only make a finding less significant.
    assert all(q >= p - 1e-12 for p, q in zip([0.001, 0.008, 0.039, 0.041, 0.9], adjusted))


def test_benjamini_hochberg_preserves_input_order_and_skips_nan():
    adjusted = st.benjamini_hochberg([0.9, float("nan"), 0.001])
    assert math.isnan(adjusted[1])
    assert adjusted[2] < adjusted[0]


def test_benjamini_hochberg_of_nothing():
    assert st.benjamini_hochberg([]) == []
    assert all(math.isnan(q) for q in st.benjamini_hochberg([float("nan")]))


# --------------------------------------------------------------------------- comparisons


def test_real_difference_is_called_real(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Revenue", dimension="Region")

    assert result["verdict"]["label"] == "real"
    assert result["significant"] is True
    assert result["p_value"] < 0.001
    assert result["difference"]["crosses_zero"] is False
    # The bootstrap interval should bracket the true 18-unit gap.
    assert result["difference"]["ci_low"] < 18 < result["difference"]["ci_high"]
    assert result["effect"]["magnitude"] in ("medium", "large")
    assert result["power"]["adequate"] is True


def test_identical_groups_are_called_noise():
    rng = np.random.default_rng(3)
    n = 500
    df = pd.DataFrame({
        "Revenue": rng.normal(100, 10, 2 * n),
        "Region": ["North"] * n + ["South"] * n,
    })
    result = st.compare(df, profile_for(df, ["Revenue"], ["Region"]),
                        measure="Revenue", dimension="Region")

    assert result["verdict"]["label"] == "noise"
    assert result["significant"] is False
    assert result["difference"]["crosses_zero"] is True
    # A large sample with no effect is not "we don't know" — it is "no".
    assert result["power"]["adequate"] is True


def test_small_sample_null_result_is_underpowered_not_negative():
    df = pd.DataFrame({
        "Revenue": [100.0, 110, 95, 105, 98, 130, 140, 125, 135, 128],
        "Region": ["North"] * 5 + ["South"] * 5,
    })
    result = st.compare(df, profile_for(df, ["Revenue"], ["Region"]),
                        measure="Revenue", dimension="Region", scan=False)

    if not result["significant"]:
        assert result["verdict"]["label"] == "underpowered"
        assert "not evidence of absence" in " ".join(result["caveats"])
    assert result["power"]["required_n_per_group"] is not None


def test_detectable_effect_shrinks_as_the_sample_grows():
    """Power is reported prospectively, so it must depend on n and not on the p-value."""
    rng = np.random.default_rng(9)
    detectable = []
    for n in (20, 200, 2000):
        df = pd.DataFrame({
            "Revenue": rng.normal(100, 10, 2 * n),
            "Region": ["North"] * n + ["South"] * n,
        })
        result = st.compare(df, profile_for(df, ["Revenue"], ["Region"]),
                            measure="Revenue", dimension="Region", scan=False)
        detectable.append(result["power"]["mde_standardised"])

    assert detectable[0] > detectable[1] > detectable[2]
    # The reference-effect power is a real prospective number, not a p-value in disguise.
    assert 0.0 < result["power"]["power_at_reference"] <= 1.0


def test_binary_measure_uses_a_proportion_test(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Converted", dimension="Region")

    assert result["metric_kind"] == "proportion"
    assert result["primary_test"] == "two_proportion_z"
    # Wilson intervals never leave [0, 1], which is the whole point of using them.
    for group in result["groups"]:
        assert 0.0 <= group["ci_low"] <= group["ci_high"] <= 1.0
    assert result["verdict"]["label"] in ("real", "noise", "underpowered")


def test_mann_whitney_resists_an_outlier_that_moves_the_mean():
    base = [10.0] * 40
    other = [10.0] * 39 + [100000.0]
    df = pd.DataFrame({"Value": base + other, "Group": ["A"] * 40 + ["B"] * 40})
    result = st.compare(df, profile_for(df, ["Value"], ["Group"]),
                        measure="Value", dimension="Group", scan=False)

    ranks = next(t for t in result["tests"] if t["id"] == "mann_whitney")
    # One extreme value must not make the distributions "different" by rank.
    assert ranks["p_value"] > 0.05
    assert result["effect"]["cliffs_delta"] == pytest.approx(0.0, abs=0.05)


def test_period_mode_compares_two_windows(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Revenue", mode="periods",
                        date_column="Order Date")

    assert result["mode"] == "periods"
    assert result["period"]["baseline"]["start"] < result["period"]["current"]["start"]
    assert len(result["groups"]) == 2


def test_scan_corrects_for_multiple_testing():
    # Twenty pure-noise segments: uncorrected testing is expected to "find" about one.
    rng = np.random.default_rng(5)
    labels, values = [], []
    for index in range(20):
        labels += [f"S{index:02d}"] * 60
        values += list(rng.normal(50, 10, 60))
    df = pd.DataFrame({"Value": values, "Segment": labels})
    result = st.compare(df, profile_for(df, ["Value"], ["Segment"]),
                        measure="Value", dimension="Segment")

    scan = result["scan"]
    assert scan["n_tests"] == 20
    assert scan["n_significant"] == 0
    assert scan["n_significant_uncorrected"] >= scan["n_significant"]
    assert all(row["p_adjusted"] >= row["p_value"] - 1e-12 for row in scan["rows"])


def test_scan_still_finds_a_genuine_outlier_segment():
    rng = np.random.default_rng(6)
    labels, values = [], []
    for index in range(10):
        labels += [f"S{index}"] * 120
        values += list(rng.normal(50, 8, 120))
    labels += ["Loud"] * 120
    values += list(rng.normal(75, 8, 120))
    df = pd.DataFrame({"Value": values, "Segment": labels})
    result = st.compare(df, profile_for(df, ["Value"], ["Segment"]),
                        measure="Value", dimension="Segment")

    significant = [r["label"] for r in result["scan"]["rows"] if r["significant"]]
    assert "Loud" in significant


def test_bootstrap_interval_is_reproducible(two_groups):
    df, profile = two_groups
    first = st.compare(df, profile, measure="Revenue", dimension="Region", scan=False)
    second = st.compare(df, profile, measure="Revenue", dimension="Region", scan=False)
    assert first["difference"]["ci_low"] == second["difference"]["ci_low"]
    assert first["difference"]["ci_high"] == second["difference"]["ci_high"]


def test_explicit_groups_and_unknown_group_error(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Revenue", dimension="Region",
                        group_a="South", group_b="North", scan=False)
    assert [g["label"] for g in result["groups"]] == ["South", "North"]

    with pytest.raises(InvalidInputError, match="not a value of Region"):
        st.compare(df, profile, measure="Revenue", dimension="Region", group_b="Atlantis")


def test_zero_variance_does_not_crash():
    df = pd.DataFrame({"Value": [5.0] * 20, "Group": ["A"] * 10 + ["B"] * 10})
    result = st.compare(df, profile_for(df, ["Value"], ["Group"]),
                        measure="Value", dimension="Group", scan=False)
    assert result["verdict"]["label"] == "inconclusive"
    assert math.isnan(result["p_value"])


def test_non_numeric_measure_is_rejected():
    df = pd.DataFrame({"Name": ["a", "b"], "Group": ["A", "B"]})
    with pytest.raises(InvalidInputError, match="not numeric"):
        st.compare(df, profile_for(df, ["Name"], ["Group"]), measure="Name", dimension="Group")


def test_options_classify_binary_columns_as_proportions(two_groups):
    df, profile = two_groups
    options = st.significance_options(profile)
    kinds = {m["name"]: m["kind"] for m in options["measures"]}
    assert kinds["Revenue"] == "mean"
    assert kinds["Converted"] == "proportion"
    assert options["available"] is True


def test_markdown_briefing_reports_the_verdict_and_the_correction(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Revenue", dimension="Region")
    markdown = st.compare_markdown(result, "Demo")

    assert result["verdict"]["headline"] in markdown
    assert "Welch's t-test" in markdown
    assert "Benjamini-Hochberg" in markdown
    assert "no model call" in markdown


def test_charts_and_tables_have_digests_for_static_export(two_groups):
    df, profile = two_groups
    result = st.compare(df, profile, measure="Revenue", dimension="Region")
    assert result["charts"] and all("digest" in c for c in result["charts"])
    assert result["tables"][0]["rows"][0][0] in ("North", "South")
