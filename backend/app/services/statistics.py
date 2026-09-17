"""Significance testing: is the difference real, or is it noise?

Every analyst tool will happily tell you that Segment A converts at 4.1% and Segment B
at 3.8%. Almost none of them tell you that with 210 and 190 records the gap is
indistinguishable from a coin landing slightly differently — which is the only part a
decision actually hangs on.

This module answers that question in plain Python over the cleaned table:

* **Two-group comparison** — Welch's t-test (unequal variances, the honest default),
  Mann-Whitney U for a distribution that is not remotely normal, and a two-proportion
  z-test when the measure is binary.
* **Interval, not just a verdict** — a bootstrap confidence interval for the difference
  itself, because "somewhere between −2% and +9%" is more useful than "p = 0.31".
* **Effect size** — Cohen's *d*, Hedges' *g* and Cliff's delta, so a statistically
  significant difference that is commercially irrelevant is visible as such.
* **Power** — the effect this sample *could* have detected, and the sample size the
  observed effect would need. A null result on 40 rows is "we don't know", not "no".
* **Scan with correction** — testing every category of a dimension at once is twenty
  chances to find a 1-in-20 fluke, so the scan applies Benjamini-Hochberg control of the
  false-discovery rate and reports adjusted p-values.

No scipy: the distribution functions are implemented here (`erf` for the normal, a
continued-fraction incomplete beta for Student's *t*), so the dependency footprint is
unchanged and the arithmetic is auditable. No model call either — this is the part of
the answer a reader has to be able to trust.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.core.errors import InvalidInputError
from app.services.drivers import MAX_CARDINALITY, Window, resolve_windows
from app.services.profiling import rank_measures

logger = logging.getLogger(__name__)

Mode = Literal["segments", "periods"]
MODES = ("segments", "periods")
MetricKind = Literal["mean", "proportion"]

# Below this a group is reported but never called a finding.
MIN_GROUP = 5
# A comparison needs both sides; a dimension with more values than this is not a choice.
MAX_SCAN_CATEGORIES = 60
# Fixed so the interval is identical on every re-run — a confidence interval that moves
# when you refresh the page is not evidence.
BOOTSTRAP_SEED = 20240517
BOOTSTRAP_RESAMPLES = 2000
# Bootstrapping a million rows buys no precision worth the wait.
BOOTSTRAP_MAX_N = 20000

# The standardised effect a comparison is expected to be able to catch — Cohen's
# conventional "medium". A sample that could not have detected an effect this large is
# reported as underpowered rather than as evidence of no difference. The minimum
# detectable effect is also published in the measure's own units, so a reader who knows
# what size actually matters to their business can apply their own threshold instead.
REFERENCE_EFFECT = 0.5
TARGET_POWER = 0.80

GOOD = "#1baf7a"
BAD = "#e34948"
NEUTRAL = "#2a78d6"


# ----------------------------------------------------------------------------- distributions


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_sf(z: float) -> float:
    return 1.0 - _norm_cdf(z)


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, ~1e-9 absolute error)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log(1.0 - x) + a * math.log(x)
    ) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    """P(|T| >= |t|) for Student's t with `df` degrees of freedom."""
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    return float(min(1.0, _betainc(df / 2.0, 0.5, df / (df + t * t))))


def _t_ppf(p: float, df: float) -> float:
    """Two-sided critical value; bisection on the CDF, which is monotone and cheap."""
    if df > 1000:
        return _norm_ppf(p)
    target = 2.0 * (1.0 - p)  # two-sided tail mass we want
    low, high = 0.0, 200.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_two_sided_p(mid, df) > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# ----------------------------------------------------------------------------- options


def significance_options(profile: dict[str, Any]) -> dict[str, Any]:
    """Which measures and dimensions a comparison can be built from."""
    roles = profile.get("roles") or {}
    columns = {c["name"]: c for c in profile.get("columns") or []}

    measures: list[dict[str, Any]] = []
    for name in rank_measures(list(roles.get("measure") or [])):
        measures.append({"name": name, "kind": "mean"})
    for name in list(roles.get("boolean") or []):
        measures.append({"name": name, "kind": "proportion"})
    # An integer column holding only 0/1 is a rate in disguise; treat it as one.
    for name in list(roles.get("dimension") or []):
        info = columns.get(name) or {}
        stats = info.get("stats") or {}
        if info.get("dtype") == "integer" and stats.get("min") == 0 and stats.get("max") == 1:
            measures.append({"name": name, "kind": "proportion"})

    dimensions = [
        name
        for name in (list(roles.get("dimension") or []) + list(roles.get("boolean") or []))
        if 2 <= int(columns.get(name, {}).get("unique") or 0) <= MAX_CARDINALITY
    ]
    dates = list(roles.get("datetime") or [])
    default_measure = measures[0]["name"] if measures else None
    return {
        "measures": measures,
        "dimensions": dimensions,
        "date_columns": dates,
        "defaults": {
            "measure": default_measure,
            "dimension": dimensions[0] if dimensions else None,
            "date_column": dates[0] if dates else None,
            "mode": "segments" if dimensions else "periods",
        },
        "available": bool(default_measure and (dimensions or dates)),
    }


# ----------------------------------------------------------------------------- entry point


def compare(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    measure: str | None = None,
    mode: Mode = "segments",
    dimension: str | None = None,
    group_a: str | None = None,
    group_b: str | None = None,
    date_column: str | None = None,
    period: str = "auto",
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    current_start: str | None = None,
    current_end: str | None = None,
    alpha: float = 0.05,
    scan: bool = True,
) -> dict[str, Any]:
    """Test whether two groups really differ on `measure`. Pure Python, no model call."""
    options = significance_options(profile)
    measure = measure or options["defaults"]["measure"]
    if not measure:
        raise InvalidInputError("This dataset has no numeric or yes/no measure to test.")
    if measure not in df.columns:
        raise InvalidInputError(f"Column '{measure}' is not in this dataset.")
    if mode not in MODES:
        raise InvalidInputError(f"Unknown comparison mode '{mode}'.")
    if not 0.0001 <= alpha <= 0.2:
        raise InvalidInputError("The significance level must be between 0.0001 and 0.2.")

    values = _numeric(df[measure], measure)
    kind: MetricKind = _metric_kind(values, options, measure)

    if mode == "segments":
        dimension = dimension or options["defaults"]["dimension"]
        if not dimension:
            raise InvalidInputError(
                "Comparing segments needs a categorical column; this dataset has none."
            )
        if dimension not in df.columns:
            raise InvalidInputError(f"Column '{dimension}' is not in this dataset.")
        frame = pd.DataFrame({"value": values, "group": _labels(df[dimension])}).dropna()
        a_label, b_label = _pick_groups(frame, group_a, group_b, dimension)
        sample_a = frame.loc[frame["group"] == a_label, "value"].to_numpy(dtype=float)
        sample_b = frame.loc[frame["group"] == b_label, "value"].to_numpy(dtype=float)
        context: dict[str, Any] = {"dimension": dimension, "date_column": None, "period": None}
    else:
        date_column = date_column or options["defaults"]["date_column"]
        if not date_column:
            raise InvalidInputError(
                "Comparing periods needs a date column; this dataset has none."
            )
        if date_column not in df.columns:
            raise InvalidInputError(f"Column '{date_column}' is not in this dataset.")
        if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
            raise InvalidInputError(f"'{date_column}' is not a date column.")
        frame = pd.DataFrame({"value": values, "date": df[date_column]}).dropna()
        if frame.empty:
            raise InvalidInputError(
                f"No rows have both a {date_column} and a {measure} value."
            )
        base_window, current_window, resolved = resolve_windows(
            frame["date"], period, baseline_start, baseline_end, current_start, current_end,
        )
        sample_a = frame.loc[base_window.mask(frame["date"]), "value"].to_numpy(dtype=float)
        sample_b = frame.loc[current_window.mask(frame["date"]), "value"].to_numpy(dtype=float)
        a_label, b_label = base_window.label, current_window.label
        context = {
            "dimension": None,
            "date_column": date_column,
            "period": {
                "mode": resolved,
                "baseline": base_window.to_dict(),
                "current": current_window.to_dict(),
            },
        }

    if sample_a.size < 2 or sample_b.size < 2:
        raise InvalidInputError(
            f"Both groups need at least 2 values to compare; got {sample_a.size:,} "
            f"({a_label}) and {sample_b.size:,} ({b_label})."
        )

    result: dict[str, Any] = {
        "measure": measure,
        "metric_kind": kind,
        "mode": mode,
        "alpha": alpha,
        **context,
        "groups": [_describe(a_label, sample_a, kind, alpha),
                   _describe(b_label, sample_b, kind, alpha)],
    }
    result["difference"] = _difference(sample_a, sample_b, kind, alpha)
    result["tests"] = _tests(sample_a, sample_b, kind)
    primary = result["tests"][0]
    result["primary_test"] = primary["id"]
    result["p_value"] = primary["p_value"]
    result["significant"] = bool(
        primary["p_value"] == primary["p_value"] and primary["p_value"] < alpha
    )
    result["effect"] = _effect(sample_a, sample_b, kind)
    result["power"] = _power(sample_a, sample_b, result["effect"], alpha)
    result["verdict"] = _verdict(result)
    result["scan"] = (
        _scan(df, measure, values, context["dimension"], kind, alpha)
        if scan and mode == "segments" and context["dimension"]
        else None
    )
    result["narrative"] = _narrative(result)
    result["caveats"] = _caveats(result, total_rows=int(len(df)))
    result["charts"] = _charts(result)
    result["tables"] = _tables(result)
    result["follow_up"] = _follow_up(result)
    result["options"] = options
    return result


# ----------------------------------------------------------------------------- inputs


def _numeric(series: pd.Series, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype("Float64").astype(float)
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    raise InvalidInputError(
        f"'{name}' is not numeric or yes/no, so two groups of it cannot be compared."
    )


def _metric_kind(values: pd.Series, options: dict[str, Any], measure: str) -> MetricKind:
    declared = next((m["kind"] for m in options["measures"] if m["name"] == measure), None)
    if declared == "proportion":
        return "proportion"
    observed = pd.unique(values.dropna())
    # A column that only ever holds 0 and 1 is a rate however it was typed.
    if len(observed) <= 2 and set(np.asarray(observed, dtype=float)).issubset({0.0, 1.0}):
        return "proportion"
    return "mean"


def _labels(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.map({True: "Yes", False: "No"}).astype("object")
    return series.astype("object").where(series.notna(), None)


def _pick_groups(frame: pd.DataFrame, group_a: str | None, group_b: str | None,
                 dimension: str) -> tuple[str, str]:
    counts = frame["group"].value_counts()
    available = [str(k) for k in counts.index]
    if len(available) < 2:
        raise InvalidInputError(f"'{dimension}' has fewer than two values with data.")
    chosen: list[str] = []
    for requested in (group_a, group_b):
        if requested is None:
            continue
        if requested not in available:
            preview = ", ".join(available[:6])
            raise InvalidInputError(
                f"'{requested}' is not a value of {dimension}. Available: {preview}"
                f"{'…' if len(available) > 6 else ''}"
            )
        chosen.append(requested)
    # Default to the two largest groups: the comparison most likely to be conclusive.
    for candidate in available:
        if len(chosen) == 2:
            break
        if candidate not in chosen:
            chosen.append(candidate)
    if chosen[0] == chosen[1]:
        raise InvalidInputError("Pick two different values to compare.")
    return chosen[0], chosen[1]


# ----------------------------------------------------------------------------- description


def _describe(label: str, sample: np.ndarray, kind: MetricKind, alpha: float) -> dict[str, Any]:
    n = int(sample.size)
    value = float(sample.mean()) if n else float("nan")
    if kind == "proportion":
        low, high = _wilson(float(sample.sum()), n, alpha)
        sd = math.sqrt(value * (1 - value)) if 0 <= value <= 1 else float("nan")
    else:
        sd = float(sample.std(ddof=1)) if n > 1 else 0.0
        margin = _t_ppf(1 - alpha / 2, n - 1) * sd / math.sqrt(n) if n > 1 and sd > 0 else 0.0
        low, high = value - margin, value + margin
    return {
        "label": str(label),
        "n": n,
        "value": _clean(value),
        "sd": _clean(sd),
        "ci_low": _clean(low),
        "ci_high": _clean(high),
        "sum": _clean(float(sample.sum())) if n else None,
        "small": n < MIN_GROUP,
    }


def _wilson(successes: float, n: int, alpha: float) -> tuple[float, float]:
    """Wilson score interval — behaves at 0% and 100%, where the textbook one does not."""
    if n == 0:
        return float("nan"), float("nan")
    z = _norm_ppf(1 - alpha / 2)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _difference(a: np.ndarray, b: np.ndarray, kind: MetricKind, alpha: float) -> dict[str, Any]:
    mean_a, mean_b = float(a.mean()), float(b.mean())
    absolute = mean_b - mean_a
    low, high = _bootstrap_ci(a, b, alpha)
    return {
        "absolute": _clean(absolute),
        "relative": _clean(absolute / abs(mean_a)) if mean_a else None,
        "ci_low": _clean(low),
        "ci_high": _clean(high),
        "ci_method": "percentile bootstrap (2,000 resamples, fixed seed)",
        "crosses_zero": bool(low <= 0 <= high) if math.isfinite(low) and math.isfinite(high) else None,
        "direction": "up" if absolute > 0 else "down" if absolute < 0 else "flat",
        "kind": kind,
    }


def _bootstrap_ci(a: np.ndarray, b: np.ndarray, alpha: float) -> tuple[float, float]:
    """Percentile bootstrap of the difference in means. Seeded, so it never moves."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sample_a = a if a.size <= BOOTSTRAP_MAX_N else rng.choice(a, BOOTSTRAP_MAX_N, replace=False)
    sample_b = b if b.size <= BOOTSTRAP_MAX_N else rng.choice(b, BOOTSTRAP_MAX_N, replace=False)
    draws_a = rng.integers(0, sample_a.size, size=(BOOTSTRAP_RESAMPLES, sample_a.size))
    draws_b = rng.integers(0, sample_b.size, size=(BOOTSTRAP_RESAMPLES, sample_b.size))
    diffs = sample_b[draws_b].mean(axis=1) - sample_a[draws_a].mean(axis=1)
    low, high = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(low), float(high)


# ----------------------------------------------------------------------------- tests


def _tests(a: np.ndarray, b: np.ndarray, kind: MetricKind) -> list[dict[str, Any]]:
    """The primary test first; the others are corroboration under different assumptions."""
    if kind == "proportion":
        tests = [_two_proportion_z(a, b)]
        if min(a.size, b.size) >= 2:
            tests.append(_welch(a, b))
        return tests
    return [_welch(a, b), _mann_whitney(a, b)]


def _welch(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Welch's t-test: does not assume the two groups share a variance, and almost
    nothing in a business dataset does."""
    n_a, n_b = a.size, b.size
    var_a = float(a.var(ddof=1)) if n_a > 1 else 0.0
    var_b = float(b.var(ddof=1)) if n_b > 1 else 0.0
    se_sq = var_a / n_a + var_b / n_b
    if se_sq <= 0:
        return {"id": "welch_t", "name": "Welch's t-test", "statistic": None, "df": None,
                "p_value": float("nan"),
                "detail": "Both groups have zero variance, so no test applies.",
                "assumption": "Means are comparable; unequal variances allowed."}
    t = (float(b.mean()) - float(a.mean())) / math.sqrt(se_sq)
    df = se_sq ** 2 / (
        (var_a / n_a) ** 2 / max(n_a - 1, 1) + (var_b / n_b) ** 2 / max(n_b - 1, 1)
    )
    p = t_two_sided_p(t, df)
    return {
        "id": "welch_t", "name": "Welch's t-test",
        "statistic": _clean(t), "df": _clean(df), "p_value": _clean(p),
        "detail": f"t = {t:.3f} on {df:.1f} degrees of freedom",
        "assumption": "Compares means without assuming equal variances.",
    }


def _mann_whitney(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Rank-based, so a handful of outliers cannot manufacture a difference."""
    n_a, n_b = a.size, b.size
    combined = np.concatenate([a, b])
    ranks = pd.Series(combined).rank(method="average").to_numpy()
    rank_sum_a = float(ranks[:n_a].sum())
    u_a = rank_sum_a - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)

    _, counts = np.unique(combined, return_counts=True)
    ties = float(np.sum(counts ** 3 - counts))
    n = n_a + n_b
    variance = n_a * n_b / 12.0 * ((n + 1) - ties / (n * (n - 1))) if n > 1 else 0.0
    if variance <= 0:
        return {"id": "mann_whitney", "name": "Mann-Whitney U", "statistic": _clean(u),
                "df": None, "p_value": float("nan"),
                "detail": "Every value is tied, so the ranks carry no information.",
                "assumption": "Rank-based; insensitive to outliers and skew."}
    mean_u = n_a * n_b / 2.0
    # Continuity correction: U is discrete, the normal approximation is not.
    z = (u - mean_u + 0.5) / math.sqrt(variance)
    p = 2.0 * _norm_cdf(z) if z < 0 else 2.0 * _norm_sf(z)
    return {
        "id": "mann_whitney", "name": "Mann-Whitney U",
        "statistic": _clean(u), "df": None, "p_value": _clean(min(1.0, p)),
        "detail": f"U = {u:,.0f}, z = {z:.3f} (tie-corrected, normal approximation)",
        "assumption": "Rank-based; insensitive to outliers and skew.",
    }


def _two_proportion_z(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """Pooled two-proportion z-test — the right test for a rate, not a mean."""
    n_a, n_b = a.size, b.size
    x_a, x_b = float(a.sum()), float(b.sum())
    p_pool = (x_a + x_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se <= 0:
        return {"id": "two_proportion_z", "name": "Two-proportion z-test", "statistic": None,
                "df": None, "p_value": float("nan"),
                "detail": "Both groups share the same constant rate.",
                "assumption": "Binary outcome; normal approximation to the binomial."}
    z = (x_b / n_b - x_a / n_a) / se
    p = 2.0 * _norm_sf(abs(z))
    expected = min(n_a * p_pool, n_b * p_pool, n_a * (1 - p_pool), n_b * (1 - p_pool))
    return {
        "id": "two_proportion_z", "name": "Two-proportion z-test",
        "statistic": _clean(z), "df": None, "p_value": _clean(min(1.0, p)),
        "detail": f"z = {z:.3f}, pooled rate {p_pool:.2%}"
                  + ("" if expected >= 5 else "; fewer than 5 expected events — approximate"),
        "assumption": "Binary outcome; normal approximation to the binomial.",
    }


# ----------------------------------------------------------------------------- effect & power


def _effect(a: np.ndarray, b: np.ndarray, kind: MetricKind) -> dict[str, Any]:
    n_a, n_b = a.size, b.size
    var_a = float(a.var(ddof=1)) if n_a > 1 else 0.0
    var_b = float(b.var(ddof=1)) if n_b > 1 else 0.0
    pooled_sd = math.sqrt(
        ((n_a - 1) * var_a + (n_b - 1) * var_b) / max(n_a + n_b - 2, 1)
    )
    d = (float(b.mean()) - float(a.mean())) / pooled_sd if pooled_sd > 0 else 0.0
    # Hedges' correction: Cohen's d is biased upward in small samples.
    correction = 1 - 3 / (4 * (n_a + n_b) - 9) if n_a + n_b > 3 else 1.0
    magnitude = ("negligible" if abs(d) < 0.2 else "small" if abs(d) < 0.5
                 else "medium" if abs(d) < 0.8 else "large")
    return {
        "cohens_d": _clean(d),
        "hedges_g": _clean(d * correction),
        "cliffs_delta": _clean(_cliffs_delta(a, b)),
        "pooled_sd": _clean(pooled_sd),
        "magnitude": magnitude,
        "scale": "proportion points" if kind == "proportion" else "standard deviations",
    }


def _cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """P(b > a) − P(a > b). Derived from the rank sum, so it is O(n log n), not O(n²)."""
    n_a, n_b = a.size, b.size
    if n_a == 0 or n_b == 0:
        return float("nan")
    ranks = pd.Series(np.concatenate([a, b])).rank(method="average").to_numpy()
    u_a = float(ranks[:n_a].sum()) - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    return (u_b - u_a) / (n_a * n_b)


def _power(a: np.ndarray, b: np.ndarray, effect: dict[str, Any], alpha: float) -> dict[str, Any]:
    """How small an effect this sample could have caught.

    Deliberately *not* observed (post-hoc) power. Power computed from the effect the data
    happened to show is a monotone function of the p-value: it carries no information the
    p-value did not, and it makes every non-significant result look underpowered —
    including a well-powered genuine null, which is a real and useful finding.

    The question worth answering is prospective: what is the smallest difference this
    many records could have detected at 80% power? If that minimum detectable effect is
    smaller than an effect anyone would act on, a null result means "no effect of a size
    that matters". If it is larger, the comparison genuinely could not tell.
    """
    n_a, n_b = a.size, b.size
    harmonic = 2 / (1 / n_a + 1 / n_b) if n_a and n_b else 0
    z_alpha = _norm_ppf(1 - alpha / 2)
    z_beta = _norm_ppf(TARGET_POWER)
    d = abs(effect["cohens_d"] or 0.0)
    pooled_sd = effect["pooled_sd"] or 0.0

    mde = (z_alpha + z_beta) * math.sqrt(2 / harmonic) if harmonic > 0 else None
    required = int(math.ceil(2 * (z_alpha + z_beta) ** 2 / (d * d))) if d > 0 else None
    # Power this sample has against the reference effect — a real, prospective number.
    power_at_reference = None
    if harmonic > 0:
        ncp = REFERENCE_EFFECT * math.sqrt(harmonic / 2)
        power_at_reference = float(
            min(1.0, max(0.0, _norm_sf(z_alpha - ncp) + _norm_cdf(-z_alpha - ncp)))
        )
    return {
        "required_n_per_group": required,
        "mde_standardised": _clean(mde) if mde is not None else None,
        "mde_absolute": _clean(mde * pooled_sd) if mde is not None else None,
        "reference_effect": REFERENCE_EFFECT,
        "power_at_reference": _clean(power_at_reference) if power_at_reference is not None else None,
        "target_power": TARGET_POWER,
        "adequate": bool(mde is not None and mde <= REFERENCE_EFFECT),
    }


# ----------------------------------------------------------------------------- scan


def _scan(df: pd.DataFrame, measure: str, values: pd.Series, dimension: str,
          kind: MetricKind, alpha: float) -> dict[str, Any] | None:
    """Every category against everything else, with the false-discovery rate controlled.

    Twenty independent tests at α = 0.05 produce one "significant" result by luck alone.
    Reporting the raw p-values from a scan is how a dashboard manufactures findings.
    """
    frame = pd.DataFrame({"value": values, "group": _labels(df[dimension])}).dropna()
    labels = [str(k) for k in frame["group"].value_counts().index]
    if len(labels) < 2:
        return None
    if len(labels) > MAX_SCAN_CATEGORIES:
        labels = labels[:MAX_SCAN_CATEGORIES]

    rows: list[dict[str, Any]] = []
    for label in labels:
        inside = frame.loc[frame["group"] == label, "value"].to_numpy(dtype=float)
        outside = frame.loc[frame["group"] != label, "value"].to_numpy(dtype=float)
        if inside.size < 2 or outside.size < 2:
            continue
        test = _two_proportion_z(outside, inside) if kind == "proportion" else _welch(outside, inside)
        rows.append({
            "label": label,
            "n": int(inside.size),
            "value": _clean(float(inside.mean())),
            "rest_value": _clean(float(outside.mean())),
            "difference": _clean(float(inside.mean()) - float(outside.mean())),
            "p_value": test["p_value"],
            "small": inside.size < MIN_GROUP,
        })
    if not rows:
        return None

    adjusted = benjamini_hochberg([r["p_value"] for r in rows])
    for row, q in zip(rows, adjusted):
        row["p_adjusted"] = _clean(q)
        row["significant"] = bool(q == q and q < alpha)
    rows.sort(key=lambda r: (not r["significant"], r["p_adjusted"] if r["p_adjusted"] == r["p_adjusted"] else 1.0))
    found = [r for r in rows if r["significant"]]
    raw_hits = sum(1 for r in rows if r["p_value"] == r["p_value"] and r["p_value"] < alpha)
    return {
        "dimension": dimension,
        "measure": measure,
        "rows": rows,
        "n_tests": len(rows),
        "n_significant": len(found),
        "n_significant_uncorrected": raw_hits,
        "method": "Benjamini-Hochberg false-discovery-rate control",
        "truncated": len(labels) >= MAX_SCAN_CATEGORIES,
    }


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Step-up FDR adjustment. Returns q-values in the input order."""
    usable = [(i, p) for i, p in enumerate(p_values) if p == p]  # drop NaN
    adjusted = [float("nan")] * len(p_values)
    if not usable:
        return adjusted
    usable.sort(key=lambda pair: pair[1])
    m = len(usable)
    running = 1.0
    for rank in range(m, 0, -1):
        index, p = usable[rank - 1]
        running = min(running, p * m / rank)
        adjusted[index] = min(1.0, running)
    return adjusted


# ----------------------------------------------------------------------------- verdict


def _verdict(result: dict[str, Any]) -> dict[str, Any]:
    a, b = result["groups"]
    difference = result["difference"]
    power = result["power"]
    p = result["p_value"]
    measure = result["measure"]
    gap = _value(difference["absolute"], result["metric_kind"])

    if p != p:  # NaN — no test could run
        return {"label": "inconclusive", "tone": "neutral",
                "headline": f"{measure} could not be tested between {a['label']} and {b['label']}.",
                "detail": "There is no variation in the data for a test to work with."}
    if result["significant"]:
        confidence = "very strong" if p < 0.001 else "strong" if p < 0.01 else "moderate"
        return {
            "label": "real", "tone": "good",
            "headline": f"{b['label']} really does differ from {a['label']} on {measure}.",
            "detail": (
                f"The {gap} gap is {confidence} evidence (p = {_p(p)}) and the "
                f"{int((1 - result['alpha']) * 100)}% interval "
                f"({_value(difference['ci_low'], result['metric_kind'])} to "
                f"{_value(difference['ci_high'], result['metric_kind'])}) excludes zero. "
                f"The effect is {result['effect']['magnitude']}."
            ),
        }
    if not power["adequate"]:
        needed = power["required_n_per_group"]
        return {
            "label": "underpowered", "tone": "warn",
            "headline": f"Not enough data to say whether {measure} differs.",
            "detail": (
                f"The {gap} gap is not significant (p = {_p(p)}), but with {a['n']:,} and "
                f"{b['n']:,} records this comparison could only have detected a difference of "
                f"about {_value(power['mde_absolute'], result['metric_kind'])} or more. "
                + (f"Confirming an effect this size would need roughly {needed:,} records per group."
                   if needed else "Collect more data before concluding anything.")
            ),
        }
    return {
        "label": "noise", "tone": "neutral",
        "headline": f"{b['label']} and {a['label']} are not distinguishable on {measure}.",
        "detail": (
            f"The {gap} gap is well within what this much data produces by chance "
            f"(p = {_p(p)}), and the interval "
            f"({_value(difference['ci_low'], result['metric_kind'])} to "
            f"{_value(difference['ci_high'], result['metric_kind'])}) includes zero. "
            f"With {a['n']:,} and {b['n']:,} records this comparison would have caught a "
            f"difference of {_value(power['mde_absolute'], result['metric_kind'])} or more, "
            f"so this is a real null rather than a shrug."
        ),
    }


# ----------------------------------------------------------------------------- narrative


def _narrative(result: dict[str, Any]) -> list[str]:
    a, b = result["groups"]
    kind = result["metric_kind"]
    lines = [
        f"**{a['label']}**: {_value(a['value'], kind)} across {a['n']:,} records "
        f"({int((1 - result['alpha']) * 100)}% CI {_value(a['ci_low'], kind)} – {_value(a['ci_high'], kind)}). "
        f"**{b['label']}**: {_value(b['value'], kind)} across {b['n']:,} records "
        f"({_value(b['ci_low'], kind)} – {_value(b['ci_high'], kind)})."
    ]
    difference = result["difference"]
    relative = f" ({_pct(difference['relative'])} relative)" if difference["relative"] is not None else ""
    lines.append(
        f"The difference is {_value(difference['absolute'], kind)}{relative}, with a bootstrap "
        f"interval of {_value(difference['ci_low'], kind)} to {_value(difference['ci_high'], kind)} — "
        + ("which includes zero, so the direction itself is not established."
           if difference["crosses_zero"] else "which does not include zero.")
    )
    for test in result["tests"]:
        if test["p_value"] == test["p_value"]:
            lines.append(f"{test['name']}: p = {_p(test['p_value'])}. {test['detail']}.")
    effect = result["effect"]
    if effect["cohens_d"] is not None:
        lines.append(
            f"Effect size is {effect['magnitude']} (Hedges' g = {effect['hedges_g']:.2f}; "
            f"Cliff's delta = {effect['cliffs_delta']:.2f}) — statistical significance and "
            f"business significance are different questions."
        )
    scan = result.get("scan")
    if scan:
        extra = scan["n_significant_uncorrected"] - scan["n_significant"]
        lines.append(
            f"Across all {scan['n_tests']} {scan['dimension']} values tested against the rest, "
            f"{scan['n_significant']} survive false-discovery correction"
            + (f" — {extra} more looked significant before it, which is what multiple testing does."
               if extra > 0 else ".")
        )
    return lines


def _caveats(result: dict[str, Any], *, total_rows: int) -> list[str]:
    caveats: list[str] = []
    a, b = result["groups"]
    used = a["n"] + b["n"]
    if used < total_rows:
        caveats.append(
            f"{used:,} of {total_rows:,} rows fall into the two compared groups; the rest are "
            "outside them or missing a value."
        )
    if a["small"] or b["small"]:
        caveats.append(
            f"A group with fewer than {MIN_GROUP} records cannot support a conclusion, "
            "whatever the p-value says."
        )
    if result["mode"] == "periods":
        caveats.append(
            "The two periods are equal-length windows anchored at the most recent record; a "
            "partially-loaded final period will bias the comparison."
        )
    else:
        caveats.append(
            "This is observational data, not a randomised experiment: a real difference "
            "between these groups is not evidence that the grouping caused it."
        )
    if result["metric_kind"] == "mean" and result["effect"]["pooled_sd"]:
        caveats.append(
            "Welch's test compares means. If the distribution is heavily skewed, read the "
            "Mann-Whitney result — it compares ranks and is not moved by a few extreme values."
        )
    if not result["power"]["adequate"] and not result["significant"]:
        caveats.append(
            "Absence of evidence is not evidence of absence: this comparison is underpowered, "
            "so a real effect could still be hiding in it."
        )
    return caveats


def _follow_up(result: dict[str, Any]) -> str:
    a, b = result["groups"]
    measure = result["measure"]
    if result["significant"]:
        return (
            f"Why does {measure} differ between {a['label']} and {b['label']}, and what else "
            f"separates those two groups?"
        )
    return (
        f"What would it take to tell {a['label']} and {b['label']} apart on {measure}, and is "
        f"another segmentation more informative?"
    )


# ----------------------------------------------------------------------------- artifacts


def _charts(result: dict[str, Any]) -> list[dict[str, Any]]:
    a, b = result["groups"]
    kind = result["metric_kind"]
    tone = GOOD if result["significant"] else NEUTRAL
    charts = [{
        "title": f"{result['measure']} with {int((1 - result['alpha']) * 100)}% confidence intervals",
        "caption": (
            "Overlapping intervals mean the gap is within what this much data produces by chance. "
            + ("The intervals here are separated." if result["significant"]
               else "The intervals here overlap.")
        ),
        "figure": {
            "data": [{
                "type": "scatter", "mode": "markers", "orientation": "h",
                "x": [a["value"], b["value"]],
                "y": [a["label"], b["label"]],
                "error_x": {
                    "type": "data", "symmetric": False,
                    "array": [a["ci_high"] - a["value"], b["ci_high"] - b["value"]],
                    "arrayminus": [a["value"] - a["ci_low"], b["value"] - b["ci_low"]],
                    "color": tone, "thickness": 2, "width": 8,
                },
                "marker": {"size": 12, "color": tone},
                "hovertemplate": "%{y}<br>%{x:,.4g}<extra></extra>",
            }],
            "layout": {
                "showlegend": False,
                "xaxis": {"title": {"text": result["measure"]}, "zeroline": False},
                "yaxis": {"automargin": True},
                "margin": {"l": 90, "r": 24, "t": 24, "b": 48},
            },
        },
        "digest": {
            "traces": [{
                "type": "bar", "name": result["measure"],
                "x": [a["label"], b["label"]], "x_count": 2,
                "y": [round(float(a["value"]), 6), round(float(b["value"]), 6)], "y_count": 2,
            }],
            "axes": {"yaxis": result["measure"]},
        },
    }]

    scan = result.get("scan")
    if scan and len(scan["rows"]) > 1:
        rows = scan["rows"][:20]
        charts.append({
            "title": f"{scan['measure']} by {scan['dimension']} — each value against the rest",
            "caption": (
                f"Bars are the gap versus every other {scan['dimension']}; "
                f"{scan['n_significant']} of {scan['n_tests']} survive false-discovery correction."
            ),
            "figure": {
                "data": [{
                    "type": "bar", "orientation": "h",
                    "x": [r["difference"] for r in rows],
                    "y": [r["label"] for r in rows],
                    "marker": {"color": [GOOD if r["significant"] else "#c3c2b7" for r in rows]},
                    "hovertemplate": "%{y}<br>%{x:,.4g}<extra></extra>",
                }],
                "layout": {
                    "showlegend": False,
                    "xaxis": {"title": {"text": f"Difference in {scan['measure']}"}, "zeroline": True},
                    "yaxis": {"automargin": True, "autorange": "reversed"},
                    "margin": {"l": 120, "r": 24, "t": 24, "b": 48},
                },
            },
            "digest": {
                "traces": [{
                    "type": "bar", "name": f"Difference in {scan['measure']}",
                    "x": [r["label"] for r in rows], "x_count": len(rows),
                    "y": [round(float(r["difference"]), 6) for r in rows], "y_count": len(rows),
                }],
                "axes": {"yaxis": scan["measure"]},
            },
        })
    return charts


def _tables(result: dict[str, Any]) -> list[dict[str, Any]]:
    tables = [{
        "title": f"{result['measure']} — group comparison",
        "columns": [
            {"name": "Group", "kind": "text"},
            {"name": "Records", "kind": "number"},
            {"name": "Value", "kind": "number"},
            {"name": "CI low", "kind": "number"},
            {"name": "CI high", "kind": "number"},
            {"name": "Std dev", "kind": "number"},
        ],
        "rows": [
            [g["label"], g["n"], _round(g["value"]), _round(g["ci_low"]),
             _round(g["ci_high"]), _round(g["sd"])]
            for g in result["groups"]
        ],
        "total_rows": 2,
        "truncated": False,
    }]
    scan = result.get("scan")
    if scan:
        tables.append({
            "title": f"{scan['measure']} by {scan['dimension']} — corrected for multiple testing",
            "columns": [
                {"name": scan["dimension"], "kind": "text"},
                {"name": "Records", "kind": "number"},
                {"name": "Value", "kind": "number"},
                {"name": "Rest", "kind": "number"},
                {"name": "Difference", "kind": "number"},
                {"name": "p", "kind": "number"},
                {"name": "q (BH)", "kind": "number"},
                {"name": "Significant", "kind": "text"},
            ],
            "rows": [
                [r["label"], r["n"], _round(r["value"]), _round(r["rest_value"]),
                 _round(r["difference"]), _round(r["p_value"], 5), _round(r["p_adjusted"], 5),
                 "Yes" if r["significant"] else "No"]
                for r in scan["rows"]
            ],
            "total_rows": len(scan["rows"]),
            "truncated": scan["truncated"],
        })
    return tables


# ----------------------------------------------------------------------------- formatting


def _clean(value: float | None) -> float | None:
    """NaN and ±inf are not JSON; they are also not answers."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else float("nan")


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or value != value:
        return None
    return round(float(value), digits)


def _value(value: float | None, kind: MetricKind) -> str:
    if value is None or value != value:
        return "n/a"
    if kind == "proportion":
        return f"{value:+.2%}" if abs(value) < 1 else f"{value:.2%}"
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{'−' if value < 0 else ''}{magnitude / threshold:,.2f}{suffix}"
    return f"{'−' if value < 0 else ''}{magnitude:,.4g}"


def _pct(value: float | None) -> str:
    if value is None or value != value:
        return "n/a"
    return f"{'−' if value < 0 else '+'}{abs(value):.1%}"


def _p(value: float | None) -> str:
    if value is None or value != value:
        return "n/a"
    if value < 0.0001:
        return "< 0.0001"
    return f"{value:.4f}"


# ----------------------------------------------------------------------------- markdown


def compare_markdown(result: dict[str, Any], dataset_name: str = "") -> str:
    kind = result["metric_kind"]
    a, b = result["groups"]
    verdict = result["verdict"]
    subject = (
        f"{result['dimension']}: {a['label']} vs {b['label']}" if result["dimension"]
        else f"{a['label']} vs {b['label']}"
    )
    lines = [
        f"# Is the difference in {result['measure']} real?",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}{subject}_",
        "",
        f"**{verdict['headline']}**",
        "",
        verdict["detail"],
        "",
        "| Group | Records | Value | CI low | CI high |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in result["groups"]:
        lines.append(
            f"| {group['label']} | {group['n']:,} | {_value(group['value'], kind)} | "
            f"{_value(group['ci_low'], kind)} | {_value(group['ci_high'], kind)} |"
        )
    difference = result["difference"]
    lines += [
        "",
        f"**Difference** {_value(difference['absolute'], kind)} "
        f"({int((1 - result['alpha']) * 100)}% CI {_value(difference['ci_low'], kind)} to "
        f"{_value(difference['ci_high'], kind)}, {difference['ci_method']})",
        "",
        "## Tests",
        "",
        "| Test | Statistic | p | Assumption |",
        "|---|---:|---:|---|",
    ]
    for test in result["tests"]:
        statistic = "n/a" if test["statistic"] is None else f"{test['statistic']:.4f}"
        lines.append(f"| {test['name']} | {statistic} | {_p(test['p_value'])} | {test['assumption']} |")

    effect, power = result["effect"], result["power"]
    lines += [
        "",
        "## Effect and power",
        "",
        f"- Effect size: **{effect['magnitude']}** (Cohen's d {effect['cohens_d']:.3f}, "
        f"Hedges' g {effect['hedges_g']:.3f}, Cliff's delta {effect['cliffs_delta']:.3f})",
        f"- Power to detect a medium effect (d = {power['reference_effect']}): "
        + (f"{power['power_at_reference']:.0%}" if power["power_at_reference"] is not None else "n/a")
        + f" (target {power['target_power']:.0%})",
        f"- Smallest difference this sample could detect at {power['target_power']:.0%} power: "
        f"{_value(power['mde_absolute'], kind)}",
    ]
    if power["required_n_per_group"]:
        lines.append(
            f"- Records per group needed to confirm an effect this size: "
            f"{power['required_n_per_group']:,}"
        )

    scan = result.get("scan")
    if scan:
        lines += [
            "",
            f"## Every {scan['dimension']} against the rest",
            "",
            f"_{scan['method']}; {scan['n_significant']} of {scan['n_tests']} significant "
            f"after correction ({scan['n_significant_uncorrected']} before)._",
            "",
            f"| {scan['dimension']} | Records | Value | Rest | Difference | p | q |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in scan["rows"]:
            label = f"**{row['label']}**" if row["significant"] else row["label"]
            lines.append(
                f"| {label} | {row['n']:,} | "
                f"{_value(row['value'], kind)} | {_value(row['rest_value'], kind)} | "
                f"{_value(row['difference'], kind)} | {_p(row['p_value'])} | "
                f"{_p(row['p_adjusted'])} |"
            )

    if result["caveats"]:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in result["caveats"]]
    lines += [
        "",
        "---",
        "",
        "_Computed deterministically from the cleaned table — no model call, and the bootstrap "
        "is seeded, so this document reproduces exactly._",
    ]
    return "\n".join(lines)


__all__ = [
    "benjamini_hochberg",
    "compare",
    "compare_markdown",
    "significance_options",
    "t_two_sided_p",
    "Window",
]
