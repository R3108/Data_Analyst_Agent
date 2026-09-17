"""Dataset profiling: column semantics, statistics and suggested questions."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from app.core.serialization import to_jsonable

ID_NAME = re.compile(r"(^id$|_id$|^id_|\bid\b|uuid|guid|code$|number$|^sku|invoice|order_no)", re.I)
TIME_PART_NAME = re.compile(r"(year|month|quarter|week|day_of|weekday|hour)", re.I)
MONEY_NAME = re.compile(r"(revenue|sales|price|cost|amount|profit|spend|income|gmv|value|total|fee|margin)", re.I)
# Most business-relevant first: "Revenue" should headline before "Unit Price".
MEASURE_PRIORITY = [
    re.compile(r"revenue|sales|gmv|income|turnover|bookings", re.I),
    re.compile(r"profit|margin|earnings", re.I),
    re.compile(r"amount|total|value|spend|units|quantity|qty|orders", re.I),
    re.compile(r"cost|fee|expense", re.I),
]
NON_ADDITIVE = re.compile(r"(price|rate|ratio|pct|percent|discount|margin|score|age|avg|average|mean|lat|lon)", re.I)


def rank_measures(measures: list[str]) -> list[str]:
    def priority(name: str) -> int:
        return next((i for i, pattern in enumerate(MEASURE_PRIORITY) if pattern.search(name)), len(MEASURE_PRIORITY))

    return sorted(measures, key=lambda m: (priority(m), measures.index(m)))


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    n_rows = int(len(df))
    columns = [_profile_column(df[c], n_rows) for c in df.columns]

    by_role: dict[str, list[str]] = {}
    for col in columns:
        by_role.setdefault(col["role"], []).append(col["name"])

    date_range = None
    if by_role.get("datetime"):
        primary = df[by_role["datetime"][0]].dropna()
        if not primary.empty:
            date_range = {"column": by_role["datetime"][0],
                          "start": primary.min().isoformat(), "end": primary.max().isoformat()}

    sample = df.head(5).copy()
    return {
        "n_rows": n_rows,
        "n_columns": int(df.shape[1]),
        "memory_mb": round(float(df.memory_usage(deep=True).sum()) / 1_048_576, 2),
        "columns": columns,
        "roles": by_role,
        "date_range": date_range,
        "highlights": _highlights(df, by_role),
        "sample_rows": {
            "columns": [str(c) for c in sample.columns],
            "rows": [[to_jsonable(v) for v in r] for r in sample.itertuples(index=False, name=None)],
        },
        "suggested_questions": suggest_questions(by_role, columns),
    }


def _profile_column(s: pd.Series, n_rows: int) -> dict[str, Any]:
    name = str(s.name)
    non_null = s.dropna()
    missing = int(s.isna().sum())
    try:
        unique = int(non_null.nunique())
    except TypeError:
        unique = int(non_null.astype(str).nunique())

    info: dict[str, Any] = {
        "name": name,
        "missing": missing,
        "missing_pct": round(100 * missing / n_rows, 2) if n_rows else 0.0,
        "unique": unique,
    }

    if pd.api.types.is_bool_dtype(s):
        info.update(dtype="boolean", role="boolean",
                    top_values=_top_values(non_null))
    elif pd.api.types.is_datetime64_any_dtype(s):
        info.update(dtype="datetime", role="datetime")
        if not non_null.empty:
            start, end = non_null.min(), non_null.max()
            info["stats"] = {"min": start.isoformat(), "max": end.isoformat(),
                             "span_days": int((end - start).days),
                             "granularity": _granularity(non_null)}
    elif pd.api.types.is_numeric_dtype(s):
        is_int = pd.api.types.is_integer_dtype(s) or (
            not non_null.empty and bool(np.all(np.mod(non_null, 1) == 0))
        )
        info["dtype"] = "integer" if is_int else "decimal"
        if ID_NAME.search(name) and unique >= 0.95 * max(len(non_null), 1):
            info["role"] = "identifier"
        elif TIME_PART_NAME.search(name) and unique <= 60:
            info["role"] = "dimension"
        elif is_int and unique <= 12 and not MONEY_NAME.search(name) and n_rows > 50:
            info["role"] = "dimension"
        else:
            info["role"] = "measure"
        if not non_null.empty:
            info["stats"] = {
                "min": to_jsonable(non_null.min()), "max": to_jsonable(non_null.max()),
                "mean": to_jsonable(non_null.mean()), "median": to_jsonable(non_null.median()),
                "std": to_jsonable(non_null.std()) if len(non_null) > 1 else None,
                "sum": to_jsonable(non_null.sum()),
            }
        if info["role"] == "dimension":
            info["top_values"] = _top_values(non_null)
    else:
        text = non_null.astype(str)
        avg_len = float(text.str.len().mean()) if not text.empty else 0.0
        unique_ratio = unique / max(len(non_null), 1)
        info["dtype"] = "text"
        if ID_NAME.search(name) and unique_ratio > 0.9:
            info["role"] = "identifier"
        elif unique <= max(50, int(0.05 * n_rows)) and avg_len <= 60:
            info["role"] = "dimension"
        elif unique_ratio > 0.9 and avg_len < 40 and n_rows > 20:
            info["role"] = "identifier"
        else:
            info["role"] = "text"
        info["avg_length"] = round(avg_len, 1)
        info["top_values"] = _top_values(text)

    info["sample_values"] = [to_jsonable(v) for v in non_null.head(3).tolist()]
    return info


def _top_values(s: pd.Series, k: int = 5) -> list[dict[str, Any]]:
    counts = s.value_counts().head(k)
    return [{"value": to_jsonable(v), "count": int(c)} for v, c in counts.items()]


def _granularity(s: pd.Series) -> str:
    unique = pd.Series(s.unique()).sort_values()
    if len(unique) < 3:
        return "unknown"
    if (unique.dt.hour != 0).any() or (unique.dt.minute != 0).any():
        return "sub-daily"
    median_gap = unique.diff().dropna().median().days
    if median_gap <= 1:
        return "daily"
    if median_gap <= 8:
        return "weekly"
    if median_gap <= 32:
        return "monthly"
    if median_gap <= 95:
        return "quarterly"
    return "yearly"


def _highlights(df: pd.DataFrame, roles: dict[str, list[str]]) -> list[dict[str, Any]]:
    highlights: list[dict[str, Any]] = [
        {"label": "Rows", "value": int(len(df)), "format": "integer"},
        {"label": "Columns", "value": int(df.shape[1]), "format": "integer"},
    ]
    additive = [m for m in rank_measures(roles.get("measure", [])) if not NON_ADDITIVE.search(m)]
    for column in additive[:2]:
        highlights.append({"label": f"Total {column}", "value": to_jsonable(df[column].sum()),
                           "format": "number"})
    if roles.get("datetime"):
        s = df[roles["datetime"][0]].dropna()
        if not s.empty:
            highlights.append({"label": "Date span", "value": f"{s.min():%b %Y} – {s.max():%b %Y}",
                               "format": "text"})
    return highlights


def suggest_questions(roles: dict[str, list[str]], columns: list[dict[str, Any]]) -> list[str]:
    measures = rank_measures(roles.get("measure", []))
    dims = roles.get("dimension", [])
    dates = roles.get("datetime", [])
    m = measures[0] if measures else None
    questions: list[str] = []

    if m and dates:
        questions.append(f"How has {m} trended over time, and are there seasonal patterns?")
    if m and dims:
        questions.append(f"Which {dims[0]} drives the most {m}, and how concentrated is it?")
    if m and len(dims) > 1:
        questions.append(f"Break down {m} by {dims[0]} and {dims[1]} — where are the biggest gaps?")
    if len(measures) >= 2:
        questions.append(f"What is the relationship between {measures[0]} and {measures[1]}?")
    if m:
        questions.append(f"Give me an executive KPI summary of {m} with the key drivers.")
    if any(c["missing_pct"] > 0 for c in columns):
        questions.append("Where are the data quality issues and how might they bias the analysis?")
    if not questions:
        questions.append("Summarise this dataset and highlight anything unusual.")
    return questions[:6]


def dataset_context(name: str, profile: dict[str, Any], cleaning: dict[str, Any]) -> str:
    """Compact schema card given to the LLM. Stable per dataset so it caches well."""
    lines = [
        f"DATASET: {name}",
        f"Shape: {profile['n_rows']:,} rows × {profile['n_columns']} columns "
        f"(data quality score {cleaning.get('quality_score', 'n/a')}/100)",
        "",
        "COLUMNS (exact names — use them verbatim):",
    ]
    for col in profile["columns"]:
        parts = [f"- `{col['name']}`", col["dtype"], f"role={col['role']}"]
        if col["missing"]:
            parts.append(f"missing {col['missing_pct']}%")
        stats = col.get("stats") or {}
        if col["dtype"] == "datetime" and stats:
            parts.append(f"range {stats['min'][:10]} → {stats['max'][:10]} ({stats.get('granularity')})")
        elif stats:
            parts.append(
                "min {min}, median {median}, mean {mean}, max {max}".format(
                    **{k: _fmt(stats.get(k)) for k in ("min", "median", "mean", "max")}
                )
            )
        if col.get("top_values") and col["role"] in ("dimension", "boolean", "text"):
            top = ", ".join(f"{t['value']} ({t['count']:,})" for t in col["top_values"][:6])
            parts.append(f"{col['unique']:,} unique; top: {top}")
        elif col["role"] == "identifier":
            parts.append(f"{col['unique']:,} unique")
        lines.append(" · ".join(parts))

    actions = [a for a in cleaning.get("actions", []) if a["step"] != "missing_values"]
    if actions:
        lines += ["", "CLEANING ALREADY APPLIED (df is the cleaned data):"]
        for action in actions[:25]:
            where = f"[{action['column']}] " if action.get("column") else ""
            lines.append(f"- {where}{action['detail']}")

    signals = profile.get("signals") or []
    if signals:
        lines += ["", "AUTO-DETECTED SIGNALS (deterministic scan, verify before relying on them):"]
        lines += [f"- {s['title']}: {s['detail']}" for s in signals]

    sample = profile.get("sample_rows") or {}
    if sample.get("rows"):
        lines += ["", "SAMPLE ROWS:", " | ".join(sample["columns"])]
        for row in sample["rows"]:
            lines.append(" | ".join("" if v is None else str(v)[:40] for v in row))
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:.4g}"
    return str(value)
