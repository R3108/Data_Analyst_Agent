"""Privacy guard: personal data is found before the model ever sees it.

Numera's whole argument is that you can check its work. That argument gets harder the
moment the table contains somebody's email address, because now every convenience —
the schema card sent to the model, the sample rows in the UI, a shared link, a PDF
mailed to a colleague — is a way for that address to travel somewhere nobody decided
it should go.

So the guard works in two stages, and the first one needs no decision from anybody:

1. **Detect and quarantine, at upload.** Every column is scanned with validated
   matchers — Luhn for card numbers, octet ranges for IP addresses, a real email
   grammar — plus column-name signals. Anything flagged with high confidence has its
   example values stripped from the *stored profile*, which is the object the prompt
   is built from. The model gets the column's name, type and statistics; it never gets
   a value. That happens whether or not anyone reads the warning.

2. **Redact, on a decision.** Keep, mask, hash or drop, per column. Applying a policy
   rewrites the cleaned table itself, so there is exactly one copy of the truth and no
   endpoint needs to remember to filter — the preview, the sandbox, every export,
   every share link and every board tile are all reading the redacted table.
   `hash` deliberately keeps a stable pseudonym, so cohort and retention analysis
   still works on a customer id whose real value has left the building.

A policy is inherited by the next version of its dataset and re-applied on arrival,
salt included, so pseudonyms line up across versions and next month's export does not
quietly re-introduce what last month's redaction removed.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from app.core.errors import InvalidInputError

logger = logging.getLogger(__name__)

ACTIONS = ("keep", "mask", "hash", "drop")
SEVERITIES = ("high", "medium", "low")
SAMPLE_SIZE = 400
# Below this share of values matching, a pattern is a coincidence, not a column type.
MIN_MATCH_RATE = 0.5
# A column name alone is decent evidence, but not proof — values can still overrule it.
NAME_CONFIDENCE = 0.6
MASK = "•"
HASH_PREFIX = "id_"
HASH_LENGTH = 16


# --------------------------------------------------------------------------- matchers


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[A-Za-z]{2,}$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
PHONE_RE = re.compile(r"^\+?\d[\d\s().\-]{5,18}\d$")
CARD_RE = re.compile(r"^[\d \-]{12,23}$")
POSTCODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 \-]{2,9}$", re.I)
STREET_RE = re.compile(
    r"\b(street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd\.?|"
    r"court|ct\.?|way|close|terrace|place|square|apt\.?|apartment|suite|floor|flat)\b",
    re.I,
)
PERSON_RE = re.compile(r"^[A-Z][a-z'’\-]{1,20}(?: [A-Z][a-z'’\-]{1,20}){1,3}$")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value))


def _is_iban(value: str) -> bool:
    return bool(IBAN_RE.match(value.replace(" ", "").upper()))


def _is_ssn(value: str) -> bool:
    return bool(SSN_RE.match(value))


def _is_ipv4(value: str) -> bool:
    match = IPV4_RE.match(value)
    return bool(match) and all(0 <= int(group) <= 255 for group in match.groups())


def _is_phone(value: str) -> bool:
    """Formatted like a phone number: a country prefix or separators.

    A bare run of eleven digits is far more often an order reference than a phone
    number, so the strict matcher insists on the punctuation a phone number carries.
    A column *named* `phone` gets the loose matcher instead.
    """
    if not _is_phone_loose(value):
        return False
    return value.startswith("+") or bool(re.search(r"[\s().\-]", value))


def _is_phone_loose(value: str) -> bool:
    if not PHONE_RE.match(value):
        return False
    digits = _digits(value)
    # Seven digits is the shortest real subscriber number; fifteen is E.164's ceiling.
    return 7 <= len(digits) <= 15


def _is_card(value: str) -> bool:
    """Luhn-checked, so a 16-digit order number is not mistaken for a card."""
    if not CARD_RE.match(value):
        return False
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _is_address(value: str) -> bool:
    return bool(STREET_RE.search(value)) and any(ch.isdigit() for ch in value)


def _is_person(value: str) -> bool:
    return bool(PERSON_RE.match(value))


def _is_postcode(value: str) -> bool:
    return bool(POSTCODE_RE.match(value)) and any(ch.isdigit() for ch in value)


@dataclass(frozen=True)
class Detector:
    kind: str
    label: str
    severity: str
    recommended: str
    explain: str
    names: re.Pattern[str] | None = None
    matcher: Callable[[str], bool] | None = None
    # Some kinds are only ever recognised by their column name — "date of birth" looks
    # exactly like any other date, and guessing from values would flag every date column.
    name_only: bool = False
    # A shape too common to flag on its own: only considered when the name agrees.
    # Without this a postcode pattern matches every short alphanumeric reference code.
    require_name: bool = False
    # Used in place of `matcher` once the column name has already vouched for the column,
    # so `phone_number` can hold unpunctuated digits without `reference_number` matching.
    loose: Callable[[str], bool] | None = None
    # The name alone settles it: nothing called `ssn` or `iban` is anything else.
    name_sufficient: bool = False


DETECTORS: tuple[Detector, ...] = (
    Detector(
        "email", "Email address", "high", "hash",
        "Directly identifies a person and is the most common join key between leaked datasets.",
        names=re.compile(r"(e[-_ ]?mail|contact_address)", re.I), matcher=_is_email,
    ),
    Detector(
        "credit_card", "Payment card number", "high", "drop",
        "Card numbers are regulated data (PCI DSS); they should not be in an analytics table at all.",
        names=re.compile(r"(credit_?card|card_?number|card_?no\b|\bpan\b|cc_?num)", re.I),
        matcher=_is_card,
    ),
    Detector(
        "national_id", "National identifier", "high", "drop",
        "A government identifier is the strongest possible link to one person.",
        names=re.compile(r"(\bssn\b|social_?security|national_?id|\bnino\b|aadhaar|tax_?id|passport)", re.I),
        matcher=_is_ssn, name_sufficient=True,
    ),
    Detector(
        "bank_account", "Bank account / IBAN", "high", "drop",
        "Account identifiers enable fraud directly, not just re-identification.",
        names=re.compile(r"(\biban\b|bank_?account|account_?number|sort_?code|routing_?number)", re.I),
        matcher=_is_iban, name_sufficient=True,
    ),
    Detector(
        "credential", "Secret or credential", "high", "drop",
        "Keys and tokens are live access, not data. They do not belong in a dataset.",
        names=re.compile(r"(password|passwd|secret|api_?key|token|private_?key|auth)", re.I),
        name_only=True,
    ),
    Detector(
        "phone", "Phone number", "high", "mask",
        "A phone number identifies a person and is widely used to link records.",
        names=re.compile(r"(phone|mobile|cell|telephone|msisdn|whatsapp)", re.I),
        matcher=_is_phone, loose=_is_phone_loose,
    ),
    Detector(
        # Name-gated on purpose. "Air Fryer", "New York" and "Office Supplies" all have
        # exactly the shape of a person's name, so flagging on the values alone would
        # call half the product catalogue personal data.
        "person_name", "Person's name", "high", "mask",
        "A name is identifying on its own and almost never needed for analysis.",
        names=re.compile(r"(first_?name|last_?name|sur_?name|full_?name|customer_?name|"
                         r"contact_?name|employee_?name|account_?holder|card_?holder|"
                         r"recipient|patient|client_?name|applicant|^name$)", re.I),
        matcher=_is_person, require_name=True,
    ),
    Detector(
        "date_of_birth", "Date of birth", "high", "drop",
        "Birth date plus postcode plus sex re-identifies most people in a population.",
        names=re.compile(r"(date_?of_?birth|birth_?date|^dob$|birthday)", re.I), name_only=True,
    ),
    Detector(
        "street_address", "Street address", "medium", "mask",
        "A full address locates a household; it is rarely the unit of analysis.",
        names=re.compile(r"(address|street|addr_?line|billing_?address|shipping_?address)", re.I),
        matcher=_is_address,
    ),
    Detector(
        "ip_address", "IP address", "medium", "mask",
        "Treated as personal data under GDPR, and it pins down a device and a location.",
        names=re.compile(r"(ip_?address|client_?ip|remote_?addr)", re.I), matcher=_is_ipv4,
    ),
    Detector(
        "postcode", "Postal code", "low", "keep",
        "Useful for geography and only identifying in combination — worth knowing it is here.",
        names=re.compile(r"(post_?code|zip_?code|^zip$|postal)", re.I), matcher=_is_postcode,
        require_name=True,
    ),
    Detector(
        "free_text", "Free-text field", "medium", "keep",
        "Long free text is the one place a scan cannot promise anything: names, numbers and "
        "complaints all end up in comment boxes.",
        names=re.compile(r"(comment|note|feedback|review|description|message|remark|reason)", re.I),
        name_only=True,
    ),
)
BY_KIND = {detector.kind: detector for detector in DETECTORS}


# --------------------------------------------------------------------------- scanning


def scan(df: pd.DataFrame, profile: dict[str, Any] | None = None,
         previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Findings, a suggested policy and a one-line verdict. No values are returned."""
    columns = {c["name"]: c for c in (profile or {}).get("columns") or []}
    findings: list[dict[str, Any]] = []
    for name in df.columns:
        finding = _scan_column(df[name], str(name), columns.get(str(name)) or {})
        if finding:
            findings.append(finding)

    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), -f["confidence"], f["column"]))
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in SEVERITIES}

    inherited = (previous or {}).get("policy") or {}
    suggested = {
        f["column"]: inherited.get(f["column"], f["recommended"])
        for f in findings if inherited.get(f["column"], f["recommended"]) != "keep"
    }
    return {
        "findings": findings,
        "counts": counts,
        "sensitive_columns": [f["column"] for f in findings if f["severity"] != "low"],
        "status": "sensitive" if counts["high"] else "review" if counts["medium"] else "clear",
        "headline": _scan_headline(findings, counts),
        "suggested_policy": suggested,
        "scanned_columns": int(df.shape[1]),
    }


def empty_scan(columns: int = 0, reason: str = "Scanning is disabled on this server.") -> dict[str, Any]:
    """The shape a caller gets when the detector did not run. Never a silent absence."""
    return {
        "findings": [], "counts": {severity: 0 for severity in SEVERITIES},
        "sensitive_columns": [], "status": "off", "headline": reason,
        "suggested_policy": {}, "scanned_columns": int(columns),
    }


def _scan_column(series: pd.Series, name: str, column: dict[str, Any]) -> dict[str, Any] | None:
    """Value evidence and name evidence, combined — neither alone is enough."""
    matches: list[tuple[Detector, float]] = []
    values = _sample(series)

    for detector in DETECTORS:
        by_name = bool(detector.names and detector.names.search(name))
        if detector.require_name and not by_name:
            continue
        if detector.name_only:
            if by_name and _plausible(detector, series, column):
                matches.append((detector, NAME_CONFIDENCE + 0.2))
            continue
        rate = _match_rate(detector, values, by_name)
        if rate >= MIN_MATCH_RATE:
            # Values that validate are the strong signal; a matching name confirms it.
            matches.append((detector, min(0.99, 0.55 + 0.4 * rate + (0.1 if by_name else 0.0))))
        elif by_name and rate >= 0.15:
            matches.append((detector, min(0.9, NAME_CONFIDENCE + rate)))
        elif by_name and (detector.name_sufficient or not values):
            matches.append((detector, NAME_CONFIDENCE))

    if not matches:
        return None
    detector, confidence = max(matches, key=lambda m: (m[1], -SEVERITIES.index(m[0].severity)))
    by_name = bool(detector.names and detector.names.search(name))
    matched = int(round(_match_rate(detector, values, by_name) * len(values))) if values else 0
    return {
        "column": name,
        "kind": detector.kind,
        "label": detector.label,
        "severity": detector.severity,
        "confidence": round(float(confidence), 3),
        "recommended": detector.recommended,
        "why": detector.explain,
        "basis": ("column name" if detector.name_only or not matched
                  else "values and column name" if detector.names and detector.names.search(name)
                  else "values"),
        "match_rate": round(matched / len(values), 3) if values else None,
        "sampled": len(values),
        # A shape, never a value: letters become a, digits become 9, punctuation stays.
        "shape": _shape(values[0]) if values else None,
        "missing_pct": column.get("missing_pct"),
        "unique": column.get("unique"),
    }


def _match_rate(detector: Detector, values: list[str], by_name: bool) -> float:
    """Share of sampled values the detector accepts, loosened once the name has vouched."""
    matcher = detector.loose if (by_name and detector.loose) else detector.matcher
    if not matcher or not values:
        return 0.0
    return sum(1 for value in values if matcher(value)) / len(values)


def _plausible(detector: Detector, series: pd.Series, column: dict[str, Any]) -> bool:
    """A name-only detector still has to agree with the column's actual type."""
    if detector.kind == "date_of_birth":
        return bool(pd.api.types.is_datetime64_any_dtype(series)) or column.get("dtype") == "datetime"
    if detector.kind == "free_text":
        return float(column.get("avg_length") or 0) >= 25 or column.get("role") == "text"
    return True


def _sample(series: pd.Series) -> list[str]:
    non_null = series.dropna()
    if non_null.empty:
        return []
    if len(non_null) > SAMPLE_SIZE:
        non_null = non_null.sample(SAMPLE_SIZE, random_state=0)
    return [text for text in (str(value).strip() for value in non_null) if text]


def _shape(value: str) -> str:
    shape = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", value))
    return shape if len(shape) <= 40 else f"{shape[:37]}…"


def _scan_headline(findings: list[dict[str, Any]], counts: dict[str, int]) -> str:
    if not findings:
        return "No personal data detected in this table."
    high = [f for f in findings if f["severity"] == "high"]
    if high:
        named = ", ".join(f"{f['column']} ({f['label'].lower()})" for f in high[:3])
        more = f" and {len(high) - 3} more" if len(high) > 3 else ""
        subject = "1 column holds" if counts["high"] == 1 else f"{counts['high']} columns hold"
        return (
            f"{subject} directly identifying data: {named}{more}. "
            "Example values are already withheld from the model."
        )
    medium = "1 column may" if counts["medium"] == 1 else f"{counts['medium']} columns may"
    return (
        f"{medium} hold personal data, and {counts['low']} "
        f"{'is' if counts['low'] == 1 else 'are'} worth a look. Nothing is directly "
        "identifying on its own."
    )


# --------------------------------------------------------------------------- policy


EMPTY: dict[str, Any] = {"policy": {}, "salt": None, "applied_at": None, "applied": []}


def normalize(payload: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """A validated policy. The salt is preserved so pseudonyms survive a re-edit."""
    if payload is None:
        return dict(EMPTY)
    source = payload.get("policy") if isinstance(payload, dict) and "policy" in payload else payload
    if not isinstance(source, dict):
        raise InvalidInputError("A privacy policy is a mapping of column name to action.")
    policy: dict[str, str] = {}
    for column, action in source.items():
        name = str(column).strip()[:200]
        verb = str(action).strip().lower()
        if not name:
            continue
        if verb not in ACTIONS:
            raise InvalidInputError(
                f"'{action}' is not a privacy action for '{name}'. Use one of: {', '.join(ACTIONS)}."
            )
        if verb != "keep":
            policy[name] = verb
    salt = (previous or {}).get("salt")
    if not salt and any(action == "hash" for action in policy.values()):
        salt = secrets.token_hex(16)
    return {
        "policy": policy,
        "salt": salt,
        "applied_at": (previous or {}).get("applied_at"),
        "applied": (previous or {}).get("applied") or [],
    }


def is_empty(state: dict[str, Any] | None) -> bool:
    return not (state or {}).get("policy")


def protected_columns(scan_result: dict[str, Any] | None,
                      minimum: str = "high") -> list[str]:
    """Columns whose example values must not leave the server."""
    threshold = SEVERITIES.index(minimum)
    return [
        f["column"] for f in (scan_result or {}).get("findings") or []
        if SEVERITIES.index(f["severity"]) <= threshold
    ]


# --------------------------------------------------------------------------- redaction


def apply_policy(df: pd.DataFrame, state: dict[str, Any] | None) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Rewrite the frame under the policy. Returns the frame and what was done to it."""
    policy = (state or {}).get("policy") or {}
    if not policy:
        return df, []
    salt = (state or {}).get("salt") or ""
    frame = df.copy()
    applied: list[dict[str, Any]] = []

    for column, action in policy.items():
        if column not in frame.columns:
            applied.append({"column": column, "action": action, "status": "missing",
                            "detail": "The column is not in this version of the table."})
            continue
        affected = int(frame[column].notna().sum())
        if action == "drop":
            frame = frame.drop(columns=[column])
            detail = "Column removed entirely."
        elif action == "hash":
            frame[column] = _hash_series(frame[column], salt)
            detail = (
                "Replaced with a salted SHA-256 pseudonym. The same value always produces the "
                "same pseudonym within this dataset lineage, so grouping and retention still work."
            )
        else:
            frame[column] = _mask_series(frame[column])
            detail = "Values masked in place, keeping their shape and last characters."
        applied.append({"column": column, "action": action, "status": "applied",
                        "affected": affected, "detail": detail})
    return frame, applied


def _hash_series(series: pd.Series, salt: str) -> pd.Series:
    def digest(value: Any) -> Any:
        if pd.isna(value):
            return value
        token = hashlib.sha256(f"{salt}|{value}".encode("utf-8")).hexdigest()[:HASH_LENGTH]
        return f"{HASH_PREFIX}{token}"

    return series.map(digest).astype("object")


def _mask_series(series: pd.Series) -> pd.Series:
    return series.map(mask_value).astype("object")


def mask_value(value: Any) -> Any:
    """Keep enough shape to recognise a column, not enough to recognise a person."""
    if pd.isna(value):
        return value
    text = str(value)
    if "@" in text and _is_email(text):
        local, _, domain = text.partition("@")
        parts = domain.split(".")
        head = parts[0] if parts else ""
        tail = ".".join(parts[1:])
        return f"{MASK * max(len(local), 3)}@{MASK * max(len(head), 3)}" + (f".{tail}" if tail else "")
    digits = _digits(text)
    if len(digits) >= 7 and len(digits) >= len(text) - 6:
        # A number worth masking: keep the last four, which is what a human uses to
        # confirm "yes, that is the right record", and nothing else.
        return f"{MASK * max(len(digits) - 4, 4)}{digits[-4:]}"
    if len(text) <= 2:
        return MASK * len(text)
    return f"{text[0]}{MASK * min(len(text) - 1, 11)}"


def shield_profile(profile: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    """Strip example values for `columns` from a profile, in place of the real ones.

    This is the guarantee that costs nothing and needs no decision: the profile is the
    object `dataset_context()` turns into the model's schema card, so a value removed
    here cannot reach a prompt. Names, types, roles and statistics stay — the model
    still knows the column exists and what it is for.
    """
    if not columns:
        return profile
    protected = set(columns)
    shielded = dict(profile)
    shielded["columns"] = [
        {
            **column,
            "sample_values": ["(withheld)"] if column.get("sample_values") else [],
            "top_values": [
                {**entry, "value": "(withheld)"} for entry in (column.get("top_values") or [])
            ],
            "sensitive": True,
        }
        if column.get("name") in protected else column
        for column in profile.get("columns") or []
    ]
    sample = profile.get("sample_rows") or {}
    if sample.get("columns"):
        indexes = [i for i, name in enumerate(sample["columns"]) if name in protected]
        if indexes:
            shielded["sample_rows"] = {
                **sample,
                "rows": [
                    [("(withheld)" if i in indexes else cell) for i, cell in enumerate(row)]
                    for row in sample.get("rows") or []
                ],
            }
    shielded["withheld_columns"] = sorted(protected & {
        str(c.get("name")) for c in profile.get("columns") or []
    })
    return shielded


# --------------------------------------------------------------------------- markdown


def privacy_markdown(scan_result: dict[str, Any], state: dict[str, Any] | None,
                     dataset_name: str = "") -> str:
    policy = (state or {}).get("policy") or {}
    applied = (state or {}).get("applied") or []
    lines = [
        "# Privacy review",
        "",
        f"_{dataset_name + ' · ' if dataset_name else ''}"
        f"{scan_result.get('scanned_columns', 0)} columns scanned · "
        f"status: {scan_result.get('status', 'unknown')}_",
        "",
        f"**{scan_result.get('headline', '')}**",
        "",
    ]
    findings = scan_result.get("findings") or []
    if findings:
        lines += [
            "| Column | What it looks like | Severity | Confidence | Evidence | Action |",
            "|---|---|---|---:|---|---|",
        ]
        for finding in findings:
            action = policy.get(finding["column"], "keep")
            lines.append(
                f"| `{finding['column']}` | {finding['label']} | {finding['severity']} | "
                f"{finding['confidence']:.0%} | {finding['basis']} | {action} |"
            )
        lines.append("")
        lines += ["## Why each one matters", ""]
        for finding in findings:
            lines.append(f"- **{finding['column']}** — {finding['why']}")
        lines.append("")
    else:
        lines += ["No column matched a personal-data pattern.", ""]

    if applied:
        lines += [
            "## Redaction applied",
            "",
            f"_{(state or {}).get('applied_at') or 'not yet applied'}_",
            "",
        ]
        for entry in applied:
            lines.append(
                f"- `{entry['column']}` → **{entry['action']}** — {entry.get('detail', '')}"
            )
        lines.append("")
    elif policy:
        lines += [
            "## Policy set, not yet applied",
            "",
            "These actions are recorded but the table has not been rewritten yet.",
            "",
        ] + [f"- `{column}` → **{action}**" for column, action in sorted(policy.items())] + [""]

    lines += [
        "---",
        "",
        "_Detection is deterministic pattern matching over the cleaned table — no model call. "
        "It finds what it recognises; a free-text column can always hold something a pattern "
        "cannot see._",
    ]
    return "\n".join(lines)
