"""Password hashing, token minting and the password policy.

Three rules shape this module:

* **A plaintext password never leaves this file.** Callers hand one in and get an
  encoded digest back, or a boolean. Nothing else in the codebase touches the value,
  nothing logs it, and no route model ever echoes it.
* **Verification is constant-time and enumeration-resistant.** A login against an
  address that does not exist costs the same as one against an address that does,
  because the caller verifies against `DUMMY_HASH` rather than returning early.
* **Hashes carry their own parameters.** The encoded string names the algorithm and
  its cost, so raising the cost later upgrades users on their next successful login
  instead of requiring a migration or a forced reset.

Argon2id is used when `argon2-cffi` is installed, which it is via requirements.txt.
PBKDF2-HMAC-SHA256 from the standard library is the fallback, so a stripped
deployment still starts and still stores nothing reversible — it is a weaker hash,
not an absent one, and `needs_rehash` migrates those users to Argon2id the moment
the dependency appears.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass

try:  # pragma: no cover - exercised by whichever branch the environment provides
    from argon2 import PasswordHasher as _Argon2Hasher
    from argon2 import exceptions as _argon2_exceptions
    from argon2.profiles import RFC_9106_LOW_MEMORY

    ARGON2_AVAILABLE = True
except ImportError:  # pragma: no cover
    ARGON2_AVAILABLE = False

# PBKDF2 cost for the fallback path. OWASP's 2023 floor for HMAC-SHA256 is 600k.
PBKDF2_ITERATIONS = 600_000
PBKDF2_PREFIX = "pbkdf2_sha256"

MIN_PASSWORD_LENGTH = 10
# Long inputs are hashed, and hashing is deliberately slow; an unbounded password is a
# denial-of-service vector rather than extra security.
MAX_PASSWORD_LENGTH = 256

# Not a dictionary — a dictionary belongs in a breach-corpus check (Pwned Passwords or
# similar), which is a deployment concern. This is the short list of strings that are
# long enough to pass the length rule while being the first thing anyone would guess.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "passw0rd", "p@ssword", "p@ssw0rd",
    "123456789", "1234567890", "12345678910", "qwertyuiop", "qwerty123", "1q2w3e4r5t",
    "letmein123", "welcome123", "admin12345", "administrator", "iloveyou1",
    "monkey123", "dragon123", "football1", "baseball1", "sunshine1", "princess1",
    "trustno1234", "changeme123", "secret123", "abc123456", "numera123",
})

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
MAX_EMAIL_LENGTH = 254


class PasswordPolicyError(ValueError):
    """The chosen password does not satisfy the policy. The message is user-facing."""


@dataclass(frozen=True)
class PasswordStrength:
    """What the policy thinks of a password, for live feedback in the UI."""

    score: int  # 0–4
    label: str
    suggestions: tuple[str, ...]


# --------------------------------------------------------------------------- hashing


class PasswordHasher:
    """Hashes and verifies passwords, and says when a stored digest is out of date."""

    def __init__(self) -> None:
        self._argon2 = (
            _Argon2Hasher.from_parameters(RFC_9106_LOW_MEMORY) if ARGON2_AVAILABLE else None
        )

    @property
    def algorithm(self) -> str:
        return "argon2id" if self._argon2 is not None else PBKDF2_PREFIX

    def hash(self, password: str) -> str:
        """Encode `password`. The result carries its own algorithm and cost parameters."""
        prepared = _prepare(password)
        if self._argon2 is not None:
            return self._argon2.hash(prepared)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", prepared.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        return "$".join([
            PBKDF2_PREFIX,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ])

    def verify(self, password: str, encoded: str | None) -> bool:
        """True when `password` matches `encoded`. Never raises on a malformed digest."""
        if not encoded:
            # Still burn the time: an account with no usable password must not be
            # distinguishable from a wrong password by how fast the answer came back.
            self._burn()
            return False
        prepared = _prepare(password)
        if encoded.startswith("$argon2"):
            if self._argon2 is None:
                return False
            try:
                return self._argon2.verify(encoded, prepared)
            except (_argon2_exceptions.VerificationError, _argon2_exceptions.InvalidHashError):
                return False
        if encoded.startswith(PBKDF2_PREFIX):
            return self._verify_pbkdf2(prepared, encoded)
        return False

    def needs_rehash(self, encoded: str | None) -> bool:
        """True when the digest was made with weaker parameters than we now use."""
        if not encoded:
            return False
        if encoded.startswith("$argon2"):
            return self._argon2 is not None and self._argon2.check_needs_rehash(encoded)
        if encoded.startswith(PBKDF2_PREFIX):
            # Any PBKDF2 digest is superseded once Argon2 is available.
            if self._argon2 is not None:
                return True
            try:
                return int(encoded.split("$")[1]) < PBKDF2_ITERATIONS
            except (IndexError, ValueError):
                return True
        return True

    def _verify_pbkdf2(self, prepared: str, encoded: str) -> bool:
        try:
            _, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
            candidate = hashlib.pbkdf2_hmac(
                "sha256", prepared.encode("utf-8"), base64.b64decode(salt_b64), int(iterations)
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(candidate, base64.b64decode(digest_b64))

    def _burn(self) -> None:
        """Spend roughly one verification's worth of time on nothing."""
        self.verify("numera-timing-equaliser", DUMMY_HASH)


def _prepare(password: str) -> str:
    """Normalise before hashing so the same typed password always encodes identically.

    NFKC matters for non-ASCII passwords: two byte sequences that render the same
    character must not become two different accounts' worth of failed logins.
    """
    if not isinstance(password, str):
        raise PasswordPolicyError("Password must be text.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    return unicodedata.normalize("NFKC", password)


hasher = PasswordHasher()
# A real digest of a value nobody knows, verified against whenever there is no user to
# verify against. Built once at import so the cost is paid at startup, not per request.
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


# ---------------------------------------------------------------------------- policy


def normalize_email(raw: str) -> str:
    """Trim and lower-case an address, and reject anything that is not one.

    Only the domain is genuinely case-insensitive per RFC 5321, but every mailbox
    provider in practice treats the local part that way too, and an app that lets
    `Ada@x.com` and `ada@x.com` become two accounts has an account-takeover surface
    rather than a feature.
    """
    email = (raw or "").strip().lower()
    if not email:
        raise PasswordPolicyError("An email address is required.")
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
        raise PasswordPolicyError("That does not look like a valid email address.")
    return email


def validate_password(password: str, *, email: str | None = None, name: str | None = None) -> str:
    """Raise `PasswordPolicyError` unless `password` is acceptable. Returns it prepared.

    The rules follow NIST SP 800-63B: length is what matters, composition rules are
    not required, and the useful screening is against obvious and context-specific
    guesses rather than against a character-class checklist.
    """
    prepared = _prepare(password)
    if len(prepared) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if prepared.strip() != prepared and not prepared.strip():
        raise PasswordPolicyError("Password cannot be only whitespace.")

    folded = prepared.casefold()
    if folded in COMMON_PASSWORDS:
        raise PasswordPolicyError("That password is too common. Choose something less guessable.")
    if len(set(folded)) < 5:
        raise PasswordPolicyError("Password repeats too few distinct characters.")
    for context in (email.split("@")[0] if email else None, name, "numera"):
        if context and len(context) >= 4 and context.casefold() in folded:
            raise PasswordPolicyError(
                "Password must not contain your name, your email address or the app name."
            )
    return prepared


def strength(password: str) -> PasswordStrength:
    """A coarse score for the signup meter. Advisory only — `validate_password` decides."""
    if not password:
        return PasswordStrength(0, "Too short", ("Use at least 10 characters.",))
    score = 0
    suggestions: list[str] = []
    if len(password) >= MIN_PASSWORD_LENGTH:
        score += 1
    else:
        suggestions.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) >= 16:
        score += 1
    else:
        suggestions.append("Longer is stronger — a short phrase beats a clever word.")
    classes = sum([
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^\w\s]", password)),
    ])
    if classes >= 2:
        score += 1
    if classes >= 3 or len(password) >= 20:
        score += 1
    if password.casefold() in COMMON_PASSWORDS:
        score = 0
        suggestions = ["That password appears on every guessing list."]
    labels = ("Very weak", "Weak", "Fair", "Good", "Strong")
    return PasswordStrength(score, labels[min(score, 4)], tuple(suggestions[:2]))


# ----------------------------------------------------------------------------- tokens

TOKEN_BYTES = 32  # 256 bits: unguessable, and short enough for a URL and a cookie.


def new_token() -> str:
    """A fresh opaque secret. This is the only place session and reset tokens are born."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str, secret: str) -> str:
    """What gets stored for a token.

    Keyed rather than plain SHA-256: the tokens are already 256 bits of entropy, so a
    rainbow table was never the threat. The key means a leaked database file is not by
    itself enough to mint a working cookie — the attacker also needs `AUTH_SECRET`,
    which lives in the environment and not in the backup. Rotating that secret
    invalidates every session and every outstanding reset link, which is exactly the
    behaviour you want from a secret rotation.
    """
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison for anything secret that arrives from a client."""
    return hmac.compare_digest(left or "", right or "")


__all__ = [
    "ARGON2_AVAILABLE",
    "MIN_PASSWORD_LENGTH",
    "PasswordHasher",
    "PasswordPolicyError",
    "PasswordStrength",
    "hasher",
    "new_token",
    "normalize_email",
    "strength",
    "token_digest",
    "tokens_equal",
    "validate_password",
]
