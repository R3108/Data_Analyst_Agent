"""Analysis recall and cross-dataset routing."""

from __future__ import annotations

from app.services import memory


def entry(question: str, headline: str = "", created_at: str = "2025-03-01T00:00:00+00:00",
          message_id: str = "msg_1") -> dict:
    return {
        "message_id": message_id, "session_id": "ses_1", "session_title": question,
        "dataset_id": "ds_1", "dataset_name": "Sales", "created_at": created_at,
        "question": question, "headline": headline, "kpis": [], "verification_score": 92,
    }


CORPUS = [
    entry("What is total revenue by region?", "North leads with $1.2M.", message_id="m1"),
    entry("How has revenue trended month over month?", "Revenue grew 8% MoM.", message_id="m2"),
    entry("Which products have the highest return rate?", "Widgets return at 12%.", message_id="m3"),
    entry("Who are our top customers by lifetime value?", "Acme is the largest.", message_id="m4"),
]


# --------------------------------------------------------------------------- tokenizing


def test_tokenize_matches_column_names_to_question_words():
    assert memory.tokenize("net_revenue") == ["net", "revenue"]
    assert memory.tokenize("netRevenue") == ["net", "revenue"]
    assert memory.tokenize("Order-Date") == ["order", "date"]
    # Stopwords and very short words carry no signal.
    assert memory.tokenize("what is the of a") == []


def test_tokenize_handles_nothing():
    assert memory.tokenize(None) == []
    assert memory.tokenize("") == []


# --------------------------------------------------------------------------- recall


def test_recall_finds_the_same_question_asked_differently():
    matches = memory.recall(CORPUS, "total revenue broken down by region")
    assert matches
    assert matches[0]["question"] == "What is total revenue by region?"
    assert "revenue" in matches[0]["matched_terms"]
    assert matches[0]["similarity"] > memory.MIN_SIMILARITY


def test_recall_marks_a_near_identical_question_as_a_duplicate():
    matches = memory.recall(CORPUS, "What is total revenue by region?")
    assert matches[0]["duplicate"] is True
    summary = memory.summarize(matches)
    assert summary["duplicate"] is True
    assert "asked this before" in summary["headline"]


def test_recall_returns_nothing_for_an_unrelated_question():
    assert memory.recall(CORPUS, "How many employees are on parental leave?") == []
    assert memory.summarize([]) is None


def test_recall_ignores_a_question_too_short_to_match_on():
    assert memory.recall(CORPUS, "why?") == []


def test_recall_respects_the_limit_and_the_exclusion():
    matches = memory.recall(CORPUS, "revenue by region and revenue trend", limit=1)
    assert len(matches) == 1

    excluded = memory.recall(CORPUS, "What is total revenue by region?",
                             exclude_message_id="m1")
    assert all(m["message_id"] != "m1" for m in excluded)


def test_recall_survives_an_empty_corpus():
    assert memory.recall([], "total revenue by region") == []


# --------------------------------------------------------------------------- prompt block


def test_recall_block_labels_the_figures_as_historical():
    matches = memory.recall(CORPUS, "total revenue by region")
    block = memory.recall_block(matches)

    assert "NOT current figures" in block
    assert "must come from this run" in block
    assert "North leads with $1.2M." in block


def test_recall_block_is_empty_when_nothing_matched():
    assert memory.recall_block([]) == ""


# --------------------------------------------------------------------------- routing


def dataset(name: str, columns: list[str], values: dict[str, list[str]] | None = None) -> dict:
    return {
        "id": f"ds_{name.lower()}", "name": name, "n_rows": 100,
        "profile": {"columns": [
            {"name": column,
             "top_values": [{"value": v} for v in (values or {}).get(column, [])]}
            for column in columns
        ]},
    }


DATASETS = [
    dataset("Retail Sales", ["order_id", "revenue", "region", "product"],
            {"region": ["EMEA", "APAC"]}),
    dataset("Headcount", ["employee_id", "department", "salary", "tenure_months"],
            {"department": ["Engineering", "Sales"]}),
    dataset("Support Tickets", ["ticket_id", "priority", "resolution_hours", "channel"]),
]


def test_routing_picks_the_dataset_whose_columns_answer_the_question():
    ranked = memory.route(DATASETS, "what is revenue by region?")
    assert ranked[0]["name"] == "Retail Sales"
    assert ranked[0]["confident"] is True


def test_routing_matches_on_category_values_not_only_column_names():
    ranked = memory.route(DATASETS, "how many engineering staff do we have")
    # "engineering" only appears as a *value* of department.
    assert ranked[0]["name"] == "Headcount"


def test_routing_reports_the_terms_no_dataset_could_match():
    ranked = memory.route(DATASETS, "what is our churn rate by cohort")
    assert ranked
    unmatched = ranked[0]["unmatched_terms"]
    assert "churn" in unmatched or "cohort" in unmatched
    # Nothing here answers it, so it must not claim confidence.
    assert ranked[0].get("confident") is not True


def test_routing_needs_a_real_question():
    assert memory.route(DATASETS, "hi") == []
    assert memory.route([], "revenue by region") == []


def test_vocabulary_includes_the_semantic_layer():
    record = {**DATASETS[0], "semantics": {
        "metrics": [{"name": "Net revenue", "definition": "gross sales minus refunds"}],
        "glossary": [{"term": "GMV"}],
    }}
    terms = memory.vocabulary(record)
    assert "refunds" in terms
    assert "gmv" in terms
