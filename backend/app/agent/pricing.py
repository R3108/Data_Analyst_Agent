"""Token usage aggregation and cost estimation (USD per million tokens)."""

from __future__ import annotations

from typing import Any

# (input, output) OpenAI list prices in USD per million tokens. Cached reads bill
# at 0.1× input and cache writes at 1.25× input for the models below.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-6-astra": (10.0, 50.0),
    "gpt-5.6-sol": (4.0, 20.0),
    "gpt-5.6": (4.0, 20.0),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-luna": (0.2, 1.2),
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def _price(model: str) -> tuple[float, float] | None:
    if model in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[model]
    # Tolerate provider prefixes and dated snapshots.
    names = sorted(PRICES_PER_MTOK, key=len, reverse=True)
    return next((PRICES_PER_MTOK[name] for name in names if name in model), None)


def estimate_cost(record: dict[str, Any]) -> float | None:
    price = _price(str(record.get("model", "")))
    if price is None:
        return None
    input_price, output_price = price
    input_tokens = int(record.get("input_tokens", 0) or 0)
    cache_read_tokens = int(record.get("cache_read_tokens", 0) or 0)
    cache_write_tokens = int(record.get("cache_write_tokens", 0) or 0)
    # Responses usage includes cached tokens in the input-token total.
    uncached_input_tokens = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    return (
        uncached_input_tokens * input_price
        + record.get("output_tokens", 0) * output_price
        + cache_read_tokens * input_price * CACHE_READ_MULTIPLIER
        + cache_write_tokens * input_price * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


def summarize_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"calls": len(records), "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0}
    cost = 0.0
    priced = True
    models: list[str] = []
    for record in records:
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            totals[key] += int(record.get(key) or 0)
        model = str(record.get("model", ""))
        if model and model not in models:
            models.append(model)
        record_cost = estimate_cost(record)
        if record_cost is None:
            priced = False
        else:
            cost += record_cost
    return {**totals, "models": models, "cost_usd": round(cost, 6) if priced and records else (0.0 if not records else None)}


def merge_usage(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Add already-summarised usage together.

    An investigation's cost is its own two model calls plus every sub-analysis it ran, and
    those were each summarised when their message was stored. Re-deriving cost from the
    totals would re-price cached tokens at the wrong rate, so the costs are added, not the
    tokens re-costed — and one unpriced model makes the whole total unpriced rather than
    quietly understated.
    """
    totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0}
    models: list[str] = []
    cost = 0.0
    priced = True
    for summary in summaries:
        if not summary:
            continue
        for key in totals:
            totals[key] += int(summary.get(key) or 0)
        for model in summary.get("models") or []:
            if model not in models:
                models.append(model)
        value = summary.get("cost_usd")
        if value is None:
            priced = False
        else:
            cost += float(value)
    return {**totals, "models": models, "cost_usd": round(cost, 6) if priced else None}
