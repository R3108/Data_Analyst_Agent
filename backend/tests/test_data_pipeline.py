from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from app.services.cleaning import clean_dataframe
from app.services.ingestion import read_table
from app.services.profiling import dataset_context, profile_dataframe


def test_cleaning_fixes_common_mess():
    raw = pd.DataFrame({
        " Order Date ": ["2024-01-05", "Jan 06, 2024", "", "2024-01-05", ""],
        "Revenue": ["$1,200.50", "300", "N/A", "$1,200.50", ""],
        "Discount": ["10%", "5%", "0%", "10%", ""],
        "Region": ["North", " north ", "NORTH", "North", ""],
        "Returned": ["Yes", "No", "No", "Yes", ""],
        "Zip": ["02134", "10001", "02134", "02134", ""],
        "Empty": ["", "", "", "", ""],
    }, dtype=str)

    clean, report = clean_dataframe(raw)

    assert list(clean.columns) == ["Order Date", "Revenue", "Discount", "Region", "Returned", "Zip"]
    assert pd.api.types.is_datetime64_any_dtype(clean["Order Date"])
    assert clean["Revenue"].iloc[0] == 1200.5
    assert clean["Revenue"].isna().sum() == 1
    assert clean["Discount"].iloc[0] == 0.10
    assert set(clean["Region"].dropna()) == {"North"}
    assert clean["Returned"].dtype == bool
    assert clean["Zip"].iloc[0] == "02134"  # leading zeros preserved
    assert report["duplicates_removed"] == 1
    assert report["rows_after"] == 3
    assert report["type_conversions"]["Revenue"] == "number"
    steps = {a["step"] for a in report["actions"]}
    assert {"drop_empty_columns", "drop_empty_rows", "convert_numeric", "unify_categories"} <= steps
    assert 0 <= report["quality_score"] <= 100


def test_mixed_date_formats_are_fully_recovered():
    raw = pd.DataFrame({"Order Date": ["2024-01-05"] * 17 + ["Mar 05, 2024"] * 3}, dtype=str)
    clean, report = clean_dataframe(raw)
    assert pd.api.types.is_datetime64_any_dtype(clean["Order Date"])
    assert clean["Order Date"].notna().all()
    assert report["invalid_values_coerced"] == 0


def test_profile_prefers_revenue_over_price():
    df = pd.DataFrame({
        "Unit Price": [10.0 + i for i in range(60)],
        "Revenue": [100.0 * i for i in range(60)],
        "Region": ["North", "South", "East"] * 20,
    })
    profile = profile_dataframe(df)
    assert "Revenue" in profile["suggested_questions"][0]
    labels = [h["label"] for h in profile["highlights"]]
    assert "Total Revenue" in labels and "Total Unit Price" not in labels


def test_csv_ingestion_detects_delimiter_and_encoding(tmp_path: Path):
    path = tmp_path / "euro.csv"
    path.write_bytes("Città;Vendite\nMilano;10\nRoma;20\n".encode("cp1252"))
    result = read_table(path, "euro.csv")
    assert result.delimiter == ";"
    assert list(result.frame.columns) == ["Città", "Vendite"]
    assert len(result.frame) == 2


def test_excel_ingestion_promotes_header_below_title(tmp_path: Path):
    path = tmp_path / "report.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Quarterly sales report"])
    ws.append([])
    ws.append(["Region", "Sales", "Units"])
    ws.append(["North", 100, 3])
    ws.append(["South", 80, 2])
    wb.save(path)

    result = read_table(path, "report.xlsx")
    assert list(result.frame.columns) == ["Region", "Sales", "Units"]
    assert len(result.frame) == 2


def test_profile_assigns_roles_and_suggestions():
    df = pd.DataFrame({
        "order_id": [f"ORD-{i}" for i in range(100)],
        "order_date": pd.date_range("2024-01-01", periods=100, freq="D"),
        "region": ["North", "South", "East", "West"] * 25,
        "revenue": [float(i) for i in range(100)],
    })
    profile = profile_dataframe(df)
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    assert roles == {"order_id": "identifier", "order_date": "datetime", "region": "dimension", "revenue": "measure"}
    assert profile["suggested_questions"]
    context = dataset_context("Test", profile, {"quality_score": 100, "actions": []})
    assert "`revenue`" in context and "SAMPLE ROWS" in context


def test_sample_dataset_cleans_end_to_end():
    sample = Path(__file__).resolve().parents[2] / "sample_data" / "retail_sales.csv"
    result = read_table(sample, sample.name)
    clean, report = clean_dataframe(result.frame)
    assert pd.api.types.is_datetime64_any_dtype(clean["Order Date"])
    assert clean["Order Date"].notna().all()
    assert pd.api.types.is_numeric_dtype(clean["Revenue"])
    assert "Unit Price" in clean.columns
    assert report["duplicates_removed"] >= 25
