"""Forecasting with a track record attached.

A projection is easy to produce and hard to believe. Numera's answer to that is the
same one it gives everywhere else: don't ask the reader to take it on faith, show the
evidence. So this module never returns a forecast on its own. It returns a forecast
*and* the walk-forward backtest that chose it — every candidate method scored on data
it had not seen, against the two baselines that any forecast has to beat before it is
worth anything:

* **naive** — tomorrow looks like today;
* **seasonal naive** — this month looks like the same month last year.

`MASE` is the headline number because it is scale-free and has an honest zero point:
1.0 means "no better than the baseline". If nothing beats the baseline, this module
says so and recommends the baseline, rather than dressing up a coin flip as a model.

Prediction intervals come from the backtest's own errors at each horizon step, not
from a normality assumption about residuals the model was fitted on — which is why
they widen with the horizon by themselves.

Everything is numpy and pandas. No model call, no statsmodels, no prophet.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from app.core.errors import InvalidInputError
from app.core.formatting import compact, percent, signed_percent
from app.services.profiling import NON_ADDITIVE, rank_measures

logger = logging.getLogger(__name__)

Granularity = Literal["auto", "day", "week", "month", "quarter"]
GRANULARITIES = ("auto", "day", "week", "month", "quarter")
Aggregation = Literal["sum", "mean"]

# resample rule, period alias for trimming, season length, reader-facing noun
GRAIN = {
    "day": ("D", "D", 7, "day"),
    "week": ("W-MON", "W-MON", 52, "week"),
    "month": ("MS", "M", 12, "month"),
    "quarter": ("QS", "Q", 4, "quarter"),
}

MIN_OBSERVATIONS = 8
MAX_HORIZON = 36
DEFAULT_HORIZON = 6
# Folds are the whole point; one fold is an anecdote.
MAX_FOLDS = 6
MIN_FOLDS = 2

HISTORY = "#2a78d6"
FORECAST = "#eb6834"
BAND = "rgba(235,104,52,0.16)"
BASELINE = "#c3c2b7"


# ----------------------------------------------------------------------------- options


def forecast_options(profile: dict[str, Any]) -> dict[str, Any]:
    """Which measures, dates and grains a projection can be built on."""
    roles = profile.get("roles") or {}
    columns = {c["name"]: c for c in profile.get("columns") or []}
    measures = rank_measures(list(roles.get("measure") or []))
    dates = list(roles.get("datetime") or [])

    span_days = 0
    if dates:
        stats = columns.get(dates[0], {}).get("stats") or {}
        span_days = int(stats.get("span_days") or 0)
    additive = [m for m in measures if not NON_ADDITIVE.search(m)]
    default_measure = (additive or measures or [None])[0]

    return {
        "measures": [{"name": m, "aggregation": default_aggregation(m)} for m in measures],
        "date_columns": dates,
        "granularities": [g for g in GRANULARITIES if g != "auto"],
        "methods": [{"id": key, "label": spec.label, "detail": spec.detail}
                    for key, spec in METHODS.items()],
        "defaults": {
            "measure": default_measure,
            "date_column": dates[0] if dates else None,
            "aggregation": default_aggregation(default_measure) if default_measure else "sum",
            "granularity": suggest_granularity(span_days),
            "horizon": DEFAULT_HORIZON,
            "method": "auto",
            "interval": 0.8,
        },
        "available": bool(default_measure and dates),
        "reason": (
            None if (default_measure and dates)
            else "A forecast needs a date column and a numeric measure; this dataset is missing one."
        ),
    }


def default_aggregation(measure: str | None) -> Aggregation:
    """Rates and prices are averaged over a period, not added up."""
    return "mean" if measure and NON_ADDITIVE.search(measure) else "sum"


def suggest_granularity(span_days: int) -> str:
    if span_days >= 1500:
        return "month"
    if span_days >= 400:
        return "month"
    if span_days >= 120:
        return "week"
    return "day"


# ----------------------------------------------------------------------------- methods
#
# Every method is stateless: it is handed a history and asked for `h` steps. That is
# what makes the walk-forward backtest honest — each fold refits from scratch on data
# that ends before the period it is asked to predict.


class Method:
    def __init__(self, key: str, label: str, detail: str,
                 fn: Callable[[np.ndarray, int, int], np.ndarray | None],
                 seasonal: bool = False, baseline: bool = False) -> None:
        self.key = key
        self.label = label
        self.detail = detail
        self.fn = fn
        self.seasonal = seasonal
        self.baseline = baseline

    def predict(self, y: np.ndarray, season: int, horizon: int) -> np.ndarray | None:
        if self.seasonal and (season < 2 or len(y) < 2 * season):
            return None
        try:
            out = self.fn(y, season, horizon)
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            return None
        if out is None:
            return None
        out = np.asarray(out, dtype=float)
        if out.shape != (horizon,) or not np.all(np.isfinite(out)):
            return None
        return out


def _naive(y: np.ndarray, season: int, h: int) -> np.ndarray:
    return np.repeat(float(y[-1]), h)


def _seasonal_naive(y: np.ndarray, season: int, h: int) -> np.ndarray:
    return np.array([y[-season + (k % season)] for k in range(h)], dtype=float)


def _mean(y: np.ndarray, season: int, h: int) -> np.ndarray:
    return np.repeat(float(np.mean(y[-min(len(y), max(season, 4)):])), h)


def _drift(y: np.ndarray, season: int, h: int) -> np.ndarray | None:
    n = len(y)
    if n < 2:
        return None
    slope = (y[-1] - y[0]) / (n - 1)
    return y[-1] + slope * np.arange(1, h + 1, dtype=float)


def _seasonal_indices(y: np.ndarray, season: int) -> np.ndarray | None:
    """Additive seasonal pattern from a centred moving average. Sums to zero."""
    if season < 2 or len(y) < 2 * season:
        return None
    smooth = pd.Series(y).rolling(season, center=True, min_periods=season).mean().to_numpy()
    detrended = y - smooth
    pattern = np.array([
        np.nanmean(detrended[i::season]) if np.isfinite(detrended[i::season]).any() else 0.0
        for i in range(season)
    ])
    pattern = np.nan_to_num(pattern)
    return pattern - pattern.mean()


def _linear(y: np.ndarray, season: int, h: int, *, seasonal: bool = False) -> np.ndarray | None:
    n = len(y)
    if n < 3:
        return None
    pattern = _seasonal_indices(y, season) if seasonal else None
    if seasonal and pattern is None:
        return None
    phases = np.arange(n) % season if pattern is not None else None
    base = y - pattern[phases] if pattern is not None else y
    t = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(t, base, 1)
    future_t = n - 1 + np.arange(1, h + 1, dtype=float)
    out = intercept + slope * future_t
    if pattern is not None:
        out = out + pattern[future_t.astype(int) % season]
    return out


def _damped_holt(y: np.ndarray, season: int, h: int, *, seasonal: bool = False) -> np.ndarray | None:
    """Level + damped trend, grid-fitted on one-step-ahead squared error.

    Damping matters: an undamped trend extrapolated eight periods out is the single
    most common way a forecast embarrasses the person who presented it.
    """
    n = len(y)
    if n < 4:
        return None
    pattern = _seasonal_indices(y, season) if seasonal else None
    if seasonal and pattern is None:
        return None
    work = y - pattern[np.arange(n) % season] if pattern is not None else y

    best: tuple[float, float, float, float] | None = None
    for alpha in (0.1, 0.2, 0.3, 0.5, 0.7, 0.9):
        for beta in (0.02, 0.05, 0.1, 0.2, 0.35):
            for phi in (0.8, 0.9, 0.95, 1.0):
                level = float(work[0])
                trend = float(work[1] - work[0])
                sse = 0.0
                for value in work[1:]:
                    prediction = level + phi * trend
                    error = value - prediction
                    sse += error * error
                    level = prediction + alpha * error
                    trend = phi * trend + beta * alpha * error
                if best is None or sse < best[0]:
                    best = (sse, alpha, beta, phi)
    if best is None:
        return None
    _, alpha, beta, phi = best

    level = float(work[0])
    trend = float(work[1] - work[0])
    for value in work[1:]:
        prediction = level + phi * trend
        error = value - prediction
        level = prediction + alpha * error
        trend = phi * trend + beta * alpha * error
    # Damped trend accumulates as phi + phi^2 + ... + phi^k, which converges.
    damping = np.cumsum(np.power(phi, np.arange(1, h + 1, dtype=float)))
    out = level + damping * trend
    if pattern is not None:
        out = out + pattern[(n - 1 + np.arange(1, h + 1)).astype(int) % season]
    return out


METHODS: dict[str, Method] = {
    "naive": Method("naive", "Naive", "Next period equals the last one.", _naive, baseline=True),
    "seasonal_naive": Method("seasonal_naive", "Seasonal naive",
                             "Next period equals the same period one season ago.",
                             _seasonal_naive, seasonal=True, baseline=True),
    "mean": Method("mean", "Recent mean", "The average of the most recent season.", _mean),
    "drift": Method("drift", "Drift", "Continues the average slope of the whole history.", _drift),
    "trend": Method("trend", "Linear trend", "Least-squares line through the history.", _linear),
    "trend_seasonal": Method(
        "trend_seasonal", "Trend + seasonality",
        "Least-squares line plus an additive seasonal pattern.",
        lambda y, s, h: _linear(y, s, h, seasonal=True), seasonal=True),
    "damped": Method("damped", "Damped trend",
                     "Exponential smoothing whose trend flattens as it extrapolates.",
                     _damped_holt),
    "damped_seasonal": Method(
        "damped_seasonal", "Damped trend + seasonality",
        "Damped exponential smoothing on the deseasonalised series.",
        lambda y, s, h: _damped_holt(y, s, h, seasonal=True), seasonal=True),
}


# ----------------------------------------------------------------------------- entry point


def project(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    measure: str | None = None,
    date_column: str | None = None,
    aggregation: Aggregation | None = None,
    granularity: Granularity = "auto",
    horizon: int = DEFAULT_HORIZON,
    method: str = "auto",
    interval: float = 0.8,
) -> dict[str, Any]:
    """Project `measure` forward, having first proved the method on held-out history."""
    options = forecast_options(profile)
    measure = measure or options["defaults"]["measure"]
    date_column = date_column or options["defaults"]["date_column"]

    if not measure:
        raise InvalidInputError("This dataset has no numeric measure to project.")
    if measure not in df.columns:
        raise InvalidInputError(f"Column '{measure}' is not in this dataset.")
    if not pd.api.types.is_numeric_dtype(df[measure]):
        raise InvalidInputError(f"'{measure}' is not numeric, so it cannot be projected.")
    if not date_column:
        raise InvalidInputError("A forecast needs a date column; this dataset has none.")
    if date_column not in df.columns:
        raise InvalidInputError(f"Column '{date_column}' is not in this dataset.")
    if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
        raise InvalidInputError(f"'{date_column}' is not a date column.")
    if granularity not in GRANULARITIES:
        raise InvalidInputError(f"Unknown granularity '{granularity}'.")
    if method != "auto" and method not in METHODS:
        raise InvalidInputError(f"Unknown forecasting method '{method}'.")
    if not 0.5 <= float(interval) <= 0.99:
        raise InvalidInputError("The prediction interval must be between 50% and 99%.")

    agg: Aggregation = aggregation or default_aggregation(measure)
    if agg not in ("sum", "mean"):
        raise InvalidInputError("Aggregation must be 'sum' or 'mean'.")

    frame = df[[date_column, measure]].dropna()
    dropped = int(len(df) - len(frame))
    if frame.empty:
        raise InvalidInputError(f"No rows have both a {date_column} and a {measure} value.")

    span_days = int((frame[date_column].max() - frame[date_column].min()).days)
    grain = suggest_granularity(span_days) if granularity == "auto" else granularity
    rule, alias, season, noun = GRAIN[grain]

    series, trimmed = _regular_series(frame, date_column, measure, agg, rule, alias)
    if len(series) < MIN_OBSERVATIONS:
        raise InvalidInputError(
            f"A backtested forecast needs at least {MIN_OBSERVATIONS} complete {noun}s; this "
            f"dataset has {len(series)}. Try a finer grain."
        )

    horizon = int(max(1, min(int(horizon), MAX_HORIZON)))
    y = series.to_numpy(dtype=float)
    # Two full cycles plus room for at least one fold, or seasonality is not on the table.
    # Every fold must be able to fit every candidate, or the scoreboard is not a fair race.
    season = season if len(y) >= 2 * season + 2 else 0

    backtest = _backtest(y, season, horizon)
    chosen = _choose(backtest, method)
    if chosen is None:
        raise InvalidInputError(
            f"'{METHODS[method].label}' cannot be fitted to this series." if method != "auto"
            else "No forecasting method could be fitted to this series. It may be too short or "
                 "too irregular after aggregation."
        )

    spec = METHODS[chosen["method"]]
    point = spec.predict(y, season or 1, horizon)
    if point is None:
        raise InvalidInputError(f"'{spec.label}' cannot be fitted to this series.")
    if float(np.nanmin(y)) >= 0:
        point = np.maximum(point, 0.0)  # a count or a total cannot go negative

    bounds = _intervals(backtest["residuals"].get(chosen["method"], []), point, float(interval), y)
    future_index = _future_index(series.index, rule, horizon)

    result: dict[str, Any] = {
        "measure": measure,
        "date_column": date_column,
        "aggregation": agg,
        "granularity": grain,
        "period_noun": noun,
        "season_length": int(season),
        "horizon": horizon,
        "interval": float(interval),
        "method": chosen["method"],
        "method_label": spec.label,
        "method_detail": spec.detail,
        "selection": "chosen by backtest" if method == "auto" else "requested",
        "history": [
            {"period": stamp.isoformat(), "value": float(value)}
            for stamp, value in zip(series.index, y)
        ],
        "forecast": [
            {
                "period": stamp.isoformat(),
                "value": float(value),
                "lower": float(low),
                "upper": float(high),
                "step": index + 1,
            }
            for index, (stamp, value, low, high) in enumerate(
                zip(future_index, point, bounds["lower"], bounds["upper"])
            )
        ],
        "accuracy": backtest["scores"],
        "backtest": {
            "folds": backtest["folds"],
            "origins": backtest["origins"],
            "tested_points": backtest["tested_points"],
            "interval_source": bounds["source"],
        },
        "verdict": _verdict(chosen, backtest),
        "totals": _totals(y, point),
        "coverage": {
            "observations": int(len(y)),
            "first_period": series.index[0].isoformat(),
            "last_period": series.index[-1].isoformat(),
            "rows_used": int(len(frame)),
            "rows_dropped": dropped,
            "empty_periods": int(np.sum(y == 0)),
            "trimmed_partial": trimmed,
        },
        "options": options,
    }
    result["headline"] = _headline(result)
    result["narrative"] = _narrative(result)
    result["caveats"] = _caveats(result)
    result["follow_up"] = _follow_up(result)
    result["charts"] = _charts(result)
    result["tables"] = _tables(result)
    return result


# ----------------------------------------------------------------------------- series


def _regular_series(
    frame: pd.DataFrame, date_column: str, measure: str, agg: Aggregation,
    rule: str, alias: str,
) -> tuple[pd.Series, list[str]]:
    """Aggregate to a gap-free grid, dropping the partial periods at each end.

    A half-finished month is not a decline, and a forecast fitted through one learns a
    downward trend that does not exist.
    """
    trimmed: list[str] = []
    data = frame.sort_values(date_column)
    if alias not in ("D",):
        periods = pd.PeriodIndex(data[date_column], freq=alias)
        first, last = periods.min(), periods.max()
        if data[date_column].iloc[0] > first.start_time:
            data = data[periods != first]
            trimmed.append("first")
            periods = pd.PeriodIndex(data[date_column], freq=alias)
        if not data.empty and data[date_column].iloc[-1] < last.end_time.normalize():
            data = data[periods != last]
            trimmed.append("last")
    if data.empty:
        raise InvalidInputError(
            "Every period in this dataset is partial, so there is nothing complete to fit."
        )
    resampled = data.set_index(date_column)[measure].resample(rule)
    series = (resampled.sum() if agg == "sum" else resampled.mean()).astype("float64")
    # `mean` leaves NaN for an empty period; a gap cannot be fitted, so carry the level.
    if series.isna().any():
        series = series.ffill().bfill()
    return series, trimmed


def _future_index(index: pd.DatetimeIndex, rule: str, horizon: int) -> list[pd.Timestamp]:
    freq = index.freqstr or pd.infer_freq(index) or rule
    return list(pd.date_range(index[-1], periods=horizon + 1, freq=freq)[1:])


# ----------------------------------------------------------------------------- backtest


def _backtest(y: np.ndarray, season: int, horizon: int) -> dict[str, Any]:
    """Walk-forward evaluation: refit at several origins, score what was not seen.

    The horizon each fold is scored over is the horizon actually asked for, so the
    reported accuracy is the accuracy of the thing being delivered — not a one-step
    number that flatters every method equally.
    """
    n = len(y)
    minimum_train = max(MIN_OBSERVATIONS - 2, 2 * season if season else 4)
    usable = n - minimum_train
    if usable < 1:
        minimum_train = max(4, n // 2)
        usable = n - minimum_train

    step = max(1, min(horizon, max(1, usable // MAX_FOLDS)))
    origins = list(range(minimum_train, n, step))[-MAX_FOLDS:]
    origins = [o for o in origins if o < n]
    if not origins:
        origins = [max(minimum_train, n - 1)]

    errors: dict[str, list[tuple[int, float, float]]] = {}  # method -> (step, actual, predicted)
    scale_values: list[float] = []
    tested = 0
    for origin in origins:
        train, test = y[:origin], y[origin:origin + horizon]
        if len(test) == 0:
            continue
        tested += len(test)
        scale_values.append(_scale(train, season))
        for key, spec in METHODS.items():
            prediction = spec.predict(train, season or 1, len(test))
            if prediction is None:
                continue
            bucket = errors.setdefault(key, [])
            for index, (actual, predicted) in enumerate(zip(test, prediction)):
                bucket.append((index + 1, float(actual), float(predicted)))

    scale = float(np.mean([s for s in scale_values if s > 0])) if any(s > 0 for s in scale_values) else 0.0
    scores = [s for s in (_score(key, points, scale) for key, points in errors.items()) if s]
    # A method that could not be fitted at every origin has been scored on an easier
    # subset, so it is shown but never allowed to win.
    complete = max((s["points"] for s in scores), default=0)
    for score in scores:
        score["complete"] = score["points"] == complete
    scores.sort(key=lambda s: (not s["complete"],
                               s["mase"] if s["mase"] is not None else s["mae"], s["mae"]))
    return {
        "scores": scores,
        "folds": len(origins),
        "origins": [int(o) for o in origins],
        "tested_points": tested,
        "scale": scale,
        "residuals": {key: points for key, points in errors.items()},
    }


def _scale(train: np.ndarray, season: int) -> float:
    """MASE denominator: the in-sample error of the relevant naive baseline."""
    lag = season if season and len(train) > season else 1
    if len(train) <= lag:
        return 0.0
    return float(np.mean(np.abs(train[lag:] - train[:-lag])))


def _score(key: str, points: list[tuple[int, float, float]], scale: float) -> dict[str, Any] | None:
    if not points:
        return None
    actual = np.array([p[1] for p in points], dtype=float)
    predicted = np.array([p[2] for p in points], dtype=float)
    error = actual - predicted
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(error ** 2)))
    non_zero = actual != 0
    mape = float(np.mean(np.abs(error[non_zero] / actual[non_zero]))) if non_zero.any() else None
    denominator = np.abs(actual) + np.abs(predicted)
    usable = denominator != 0
    smape = float(np.mean(2 * np.abs(error[usable]) / denominator[usable])) if usable.any() else None
    spec = METHODS[key]
    # Error by horizon step: this is why an interval eight periods out is not the same
    # width as the one for next period.
    steps: dict[int, list[float]] = {}
    for step, actual_value, predicted_value in points:
        steps.setdefault(int(step), []).append(abs(actual_value - predicted_value))
    return {
        "method": key,
        "label": spec.label,
        "detail": spec.detail,
        "baseline": spec.baseline,
        "mae": mae,
        "rmse": rmse,
        "mape": mape,
        "smape": smape,
        "mase": (mae / scale) if scale else None,
        "points": len(points),
        "bias": float(np.mean(error)),
        "by_step": [
            {"step": step, "mae": float(np.mean(steps[step])), "points": len(steps[step])}
            for step in sorted(steps)
        ],
    }


def _choose(backtest: dict[str, Any], requested: str) -> dict[str, Any] | None:
    scores = backtest["scores"]
    if not scores:
        return None
    if requested != "auto":
        return next((s for s in scores if s["method"] == requested), None)
    return next((s for s in scores if s["complete"]), scores[0])


def _intervals(residuals: list[tuple[int, float, float]], point: np.ndarray, coverage: float,
               y: np.ndarray) -> dict[str, Any]:
    """Empirical: the spread of this method's own out-of-sample errors at each step."""
    by_step: dict[int, list[float]] = {}
    for step, actual, predicted in residuals:
        by_step.setdefault(int(step), []).append(float(actual - predicted))

    tail = (1 - coverage) / 2
    lower, upper = [], []
    empirical = 0
    pooled = [e for errors in by_step.values() for e in errors]
    fallback = float(np.std(pooled)) if len(pooled) >= 3 else float(np.std(np.diff(y)) or 0.0)
    # z for the requested two-sided coverage, without scipy: a small lookup is honest
    # about being an approximation and avoids a dependency for four numbers.
    z = {0.5: 0.674, 0.6: 0.842, 0.7: 1.036, 0.8: 1.282, 0.9: 1.645, 0.95: 1.960, 0.99: 2.576}
    multiplier = min(z.items(), key=lambda kv: abs(kv[0] - coverage))[1]

    for index in range(len(point)):
        step = index + 1
        errors = by_step.get(step, [])
        if len(errors) >= 3:
            low = float(np.quantile(errors, tail))
            high = float(np.quantile(errors, 1 - tail))
            empirical += 1
        else:
            # No held-out evidence at this step: widen with the square root of the horizon,
            # which is what a random walk does, and say that is what happened.
            width = multiplier * fallback * np.sqrt(step)
            low, high = -width, width
        # A forecast does not become more certain the further out it goes. With a handful
        # of folds an empirical quantile can say otherwise, so the band is held to its
        # widest so far — never narrowed below what the evidence at an earlier step showed.
        low = min(low, lower[-1]) if lower else low
        high = max(high, upper[-1]) if upper else high
        lower.append(low)
        upper.append(high)

    lower = [point[i] + offset for i, offset in enumerate(lower)]
    upper = [point[i] + offset for i, offset in enumerate(upper)]

    floor = float(np.nanmin(y)) >= 0
    lower_array = np.maximum(np.array(lower), 0.0) if floor else np.array(lower)
    return {
        "lower": lower_array,
        "upper": np.array(upper),
        "source": ("backtest errors" if empirical == len(point)
                   else "backtest errors (partly)" if empirical
                   else "residual spread"),
    }


# ----------------------------------------------------------------------------- reading


def _verdict(chosen: dict[str, Any], backtest: dict[str, Any]) -> dict[str, Any]:
    """Does the chosen method actually beat doing nothing clever?"""
    scores = {s["method"]: s for s in backtest["scores"]}
    baselines = [s for s in backtest["scores"] if s["baseline"]]
    best_baseline = min(baselines, key=lambda s: s["mae"]) if baselines else None
    mase = chosen.get("mase")

    if best_baseline is None or chosen["method"] == best_baseline["method"]:
        label = "baseline"
        summary = (
            f"No method beat the {chosen['label'].lower()} baseline on held-out data, so the "
            "baseline is what is shown. That is a finding, not a failure: this series has no "
            "structure worth modelling."
        )
    else:
        improvement = 1 - (chosen["mae"] / best_baseline["mae"]) if best_baseline["mae"] else 0.0
        if improvement <= 0.02:
            label = "no better"
            summary = (
                f"{chosen['label']} is no better than the {best_baseline['label'].lower()} "
                f"baseline out of sample ({percent(abs(improvement))} apart). Treat the projection "
                "as a trajectory, not a number."
            )
        elif mase is not None and mase >= 1:
            label = "weak"
            summary = (
                f"{chosen['label']} beats the other candidates but still scores MASE "
                f"{mase:.2f} — worse than a one-step naive forecast on this history."
            )
        else:
            label = "useful"
            summary = (
                f"{chosen['label']} cut out-of-sample error {percent(improvement)} against the "
                f"{best_baseline['label'].lower()} baseline across {backtest['folds']} refits."
            )
    return {
        "label": label,
        "summary": summary,
        "baseline": best_baseline["method"] if best_baseline else None,
        "baseline_label": best_baseline["label"] if best_baseline else None,
        "baseline_mae": best_baseline["mae"] if best_baseline else None,
        "improvement": (
            None if not best_baseline or not best_baseline["mae"]
            else 1 - chosen["mae"] / best_baseline["mae"]
        ),
        "trustworthy": bool(scores and chosen.get("mase") is not None and chosen["mase"] < 1),
    }


def _totals(y: np.ndarray, point: np.ndarray) -> dict[str, Any]:
    horizon = len(point)
    recent = float(np.sum(y[-horizon:])) if len(y) >= horizon else float(np.sum(y))
    projected = float(np.sum(point))
    compared = len(y[-horizon:])
    return {
        "projected": projected,
        "recent": recent,
        "recent_periods": compared,
        "change": projected - recent,
        "change_pct": ((projected - recent) / abs(recent)) if recent else None,
        "last_actual": float(y[-1]),
        "next_period": float(point[0]),
        "direction": "up" if projected > recent else "down" if projected < recent else "flat",
    }


def _headline(result: dict[str, Any]) -> str:
    totals, noun = result["totals"], result["period_noun"]
    horizon, measure = result["horizon"], result["measure"]
    movement = {"up": "above", "down": "below", "flat": "level with"}[totals["direction"]]
    change = (
        f" — {percent(abs(totals['change_pct']))} {movement} the last {totals['recent_periods']}"
        if totals["change_pct"] is not None else ""
    )
    return (
        f"{measure} is projected at {compact(totals['projected'])} over the next {horizon} "
        f"{noun}{'s' if horizon != 1 else ''}{change}."
    )


def _narrative(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    noun, measure = result["period_noun"], result["measure"]
    forecast = result["forecast"]
    chosen = next((s for s in result["accuracy"] if s["method"] == result["method"]), None)

    first = forecast[0]
    lines.append(
        f"Next {noun}: **{compact(first['value'])}**, with a {percent(result['interval'], 0)} "
        f"interval of {compact(first['lower'])} to {compact(first['upper'])}."
    )
    lines.append(result["verdict"]["summary"])

    if chosen and chosen["mape"] is not None:
        lines.append(
            f"Across {result['backtest']['folds']} walk-forward refits covering "
            f"{result['backtest']['tested_points']} held-out {noun}s, {result['method_label']} was "
            f"off by {percent(chosen['mape'])} on average"
            + (f" (MASE {chosen['mase']:.2f})." if chosen["mase"] is not None else ".")
        )
    if chosen and abs(chosen["bias"]) > 0.15 * max(chosen["mae"], 1e-9):
        direction = "over" if chosen["bias"] < 0 else "under"
        lines.append(
            f"It tends to {direction}-shoot: the average signed error across the backtest is "
            f"{compact(chosen['bias'])}, so read the point forecast as slightly {direction}stated."
        )
    if result["season_length"]:
        lines.append(
            f"A seasonal cycle of {result['season_length']} {noun}s was available to the "
            f"candidates; the winner {'uses' if 'seasonal' in result['method'] else 'does not use'} it."
        )
    last = forecast[-1]
    width = last["upper"] - last["lower"]
    if last["value"]:
        lines.append(
            f"By {noun} {result['horizon']} the interval spans {compact(width)} — "
            f"{percent(width / abs(last['value']))} of the projected value. Anything quoted as a "
            "single number that far out is being quoted with false precision."
        )
    return lines


def _caveats(result: dict[str, Any]) -> list[str]:
    coverage = result["coverage"]
    noun = result["period_noun"]
    caveats = [
        "Every figure assumes the future behaves like the history. A forecast cannot see a "
        "price change, a launch or a lost contract that has not happened yet.",
    ]
    if result["backtest"]["interval_source"] != "backtest errors":
        caveats.append(
            "There was not enough held-out history to measure the error at every horizon step, so "
            "the wider steps of the interval are extrapolated from the residual spread rather than "
            "observed."
        )
    if coverage["observations"] < 24:
        caveats.append(
            f"Only {coverage['observations']} complete {noun}s of history: the backtest has few "
            "points, so the accuracy figures themselves are uncertain."
        )
    if not result["season_length"]:
        caveats.append(
            f"The history is shorter than two full seasonal cycles, so no seasonal method could be "
            f"fitted. A yearly pattern in this {noun}ly series would be invisible here."
        )
    if coverage["trimmed_partial"]:
        caveats.append(
            f"The {' and '.join(coverage['trimmed_partial'])} {noun}(s) were incomplete and have "
            "been dropped, so a partial period does not read as a collapse."
        )
    if coverage["empty_periods"]:
        share = coverage["empty_periods"] / max(coverage["observations"], 1)
        caveats.append(
            f"{coverage['empty_periods']} {noun}(s) ({percent(share)}) have no activity at all. "
            "If those are missing data rather than genuine zeros, the level is understated."
        )
    if coverage["rows_dropped"]:
        caveats.append(
            f"{coverage['rows_dropped']:,} row(s) have no {result['date_column']} or "
            f"{result['measure']} value and are excluded."
        )
    if result["aggregation"] == "mean":
        caveats.append(
            f"{result['measure']} is averaged within each {noun}, not totalled, so the projected "
            "total is a sum of averages — read the per-period values, not the total."
        )
    return caveats


def _follow_up(result: dict[str, Any]) -> str:
    totals = result["totals"]
    direction = "growth" if totals["direction"] == "up" else "decline"
    return (
        f"Which segments are driving the projected {direction} in {result['measure']}, and how "
        f"different is the trajectory between them?"
    )


# ----------------------------------------------------------------------------- artifacts


def _charts(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in (_fan_chart(result), _accuracy_chart(result), _backtest_chart(result)) if c]


def _fan_chart(result: dict[str, Any]) -> dict[str, Any]:
    history = result["history"]
    forecast = result["forecast"]
    noun = result["period_noun"]
    # Anchor the band on the last actual so the fan does not float away from the line.
    anchor_x, anchor_y = history[-1]["period"], history[-1]["value"]
    xs = [anchor_x] + [f["period"] for f in forecast]
    upper = [anchor_y] + [f["upper"] for f in forecast]
    lower = [anchor_y] + [f["lower"] for f in forecast]

    return {
        "title": f"{result['measure']} — next {result['horizon']} {noun}s",
        "caption": (
            f"{result['method_label']}, {result['selection']}. The band is the "
            f"{percent(result['interval'], 0)} interval measured from this method's own "
            f"out-of-sample errors ({result['backtest']['interval_source']})."
        ),
        "figure": {
            "data": [
                {"type": "scatter", "mode": "lines", "x": xs, "y": upper, "name": "Upper",
                 "line": {"width": 0}, "hoverinfo": "skip", "showlegend": False},
                {"type": "scatter", "mode": "lines", "x": xs, "y": lower, "name": "Interval",
                 "line": {"width": 0}, "fill": "tonexty", "fillcolor": BAND,
                 "hoverinfo": "skip", "showlegend": True},
                {"type": "scatter", "mode": "lines", "name": "Actual",
                 "x": [h["period"] for h in history], "y": [h["value"] for h in history],
                 "line": {"color": HISTORY, "width": 2},
                 "hovertemplate": "%{x|%b %Y}<br>%{y:,.2f}<extra></extra>"},
                {"type": "scatter", "mode": "lines+markers", "name": "Forecast",
                 "x": [anchor_x] + [f["period"] for f in forecast],
                 "y": [anchor_y] + [f["value"] for f in forecast],
                 "line": {"color": FORECAST, "width": 2, "dash": "dash"},
                 "marker": {"size": 6},
                 "hovertemplate": "%{x|%b %Y}<br>%{y:,.2f}<extra></extra>"},
            ],
            "layout": {
                "xaxis": {"title": {"text": noun.title()}},
                "yaxis": {"title": {"text": result["measure"]}, "rangemode": "tozero"},
                "legend": {"orientation": "h", "y": 1.14, "x": 0},
                "margin": {"l": 64, "r": 16, "t": 32, "b": 44},
            },
        },
        "digest": {
            "traces": [
                {"type": "line", "name": "Actual",
                 "x": [h["period"][:10] for h in history], "x_count": len(history),
                 "y": [round(h["value"], 4) for h in history], "y_count": len(history)},
                {"type": "line", "name": "Forecast",
                 "x": [f["period"][:10] for f in forecast], "x_count": len(forecast),
                 "y": [round(f["value"], 4) for f in forecast], "y_count": len(forecast)},
            ],
            "axes": {"yaxis": result["measure"]},
        },
    }


def _accuracy_chart(result: dict[str, Any]) -> dict[str, Any] | None:
    scores = [s for s in result["accuracy"] if s["mase"] is not None]
    if len(scores) < 2:
        return None
    scores = sorted(scores, key=lambda s: s["mase"], reverse=True)
    labels = [s["label"] for s in scores]
    values = [round(s["mase"], 4) for s in scores]
    colors = [FORECAST if s["method"] == result["method"] else BASELINE for s in scores]
    return {
        "title": "Out-of-sample accuracy (MASE, lower is better)",
        "caption": (
            "Scaled against the naive baseline's own error: 1.0 means the method is no better "
            "than doing nothing. Measured on data no fit ever saw."
        ),
        "figure": {
            "data": [{
                "type": "bar",
                "orientation": "h",
                "x": values,
                "y": labels,
                "marker": {"color": colors},
                "text": [f"{v:.2f}" for v in values],
                "textposition": "outside",
                "cliponaxis": False,
                "hovertemplate": "%{y}<br>MASE %{x:.3f}<extra></extra>",
            }],
            "layout": {
                "showlegend": False,
                "xaxis": {"title": {"text": "MASE"}, "zeroline": True},
                "yaxis": {"automargin": True, "type": "category"},
                "shapes": [{"type": "line", "x0": 1, "x1": 1, "y0": -0.5, "y1": len(labels) - 0.5,
                            "line": {"color": "#e34948", "width": 1, "dash": "dot"}}],
                "margin": {"l": 150, "r": 40, "t": 24, "b": 44},
            },
        },
        "digest": {
            "traces": [{"type": "bar", "name": "MASE", "x": labels, "x_count": len(labels),
                        "y": values, "y_count": len(values)}],
            "axes": {"yaxis": "MASE"},
        },
    }


def _backtest_chart(result: dict[str, Any]) -> dict[str, Any] | None:
    chosen = next((s for s in result["accuracy"] if s["method"] == result["method"]), None)
    if chosen is None or len(chosen.get("by_step") or []) < 2:
        return None
    steps = [entry["step"] for entry in chosen["by_step"]]
    values = [round(entry["mae"], 4) for entry in chosen["by_step"]]
    noun = result["period_noun"]
    return {
        "title": "How the error grows with the horizon",
        "caption": (
            f"Mean absolute error at each step ahead, measured across "
            f"{result['backtest']['folds']} refits. This is why the interval widens."
        ),
        "figure": {
            "data": [{
                "type": "bar",
                "x": [str(s) for s in steps],
                "y": values,
                "marker": {"color": FORECAST},
                "hovertemplate": f"{noun} %{{x}} ahead<br>MAE %{{y:,.2f}}<extra></extra>",
            }],
            "layout": {
                "showlegend": False,
                "xaxis": {"title": {"text": f"{noun.title()}s ahead"}, "type": "category"},
                "yaxis": {"title": {"text": f"Mean absolute error ({result['measure']})"}},
                "margin": {"l": 64, "r": 16, "t": 24, "b": 44},
            },
        },
        "digest": {
            "traces": [{"type": "bar", "name": "MAE", "x": [str(s) for s in steps],
                        "x_count": len(steps), "y": values, "y_count": len(values)}],
            "axes": {"yaxis": f"Mean absolute error ({result['measure']})"},
        },
    }


def _tables(result: dict[str, Any]) -> list[dict[str, Any]]:
    noun = result["period_noun"]
    forecast_rows = [
        [f["period"][:10], f["step"], round(f["value"], 4), round(f["lower"], 4), round(f["upper"], 4)]
        for f in result["forecast"]
    ]
    accuracy_rows = [
        [
            s["label"],
            None if s["mase"] is None else round(s["mase"], 4),
            round(s["mae"], 4),
            None if s["mape"] is None else round(s["mape"], 4),
            round(s["rmse"], 4),
            s["points"],
        ]
        for s in result["accuracy"]
    ]
    return [
        {
            "title": f"Projected {result['measure']}",
            "columns": [
                {"name": noun.title(), "kind": "text"},
                {"name": "Steps ahead", "kind": "number"},
                {"name": "Forecast", "kind": "number"},
                {"name": f"Lower {percent(result['interval'], 0)}", "kind": "number"},
                {"name": f"Upper {percent(result['interval'], 0)}", "kind": "number"},
            ],
            "rows": forecast_rows,
            "total_rows": len(forecast_rows),
            "truncated": False,
        },
        {
            "title": "Backtest scoreboard",
            "columns": [
                {"name": "Method", "kind": "text"},
                {"name": "MASE", "kind": "number"},
                {"name": "MAE", "kind": "number"},
                {"name": "MAPE", "kind": "number"},
                {"name": "RMSE", "kind": "number"},
                {"name": "Points scored", "kind": "number"},
            ],
            "rows": accuracy_rows,
            "total_rows": len(accuracy_rows),
            "truncated": False,
        },
    ]


# ----------------------------------------------------------------------------- markdown


def project_markdown(result: dict[str, Any], dataset_name: str = "") -> str:
    noun, coverage = result["period_noun"], result["coverage"]
    totals = result["totals"]
    lines = [
        f"# {result['measure']} forecast",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}"
        f"{coverage['first_period'][:10]} → {coverage['last_period'][:10]} · "
        f"{coverage['observations']} complete {noun}s · {result['method_label']} "
        f"({result['selection']})_",
        "",
        f"**{result['headline']}**",
        "",
        f"| | Last {totals['recent_periods']} {noun}s | Next {result['horizon']} {noun}s | Change |",
        "|---|---:|---:|---:|",
        f"| {result['measure']} | {compact(totals['recent'])} | {compact(totals['projected'])} | "
        f"{compact(totals['change'])} ({signed_percent(totals['change_pct'])}) |",
        "",
    ]
    for line in result["narrative"]:
        lines.append(f"- {line}")
    lines += [
        "",
        f"## Projection",
        "",
        f"| {noun.title()} | Forecast | Lower | Upper |",
        "|---|---:|---:|---:|",
    ]
    for row in result["forecast"]:
        lines.append(
            f"| {row['period'][:10]} | {compact(row['value'])} | {compact(row['lower'])} | "
            f"{compact(row['upper'])} |"
        )
    lines += [
        "",
        "## Backtest scoreboard",
        "",
        f"_{result['backtest']['folds']} walk-forward refits · "
        f"{result['backtest']['tested_points']} held-out {noun}s · lower is better_",
        "",
        "| Method | MASE | MAE | MAPE |",
        "|---|---:|---:|---:|",
    ]
    for score in result["accuracy"]:
        mase = "n/a" if score["mase"] is None else f"{score['mase']:.2f}"
        mark = " ✓" if score["method"] == result["method"] else ""
        lines.append(
            f"| {score['label']}{mark} | {mase} | {compact(score['mae'])} | {percent(score['mape'])} |"
        )
    lines.append("")
    if result["caveats"]:
        lines += ["## Caveats", ""] + [f"- {c}" for c in result["caveats"]] + [""]
    lines += [
        "---",
        "",
        "_Every method was refitted from scratch at each origin and scored on periods it had "
        "not seen. No model call._",
    ]
    return "\n".join(lines)
