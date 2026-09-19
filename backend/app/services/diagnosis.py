"""Root cause on breach: a monitor that only says *what* broke is half a page.

A monitor already re-runs the code that produced a KPI and compares the answer with a
threshold. The moment it breaches, the next question is always the same one — *why?* —
and Numera already answers that deterministically for any measure in the table. This
module is the wire between the two: it works out which measure the monitor is watching,
runs the driver drill-down on it, and reduces the result to the handful of facts that
fit in a Slack message.

It is best-effort by design. A monitor can watch a KPI with no obvious measure column,
a dataset with no dates, or a table with nothing to segment by. In all of those cases
the breach alert is still sent — it simply arrives without the explanation, and says so.
No model call, so a diagnosis costs nothing and cannot invent a cause.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

from app.core.formatting import compact, percent
from app.services import drivers as drivers_service

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "the", "a", "an", "of", "in", "by", "for", "and", "or", "to", "is", "are", "was", "were",
    "total", "sum", "avg", "average", "mean", "count", "number", "per", "this", "that", "what",
    "how", "much", "many", "show", "me", "my", "our", "all", "last", "month", "year", "week",
    "day", "df", "kpi", "chart", "table", "print", "px", "pd", "np", "value", "format",
}
MAX_CONTRIBUTORS = 3


def diagnose(
    monitor: dict[str, Any],
    profile: dict[str, Any],
    frame: pd.DataFrame,
    *,
    measure: str | None = None,
) -> dict[str, Any]:
    """Explain a breach from the cleaned table. Never raises."""
    options = drivers_service.driver_options(profile)
    if not options["available"]:
        return _unavailable(
            "This dataset has no date column and dimension pair to break the change down by, "
            "so the move cannot be attributed."
        )

    chosen = measure or _pick_measure(monitor, options, frame)
    if not chosen:
        return _unavailable("No numeric measure in this dataset matches what the monitor watches.")

    params = {"measure": chosen, "period": "auto"}
    try:
        result = drivers_service.explain(frame, profile, **params)
    except Exception as exc:  # noqa: BLE001 — an explanation is a bonus, never a blocker
        logger.info("Root-cause analysis for monitor %s failed: %s", monitor.get("id"), exc)
        return _unavailable(str(getattr(exc, "message", exc))[:200])

    best = next((d for d in result["dimensions"] if d["column"] == result["best_dimension"]), None)
    contributors = [
        {
            "label": c["label"],
            "change": c["change"],
            "contribution_pct": c["contribution_pct"],
            "share_change": c["share_change"],
            "status": c["status"],
        }
        for c in (best["contributors"] if best else [])
        if c["status"] != "other"
    ][:MAX_CONTRIBUTORS]

    shift = result.get("shift_share") or {}
    largest = next((t for t in shift.get("terms") or [] if t["key"] == shift.get("largest")), None)

    return {
        "status": "ok",
        "measure": chosen,
        "matched_on": _match_basis(monitor, chosen),
        "dimension": result["best_dimension"],
        "headline": result["headline"],
        "total": result["total"],
        "period": result["period"],
        "contributors": contributors,
        "largest_term": (
            None if largest is None
            else {"key": largest["key"], "label": largest["label"], "value": largest["value"],
                  "share": largest["share"], "detail": largest["detail"]}
        ),
        "summary": _summary(result, best, contributors, largest),
        "follow_up": result["follow_up"],
        # Everything the UI needs to open the full drill-down pre-aimed at this finding.
        "params": {**params, "focus": result["best_dimension"]},
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "summary": None, "contributors": []}


# ----------------------------------------------------------------------------- matching


def _pick_measure(monitor: dict[str, Any], options: dict[str, Any],
                  frame: pd.DataFrame) -> str | None:
    """Which column is this KPI about? Word overlap with what we know about the monitor.

    The KPI's own label is the strongest evidence, then the question that produced it,
    then the snapshotted code — a column referenced in the code that computed the number
    is at least in the neighbourhood of the right answer.
    """
    measures = [m["name"] for m in options["measures"] if m["name"] in frame.columns]
    if not measures:
        return None
    if len(measures) == 1:
        return measures[0]

    weighted = [
        (_tokens(monitor.get("kpi_label")), 6.0),
        (_tokens(monitor.get("title")), 4.0),
        (_tokens(monitor.get("question")), 2.0),
        (_tokens(monitor.get("code")), 1.0),
    ]
    best: tuple[float, str] | None = None
    for name in measures:
        tokens = _tokens(name)
        if not tokens:
            continue
        score = sum(weight * len(tokens & source) / len(tokens) for source, weight in weighted)
        if best is None or score > best[0]:
            best = (score, name)
    if best and best[0] > 0:
        return best[1]
    return options["defaults"]["measure"] if options["defaults"]["measure"] in measures else measures[0]


def _tokens(text: Any) -> set[str]:
    if not text:
        return set()
    return {t for t in TOKEN_RE.findall(str(text).lower()) if t not in STOPWORDS and len(t) > 2}


def _match_basis(monitor: dict[str, Any], measure: str) -> str:
    tokens = _tokens(measure)
    if tokens & _tokens(monitor.get("kpi_label")):
        return "the KPI's name"
    if tokens & _tokens(monitor.get("question")):
        return "the question behind the monitor"
    if tokens & _tokens(monitor.get("code")):
        return "the analysis code"
    return "the dataset's primary measure"


# ----------------------------------------------------------------------------- summary


def _summary(result: dict[str, Any], best: dict[str, Any] | None,
             contributors: list[dict[str, Any]], largest: dict[str, Any] | None) -> str:
    """Two sentences that fit in a Slack message and still name a cause."""
    if not best or not contributors:
        return result["headline"]
    top = contributors[0]
    parts = [
        f"{best['column']} explains it best: {top['label']} moved {compact(top['change'])}"
        + (f" ({percent(top['contribution_pct'])} of the change)" if top["contribution_pct"] is not None else "")
        + "."
    ]
    runner = next((c for c in contributors[1:] if (c["change"] > 0) == (top["change"] > 0)), None)
    if runner:
        parts.append(f"{runner['label']} moved the same way ({compact(runner['change'])}).")
    if largest:
        parts.append(
            f"Most of the move is {largest['label'].lower()} — {largest['detail']}."
        )
    return " ".join(parts)


def alert_facts(root_cause: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The root cause as alert fields, so the breach message names a cause."""
    if not root_cause or root_cause.get("status") != "ok":
        return []
    facts: list[tuple[str, str]] = [("Most likely driver", root_cause["summary"])]
    top = (root_cause.get("contributors") or [None])[0]
    if top:
        facts.append((
            f"Biggest mover ({root_cause['dimension']})",
            f"{top['label']} · {compact(top['change'])}"
            + (f" · {percent(top['contribution_pct'])} of the change"
               if top["contribution_pct"] is not None else ""),
        ))
    largest = root_cause.get("largest_term")
    if largest:
        facts.append(("Volume / mix / rate", f"{largest['label']} — {compact(largest['value'])}"))
    return facts
