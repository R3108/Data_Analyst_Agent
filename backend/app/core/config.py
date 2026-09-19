"""Application settings, loaded from environment variables and `.env` files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Numera"
    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    # --- LLM -----------------------------------------------------------------
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    # Low is the cost-conscious default; None delegates to the model default.
    analyst_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "low"
    llm_max_tokens: int = 8000
    llm_timeout_s: float = 300.0
    # Set to 0 to disable. New analyses are blocked after the calendar-month
    # estimate reaches this amount; the request that crosses it can still finish.
    ai_monthly_budget_usd: float = Field(default=5.0, ge=0)

    # --- Storage & limits ------------------------------------------------------
    data_dir: Path = BACKEND_ROOT / "data"
    sample_data_path: Path = PROJECT_ROOT / "sample_data" / "retail_sales.csv"
    max_upload_mb: int = 50
    max_rows: int = 2_000_000
    max_columns: int = 500

    # --- Agent -----------------------------------------------------------------
    max_repair_attempts: int = Field(default=2, ge=0, le=5)
    history_turns: int = Field(default=6, ge=0, le=20)

    # --- Analysis memory ---------------------------------------------------------
    # Prior answers to a similar question are recalled deterministically (TF-IDF, no
    # embedding model) and handed to the planner as continuity context.
    recall_enabled: bool = True
    recall_limit: int = Field(default=3, ge=1, le=10)

    # --- Investigations ----------------------------------------------------------
    # A deep-research run executes this many analyses plus a scoping and a synthesis
    # call, so its cost is roughly this many ordinary questions plus two.
    investigation_max_steps: int = Field(default=4, ge=1, le=8)

    # --- Scheduled briefings -------------------------------------------------------
    # Sweep interval for saved questions that re-run on a cadence. 0 disables the
    # scheduler; briefings can still be run on demand. Unlike monitors these cost tokens.
    briefing_interval_minutes: int = Field(default=0, ge=0, le=1440)

    # --- Sandbox ---------------------------------------------------------------
    sandbox_timeout_s: float = 60.0
    sandbox_memory_mb: int = 2048

    # --- Monitors ----------------------------------------------------------------
    # Background sweep interval for metric monitors. 0 disables the scheduler; monitors
    # can still be run on demand and always re-run when a new dataset version arrives.
    monitor_interval_minutes: int = Field(default=0, ge=0, le=1440)
    monitor_history_limit: int = Field(default=60, ge=5, le=500)
    # When a monitor breaches, run the deterministic driver drill-down on its measure and
    # attach the result to the run and the alert. Costs one pass over the cleaned table
    # and zero tokens; set to false if a very large table makes the sweep too slow.
    monitor_root_cause: bool = True

    # --- Privacy guard --------------------------------------------------------------
    # Personal data is detected at upload and its example values are withheld from every
    # prompt regardless of this setting. This controls whether the *detector* runs at all.
    privacy_scan_enabled: bool = True

    # --- Alerts ------------------------------------------------------------------
    # Delivery of monitor breaches, recoveries and failed data contracts. Channels are
    # configured in the UI; these settings only gate the transport.
    alerts_enabled: bool = True
    alert_timeout_s: float = Field(default=10.0, gt=0, le=120)
    # 0 disables the scheduled briefing; monitors still alert on every state change.
    alert_digest_hours: int = Field(default=0, ge=0, le=168)
    # Used for the "open in Numera" link inside an alert.
    public_base_url: str = "http://localhost:3000"

    # Email alerts are off unless an SMTP host is configured.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True

    # --- SQL sources ----------------------------------------------------------------
    # Background refresh sweep for connected databases. 0 disables the scheduler; a
    # source can always be synced on demand.
    source_sync_interval_minutes: int = Field(default=0, ge=0, le=1440)

    # --- HTTP ------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Access control -----------------------------------------------------------
    # Empty means the workspace is open, which is the right default for a local tool.
    # Set to "token:Name, other-token:Other Name" to require a bearer token on every API
    # call and to attribute comments and activity to the matching person.
    workspace_tokens: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_path(self) -> Path:
        return self.data_dir / "numera.db"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def llm_credentials_detected(self) -> bool:
        return bool(
            self.openai_api_key
            or os.environ.get("OPENAI_API_KEY")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
