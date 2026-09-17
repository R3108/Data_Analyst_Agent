"""The semantic layer: binding metric definitions, business rules and a glossary.

An AI analyst that re-invents "revenue" on every turn cannot be trusted. Users
define their metrics once per dataset; the definitions are injected into every
prompt as *binding* instructions and are checked afterwards by the verifier
(see `app.services.verification`).

Nothing here talks to a model — it is plain normalisation plus prompt rendering.
"""

from __future__ import annotations

import re
from typing import Any

MAX_METRICS = 40
MAX_RULES = 20
MAX_GLOSSARY = 60
MAX_NAME = 80
MAX_TEXT = 600

FORMATS = ("auto", "number", "integer", "currency", "percent", "text")
EMPTY: dict[str, Any] = {"metrics": [], "rules": [], "glossary": []}

# Words that carry no discriminating power when checking whether a definition was applied.
STOPWORDS = frozenset("""
a an and as at be by for from in into is it of on or per the to with sum total count average mean
number all each any that this over across between divided multiplied minus plus value values
""".split())


def normalize(payload: Any) -> dict[str, Any]:
    """Coerce arbitrary client input into the stored semantics shape."""
    if not isinstance(payload, dict):
        return dict(EMPTY)
    return {
        "metrics": _metrics(payload.get("metrics")),
        "rules": _rules(payload.get("rules")),
        "glossary": _glossary(payload.get("glossary")),
    }


def is_empty(semantics: dict[str, Any] | None) -> bool:
    if not semantics:
        return True
    return not any(semantics.get(key) for key in ("metrics", "rules", "glossary"))


def semantic_context(semantics: dict[str, Any] | None) -> str:
    """Prompt block appended after the schema card. Empty string when nothing is defined."""
    if is_empty(semantics):
        return ""
    assert semantics is not None
    lines = [
        "BUSINESS DEFINITIONS (authoritative — these override your own assumptions).",
        "Compute metrics exactly as defined here. If a definition cannot be computed from the "
        "available columns, say so explicitly instead of silently substituting another calculation.",
    ]

    metrics = semantics.get("metrics") or []
    if metrics:
        lines += ["", "METRICS:"]
        for metric in metrics:
            parts = [f"- {metric['name']}: {metric['definition']}"]
            if metric.get("format") and metric["format"] != "auto":
                parts.append(f"format={metric['format']}")
            if metric.get("unit"):
                parts.append(f"unit={metric['unit']}")
            if metric.get("higher_is_better") is False:
                parts.append("lower is better")
            lines.append(" · ".join(parts))

    rules = semantics.get("rules") or []
    if rules:
        lines += ["", "RULES:"] + [f"- {rule}" for rule in rules]

    glossary = semantics.get("glossary") or []
    if glossary:
        lines += ["", "GLOSSARY:"] + [f"- {entry['term']}: {entry['definition']}" for entry in glossary]
    return "\n".join(lines)


def metric_by_name(semantics: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    for metric in (semantics or {}).get("metrics") or []:
        if metric["name"].casefold() == name.casefold():
            return metric
    return None


def mentioned_metrics(semantics: dict[str, Any] | None, *texts: str) -> list[dict[str, Any]]:
    """Metrics whose name appears in any of `texts` (whole-word, case-insensitive)."""
    haystack = " ".join(t for t in texts if t).casefold()
    if not haystack:
        return []
    found: list[dict[str, Any]] = []
    for metric in (semantics or {}).get("metrics") or []:
        if re.search(rf"(?<!\w){re.escape(metric['name'].casefold())}(?!\w)", haystack):
            found.append(metric)
    return found


def definition_keywords(definition: str) -> list[str]:
    """Distinctive tokens of a definition, used to check whether the code honoured it."""
    seen: dict[str, None] = {}
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", definition):
        lowered = token.casefold()
        if lowered not in STOPWORDS:
            seen.setdefault(lowered, None)
    return list(seen)


# --------------------------------------------------------------------------- normalisation


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _metrics(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), MAX_NAME)
        definition = _text(raw.get("definition"))
        if not name or not definition or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        metric: dict[str, Any] = {"name": name, "definition": definition}
        fmt = _text(raw.get("format"), 16).lower()
        metric["format"] = fmt if fmt in FORMATS else "auto"
        unit = _text(raw.get("unit"), 24)
        if unit:
            metric["unit"] = unit
        if raw.get("higher_is_better") is not None:
            metric["higher_is_better"] = bool(raw["higher_is_better"])
        out.append(metric)
        if len(out) >= MAX_METRICS:
            break
    return out


def _rules(value: Any) -> list[str]:
    out: list[str] = []
    for raw in value if isinstance(value, list) else []:
        rule = _text(raw)
        if rule and rule not in out:
            out.append(rule)
        if len(out) >= MAX_RULES:
            break
    return out


def _glossary(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        term = _text(raw.get("term"), MAX_NAME)
        definition = _text(raw.get("definition"))
        if not term or not definition or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        out.append({"term": term, "definition": definition})
        if len(out) >= MAX_GLOSSARY:
            break
    return out
