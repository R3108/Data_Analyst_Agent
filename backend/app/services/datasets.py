"""Dataset lifecycle: upload → ingest → clean → profile → persist."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError, PayloadTooLargeError
from app.core.serialization import frame_to_records
from app.db import Database, new_id, utcnow
from app.services import contracts as contract_service
from app.services.cleaning import clean_dataframe
from app.services.ingestion import detect_file_type, read_table
from app.services.profiling import profile_dataframe
from app.services.semantics import normalize as normalize_semantics
from app.services.signals import detect_signals
from app.services.versions import diff_datasets

logger = logging.getLogger(__name__)
CHUNK = 1024 * 1024


class DatasetService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        settings.datasets_dir.mkdir(parents=True, exist_ok=True)

    def ingest_stream(self, filename: str, stream: BinaryIO, sheet: str | None = None,
                      replaces: str | None = None) -> dict[str, Any]:
        detect_file_type(filename)  # fail fast before reading the body
        limit = self.settings.max_upload_mb * CHUNK
        suffix = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            size = 0
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    tmp.close()
                    Path(tmp.name).unlink(missing_ok=True)
                    raise PayloadTooLargeError(
                        f"File exceeds the {self.settings.max_upload_mb} MB upload limit."
                    )
                tmp.write(chunk)
        try:
            if size == 0:
                raise InvalidInputError("The uploaded file is empty.")
            return self.ingest_path(Path(tmp.name), filename, sheet=sheet, replaces=replaces)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def ingest_path(self, path: Path, filename: str, sheet: str | None = None,
                    name: str | None = None, replaces: str | None = None) -> dict[str, Any]:
        ingest = read_table(path, filename, sheet)
        raw = ingest.frame
        if len(raw) > self.settings.max_rows:
            raise PayloadTooLargeError(
                f"Dataset has {len(raw):,} rows; the limit is {self.settings.max_rows:,}."
            )
        if raw.shape[1] > self.settings.max_columns:
            raise PayloadTooLargeError(
                f"Dataset has {raw.shape[1]} columns; the limit is {self.settings.max_columns}."
            )

        clean, cleaning = clean_dataframe(raw)
        if clean.empty:
            raise InvalidInputError("No usable rows remain after removing empty rows.")
        cleaning["ingestion"] = {
            "encoding": ingest.encoding,
            "delimiter": ingest.delimiter,
            "sheet_name": ingest.sheet_name,
            "sheet_names": ingest.sheet_names,
            "skipped_malformed_lines": ingest.skipped_lines,
            "header_row": ingest.header_row,
        }
        if ingest.skipped_lines:
            cleaning["actions"].insert(0, {
                "step": "skip_malformed_lines", "column": None, "affected": ingest.skipped_lines,
                "detail": f"Skipped {ingest.skipped_lines:,} malformed line(s) with the wrong number of fields",
            })
        profile = profile_dataframe(clean)
        profile["signals"] = detect_signals(clean, profile)

        dataset_id = new_id("ds")
        target_dir = self.settings.datasets_dir / dataset_id
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            clean.to_parquet(target_dir / "clean.parquet", index=False)
            shutil.copyfile(path, target_dir / f"raw{Path(filename).suffix.lower()}")
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

        record = {
            "id": dataset_id,
            "name": name or Path(filename).stem.replace("_", " ").replace("-", " ").strip().title(),
            "original_filename": filename,
            "file_type": ingest.file_type,
            "sheet_name": ingest.sheet_name,
            "n_rows": int(len(clean)),
            "n_cols": int(clean.shape[1]),
            "size_bytes": int(path.stat().st_size),
            "created_at": utcnow(),
            "parent_dataset_id": None,
            "root_dataset_id": dataset_id,
            "version": 1,
        }

        # A replacement upload continues the previous dataset's history: it inherits the
        # semantic layer and the data contract, and gains a diff against what it supersedes.
        semantics: dict[str, Any] | None = None
        contract: dict[str, Any] | None = None
        version_diff: dict[str, Any] | None = None
        if replaces:
            previous = self.latest_version(replaces)
            record.update(
                name=name or previous["name"],
                parent_dataset_id=previous["id"],
                root_dataset_id=previous.get("root_dataset_id") or previous["id"],
                version=int(previous.get("version") or 1) + 1,
            )
            semantics = previous.get("semantics")
            contract = previous.get("contract")
            try:
                version_diff = diff_datasets(previous, {**record, "profile": profile, "cleaning": cleaning})
            except Exception:  # noqa: BLE001 — a diff is a nicety, never a blocker
                logger.warning("Could not diff dataset %s against %s", dataset_id, previous["id"],
                               exc_info=True)

        # The contract is the promise the *previous* upload made; checking it here is what
        # makes a re-upload verified rather than merely diffed.
        contract_result = contract_service.evaluate(contract, clean, profile) if contract else None

        self.db.insert_dataset({
            **record,
            "profile_json": json.dumps(profile),
            "cleaning_json": json.dumps(cleaning),
            "semantics_json": json.dumps(semantics) if semantics else None,
            "version_diff_json": json.dumps(version_diff) if version_diff else None,
            "contract_json": json.dumps(contract) if contract else None,
            "contract_result_json": json.dumps(contract_result) if contract_result else None,
        })
        logger.info("Ingested dataset %s v%s (%s rows × %s cols)%s", dataset_id, record["version"],
                    record["n_rows"], record["n_cols"],
                    f" — contract {contract_result['status']}" if contract_result else "")
        return {**record, "profile": profile, "cleaning": cleaning, "semantics": semantics,
                "version_diff": version_diff, "contract": contract, "contract_result": contract_result}

    def get(self, dataset_id: str) -> dict[str, Any]:
        record = self.db.get_dataset(dataset_id)
        if record is None:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")
        if "signals" not in record["profile"]:
            # Datasets ingested before signals existed get them computed once, lazily.
            try:
                record["profile"]["signals"] = detect_signals(self.load_frame(dataset_id), record["profile"])
                self.db.update_dataset_profile(dataset_id, record["profile"])
            except NotFoundError:
                record["profile"]["signals"] = []
        return record

    def latest_version(self, dataset_id: str) -> dict[str, Any]:
        """The newest upload in the same lineage — what a monitor should measure."""
        record = self.db.latest_dataset_version(dataset_id)
        if record is None:
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")
        return record

    def versions(self, dataset_id: str) -> list[dict[str, Any]]:
        record = self.get(dataset_id)
        return self.db.dataset_versions(record.get("root_dataset_id") or record["id"])

    def set_semantics(self, dataset_id: str, payload: Any) -> dict[str, Any]:
        """Replace the dataset's metric definitions, rules and glossary."""
        self.get(dataset_id)
        semantics = normalize_semantics(payload)
        self.db.update_dataset_semantics(dataset_id, semantics)
        return semantics

    def get_semantics(self, dataset_id: str) -> dict[str, Any]:
        return self.get(dataset_id).get("semantics") or normalize_semantics(None)

    # --- data contract ------------------------------------------------------------
    def get_contract(self, dataset_id: str) -> dict[str, Any]:
        record = self.get(dataset_id)
        return {
            "contract": record.get("contract") or dict(contract_service.EMPTY),
            "result": record.get("contract_result"),
            "suggested": contract_service.is_empty(record.get("contract")),
        }

    def suggest_contract(self, dataset_id: str) -> dict[str, Any]:
        """A proposed contract inferred from the profile. Nothing is saved."""
        return contract_service.suggest(self.get(dataset_id)["profile"])

    def set_contract(self, dataset_id: str, payload: Any) -> dict[str, Any]:
        """Replace the expectations and immediately check them against this version."""
        record = self.get(dataset_id)
        contract = contract_service.normalize(payload)
        result = (
            contract_service.evaluate(contract, self.load_frame(dataset_id), record["profile"])
            if not contract_service.is_empty(contract)
            else None
        )
        self.db.update_dataset_contract(dataset_id, contract or None, result)
        return {"contract": contract, "result": result, "suggested": False}

    def check_contract(self, dataset_id: str) -> dict[str, Any]:
        record = self.get(dataset_id)
        contract = record.get("contract")
        result = contract_service.evaluate(contract, self.load_frame(dataset_id), record["profile"])
        self.db.update_dataset_contract(dataset_id, contract, result)
        return {"contract": contract or dict(contract_service.EMPTY), "result": result,
                "suggested": contract_service.is_empty(contract)}

    def export_bytes(self, dataset_id: str, fmt: str = "csv") -> tuple[bytes, str, str]:
        """The cleaned table as bytes, so exported notebooks can reproduce every figure."""
        if fmt not in ("csv", "parquet"):
            raise InvalidInputError("Supported download formats are 'csv' and 'parquet'.")
        path = self.data_path(dataset_id)
        if fmt == "parquet":
            return path.read_bytes(), "application/octet-stream", "parquet"
        return (self.load_frame(dataset_id).to_csv(index=False).encode("utf-8"),
                "text/csv; charset=utf-8", "csv")

    def data_path(self, dataset_id: str) -> Path:
        path = self.settings.datasets_dir / dataset_id / "clean.parquet"
        if not path.exists():
            raise NotFoundError(f"Data file for dataset '{dataset_id}' is missing.")
        return path

    def load_frame(self, dataset_id: str) -> pd.DataFrame:
        return pd.read_parquet(self.data_path(dataset_id))

    def preview(self, dataset_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        self.get(dataset_id)
        df = self.load_frame(dataset_id)
        page = df.iloc[offset : offset + limit]
        payload = frame_to_records(page)
        payload.update(total_rows=int(len(df)), offset=offset, limit=limit)
        return payload

    def delete(self, dataset_id: str) -> None:
        if not self.db.delete_dataset(dataset_id):
            raise NotFoundError(f"Dataset '{dataset_id}' was not found.")
        shutil.rmtree(self.settings.datasets_dir / dataset_id, ignore_errors=True)
