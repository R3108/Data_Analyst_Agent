"""The verifier is what makes an AI answer auditable, so it gets its own tests."""

from __future__ import annotations

from typing import Any

from app.services.verification import verify_analysis

PROFILE = {
    "n_rows": 100,
    "columns": [
        {"name": "Region", "dtype": "text", "role": "dimension", "missing_pct": 0.0, "missing": 0,
         "unique": 3},
        {"name": "Revenue", "dtype": "decimal", "role": "measure", "missing_pct": 0.0, "missing": 0,
         "unique": 80},
    ],
}
CLEANING = {"quality_score": 96, "outliers": []}
CODE = 'by_region = df.groupby("Region")["Revenue"].sum()\nkpi("Total revenue", 1750.0)\n'


def execution(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "stdout": "",
        "warnings": [],
        "kpis": [{"label": "Total revenue", "value": 1750.0, "format": "currency", "delta": None,
                  "delta_label": None, "higher_is_better": True, "description": None}],
        "charts": [],
        "tables": [{
            "title": "Revenue by region",
            "columns": [{"name": "Region", "kind": "text"}, {"name": "Revenue", "kind": "number"}],
            "rows": [["North", 1000.0], ["South", 500.0], ["East", 250.0]],
            "total_rows": 3,
            "truncated": False,
        }],
    }
    base.update(overrides)
    return base


def report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "headline": "Total revenue reached $1,750 with North contributing 1,000.",
        "answer_markdown": "**North** leads on 1,000 of the 1,750 total.",
        "insights": [],
        "recommendations": [],
        "caveats": [],
        "follow_up_questions": [],
    }
    base.update(overrides)
    return base


def verify(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "plan": {"restated_question": "What is revenue by region?", "kpis": []},
        "code": CODE,
        "execution": execution(),
        "report": report(),
        "profile": PROFILE,
        "cleaning": CLEANING,
        "semantics": None,
        "attempt_log": [{"attempt": 1, "ok": True}],
        "question": "Revenue by region?",
    }
    kwargs.update(overrides)
    return verify_analysis(**kwargs)


def checks(result: dict[str, Any]) -> set[str]:
    return {finding["check"] for finding in result["findings"]}


def test_a_fully_grounded_answer_passes_every_check():
    result = verify()
    assert result["findings"] == []
    assert result["score"] == 100
    assert result["confidence"] == "high"
    assert result["checks"] > 0
    assert "passed" in result["summary"]


def test_a_number_that_was_never_computed_is_flagged():
    result = verify(report=report(
        headline="Revenue reached $9.9M this year.",
        answer_markdown="Growth was driven by a $9.9M quarter.",
    ))
    assert "number_grounding" in checks(result)
    assert result["score"] < 100
    finding = next(f for f in result["findings"] if f["check"] == "number_grounding")
    assert "$9.9M" in finding["detail"]


def test_compact_and_percent_renderings_count_as_grounded():
    """"$1.2M" for 1,234,567 and "23.4%" for 0.234 are the same claim, not a new number."""
    result = verify(
        execution=execution(
            kpis=[
                {"label": "Revenue", "value": 1_234_567.0, "format": "currency", "delta": None,
                 "delta_label": None, "higher_is_better": True, "description": None},
                {"label": "Margin", "value": 0.234, "format": "percent", "delta": None,
                 "delta_label": None, "higher_is_better": True, "description": None},
            ],
            tables=[],
        ),
        report=report(
            headline="Revenue of $1.2M at a 23.4% margin.",
            answer_markdown="Revenue was **$1.2M** with a margin of **23.4%**.",
        ),
    )
    assert "number_grounding" not in checks(result)


def test_numbers_from_the_question_are_not_treated_as_claims():
    result = verify(
        question="How did revenue compare with our 5000 target?",
        report=report(headline="Revenue of 1,750 fell short of the 5000 target.",
                      answer_markdown="Short of 5000."),
    )
    assert "number_grounding" not in checks(result)


def test_a_percentage_multiplied_twice_is_caught():
    result = verify(execution=execution(kpis=[
        {"label": "Margin", "value": 23.4, "format": "percent", "delta": None, "delta_label": None,
         "higher_is_better": True, "description": None},
    ]))
    finding = next(f for f in result["findings"] if f["check"] == "percent_scale")
    assert finding["severity"] == "high"
    assert result["confidence"] in ("medium", "low")


def test_a_delta_passed_as_a_percentage_is_caught():
    result = verify(execution=execution(kpis=[
        {"label": "Revenue", "value": 1750.0, "format": "currency", "delta": 25.0,
         "delta_label": "vs 2023", "higher_is_better": True, "description": None},
    ]))
    assert "delta_scale" in checks(result)


def test_an_empty_result_is_reported_rather_than_narrated():
    result = verify(execution=execution(kpis=[], tables=[], charts=[]))
    assert "outputs_present" in checks(result)


def test_a_kpi_with_no_value_is_flagged():
    result = verify(execution=execution(kpis=[
        {"label": "Conversion", "value": None, "format": "percent", "delta": None,
         "delta_label": None, "higher_is_better": True, "description": None},
    ]))
    assert "kpi_values_finite" in checks(result)


def test_undisclosed_missing_data_is_flagged_then_cleared_by_a_caveat():
    gappy = {
        "n_rows": 100,
        "columns": [
            {**PROFILE["columns"][0]},
            {"name": "Revenue", "dtype": "decimal", "role": "measure", "missing_pct": 18.0,
             "missing": 18, "unique": 70},
        ],
    }
    flagged = verify(profile=gappy)
    assert "missing_data_disclosed" in checks(flagged)

    disclosed = verify(profile=gappy, report=report(
        caveats=["18% of Revenue values are missing and those rows are excluded."]
    ))
    assert "missing_data_disclosed" not in checks(disclosed)


def test_a_mean_over_outliers_is_flagged():
    result = verify(
        code='mean = df["Revenue"].mean()\n',
        cleaning={"quality_score": 90,
                  "outliers": [{"column": "Revenue", "count": 12, "lower_fence": 0.0,
                                "upper_fence": 900.0}]},
    )
    assert "outlier_sensitive_mean" in checks(result)


def test_shares_that_do_not_add_up_are_flagged():
    result = verify(execution=execution(tables=[{
        "title": "Share of revenue",
        "columns": [{"name": "Region", "kind": "text"}, {"name": "Share", "kind": "number"}],
        "rows": [["North", 0.4], ["South", 0.2], ["East", 0.1]],
        "total_rows": 3,
        "truncated": False,
    }]))
    assert "share_totals" in checks(result)


def test_complete_shares_are_accepted():
    for rows in ([["North", 0.5], ["South", 0.5]], [["North", 60.0], ["South", 40.0]]):
        result = verify(execution=execution(tables=[{
            "title": "Share of revenue",
            "columns": [{"name": "Region", "kind": "text"}, {"name": "Share", "kind": "number"}],
            "rows": rows, "total_rows": 2, "truncated": False,
        }]))
        assert "share_totals" not in checks(result)


def test_a_business_definition_the_code_ignored_is_flagged():
    semantics = {
        "metrics": [{"name": "Net revenue", "definition": "Gross sales minus refunds and chargebacks",
                     "format": "currency"}],
        "rules": [],
        "glossary": [],
    }
    ignored = verify(semantics=semantics, question="What was net revenue by region?")
    finding = next(f for f in ignored["findings"] if f["check"] == "metric_definitions")
    assert finding["severity"] == "high"
    assert "Net revenue" in finding["detail"]

    applied = verify(
        semantics=semantics,
        question="What was net revenue by region?",
        code='net = df["Gross sales"].sum() - df["refunds"].sum()\n',
    )
    assert "metric_definitions" not in checks(applied)


def test_definitions_for_unmentioned_metrics_are_not_checked():
    semantics = {
        "metrics": [{"name": "Churn rate", "definition": "Cancelled subscriptions over active ones"}],
        "rules": [], "glossary": [],
    }
    result = verify(semantics=semantics, question="Revenue by region?")
    assert "metric_definitions" not in checks(result)


def test_self_repairs_are_surfaced_without_dominating_the_score():
    result = verify(attempt_log=[
        {"attempt": 1, "ok": False, "error": "KeyError: 'revenue'"},
        {"attempt": 2, "ok": True, "error": None},
    ])
    assert "self_repairs" in checks(result)
    assert result["confidence"] == "high"  # a low-severity note only


def test_a_failed_analysis_is_reported_as_unverified():
    result = verify(execution={"ok": False, "kpis": [], "charts": [], "tables": []})
    assert result["confidence"] == "unverified"
    assert result["score"] is None
    assert result["findings"] == []
