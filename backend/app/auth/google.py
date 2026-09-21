"""Sign in with Google: OpenID Connect, authorization-code flow with PKCE.

The whole exchange happens server to server. The browser only ever carries Google's
one-time `code` and our own `state`; the client secret, the access token and the ID
token never reach it. That is the reason for a redirect flow rather than Google's
JavaScript button: nothing a script on the page can read is worth stealing.

Four values tie a callback to the browser that started it, and each closes a
different attack:

* **state** — a random value kept in a signed, `HttpOnly` cookie and echoed by Google.
  A callback whose state does not match the cookie was not started here, which is
  what stops a *login CSRF*: an attacker completing their own Google sign-in in your
  browser and leaving you working inside their account.
* **nonce** — sent to Google and required back inside the ID token, so a token minted
  for some other request cannot be replayed into this one.
* **PKCE verifier** — only its hash goes to Google; the verifier itself is presented
  at the token endpoint. A `code` intercepted on the way back is useless without it.
* **The signed cookie itself** — HMAC'd with `AUTH_SECRET`, so a client cannot forge
  a flow or change where it will be sent afterwards.

The ID token's signature is not checked against Google's published keys. OpenID
Connect Core §3.1.3.7 allows this when the token arrives directly from the token
endpoint over TLS, which is the only way this code ever receives one: the TLS
certificate of `oauth2.googleapis.com` is what authenticates it. Every claim that
matters — issuer, audience, expiry, nonce, verified email — is still checked.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.errors import AppError

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
PROVIDER = "google"

# Long enough to pick an account and approve a consent screen, short enough that a
# flow cookie left behind in a shared browser is dead by the time anyone finds it.
FLOW_TTL_S = 600
# Tolerated clock difference between this server and Google when reading `exp`/`iat`.
CLOCK_SKEW_S = 120

# Addresses Google is the authority for. For these, "email_verified" means Google owns
# the mailbox; for any other domain it only means the address was verified once, by
# somebody, at some point — not strong enough to hand over an existing account.
GOOGLE_MAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


class GoogleAuthError(AppError):
    """A sign-in with Google could not be completed. `code` is safe to put in a URL."""

    status_code = 400
    code = "google_failed"


@dataclass(frozen=True)
class GoogleProfile:
    """Who Google says signed in, after every claim has been checked."""

    subject: str
    email: str
    email_verified: bool
    name: str
    hosted_domain: str | None = None

    @property
    def email_is_authoritative(self) -> bool:
        """True when Google, and nobody else, controls this mailbox.

        A Google Workspace account (`hd` present) or a Gmail address. Only these may
        be linked to an existing password account automatically.
        """
        if not self.email_verified:
            return False
        domain = self.email.rsplit("@", 1)[-1].lower()
        return bool(self.hosted_domain) or domain in GOOGLE_MAIL_DOMAINS


@dataclass(frozen=True)
class Flow:
    """One sign-in attempt in progress, as carried by the flow cookie."""

    state: str
    nonce: str
    verifier: str
    next_path: str
    intent: str  # "signin" or "link"
    user_id: str | None  # the account being linked, for intent == "link"
    issued_at: int


# ------------------------------------------------------------------------ flow cookie


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_flow(*, next_path: str, intent: str, user_id: str | None = None) -> Flow:
    return Flow(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        # RFC 7636 allows 43–128 characters; 64 random bytes encode to 86.
        verifier=secrets.token_urlsafe(64),
        next_path=next_path,
        intent=intent,
        user_id=user_id,
        issued_at=int(time.time()),
    )


def seal_flow(flow: Flow, secret: str) -> str:
    """Serialise and sign a flow for the cookie. Signed, not encrypted: nothing in it
    is useful to the person holding the browser, only to someone who could alter it."""
    body = _b64(json.dumps(flow.__dict__, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body, secret)}"


def open_flow(value: str | None, secret: str) -> Flow | None:
    """The flow in a cookie, or None if it is missing, forged, altered or expired."""
    if not value or "." not in value:
        return None
    body, _, signature = value.rpartition(".")
    if not hmac.compare_digest(signature, _sign(body, secret)):
        return None
    try:
        data = json.loads(_unb64(body))
        flow = Flow(**data)
    except (ValueError, TypeError):
        return None
    if int(time.time()) - flow.issued_at > FLOW_TTL_S:
        return None
    return flow


def _sign(body: str, secret: str) -> str:
    # Domain-separated from the session-token digests, which use the same secret.
    key = hmac.new(secret.encode("utf-8"), b"numera/google-flow/v1", hashlib.sha256).digest()
    return _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


# ------------------------------------------------------------------------ the client


class GoogleOAuth:
    """Builds the authorization redirect and redeems the code that comes back."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport

    @property
    def client_id(self) -> str:
        return (self.settings.google_client_id or "").strip()

    @property
    def redirect_uri(self) -> str:
        return self.settings.resolved_google_redirect_uri

    def authorization_url(self, flow: Flow, *, login_hint: str | None = None) -> str:
        challenge = _b64(hashlib.sha256(flow.verifier.encode("ascii")).digest())
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Always show the account chooser. Without it a browser signed in to one
            # Google account silently becomes that account here, which is the wrong
            # default on a shared or work machine.
            "prompt": "select_account",
            "access_type": "online",
            "include_granted_scopes": "true",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange(self, *, code: str, flow: Flow) -> GoogleProfile:
        """Redeem an authorization code and return the verified profile."""
        try:
            with httpx.Client(transport=self._transport, timeout=10.0) as client:
                response = client.post(
                    TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self.client_id,
                        "client_secret": (self.settings.google_client_secret or "").strip(),
                        "redirect_uri": self.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": flow.verifier,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise GoogleAuthError("Could not reach Google to finish signing in.",
                                  code="google_unreachable") from exc
        if response.status_code != 200:
            raise GoogleAuthError("Google did not accept the sign-in. Please try again.",
                                  code="google_exchange_failed")
        try:
            id_token = response.json()["id_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GoogleAuthError("Google's response did not include an identity.",
                                  code="google_exchange_failed") from exc
        return self.verify_id_token(id_token, nonce=flow.nonce)

    def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleProfile:
        """Check the claims of an ID token received directly from the token endpoint."""
        claims = _decode_claims(id_token)
        now = int(time.time())

        def reject(code: str) -> GoogleAuthError:
            return GoogleAuthError("Google returned an identity this app cannot accept.", code=code)

        if claims.get("iss") not in ISSUERS:
            raise reject("google_bad_issuer")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if self.client_id not in audiences:
            raise reject("google_bad_audience")
        if isinstance(audience, list) and len(audiences) > 1 and claims.get("azp") != self.client_id:
            raise reject("google_bad_audience")
        try:
            expires, issued = int(claims["exp"]), int(claims.get("iat", now))
        except (KeyError, TypeError, ValueError) as exc:
            raise reject("google_bad_token") from exc
        if expires + CLOCK_SKEW_S < now or issued - CLOCK_SKEW_S > now:
            raise reject("google_token_expired")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise reject("google_bad_nonce")

        subject = str(claims.get("sub") or "")
        email = str(claims.get("email") or "").strip().lower()
        if not subject or not email:
            raise reject("google_no_email")
        verified = claims.get("email_verified") in (True, "true")
        if not verified:
            raise GoogleAuthError(
                "Your Google account's email address is not verified.",
                code="google_email_unverified",
            )
        return GoogleProfile(
            subject=subject,
            email=email,
            email_verified=verified,
            name=str(claims.get("name") or claims.get("given_name") or "").strip(),
            hosted_domain=(str(claims["hd"]).lower() if claims.get("hd") else None),
        )


def _decode_claims(id_token: str) -> dict[str, Any]:
    try:
        _, payload, _ = id_token.split(".")
        claims = json.loads(_unb64(payload))
    except (ValueError, AttributeError) as exc:
        raise GoogleAuthError("Google returned a malformed identity.",
                              code="google_bad_token") from exc
    if not isinstance(claims, dict):
        raise GoogleAuthError("Google returned a malformed identity.", code="google_bad_token")
    return claims


__all__ = [
    "FLOW_TTL_S",
    "Flow",
    "GoogleAuthError",
    "GoogleOAuth",
    "GoogleProfile",
    "PROVIDER",
    "new_flow",
    "open_flow",
    "seal_flow",
]
