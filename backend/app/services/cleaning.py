"""Deterministic, explainable data cleaning.

Every transformation is recorded as an action so users can see (and trust)
exactly what happened to their data before any AI touched it. The pipeline
never imputes values — silently inventing numbers is worse than reporting gaps.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

NULL_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "#n/a", "missing",
    "undefined", "#value!", "#ref!", "#div/0!", "not available",
}
BOOL_TRUE = {"true", "yes", "y", "t"}
BOOL_FALSE = {"false", "no", "n", "f"}
CURRENCY_RE = r"[\$€£¥₹]|\b(?:USD|EUR|GBP|INR|JPY|CAD|AUD)\b"
NUMERIC_BODY_RE = r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$"
DATE_NAME_HINT = re.compile(
    r"(date|time|day|month|period|timestamp|created|updated|modified|_at$|_on$|dob|birth)", re.I
)
NUMERIC_THRESHOLD = 0.95


@dataclass
class CleaningAction:
    step: str
    detail: str
    column: str | None = None
    affected: int = 0


class _Recorder:
    def __init__(self) -> None:
        self.actions: list[CleaningAction] = []

    def add(self, step: str, detail: str, column: str | None = None, affected: int = 0) -> None:
        self.actions.append(CleaningAction(step, detail, column, int(affected)))


def clean_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rec = _Recorder()
    rows_before, cols_before = df.shape
    df = df.copy()

    renames = _normalize_headers(df, rec)
    df = _drop_empty(df, rec)

    conversions: dict[str, str] = {}
    invalid_cells = 0
    for column in list(df.columns):
        series = df[column]
        if not _is_textual(series):
            continue
        series = _normalize_text(series, column, rec)
        converted, kind, invalid = _infer_type(series, column, rec)
        if kind:
            conversions[column] = kind
            invalid_cells += invalid
            df[column] = converted
        else:
            series = _unify_category_variants(series, column, rec)
            df[column] = series.astype(object).where(series.notna(), np.nan)

    duplicates = int(df.duplicated().sum())
    if duplicates:
        df = df.drop_duplicates().reset_index(drop=True)
        rec.add("remove_duplicates", f"Removed {duplicates:,} exact duplicate rows", affected=duplicates)

    df = df.reset_index(drop=True)
    total_cells = max(df.shape[0] * df.shape[1], 1)
    missing_cells = int(df.isna().sum().sum())
    outliers = _outlier_summary(df)
    missing_by_column = {
        str(c): int(n) for c, n in df.isna().sum().items() if n > 0
    }

    missing_ratio = missing_cells / total_cells
    dup_ratio = duplicates / max(rows_before, 1)
    invalid_ratio = invalid_cells / total_cells
    quality = 100 * (1 - missing_ratio) * (1 - min(dup_ratio, 0.5)) * (1 - min(invalid_ratio * 2, 0.5))

    if missing_by_column:
        worst = sorted(missing_by_column.items(), key=lambda kv: -kv[1])[:3]
        rec.add(
            "missing_values",
            "Left missing values as-is (no imputation). Most affected: "
            + ", ".join(f"{c} ({n:,})" for c, n in worst),
            affected=missing_cells,
        )

    report = {
        "rows_before": int(rows_before),
        "rows_after": int(df.shape[0]),
        "columns_before": int(cols_before),
        "columns_after": int(df.shape[1]),
        "actions": [asdict(a) for a in rec.actions],
        "column_renames": renames,
        "type_conversions": conversions,
        "duplicates_removed": duplicates,
        "missing_cells": missing_cells,
        "missing_cells_pct": round(100 * missing_ratio, 2),
        "missing_by_column": missing_by_column,
        "invalid_values_coerced": int(invalid_cells),
        "outliers": outliers,
        "quality_score": int(round(max(0.0, min(100.0, quality)))),
    }
    return df, report


# --- steps -------------------------------------------------------------------------


def _normalize_headers(df: pd.DataFrame, rec: _Recorder) -> dict[str, str]:
    renames: dict[str, str] = {}
    seen: dict[str, int] = {}
    new_columns: list[str] = []
    for i, original in enumerate(df.columns):
        name = re.sub(r"\s+", " ", str(original)).strip()
        if not name or name.lower().startswith("unnamed:") or name.lower() == "nan":
            name = f"column_{i + 1}"
        base = name
        if base in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        if name != str(original):
            renames[str(original)] = name
        new_columns.append(name)
    df.columns = new_columns
    if renames:
        rec.add("normalize_headers", f"Tidied {len(renames)} column name(s) (whitespace, blanks, duplicates)",
                affected=len(renames))
    return renames


def _drop_empty(df: pd.DataFrame, rec: _Recorder) -> pd.DataFrame:
    def blank(s: pd.Series) -> pd.Series:
        if _is_textual(s):
            return s.isna() | s.astype("string").str.strip().str.lower().isin(NULL_TOKENS)
        return s.isna()

    mask = pd.DataFrame({c: blank(df[c]) for c in df.columns})
    empty_cols = [c for c in df.columns if bool(mask[c].all())]
    if empty_cols:
        df = df.drop(columns=empty_cols)
        mask = mask.drop(columns=empty_cols)
        rec.add("drop_empty_columns", f"Dropped {len(empty_cols)} completely empty column(s): "
                + ", ".join(empty_cols[:5]), affected=len(empty_cols))
    empty_rows = mask.all(axis=1)
    n_empty = int(empty_rows.sum())
    if n_empty:
        df = df.loc[~empty_rows].reset_index(drop=True)
        rec.add("drop_empty_rows", f"Dropped {n_empty:,} completely empty row(s)", affected=n_empty)
    return df


def _normalize_text(series: pd.Series, column: str, rec: _Recorder) -> pd.Series:
    s = series.astype("string")
    stripped = s.str.strip().str.replace(r"\s+", " ", regex=True)
    changed = int(((stripped != s) & s.notna()).sum())
    is_null_token = stripped.str.lower().isin(NULL_TOKENS) & stripped.notna()
    n_null_tokens = int(is_null_token.sum())
    stripped = stripped.mask(is_null_token)
    if changed:
        rec.add("trim_whitespace", f"Trimmed stray whitespace in {changed:,} value(s)", column, changed)
    if n_null_tokens:
        rec.add("standardize_missing", f"Marked {n_null_tokens:,} blank or placeholder value(s) (e.g. 'N/A') as missing",
                column, n_null_tokens)
    return stripped


def _infer_type(series: pd.Series, column: str, rec: _Recorder) -> tuple[pd.Series, str | None, int]:
    non_null = series.dropna()
    if non_null.empty:
        return series, None, 0

    lowered = non_null.str.lower()
    unique_lower = set(lowered.unique())
    if unique_lower <= (BOOL_TRUE | BOOL_FALSE) and len(unique_lower) >= 2:
        mapped = series.str.lower().map(lambda v: True if v in BOOL_TRUE else (False if v in BOOL_FALSE else np.nan))
        converted = mapped.astype("boolean") if mapped.isna().any() else mapped.astype(bool)
        rec.add("convert_boolean", "Converted yes/no style values to booleans", column, len(non_null))
        return converted, "boolean", 0

    numeric = _try_numeric(series, non_null)
    if numeric is not None:
        values, notes = numeric
        invalid = int(values.isna().sum() - series.isna().sum())
        detail = "Converted text to numbers" + (f" ({', '.join(notes)})" if notes else "")
        if invalid:
            detail += f"; {invalid:,} unparseable value(s) set to missing"
        rec.add("convert_numeric", detail, column, len(non_null))
        return values, "number", max(invalid, 0)

    dates = _try_datetime(series, non_null, column)
    if dates is not None:
        invalid = int(dates.isna().sum() - series.isna().sum())
        detail = "Parsed dates"
        if invalid:
            detail += f"; {invalid:,} unparseable value(s) set to missing"
        rec.add("convert_datetime", detail, column, len(non_null))
        return dates, "datetime", max(invalid, 0)

    return series, None, 0


def _try_numeric(series: pd.Series, non_null: pd.Series) -> tuple[pd.Series, list[str]] | None:
    # Identifier-like codes with leading zeros (zip codes, SKUs) must stay text.
    digits_only = non_null.str.fullmatch(r"\d+")
    leading_zero = non_null.str.match(r"^0\d+") & digits_only
    if leading_zero.mean() > 0.05:
        return None

    notes: list[str] = []
    s = series
    has_currency = s.str.contains(CURRENCY_RE, regex=True, na=False)
    is_pct = s.str.endswith("%", na=False)
    is_paren_negative = s.str.fullmatch(r"\(.*\)", na=False)
    has_thousands = s.str.contains(r"\d,\d{3}", regex=True, na=False)

    body = (
        s.str.replace(CURRENCY_RE, "", regex=True)
        .str.replace(r"[,\s%()]", "", regex=True)
    )
    parsable = body.str.fullmatch(NUMERIC_BODY_RE, na=False)
    if parsable[non_null.index].mean() < NUMERIC_THRESHOLD:
        return None

    values = pd.to_numeric(body.where(parsable), errors="coerce").astype("float64")
    values = values.where(~is_paren_negative, -values)
    if is_pct.any():
        values = values.where(~is_pct, values / 100)
        notes.append("percentages → fractions, e.g. 12% → 0.12")
    if has_currency.any():
        notes.append("removed currency symbols")
    if has_thousands.any():
        notes.append("removed thousands separators")
    if is_paren_negative.any():
        notes.append("(123) → -123")

    finite = values.dropna()
    if not values.isna().any() and len(finite) and np.all(np.mod(finite, 1) == 0):
        values = values.astype("int64")
    return values, notes


def _try_datetime(series: pd.Series, non_null: pd.Series, column: str) -> pd.Series | None:
    if non_null.str.fullmatch(NUMERIC_BODY_RE).mean() > 0.5:
        return None
    looks_datey = non_null.str.contains(r"\d{1,4}[-/.]\d{1,2}|\d{1,2}:\d{2}|[A-Za-z]{3,9}\s+\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}",
                                        regex=True)
    if looks_datey.mean() < 0.8:
        return None

    threshold = 0.8 if DATE_NAME_HINT.search(column) else 0.95
    sample = non_null.sample(min(len(non_null), 2000), random_state=0)
    attempts: list[dict[str, Any]] = [
        {"format": "ISO8601"},
        {"format": "mixed", "dayfirst": False},
        {"format": "mixed", "dayfirst": True},
    ]
    # Pick the strategy that parses the most values — a fast ISO parse that clears the
    # threshold must not win over a mixed-format parse that recovers every value.
    best: tuple[float, dict[str, Any]] | None = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for kwargs in attempts:
            try:
                ratio = float(pd.to_datetime(sample, errors="coerce", **kwargs).notna().mean())
            except (ValueError, TypeError, OverflowError):
                continue
            if best is None or ratio > best[0]:
                best = (ratio, kwargs)
            if ratio >= 0.999:
                break
        if best is None or best[0] < threshold:
            return None
        try:
            full = pd.to_datetime(series, errors="coerce", **best[1])
        except (ValueError, TypeError, OverflowError):
            return None
    if not pd.api.types.is_datetime64_any_dtype(full):
        return None
    if getattr(full.dt, "tz", None) is not None:
        full = full.dt.tz_convert("UTC").dt.tz_localize(None)
    return full


def _unify_category_variants(series: pd.Series, column: str, rec: _Recorder) -> pd.Series:
    non_null = series.dropna()
    if non_null.empty or non_null.nunique() > 500:
        return series
    counts = non_null.value_counts()
    keys = counts.index.to_series().str.lower()
    mapping: dict[str, str] = {}
    for _, variants in keys.groupby(keys):
        if len(variants) > 1:
            canonical = max(variants.index, key=lambda v: counts[v])
            for variant in variants.index:
                if variant != canonical:
                    mapping[variant] = canonical
    if not mapping:
        return series
    affected = int(series.isin(list(mapping)).sum())
    examples = ", ".join(f"'{k}' → '{v}'" for k, v in list(mapping.items())[:3])
    rec.add("unify_categories", f"Merged {len(mapping)} inconsistent capitalisation variant(s): {examples}",
            column, affected)
    return series.replace(mapping)


def _outlier_summary(df: pd.DataFrame) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for column in df.columns:
        s = df[column]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        values = s.dropna()
        if len(values) < 20:
            continue
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr == 0:
            continue
        low, high = q1 - 3 * iqr, q3 + 3 * iqr
        count = int(((values < low) | (values > high)).sum())
        if count:
            summary.append({"column": str(column), "count": count,
                            "lower_fence": float(low), "upper_fence": float(high)})
    return sorted(summary, key=lambda o: -o["count"])[:10]


def _is_textual(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
