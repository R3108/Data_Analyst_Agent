"""Registration, sign-in, sessions and password recovery.

Everything here follows one rule: **an unauthenticated caller learns nothing about who
has an account.** Sign-in reports one message whether the address is unknown, the
password is wrong or the account is locked. A password-reset request reports success
whether or not the address exists. Verification against a non-existent account still
pays the cost of an Argon2 verify, so the answer cannot be timed either. An attacker
who can enumerate your users has already done half the work of a credential-stuffing
campaign, and the feature that leaks them is almost always a helpful error message.

Sessions are server-side and opaque. The cookie carries 256 bits of randomness and
nothing else — no claims, no user id, no expiry the client can read. Revocation is
therefore immediate: suspending an account, changing a password or signing out a
device takes effect on the very next request, which is not true of a self-contained
token that stays valid until it expires.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

from app.auth.google import PROVIDER as GOOGLE
from app.auth.google import GoogleProfile
from app.auth.store import AuthStore, parse, public_user, stamp, utcnow
from app.core import security
from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)

logger = logging.getLogger(__name__)

# One message for every way a sign-in can fail. See the module docstring.
SIGNIN_FAILED = "That email address and password combination is not recognised."
MAX_NAME_LENGTH = 80


@dataclass(frozen=True)
class RequestMeta:
    """Where a request came from, for the audit trail and for throttling."""

    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class SignedIn:
    """The outcome of a successful sign-in: the account, and the cookie value to set."""

    user: dict[str, Any]
    token: str
    session: dict[str, Any]


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    """Sends through the same SMTP settings the alert channels use."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.smtp_host)

    def send(self, *, to: str, subject: str, body: str) -> None:
        settings = self.settings
        if not settings.smtp_host:
            raise RuntimeError("SMTP is not configured.")
        message = EmailMessage()
        message["Subject"] = subject[:200]
        message["From"] = settings.smtp_from or settings.smtp_user or "numera@localhost"
        message["To"] = to
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.alert_timeout_s) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)


class AuthService:
    def __init__(self, settings: Settings, store: AuthStore, mailer: Mailer | None = None,
                 on_first_account: Callable[[str], None] | None = None) -> None:
        self.settings = settings
        self.store = store
        self.secret = settings.resolved_auth_secret()
        self._mailer = mailer
        self._default_mailer = SmtpMailer(settings)
        # Called once, with the id of the very first account on this deployment. Used to
        # adopt a pre-multi-user `data/numera.db` into it, whichever way that account came
        # into existence — a bootstrap variable or somebody filling in the sign-up form.
        self._on_first_account = on_first_account

    @property
    def mailer(self) -> Mailer | None:
        if self._mailer is not None:
            return self._mailer
        return self._default_mailer if self._default_mailer.configured else None

    # ------------------------------------------------------------ registration
    def register(self, *, email: str, password: str, name: str, meta: RequestMeta) -> SignedIn:
        """Create an account and sign it in. The first account created is the admin."""
        address = self._normalise_email(email)
        display = self._clean_name(name) or address.split("@")[0]
        first_user = self._check_signup_allowed(address)

        try:
            validated = security.validate_password(password, email=address, name=display)
        except security.PasswordPolicyError as exc:
            raise InvalidInputError(str(exc), code="weak_password") from exc

        if self.store.get_user_by_email(address) is not None:
            # Registration is the one place enumeration cannot be avoided — the form has
            # to say the address is taken — so the *rate limit* above is what protects it,
            # and the audit trail records the attempt.
            self.store.record_event(event="register", outcome="conflict", email=address,
                                    ip=meta.ip, user_agent=meta.user_agent)
            raise ConflictError(
                "An account already exists for that email address. Sign in instead, "
                "or reset the password if you have forgotten it.",
                code="email_taken",
            )

        # Throttled here rather than at the top of the method, so the counter measures
        # accounts actually created. A visitor who is told three times that their
        # password is too short has not tried to create three accounts, and locking them
        # out for an hour over it would be a bug wearing a security feature's clothes.
        # The rejected attempts above are cheap — no hashing happens until this point.
        self._throttle(
            f"register:{meta.ip or 'unknown'}",
            limit=self.settings.register_max_per_hour,
            window_s=3600,
            message="Too many accounts created from this address. Try again in an hour.",
        )

        user = self.store.create_user(
            email=address,
            name=display,
            password_hash=security.hasher.hash(validated),
            role="admin" if first_user else "user",
        )
        self.store.record_event(
            event="register", user_id=user["id"], email=address, ip=meta.ip,
            user_agent=meta.user_agent,
            detail="first account — granted admin" if first_user else None,
        )
        if first_user:
            self._announce_first_account(user["id"])
        # Signing up signs the account in, so it counts as its first sign-in. (An account
        # an admin creates keeps `last_login_at` empty until its owner actually signs in.)
        self.store.update_user(user["id"], last_login_at=stamp())
        user = self.store.get_user(user["id"]) or user
        return self._open_session(user, meta)

    def create_account(self, *, email: str, password: str, name: str, role: str = "user",
                       must_change_password: bool = True, actor_id: str | None = None,
                       meta: RequestMeta | None = None) -> dict[str, Any]:
        """Admin-side account creation. Never signs anybody in."""
        meta = meta or RequestMeta()
        address = self._normalise_email(email)
        display = self._clean_name(name) or address.split("@")[0]
        if role not in ("admin", "user"):
            raise InvalidInputError(f"Unknown role '{role}'.")
        if self.store.get_user_by_email(address) is not None:
            raise ConflictError("An account already exists for that email address.",
                                code="email_taken")
        if self.settings.max_users and self.store.count_users() >= self.settings.max_users:
            raise ForbiddenError("This deployment has reached its account limit.",
                                 code="user_limit_reached")
        try:
            validated = security.validate_password(password, email=address, name=display)
        except security.PasswordPolicyError as exc:
            raise InvalidInputError(str(exc), code="weak_password") from exc

        first_user = self.store.count_users() == 0
        user = self.store.create_user(
            email=address, name=display, password_hash=security.hasher.hash(validated),
            role=role, must_change_password=must_change_password,
        )
        self.store.record_event(event="admin.user.create", user_id=user["id"], email=address,
                                actor_id=actor_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail=f"role={role}")
        if first_user:
            self._announce_first_account(user["id"])
        return user

    # ----------------------------------------------------------------- sign-in
    def sign_in(self, *, email: str, password: str, meta: RequestMeta) -> SignedIn:
        address = (email or "").strip().lower()
        self._throttle(
            f"login:{meta.ip or 'unknown'}",
            limit=self.settings.login_max_attempts,
            window_s=self.settings.login_window_minutes * 60,
            message="Too many sign-in attempts. Wait a few minutes and try again.",
        )
        user = self.store.get_user_by_email(address) if address else None

        if user is None:
            # Equalise the cost so a missing account cannot be spotted by how fast the
            # rejection came back.
            security.hasher.verify(password or "", security.DUMMY_HASH)
            self.store.record_event(event="login", outcome="unknown_user", email=address or None,
                                    ip=meta.ip, user_agent=meta.user_agent)
            raise UnauthorizedError(SIGNIN_FAILED, code="invalid_credentials")

        locked_until = parse(user.get("locked_until"))
        if locked_until and locked_until > utcnow():
            self.store.record_event(event="login", outcome="locked", user_id=user["id"],
                                    email=address, ip=meta.ip, user_agent=meta.user_agent)
            # Deliberately the same message as a wrong password: telling an attacker that
            # they have successfully locked an account is telling them it exists.
            raise UnauthorizedError(SIGNIN_FAILED, code="invalid_credentials")

        if not security.hasher.verify(password or "", user["password_hash"]):
            self._register_failure(user, meta)
            raise UnauthorizedError(SIGNIN_FAILED, code="invalid_credentials")

        if user["status"] != "active":
            self.store.record_event(event="login", outcome="suspended", user_id=user["id"],
                                    email=address, ip=meta.ip, user_agent=meta.user_agent)
            # The credentials were right, so this one *can* be specific without leaking
            # anything the caller does not already know.
            raise ForbiddenError(
                "This account has been suspended. Contact an administrator.",
                code="account_suspended",
            )

        updates: dict[str, Any] = {"failed_attempts": 0, "locked_until": None,
                                   "last_login_at": stamp()}
        # The cost of hashing goes up over time; a correct password is the one moment we
        # hold the plaintext and can quietly upgrade the stored digest.
        if security.hasher.needs_rehash(user["password_hash"]):
            updates["password_hash"] = security.hasher.hash(password)
        self.store.update_user(user["id"], **updates)
        self.store.clear_rate_limit(f"login:{meta.ip or 'unknown'}")

        user = self.store.get_user(user["id"]) or user
        self.store.record_event(event="login", user_id=user["id"], email=address, ip=meta.ip,
                                user_agent=meta.user_agent)
        return self._open_session(user, meta)

    def _register_failure(self, user: dict[str, Any], meta: RequestMeta) -> None:
        attempts = int(user.get("failed_attempts") or 0) + 1
        updates: dict[str, Any] = {"failed_attempts": attempts}
        outcome = "bad_password"
        if attempts >= self.settings.lockout_threshold:
            updates["locked_until"] = stamp(utcnow() + timedelta(minutes=self.settings.lockout_minutes))
            updates["failed_attempts"] = 0
            outcome = "locked_out"
        self.store.update_user(user["id"], **updates)
        self.store.record_event(event="login", outcome=outcome, user_id=user["id"],
                                email=user["email"], ip=meta.ip, user_agent=meta.user_agent,
                                detail=f"attempt {attempts}")

    # ------------------------------------------------------ sign in with Google
    def sign_in_with_google(self, profile: GoogleProfile, *, meta: RequestMeta) -> SignedIn:
        """Sign in, link, or create an account for a verified Google identity.

        Resolution order, strictest first:

        1. The Google subject is already linked — that account, whatever its email
           now says. The subject is permanent; an address is not.
        2. An account exists with the same email — linked automatically *only* when
           Google is the authority for that mailbox (Gmail or Workspace). Linking
           signs every existing session on the account out, so anybody who registered
           the address before its real owner arrived loses their foothold.
        3. Nobody — a new account, under exactly the rules the sign-up form obeys.
        """
        self._throttle(
            f"login:{meta.ip or 'unknown'}",
            limit=self.settings.login_max_attempts,
            window_s=self.settings.login_window_minutes * 60,
            message="Too many sign-in attempts. Wait a few minutes and try again.",
        )
        address = self._normalise_email(profile.email)

        linked = self.store.find_identity(GOOGLE, profile.subject)
        if linked is not None:
            user = self.require_user(linked["user_id"])
            self._require_active(user, address, meta)
            self.store.touch_identity(linked["id"], email=address)
            return self._finish_external_sign_in(user, address, meta, detail="google")

        existing = self.store.get_user_by_email(address)
        if existing is not None:
            if not profile.email_is_authoritative:
                self.store.record_event(event="login.google", outcome="link_refused",
                                        user_id=existing["id"], email=address, ip=meta.ip,
                                        user_agent=meta.user_agent)
                raise ConflictError(
                    "An account already uses this email address. Sign in with your password, "
                    "then connect Google from your account page.",
                    code="google_link_required",
                )
            self._require_active(existing, address, meta)
            self._link(existing, profile, address, meta, actor_id=None)
            # Whoever held a session before the mailbox's owner proved themselves loses it.
            self.store.bump_epoch(existing["id"])
            return self._finish_external_sign_in(existing, address, meta, detail="google, linked")

        first_user = self._check_signup_allowed(address)
        self._throttle(
            f"register:{meta.ip or 'unknown'}",
            limit=self.settings.register_max_per_hour,
            window_s=3600,
            message="Too many accounts created from this address. Try again in an hour.",
        )
        display = self._clean_name(profile.name) or address.split("@")[0]
        user = self.store.create_user(
            email=address,
            name=display,
            # No password yet. An empty digest never verifies, and costs the same to
            # reject as a wrong password, so this account cannot be told apart by timing.
            password_hash="",
            role="admin" if first_user else "user",
        )
        self._link(user, profile, address, meta, actor_id=None)
        self.store.record_event(
            event="register", user_id=user["id"], email=address, ip=meta.ip,
            user_agent=meta.user_agent,
            detail="via google" + (" — first account, granted admin" if first_user else ""),
        )
        if first_user:
            self._announce_first_account(user["id"])
        return self._finish_external_sign_in(user, address, meta, detail="google, new account")

    def link_google(self, user_id: str, profile: GoogleProfile, *, meta: RequestMeta) -> None:
        """Connect a Google identity to an account that is already signed in.

        The Google address does not have to match the account's: the owner has just
        proved both, one with a session and one with Google.
        """
        user = self.require_user(user_id)
        linked = self.store.find_identity(GOOGLE, profile.subject)
        if linked is not None:
            if linked["user_id"] == user_id:
                return
            raise ConflictError("That Google account is already connected to another account.",
                                code="google_in_use")
        if any(i["provider"] == GOOGLE for i in self.store.list_identities(user_id)):
            raise ConflictError("A Google account is already connected. Disconnect it first.",
                                code="google_already_linked")
        self._link(user, profile, profile.email, meta, actor_id=user_id)

    def unlink_google(self, user_id: str, *, meta: RequestMeta) -> None:
        """Disconnect Google, unless it is the only way into the account."""
        user = self.require_user(user_id)
        if not user["password_hash"]:
            raise ForbiddenError(
                "Set a password before disconnecting Google, or you will have no way to sign in.",
                code="last_sign_in_method",
            )
        if not self.store.unlink_identity(user_id, GOOGLE):
            raise NotFoundError("No Google account is connected.")
        self.store.record_event(event="oauth.unlink", user_id=user_id, email=user["email"],
                                actor_id=user_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail=GOOGLE)

    def sign_in_methods(self, user_id: str) -> dict[str, Any]:
        user = self.require_user(user_id)
        google = next((i for i in self.store.list_identities(user_id) if i["provider"] == GOOGLE), None)
        return {
            "password": bool(user["password_hash"]),
            "google": (
                {"email": google["email"], "connected_at": google["created_at"],
                 "last_used_at": google["last_used_at"]}
                if google else None
            ),
            "google_available": self.settings.google_enabled,
        }

    def _link(self, user: dict[str, Any], profile: GoogleProfile, address: str,
              meta: RequestMeta, *, actor_id: str | None) -> None:
        try:
            self.store.link_identity(provider=GOOGLE, subject=profile.subject,
                                     user_id=user["id"], email=address)
        except sqlite3.IntegrityError as exc:
            # Lost a race with a concurrent link of the same identity or account.
            raise ConflictError("That Google account is already connected.",
                                code="google_in_use") from exc
        self.store.record_event(event="oauth.link", user_id=user["id"], email=user["email"],
                                actor_id=actor_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail=f"{GOOGLE}: {address}")

    def _require_active(self, user: dict[str, Any], address: str, meta: RequestMeta) -> None:
        if user["status"] != "active":
            self.store.record_event(event="login.google", outcome="suspended", user_id=user["id"],
                                    email=address, ip=meta.ip, user_agent=meta.user_agent)
            raise ForbiddenError("This account has been suspended. Contact an administrator.",
                                 code="account_suspended")

    def _finish_external_sign_in(self, user: dict[str, Any], address: str, meta: RequestMeta,
                                 *, detail: str) -> SignedIn:
        # A password lockout is about guessing the password; proving identity through
        # Google is not a guess, so it ends the lockout the way a correct password would.
        self.store.update_user(user["id"], failed_attempts=0, locked_until=None,
                               last_login_at=stamp())
        self.store.clear_rate_limit(f"login:{meta.ip or 'unknown'}")
        user = self.require_user(user["id"])
        self.store.record_event(event="login", user_id=user["id"], email=address, ip=meta.ip,
                                user_agent=meta.user_agent, detail=detail)
        return self._open_session(user, meta)

    # ---------------------------------------------------------------- sessions
    def start_session(self, user_id: str, meta: RequestMeta) -> SignedIn:
        """Mint a fresh session for an account that is already known to be good.

        Re-reads the account first, so the new session carries the current epoch. That
        matters directly after a password change, which bumps the epoch to sign every
        other device out: a session stamped with the old number would revoke itself.
        """
        return self._open_session(self.require_user(user_id), meta)

    def _open_session(self, user: dict[str, Any], meta: RequestMeta) -> SignedIn:
        token = security.new_token()
        now = utcnow()
        session = self.store.create_session(
            user_id=user["id"],
            token_digest=security.token_digest(token, self.secret),
            epoch=int(user.get("session_epoch") or 1),
            idle_expires_at=now + timedelta(days=self.settings.session_idle_days),
            absolute_expires_at=now + timedelta(days=self.settings.session_absolute_days),
            ip=meta.ip,
            user_agent=meta.user_agent,
        )
        return SignedIn(user=user, token=token, session=session)

    def resolve_session(self, token: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Who a session cookie belongs to, or None. Slides the idle deadline forward.

        Every reason a session can be dead is checked on every request: revoked, idle
        out, past its absolute life, belonging to a suspended or deleted account, or
        minted before the account's epoch was bumped by a password change.
        """
        if not token:
            return None
        record = self.store.find_session(security.token_digest(token, self.secret))
        if record is None or record["revoked_at"]:
            return None

        now = utcnow()
        idle_deadline = parse(record["expires_at"])
        hard_deadline = parse(record["absolute_expires_at"])
        if (idle_deadline and idle_deadline <= now) or (hard_deadline and hard_deadline <= now):
            return None

        user = self.store.get_user(record["user_id"])
        if user is None or user["status"] != "active":
            return None
        if int(record["epoch"]) != int(user.get("session_epoch") or 1):
            return None

        # Slide the idle window, but never past the absolute deadline, and only when it
        # has actually moved — a busy client would otherwise write on every request.
        extended = min(now + timedelta(days=self.settings.session_idle_days),
                       hard_deadline or now + timedelta(days=self.settings.session_idle_days))
        last_seen = parse(record["last_seen_at"]) or now
        if (now - last_seen).total_seconds() > 60:
            self.store.touch_session(record["id"], extended)
        return user, record

    def sign_out(self, token: str | None, *, meta: RequestMeta | None = None) -> None:
        if not token:
            return
        digest = security.token_digest(token, self.secret)
        record = self.store.find_session(digest)
        if self.store.revoke_session_by_digest(digest) and record:
            self.store.record_event(event="logout", user_id=record["user_id"],
                                    ip=(meta.ip if meta else None),
                                    user_agent=(meta.user_agent if meta else None))

    def sign_out_everywhere(self, user_id: str, *, actor_id: str | None = None,
                            meta: RequestMeta | None = None) -> None:
        self.store.bump_epoch(user_id)
        self.store.record_event(event="logout.all", user_id=user_id, actor_id=actor_id,
                                ip=(meta.ip if meta else None),
                                user_agent=(meta.user_agent if meta else None))

    def revoke_session(self, user_id: str, session_id: str) -> None:
        """Sign out one device. Scoped to the owner so an id cannot be guessed across users."""
        owned = [s for s in self.store.list_sessions(user_id) if s["id"] == session_id]
        if not owned:
            raise NotFoundError("That session was not found.")
        self.store.revoke_session(session_id)
        self.store.record_event(event="session.revoke", user_id=user_id, detail=session_id)

    def list_sessions(self, user_id: str, *, current_token: str | None = None) -> list[dict[str, Any]]:
        current = security.token_digest(current_token, self.secret) if current_token else None
        return [
            {
                "id": s["id"],
                "created_at": s["created_at"],
                "last_seen_at": s["last_seen_at"],
                "expires_at": s["expires_at"],
                "ip": s["ip"],
                "user_agent": s["user_agent"],
                "current": s["token_digest"] == current,
            }
            for s in self.store.list_sessions(user_id)
        ]

    # ---------------------------------------------------------------- password
    def change_password(self, user: dict[str, Any], *, current_password: str,
                        new_password: str, meta: RequestMeta) -> None:
        """Change a password from inside the app. Requires the current one.

        An account created through Google has no password to present, so its first one
        is set with the session alone — the same trust a signed-in owner already has.
        """
        if user["password_hash"] and not security.hasher.verify(current_password or "",
                                                                user["password_hash"]):
            self.store.record_event(event="password.change", outcome="bad_current",
                                    user_id=user["id"], ip=meta.ip, user_agent=meta.user_agent)
            raise UnauthorizedError("Your current password is not correct.",
                                    code="invalid_credentials")
        self._set_password(user, new_password, meta=meta, event="password.change")

    def request_reset(self, *, email: str, meta: RequestMeta) -> str | None:
        """Start a password reset. Returns the link only when it is safe to show it.

        The caller always reports success. A returned link means SMTP is unconfigured
        *and* the deployment is a non-production one that opted in with
        `EXPOSE_RESET_LINK`; everywhere else this returns None and the link goes out by
        email, or is logged for the operator if email fails.
        """
        address = (email or "").strip().lower()
        self._throttle(
            f"reset:{address or meta.ip or 'unknown'}",
            limit=self.settings.reset_max_per_hour,
            window_s=3600,
            message="Too many reset requests for that address. Try again in an hour.",
        )
        user = self.store.get_user_by_email(address) if address else None
        if user is None or user["status"] != "active":
            self.store.record_event(event="password.reset.request", outcome="unknown",
                                    email=address or None, ip=meta.ip, user_agent=meta.user_agent)
            return None

        token = security.new_token()
        self.store.create_reset(
            user_id=user["id"],
            token_digest=security.token_digest(token, self.secret),
            expires_at=utcnow() + timedelta(minutes=self.settings.reset_token_ttl_minutes),
            ip=meta.ip,
        )
        link = self.reset_link(token)
        self.store.record_event(event="password.reset.request", user_id=user["id"],
                                email=address, ip=meta.ip, user_agent=meta.user_agent)

        mailer = self.mailer
        if mailer is not None:
            try:
                mailer.send(to=user["email"], subject="Reset your Numera password",
                            body=_reset_email(user["name"], link,
                                              self.settings.reset_token_ttl_minutes))
                return None
            except Exception:  # noqa: BLE001 — a dead mail server must not break the flow
                logger.warning("Could not email a password reset to %s", user["id"], exc_info=True)

        if self.settings.is_production or not self.settings.expose_reset_link:
            # No mail and no safe way to show the link: leave it in the server log, where
            # only the operator can see it, rather than in an HTTP response.
            logger.warning(
                "Password reset requested for %s but no mailer is configured. Link: %s",
                user["id"], link,
            )
            return None
        return link

    def reset_link(self, token: str) -> str:
        base = (self.settings.public_base_url or "").rstrip("/")
        return f"{base}/reset-password?token={quote(token, safe='')}"

    def confirm_reset(self, *, token: str, new_password: str, meta: RequestMeta) -> dict[str, Any]:
        record = self.store.find_reset(security.token_digest(token or "", self.secret))
        expired = record is None or record["used_at"] or (parse(record["expires_at"]) or utcnow()) <= utcnow()
        if expired:
            self.store.record_event(event="password.reset.confirm", outcome="invalid_token",
                                    ip=meta.ip, user_agent=meta.user_agent)
            raise UnauthorizedError(
                "That reset link has expired or has already been used. Request a new one.",
                code="invalid_reset_token",
            )
        user = self.store.get_user(record["user_id"])
        if user is None or user["status"] != "active":
            raise UnauthorizedError("That reset link is no longer valid.",
                                    code="invalid_reset_token")
        # Validate before consuming, so a password the policy rejects does not silently
        # burn the user's only link and force them to start the email dance again.
        self._validate_new_password(user, new_password)
        if not self.store.consume_reset(record["id"]):
            raise UnauthorizedError("That reset link has already been used.",
                                    code="invalid_reset_token")
        self._set_password(user, new_password, meta=meta, event="password.reset.confirm")
        return user

    def _set_password(self, user: dict[str, Any], new_password: str, *, meta: RequestMeta,
                      event: str, actor_id: str | None = None) -> None:
        validated = self._validate_new_password(user, new_password)
        self.store.update_user(
            user["id"],
            password_hash=security.hasher.hash(validated),
            password_changed_at=stamp(),
            must_change_password=False,
            failed_attempts=0,
            locked_until=None,
        )
        # Every other device is signed out. A password change is usually a response to a
        # suspected compromise, and leaving the attacker's session alive would defeat it.
        self.store.bump_epoch(user["id"])
        self.store.record_event(event=event, user_id=user["id"], email=user["email"],
                                actor_id=actor_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail="all sessions signed out")

    def _validate_new_password(self, user: dict[str, Any], new_password: str) -> str:
        try:
            validated = security.validate_password(
                new_password, email=user["email"], name=user["name"]
            )
        except security.PasswordPolicyError as exc:
            raise InvalidInputError(str(exc), code="weak_password") from exc
        if security.hasher.verify(validated, user["password_hash"]):
            raise InvalidInputError(
                "That is your current password. Choose a different one.", code="weak_password"
            )
        return validated

    # ------------------------------------------------------------------ admin
    def set_role(self, *, user_id: str, role: str, actor_id: str, meta: RequestMeta) -> dict[str, Any]:
        if role not in ("admin", "user"):
            raise InvalidInputError(f"Unknown role '{role}'.")
        user = self.require_user(user_id)
        if user["role"] == "admin" and role != "admin" and self.store.count_admins(excluding=user_id) == 0:
            raise ForbiddenError(
                "This is the last administrator. Promote somebody else first.",
                code="last_admin",
            )
        self.store.update_user(user_id, role=role)
        # A demotion has to reach live sessions immediately, or the browser that was an
        # admin a moment ago keeps its admin session until it happens to expire.
        self.store.bump_epoch(user_id)
        self.store.record_event(event="admin.user.role", user_id=user_id, email=user["email"],
                                actor_id=actor_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail=f"{user['role']} → {role}")
        return self.require_user(user_id)

    def set_status(self, *, user_id: str, status: str, actor_id: str, meta: RequestMeta) -> dict[str, Any]:
        if status not in ("active", "suspended"):
            raise InvalidInputError(f"Unknown status '{status}'.")
        user = self.require_user(user_id)
        if status == "suspended":
            if user_id == actor_id:
                raise ForbiddenError("You cannot suspend your own account.", code="self_suspend")
            if user["role"] == "admin" and self.store.count_admins(excluding=user_id) == 0:
                raise ForbiddenError("This is the last administrator.", code="last_admin")
        self.store.update_user(user_id, status=status)
        if status == "suspended":
            self.store.bump_epoch(user_id)
        self.store.record_event(event="admin.user.status", user_id=user_id, email=user["email"],
                                actor_id=actor_id, ip=meta.ip, user_agent=meta.user_agent,
                                detail=status)
        return self.require_user(user_id)

    def admin_reset_password(self, *, user_id: str, new_password: str, actor_id: str,
                             meta: RequestMeta) -> None:
        """Set a password on someone's behalf, and require them to change it on first use."""
        user = self.require_user(user_id)
        self._set_password(user, new_password, meta=meta, event="admin.user.password",
                           actor_id=actor_id)
        self.store.update_user(user_id, must_change_password=True)

    def require_user(self, user_id: str) -> dict[str, Any]:
        user = self.store.get_user(user_id)
        if user is None:
            raise NotFoundError("That account was not found.")
        return user

    def update_profile(self, user_id: str, *, name: str | None = None) -> dict[str, Any]:
        if name is not None:
            cleaned = self._clean_name(name)
            if not cleaned:
                raise InvalidInputError("Your display name cannot be empty.")
            self.store.update_user(user_id, name=cleaned)
        return self.require_user(user_id)

    # ------------------------------------------------------------------ helpers
    def _announce_first_account(self, user_id: str) -> None:
        """Let the app adopt any pre-existing single-user data into the first account.

        Never allowed to fail the registration it follows: somebody signing up should
        not be told their account could not be created because an old database could
        not be moved.
        """
        if self._on_first_account is None:
            return
        try:
            self._on_first_account(user_id)
        except Exception:  # noqa: BLE001
            logger.warning("Could not adopt the existing workspace into %s", user_id, exc_info=True)

    def _throttle(self, bucket: str, *, limit: int, window_s: int, message: str) -> None:
        allowed, _ = self.store.hit_rate_limit(bucket, limit=limit, window_s=window_s)
        if not allowed:
            raise RateLimitedError(message, retry_after_s=window_s)

    @staticmethod
    def _normalise_email(email: str) -> str:
        try:
            return security.normalize_email(email)
        except security.PasswordPolicyError as exc:
            raise InvalidInputError(str(exc), code="invalid_email") from exc

    def _check_signup_allowed(self, address: str) -> bool:
        """Every rule a new self-service account must pass. Returns whether it is the first.

        Shared by the sign-up form and by Google, so closing registration, restricting
        it to a domain or capping seats cannot be sidestepped by picking the other door.
        """
        first_user = self.store.count_users() == 0
        if not self.settings.registration_enabled and not first_user:
            raise ForbiddenError(
                "Sign-up is closed on this deployment. Ask an administrator for an invitation.",
                code="registration_closed",
            )
        self._check_domain_allowed(address)
        if not first_user and self.settings.max_users and self.store.count_users() >= self.settings.max_users:
            raise ForbiddenError(
                "This deployment has reached its account limit.", code="user_limit_reached"
            )
        return first_user

    def _check_domain_allowed(self, address: str) -> None:
        allowed = self.settings.allowed_signup_domains
        if allowed and address.rsplit("@", 1)[-1] not in allowed:
            raise ForbiddenError(
                f"Sign-up is limited to {', '.join('@' + d for d in allowed)} addresses.",
                code="domain_not_allowed",
            )

    @staticmethod
    def _clean_name(name: str | None) -> str:
        # Strip control characters: a display name is rendered in the UI, in emails and
        # in the activity feed, and a newline in it is a formatting injection.
        cleaned = "".join(ch for ch in (name or "") if ch.isprintable()).strip()
        return cleaned[:MAX_NAME_LENGTH]


def _reset_email(name: str, link: str, ttl_minutes: int) -> str:
    return (
        f"Hi {name},\n\n"
        "Somebody asked to reset the password on your Numera account. "
        f"Open the link below within {ttl_minutes} minutes to choose a new one:\n\n"
        f"{link}\n\n"
        "The link can only be used once. If you did not ask for this, you can ignore "
        "this email — your password has not changed.\n"
    )


__all__ = ["AuthService", "Mailer", "RequestMeta", "SignedIn", "SmtpMailer", "public_user"]
