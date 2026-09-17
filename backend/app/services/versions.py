"""Dataset versions: what changed between two uploads of the same table.

Re-uploading next month's export is the most common thing a real user does, and
the question that follows is always "what moved?". The diff is computed from the
stored profiles alone — no data is re-read, so it is instant.
"""

from __future__ import annotations

from typing import Any

from app.services.profiling import NON_ADDITIVE, rank_measures

MATERIAL_ROW_CHANGE = 0.02
MATERIAL_MEASURE_CHANGE = 0.05


def diff_datasets(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare two dataset records (each with `profile` and `cleaning`)."""
    before, after = previous.get("profile") or {}, current.get("profile") or {}
    columns_before = {c["name"]: c for c in before.get("columns") or []}
    columns_after = {c["name"]: c for c in after.get("columns") or []}

    added = [name for name in columns_after if name not in columns_before]
    removed = [name for name in columns_before if name not in columns_after]
    type_changes = [
        {"column": name, "previous": columns_before[name]["dtype"], "current": column["dtype"]}
        for name, column in columns_after.items()
        if name in columns_before and columns_before[name]["dtype"] != column["dtype"]
    ]

    rows_before = int(before.get("n_rows") or 0)
    rows_after = int(after.get("n_rows") or 0)
    rows = {
        "previous": rows_before,
        "current": rows_after,
        "change": rows_after - rows_before,
        "change_pct": _pct(rows_before, rows_after),
    }

    quality_before = int((previous.get("cleaning") or {}).get("quality_score") or 0)
    quality_after = int((current.get("cleaning") or {}).get("quality_score") or 0)

    diff: dict[str, Any] = {
        "previous_dataset_id": previous.get("id"),
        "previous_version": previous.get("version", 1),
        "current_version": current.get("version", 1),
        "rows": rows,
        "columns_added": added,
        "columns_removed": removed,
        "type_changes": type_changes,
        "measures": _measure_changes(before, after, columns_before, columns_after),
        "quality": {"previous": quality_before, "current": quality_after,
                    "change": quality_after - quality_before},
        "coverage": _coverage(before, after),
    }
    diff["notable"] = _notable(diff)
    diff["headline"] = _headline(diff)
    return diff


def _measure_changes(
    before: dict[str, Any], after: dict[str, Any],
    columns_before: dict[str, Any], columns_after: dict[str, Any],
) -> list[dict[str, Any]]:
    shared = [
        name for name in rank_measures((after.get("roles") or {}).get("measure") or [])
        if name in columns_before and not NON_ADDITIVE.search(name)
    ]
    changes: list[dict[str, Any]] = []
    for name in shared[:8]:
        previous_total = ((columns_before[name].get("stats") or {}).get("sum"))
        current_total = ((columns_after[name].get("stats") or {}).get("sum"))
        if not isinstance(previous_total, (int, float)) or not isinstance(current_total, (int, float)):
            continue
        changes.append({
            "column": name,
            "previous_total": float(previous_total),
            "current_total": float(current_total),
            "change_pct": _pct(float(previous_total), float(current_total)),
        })
    return changes


def _coverage(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    old_range, new_range = before.get("date_range"), after.get("date_range")
    if not old_range or not new_range:
        return None
    return {
        "column": new_range["column"],
        "previous_end": old_range["end"],
        "current_end": new_range["end"],
        "extended": new_range["end"] > old_range["end"],
        "previous_start": old_range["start"],
        "current_start": new_range["start"],
    }


def _notable(diff: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    rows = diff["rows"]
    if rows["change"]:
        direction = "more" if rows["change"] > 0 else "fewer"
        pct = f" ({abs(rows['change_pct']):.1%})" if rows["change_pct"] is not None else ""
        notes.append(f"{abs(rows['change']):,} {direction} rows{pct}")
    if diff["columns_added"]:
        notes.append(f"new column(s): {', '.join(diff['columns_added'][:4])}")
    if diff["columns_removed"]:
        notes.append(f"removed column(s): {', '.join(diff['columns_removed'][:4])}")
    for change in diff["type_changes"][:3]:
        notes.append(f"{change['column']} changed type {change['previous']} → {change['current']}")
    for measure in diff["measures"]:
        if measure["change_pct"] is not None and abs(measure["change_pct"]) >= MATERIAL_MEASURE_CHANGE:
            notes.append(f"total {measure['column']} {_signed(measure['change_pct'])}")
    quality = diff["quality"]
    if abs(quality["change"]) >= 5:
        notes.append(f"data quality {quality['previous']} → {quality['current']}/100")
    coverage = diff["coverage"]
    if coverage and coverage["extended"]:
        notes.append(f"data now runs to {coverage['current_end'][:10]}")
    return notes


def _headline(diff: dict[str, Any]) -> str:
    if not diff["notable"]:
        return "No material changes from the previous version."
    schema_changed = bool(diff["columns_added"] or diff["columns_removed"] or diff["type_changes"])
    prefix = "Schema and data changed" if schema_changed else "Data updated"
    return f"{prefix}: {'; '.join(diff['notable'][:3])}."


def _pct(previous: float, current: float) -> float | None:
    if not previous:
        return None
    return (current - previous) / abs(previous)


def _signed(fraction: float) -> str:
    return f"{'up' if fraction > 0 else 'down'} {abs(fraction):.1%}"
