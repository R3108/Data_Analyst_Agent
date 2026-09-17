"""Read CSV / TSV / Excel files into DataFrames robustly.

CSV files are read with every cell as text so that the cleaning pipeline owns
type inference (currency strings, percentages, mixed date formats, ...).
"""

from __future__ import annotations

import csv
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.core.errors import InvalidInputError, UnsupportedFileError

FILE_TYPES: dict[str, str] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
}
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass
class IngestResult:
    frame: pd.DataFrame
    file_type: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    sheet_names: list[str] = field(default_factory=list)
    skipped_lines: int = 0
    header_row: int = 0


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xls":
        raise UnsupportedFileError("Legacy .xls files are not supported. Please re-save the workbook as .xlsx.")
    if suffix not in FILE_TYPES:
        allowed = ", ".join(sorted(FILE_TYPES))
        raise UnsupportedFileError(f"Unsupported file type '{suffix or 'unknown'}'. Upload one of: {allowed}.")
    return FILE_TYPES[suffix]


def read_table(path: Path, filename: str, sheet: str | None = None) -> IngestResult:
    file_type = detect_file_type(filename)
    result = _read_csv(path) if file_type == "csv" else _read_excel(path, sheet)
    result.frame, result.header_row = _promote_header_if_needed(result.frame)
    if result.frame.empty or result.frame.shape[1] == 0:
        raise InvalidInputError("The file was read successfully but contains no tabular data.")
    return result


def _read_csv(path: Path) -> IngestResult:
    raw = path.read_bytes()[:128_000]
    if b"\x00" in raw[:4096]:
        raise UnsupportedFileError("This file looks binary, not a CSV/TSV text file.")

    encoding = next((enc for enc in ENCODINGS if _decodes(raw, enc)), "latin-1")
    sample = raw.decode(encoding, errors="replace")
    delimiter = _sniff_delimiter(sample)

    bad_lines: list[list[str]] = []

    def on_bad_line(line: list[str]) -> None:
        bad_lines.append(line)
        return None

    try:
        frame = pd.read_csv(
            path,
            sep=delimiter,
            encoding=encoding,
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=True,
            on_bad_lines=on_bad_line,
            engine="python",
        )
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        raise InvalidInputError(f"Could not parse the CSV file: {exc}") from exc

    return IngestResult(frame=frame, file_type="csv", encoding=encoding,
                        delimiter=delimiter, skipped_lines=len(bad_lines))


def _read_excel(path: Path, sheet: str | None) -> IngestResult:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            workbook = pd.ExcelFile(path, engine="openpyxl")
            sheet_names = [str(s) for s in workbook.sheet_names]
            if sheet is not None and sheet not in sheet_names:
                raise InvalidInputError(
                    f"Sheet '{sheet}' not found. Available sheets: {', '.join(sheet_names)}."
                )
            candidates = [sheet] if sheet else sheet_names
            for name in candidates:
                frame = workbook.parse(name)
                if not frame.dropna(how="all").empty:
                    return IngestResult(frame=frame, file_type="excel",
                                        sheet_name=name, sheet_names=sheet_names)
    except InvalidInputError:
        raise
    except Exception as exc:  # openpyxl raises a wide variety of errors on corrupt files
        raise InvalidInputError(f"Could not read the Excel workbook: {exc}") from exc
    raise InvalidInputError("Every sheet in this workbook is empty.")


def _decodes(raw: bytes, encoding: str) -> bool:
    try:
        raw.decode(encoding)
        return True
    except UnicodeDecodeError:
        # The sample may cut a multi-byte character in half at the very end.
        try:
            raw[:-4].decode(encoding)
            return True
        except UnicodeDecodeError:
            return False


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample[:32_000], delimiters=",;\t|").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        counts = {d: first_line.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _promote_header_if_needed(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Handle spreadsheets that have title rows above the real header."""
    columns = [str(c) for c in frame.columns]
    unnamed = sum(c.startswith("Unnamed") or not c.strip() for c in columns)
    if unnamed <= len(columns) / 2:
        return frame, 0

    for idx in range(min(20, len(frame))):
        row = frame.iloc[idx]
        filled = row.map(lambda v: v is not None and str(v).strip() not in ("", "nan", "None"))
        if filled.mean() >= 0.6:
            header = [str(v).strip() if filled.iloc[i] else f"column_{i + 1}" for i, v in enumerate(row)]
            body = frame.iloc[idx + 1 :].reset_index(drop=True)
            body.columns = header
            return body, idx + 1
    return frame, 0
