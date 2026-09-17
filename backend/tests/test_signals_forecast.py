from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.agent.pricing import estimate_cost, summarize_usage
from app.sandbox.runner import SandboxRunner
from app.services.cleaning import clean_dataframe
from app.services.ingestion import read_table
from app.services.profiling import profile_dataframe
from app.services.signals import detect_signals

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "retail_sales.csv"


def test_sample_dataset_surfaces_business_signals():
    clean, _ = clean_dataframe(read_table(SAMPLE, SAMPLE.name).frame)
    profile = profile_dataframe(clean)
    signals = detect_signals(clean, profile)
    kinds = {s["kind"] for s in signals}

    assert {"trend", "concentration", "mix_shift", "seasonality"} <= kinds
    trend = next(s for s in signals if s["kind"] == "trend")
    assert trend["title"].startswith("Revenue up") and trend["severity"] == "positive"
    assert len(trend["sparkline"]) >= 12
    # The strongest concentration wins: a single hero product outranks the leading region.
    concentration = next(s for s in signals if s["kind"] == "concentration")
    assert "leads" in concentration["title"] and concentration["breakdown"][0]["share"] >= 0.3
    # Online's growth comes largely at Retail Store's expense; either side of that shift is a valid headline.
    mix_shift = next(s for s in signals if s["kind"] == "mix_shift")
    assert "share of Revenue" in mix_shift["title"]
    before, after = (row["share"] for row in mix_shift["breakdown"])
    assert abs(after - before) >= 0.05
    assert all(s["question"] for s in signals) and len(signals) <= 6


def test_signals_stay_quiet_on_flat_noise():
    rng = np.random.default_rng(1)
    months = pd.date_range("2023-01-01", periods=24, freq="MS")
    rows_per_month = 40  # equal rows per month, so month length can't masquerade as an anomaly
    dates = np.repeat(months.to_numpy(), rows_per_month) + pd.to_timedelta(
        rng.integers(0, 28, 24 * rows_per_month), unit="D").to_numpy()
    df = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "sales": rng.normal(100, 5, 24 * rows_per_month),
        "store": np.tile(["A", "B", "C", "D"], 6 * rows_per_month),
    })
    profile = profile_dataframe(df)
    kinds = {s["kind"] for s in detect_signals(df, profile)}
    assert "trend" not in kinds and "anomaly" not in kinds and "concentration" not in kinds


def test_cost_estimation():
    record = {"model": "gpt-5.6-luna", "input_tokens": 1_000_000, "output_tokens": 100_000,
              "cache_read_tokens": 1_000_000, "cache_write_tokens": 0}
    assert estimate_cost(record) == pytest.approx(0.02 + 0.12)
    summary = summarize_usage([record, {**record, "model": "unknown-model"}])
    assert summary["calls"] == 2 and summary["cost_usd"] is None
    assert summarize_usage([])["cost_usd"] == 0.0


FORECAST_CODE = """
monthly = df.set_index("order_date")["revenue"].resample("MS").sum()
fc = forecast(monthly, periods=12)
future = fc[fc["forecast"].notna()]
kpi("Projected revenue (next 12 months)", future["forecast"].sum(), format="currency")
chart_forecast(fc, title="Revenue forecast", caption="Trend continues", y_label="Revenue ($)")
table(future[["period", "forecast", "lower", "upper"]], title="Forecast")
"""


def test_forecast_helper_projects_trend_and_seasonality(tmp_path):
    rows = []
    for i, month in enumerate(pd.date_range("2021-01-01", periods=36, freq="MS")):
        value = 1000 + 20 * i + (300 if month.month == 12 else 0)
        rows.append({"order_date": month + pd.Timedelta(days=3), "revenue": float(value)})
    path = tmp_path / "monthly.parquet"
    pd.DataFrame(rows).to_parquet(path)

    result = SandboxRunner(timeout_s=60).run(FORECAST_CODE, path)
    assert result.ok, result.error
    table = result.tables[0]
    assert table["total_rows"] == 12
    forecasts = [row[1] for row in table["rows"]]
    # history ends Dec 2023, so step 12 is Dec 2024: it must carry the seasonal uplift on top of the trend
    assert forecasts[11] > forecasts[10] + 250
    assert forecasts[10] > forecasts[0]
    assert all(row[2] <= row[1] <= row[3] for row in table["rows"])
    names = [t["name"] for t in result.charts[0]["digest"]["traces"]]
    assert names == ["95% interval", "Actual", "Forecast"]


def test_forecast_rejects_irregular_series(tmp_path):
    path = tmp_path / "irregular.parquet"
    pd.DataFrame({"order_date": pd.to_datetime(["2024-01-01", "2024-01-03", "2024-02-11", "2024-02-12",
                                                "2024-03-30", "2024-05-01", "2024-05-02"]),
                  "revenue": [1.0, 2, 3, 4, 5, 6, 7]}).to_parquet(path)
    result = SandboxRunner(timeout_s=60).run("forecast(df.set_index('order_date')['revenue'])", path)
    assert not result.ok and "evenly spaced" in result.error
