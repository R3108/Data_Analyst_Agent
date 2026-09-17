"""Export an analysis session as a runnable Jupyter notebook.

The notebook is self-contained: it defines the same `kpi` / `table` / `chart` /
`forecast` helpers the sandbox provides, so every code cell the agent wrote runs
unchanged against the cleaned data. That makes the AI's work auditable and
extendable in the user's own environment — no Numera required.

Written as plain ipynb v4 JSON so there is no nbformat dependency.
"""

from __future__ import annotations

import json
from typing import Any

SHIM = '''\
# Numera analysis helpers — the same API the sandbox exposes to generated code.
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from IPython.display import display, Markdown


def kpi(label, value, format="auto", delta=None, delta_label=None,
        higher_is_better=True, description=None):
    """Print a KPI the way the report shows it."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if format == "percent":
            shown = f"{value * 100:,.1f}%"
        elif format == "currency":
            shown = f"${value:,.2f}"
        elif format == "integer":
            shown = f"{value:,.0f}"
        else:
            shown = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
    else:
        shown = str(value)
    suffix = ""
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        suffix = f"  ({delta * 100:+.1f}% {delta_label or ''})".rstrip()
        if not suffix.endswith(")"):
            suffix += ")"
    print(f"KPI | {label}: {shown}{suffix}")


def table(data, title=None, max_rows=50):
    """Display a DataFrame or Series as the report's table output."""
    frame = data.to_frame() if isinstance(data, pd.Series) else pd.DataFrame(data)
    if title:
        display(Markdown(f"**{title}**"))
    display(frame.head(max_rows))


def chart(fig, title=None, caption=None):
    """Render a plotly figure inline."""
    if title:
        fig.update_layout(title=title)
    fig.show()
    if caption:
        display(Markdown(f"_{caption}_"))


def forecast(series, periods=6, season_length=None):
    """Additive trend + seasonality projection with an approximate 95% interval.

    Mirrors the sandbox helper: returns period, actual, fitted, forecast, lower, upper.
    """
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna().astype("float64")
    if len(s) < 6:
        raise ValueError(f"forecast() needs at least 6 observations, got {len(s)}")
    periods = int(max(1, min(int(periods), 60)))
    freq = None
    if isinstance(s.index, pd.DatetimeIndex):
        s = s.sort_index()
        freq = s.index.freqstr or pd.infer_freq(s.index)
        if freq is None:
            raise ValueError("forecast() needs evenly spaced dates; resample first.")
    if season_length is None:
        code = str(freq or "").upper().lstrip("0123456789")
        season_length = {"M": 12, "MS": 12, "ME": 12, "Q": 4, "QS": 4, "QE": 4,
                         "W": 52, "D": 7, "B": 7, "H": 24}.get(code[:2].rstrip("-") or code[:1])
    y = s.to_numpy()
    n = y.size
    t = np.arange(n, dtype=float)
    pattern = None
    if season_length and season_length >= 2 and n >= 2 * season_length:
        smooth = pd.Series(y).rolling(season_length, center=True, min_periods=season_length).mean().to_numpy()
        detrended = y - smooth
        pattern = np.array([
            np.nanmean(detrended[i::season_length]) if np.isfinite(detrended[i::season_length]).any() else 0.0
            for i in range(season_length)
        ])
        pattern = np.nan_to_num(pattern - np.nanmean(pattern))
    seasonal = pattern[np.arange(n) % season_length] if pattern is not None else np.zeros(n)
    slope, intercept = np.polyfit(t, y - seasonal, 1)
    fitted = intercept + slope * t + seasonal
    dof = max(n - 2 - ((season_length - 1) if pattern is not None else 0), 1)
    sigma = float(np.sqrt(np.sum((y - fitted) ** 2) / dof))
    steps = np.arange(1, periods + 1)
    future_t = (n - 1 + steps).astype(float)
    future_seasonal = pattern[(n - 1 + steps) % season_length] if pattern is not None else 0.0
    point = intercept + slope * future_t + future_seasonal
    leverage = 1 + 1 / n + (future_t - t.mean()) ** 2 / np.sum((t - t.mean()) ** 2)
    half = 1.96 * sigma * np.sqrt(leverage)
    lower, upper = point - half, point + half
    if (y >= 0).all():
        point, lower = np.maximum(point, 0), np.maximum(lower, 0)
    index = (list(pd.date_range(s.index[-1], periods=periods + 1, freq=freq)[1:])
             if freq is not None else list(range(n, n + periods)))
    history = pd.DataFrame({"period": list(s.index), "actual": y, "fitted": fitted,
                            "forecast": np.nan, "lower": np.nan, "upper": np.nan})
    future = pd.DataFrame({"period": index, "actual": np.nan, "fitted": np.nan,
                           "forecast": point, "lower": lower, "upper": upper})
    return pd.concat([history, future], ignore_index=True)


def chart_forecast(frame, title="Forecast", caption=None, y_label="Value", x_label="Period"):
    """Actuals, dashed forecast and interval band."""
    history = frame[frame["actual"].notna()]
    future = frame[frame["forecast"].notna()]
    anchor_x, anchor_y = history["period"].iloc[-1], float(history["actual"].iloc[-1])
    xs = [anchor_x, *future["period"].tolist()]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs + xs[::-1],
                             y=[anchor_y, *future["upper"]] + [anchor_y, *future["lower"]][::-1],
                             fill="toself", fillcolor="rgba(42,120,214,0.14)",
                             line=dict(width=0), hoverinfo="skip", name="95% interval"))
    fig.add_trace(go.Scatter(x=history["period"], y=history["actual"], mode="lines", name="Actual"))
    fig.add_trace(go.Scatter(x=xs, y=[anchor_y, *future["forecast"]], mode="lines+markers",
                             name="Forecast", line=dict(dash="dash")))
    fig.update_layout(xaxis_title=x_label, yaxis_title=y_label, hovermode="x unified")
    chart(fig, title=title, caption=caption)
'''


def build_notebook(
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    dataset: dict[str, Any] | None = None,
    data_filename: str = "clean.parquet",
) -> dict[str, Any]:
    """Assemble an ipynb v4 document for one analysis session."""
    name = (dataset or {}).get("name") or session.get("dataset_name") or "the dataset"
    cells: list[dict[str, Any]] = [
        _markdown([
            f"# {session['title']}\n",
            f"Reproducible export of a Numera analysis of **{name}**.\n",
            "Every code cell below is the exact program the agent wrote and executed in the "
            "sandbox. Run the cells in order to reproduce every figure in the report.\n",
        ]),
        _markdown(["## Setup\n", "Run this cell first — it defines the analysis helpers "
                   "(`kpi`, `table`, `chart`, `forecast`) that the generated code calls.\n"]),
        _code(SHIM),
        *_data_cells(dataset, data_filename),
    ]

    if dataset:
        cells += _cleaning_cells(dataset)

    turn = 0
    for index, message in enumerate(messages):
        if message["role"] == "user":
            turn += 1
            cells.append(_markdown([f"## {turn}. {message['content']}\n"]))
            continue
        payload = message.get("payload") or {}
        cells += _answer_cells(message, payload)

    cells.append(_markdown([
        "---\n",
        "_Generated by Numera. Figures in the write-up were verified against the computed "
        "output before export._\n",
    ]))

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.12"},
            "numera": {
                "session_id": session.get("id"),
                "dataset": name,
                "exported_messages": len(messages),
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def notebook_bytes(notebook: dict[str, Any]) -> bytes:
    return json.dumps(notebook, indent=1, ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------------------------ cell builders


def _data_cells(dataset: dict[str, Any] | None, data_filename: str) -> list[dict[str, Any]]:
    original = (dataset or {}).get("original_filename") or "your-source-file.csv"
    body = f'''\
# Numera's cleaned table, exported alongside this notebook.
# Download it from the dataset panel ("Download cleaned data"), or re-read the raw
# source and re-apply the cleaning steps listed below.
from pathlib import Path

DATA_PATH = Path("{data_filename}")
if DATA_PATH.exists():
    df = pd.read_parquet(DATA_PATH)
else:
    raise FileNotFoundError(
        "Place the exported cleaned data next to this notebook, or load the raw file "
        "{original} and apply the cleaning steps described above."
    )

print(f"{{len(df):,}} rows x {{df.shape[1]}} columns")
df.head()
'''
    return [_markdown(["## Data\n"]), _code(body)]


def _cleaning_cells(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    cleaning = dataset.get("cleaning") or {}
    actions = cleaning.get("actions") or []
    if not actions:
        return []
    lines = [
        "## Cleaning applied before analysis\n",
        f"Deterministic, rule-based steps recorded at ingest — data quality score "
        f"**{cleaning.get('quality_score', 'n/a')}/100**. Missing values were never imputed.\n",
    ]
    for action in actions:
        where = f"`{action['column']}` — " if action.get("column") else ""
        lines.append(f"- {where}{action['detail']}\n")
    return [_markdown(lines)]


def _answer_cells(message: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    report = payload.get("report") or {}
    approach = payload.get("approach")

    if approach:
        cells.append(_markdown([f"**Approach.** {approach}\n"]))
    if payload.get("code"):
        cells.append(_code(payload["code"].rstrip() + "\n"))

    narrative: list[str] = []
    if report.get("headline"):
        narrative.append(f"**{report['headline']}**\n")
    if report.get("answer_markdown"):
        narrative.append("\n" + report["answer_markdown"] + "\n")
    elif message.get("content"):
        narrative.append("\n" + message["content"] + "\n")

    if report.get("insights"):
        narrative.append("\n**Insights**\n\n")
        narrative += [f"- **{i['title']}** — {i['detail']}\n" for i in report["insights"]]
    if report.get("recommendations"):
        narrative.append("\n**Recommended actions**\n\n")
        narrative += [f"{n}. {r}\n" for n, r in enumerate(report["recommendations"], 1)]
    if report.get("caveats"):
        narrative.append("\n**Caveats**\n\n")
        narrative += [f"- {c}\n" for c in report["caveats"]]

    verification = payload.get("verification")
    if verification and verification.get("score") is not None:
        narrative.append(
            f"\n> Verification: {verification['confidence']} confidence "
            f"({verification['score']}/100) across {verification.get('checks', 0)} automated checks."
            "\n"
        )
        for finding in verification.get("findings") or []:
            narrative.append(f">\n> - **{finding['title']}** ({finding['severity']}) — {finding['detail']}\n")

    if narrative:
        cells.append(_markdown(narrative))
    return cells


def _markdown(source: list[str]) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _code(source: str) -> dict[str, Any]:
    lines = source.splitlines(keepends=True)
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": lines}
