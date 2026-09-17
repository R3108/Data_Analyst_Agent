"""Convert pandas / numpy values into JSON-safe Python primitives."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, Decimal):
        return to_jsonable(float(value))
    if isinstance(value, str):
        return value
    if value is pd.NaT or (not isinstance(value, (list, tuple, dict)) and _is_na(value)):
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta, dt.timedelta)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray, pd.Index)):
        return [to_jsonable(v) for v in value]
    return str(value)


def _is_na(value: Any) -> bool:
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def frame_to_records(df: pd.DataFrame, limit: int | None = None) -> dict[str, Any]:
    view = df if limit is None else df.head(limit)
    return {
        "columns": [str(c) for c in view.columns],
        "rows": [[to_jsonable(v) for v in row] for row in view.itertuples(index=False, name=None)],
        "total_rows": int(len(df)),
    }
