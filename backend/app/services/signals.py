"""Proactive insight detection ("signals").

Runs deterministically at ingest — no LLM, no cost, instant — so users see what
matters in their data before they ask a single question. Every detector is
best-effort: a failure in one never blocks the others or the upload.
"""

from __future__ import annotations

import calendar
import logging
import re
from typing import Any, Callable

import numpy as np
import pandas as pd

from app.services.profiling import NON_ADDITIVE, rank_measures

logger = logging.getLogger(__name__)

MAX_SIGNALS = 6
LOWER_IS_BETTER = re.compile(r"(cost|expense|return|refund|churn|complaint|defect|delay|discount|loss|error)", re.I)


def detect_signals(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, Any]]:
    roles = profile.get("roles", {})
    all_measures = rank_measures(roles.get("measure", []))
    additive = [m for m in all_measures if not NON_ADDITIVE.search(m)]
    measure = additive[0] if additive else None
    date = (roles.get("datetime") or [None])[0]
    dims = [c["name"] for c in profile.get("columns", []) if c["role"] == "dimension" and 2 <= c["unique"] <= 50]

    series, grain = _time_series(df, date, measure) if (date and measure) else (None, None)

    detectors: list[Callable[[], dict[str, Any] | None]] = [
        lambda: _trend(series, grain, measure),
        lambda: _mix_shift(df, dims, date, measure),
        lambda: _concentration(df, dims, measure),
        lambda: _anomaly(series, grain, measure),
        lambda: _seasonality(series, grain, measure),
        lambda: _correlation(df, all_measures),
        lambda: _quality(profile),
    ]
    signals: list[dict[str, Any]] = []
    for detector in detectors:
        try:
            signal = detector()
        except Exception:  # noqa: BLE001 — signals are best-effort
            logger.debug("Signal detector failed", exc_info=True)
            continue
        if signal:
            signal["id"] = f"{signal['kind']}-{len(signals)}"
            signals.append(signal)
    return signals[:MAX_SIGNALS]


# ------------------------------------------------------------------------------ helpers


def _signal(kind: str, severity: str, title: str, detail: str, question: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail, "question": question,
            "sparkline": extra.get("sparkline"), "breakdown": extra.get("breakdown"),
            # Enough to open the deterministic drill-down pre-aimed at this finding.
            "explain": extra.get("explain")}


def _compact(value: float) -> str:
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{value / threshold:.1f}{suffix}"
    return f"{value:,.0f}" if magnitude >= 100 else f"{value:,.2f}"


def _direction(measure: str, up: bool) -> str:
    good = up != bool(LOWER_IS_BETTER.search(measure))
    return "positive" if good else "negative"


def _time_series(df: pd.DataFrame, date: str, measure: str) -> tuple[pd.Series | None, str | None]:
    data = df[[date, measure]].dropna()
    if len(data) < 12:
        return None, None
    start, end = data[date].min(), data[date].max()
    span = (end - start).days
    if span >= 180:
        series = data.set_index(date)[measure].resample("MS").sum()
        if end.day < 25:  # drop an incomplete final month
            series = series.iloc[:-1]
        if start.day > 7:  # and an incomplete first month
            series = series.iloc[1:]
        return series, "month"
    if span >= 42:
        series = data.set_index(date)[measure].resample("W-MON", label="left", closed="left").sum()
        return series.iloc[1:-1], "week"
    return None, None


# ------------------------------------------------------------------------------ detectors


def _trend(series: pd.Series | None, grain: str | None, measure: str | None) -> dict[str, Any] | None:
    if series is None or measure is None or len(series) < 6:
        return None
    window = min(12, len(series) // 2)
    recent, prior = float(series.iloc[-window:].sum()), float(series.iloc[-2 * window:-window].sum())
    if prior <= 0:
        return None
    change = recent / prior - 1
    if abs(change) < 0.05:
        return None
    up = change > 0
    return _signal(
        "trend", _direction(measure, up),
        f"{measure} {'up' if up else 'down'} {abs(change):.0%}",
        f"Last {window} {grain}s vs the {window} before ({_compact(recent)} vs {_compact(prior)}).",
        f"What is driving the {abs(change):.0%} {'increase' if up else 'decline'} in {measure} "
        f"over the last {window} {grain}s?",
        sparkline=[round(float(v), 2) for v in series.tail(36)],
        explain={"measure": measure},
    )


def _seasonality(series: pd.Series | None, grain: str | None, measure: str | None) -> dict[str, Any] | None:
    if series is None or grain != "month" or len(series) < 24 or measure is None:
        return None
    by_month = series.groupby(series.index.month).mean()
    overall = float(by_month.mean())
    if overall <= 0 or len(by_month) < 12:
        return None
    lift = (by_month / overall - 1).sort_values(ascending=False)
    peaks = lift[lift >= 0.2]
    if peaks.empty:
        return None
    names = [calendar.month_abbr[int(m)] for m in sorted(peaks.index[:3])]
    top = int(peaks.index[0])
    return _signal(
        "seasonality", "neutral",
        f"Seasonal peak in {' & '.join(names)}",
        f"{measure} in {calendar.month_name[top]} runs {peaks.iloc[0]:.0%} above a typical month.",
        f"How strong is the seasonality in {measure}, and which segments drive the {', '.join(names)} peak?",
        sparkline=[round(float(by_month.get(m, 0.0)), 2) for m in range(1, 13)],
    )


def _anomaly(series: pd.Series | None, grain: str | None, measure: str | None) -> dict[str, Any] | None:
    if series is None or measure is None or len(series) < 8:
        return None
    median = float(series.median())
    mad = float((series - median).abs().median())
    if mad == 0 or median == 0:
        return None
    z = 0.6745 * (series - median) / mad
    when = z.abs().idxmax()
    score = float(z[when])
    if abs(score) < 3.5:
        return None
    label = when.strftime("%b %Y") if grain == "month" else f"the week of {when:%d %b %Y}"
    ratio = float(series[when]) / median
    return _signal(
        "anomaly", "warning",
        f"Unusual {'spike' if score > 0 else 'drop'} in {label}",
        f"{measure} reached {_compact(float(series[when]))} — {ratio:.1f}× a typical {grain}.",
        f"What caused the unusual {'spike' if score > 0 else 'drop'} in {measure} in {label}?",
        sparkline=[round(float(v), 2) for v in series.tail(36)],
        explain={"measure": measure},
    )


def _concentration(df: pd.DataFrame, dims: list[str], measure: str | None) -> dict[str, Any] | None:
    if measure is None:
        return None
    best: tuple[float, str, pd.Series] | None = None
    for dim in dims:
        grouped = df.groupby(dim, observed=True)[measure].sum().sort_values(ascending=False)
        total = float(grouped.sum())
        if total <= 0 or len(grouped) < 3:
            continue
        lift = float(grouped.iloc[0]) / total * len(grouped)
        if best is None or lift > best[0]:
            best = (lift, dim, grouped / total)
    if best is None:
        return None
    lift, dim, shares = best
    top, share = str(shares.index[0]), float(shares.iloc[0])
    if share < 0.3 or lift < 1.3:
        return None
    return _signal(
        "concentration", "neutral",
        f"{top} leads {dim} with {share:.0%} of {measure}",
        f"{lift:.1f}× an even split across {len(shares)} {dim} values.",
        f"Why does {top} outperform the other {dim} values on {measure}, and is that concentration a risk?",
        breakdown=[{"label": str(k), "share": round(float(v), 4)} for k, v in shares.head(5).items()],
        explain={"measure": measure, "dimension": dim},
    )


def _mix_shift(df: pd.DataFrame, dims: list[str], date: str | None, measure: str | None) -> dict[str, Any] | None:
    if not date or measure is None:
        return None
    data = df.dropna(subset=[date, measure])
    start, end = data[date].min(), data[date].max()
    if pd.isna(start) or (end - start).days < 60:
        return None
    midpoint = start + (end - start) / 2
    first, second = data[data[date] < midpoint], data[data[date] >= midpoint]
    if len(first) < 30 or len(second) < 30:
        return None

    best: tuple[float, str, str, float, float] | None = None
    for dim in dims:
        if df[dim].nunique() > 12:
            continue
        before = first.groupby(dim, observed=True)[measure].sum()
        after = second.groupby(dim, observed=True)[measure].sum()
        if before.sum() <= 0 or after.sum() <= 0:
            continue
        shares = pd.concat([before / before.sum(), after / after.sum()], axis=1).fillna(0.0)
        delta = shares.iloc[:, 1] - shares.iloc[:, 0]
        category = delta.abs().idxmax()
        change = float(delta[category])
        if best is None or abs(change) > abs(best[0]):
            best = (change, dim, str(category), float(shares.loc[category].iloc[0]), float(shares.loc[category].iloc[1]))
    if best is None or abs(best[0]) < 0.05:
        return None
    change, dim, category, was, now = best
    return _signal(
        "mix_shift", "neutral",
        f"{category} {'gained' if change > 0 else 'lost'} share of {measure}",
        f"{was:.0%} → {now:.0%} of {measure} between the first and second half of the period ({dim}).",
        f"Why is {category}'s share of {measure} {'growing' if change > 0 else 'shrinking'}, "
        f"and what does that mean for the {dim} mix?",
        breakdown=[{"label": "First half", "share": round(was, 4)}, {"label": "Second half", "share": round(now, 4)}],
        explain={"measure": measure, "dimension": dim},
    )


def _correlation(df: pd.DataFrame, measures: list[str]) -> dict[str, Any] | None:
    columns = measures[:8]
    if len(columns) < 2 or len(df) < 30:
        return None
    corr = df[columns].corr(numeric_only=True)
    best: tuple[float, str, str] | None = None
    for i, a in enumerate(columns):
        for b in columns[i + 1:]:
            r = corr.loc[a, b]
            if pd.notna(r) and abs(r) < 0.999 and (best is None or abs(r) > abs(best[0])):
                best = (float(r), a, b)
    if best is None or abs(best[0]) < 0.7:
        return None
    r, a, b = best
    return _signal(
        "correlation", "neutral",
        f"{a} and {b} move together" if r > 0 else f"{a} rises as {b} falls",
        f"Correlation r = {r:.2f} — a relationship worth testing, not proof of cause.",
        f"How strong is the relationship between {a} and {b}, and does it hold across segments?",
    )


def _quality(profile: dict[str, Any]) -> dict[str, Any] | None:
    worst = max(profile.get("columns", []), key=lambda c: c["missing_pct"], default=None)
    if worst is None or worst["missing_pct"] < 10:
        return None
    return _signal(
        "quality", "warning",
        f"{worst['name']} is {worst['missing_pct']:.0f}% empty",
        f"{worst['missing']:,} missing values could bias any analysis that relies on this column.",
        f"How do the missing values in {worst['name']} affect the analysis, and are they random?",
    )
