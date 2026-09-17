"""Data contracts: what this table must look like for the analysis to keep meaning.

Cleaning explains what happened to *this* upload. A contract is the stronger promise:
the column is still there, it is still a date, fewer than 5% of its values are empty,
`status` still only contains the four values the analysis branches on, and the export
is not three weeks stale. Next month's file either honours that or it does not.

Expectations are suggested from the first upload's profile, edited by the user, and
inherited by every later version of the dataset — so a re-upload is *checked*, not
merely diffed. Evaluation is pure pandas: deterministic, free, and impossible for a
model to talk its way around.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.core.serialization import to_jsonable

logger = logging.getLogger(__name__)

KINDS = ("schema", "not_null", "unique", "range", "allowed_values", "row_count", "freshness")
SEVERITIES = ("fail", "warn")
MAX_EXPECTATIONS = 120
MAX_ALLOWED_VALUES = 200
MAX_REPORTED = 6

EMPTY: dict[str, Any] = {"expectations": []}

# Suggestion thresholds — deliberately loose, so a sensible file never trips the gate.
SUGGEST_MAX_CATEGORIES = 25
SUGGEST_NULL_SLACK_PCT = 5.0
SUGGEST_ROW_FLOOR = 0.5


# --------------------------------------------------------------------------- normalisation


def normalize(payload: Any) -> dict[str, Any]:
    """Coerce arbitrary client input into the stored contract shape."""
    raw = payload.get("expectations") if isinstance(payload, dict) else payload
    expectations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        expectation = _expectation(item)
        if expectation is None:
            continue
        key = expectation["id"]
        if key in seen:
            continue
        seen.add(key)
        expectations.append(expectation)
        if len(expectations) >= MAX_EXPECTATIONS:
            break
    return {"expectations": expectations}


def is_empty(contract: dict[str, Any] | None) -> bool:
    return not (contract or {}).get("expectations")


def _expectation(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return None
    column = _text(raw.get("column"), 200) or None
    if kind not in ("row_count",) and not column:
        return None
    params = _params(kind, raw.get("params") if isinstance(raw.get("params"), dict) else {})
    if params is None:
        return None
    severity = str(raw.get("severity") or "").lower()
    expectation = {
        "id": _text(raw.get("id"), 80) or _identity(kind, column),
        "kind": kind,
        "column": column,
        "severity": severity if severity in SEVERITIES else _default_severity(kind),
        "params": params,
        "enabled": raw.get("enabled") is not False,
    }
    expectation["description"] = _text(raw.get("description"), 300) or describe(expectation)
    return expectation


def _params(kind: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "schema":
        return {"dtype": _text(raw.get("dtype"), 20) or None}
    if kind == "not_null":
        return {"max_missing_pct": _number(raw.get("max_missing_pct"), default=0.0, low=0.0, high=100.0)}
    if kind == "unique":
        return {}
    if kind == "range":
        low, high = _optional_number(raw.get("min")), _optional_number(raw.get("max"))
        if low is None and high is None:
            return None
        if low is not None and high is not None and low > high:
            return None
        return {"min": low, "max": high}
    if kind == "allowed_values":
        values = [
            to_jsonable(v) for v in (raw.get("values") if isinstance(raw.get("values"), list) else [])
        ][:MAX_ALLOWED_VALUES]
        return {"values": values} if values else None
    if kind == "row_count":
        low, high = _optional_number(raw.get("min")), _optional_number(raw.get("max"))
        if low is None and high is None:
            return None
        return {"min": None if low is None else int(low), "max": None if high is None else int(high)}
    if kind == "freshness":
        return {"max_age_days": _number(raw.get("max_age_days"), default=30.0, low=0.0, high=3650.0)}
    return None


def _default_severity(kind: str) -> str:
    # A missing column breaks every downstream analysis; a drifting distribution is a warning.
    return "fail" if kind in ("schema", "unique", "row_count") else "warn"


def _identity(kind: str, column: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (column or "table").lower()).strip("_") or "table"
    return f"{kind}:{slug}"


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _number(value: Any, *, default: float, low: float, high: float) -> float:
    number = _optional_number(value)
    return default if number is None else max(low, min(high, number))


def describe(expectation: dict[str, Any]) -> str:
    kind, column, params = expectation["kind"], expectation["column"], expectation["params"]
    if kind == "schema":
        dtype = params.get("dtype")
        return f"`{column}` is present" + (f" and still {dtype}" if dtype else "")
    if kind == "not_null":
        limit = params["max_missing_pct"]
        return f"`{column}` is never empty" if limit == 0 else f"`{column}` is at most {limit:g}% empty"
    if kind == "unique":
        return f"`{column}` has no duplicate values"
    if kind == "range":
        low, high = params.get("min"), params.get("max")
        if low is not None and high is not None:
            return f"`{column}` stays between {low:g} and {high:g}"
        if low is not None:
            return f"`{column}` is never below {low:g}"
        return f"`{column}` is never above {high:g}"
    if kind == "allowed_values":
        values = params["values"]
        shown = ", ".join(str(v) for v in values[:4])
        more = f" (+{len(values) - 4} more)" if len(values) > 4 else ""
        return f"`{column}` only contains {shown}{more}"
    if kind == "row_count":
        low, high = params.get("min"), params.get("max")
        if low is not None and high is not None:
            return f"the table has between {low:,} and {high:,} rows"
        if low is not None:
            return f"the table has at least {low:,} rows"
        return f"the table has at most {high:,} rows"
    if kind == "freshness":
        return f"`{column}` reaches within {params['max_age_days']:g} days of today"
    return kind


# --------------------------------------------------------------------------- suggestion


def suggest(profile: dict[str, Any]) -> dict[str, Any]:
    """A starting contract inferred from a profile the user has already accepted."""
    expectations: list[dict[str, Any]] = []
    columns = profile.get("columns") or []
    n_rows = int(profile.get("n_rows") or 0)

    if n_rows:
        expectations.append(_make("row_count", None, {"min": max(1, int(n_rows * SUGGEST_ROW_FLOOR))}))

    for column in columns:
        name, role, dtype = column["name"], column["role"], column["dtype"]
        missing_pct = float(column.get("missing_pct") or 0.0)
        expectations.append(_make("schema", name, {"dtype": dtype}))

        if missing_pct <= SUGGEST_NULL_SLACK_PCT:
            limit = 0.0 if missing_pct == 0 else math.ceil(missing_pct + SUGGEST_NULL_SLACK_PCT)
            expectations.append(_make("not_null", name, {"max_missing_pct": limit}))

        if role == "identifier" and column["unique"] >= n_rows and n_rows:
            expectations.append(_make("unique", name, {}))

        stats = column.get("stats") or {}
        if role == "measure" and isinstance(stats.get("min"), (int, float)) and stats["min"] >= 0:
            expectations.append(_make("range", name, {"min": 0, "max": None}))

        if role in ("dimension", "boolean") and 0 < column["unique"] <= SUGGEST_MAX_CATEGORIES:
            values = [v["value"] for v in column.get("top_values") or []]
            if values and len(values) >= column["unique"]:
                expectations.append(_make("allowed_values", name, {"values": values}))

    date_range = profile.get("date_range")
    if date_range:
        age = _age_days(date_range.get("end"))
        if age is not None:
            expectations.append(
                _make("freshness", date_range["column"],
                      {"max_age_days": max(7, math.ceil(age * 2) if age > 0 else 7)})
            )
    return normalize({"expectations": expectations})


def _make(kind: str, column: str | None, params: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "column": column, "params": params, "id": _identity(kind, column)}


def _age_days(iso: Any) -> float | None:
    try:
        stamp = pd.Timestamp(iso)
    except (ValueError, TypeError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(None)
    return max(0.0, (pd.Timestamp.utcnow().tz_localize(None) - stamp).total_seconds() / 86400)


# --------------------------------------------------------------------------- evaluation


def evaluate(contract: dict[str, Any] | None, df: pd.DataFrame, profile: dict[str, Any]) -> dict[str, Any]:
    """Check every enabled expectation against the cleaned table. Never raises."""
    expectations = [e for e in (contract or {}).get("expectations") or [] if e.get("enabled", True)]
    checked_at = datetime.now(timezone.utc).isoformat()
    if not expectations:
        return {"status": "empty", "checked_at": checked_at, "counts": {"pass": 0, "warn": 0, "fail": 0},
                "score": None, "results": [], "headline": "No data contract defined for this dataset.",
                "failures": []}

    results: list[dict[str, Any]] = []
    for expectation in expectations:
        try:
            status, detail, observed = _check(expectation, df, profile)
        except Exception:  # noqa: BLE001 — a broken expectation must not block an upload
            logger.warning("Contract check %s failed to evaluate", expectation.get("id"), exc_info=True)
            status, detail, observed = "error", "This expectation could not be evaluated.", None
        results.append({
            "id": expectation["id"],
            "kind": expectation["kind"],
            "column": expectation["column"],
            "severity": expectation["severity"],
            "description": expectation.get("description") or describe(expectation),
            "status": status,
            "detail": detail,
            "observed": observed,
        })

    counts = {
        "pass": sum(1 for r in results if r["status"] == "pass"),
        "warn": sum(1 for r in results if r["status"] in ("warn", "error")),
        "fail": sum(1 for r in results if r["status"] == "fail"),
    }
    total = len(results) or 1
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "pass"
    failures = [r for r in results if r["status"] in ("fail", "warn", "error")]
    return {
        "status": status,
        "checked_at": checked_at,
        "counts": counts,
        "score": round(100 * (counts["pass"] + 0.5 * counts["warn"]) / total),
        "results": results,
        "failures": failures[:MAX_REPORTED],
        "headline": _headline(status, counts, failures),
    }


def _headline(status: str, counts: dict[str, int], failures: list[dict[str, Any]]) -> str:
    total = counts["pass"] + counts["warn"] + counts["fail"]
    if status == "pass":
        return f"All {total} contract checks passed."
    lead = failures[0]["description"] if failures else ""
    broken = counts["fail"] + counts["warn"]
    suffix = f" — starting with: {lead}" if lead else ""
    if status == "fail":
        return f"{counts['fail']} of {total} contract checks failed{suffix}"
    return f"{broken} of {total} contract checks raised a warning{suffix}"


def _check(
    expectation: dict[str, Any], df: pd.DataFrame, profile: dict[str, Any]
) -> tuple[str, str, Any]:
    kind, column, params = expectation["kind"], expectation["column"], expectation["params"]
    severity = expectation["severity"]

    if kind == "row_count":
        rows = int(len(df))
        low, high = params.get("min"), params.get("max")
        if low is not None and rows < low:
            return severity, f"{rows:,} rows — below the agreed floor of {low:,}.", rows
        if high is not None and rows > high:
            return severity, f"{rows:,} rows — above the agreed ceiling of {high:,}.", rows
        return "pass", f"{rows:,} rows.", rows

    if column not in df.columns:
        return ("fail" if kind == "schema" else severity), "The column is missing from this version.", None

    series = df[column]
    if kind == "schema":
        expected = params.get("dtype")
        actual = _dtype_name(series)
        if expected and expected != actual:
            return severity, f"Type changed from {expected} to {actual}.", actual
        return "pass", f"Present as {actual}.", actual

    if kind == "not_null":
        missing = int(series.isna().sum())
        pct = 100 * missing / len(df) if len(df) else 0.0
        limit = params["max_missing_pct"]
        if pct > limit + 1e-9:
            return severity, f"{missing:,} empty values ({pct:.2f}%) exceed the {limit:g}% limit.", round(pct, 4)
        return "pass", f"{pct:.2f}% empty.", round(pct, 4)

    if kind == "unique":
        non_null = series.dropna()
        duplicates = int(len(non_null) - non_null.astype(str).nunique())
        if duplicates > 0:
            return severity, f"{duplicates:,} duplicate value(s) found.", duplicates
        return "pass", "Every value is distinct.", 0

    if kind == "range":
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if numeric.empty:
            return severity, "No numeric values to check.", None
        low, high = params.get("min"), params.get("max")
        breaches = 0
        if low is not None:
            breaches += int((numeric < low).sum())
        if high is not None:
            breaches += int((numeric > high).sum())
        observed = {"min": float(numeric.min()), "max": float(numeric.max())}
        if breaches:
            return severity, (
                f"{breaches:,} value(s) outside the agreed range "
                f"(observed {observed['min']:,.4g} to {observed['max']:,.4g})."
            ), observed
        return "pass", f"Observed {observed['min']:,.4g} to {observed['max']:,.4g}.", observed

    if kind == "allowed_values":
        allowed = {str(v) for v in params["values"]}
        present = series.dropna().astype(str)
        unexpected = sorted(set(present.unique()) - allowed)
        if unexpected:
            rows = int(present.isin(unexpected).sum())
            shown = ", ".join(unexpected[:4]) + (f" (+{len(unexpected) - 4} more)" if len(unexpected) > 4 else "")
            return severity, f"{rows:,} row(s) use unexpected value(s): {shown}.", unexpected[:20]
        return "pass", f"All values are within the agreed {len(allowed)}.", []

    if kind == "freshness":
        dates = pd.to_datetime(series, errors="coerce").dropna()
        if dates.empty:
            return severity, "No usable dates in this column.", None
        latest = dates.max()
        if getattr(latest, "tzinfo", None) is not None:
            latest = latest.tz_convert(None)
        age = max(0.0, (pd.Timestamp.utcnow().tz_localize(None) - latest).total_seconds() / 86400)
        limit = params["max_age_days"]
        if age > limit:
            return severity, (
                f"The newest record is {age:.1f} days old ({latest:%Y-%m-%d}), "
                f"past the {limit:g}-day limit."
            ), round(age, 2)
        return "pass", f"Newest record is {age:.1f} days old ({latest:%Y-%m-%d}).", round(age, 2)

    return "error", f"Unknown expectation kind '{kind}'.", None


def _dtype_name(series: pd.Series) -> str:
    """The same label `profiling` assigns, so a contract never fights the profile."""
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_numeric_dtype(series):
        non_null = series.dropna()
        whole = not non_null.empty and bool(np.all(np.mod(non_null, 1) == 0))
        return "integer" if whole else "decimal"
    return "text"


# --------------------------------------------------------------------------- rendering


def contract_markdown(contract: dict[str, Any] | None, result: dict[str, Any] | None,
                      dataset_name: str = "") -> str:
    lines = [f"# Data contract{f' — {dataset_name}' if dataset_name else ''}", ""]
    if is_empty(contract):
        lines.append("No expectations are defined for this dataset yet.")
        return "\n".join(lines)
    if result:
        lines += [f"**{result['headline']}**",
                  "",
                  f"_Checked {result['checked_at'][:19].replace('T', ' ')} UTC · "
                  f"{result['counts']['pass']} passed · {result['counts']['warn']} warned · "
                  f"{result['counts']['fail']} failed_",
                  ""]
    lines += ["| Status | Expectation | Observed |", "|---|---|---|"]
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌", "error": "⚠️"}
    for row in (result or {}).get("results") or []:
        lines.append(f"| {icons.get(row['status'], '•')} | {row['description']} | {row['detail']} |")
    return "\n".join(lines)
