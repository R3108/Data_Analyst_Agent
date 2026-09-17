"""Driver analysis: *why* a number moved, decomposed deterministically.

An answer like "revenue fell 12%" is only half an answer. The half that matters is
which segments moved, whether the business sold less or sold cheaper, and which
part of the change is simply the mix tilting between segments.

This module computes that in plain pandas — no model call, no cost, no chance of a
plausible-sounding invention:

* **Contribution** — each category's share of the absolute change, and how much of
  that is a *surprise* (more or less than its baseline size implies).
* **Shift-share** — the change split into three terms that add back exactly to it:
  volume (more or fewer records), mix (share moving between categories) and rate
  (the average value per record).
* **Ranking** — which dimension explains the move best, scored by how unevenly the
  change is spread across its categories relative to their baseline weight.

Every figure here is reproducible from the cleaned table, which is what lets the
verifier and the user check it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from app.core.errors import InvalidInputError
from app.core.formatting import compact, percent
from app.services.profiling import NON_ADDITIVE, rank_measures

logger = logging.getLogger(__name__)

# Categories beyond this are bucketed into "Other" so a waterfall stays readable.
MAX_CATEGORIES = 30
# A dimension with more distinct values than this is not a useful explanation.
MAX_CARDINALITY = 300
DEFAULT_TOP_N = 8
# Below this a period comparison is noise, not a finding.
MIN_ROWS_PER_PERIOD = 5
MAX_DIMENSIONS = 8

PeriodMode = Literal["auto", "yoy", "month", "week", "halves", "custom"]
PERIOD_MODES = ("auto", "yoy", "month", "week", "halves", "custom")
Aggregation = Literal["sum", "mean"]

INCREASE = "#1baf7a"
DECREASE = "#e34948"
TOTAL = "#2a78d6"
CONNECTOR = "#c3c2b7"


@dataclass(frozen=True)
class Window:
    """One side of the comparison, as a half-open interval of timestamps."""

    start: pd.Timestamp
    end: pd.Timestamp
    label: str
    closed_left: bool = True

    def mask(self, dates: pd.Series) -> pd.Series:
        left = dates >= self.start if self.closed_left else dates > self.start
        return left & (dates <= self.end)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label}


# ----------------------------------------------------------------------------- options


def driver_options(profile: dict[str, Any]) -> dict[str, Any]:
    """Measures, dimensions and date columns a drill-down can be built from."""
    roles = profile.get("roles") or {}
    columns = {c["name"]: c for c in profile.get("columns") or []}

    measures = rank_measures(list(roles.get("measure") or []))
    dates = list(roles.get("datetime") or [])
    dimensions = [
        name
        for name in (list(roles.get("dimension") or []) + list(roles.get("boolean") or []))
        if 2 <= int(columns.get(name, {}).get("unique") or 0) <= MAX_CARDINALITY
    ]

    additive = [m for m in measures if not NON_ADDITIVE.search(m)]
    default_measure = (additive or measures or [None])[0]
    return {
        "measures": [
            {"name": name, "aggregation": default_aggregation(name)} for name in measures
        ],
        "dimensions": dimensions,
        "date_columns": dates,
        "defaults": {
            "measure": default_measure,
            "date_column": dates[0] if dates else None,
            "aggregation": default_aggregation(default_measure) if default_measure else "sum",
            "period": "auto",
        },
        "available": bool(default_measure and dates and dimensions),
    }


def default_aggregation(measure: str) -> Aggregation:
    """Rates, prices and percentages must not be summed across rows."""
    return "mean" if NON_ADDITIVE.search(measure) else "sum"


# ----------------------------------------------------------------------------- entry point


def explain(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    measure: str | None = None,
    date_column: str | None = None,
    dimensions: list[str] | None = None,
    focus: str | None = None,
    aggregation: Aggregation | None = None,
    period: PeriodMode = "auto",
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    current_start: str | None = None,
    current_end: str | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Decompose the change in `measure` between two periods. Pure pandas."""
    options = driver_options(profile)
    measure = measure or options["defaults"]["measure"]
    date_column = date_column or options["defaults"]["date_column"]

    if not measure:
        raise InvalidInputError("This dataset has no numeric measure to explain.")
    if measure not in df.columns:
        raise InvalidInputError(f"Column '{measure}' is not in this dataset.")
    if not date_column:
        raise InvalidInputError(
            "Explaining a change needs a date column; this dataset has none."
        )
    if date_column not in df.columns:
        raise InvalidInputError(f"Column '{date_column}' is not in this dataset.")
    if not pd.api.types.is_numeric_dtype(df[measure]):
        raise InvalidInputError(f"'{measure}' is not numeric, so its change cannot be decomposed.")
    if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
        raise InvalidInputError(f"'{date_column}' is not a date column.")
    if period not in PERIOD_MODES:
        raise InvalidInputError(f"Unknown comparison period '{period}'.")

    agg: Aggregation = aggregation or default_aggregation(measure)
    if agg not in ("sum", "mean"):
        raise InvalidInputError("Aggregation must be 'sum' or 'mean'.")

    candidates = [d for d in (dimensions or options["dimensions"]) if d in df.columns]
    if dimensions:
        unknown = [d for d in dimensions if d not in df.columns]
        if unknown:
            raise InvalidInputError(f"Column '{unknown[0]}' is not in this dataset.")
    candidates = candidates[:MAX_DIMENSIONS]

    frame = df[[c for c in {measure, date_column, *candidates} if c in df.columns]].copy()
    usable = frame.dropna(subset=[measure, date_column])
    dropped = int(len(df) - len(usable))
    if usable.empty:
        raise InvalidInputError(
            f"No rows have both a {date_column} and a {measure} value."
        )

    base_window, current_window, resolved_mode = _resolve_windows(
        usable[date_column], period,
        baseline_start, baseline_end, current_start, current_end,
    )
    base = usable[base_window.mask(usable[date_column])]
    current = usable[current_window.mask(usable[date_column])]
    if len(base) < MIN_ROWS_PER_PERIOD or len(current) < MIN_ROWS_PER_PERIOD:
        raise InvalidInputError(
            "Each period needs at least "
            f"{MIN_ROWS_PER_PERIOD} rows to compare; got {len(base):,} and {len(current):,}. "
            "Try a wider comparison window."
        )

    totals = _totals(base[measure], current[measure], agg)
    breakdowns = [
        b for b in (
            _breakdown(base, current, dim, measure, agg, totals, top_n) for dim in candidates
        )
        if b is not None
    ]
    breakdowns.sort(key=lambda b: b["score"], reverse=True)
    # The best-scoring dimension leads unless the reader asked to look through another one.
    best = next((b for b in breakdowns if b["column"] == focus), None) or (
        breakdowns[0] if breakdowns else None
    )

    result: dict[str, Any] = {
        "measure": measure,
        "aggregation": agg,
        "date_column": date_column,
        "period": {
            "mode": resolved_mode,
            "baseline": base_window.to_dict(),
            "current": current_window.to_dict(),
            "baseline_rows": int(len(base)),
            "current_rows": int(len(current)),
        },
        "total": totals,
        "shift_share": best["shift_share"] if best else None,
        "dimensions": breakdowns,
        "best_dimension": best["column"] if best else None,
        "options": options,
    }
    result["headline"] = _headline(result)
    result["narrative"] = _narrative(result)
    result["caveats"] = _caveats(result, dropped=dropped, total_rows=int(len(df)))
    result["follow_up"] = _follow_up(result)
    result["charts"] = _charts(result)
    result["tables"] = _tables(result)
    return result


# ----------------------------------------------------------------------------- periods


def resolve_windows(
    dates: pd.Series,
    mode: str = "auto",
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    current_start: str | None = None,
    current_end: str | None = None,
) -> tuple[Window, Window, str]:
    """Two equal-length comparison windows over `dates`.

    Public because period selection is not specific to driver analysis: significance
    testing and scenario planning compare the same two windows, and they must agree with
    the drill-down about what "last 30 days" means.
    """
    if mode not in PERIOD_MODES:
        raise InvalidInputError(f"Unknown comparison period '{mode}'.")
    return _resolve_windows(dates, mode, baseline_start, baseline_end, current_start, current_end)


def _resolve_windows(
    dates: pd.Series,
    mode: str,
    baseline_start: str | None,
    baseline_end: str | None,
    current_start: str | None,
    current_end: str | None,
) -> tuple[Window, Window, str]:
    explicit = (baseline_start, baseline_end, current_start, current_end)
    if any(explicit):
        if not all(explicit):
            raise InvalidInputError(
                "A custom comparison needs all four dates: baseline_start, baseline_end, "
                "current_start and current_end."
            )
        base = Window(_timestamp(baseline_start), _timestamp(baseline_end), "baseline period")
        current = Window(_timestamp(current_start), _timestamp(current_end), "selected period")
        if base.start > base.end or current.start > current.end:
            raise InvalidInputError("Each period must start before it ends.")
        return base, current, "custom"

    start, end = dates.min(), dates.max()
    span = int((end - start).days)
    if mode in ("auto", "custom"):
        mode = "yoy" if span >= 400 else "month" if span >= 120 else "week" if span >= 28 else "halves"

    lengths = {"yoy": (365, "12 months"), "month": (30, "30 days"), "week": (28, "4 weeks")}
    if mode in lengths:
        days, label = lengths[mode]
        offset = pd.Timedelta(days=days)
        if end - 2 * offset >= start - pd.Timedelta(days=1):
            # Trailing windows anchored at the last data point: no partial-calendar bias,
            # and the two sides are exactly the same length.
            return (
                Window(end - 2 * offset, end - offset, f"previous {label}", closed_left=False),
                Window(end - offset, end, f"last {label}", closed_left=False),
                mode,
            )
        mode = "halves"  # not enough history for the requested window

    midpoint = start + (end - start) / 2
    return (
        Window(start, midpoint, "first half"),
        Window(midpoint, end, "second half", closed_left=False),
        "halves",
    )


def _timestamp(value: str | None) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise InvalidInputError(f"'{value}' is not a valid date.") from exc
    if stamp is pd.NaT or pd.isna(stamp):
        raise InvalidInputError(f"'{value}' is not a valid date.")
    return stamp.tz_localize(None) if stamp.tzinfo else stamp


# ----------------------------------------------------------------------------- maths


def _totals(base: pd.Series, current: pd.Series, agg: Aggregation) -> dict[str, Any]:
    baseline_value = float(base.sum() if agg == "sum" else base.mean())
    current_value = float(current.sum() if agg == "sum" else current.mean())
    change = current_value - baseline_value
    return {
        "baseline": baseline_value,
        "current": current_value,
        "change": change,
        "change_pct": (change / abs(baseline_value)) if baseline_value else None,
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
    }


def _breakdown(
    base: pd.DataFrame,
    current: pd.DataFrame,
    dim: str,
    measure: str,
    agg: Aggregation,
    totals: dict[str, Any],
    top_n: int,
) -> dict[str, Any] | None:
    """Per-category contributions plus the volume / mix / rate split for one dimension."""
    labels = pd.concat([base[dim], current[dim]]).dropna()
    if labels.empty:
        return None
    cardinality = int(labels.astype(str).nunique())
    if cardinality < 2 or cardinality > MAX_CARDINALITY:
        return None

    base_g = _group(base, dim, measure)
    current_g = _group(current, dim, measure)
    keys = list(dict.fromkeys(list(current_g.index) + list(base_g.index)))

    n_base = float(base_g["n"].sum())
    n_current = float(current_g["n"].sum())
    if not n_base or not n_current:
        return None
    baseline_total, current_total = totals["baseline"], totals["change"] + totals["baseline"]
    delta_total = totals["change"]

    rows: list[dict[str, Any]] = []
    mix = rate = 0.0
    for key in keys:
        b_n = float(base_g["n"].get(key, 0.0))
        c_n = float(current_g["n"].get(key, 0.0))
        b_sum = float(base_g["sum"].get(key, 0.0))
        c_sum = float(current_g["sum"].get(key, 0.0))
        b_avg = b_sum / b_n if b_n else 0.0
        c_avg = c_sum / c_n if c_n else 0.0
        b_share_rows, c_share_rows = b_n / n_base, c_n / n_current

        if agg == "sum":
            b_value, c_value = b_sum, c_sum
            mix += n_current * (c_share_rows - b_share_rows) * b_avg
            rate += n_current * c_share_rows * (c_avg - b_avg)
        else:
            b_value, c_value = b_share_rows * b_avg, c_share_rows * c_avg
            mix += (c_share_rows - b_share_rows) * b_avg
            rate += c_share_rows * (c_avg - b_avg)

        contribution = c_value - b_value
        share_baseline = b_value / baseline_total if baseline_total else None
        share_current = c_value / current_total if current_total else None
        # What this category "should" have contributed if everything moved in proportion.
        expected = delta_total * share_baseline if share_baseline is not None else None
        rows.append({
            "label": _label(key),
            "baseline": b_value,
            "current": c_value,
            "change": contribution,
            "change_pct": (contribution / abs(b_value)) if b_value else None,
            "contribution_pct": (contribution / delta_total) if delta_total else None,
            "surprise": (contribution - expected) if expected is not None else None,
            "share_baseline": share_baseline,
            "share_current": share_current,
            "share_change": (
                None if share_baseline is None or share_current is None
                else share_current - share_baseline
            ),
            "baseline_rows": int(b_n),
            "current_rows": int(c_n),
            "status": _status(b_n, c_n, contribution),
        })

    volume = (n_current - n_base) * (baseline_total / n_base) if agg == "sum" else 0.0
    absolute = sum(abs(r["change"]) for r in rows) or 1.0
    surprise_total = sum(abs(r["surprise"] or 0.0) for r in rows)
    # Scale-free and comparable across dimensions of the same measure: how far the change
    # is from "every category moved in proportion to its size".
    score = surprise_total / abs(baseline_total) if baseline_total else 0.0

    ranked = sorted(rows, key=lambda r: abs(r["change"]), reverse=True)
    return {
        "column": dim,
        "categories": cardinality,
        "score": round(score, 6),
        "top_share": round(sum(abs(r["change"]) for r in ranked[:3]) / absolute, 4),
        "contributors": _bucket(ranked, top_n),
        "gained": [r for r in ranked if r["status"] == "new"][:3],
        "lost": [r for r in ranked if r["status"] == "lost"][:3],
        "shift_share": shift_share(volume, mix, rate, delta_total, dim, agg),
    }


def _group(frame: pd.DataFrame, dim: str, measure: str) -> pd.DataFrame:
    grouped = frame.groupby(frame[dim].astype("object").where(frame[dim].notna(), "(missing)"),
                            observed=True, dropna=False)[measure]
    return pd.DataFrame({"sum": grouped.sum(), "n": grouped.count()})


def shift_share(
    volume: float, mix: float, rate: float, delta: float, dim: str, agg: Aggregation
) -> dict[str, Any]:
    """The three terms add back to the total change exactly — that is the point.

    Public because the scenario simulator decomposes a *projected* change with the same
    identity; a what-if that did not reconcile the way the drill-down does would be
    telling the reader two different stories about the same arithmetic.
    """
    terms = [
        {"key": "volume", "label": "Volume", "value": volume,
         "detail": "more or fewer records in the period"},
        {"key": "mix", "label": "Mix", "value": mix,
         "detail": f"share moving between {dim} values"},
        {"key": "rate", "label": "Rate", "value": rate,
         "detail": "the average value per record"},
    ]
    if agg == "mean":
        terms = [t for t in terms if t["key"] != "volume"]
    for term in terms:
        term["share"] = (term["value"] / delta) if delta else None
    residual = delta - sum(t["value"] for t in terms)
    return {
        "dimension": dim,
        "terms": terms,
        "total": delta,
        "residual": residual,
        "closes": abs(residual) <= max(1e-6, abs(delta) * 1e-6),
        "largest": max(terms, key=lambda t: abs(t["value"]))["key"] if terms else None,
    }


def _bucket(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Keep the movers that matter; fold the tail into one honest "Other" row."""
    keep = max(2, min(top_n, MAX_CATEGORIES))
    if len(rows) <= keep + 1:
        return rows
    head, tail = rows[:keep], rows[keep:]
    return head + [{
        "label": f"Other ({len(tail)} values)",
        "baseline": sum(r["baseline"] for r in tail),
        "current": sum(r["current"] for r in tail),
        "change": sum(r["change"] for r in tail),
        "change_pct": None,
        "contribution_pct": None,
        "surprise": None,
        "share_baseline": None,
        "share_current": None,
        "share_change": None,
        "baseline_rows": sum(r["baseline_rows"] for r in tail),
        "current_rows": sum(r["current_rows"] for r in tail),
        "status": "other",
    }]


def _status(b_n: float, c_n: float, change: float) -> str:
    if b_n == 0 and c_n > 0:
        return "new"
    if c_n == 0 and b_n > 0:
        return "lost"
    if change > 0:
        return "grew"
    if change < 0:
        return "shrank"
    return "flat"


def _label(key: Any) -> str:
    if isinstance(key, bool):
        return "Yes" if key else "No"
    text = str(key)
    return text if len(text) <= 60 else f"{text[:57]}…"


# ----------------------------------------------------------------------------- narrative


# Shared with the significance and scenario briefings, which print the same figures.
_compact = compact
_pct = percent


def _headline(result: dict[str, Any]) -> str:
    total, period = result["total"], result["period"]
    measure = result["measure"]
    movement = {"up": "rose", "down": "fell", "flat": "was flat"}[total["direction"]]
    amount = (
        f" {_pct(abs(total['change_pct']))} ({_compact(total['change'])})"
        if total["change_pct"] is not None and total["direction"] != "flat"
        else f" by {_compact(total['change'])}" if total["direction"] != "flat" else ""
    )
    return (
        f"{measure} {movement}{amount} in the {period['current']['label']} "
        f"versus the {period['baseline']['label']}."
    )


def _narrative(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    total = result["total"]
    measure = result["measure"]
    best = next((d for d in result["dimensions"] if d["column"] == result["best_dimension"]), None)
    if best is None:
        lines.append("No dimension in this dataset separates the change any further.")
        return lines

    movers = [c for c in best["contributors"] if c["status"] != "other"]
    top = movers[0] if movers else None
    if top and total["change"]:
        lines.append(
            f"{best['column']} explains the most: **{top['label']}** contributed "
            f"{_compact(top['change'])} ({_pct(top['contribution_pct'])} of the move) and went from "
            f"{_pct(top['share_baseline'])} to {_pct(top['share_current'])} of {measure}."
        )
        opposite = next((c for c in movers[1:] if (c["change"] > 0) != (top["change"] > 0)), None)
        if opposite:
            lines.append(
                f"Pulling the other way, **{opposite['label']}** moved {_compact(opposite['change'])}, "
                f"partly offsetting the {'decline' if total['change'] < 0 else 'gain'}."
            )

    shift = best["shift_share"]
    parts = [
        f"{_compact(t['value'])} from {t['label'].lower()} ({t['detail']})" for t in shift["terms"]
    ]
    if parts:
        lines.append(
            f"Split three ways, the {_compact(total['change'])} move is " + ", ".join(parts[:-1])
            + (" and " if len(parts) > 1 else "") + parts[-1] + "."
        )

    if best["gained"]:
        names = ", ".join(f"**{c['label']}**" for c in best["gained"])
        lines.append(f"New in this period: {names} — absent from the baseline entirely.")
    if best["lost"]:
        names = ", ".join(f"**{c['label']}**" for c in best["lost"])
        lines.append(f"Gone from this period: {names}, which the baseline still contained.")

    runner = next(
        (d for d in result["dimensions"] if d["column"] != best["column"] and d["score"] > 0), None
    )
    if runner:
        lines.append(
            f"{runner['column']} is the next most differentiated dimension "
            f"(its top three values account for {_pct(runner['top_share'])} of the movement)."
        )
    return lines


def _caveats(result: dict[str, Any], *, dropped: int, total_rows: int) -> list[str]:
    caveats: list[str] = []
    period = result["period"]
    if dropped:
        caveats.append(
            f"{dropped:,} of {total_rows:,} rows ({dropped / max(total_rows, 1):.1%}) have no "
            f"{result['date_column']} or {result['measure']} value and are excluded."
        )
    if period["mode"] == "halves":
        caveats.append(
            "The history is too short for a like-for-like window, so the period is split in half; "
            "the two halves may not be seasonally comparable."
        )
    else:
        caveats.append(
            "Both periods are equal-length trailing windows anchored at the most recent record, "
            "so a partially-loaded final period will understate the current figure."
        )
    shift = result.get("shift_share")
    if shift and not shift["closes"]:
        caveats.append("The volume/mix/rate split does not reconcile exactly — treat it as indicative.")
    if result["aggregation"] == "mean":
        caveats.append(
            f"{result['measure']} is averaged, not summed, so 'volume' does not apply and the "
            "change reflects mix and per-record rate only."
        )
    return caveats


def _follow_up(result: dict[str, Any]) -> str:
    total, measure = result["total"], result["measure"]
    best = result["best_dimension"]
    direction = "increase" if total["change"] > 0 else "decline"
    if not best:
        return f"What is driving the {direction} in {measure}?"
    contributors = next(d for d in result["dimensions"] if d["column"] == best)["contributors"]
    movers = [c["label"] for c in contributors if c["status"] != "other"][:2]
    named = " and ".join(movers) if movers else best
    return (
        f"Why did {named} drive the {direction} in {measure} between the "
        f"{result['period']['baseline']['label']} and the {result['period']['current']['label']}?"
    )


# ----------------------------------------------------------------------------- artifacts


def _charts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Plotly figures in the same shape the sandbox emits, so the UI reuses one renderer."""
    charts: list[dict[str, Any]] = []
    best = next((d for d in result["dimensions"] if d["column"] == result["best_dimension"]), None)
    total, measure = result["total"], result["measure"]
    period = result["period"]

    if best:
        contributors = best["contributors"]
        labels = [period["baseline"]["label"].title()] + [c["label"] for c in contributors] + [
            period["current"]["label"].title()
        ]
        values = [total["baseline"]] + [c["change"] for c in contributors] + [0.0]
        measures = ["absolute"] + ["relative"] * len(contributors) + ["total"]
        charts.append(_chart(
            f"What moved {measure}, by {best['column']}",
            f"Each step is one {best['column']} value's contribution to the "
            f"{_compact(total['change'])} change.",
            labels, values, measures, measure,
        ))

    shift = result.get("shift_share")
    if shift:
        labels = [period["baseline"]["label"].title()] + [t["label"] for t in shift["terms"]] + [
            period["current"]["label"].title()
        ]
        values = [total["baseline"]] + [t["value"] for t in shift["terms"]] + [0.0]
        measures = ["absolute"] + ["relative"] * len(shift["terms"]) + ["total"]
        charts.append(_chart(
            f"Volume, mix and rate — {measure}",
            f"Shift-share across {shift['dimension']}; the three terms add back to the "
            f"{_compact(total['change'])} change exactly.",
            labels, values, measures, measure,
        ))
    return charts


def _chart(title: str, caption: str, labels: list[str], values: list[float],
           measures: list[str], axis: str) -> dict[str, Any]:
    """Interactive waterfall plus the numeric digest the PDF/PPTX exporters rebuild from.

    A static exporter cannot draw a waterfall, so the digest carries the contributions on
    their own — the honest bar-chart reduction of the same numbers.
    """
    steps = [(label, value) for label, value, kind in zip(labels, values, measures)
             if kind == "relative"]
    return {
        "title": title,
        "caption": caption,
        "figure": _waterfall(labels, values, measures, axis),
        "digest": {
            "traces": [{
                "type": "bar",
                "name": f"Contribution to {axis}",
                "x": [label for label, _ in steps],
                "x_count": len(steps),
                "y": [round(float(value), 6) for _, value in steps],
                "y_count": len(steps),
            }],
            "axes": {"yaxis": axis},
        },
    }


def _waterfall(labels: list[str], values: list[float], measures: list[str], title: str) -> dict[str, Any]:
    return {
        "data": [{
            "type": "waterfall",
            "orientation": "v",
            "measure": measures,
            "x": labels,
            "y": [round(float(v), 6) for v in values],
            "text": [_compact(float(v)) if m != "total" else "" for v, m in zip(values, measures)],
            "textposition": "outside",
            "cliponaxis": False,
            "connector": {"line": {"color": CONNECTOR, "width": 1}},
            "increasing": {"marker": {"color": INCREASE}},
            "decreasing": {"marker": {"color": DECREASE}},
            "totals": {"marker": {"color": TOTAL}},
            "hovertemplate": "%{x}<br>%{y:,.2f}<extra></extra>",
        }],
        "layout": {
            "showlegend": False,
            "yaxis": {"title": {"text": title}, "zeroline": True},
            "xaxis": {"automargin": True, "tickangle": -25},
            "margin": {"l": 64, "r": 16, "t": 24, "b": 80},
        },
    }


def _ordered(result: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Score order, but whatever the reader is looking through comes first."""
    rest = [d for d in result["dimensions"] if d["column"] != result["best_dimension"]]
    lead = [d for d in result["dimensions"] if d["column"] == result["best_dimension"]]
    return (lead + rest)[:limit]


def _tables(result: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for breakdown in _ordered(result):
        rows = [
            [
                c["label"],
                round(c["baseline"], 4),
                round(c["current"], 4),
                round(c["change"], 4),
                None if c["contribution_pct"] is None else round(c["contribution_pct"], 4),
                None if c["surprise"] is None else round(c["surprise"], 4),
                None if c["share_change"] is None else round(c["share_change"], 4),
            ]
            for c in breakdown["contributors"]
        ]
        tables.append({
            "title": f"{result['measure']} by {breakdown['column']}",
            "columns": [
                {"name": breakdown["column"], "kind": "text"},
                {"name": "Baseline", "kind": "number"},
                {"name": "Current", "kind": "number"},
                {"name": "Change", "kind": "number"},
                {"name": "Share of move", "kind": "number"},
                {"name": "Surprise", "kind": "number"},
                {"name": "Share shift", "kind": "number"},
            ],
            "rows": rows,
            "total_rows": len(rows),
            "truncated": len(rows) < breakdown["categories"],
        })
    return tables


# ----------------------------------------------------------------------------- markdown


def explain_markdown(result: dict[str, Any], dataset_name: str = "") -> str:
    period = result["period"]
    total = result["total"]
    lines = [
        f"# Why {result['measure']} moved",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}"
        f"{period['current']['label']} ({period['current']['start'][:10]} → "
        f"{period['current']['end'][:10]}) vs {period['baseline']['label']} "
        f"({period['baseline']['start'][:10]} → {period['baseline']['end'][:10]})_",
        "",
        f"**{result['headline']}**",
        "",
        f"| | {period['baseline']['label'].title()} | {period['current']['label'].title()} | Change |",
        "|---|---:|---:|---:|",
        f"| {result['measure']} | {_compact(total['baseline'])} | {_compact(total['current'])} | "
        f"{_compact(total['change'])} ({_pct(total['change_pct'])}) |",
        "",
    ]
    for line in result["narrative"]:
        lines.append(f"- {line}")
    lines.append("")

    for breakdown in _ordered(result):
        lines += [
            f"## {result['measure']} by {breakdown['column']}",
            "",
            "| Value | Baseline | Current | Change | Share of move | Share shift |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for c in breakdown["contributors"]:
            lines.append(
                f"| {c['label']} | {_compact(c['baseline'])} | {_compact(c['current'])} | "
                f"{_compact(c['change'])} | {_pct(c['contribution_pct'])} | "
                f"{_pct(c['share_change'])} |"
            )
        lines.append("")

    if result["caveats"]:
        lines += ["## Caveats", ""] + [f"- {c}" for c in result["caveats"]] + [""]
    lines += [
        "---",
        "",
        "_Computed deterministically from the cleaned table — no model call, no estimated figures._",
    ]
    return "\n".join(lines)
