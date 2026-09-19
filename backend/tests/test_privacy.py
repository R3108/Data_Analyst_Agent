"""Privacy guard: detection, redaction, and the guarantee that a value never reaches a prompt."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from app.core.errors import InvalidInputError
from app.services.privacy import (
    apply_policy,
    mask_value,
    normalize,
    privacy_markdown,
    protected_columns,
    scan,
    shield_profile,
)
from app.services.profiling import dataset_context, profile_dataframe

# Synthetic throughout: nothing here belongs to a real person.
EMAILS = [f"person{i}@example.com" for i in range(60)]
PHONES = [f"+1 415 555 {1000 + i:04d}" for i in range(60)]
# Luhn-valid test numbers from the card networks' published test ranges.
CARDS = ["4111111111111111", "5500005555555559", "4012888888881881"] * 20
NAMES = ["Ada Lovelace", "Grace Hopper", "Alan Turing"] * 20
ORDER_IDS = [f"ORD-{90000 + i}" for i in range(60)]


def make_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_email": EMAILS,
        "phone_number": PHONES,
        "card_number": CARDS,
        "full_name": NAMES,
        "client_ip": [f"10.0.{i // 8}.{i % 256}" for i in range(60)],
        "order_id": ORDER_IDS,
        "region": ["North", "South", "East"] * 20,
        "revenue": [100.0 + i for i in range(60)],
        "order_date": pd.date_range("2024-01-01", periods=60, freq="D"),
    })


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


@pytest.fixture(scope="module")
def profile(frame: pd.DataFrame) -> dict:
    return profile_dataframe(frame)


@pytest.fixture(scope="module")
def findings(frame: pd.DataFrame, profile: dict) -> dict:
    return scan(frame, profile)


def kinds(result: dict) -> dict[str, str]:
    return {f["column"]: f["kind"] for f in result["findings"]}


# --------------------------------------------------------------------------- detection


def test_the_obvious_identifiers_are_all_found(findings: dict) -> None:
    found = kinds(findings)
    assert found["customer_email"] == "email"
    assert found["phone_number"] == "phone"
    assert found["card_number"] == "credit_card"
    assert found["full_name"] == "person_name"
    assert found["client_ip"] == "ip_address"
    assert findings["status"] == "sensitive"


def test_ordinary_business_columns_are_left_alone(findings: dict) -> None:
    found = kinds(findings)
    for column in ("region", "revenue", "order_date", "order_id"):
        assert column not in found


def test_a_long_order_number_is_not_a_card_number() -> None:
    """Luhn is the difference between a finding and a false alarm."""
    frame = pd.DataFrame({"reference_number": [str(1234567890123 + i) for i in range(60)]})
    assert scan(frame, profile_dataframe(frame))["findings"] == []


def test_the_column_name_loosens_the_matcher_without_loosening_it_everywhere() -> None:
    """`phone_number` may hold bare digits; `reference_number` holding the same is not a phone."""
    phones = pd.DataFrame({"phone_number": [str(4155550000 + i) for i in range(40)]})
    assert kinds(scan(phones, profile_dataframe(phones)))["phone_number"] == "phone"
    references = pd.DataFrame({"reference_number": [str(4155550000 + i) for i in range(40)]})
    assert "reference_number" not in kinds(scan(references, profile_dataframe(references)))


def test_an_unambiguous_name_is_enough_on_its_own() -> None:
    frame = pd.DataFrame({"ssn": [f"SSN{i:06d}" for i in range(40)]})
    result = scan(frame, profile_dataframe(frame))
    assert kinds(result)["ssn"] == "national_id"
    assert result["findings"][0]["basis"] == "column name"


def test_a_short_reference_code_is_not_a_postcode() -> None:
    frame = pd.DataFrame({"order_id": [f"ORD-{90000 + i}" for i in range(40)]})
    assert scan(frame, profile_dataframe(frame))["findings"] == []
    # The same values under a name that says postcode are flagged, at low severity.
    named = frame.rename(columns={"order_id": "post_code"})
    finding = scan(named, profile_dataframe(named))["findings"][0]
    assert finding["kind"] == "postcode" and finding["severity"] == "low"


def test_a_matching_name_alone_is_not_enough_for_a_value_detector() -> None:
    frame = pd.DataFrame({"email_campaign": ["spring", "summer", "autumn"] * 20})
    result = scan(frame, profile_dataframe(frame))
    assert kinds(result).get("email_campaign") is None


def test_values_win_even_when_the_column_is_misnamed() -> None:
    frame = pd.DataFrame({"contact": [f"user{i}@mail.test" for i in range(40)]})
    result = scan(frame, profile_dataframe(frame))
    assert kinds(result)["contact"] == "email"
    assert result["findings"][0]["basis"] == "values"


def test_name_only_detectors_check_the_column_type() -> None:
    dated = pd.DataFrame({"date_of_birth": pd.date_range("1980-01-01", periods=40, freq="365D")})
    assert kinds(scan(dated, profile_dataframe(dated)))["date_of_birth"] == "date_of_birth"
    # The same name over a numeric column is something else, and is not flagged.
    numeric = pd.DataFrame({"date_of_birth": list(range(40))})
    assert "date_of_birth" not in kinds(scan(numeric, profile_dataframe(numeric)))


def test_no_raw_value_is_ever_returned_by_the_scan(findings: dict) -> None:
    payload = json.dumps(findings)
    for secret in EMAILS[:3] + PHONES[:3] + CARDS[:3] + NAMES[:3]:
        assert secret not in payload
    shapes = {f["column"]: f["shape"] for f in findings["findings"]}
    # A shape shows the structure and nothing else.
    assert shapes["customer_email"] == "aaaaaa9@aaaaaaa.aaa"


def test_the_suggestion_matches_the_risk(findings: dict) -> None:
    suggested = findings["suggested_policy"]
    assert suggested["card_number"] == "drop"
    assert suggested["customer_email"] == "hash"
    assert suggested["full_name"] == "mask"
    assert "region" not in suggested


def test_an_inherited_policy_wins_over_the_suggestion(frame: pd.DataFrame, profile: dict) -> None:
    previous = {"policy": {"customer_email": "drop"}, "salt": "abc"}
    result = scan(frame, profile, previous)
    assert result["suggested_policy"]["customer_email"] == "drop"


# --------------------------------------------------------------------------- policy


def test_a_policy_is_validated() -> None:
    with pytest.raises(InvalidInputError, match="not a privacy action"):
        normalize({"customer_email": "encrypt"})
    with pytest.raises(InvalidInputError, match="mapping"):
        normalize(["customer_email"])
    # "keep" is the absence of an action, not an action.
    assert normalize({"a": "keep", "b": "mask"})["policy"] == {"b": "mask"}


def test_a_salt_appears_only_when_hashing_and_then_persists() -> None:
    assert normalize({"a": "mask"})["salt"] is None
    state = normalize({"a": "hash"})
    assert state["salt"]
    # Re-editing keeps the salt, or every pseudonym in the table would change.
    assert normalize({"a": "hash", "b": "mask"}, previous=state)["salt"] == state["salt"]


# --------------------------------------------------------------------------- redaction


def test_dropping_removes_the_column(frame: pd.DataFrame) -> None:
    redacted, applied = apply_policy(frame, normalize({"card_number": "drop"}))
    assert "card_number" not in redacted.columns
    assert applied[0]["status"] == "applied"
    assert len(redacted) == len(frame)


def test_hashing_is_stable_and_irreversible(frame: pd.DataFrame) -> None:
    state = normalize({"customer_email": "hash"})
    first, _ = apply_policy(frame, state)
    second, _ = apply_policy(frame, state)
    assert first["customer_email"].tolist() == second["customer_email"].tolist()
    # The pseudonym preserves the grouping the original had — which is what keeps
    # cohort and retention analysis working on a redacted table.
    assert first["customer_email"].nunique() == frame["customer_email"].nunique()
    assert not set(first["customer_email"]) & set(frame["customer_email"])
    assert all(value.startswith("id_") for value in first["customer_email"])


def test_a_different_salt_gives_different_pseudonyms(frame: pd.DataFrame) -> None:
    one, _ = apply_policy(frame, {"policy": {"customer_email": "hash"}, "salt": "one"})
    two, _ = apply_policy(frame, {"policy": {"customer_email": "hash"}, "salt": "two"})
    assert one["customer_email"].iloc[0] != two["customer_email"].iloc[0]


def test_masking_keeps_the_shape_and_loses_the_content(frame: pd.DataFrame) -> None:
    redacted, _ = apply_policy(frame, normalize({"customer_email": "mask", "phone_number": "mask"}))
    email = redacted["customer_email"].iloc[0]
    assert "@" in email and email.endswith(".com") and "person0" not in email
    phone = redacted["phone_number"].iloc[0]
    assert phone.endswith("1000") and "415" not in phone


def test_masking_leaves_missing_values_missing() -> None:
    frame = pd.DataFrame({"full_name": ["Ada Lovelace", None, "Alan Turing"]})
    redacted, _ = apply_policy(frame, normalize({"full_name": "mask"}))
    assert redacted["full_name"].isna().sum() == 1
    assert "Ada" not in str(redacted["full_name"].iloc[0])


def test_mask_value_never_returns_the_input() -> None:
    for value in ("ada@example.com", "+1 415 555 0000", "Ada Lovelace", "4111111111111111"):
        assert mask_value(value) != value


def test_a_column_that_vanished_is_reported_not_raised(frame: pd.DataFrame) -> None:
    _, applied = apply_policy(frame, normalize({"gone": "drop"}))
    assert applied[0]["status"] == "missing"


def test_an_empty_policy_is_a_no_op(frame: pd.DataFrame) -> None:
    redacted, applied = apply_policy(frame, normalize(None))
    assert applied == []
    assert list(redacted.columns) == list(frame.columns)


# --------------------------------------------------------------------------- the prompt


def test_shielding_removes_examples_but_keeps_the_schema(frame: pd.DataFrame, profile: dict,
                                                         findings: dict) -> None:
    shielded = shield_profile(profile, protected_columns(findings))
    by_name = {c["name"]: c for c in shielded["columns"]}
    assert by_name["customer_email"]["sensitive"] is True
    assert by_name["customer_email"]["sample_values"] == ["(withheld)"]
    # The model still knows the column exists, what it is called and what type it is.
    assert by_name["customer_email"]["role"] == "identifier"
    assert by_name["region"].get("sensitive") is None
    assert by_name["region"]["top_values"][0]["value"] == "North"


def test_no_personal_value_survives_into_the_schema_card(frame: pd.DataFrame, profile: dict,
                                                         findings: dict) -> None:
    """This is the guarantee: the card is what the model is given."""
    shielded = shield_profile(profile, protected_columns(findings))
    card = dataset_context("Customers", shielded, {"quality_score": 90, "actions": []})
    for secret in EMAILS[:5] + PHONES[:5] + CARDS[:5] + NAMES[:3]:
        assert secret not in card
    # And the columns themselves are still described, so the analysis still works.
    assert "`customer_email`" in card
    assert "North" in card
    # The model is told why it cannot see inside them, rather than left to guess.
    assert "WITHHELD" in card


def test_only_high_severity_columns_are_withheld_by_default(findings: dict) -> None:
    protected = protected_columns(findings)
    assert "customer_email" in protected and "card_number" in protected
    assert "client_ip" not in protected  # medium: flagged for review, not withheld
    assert "client_ip" in protected_columns(findings, minimum="medium")


# --------------------------------------------------------------------------- markdown


def test_markdown_names_every_finding_and_its_action(findings: dict) -> None:
    state = normalize({"card_number": "drop", "customer_email": "hash"})
    markdown = privacy_markdown(findings, state, "Customers")
    assert markdown.startswith("# Privacy review")
    assert "`card_number`" in markdown and "drop" in markdown
    assert "Policy set, not yet applied" in markdown
    assert "no model call" in markdown
    for secret in EMAILS[:3] + CARDS[:3]:
        assert secret not in markdown
