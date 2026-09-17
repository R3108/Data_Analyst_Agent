"""Scenario planning: what would have to be true for the number to land there?

The drill-down explains a change that already happened. This is the other half of the
same arithmetic, pointed forwards: hold the shape of the business fixed, move one lever,
and read off what the measure becomes.

Three things it does that a spreadsheet does not:

* **Levers that respect structure.** Volume (how many records), rate (the average value
  per record) and mix (the share each segment holds) are moved independently, per
  segment or across the board. Moving mix redistributes the remaining share across the
  untouched segments, so the shares still sum to one — a "what if Enterprise were 40% of
  the book" that quietly leaves the other segments adding to 75% is not a scenario.
* **The same decomposition as the drill-down.** The projected change is split into
  volume, mix and rate with the identity from `drivers.shift_share`, so the forward-looking
  and backward-looking views of the same table reconcile.
* **Goal seek.** Name a target instead of a lever and bisection finds the lever value
  that reaches it — and says plainly when nothing in the allowed range does.

Deterministic pandas over the cleaned table: no model call, no simulation noise, and the
same inputs give the same answer every time.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import pandas as pd

from app.core.errors import InvalidInputError
from app.core.formatting import compact, percent, signed_percent
from app.services.drivers import (
    Aggregation,
    MAX_CARDINALITY,
    default_aggregation,
    driver_options,
    resolve_windows,
    shift_share,
)

logger = logging.getLogger(__name__)

# A lever below −100% would mean negative records; above this it is fantasy, not planning.
MIN_LEVER = -0.95
MAX_LEVER = 10.0
# Segments beyond this stop being levers and start being a spreadsheet.
MAX_SEGMENTS = 24
MIN_ROWS = 5
GOAL_SEEK_ITERATIONS = 80
# Bisection stops when the projected value is this close, relatively, to the target.
GOAL_SEEK_TOLERANCE = 1e-9

LeverKind = Literal["rate", "volume"]
LEVER_KINDS = ("rate", "volume")

INCREASE = "#1baf7a"
DECREASE = "#e34948"
TOTAL = "#2a78d6"
CONNECTOR = "#c3c2b7"


# ----------------------------------------------------------------------------- options


def scenario_options(profile: dict[str, Any]) -> dict[str, Any]:
    """Which measures, segmentations and periods a scenario can be built on.

    Deliberately the driver options: a reader who drilled into a change should find the
    same measures and dimensions when they ask what would move it.
    """
    options = driver_options(profile)
    return {**options, "levers": list(LEVER_KINDS), "max_segments": MAX_SEGMENTS}


# ----------------------------------------------------------------------------- baseline


def _baseline(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    measure: str | None,
    dimension: str | None,
    date_column: str | None,
    aggregation: Aggregation | None,
    period: str,
    baseline_start: str | None,
    baseline_end: str | None,
    current_start: str | None,
    current_end: str | None,
) -> dict[str, Any]:
    """The most recent period, broken into segments — the thing the levers act on."""
    options = scenario_options(profile)
    measure = measure or options["defaults"]["measure"]
    dimension = dimension or (options["dimensions"][0] if options["dimensions"] else None)
    date_column = date_column or options["defaults"]["date_column"]

    if not measure:
        raise InvalidInputError("This dataset has no numeric measure to plan with.")
    if measure not in df.columns:
        raise InvalidInputError(f"Column '{measure}' is not in this dataset.")
    if not pd.api.types.is_numeric_dtype(df[measure]):
        raise InvalidInputError(f"'{measure}' is not numeric, so it cannot be projected.")
    if not dimension:
        raise InvalidInputError(
            "A scenario needs a categorical column to move share between; this dataset has none."
        )
    if dimension not in df.columns:
        raise InvalidInputError(f"Column '{dimension}' is not in this dataset.")

    agg: Aggregation = aggregation or default_aggregation(measure)
    if agg not in ("sum", "mean"):
        raise InvalidInputError("Aggregation must be 'sum' or 'mean'.")

    frame = df[[c for c in {measure, dimension, date_column} if c and c in df.columns]].copy()
    usable = frame.dropna(subset=[measure])
    dropped = int(len(df) - len(usable))

    window: dict[str, Any] | None = None
    if date_column:
        if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
            raise InvalidInputError(f"'{date_column}' is not a date column.")
        dated = usable.dropna(subset=[date_column])
        if not dated.empty:
            _, current, resolved = resolve_windows(
                dated[date_column], period,
                baseline_start, baseline_end, current_start, current_end,
            )
            selected = dated[current.mask(dated[date_column])]
            if len(selected) >= MIN_ROWS:
                usable = selected
                window = {"mode": resolved, **current.to_dict()}
            else:
                # Too little in the trailing window to plan from; use the whole history
                # and say so rather than projecting from a handful of rows.
                window = {"mode": "all", "start": dated[date_column].min().isoformat(),
                          "end": dated[date_column].max().isoformat(),
                          "label": "all available history"}
    if usable.empty:
        raise InvalidInputError(f"No rows have a {measure} value to plan from.")

    labels = usable[dimension].astype("object").where(usable[dimension].notna(), "(missing)")
    grouped = usable.groupby(labels, observed=True, dropna=False)[measure]
    stats = pd.DataFrame({"sum": grouped.sum(), "n": grouped.count()})
    stats = stats[stats["n"] > 0].sort_values("sum", ascending=False)
    if stats.empty:
        raise InvalidInputError(f"'{dimension}' has no values with a {measure} to plan from.")
    if len(stats) > MAX_CARDINALITY:
        raise InvalidInputError(
            f"'{dimension}' has {len(stats):,} distinct values — too many to plan segment by "
            "segment. Pick a coarser grouping."
        )

    total_n = float(stats["n"].sum())
    segments = [
        {
            "label": _label(index),
            "rows": float(row["n"]),
            "average": float(row["sum"] / row["n"]),
            "share": float(row["n"] / total_n),
        }
        for index, row in stats.iterrows()
    ]
    return {
        "measure": measure,
        "dimension": dimension,
        "date_column": date_column,
        "aggregation": agg,
        "segments": segments[:MAX_SEGMENTS] if len(segments) <= MAX_SEGMENTS else _fold(segments),
        "total_rows": total_n,
        "window": window,
        "dropped_rows": dropped,
        "options": options,
    }


def _fold(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the levers that matter; fold the tail into one honest "Other" segment."""
    head, tail = segments[:MAX_SEGMENTS - 1], segments[MAX_SEGMENTS - 1:]
    rows = sum(s["rows"] for s in tail)
    value = sum(s["rows"] * s["average"] for s in tail)
    return head + [{
        "label": f"Other ({len(tail)} values)",
        "rows": rows,
        "average": value / rows if rows else 0.0,
        "share": sum(s["share"] for s in tail),
        "folded": True,
    }]


def _label(key: Any) -> str:
    if isinstance(key, bool):
        return "Yes" if key else "No"
    text = str(key)
    return text if len(text) <= 60 else f"{text[:57]}…"


# ----------------------------------------------------------------------------- projection


def _project(baseline: dict[str, Any], levers: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the levers to the baseline segments. The order matters and is fixed:
    rate, then volume, then mix — because mix is a statement about shares *after* the
    volume moves, which is how anyone stating one means it."""
    globals_ = levers.get("global") or {}
    per_segment = levers.get("segments") or {}
    global_volume = _lever(globals_.get("volume_pct"))
    global_rate = _lever(globals_.get("rate_pct"))

    projected: list[dict[str, Any]] = []
    for segment in baseline["segments"]:
        overrides = per_segment.get(segment["label"]) or {}
        rate = _lever(overrides.get("rate_pct")) + global_rate
        volume = _lever(overrides.get("volume_pct")) + global_volume
        projected.append({
            "label": segment["label"],
            "rows": max(0.0, segment["rows"] * (1.0 + volume)),
            "average": segment["average"] * (1.0 + rate),
            "share_points": _share_points(overrides.get("share_points")),
        })

    total_rows = sum(s["rows"] for s in projected)
    if total_rows <= 0:
        # Every segment driven to zero: a legal scenario, and the answer is zero.
        for segment in projected:
            segment["share"] = 0.0
        return projected

    for segment in projected:
        segment["share"] = segment["rows"] / total_rows
    _apply_mix(projected)
    for segment in projected:
        segment["rows"] = total_rows * segment["share"]
    return projected


def _apply_mix(projected: list[dict[str, Any]]) -> None:
    """Move share between segments and keep the shares summing to one.

    The requested shifts are honoured first; whatever share is left over is spread across
    the untouched segments in proportion to the share they already held. If the requests
    alone exceed 100% they are scaled back proportionally rather than silently clipped.
    """
    pinned = [s for s in projected if s["share_points"]]
    if not pinned:
        return
    free = [s for s in projected if not s["share_points"]]

    targets = {s["label"]: max(0.0, min(1.0, s["share"] + s["share_points"])) for s in pinned}
    requested = sum(targets.values())
    if requested > 1.0:
        targets = {label: value / requested for label, value in targets.items()}
        requested = 1.0

    remaining = max(0.0, 1.0 - requested)
    free_share = sum(s["share"] for s in free)
    for segment in projected:
        if segment["label"] in targets:
            segment["share"] = targets[segment["label"]]
        elif free_share > 0:
            segment["share"] = remaining * segment["share"] / free_share
        else:
            # Nothing left to redistribute proportionally: split what remains evenly.
            segment["share"] = remaining / len(free) if free else 0.0


def _total(segments: list[dict[str, Any]], agg: Aggregation) -> float:
    if agg == "sum":
        return float(sum(s["rows"] * s["average"] for s in segments))
    total_rows = sum(s["rows"] for s in segments)
    if not total_rows:
        return 0.0
    return float(sum(s["rows"] * s["average"] for s in segments) / total_rows)


def _lever(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"'{value}' is not a valid percentage change.") from exc
    if number != number:
        raise InvalidInputError("A lever cannot be blank.")
    if not MIN_LEVER <= number <= MAX_LEVER:
        raise InvalidInputError(
            f"Levers are limited to {MIN_LEVER:+.0%} to {MAX_LEVER:+.0%}; got {number:+.0%}."
        )
    return number


def _share_points(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"'{value}' is not a valid share shift.") from exc
    if not -1.0 <= number <= 1.0:
        raise InvalidInputError(
            "A share shift is a fraction between −1 and 1 (−0.05 moves five points out)."
        )
    return number


# ----------------------------------------------------------------------------- entry point


def simulate(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    measure: str | None = None,
    dimension: str | None = None,
    date_column: str | None = None,
    aggregation: Aggregation | None = None,
    period: str = "auto",
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    current_start: str | None = None,
    current_end: str | None = None,
    levers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project `measure` under the given levers. Pure pandas, no model call."""
    baseline = _baseline(
        df, profile, measure=measure, dimension=dimension, date_column=date_column,
        aggregation=aggregation, period=period, baseline_start=baseline_start,
        baseline_end=baseline_end, current_start=current_start, current_end=current_end,
    )
    levers = _normalize_levers(levers, baseline)
    agg = baseline["aggregation"]

    projected = _project(baseline, levers)
    baseline_value = _total(baseline["segments"], agg)
    scenario_value = _total(projected, agg)
    change = scenario_value - baseline_value

    result: dict[str, Any] = {
        "measure": baseline["measure"],
        "dimension": baseline["dimension"],
        "date_column": baseline["date_column"],
        "aggregation": agg,
        "window": baseline["window"],
        "levers": levers,
        "baseline": {
            "value": baseline_value,
            "rows": baseline["total_rows"],
            "segments": [
                {**s, "value": _segment_value(s, baseline["segments"], agg)}
                for s in baseline["segments"]
            ],
        },
        "scenario": {
            "value": scenario_value,
            "rows": float(sum(s["rows"] for s in projected)),
            "segments": [
                {**s, "value": _segment_value(s, projected, agg)} for s in projected
            ],
        },
        "change": {
            "absolute": change,
            "pct": (change / abs(baseline_value)) if baseline_value else None,
            "direction": "up" if change > 0 else "down" if change < 0 else "flat",
        },
    }
    result["segments"] = _segment_rows(result)
    result["shift_share"] = _decompose(baseline["segments"], projected, change,
                                       baseline["dimension"], agg)
    result["sensitivity"] = _sensitivity(baseline, agg)
    result["headline"] = _headline(result)
    result["narrative"] = _narrative(result)
    result["caveats"] = _caveats(result, baseline)
    result["charts"] = _charts(result)
    result["tables"] = _tables(result)
    result["follow_up"] = _follow_up(result)
    result["options"] = baseline["options"]
    return result


def _normalize_levers(levers: dict[str, Any] | None, baseline: dict[str, Any]) -> dict[str, Any]:
    """Validate the levers and drop any segment that is not in the baseline."""
    levers = levers or {}
    if not isinstance(levers, dict):
        raise InvalidInputError("Levers must be an object.")
    known = {s["label"] for s in baseline["segments"]}
    raw_segments = levers.get("segments") or {}
    if not isinstance(raw_segments, dict):
        raise InvalidInputError("Segment levers must be an object keyed by segment name.")

    unknown = [label for label in raw_segments if label not in known]
    if unknown:
        preview = ", ".join(sorted(known)[:6])
        raise InvalidInputError(
            f"'{unknown[0]}' is not a {baseline['dimension']} in the selected period. "
            f"Available: {preview}{'…' if len(known) > 6 else ''}"
        )

    global_levers = levers.get("global") or {}
    normalized_global = {
        "volume_pct": _lever(global_levers.get("volume_pct")),
        "rate_pct": _lever(global_levers.get("rate_pct")),
    }
    normalized_segments = {
        label: {
            "volume_pct": _lever((values or {}).get("volume_pct")),
            "rate_pct": _lever((values or {}).get("rate_pct")),
            "share_points": _share_points((values or {}).get("share_points")),
        }
        for label, values in raw_segments.items()
    }
    # An untouched segment carries no lever, so the payload says what was actually moved.
    normalized_segments = {
        label: values for label, values in normalized_segments.items() if any(values.values())
    }
    return {"global": normalized_global, "segments": normalized_segments}


def _segment_value(segment: dict[str, Any], all_segments: list[dict[str, Any]],
                   agg: Aggregation) -> float:
    if agg == "sum":
        return float(segment["rows"] * segment["average"])
    total_rows = sum(s["rows"] for s in all_segments)
    if not total_rows:
        return 0.0
    return float(segment["rows"] * segment["average"] / total_rows)


def _segment_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    scenario = {s["label"]: s for s in result["scenario"]["segments"]}
    rows = []
    for base in result["baseline"]["segments"]:
        after = scenario.get(base["label"])
        if after is None:
            continue
        change = after["value"] - base["value"]
        rows.append({
            "label": base["label"],
            "baseline_value": base["value"],
            "scenario_value": after["value"],
            "change": change,
            "change_pct": (change / abs(base["value"])) if base["value"] else None,
            "baseline_rows": base["rows"],
            "scenario_rows": after["rows"],
            "baseline_average": base["average"],
            "scenario_average": after["average"],
            "baseline_share": base["share"],
            "scenario_share": after["share"],
            "share_change": after["share"] - base["share"],
        })
    rows.sort(key=lambda r: abs(r["change"]), reverse=True)
    return rows


def _decompose(baseline: list[dict[str, Any]], projected: list[dict[str, Any]],
               change: float, dimension: str, agg: Aggregation) -> dict[str, Any]:
    """Split the projected change with the identity the drill-down uses."""
    by_label = {s["label"]: s for s in projected}
    n_base = sum(s["rows"] for s in baseline)
    n_new = sum(s["rows"] for s in projected)
    baseline_total = _total(baseline, agg)

    mix = rate = 0.0
    for segment in baseline:
        after = by_label.get(segment["label"])
        if after is None:
            continue
        if agg == "sum":
            mix += n_new * (after["share"] - segment["share"]) * segment["average"]
            rate += n_new * after["share"] * (after["average"] - segment["average"])
        else:
            mix += (after["share"] - segment["share"]) * segment["average"]
            rate += after["share"] * (after["average"] - segment["average"])
    volume = (n_new - n_base) * (baseline_total / n_base) if agg == "sum" and n_base else 0.0
    return shift_share(volume, mix, rate, change, dimension, agg)


def _sensitivity(baseline: dict[str, Any], agg: Aggregation) -> list[dict[str, Any]]:
    """Which lever is worth pulling: the measure's response to a 1% move in each segment.

    Ranked so the reader sees leverage rather than size — a segment that is 5% of the book
    but carries twice the average value moves the total more than its share suggests.
    """
    total = _total(baseline["segments"], agg)
    rows: list[dict[str, Any]] = []
    for segment in baseline["segments"]:
        bumped = [
            {**s, "average": s["average"] * 1.01} if s["label"] == segment["label"] else dict(s)
            for s in baseline["segments"]
        ]
        rate_gain = _total(bumped, agg) - total

        grown = [
            {**s, "rows": s["rows"] * 1.01} if s["label"] == segment["label"] else dict(s)
            for s in baseline["segments"]
        ]
        grown_total = sum(s["rows"] for s in grown)
        for item in grown:
            item["share"] = item["rows"] / grown_total if grown_total else 0.0
        volume_gain = _total(grown, agg) - total

        rows.append({
            "label": segment["label"],
            "share": segment["share"],
            "rate_gain_per_pct": rate_gain,
            "volume_gain_per_pct": volume_gain,
            # Scale-free: how many times its share of records this segment is worth.
            "leverage": (rate_gain / total / 0.01 / segment["share"])
            if total and segment["share"] else None,
        })
    rows.sort(key=lambda r: abs(r["rate_gain_per_pct"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


# ----------------------------------------------------------------------------- goal seek


def goal_seek(
    df: pd.DataFrame,
    profile: dict[str, Any],
    *,
    target: float,
    lever: LeverKind = "rate",
    segment: str | None = None,
    measure: str | None = None,
    dimension: str | None = None,
    date_column: str | None = None,
    aggregation: Aggregation | None = None,
    period: str = "auto",
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    current_start: str | None = None,
    current_end: str | None = None,
    levers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Solve for the lever value that reaches `target`.

    The projection is monotonic in a single lever, so bisection converges and — unlike a
    solver that reports the closest it got — this one can say honestly that no value in
    the allowed range reaches the target.
    """
    if lever not in LEVER_KINDS:
        raise InvalidInputError(f"Unknown lever '{lever}'. Use 'rate' or 'volume'.")
    try:
        target = float(target)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("The target must be a number.") from exc
    if target != target:
        raise InvalidInputError("The target must be a number.")

    baseline = _baseline(
        df, profile, measure=measure, dimension=dimension, date_column=date_column,
        aggregation=aggregation, period=period, baseline_start=baseline_start,
        baseline_end=baseline_end, current_start=current_start, current_end=current_end,
    )
    base_levers = _normalize_levers(levers, baseline)
    if segment is not None and segment not in {s["label"] for s in baseline["segments"]}:
        raise InvalidInputError(f"'{segment}' is not a {baseline['dimension']} in this period.")
    if baseline["aggregation"] == "mean" and lever == "volume" and segment is None:
        raise InvalidInputError(
            f"{baseline['measure']} is averaged per record, so changing the number of records "
            "across the board does not move it. Try the rate lever, or a single segment."
        )

    def evaluate(value: float) -> float:
        return _total(_project(baseline, _with(base_levers, lever, segment, value)),
                      baseline["aggregation"])

    low_value, high_value = evaluate(MIN_LEVER), evaluate(MAX_LEVER)
    lower, upper = min(low_value, high_value), max(low_value, high_value)
    if not lower <= target <= upper:
        return {
            "achievable": False,
            "target": target,
            "lever": {"kind": lever, "segment": segment},
            "reachable_range": {"min": lower, "max": upper},
            "message": (
                f"No {lever} change between {MIN_LEVER:+.0%} and {MAX_LEVER:+.0%}"
                + (f" in {segment}" if segment else "")
                + f" reaches {compact(target)}. That lever can only take "
                f"{baseline['measure']} to between {compact(lower)} and {compact(upper)}."
            ),
            "scenario": simulate(
                df, profile, measure=measure, dimension=dimension, date_column=date_column,
                aggregation=aggregation, period=period, baseline_start=baseline_start,
                baseline_end=baseline_end, current_start=current_start, current_end=current_end,
                levers=base_levers,
            ),
        }

    ascending = high_value >= low_value
    low, high = MIN_LEVER, MAX_LEVER
    solution = 0.0
    for _ in range(GOAL_SEEK_ITERATIONS):
        solution = (low + high) / 2.0
        value = evaluate(solution)
        if abs(value - target) <= max(abs(target), 1.0) * GOAL_SEEK_TOLERANCE:
            break
        if (value < target) == ascending:
            low = solution
        else:
            high = solution

    scenario = simulate(
        df, profile, measure=measure, dimension=dimension, date_column=date_column,
        aggregation=aggregation, period=period, baseline_start=baseline_start,
        baseline_end=baseline_end, current_start=current_start, current_end=current_end,
        levers=_with(base_levers, lever, segment, solution),
    )
    where = f"{segment}'s" if segment else "the overall"
    noun = "average value per record" if lever == "rate" else "number of records"
    return {
        "achievable": True,
        "target": target,
        "lever": {"kind": lever, "segment": segment},
        "required_pct": solution,
        "achieved": scenario["scenario"]["value"],
        "message": (
            f"Reaching {compact(target)} needs {where} {noun} to move "
            f"{signed_percent(solution)}."
        ),
        "scenario": scenario,
    }


def _with(levers: dict[str, Any], lever: LeverKind, segment: str | None,
          value: float) -> dict[str, Any]:
    """A copy of `levers` with one lever set — never a mutation of the caller's dict."""
    key = f"{lever}_pct"
    if segment is None:
        return {
            "global": {**levers["global"], key: value},
            "segments": {label: dict(v) for label, v in levers["segments"].items()},
        }
    segments = {label: dict(v) for label, v in levers["segments"].items()}
    current = segments.get(segment) or {"volume_pct": 0.0, "rate_pct": 0.0, "share_points": 0.0}
    segments[segment] = {**current, key: value}
    return {"global": dict(levers["global"]), "segments": segments}


# ----------------------------------------------------------------------------- narrative


def _headline(result: dict[str, Any]) -> str:
    change = result["change"]
    measure = result["measure"]
    if change["direction"] == "flat":
        return f"No lever is set, so {measure} stays at {compact(result['baseline']['value'])}."
    movement = "rises" if change["direction"] == "up" else "falls"
    amount = f" {percent(abs(change['pct']))}" if change["pct"] is not None else ""
    return (
        f"{measure} {movement}{amount} to {compact(result['scenario']['value'])} "
        f"from {compact(result['baseline']['value'])} — a change of "
        f"{compact(change['absolute'])}."
    )


def _narrative(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    moved = _describe_levers(result)
    if moved:
        lines.append("Levers applied: " + "; ".join(moved) + ".")
    else:
        lines.append(
            "Nothing is moved yet — this is the current period, ready for a lever. "
            "The sensitivity ranking below shows which segment is worth pulling."
        )

    top = result["segments"][0] if result["segments"] else None
    if top and top["change"]:
        lines.append(
            f"**{top['label']}** moves most: {compact(top['baseline_value'])} → "
            f"{compact(top['scenario_value'])} ({compact(top['change'])}), and its share of "
            f"{result['measure']} goes from {percent(top['baseline_share'])} to "
            f"{percent(top['scenario_share'])}."
        )

    shift = result["shift_share"]
    parts = [f"{compact(t['value'])} from {t['label'].lower()}" for t in shift["terms"] if t["value"]]
    if parts and result["change"]["absolute"]:
        lines.append(
            f"Split the way the drill-down splits a real change, the "
            f"{compact(result['change']['absolute'])} move is "
            + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."
        )

    best = result["sensitivity"][0] if result["sensitivity"] else None
    if best and best["leverage"] is not None:
        lines.append(
            f"Highest leverage right now is **{best['label']}**: a 1% lift in its average value "
            f"adds {compact(best['rate_gain_per_pct'])}, which is {best['leverage']:.1f}× what "
            f"its {percent(best['share'])} share of records would imply."
        )
    return lines


def _describe_levers(result: dict[str, Any]) -> list[str]:
    levers = result["levers"]
    described: list[str] = []
    global_levers = levers["global"]
    if global_levers.get("rate_pct"):
        described.append(f"average value {signed_percent(global_levers['rate_pct'])} across the board")
    if global_levers.get("volume_pct"):
        described.append(f"record count {signed_percent(global_levers['volume_pct'])} across the board")
    for label, values in levers["segments"].items():
        if values.get("rate_pct"):
            described.append(f"{label} average value {signed_percent(values['rate_pct'])}")
        if values.get("volume_pct"):
            described.append(f"{label} record count {signed_percent(values['volume_pct'])}")
        if values.get("share_points"):
            described.append(
                f"{label} share {signed_percent(values['share_points'])} of the mix"
            )
    return described


def _caveats(result: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    caveats = [
        "A scenario is arithmetic, not a forecast: it assumes every segment keeps the "
        "distribution it has today and that the levers do not affect one another.",
    ]
    window = result["window"]
    if window and window.get("mode") == "all":
        caveats.append(
            "The most recent comparison window held too few rows to plan from, so the "
            "baseline is the whole history — seasonality is therefore averaged out of it."
        )
    elif window:
        caveats.append(
            f"The baseline is {window['label']} ({window['start'][:10]} → {window['end'][:10]}); "
            "a partially-loaded final period will understate it."
        )
    if baseline["dropped_rows"]:
        caveats.append(
            f"{baseline['dropped_rows']:,} rows have no {result['measure']} value and are excluded."
        )
    if any(s.get("folded") for s in baseline["segments"]):
        caveats.append(
            f"The smallest {result['dimension']} values are folded into one 'Other' segment, "
            "which can only be moved as a block."
        )
    if result["aggregation"] == "mean":
        caveats.append(
            f"{result['measure']} is averaged, not summed, so the overall record count does not "
            "move it — only the mix between segments and the per-record rate do."
        )
    if not result["shift_share"]["closes"]:
        caveats.append(
            "The volume/mix/rate split does not reconcile exactly — treat it as indicative."
        )
    return caveats


def _follow_up(result: dict[str, Any]) -> str:
    best = result["sensitivity"][0] if result["sensitivity"] else None
    measure, dimension = result["measure"], result["dimension"]
    if best:
        return (
            f"What would it take to lift {measure} in {best['label']}, and what has driven "
            f"that {dimension}'s average value historically?"
        )
    return f"What levers realistically move {measure}?"


# ----------------------------------------------------------------------------- artifacts


def _charts(result: dict[str, Any]) -> list[dict[str, Any]]:
    segments = [s for s in result["segments"] if s["change"]][:12]
    charts: list[dict[str, Any]] = []
    if segments:
        labels = ["Today"] + [s["label"] for s in segments] + ["Scenario"]
        values = [result["baseline"]["value"]] + [s["change"] for s in segments] + [0.0]
        measures = ["absolute"] + ["relative"] * len(segments) + ["total"]
        # Segments folded out of the waterfall would make it not add up; close the gap.
        shown = sum(s["change"] for s in segments)
        residual = result["change"]["absolute"] - shown
        if abs(residual) > max(1e-9, abs(result["change"]["absolute"]) * 1e-9):
            labels.insert(-1, "Other segments")
            values.insert(-1, residual)
            measures.insert(-1, "relative")
        charts.append(_waterfall(
            f"{result['measure']} under this scenario",
            f"Each step is one {result['dimension']} value's contribution to the "
            f"{compact(result['change']['absolute'])} projected change.",
            labels, values, measures, result["measure"],
        ))

    sensitivity = result["sensitivity"][:12]
    if sensitivity:
        charts.append({
            "title": f"Leverage by {result['dimension']}",
            "caption": (
                f"What a 1% lift in each segment's average value adds to {result['measure']}. "
                "The tallest bar is the lever worth pulling first."
            ),
            "figure": {
                "data": [{
                    "type": "bar", "orientation": "h",
                    "x": [round(float(s["rate_gain_per_pct"]), 6) for s in sensitivity],
                    "y": [s["label"] for s in sensitivity],
                    "marker": {"color": TOTAL},
                    "hovertemplate": "%{y}<br>+%{x:,.2f} per 1%<extra></extra>",
                }],
                "layout": {
                    "showlegend": False,
                    "xaxis": {"title": {"text": f"{result['measure']} added per 1% lift"},
                              "zeroline": True},
                    "yaxis": {"automargin": True, "autorange": "reversed"},
                    "margin": {"l": 120, "r": 24, "t": 24, "b": 48},
                },
            },
            "digest": {
                "traces": [{
                    "type": "bar", "name": f"{result['measure']} per 1% lift",
                    "x": [s["label"] for s in sensitivity], "x_count": len(sensitivity),
                    "y": [round(float(s["rate_gain_per_pct"]), 6) for s in sensitivity],
                    "y_count": len(sensitivity),
                }],
                "axes": {"yaxis": result["measure"]},
            },
        })
    return charts


def _waterfall(title: str, caption: str, labels: list[str], values: list[float],
               measures: list[str], axis: str) -> dict[str, Any]:
    steps = [(label, value) for label, value, kind in zip(labels, values, measures)
             if kind == "relative"]
    return {
        "title": title,
        "caption": caption,
        "figure": {
            "data": [{
                "type": "waterfall", "orientation": "v", "measure": measures,
                "x": labels, "y": [round(float(v), 6) for v in values],
                "text": [compact(float(v)) if m != "total" else "" for v, m in zip(values, measures)],
                "textposition": "outside", "cliponaxis": False,
                "connector": {"line": {"color": CONNECTOR, "width": 1}},
                "increasing": {"marker": {"color": INCREASE}},
                "decreasing": {"marker": {"color": DECREASE}},
                "totals": {"marker": {"color": TOTAL}},
                "hovertemplate": "%{x}<br>%{y:,.2f}<extra></extra>",
            }],
            "layout": {
                "showlegend": False,
                "yaxis": {"title": {"text": axis}, "zeroline": True},
                "xaxis": {"automargin": True, "tickangle": -25},
                "margin": {"l": 64, "r": 16, "t": 24, "b": 80},
            },
        },
        "digest": {
            "traces": [{
                "type": "bar", "name": f"Contribution to {axis}",
                "x": [label for label, _ in steps], "x_count": len(steps),
                "y": [round(float(value), 6) for _, value in steps], "y_count": len(steps),
            }],
            "axes": {"yaxis": axis},
        },
    }


def _tables(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        [
            s["label"],
            round(s["baseline_value"], 4),
            round(s["scenario_value"], 4),
            round(s["change"], 4),
            None if s["change_pct"] is None else round(s["change_pct"], 4),
            round(s["baseline_share"], 4),
            round(s["scenario_share"], 4),
        ]
        for s in result["segments"]
    ]
    tables = [{
        "title": f"{result['measure']} by {result['dimension']} — today vs scenario",
        "columns": [
            {"name": result["dimension"], "kind": "text"},
            {"name": "Today", "kind": "number"},
            {"name": "Scenario", "kind": "number"},
            {"name": "Change", "kind": "number"},
            {"name": "Change %", "kind": "number"},
            {"name": "Share today", "kind": "number"},
            {"name": "Share scenario", "kind": "number"},
        ],
        "rows": rows,
        "total_rows": len(rows),
        "truncated": False,
    }]
    sensitivity = result["sensitivity"]
    if sensitivity:
        tables.append({
            "title": f"Leverage by {result['dimension']}",
            "columns": [
                {"name": result["dimension"], "kind": "text"},
                {"name": "Share of records", "kind": "number"},
                {"name": "Per 1% rate lift", "kind": "number"},
                {"name": "Per 1% more records", "kind": "number"},
                {"name": "Leverage vs share", "kind": "number"},
            ],
            "rows": [
                [s["label"], round(s["share"], 4), round(s["rate_gain_per_pct"], 4),
                 round(s["volume_gain_per_pct"], 4),
                 None if s["leverage"] is None else round(s["leverage"], 3)]
                for s in sensitivity
            ],
            "total_rows": len(sensitivity),
            "truncated": False,
        })
    return tables


# ----------------------------------------------------------------------------- markdown


def simulate_markdown(result: dict[str, Any], dataset_name: str = "",
                      goal: dict[str, Any] | None = None) -> str:
    window = result["window"]
    lines = [
        f"# {result['measure']} — scenario",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}"
        + (f"baseline {window['label']} ({window['start'][:10]} → {window['end'][:10]})"
           if window else "baseline: all available data")
        + "_",
        "",
    ]
    if goal:
        lines += [f"**Goal seek.** {goal['message']}", ""]
    lines += [
        f"**{result['headline']}**",
        "",
        f"| | Today | Scenario | Change |",
        "|---|---:|---:|---:|",
        f"| {result['measure']} | {compact(result['baseline']['value'])} | "
        f"{compact(result['scenario']['value'])} | {compact(result['change']['absolute'])} "
        f"({percent(result['change']['pct'])}) |",
        "",
    ]
    for line in result["narrative"]:
        lines.append(f"- {line}")

    lines += [
        "",
        f"## {result['measure']} by {result['dimension']}",
        "",
        f"| {result['dimension']} | Today | Scenario | Change | Share today | Share scenario |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for segment in result["segments"]:
        lines.append(
            f"| {segment['label']} | {compact(segment['baseline_value'])} | "
            f"{compact(segment['scenario_value'])} | {compact(segment['change'])} | "
            f"{percent(segment['baseline_share'])} | {percent(segment['scenario_share'])} |"
        )

    shift = result["shift_share"]
    lines += [
        "",
        "## Volume · mix · rate",
        "",
        "| Term | Value | Share of change |",
        "|---|---:|---:|",
    ]
    for term in shift["terms"]:
        lines.append(f"| {term['label']} | {compact(term['value'])} | {percent(term['share'])} |")
    lines.append(
        f"\n_Residual {compact(shift['residual'])} — "
        + ("the three terms reconcile to the projected change exactly._"
           if shift["closes"] else "the split does not reconcile; treat it as indicative._")
    )

    lines += ["", "## Leverage", "",
              f"| {result['dimension']} | Share of records | Per 1% rate lift | Leverage |",
              "|---|---:|---:|---:|"]
    for row in result["sensitivity"]:
        leverage = "n/a" if row["leverage"] is None else f"{row['leverage']:.2f}×"
        lines.append(
            f"| {row['label']} | {percent(row['share'])} | "
            f"{compact(row['rate_gain_per_pct'])} | {leverage} |"
        )

    if result["caveats"]:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in result["caveats"]]
    lines += [
        "",
        "---",
        "",
        "_Computed deterministically from the cleaned table — no model call, no simulation "
        "noise, and the volume/mix/rate split uses the same identity as the drill-down._",
    ]
    return "\n".join(lines)


__all__ = ["goal_seek", "scenario_options", "simulate", "simulate_markdown"]
