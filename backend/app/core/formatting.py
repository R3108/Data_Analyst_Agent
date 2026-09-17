"""Number rendering shared by every deterministic briefing.

Drivers, significance tests and scenarios all put figures in the same tables, so they
have to agree on how one is written — including the typographic minus, so a column never
mixes "-16%" with "−16K".
"""

from __future__ import annotations

MINUS = "−"


def compact(value: float | None) -> str:
    """1_234_567 → "1.2M". Two decimals below 100 so small differences stay visible."""
    if value is None or value != value:
        return "n/a"
    value = float(value)
    magnitude = abs(value)
    sign = MINUS if value < 0 else ""
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{sign}{magnitude / threshold:,.1f}{suffix}"
    if magnitude >= 100:
        return f"{sign}{magnitude:,.0f}"
    return f"{sign}{magnitude:,.2f}"


def percent(value: float | None, digits: int = 1) -> str:
    """0.234 → "23.4%". Takes a fraction, not an already-multiplied number."""
    if value is None or value != value:
        return "n/a"
    value = float(value)
    return f"{MINUS if value < 0 else ''}{abs(value):.{digits}%}"


def signed_percent(value: float | None, digits: int = 1) -> str:
    """Like `percent`, but an increase is written with an explicit +."""
    if value is None or value != value:
        return "n/a"
    value = float(value)
    return f"{MINUS if value < 0 else '+'}{abs(value):.{digits}%}"
