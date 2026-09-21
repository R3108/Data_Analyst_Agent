"""Application settings, loaded from environment variables and `.env` files."""

from __future__ import annotations

import os
import re
import secrets
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
    # The origins the browser app is served from. Used both for CORS and to verify the
    # `Origin` header on every state-changing request, so this is a trust list, not a
    # convenience — do not widen it to make a deployment "just work".
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Turn on only when the server sits behind a reverse proxy that sets the header
    # itself. Exposed directly, `X-Forwarded-For` is attacker-controlled, and trusting
    # it hands every guess its own rate-limit bucket.
    trust_forwarded_for: bool = False

    # --- Accounts and sessions ------------------------------------------------------
    # Keys session and password-reset tokens at rest, so a stolen copy of auth.db cannot
    # be replayed against a running server. Required in production; a development server
    # derives an ephemeral one, which means restarting it signs everybody out.
    auth_secret: str | None = None
    # Idle timeout: a session that goes unused for this long stops working.
    session_idle_days: int = Field(default=7, ge=1, le=365)
    # Hard ceiling, never extended by activity. A stolen cookie expires on its own.
    session_absolute_days: int = Field(default=30, ge=1, le=365)
    session_cookie_name: str = "numera_session"
    csrf_cookie_name: str = "numera_csrf"
    # None means "Secure in production, not in development", which is what you want: the
    # flag is mandatory over HTTPS and would stop the cookie being set over plain HTTP.
    cookie_secure: bool | None = None
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    # Leave unset for a host-only cookie. Set (".example.com") only when the API and the
    # app are on different subdomains of one site.
    cookie_domain: str | None = None

    # Open sign-up. Turn off for an invite-only deployment: an admin then creates
    # accounts from the dashboard and the sign-up form disappears from the UI.
    registration_enabled: bool = True
    # Comma-separated list. Empty means any address; "acme.com" restricts sign-up to it.
    registration_allowed_domains: str = ""
    # 0 means unlimited. A cheap seat cap for a small hosted plan.
    max_users: int = Field(default=0, ge=0)

    # The first account, created on an empty database so a fresh deployment is never
    # unreachable. Without a password set here the first person to register becomes the
    # admin instead, which is the right behaviour for a self-hosted install.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # --- Sign in with Google ----------------------------------------------------------
    # OpenID Connect through Google. Off until both are set. Create an OAuth client of
    # type "Web application" in Google Cloud Console and register the redirect URI
    # below as an authorised redirect URI, character for character.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # Defaults to PUBLIC_BASE_URL + /api/auth/google/callback, which is right for the
    # standard same-origin deployment where Next rewrites /api to this server.
    google_redirect_uri: str | None = None

    # --- Abuse limits ---------------------------------------------------------------
    login_max_attempts: int = Field(default=10, ge=1, le=1000)
    login_window_minutes: int = Field(default=15, ge=1, le=1440)
    # Consecutive failures against one account before it is temporarily locked. This is
    # per account rather than per IP, so a distributed guessing attack is still stopped.
    lockout_threshold: int = Field(default=8, ge=3, le=100)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)
    register_max_per_hour: int = Field(default=5, ge=1, le=1000)
    reset_max_per_hour: int = Field(default=5, ge=1, le=100)
    reset_token_ttl_minutes: int = Field(default=30, ge=5, le=1440)

    # How many per-user workspaces stay warm in memory. Each is a handful of service
    # objects and no open file handle, so this is a latency knob, not a limit on users.
    workspace_cache_size: int = Field(default=128, ge=8, le=4096)

    # Development convenience: return the reset link in the API response when no SMTP
    # host is configured. Ignored in production, where it would be a takeover vector.
    expose_reset_link: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_origins(self) -> list[str]:
        """Origins the browser app may be served from, for `Origin` verification.

        `PUBLIC_BASE_URL` is included because it already names the app's public
        address — it is what an alert's "open in Numera" link points at. Behind the
        default same-origin setup, where Next rewrites `/api` to this backend, that is
        exactly the origin the browser stamps on every write. Deriving it here means an
        operator who sets the URL they already had to set does not then get every
        request refused for a reason that reads like a bug.
        """
        origins = list(self.cors_origin_list)
        base = (self.public_base_url or "").strip().rstrip("/")
        if base and base not in origins:
            origins.append(base)
        return origins

    @property
    def data_root(self) -> Path:
        """Everything the server persists lives under here."""
        return self.data_dir

    @property
    def control_database_path(self) -> Path:
        """Accounts, sessions and the audit trail. The only cross-tenant database."""
        return self.data_dir / "auth.db"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def workspace_dir(self, user_id: str) -> Path:
        """The private data directory for one account.

        `user_id` is server-minted (`usr_<hex>`) and never taken from a request, but it
        is validated here anyway: this value becomes a filesystem path, and a path is
        exactly the wrong place to trust an identifier's provenance.
        """
        if not user_id or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", user_id):
            raise ValueError(f"Refusing to build a workspace path from {user_id!r}.")
        return self.users_dir / user_id

    @property
    def database_path(self) -> Path:
        return self.data_dir / "numera.db"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def allowed_signup_domains(self) -> list[str]:
        return [d.strip().lower().lstrip("@") for d in self.registration_allowed_domains.split(",") if d.strip()]

    @property
    def google_enabled(self) -> bool:
        return bool((self.google_client_id or "").strip() and (self.google_client_secret or "").strip())

    @property
    def resolved_google_redirect_uri(self) -> str:
        explicit = (self.google_redirect_uri or "").strip()
        if explicit:
            return explicit
        return f"{(self.public_base_url or '').rstrip('/')}/api/auth/google/callback"

    @property
    def llm_credentials_detected(self) -> bool:
        return bool(
            self.openai_api_key
            or os.environ.get("OPENAI_API_KEY")
        )

    def resolved_auth_secret(self) -> str:
        """The key session and reset tokens are digested with.

        Production refuses to start without one rather than silently generating a
        per-process secret: that would appear to work, and then sign every user out on
        each restart and on every extra worker.
        """
        secret = (self.auth_secret or "").strip()
        if secret:
            if len(secret) < 32:
                raise RuntimeError(
                    "AUTH_SECRET must be at least 32 characters. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            return secret
        if self.is_production:
            raise RuntimeError(
                "AUTH_SECRET is required when ENVIRONMENT=production. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return _ephemeral_secret()


@lru_cache
def _ephemeral_secret() -> str:
    """One random key per process, for development only. Cached so it is stable."""
    return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
