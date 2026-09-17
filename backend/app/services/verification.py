"""Deterministic verification of a finished analysis.

Every number an LLM writes is a claim. This module checks those claims against
what the sandbox actually computed, and audits the computation itself for the
mistakes that quietly produce confident nonsense:

  * figures in the narrative that appear nowhere in the executed output
  * percentages already multiplied by 100 (23.4 instead of 0.234)
  * share columns that do not add up
  * missing data in the columns used, never mentioned to the reader
  * means reported over columns with flagged outliers
  * business-metric definitions the code appears to have ignored

No model call, no network: the verifier is cheap, reproducible and cannot itself
hallucinate. Every check is best-effort — a failing check never breaks a turn.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Iterable

from app.services import semantics as semantics_lib

logger = logging.getLogger(__name__)

WEIGHTS = {"high": 18, "medium": 9, "low": 4}
MAX_UNGROUNDED_REPORTED = 4

# A number, optionally with a currency prefix and a magnitude/percent suffix.
NUMBER_RE = re.compile(
    r"(?<![\w.])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(%|percent|pp|bn|[KkMmBb](?![A-Za-z])|thousand|million|billion)?",
    re.I,
)
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
SHARE_COLUMN_RE = re.compile(r"(share|pct|percent|proportion|%|mix|weight)", re.I)
COUNT_COLUMN_RE = re.compile(r"^(n|count|orders|transactions|rows|records|customers|users|observations)$", re.I)
MISSING_WORDS_RE = re.compile(
    r"(missing|blank|null|empty|incomplete|not recorded|unavailable|absent|excluded|no data)", re.I
)
MEAN_CALL_RE = re.compile(r"\.(mean|average)\s*\(")
MAGNITUDES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


# ------------------------------------------------------------------------------ public API


def verify_analysis(
    *,
    plan: dict[str, Any] | None,
    code: str | None,
    execution: dict[str, Any] | None,
    report: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
    cleaning: dict[str, Any] | None = None,
    semantics: dict[str, Any] | None = None,
    attempt_log: list[dict[str, Any]] | None = None,
    question: str = "",
) -> dict[str, Any]:
    """Audit one analysis turn. Returns a verification record for persistence and display."""
    if not execution or not execution.get("ok"):
        return {
            "score": None,
            "confidence": "unverified",
            "summary": "Not verified — the analysis did not produce results.",
            "findings": [],
            "checks": 0,
        }

    findings = audit_execution(
        plan=plan, code=code, execution=execution, report=report, profile=profile,
        cleaning=cleaning, semantics=semantics, attempt_log=attempt_log, question=question,
    )
    findings += audit_report(execution=execution, report=report, question=question)

    penalty = sum(WEIGHTS.get(f["severity"], WEIGHTS["low"]) for f in findings)
    score = max(0, min(100, 100 - penalty))
    confidence = "high" if score >= 85 else "medium" if score >= 65 else "low"
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return {
        "score": score,
        "confidence": confidence,
        "summary": _summary(score, confidence, findings),
        "findings": findings,
        "checks": len(CHECK_NAMES),
    }


def data_check_block(findings: list[dict[str, Any]]) -> str:
    """Render pre-report findings so the write-up can address them honestly."""
    relevant = [f for f in findings if f["category"] in ("data", "definition", "method")]
    if not relevant:
        return ""
    lines = [
        "AUTOMATED DATA CHECKS (deterministic; acknowledge material ones in `caveats` and "
        "correct the interpretation if a check contradicts it):"
    ]
    lines += [f"- [{f['severity']}] {f['title']}: {f['detail']}" for f in relevant]
    return "\n".join(lines)


CHECK_NAMES = (
    "outputs_present", "kpi_values_finite", "percent_scale", "delta_scale", "share_totals",
    "missing_data_disclosed", "outlier_sensitive_mean", "small_groups", "metric_definitions",
    "self_repairs", "number_grounding",
)


# ------------------------------------------------------------------------------ execution audit


def audit_execution(
    *,
    plan: dict[str, Any] | None,
    code: str | None,
    execution: dict[str, Any],
    report: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    cleaning: dict[str, Any] | None,
    semantics: dict[str, Any] | None,
    attempt_log: list[dict[str, Any]] | None,
    question: str,
) -> list[dict[str, Any]]:
    checks: list[Callable[[], list[dict[str, Any]]]] = [
        lambda: _outputs_present(execution),
        lambda: _kpi_values_finite(execution),
        lambda: _percent_scale(execution),
        lambda: _delta_scale(execution),
        lambda: _share_totals(execution),
        lambda: _missing_data_disclosed(code or "", execution, report, profile),
        lambda: _outlier_sensitive_mean(code or "", cleaning),
        lambda: _small_groups(execution),
        lambda: _metric_definitions(code or "", semantics, plan, question),
        lambda: _self_repairs(attempt_log or []),
    ]
    return _run(checks)


def audit_report(
    *, execution: dict[str, Any], report: dict[str, Any] | None, question: str = ""
) -> list[dict[str, Any]]:
    return _run([lambda: _number_grounding(execution, report, question)])


def _run(checks: Iterable[Callable[[], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for check in checks:
        try:
            findings.extend(check() or [])
        except Exception:  # noqa: BLE001 — verification must never break an analysis
            logger.debug("Verification check failed", exc_info=True)
    return findings


def _finding(check: str, severity: str, category: str, title: str, detail: str) -> dict[str, Any]:
    return {"id": check, "check": check, "severity": severity, "category": category,
            "title": title, "detail": detail}


def _summary(score: int, confidence: str, findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "All automated checks passed: every figure in the write-up traces back to computed output."
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    parts = [f"{counts[s]} {s}" for s in ("high", "medium", "low") if s in counts]
    return f"{confidence.title()} confidence ({score}/100) — {', '.join(parts)} finding(s) to review."


# ------------------------------------------------------------------------------ individual checks


def _outputs_present(execution: dict[str, Any]) -> list[dict[str, Any]]:
    if execution.get("kpis") or execution.get("charts") or execution.get("tables"):
        return []
    return [_finding(
        "outputs_present", "high", "method", "The analysis produced no results",
        "The code ran without error but emitted no KPIs, charts or tables, so there is nothing "
        "for the write-up to be based on.",
    )]


def _kpi_values_finite(execution: dict[str, Any]) -> list[dict[str, Any]]:
    empty = [k["label"] for k in execution.get("kpis") or [] if k.get("value") is None]
    if not empty:
        return []
    return [_finding(
        "kpi_values_finite", "high", "method", f"{len(empty)} KPI(s) have no value",
        "Empty results usually mean a division by zero, an all-missing column or a filter that "
        f"matched no rows: {', '.join(empty[:4])}.",
    )]


def _percent_scale(execution: dict[str, Any]) -> list[dict[str, Any]]:
    suspects = [
        f"{k['label']} = {k['value']:g}"
        for k in execution.get("kpis") or []
        if k.get("format") == "percent" and isinstance(k.get("value"), (int, float))
        and not isinstance(k.get("value"), bool) and abs(float(k["value"])) > 1.5
    ]
    if not suspects:
        return []
    return [_finding(
        "percent_scale", "high", "method", "A percentage looks multiplied by 100 twice",
        "KPIs formatted as percentages expect a fraction (0.234 → 23.4%), but these hold values "
        f"above 1.5, so the displayed figure is likely 100× too large: {', '.join(suspects[:3])}.",
    )]


def _delta_scale(execution: dict[str, Any]) -> list[dict[str, Any]]:
    suspects = [
        f"{k['label']} (Δ {k['delta']:g})"
        for k in execution.get("kpis") or []
        if isinstance(k.get("delta"), (int, float)) and not isinstance(k.get("delta"), bool)
        and abs(float(k["delta"])) > 10
    ]
    if not suspects:
        return []
    return [_finding(
        "delta_scale", "medium", "method", "A change value looks like a percentage, not a fraction",
        "Deltas are fractional changes (0.12 → +12%). Values above 10 imply a 1,000%+ move, which "
        f"usually means the number was passed as a percentage: {', '.join(suspects[:3])}.",
    )]


def _share_totals(execution: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for table in execution.get("tables") or []:
        if table.get("truncated") or len(table.get("rows") or []) < 2:
            continue
        for position, column in enumerate(table.get("columns") or []):
            if column.get("kind") != "number" or not SHARE_COLUMN_RE.search(column.get("name", "")):
                continue
            values = [row[position] for row in table["rows"]
                      if position < len(row) and isinstance(row[position], (int, float))
                      and not isinstance(row[position], bool)]
            if len(values) < 2:
                continue
            total = float(sum(values))
            # Only a total of ~1 or ~100% proves the breakdown is complete.
            if _near(total, 1.0, 0.02) or _near(total, 100.0, 0.02):
                continue
            findings.append(_finding(
                "share_totals", "low", "data", f"Shares in “{table['title']}” do not add up",
                f"Column “{column['name']}” sums to {total:,.4g} across {len(values)} rows rather than "
                "1 or 100%. That is expected for a filtered subset, but wrong if it is meant to be a "
                "complete breakdown.",
            ))
            break
    return findings[:2]


def _missing_data_disclosed(
    code: str, execution: dict[str, Any], report: dict[str, Any] | None, profile: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not profile:
        return []
    used = _columns_used(code, profile)
    gappy = [
        c for c in profile.get("columns") or []
        if c["name"] in used and float(c.get("missing_pct") or 0) >= 5
    ]
    if not gappy:
        return []
    disclosed = MISSING_WORDS_RE.search(" ".join([
        *(report or {}).get("caveats", []),
        (report or {}).get("answer_markdown", "") or "",
        *(execution.get("warnings") or []),
        execution.get("stdout", "") or "",
    ]))
    if disclosed:
        return []
    worst = sorted(gappy, key=lambda c: -float(c["missing_pct"]))[:3]
    listed = ", ".join(f"{c['name']} ({c['missing_pct']:.0f}% empty)" for c in worst)
    return [_finding(
        "missing_data_disclosed", "medium", "data", "Missing data was not disclosed to the reader",
        f"The analysis uses columns with material gaps — {listed} — and neither the answer nor its "
        "caveats mention them. Rows with gaps are silently excluded by most aggregations.",
    )]


def _outlier_sensitive_mean(code: str, cleaning: dict[str, Any] | None) -> list[dict[str, Any]]:
    outliers = (cleaning or {}).get("outliers") or []
    if not outliers or not MEAN_CALL_RE.search(code):
        return []
    hit = [o for o in outliers if re.search(rf"(?<!\w){re.escape(o['column'])}(?!\w)", code)]
    if not hit:
        return []
    worst = max(hit, key=lambda o: o["count"])
    return [_finding(
        "outlier_sensitive_mean", "low", "method", "An average is exposed to flagged outliers",
        f"“{worst['column']}” has {worst['count']:,} values beyond 3×IQR and the code takes a mean. "
        "A median, or a stated exclusion, is usually the more honest summary.",
    )]


def _small_groups(execution: dict[str, Any]) -> list[dict[str, Any]]:
    for table in execution.get("tables") or []:
        for position, column in enumerate(table.get("columns") or []):
            if column.get("kind") != "number" or not COUNT_COLUMN_RE.match(column.get("name", "").strip()):
                continue
            values = [row[position] for row in table.get("rows") or []
                      if position < len(row) and isinstance(row[position], (int, float))
                      and not isinstance(row[position], bool)]
            thin = [v for v in values if 0 < v < 5]
            if len(values) >= 3 and thin:
                return [_finding(
                    "small_groups", "low", "data", "Some groups are too small to compare",
                    f"“{table['title']}” contains {len(thin)} group(s) with fewer than 5 records "
                    f"(smallest: {min(thin):g}). Rates computed on them are very noisy.",
                )]
    return []


def _metric_definitions(
    code: str, semantics: dict[str, Any] | None, plan: dict[str, Any] | None, question: str
) -> list[dict[str, Any]]:
    if semantics_lib.is_empty(semantics) or not code:
        return []
    restated = (plan or {}).get("restated_question", "") or ""
    labels = " ".join(str(k.get("name", "")) for k in (plan or {}).get("kpis") or [])
    mentioned = semantics_lib.mentioned_metrics(semantics, question, restated, labels)
    lowered_code = code.casefold()
    ignored = []
    for metric in mentioned:
        keywords = semantics_lib.definition_keywords(metric["definition"])
        if not keywords:
            continue
        if not any(keyword in lowered_code for keyword in keywords):
            ignored.append(metric["name"])
    if not ignored:
        return []
    return [_finding(
        "metric_definitions", "high", "definition", "A defined metric may not have been applied",
        f"Your definition of {', '.join(ignored[:3])} is part of this question, but none of the terms "
        "in that definition appear in the code that produced the answer. Check the code before "
        "acting on the number.",
    )]


def _self_repairs(attempt_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = [a for a in attempt_log if not a.get("ok")]
    if not failures:
        return []
    first = (failures[0].get("error") or "").splitlines()[0][:160]
    return [_finding(
        "self_repairs", "low", "process", f"The code needed {len(failures)} automatic fix(es)",
        f"The first attempt failed with: {first}. The final run succeeded, but the approach changed "
        "along the way — worth a glance at the code.",
    )]


# ------------------------------------------------------------------------------ number grounding


def _number_grounding(
    execution: dict[str, Any], report: dict[str, Any] | None, question: str
) -> list[dict[str, Any]]:
    if not report:
        return []
    narrative = " ".join(filter(None, [
        report.get("headline") or "",
        report.get("answer_markdown") or "",
        *[f"{i.get('title', '')} {i.get('detail', '')}" for i in report.get("insights") or []],
    ]))
    if not narrative.strip():
        return []

    computed = _computed_values(execution)
    if not computed:
        return []
    asked = {literal for literal, _ in _numbers(question)}

    ungrounded: list[str] = []
    for literal, candidates in _numbers(narrative):
        if literal in asked or not candidates:
            continue
        if not any(_matches_any(value, computed, literal) for value in candidates):
            ungrounded.append(literal)
    # Repeated figures are one claim, not several.
    unique = list(dict.fromkeys(ungrounded))
    if not unique:
        return []
    severity = "high" if len(unique) >= 3 else "medium"
    shown = ", ".join(unique[:MAX_UNGROUNDED_REPORTED])
    more = f" (+{len(unique) - MAX_UNGROUNDED_REPORTED} more)" if len(unique) > MAX_UNGROUNDED_REPORTED else ""
    return [_finding(
        "number_grounding", severity, "grounding",
        f"{len(unique)} figure(s) in the write-up are not in the computed output",
        f"{shown}{more} could not be matched to any KPI, table cell, chart value or printed result. "
        "Treat them as unverified — they may be rounded differently, derived in the text, or invented.",
    )]


def _numbers(text: str) -> list[tuple[str, list[float]]]:
    """Extract numeric literals with every plausible numeric interpretation."""
    cleaned = ISO_DATE_RE.sub(" ", text or "")
    found: list[tuple[str, list[float]]] = []
    for match in NUMBER_RE.finditer(cleaned):
        currency, body, suffix = match.group(1), match.group(2), (match.group(3) or "").lower()
        literal = match.group(0).strip()
        try:
            base = float(body.replace(",", ""))
        except ValueError:
            continue
        is_percent = suffix in ("%", "percent", "pp")
        magnitude = MAGNITUDES.get(suffix)

        if not suffix and not currency:
            # Years and small counts ("top 10", "3 regions", "Q4") are not claims worth auditing.
            if base.is_integer() and (1900 <= base <= 2099 or base <= 24):
                continue
        candidates = [base]
        if is_percent:
            candidates.append(base / 100)
        if magnitude:
            candidates.append(base * magnitude)
        found.append((literal, candidates))
    return found


def _computed_values(execution: dict[str, Any]) -> list[float]:
    values: list[float] = []

    def add(value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        number = float(value)
        if math.isfinite(number):
            values.append(number)

    for kpi in execution.get("kpis") or []:
        add(kpi.get("value"))
        add(kpi.get("delta"))
    for table in execution.get("tables") or []:
        for row in table.get("rows") or []:
            for cell in row:
                add(cell)
    for chart in execution.get("charts") or []:
        for trace in (chart.get("digest") or {}).get("traces") or []:
            for key in ("x", "y", "values", "z"):
                for item in trace.get(key) or []:
                    add(item)
    for _, candidates in _numbers(execution.get("stdout") or ""):
        for candidate in candidates:
            add(candidate)

    # Percentages are usually reported from fractions, and totals from their parts.
    derived = [v * 100 for v in values if abs(v) <= 1]
    return values + derived


def _matches_any(value: float, computed: list[float], literal: str) -> bool:
    tolerance = 0.05 if _significant_digits(literal) <= 3 else 0.005
    return any(_near(value, other, tolerance) for other in computed)


def _near(a: float, b: float, tolerance: float) -> bool:
    scale = max(abs(a), abs(b))
    if scale == 0:
        return True
    return abs(a - b) <= max(tolerance * scale, 1e-9)


def _significant_digits(literal: str) -> int:
    body = re.sub(r"[^\d.]", "", literal)
    if "." in body:
        digits = body.replace(".", "").lstrip("0")
    else:
        digits = body.lstrip("0").rstrip("0")
    return max(len(digits), 1)


def _columns_used(code: str, profile: dict[str, Any]) -> set[str]:
    """Column names that appear as literals in the generated code."""
    used: set[str] = set()
    for column in profile.get("columns") or []:
        name = column.get("name")
        if name and re.search(rf"""['"]{re.escape(str(name))}['"]""", code):
            used.add(name)
    return used
