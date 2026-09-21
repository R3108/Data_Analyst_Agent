"""Session and CSRF cookies, and the double-submit check that guards writes.

Two cookies, with deliberately different rules:

* **The session cookie** is `HttpOnly`. JavaScript cannot read it, so a cross-site
  scripting bug in the app — or in any dependency it loads — cannot exfiltrate a
  login. This is the whole reason the token is not kept in `localStorage`, which is
  readable by every script on the page.
* **The CSRF cookie** is deliberately *not* `HttpOnly`, because the app has to read it
  and echo it back in a header. That is safe: it is not a credential on its own, and
  the browser's same-origin policy stops another site from reading it.

Together they implement the signed double-submit pattern. A cross-site form post
carries the session cookie (that is what CSRF *is*) but cannot read the CSRF cookie to
copy it into the header, and cannot set a custom header on a simple form post at all —
doing so forces a preflight that CORS then refuses. A request that presents both, and
where the two match, therefore came from our own origin.

In production the cookies take the `__Host-` prefix, which browsers only accept when
the cookie is `Secure`, `Path=/` and has no `Domain`. That closes subdomain fixation:
a compromised `blog.example.com` cannot overwrite the session of `app.example.com`.
The prefix is dropped when a `Domain` is configured, because the two are incompatible
by design.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.core.security import new_token, tokens_equal

# Methods that cannot change state. RFC 9110 requires these to be safe, and the browser
# sends them on navigations we do not control, so requiring a header on them would break
# ordinary links without buying anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_HEADER = "x-csrf-token"


def session_cookie_name(settings: Settings) -> str:
    return _prefixed(settings.session_cookie_name, settings)


def csrf_cookie_name(settings: Settings) -> str:
    return _prefixed(settings.csrf_cookie_name, settings)


def oauth_flow_cookie_name(settings: Settings) -> str:
    """The short-lived cookie that carries a Google sign-in between leaving and returning."""
    return _prefixed("numera_oauth", settings)


def _prefixed(name: str, settings: Settings) -> str:
    """`__Host-` when the browser will accept it, plain otherwise."""
    if settings.secure_cookies and not settings.cookie_domain:
        return f"__Host-{name}"
    return name


def _options(settings: Settings, *, http_only: bool, max_age: int | None) -> dict[str, object]:
    options: dict[str, object] = {
        "httponly": http_only,
        "secure": settings.secure_cookies,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    if max_age is not None:
        options["max_age"] = max_age
    # `__Host-` forbids Domain, and the prefix is only applied when none is configured.
    if settings.cookie_domain:
        options["domain"] = settings.cookie_domain
    return options


def issue_session(response: Response, settings: Settings, token: str) -> str:
    """Attach a new session and a fresh CSRF token. Returns the CSRF value."""
    # The cookie's own lifetime matches the *absolute* session deadline, not the idle
    # one. The server is the authority on idleness; a shorter cookie would just log
    # people out of a session the server still considers live.
    max_age = settings.session_absolute_days * 24 * 3600
    response.set_cookie(
        session_cookie_name(settings), token,
        **_options(settings, http_only=True, max_age=max_age),
    )
    csrf = new_token()
    response.set_cookie(
        csrf_cookie_name(settings), csrf,
        **_options(settings, http_only=False, max_age=max_age),
    )
    return csrf


def clear_session(response: Response, settings: Settings) -> None:
    """Delete both cookies. The attributes must match those they were set with."""
    for name in (session_cookie_name(settings), csrf_cookie_name(settings)):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.cookie_domain,
            secure=settings.secure_cookies,
            httponly=name == session_cookie_name(settings),
            samesite=settings.cookie_samesite,
        )


def read_session_token(request: Request, settings: Settings) -> str | None:
    """The session token this request presents, from the cookie or a bearer header.

    The bearer form exists for non-browser callers — scripts, CI, a terminal — which
    have no cookie jar and no CSRF problem to solve, since a header cannot be forged by
    a hostile page. Browsers use the cookie.
    """
    cookie = request.cookies.get(session_cookie_name(settings))
    if cookie:
        return cookie
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and presented.strip():
        return presented.strip()
    return None


def came_from_cookie(request: Request, settings: Settings) -> bool:
    """True when the session was presented as a cookie, which is what CSRF applies to."""
    return bool(request.cookies.get(session_cookie_name(settings)))


def verify_csrf(request: Request, settings: Settings) -> None:
    """Enforce the double-submit on any write that authenticates with a cookie."""
    if request.method.upper() in SAFE_METHODS:
        return
    if not came_from_cookie(request, settings):
        # Authenticated by bearer token: no ambient credential, so no CSRF surface.
        return
    cookie = request.cookies.get(csrf_cookie_name(settings))
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not tokens_equal(cookie, header):
        raise ForbiddenError(
            "This request was blocked because it is missing a valid CSRF token. "
            "Reload the page and try again.",
            code="csrf_failed",
        )


__all__ = [
    "CSRF_HEADER",
    "SAFE_METHODS",
    "clear_session",
    "came_from_cookie",
    "csrf_cookie_name",
    "issue_session",
    "oauth_flow_cookie_name",
    "read_session_token",
    "session_cookie_name",
    "verify_csrf",
]
