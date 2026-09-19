"""Cohort and retention analysis: does what you win actually stay won?

Drivers explain a change, significance says whether a gap is real and scenarios ask
what would have to be true. All three look at a period. This module looks at a
*population over time*: group every entity by the period it first appeared, then
follow each group forward and read off how much of it is still there.

Everything here is plain pandas over the cleaned table — no model call, no cost, and
the same answer twice. Three things make the numbers honest rather than flattering:

* **Right-censoring is respected.** A cohort born last month cannot have a
  six-month retention rate. Those cells are left empty rather than counted as zero,
  and the average curve at each offset is computed only over the cohorts that have
  actually lived that long.
* **The partial final period is dropped.** A month that is three days old always
  looks like a collapse, so it is excluded and disclosed.
* **Nothing is extrapolated.** The value curve is cumulative *observed* value per
  entity. It is not an LTV projection, and it does not pretend to be one.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import pandas as pd

from app.core.errors import InvalidInputError
from app.core.formatting import compact, percent
from app.services.profiling import NON_ADDITIVE, rank_measures

logger = logging.getLogger(__name__)

Granularity = Literal["auto", "day", "week", "month", "quarter"]
GRANULARITIES = ("auto", "day", "week", "month", "quarter")
# pandas period aliases, plus how a period is written to a reader.
FREQ = {"day": "D", "week": "W-MON", "month": "M", "quarter": "Q"}
PERIOD_NOUN = {"day": "day", "week": "week", "month": "month", "quarter": "quarter"}

# An entity column needs repeats to have retention at all, and enough distinct values
# that a rate means something.
MIN_ENTITIES = 12
MAX_ENTITY_RATIO = 0.98
# Beyond this a heat map is unreadable; older cohorts are folded into one honest row.
MAX_COHORT_ROWS = 14
MAX_OFFSETS = 24
DEFAULT_PERIODS = 12
# Below this, a cohort's retention rate is one or two entities and moves 50% at a time.
MIN_COHORT_SIZE = 3

# What a retention question is usually about. A column naming a subject *and* carrying a
# key ("Customer ID") beats one that merely names a subject ("Customer Email"), which in
# turn beats a column that just happens to repeat ("Product") — an id is the stable key,
# and a contact detail is the thing most likely to change under the same person.
ENTITY_SUBJECT = re.compile(
    r"(customer|client|user|account|member|subscriber|device|patient|visitor|shopper|"
    r"buyer|guest|household|company|organisation|organization|person|email|player|donor)",
    re.I,
)
ENTITY_KEY = re.compile(r"(^|[_\s])(id|ids|key|uuid|guid|no|num|number|ref|code)([_\s]|$)", re.I)

HEAT_LOW = "#f4f3ee"
HEAT_HIGH = "#2a78d6"
CURVE = "#2a78d6"
COHORT_LINE = "#c3c2b7"
VALUE = "#1baf7a"


# ----------------------------------------------------------------------------- options


def cohort_options(profile: dict[str, Any]) -> dict[str, Any]:
    """Which entity, date and value columns a cohort grid can be built from.

    An "entity" is whatever repeats: a customer, an account, a device, a store. The
    profile already knows each column's cardinality, so the candidates are the columns
    with enough distinct values to be a population and enough repeats to have a history.
    """
    columns = {c["name"]: c for c in profile.get("columns") or []}
    roles = profile.get("roles") or {}
    n_rows = int(profile.get("n_rows") or 0)

    entities: list[dict[str, Any]] = []
    for name, column in columns.items():
        if column["role"] not in ("identifier", "dimension", "text"):
            continue
        unique = int(column.get("unique") or 0)
        if unique < MIN_ENTITIES or not n_rows:
            continue
        if unique > n_rows * MAX_ENTITY_RATIO:
            continue  # one row per value: nothing ever comes back
        entities.append({
            "name": name,
            "unique": unique,
            # Rows per entity: the higher it is, the more history there is to follow.
            "rows_per_entity": round(n_rows / unique, 2),
            "role": column["role"],
            "rank": entity_rank(name),
        })
    # What it is called first, then how much history it has: "Customer ID" is a retention
    # question and "Product" is not, however often a product repeats.
    entities.sort(key=lambda e: (e["rank"], -e["rows_per_entity"], e["name"]))

    dates = list(roles.get("datetime") or [])
    measures = [m for m in rank_measures(list(roles.get("measure") or []))
                if not NON_ADDITIVE.search(m)]
    span_days = int(((profile.get("date_range") or {}).get("span_days") or 0))
    if not span_days and dates:
        stats = columns.get(dates[0], {}).get("stats") or {}
        span_days = int(stats.get("span_days") or 0)

    return {
        "entities": entities,
        "date_columns": dates,
        "measures": measures,
        "granularities": [g for g in GRANULARITIES if g != "auto"],
        "defaults": {
            "entity": entities[0]["name"] if entities else None,
            "date_column": dates[0] if dates else None,
            "measure": measures[0] if measures else None,
            "granularity": suggest_granularity(span_days),
            "periods": DEFAULT_PERIODS,
        },
        "available": bool(entities and dates),
        "reason": _unavailable_reason(entities, dates),
    }


def entity_rank(name: str) -> int:
    """0 = an identified subject, 1 = a subject, 2 = a bare key, 3 = anything else."""
    subject = bool(ENTITY_SUBJECT.search(name))
    key = bool(ENTITY_KEY.search(name))
    if subject and key:
        return 0
    if subject:
        return 1
    return 2 if key else 3


def _unavailable_reason(entities: list[dict[str, Any]], dates: list[str]) -> str | None:
    if entities and dates:
        return None
    if not dates:
        return "Cohorts need a date column to group by, and this dataset has none."
    return (
        "Cohorts need a column that repeats — a customer, account or device id. Every "
        "candidate column in this dataset is either unique per row or has too few values."
    )


def suggest_granularity(span_days: int) -> str:
    """The grain that gives enough periods to see a curve without one row per day."""
    if span_days >= 730:
        return "quarter" if span_days >= 2200 else "month"
    if span_days >= 180:
        return "month"
    if span_days >= 42:
        return "week"
    return "day"


# ----------------------------------------------------------------------------- entry point


def analyze(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    entity: str | None = None,
    date_column: str | None = None,
    measure: str | None = None,
    granularity: Granularity = "auto",
    periods: int = DEFAULT_PERIODS,
    min_cohort_size: int = MIN_COHORT_SIZE,
) -> dict[str, Any]:
    """Build the cohort grid and everything read off it. Pure pandas."""
    options = cohort_options(profile)
    entity = entity or options["defaults"]["entity"]
    date_column = date_column or options["defaults"]["date_column"]

    if not entity:
        raise InvalidInputError(
            options["reason"] or "This dataset has no repeating column to build cohorts from."
        )
    if entity not in df.columns:
        raise InvalidInputError(f"Column '{entity}' is not in this dataset.")
    if not date_column:
        raise InvalidInputError("Cohorts need a date column; this dataset has none.")
    if date_column not in df.columns:
        raise InvalidInputError(f"Column '{date_column}' is not in this dataset.")
    if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
        raise InvalidInputError(f"'{date_column}' is not a date column.")
    if measure is not None and measure not in df.columns:
        raise InvalidInputError(f"Column '{measure}' is not in this dataset.")
    if measure is not None and not pd.api.types.is_numeric_dtype(df[measure]):
        raise InvalidInputError(f"'{measure}' is not numeric, so it cannot be totalled per cohort.")
    if granularity not in GRANULARITIES:
        raise InvalidInputError(f"Unknown granularity '{granularity}'.")

    wanted = [c for c in (entity, date_column, measure) if c]
    frame = df[list(dict.fromkeys(wanted))].dropna(subset=[entity, date_column]).copy()
    dropped = int(len(df) - len(frame))
    if frame.empty:
        raise InvalidInputError(f"No rows have both a {entity} and a {date_column} value.")

    span_days = int((frame[date_column].max() - frame[date_column].min()).days)
    grain = suggest_granularity(span_days) if granularity == "auto" else granularity
    freq = FREQ[grain]

    index = pd.PeriodIndex(frame[date_column], freq=freq)
    frame["_period"] = index.asi8  # consecutive integers at every supported grain
    period_of = dict(zip(index.asi8, index))

    # The final period is nearly always partial, and a partial period is not a churn
    # event. Drop it unless the data actually runs to its end.
    last = index.max()
    complete = bool(frame[date_column].max() >= last.end_time.normalize())
    if not complete:
        frame = frame[frame["_period"] < last.ordinal]
        if frame.empty:
            raise InvalidInputError(
                f"Only one partial {PERIOD_NOUN[grain]} of data is present — too little for cohorts."
            )

    first_seen = frame.groupby(entity, observed=True)["_period"].transform("min")
    frame["_cohort"] = first_seen
    frame["_offset"] = frame["_period"] - frame["_cohort"]

    last_period = int(frame["_period"].max())
    horizon = int(max(1, min(int(periods), MAX_OFFSETS)))
    offsets = list(range(0, horizon + 1))

    grid = _grid(frame, entity, measure, offsets, last_period, min_cohort_size)
    if not grid["cohorts"]:
        raise InvalidInputError(
            f"No cohort has at least {max(int(min_cohort_size), 1)} {entity} values. "
            "Try a coarser grain."
        )

    curve = _curve(grid["cohorts"], offsets)
    summary = _summary(frame, entity, measure, grid["cohorts"], curve, grain)

    result: dict[str, Any] = {
        "entity": entity,
        "date_column": date_column,
        "measure": measure,
        "granularity": grain,
        "period_noun": PERIOD_NOUN[grain],
        "horizon": horizon,
        "cohorts": [_render_cohort(c, period_of) for c in grid["cohorts"]],
        "folded": grid["folded"],
        "offsets": offsets,
        "curve": curve,
        "summary": summary,
        "coverage": {
            "rows": int(len(frame)),
            "rows_dropped": dropped,
            "entities": summary["entities"],
            "periods": int(last_period - int(frame["_cohort"].min()) + 1),
            "first_period": period_of[int(frame["_period"].min())].start_time.isoformat(),
            "last_period": period_of[last_period].start_time.isoformat(),
            "final_period_complete": complete,
            "min_cohort_size": max(int(min_cohort_size), 1),
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


# ----------------------------------------------------------------------------- the grid


def _grid(
    frame: pd.DataFrame,
    entity: str,
    measure: str | None,
    offsets: list[int],
    last_period: int,
    min_cohort_size: int,
) -> dict[str, Any]:
    """One row per cohort: size, active count and value at each offset.

    A cell is `None` — not zero — when the cohort has not lived that long yet. That
    distinction is the whole difference between a retention curve and a triangle of
    decay that is really just the calendar running out.
    """
    minimum = max(int(min_cohort_size), 1)
    # One row per (entity, period): a customer who ordered four times in March was
    # active in March once, not four times.
    visits = frame.drop_duplicates(subset=[entity, "_period"])
    sizes = visits[visits["_offset"] == 0].groupby("_cohort", observed=True)[entity].nunique()
    active = (
        visits.groupby(["_cohort", "_offset"], observed=True)[entity].nunique().unstack(fill_value=0)
    )
    values = None
    if measure:
        values = frame.groupby(["_cohort", "_offset"], observed=True)[measure].sum().unstack(fill_value=0.0)

    rows: list[dict[str, Any]] = []
    for cohort in sorted(sizes.index):
        size = int(sizes.loc[cohort])
        if size < minimum:
            continue
        observed = int(last_period - int(cohort))  # the largest offset this cohort can have
        counts: list[int | None] = []
        retention: list[float | None] = []
        value: list[float | None] = []
        per_entity: list[float | None] = []
        revenue_retention: list[float | None] = []
        first_value = (
            float(values.loc[cohort].get(0, 0.0))
            if values is not None and cohort in values.index else 0.0
        )
        for offset in offsets:
            if offset > observed:
                counts.append(None)
                retention.append(None)
                value.append(None)
                per_entity.append(None)
                revenue_retention.append(None)
                continue
            n = int(active.loc[cohort].get(offset, 0)) if cohort in active.index else 0
            counts.append(n)
            retention.append(n / size if size else None)
            if values is not None:
                total = float(values.loc[cohort].get(offset, 0.0)) if cohort in values.index else 0.0
                value.append(total)
                per_entity.append(total / size if size else None)
                revenue_retention.append(total / first_value if first_value else None)
            else:
                value.append(None)
                per_entity.append(None)
                revenue_retention.append(None)
        rows.append({
            "cohort": int(cohort),
            "size": size,
            "observed_offsets": observed,
            "active": counts,
            "retention": retention,
            "value": value,
            "value_per_entity": per_entity,
            "revenue_retention": revenue_retention,
        })

    folded = 0
    if len(rows) > MAX_COHORT_ROWS:
        # Keep the newest cohorts, which are the ones a reader can still act on.
        folded = len(rows) - MAX_COHORT_ROWS
        rows = rows[folded:]
    return {"cohorts": rows, "folded": folded}


def _curve(cohorts: list[dict[str, Any]], offsets: list[int]) -> dict[str, Any]:
    """The pooled curve: at each offset, only the cohorts old enough to be counted.

    Weighted by cohort size, so a 900-entity cohort is not outvoted by a 4-entity one.
    """
    retention: list[float | None] = []
    per_entity: list[float | None] = []
    cumulative: list[float | None] = []
    observed_cohorts: list[int] = []
    observed_entities: list[int] = []
    running = 0.0
    running_valid = True

    for offset in offsets:
        eligible = [c for c in cohorts if c["retention"][offset] is not None]
        observed_cohorts.append(len(eligible))
        population = sum(c["size"] for c in eligible)
        observed_entities.append(int(population))
        if not eligible or not population:
            retention.append(None)
            per_entity.append(None)
            cumulative.append(None)
            running_valid = False
            continue
        retention.append(sum(c["active"][offset] for c in eligible) / population)
        contributions = [c["value"][offset] for c in eligible if c["value"][offset] is not None]
        if len(contributions) == len(eligible):
            average = sum(contributions) / population
            per_entity.append(average)
            if running_valid:
                running += average
                cumulative.append(running)
            else:
                cumulative.append(None)
        else:
            per_entity.append(None)
            cumulative.append(None)
            running_valid = False
    return {
        "offsets": offsets,
        "retention": retention,
        "value_per_entity": per_entity,
        "cumulative_value_per_entity": cumulative,
        "cohorts_observed": observed_cohorts,
        "entities_observed": observed_entities,
    }


def _summary(
    frame: pd.DataFrame,
    entity: str,
    measure: str | None,
    cohorts: list[dict[str, Any]],
    curve: dict[str, Any],
    grain: str,
) -> dict[str, Any]:
    visits = frame.drop_duplicates(subset=[entity, "_period"])
    per_entity = visits.groupby(entity, observed=True)["_offset"]
    active_periods = per_entity.count()
    entities = int(len(active_periods))
    returners = active_periods[active_periods > 1]

    # Time to the second appearance, in periods — a lagging repeat is still a repeat,
    # but it is a different business from one that comes back immediately.
    ordered = visits.sort_values([entity, "_offset"])
    second = pd.Series(
        ordered.groupby(entity, observed=True)["_offset"].nth(1), dtype="float64"
    ).dropna()

    at = {offset: curve["retention"][offset] if offset < len(curve["retention"]) else None
          for offset in (1, 3, 6, 12)}
    ranked = [c for c in cohorts if c["size"] >= MIN_COHORT_SIZE]
    benchmark = _benchmark_offset(ranked)
    best = worst = None
    if benchmark is not None:
        comparable = [c for c in ranked if c["retention"][benchmark] is not None]
        if len(comparable) >= 2:
            best = max(comparable, key=lambda c: c["retention"][benchmark])
            worst = min(comparable, key=lambda c: c["retention"][benchmark])

    summary: dict[str, Any] = {
        "entities": entities,
        "cohorts": len(cohorts),
        "grain": grain,
        "repeat_rate": (len(returners) / entities) if entities else None,
        "one_and_done_pct": (1 - len(returners) / entities) if entities else None,
        "median_active_periods": float(active_periods.median()) if entities else None,
        "mean_active_periods": float(active_periods.mean()) if entities else None,
        "median_periods_to_return": float(second.median()) if len(second) else None,
        "retention_1": at[1],
        "retention_3": at[3],
        "retention_6": at[6],
        "retention_12": at[12],
        "benchmark_offset": benchmark,
        "best_cohort": {"cohort": best["cohort"], "retention": best["retention"][benchmark],
                        "size": best["size"]} if best and benchmark is not None else None,
        "worst_cohort": {"cohort": worst["cohort"], "retention": worst["retention"][benchmark],
                         "size": worst["size"]} if worst and benchmark is not None else None,
        "revenue_retention_1": None,
        "value_per_entity_total": None,
    }
    if measure:
        comparable = [
            c for c in cohorts
            if len(c["revenue_retention"]) > 1 and c["revenue_retention"][1] is not None
        ]
        if comparable:
            weights = sum(c["size"] for c in comparable)
            summary["revenue_retention_1"] = sum(
                c["revenue_retention"][1] * c["size"] for c in comparable) / weights
        totals = [v for v in curve["cumulative_value_per_entity"] if v is not None]
        summary["value_per_entity_total"] = totals[-1] if totals else None
    return summary


def _benchmark_offset(cohorts: list[dict[str, Any]]) -> int | None:
    """The largest offset at which at least half the cohorts can be compared fairly."""
    if not cohorts:
        return None
    width = len(cohorts[0]["retention"])
    needed = max(2, len(cohorts) // 2)
    for offset in range(min(width - 1, MAX_OFFSETS), 0, -1):
        if sum(1 for c in cohorts if c["retention"][offset] is not None) >= needed:
            return offset
    return 1 if width > 1 else None


def _render_cohort(cohort: dict[str, Any], period_of: dict[int, Any]) -> dict[str, Any]:
    period = period_of.get(cohort["cohort"])
    return {
        **cohort,
        "label": _period_label(period),
        "start": period.start_time.isoformat() if period is not None else None,
    }


def _period_label(period: Any) -> str:
    if period is None:
        return "—"
    code = str(getattr(period, "freqstr", "")).upper()
    if code.startswith("M"):
        return period.start_time.strftime("%b %Y")
    if code.startswith("Q"):
        return f"Q{period.quarter} {period.year}"
    if code.startswith("W"):
        return f"w/c {period.start_time:%d %b %Y}"
    return period.start_time.strftime("%d %b %Y")


# ----------------------------------------------------------------------------- narrative


_compact = compact
_pct = percent


def _headline(result: dict[str, Any]) -> str:
    summary, noun = result["summary"], result["period_noun"]
    entity = result["entity"]
    first = summary["retention_1"]
    if first is None:
        return (
            f"{summary['entities']:,} {entity} values across {summary['cohorts']} "
            f"{noun}ly cohorts; the history is too short to read a retention rate yet."
        )
    return (
        f"{_pct(first)} of {entity} values come back in the {noun} after they first appear, "
        f"and {_pct(summary['repeat_rate'])} come back at all."
    )


def _narrative(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    summary, noun, entity = result["summary"], result["period_noun"], result["entity"]
    curve = result["curve"]

    horizons = [(n, summary[f"retention_{n}"]) for n in (1, 3, 6, 12) if summary.get(f"retention_{n}") is not None]
    if len(horizons) >= 2:
        parts = [f"{_pct(value)} at {n} {noun}{'s' if n != 1 else ''}" for n, value in horizons]
        lines.append("The curve reads " + ", ".join(parts[:-1]) + f" and {parts[-1]}.")
    elif horizons:
        n, value = horizons[0]
        lines.append(f"{_pct(value)} are still active {n} {noun}{'s' if n != 1 else ''} on.")

    if summary["one_and_done_pct"] is not None:
        lines.append(
            f"**{_pct(summary['one_and_done_pct'])}** of {entity} values appear in exactly one "
            f"{noun} and never again; the rest are active for a median of "
            f"{summary['median_active_periods']:.0f} {noun}s."
        )
    if summary["median_periods_to_return"] is not None:
        gap = summary["median_periods_to_return"]
        lines.append(
            f"When they do return, the median gap is {gap:.0f} {noun}{'s' if gap != 1 else ''} — "
            f"anything measured over a shorter window will under-count repeats."
        )

    best, worst, offset = summary["best_cohort"], summary["worst_cohort"], summary["benchmark_offset"]
    if best and worst and offset is not None and best["cohort"] != worst["cohort"]:
        labels = {c["cohort"]: c["label"] for c in result["cohorts"]}
        lines.append(
            f"At {offset} {noun}{'s' if offset != 1 else ''}, the **{labels.get(best['cohort'], '?')}** "
            f"cohort retains {_pct(best['retention'])} against **{labels.get(worst['cohort'], '?')}** at "
            f"{_pct(worst['retention'])} — a {_pct(best['retention'] - worst['retention'])} spread "
            f"between groups of the same age."
        )

    if result["measure"] and summary["revenue_retention_1"] is not None:
        direction = "expands" if summary["revenue_retention_1"] >= 1 else "contracts"
        lines.append(
            f"{result['measure']} {direction} to {_pct(summary['revenue_retention_1'])} of the "
            f"first {noun}'s total by the second — spend per surviving {entity}, not just headcount."
        )
    if result["measure"] and summary["value_per_entity_total"] is not None:
        depth = sum(1 for v in curve["cumulative_value_per_entity"] if v is not None) - 1
        lines.append(
            f"Cumulative observed {result['measure']} reaches "
            f"{_compact(summary['value_per_entity_total'])} per {entity} by {noun} {depth}. "
            "That is what has actually been booked, not a projection."
        )
    return lines


def _caveats(result: dict[str, Any]) -> list[str]:
    coverage, summary, noun = result["coverage"], result["summary"], result["period_noun"]
    caveats = [
        f"Recent cohorts have not lived long enough to fill the later {noun}s. Those cells are "
        "left empty and excluded from the average curve rather than counted as churn.",
    ]
    if not coverage["final_period_complete"]:
        caveats.append(
            f"The final {noun} in the data was still in progress and has been dropped — a partial "
            f"{noun} always looks like a collapse."
        )
    if coverage["rows_dropped"]:
        caveats.append(
            f"{coverage['rows_dropped']:,} row(s) have no {result['entity']} or "
            f"{result['date_column']} value and are excluded."
        )
    if result["folded"]:
        caveats.append(
            f"The {result['folded']} oldest cohort(s) are not shown; the grid keeps the "
            f"{MAX_COHORT_ROWS} most recent so it stays readable."
        )
    caveats.append(
        f"Cohorts are built on '{result['entity']}' as-is. If the same real-world "
        f"{result['entity']} appears under two values, it will look like two."
    )
    if coverage["min_cohort_size"] > 1:
        caveats.append(
            f"Cohorts smaller than {coverage['min_cohort_size']} are excluded, because a rate over "
            "one or two values moves in 50-point steps."
        )
    if result["measure"]:
        caveats.append(
            f"The {result['measure']} curve is cumulative observed value per {result['entity']}, "
            "not a lifetime-value forecast — nothing here is extrapolated."
        )
    if summary["entities"] and summary["entities"] < 100:
        caveats.append(
            f"Only {summary['entities']:,} distinct {result['entity']} values: every rate here "
            "carries a wide margin of error."
        )
    return caveats


def _follow_up(result: dict[str, Any]) -> str:
    summary, noun, entity = result["summary"], result["period_noun"], result["entity"]
    if summary["worst_cohort"]:
        labels = {c["cohort"]: c["label"] for c in result["cohorts"]}
        label = labels.get(summary["worst_cohort"]["cohort"], "the weakest cohort")
        return (
            f"What was different about the {entity} values acquired in {label}, given they retain "
            f"worse than every other cohort of the same age?"
        )
    return f"Which segments retain best after the first {noun}, and what do they have in common?"


# ----------------------------------------------------------------------------- artifacts


def _charts(result: dict[str, Any]) -> list[dict[str, Any]]:
    charts = [_heatmap(result), _curve_chart(result)]
    if result["measure"]:
        value = _value_chart(result)
        if value:
            charts.append(value)
    return [c for c in charts if c]


def _heatmap(result: dict[str, Any]) -> dict[str, Any]:
    """Retention grid. Unobserved cells are `None`, which Plotly renders as a gap."""
    cohorts = result["cohorts"]
    offsets = result["offsets"]
    noun = result["period_noun"]
    # Newest cohort at the top, which is how a cohort table is read.
    rows = list(reversed(cohorts))
    z = [[None if v is None else round(v * 100, 2) for v in c["retention"]] for c in rows]
    text = [
        [
            "" if c["retention"][i] is None
            else f"{c['retention'][i] * 100:.0f}%"
            for i in range(len(offsets))
        ]
        for c in rows
    ]
    labels = [f"{c['label']} · {c['size']:,}" for c in rows]
    return {
        "title": f"Retention by cohort, {noun} by {noun}",
        "caption": (
            f"Each row is the {result['entity']} values that first appeared in that {noun}; each "
            f"column is how many {noun}s later. Blank cells are cohorts that have not lived that long."
        ),
        "figure": {
            "data": [{
                "type": "heatmap",
                "z": z,
                "x": [str(o) for o in offsets],
                "y": labels,
                "text": text,
                "texttemplate": "%{text}",
                "textfont": {"size": 10},
                "colorscale": [[0, HEAT_LOW], [1, HEAT_HIGH]],
                "zmin": 0,
                "zmax": 100,
                "hovertemplate": (
                    f"%{{y}}<br>{noun} %{{x}}<br>%{{z:.1f}}%% retained<extra></extra>"
                ),
                "colorbar": {"title": {"text": "% retained"}, "thickness": 12, "outlinewidth": 0},
                "xgap": 2,
                "ygap": 2,
            }],
            "layout": {
                "xaxis": {"title": {"text": f"{noun.title()}s since first seen"}, "side": "top",
                          "type": "category"},
                "yaxis": {"title": {"text": "Cohort"}, "automargin": True, "type": "category"},
                "margin": {"l": 120, "r": 16, "t": 48, "b": 24},
            },
        },
        # A static exporter cannot draw a heat map, so the digest carries the pooled curve —
        # the honest bar-chart reduction of the same grid.
        "digest": {
            "traces": [{
                "type": "bar",
                "name": "Retained",
                "x": [str(o) for o in offsets],
                "x_count": len(offsets),
                "y": [0.0 if v is None else round(v * 100, 2) for v in result["curve"]["retention"]],
                "y_count": len(offsets),
            }],
            "axes": {"yaxis": "% retained", "xaxis": f"{noun.title()}s since first seen"},
        },
    }


def _curve_chart(result: dict[str, Any]) -> dict[str, Any]:
    offsets = result["offsets"]
    noun = result["period_noun"]
    curve = result["curve"]
    traces: list[dict[str, Any]] = []
    # The three newest cohorts, faintly, so the reader can see the spread the average hides.
    for cohort in result["cohorts"][-3:]:
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "name": cohort["label"],
            "x": offsets,
            "y": [None if v is None else round(v * 100, 3) for v in cohort["retention"]],
            "line": {"color": COHORT_LINE, "width": 1},
            "hovertemplate": f"{cohort['label']}<br>{noun} %{{x}}: %{{y:.1f}}%%<extra></extra>",
            "showlegend": False,
        })
    traces.append({
        "type": "scatter",
        "mode": "lines+markers",
        "name": "All cohorts",
        "x": offsets,
        "y": [None if v is None else round(v * 100, 3) for v in curve["retention"]],
        "line": {"color": CURVE, "width": 2.5},
        "marker": {"size": 6},
        "hovertemplate": f"{noun} %{{x}}: %{{y:.1f}}%% retained<extra></extra>",
    })
    return {
        "title": "Retention curve",
        "caption": (
            "Size-weighted across every cohort old enough to be counted at each point, so the tail "
            "is not dragged down by cohorts that simply have not lived that long."
        ),
        "figure": {
            "data": traces,
            "layout": {
                "xaxis": {"title": {"text": f"{noun.title()}s since first seen"}, "dtick": 1},
                "yaxis": {"title": {"text": "% still active"}, "rangemode": "tozero", "ticksuffix": "%"},
                "legend": {"orientation": "h", "y": 1.12, "x": 0},
                "margin": {"l": 56, "r": 16, "t": 28, "b": 44},
            },
        },
        "digest": {
            "traces": [{
                "type": "line",
                "name": "% still active",
                "x": [str(o) for o in offsets],
                "x_count": len(offsets),
                "y": [0.0 if v is None else round(v * 100, 2) for v in curve["retention"]],
                "y_count": len(offsets),
            }],
            "axes": {"yaxis": "% still active", "xaxis": f"{noun.title()}s since first seen"},
        },
    }


def _value_chart(result: dict[str, Any]) -> dict[str, Any] | None:
    curve = result["curve"]
    cumulative = curve["cumulative_value_per_entity"]
    depth = [i for i, v in enumerate(cumulative) if v is not None]
    if len(depth) < 2:
        return None
    offsets = curve["offsets"][: depth[-1] + 1]
    values = [round(float(v), 4) for v in cumulative[: depth[-1] + 1]]
    noun = result["period_noun"]
    return {
        "title": f"Cumulative {result['measure']} per {result['entity']}",
        "caption": (
            f"What one {result['entity']} has actually contributed by each {noun} after it first "
            "appeared. Observed only — no extrapolation, so the line stops where the data does."
        ),
        "figure": {
            "data": [{
                "type": "scatter",
                "mode": "lines+markers",
                "name": f"Cumulative {result['measure']}",
                "x": offsets,
                "y": values,
                "line": {"color": VALUE, "width": 2.5},
                "marker": {"size": 6},
                "fill": "tozeroy",
                "fillcolor": "rgba(27,175,122,0.10)",
                "hovertemplate": f"{noun} %{{x}}: %{{y:,.2f}}<extra></extra>",
            }],
            "layout": {
                "showlegend": False,
                "xaxis": {"title": {"text": f"{noun.title()}s since first seen"}, "dtick": 1},
                "yaxis": {"title": {"text": f"{result['measure']} per {result['entity']}"},
                          "rangemode": "tozero"},
                "margin": {"l": 64, "r": 16, "t": 28, "b": 44},
            },
        },
        "digest": {
            "traces": [{
                "type": "line",
                "name": f"Cumulative {result['measure']}",
                "x": [str(o) for o in offsets],
                "x_count": len(offsets),
                "y": values,
                "y_count": len(values),
            }],
            "axes": {"yaxis": f"{result['measure']} per {result['entity']}"},
        },
    }


def _tables(result: dict[str, Any]) -> list[dict[str, Any]]:
    offsets = result["offsets"]
    noun = result["period_noun"]
    grid_rows = [
        [c["label"], c["size"]] + [
            None if value is None else round(value, 4) for value in c["retention"]
        ]
        for c in reversed(result["cohorts"])
    ]
    tables = [{
        "title": f"Retention by cohort ({noun}s since first seen)",
        "columns": (
            [{"name": "Cohort", "kind": "text"}, {"name": f"{result['entity']}s", "kind": "number"}]
            + [{"name": str(o), "kind": "number"} for o in offsets]
        ),
        "rows": grid_rows,
        "total_rows": len(grid_rows),
        "truncated": bool(result["folded"]),
    }]

    curve = result["curve"]
    curve_rows = [
        [
            offset,
            None if curve["retention"][i] is None else round(curve["retention"][i], 4),
            curve["cohorts_observed"][i],
            curve["entities_observed"][i],
            None if curve["cumulative_value_per_entity"][i] is None
            else round(curve["cumulative_value_per_entity"][i], 4),
        ]
        for i, offset in enumerate(offsets)
    ]
    tables.append({
        "title": "Pooled retention curve",
        "columns": [
            {"name": f"{noun.title()}s on", "kind": "number"},
            {"name": "Retained", "kind": "number"},
            {"name": "Cohorts counted", "kind": "number"},
            {"name": f"{result['entity']}s counted", "kind": "number"},
            {"name": f"Cumulative {result['measure']} per {result['entity']}" if result["measure"]
             else "Cumulative value", "kind": "number"},
        ],
        "rows": curve_rows,
        "total_rows": len(curve_rows),
        "truncated": False,
    })
    return tables


# ----------------------------------------------------------------------------- markdown


def cohort_markdown(result: dict[str, Any], dataset_name: str = "") -> str:
    summary, noun = result["summary"], result["period_noun"]
    coverage = result["coverage"]
    median = summary["median_active_periods"]
    lines = [
        f"# Retention of {result['entity']}",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}"
        f"{coverage['first_period'][:10]} → {coverage['last_period'][:10]} · "
        f"{summary['entities']:,} {result['entity']} values in {summary['cohorts']} "
        f"{noun}ly cohorts_",
        "",
        f"**{result['headline']}**",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Come back at all | {_pct(summary['repeat_rate'])} |",
        f"| Next {noun} | {_pct(summary['retention_1'])} |",
        f"| {3} {noun}s on | {_pct(summary['retention_3'])} |",
        f"| {6} {noun}s on | {_pct(summary['retention_6'])} |",
        f"| {12} {noun}s on | {_pct(summary['retention_12'])} |",
        f"| Median active {noun}s | {'n/a' if median is None else format(median, '.0f')} |",
        "",
    ]
    for line in result["narrative"]:
        lines.append(f"- {line}")
    lines.append("")

    lines += [
        f"## Retention grid ({noun}s since first seen)",
        "",
        "| Cohort | Size | " + " | ".join(str(o) for o in result["offsets"]) + " |",
        "|---|---:|" + "---:|" * len(result["offsets"]),
    ]
    for cohort in reversed(result["cohorts"]):
        cells = " | ".join(
            "" if value is None else _pct(value, 0) for value in cohort["retention"]
        )
        lines.append(f"| {cohort['label']} | {cohort['size']:,} | {cells} |")
    lines.append("")

    if result["measure"] and summary["value_per_entity_total"] is not None:
        lines += [
            f"## Observed {result['measure']} per {result['entity']}",
            "",
            f"| {noun.title()}s on | Cumulative |",
            "|---:|---:|",
        ]
        for offset, value in zip(result["curve"]["offsets"],
                                 result["curve"]["cumulative_value_per_entity"]):
            if value is not None:
                lines.append(f"| {offset} | {_compact(value)} |")
        lines.append("")

    if result["caveats"]:
        lines += ["## Caveats", ""] + [f"- {c}" for c in result["caveats"]] + [""]
    lines += [
        "---",
        "",
        "_Computed deterministically from the cleaned table — no model call, no projection._",
    ]
    return "\n".join(lines)
