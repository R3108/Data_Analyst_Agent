"""Sign-up, sign-in, sign-out, password recovery and device management.

Every response that establishes or ends a session writes cookies directly on a
`JSONResponse` rather than returning a body and letting a dependency patch it later:
the `Set-Cookie` and the outcome have to be atomic, or a client can end up believing
it is signed in while holding no cookie.

None of these routes ever return a password, a password hash, a session token or a
reset token in a body. The session token exists only as an `HttpOnly` cookie, and the
reset token only inside the emailed link.
"""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth import cookies
from app.auth.google import FLOW_TTL_S, GoogleOAuth, new_flow, open_flow, seal_flow
from app.auth.service import AuthService, RequestMeta, SignedIn
from app.auth.store import public_user
from app.core import security
from app.core.auth import Identity, identity_of
from app.core.config import Settings
from app.core.errors import AppError
from app.api.deps import get_auth, get_identity, get_meta, get_settings_dep

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=80)


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ForgotBody(BaseModel):
    email: str = Field(max_length=254)


class ResetBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordBody(BaseModel):
    # Empty only for an account created through Google that has never had a password.
    current_password: str = Field(default="", max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _session_response(signed_in: SignedIn, settings: Settings, *, status_code: int = 200,
                      extra: dict[str, Any] | None = None) -> JSONResponse:
    """A body describing the account, plus the cookies that make it a session."""
    response = JSONResponse(
        status_code=status_code,
        content={"user": public_user(signed_in.user), **(extra or {})},
    )
    cookies.issue_session(response, settings, signed_in.token)
    return response


@router.get("/config")
def auth_config(request: Request,
                settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    """What the sign-in screens need to render, before anybody is signed in.

    Deliberately thin: whether sign-up is open, any domain restriction, and the
    password rules, so the form can validate before a round trip. It reveals nothing
    about who has an account.
    """
    store = request.app.state.auth_store
    first_run = store.count_users() == 0
    return {
        "registration_enabled": settings.registration_enabled or first_run,
        "allowed_domains": settings.allowed_signup_domains,
        "first_run": first_run,
        "password_reset_enabled": True,
        "email_delivery": bool(settings.smtp_host),
        "min_password_length": security.MIN_PASSWORD_LENGTH,
        "google_enabled": settings.google_enabled,
    }


@router.post("/register")
def register(
    body: RegisterBody,
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> JSONResponse:
    """Create an account and sign in. The very first account becomes the administrator."""
    signed_in = auth.register(email=body.email, password=body.password, name=body.name, meta=meta)
    return _session_response(signed_in, settings, status_code=201)


@router.post("/login")
def login(
    body: LoginBody,
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> JSONResponse:
    signed_in = auth.sign_in(email=body.email, password=body.password, meta=meta)
    return _session_response(signed_in, settings)


@router.post("/logout")
def logout(
    request: Request,
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """End this session. Always succeeds, so a stale cookie is still cleared."""
    auth.sign_out(cookies.read_session_token(request, settings), meta=meta)
    response = JSONResponse(content={"ok": True})
    cookies.clear_session(response, settings)
    return response


@router.get("/session")
def session(request: Request, settings: Settings = Depends(get_settings_dep),
            auth: AuthService = Depends(get_auth)) -> dict[str, Any]:
    """Who this browser is, or `null`. The app's bootstrap call — never a 401.

    A 401 here would be noise: "not signed in" is the expected answer on the login
    screen, and making it an error means every client has to special-case it.
    """
    identity: Identity | None = identity_of(request)
    if identity is None:
        return {"user": None}
    user = auth.store.get_user(identity.user_id)
    return {"user": public_user(user) if user else None}


@router.post("/forgot-password")
def forgot_password(
    body: ForgotBody,
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
) -> dict[str, Any]:
    """Send a reset link. The answer is the same whether or not the address exists."""
    link = auth.request_reset(email=body.email, meta=meta)
    response: dict[str, Any] = {
        "ok": True,
        "message": "If an account exists for that address, a reset link is on its way.",
    }
    # Present only on a development server with no SMTP host and EXPOSE_RESET_LINK on.
    if link:
        response["reset_link"] = link
    return response


@router.post("/reset-password")
def reset_password(
    body: ResetBody,
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> JSONResponse:
    """Spend a reset link and sign in with the new password."""
    user = auth.confirm_reset(token=body.token, new_password=body.password, meta=meta)
    signed_in = auth.start_session(user["id"], meta)
    return _session_response(signed_in, settings)


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(get_identity),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> JSONResponse:
    """Change your own password, and keep this browser signed in.

    The change signs every session out, including this one. Minting a replacement here
    is session rotation, not a loophole: the caller has just proved the old password,
    and a new cookie means a token captured earlier is worthless.
    """
    user = auth.require_user(identity.user_id)
    auth.change_password(user, current_password=body.current_password,
                         new_password=body.new_password, meta=meta)
    signed_in = auth.start_session(identity.user_id, meta)
    return _session_response(signed_in, settings, extra={"signed_out_other_devices": True})


@router.patch("/profile")
def update_profile(
    body: ProfileBody,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    return {"user": public_user(auth.update_profile(identity.user_id, name=body.name))}


@router.get("/sessions")
def list_sessions(
    request: Request,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings_dep),
) -> list[dict[str, Any]]:
    """Every device signed in as you, with the current one flagged."""
    token = cookies.read_session_token(request, settings)
    return auth.list_sessions(identity.user_id, current_token=token)


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: str,
    request: Request,
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(get_identity),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Sign one device out. Revoking your own clears this browser's cookies too."""
    auth.revoke_session(identity.user_id, session_id)
    response = JSONResponse(content={"ok": True})
    if identity.session_id == session_id:
        cookies.clear_session(response, settings)
    return response


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    auth: AuthService = Depends(get_auth),
    identity: Identity = Depends(get_identity),
    meta: RequestMeta = Depends(get_meta),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Sign out everywhere, including here."""
    auth.sign_out_everywhere(identity.user_id, actor_id=identity.user_id, meta=meta)
    response = JSONResponse(content={"ok": True})
    cookies.clear_session(response, settings)
    return response


@router.get("/methods")
def sign_in_methods(auth: AuthService = Depends(get_auth),
                    identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """How this account can sign in: a password, a connected Google account, or both."""
    return auth.sign_in_methods(identity.user_id)


@router.delete("/google")
def disconnect_google(auth: AuthService = Depends(get_auth),
                      identity: Identity = Depends(get_identity),
                      meta: RequestMeta = Depends(get_meta)) -> dict[str, Any]:
    auth.unlink_google(identity.user_id, meta=meta)
    return auth.sign_in_methods(identity.user_id)


# ------------------------------------------------------------ sign in with Google
#
# Both routes are browser navigations, not API calls, so they answer with redirects
# rather than JSON. A failure lands on a page of the app with a short error *code* in
# the query string; the page owns the wording. Nothing secret ever goes in a URL.


def _safe_next(raw: str | None) -> str:
    """Only a path on this app. See `safeNext` in the login form for why."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw[:512]


def _app_url(settings: Settings, path: str, **query: str) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}{path}" + (f"?{urlencode(query)}" if query else "")


def _redirect(url: str, settings: Settings) -> RedirectResponse:
    """A 303 that also retires the flow cookie. Every callback outcome goes through here."""
    response = RedirectResponse(url, status_code=303)
    response.delete_cookie(cookies.oauth_flow_cookie_name(settings), path="/", domain=settings.cookie_domain,
                           secure=settings.secure_cookies, httponly=True, samesite="lax")
    return response


@router.get("/google/start")
def google_start(
    request: Request,
    next: str | None = None,  # noqa: A002 - the conventional name for a post-login path
    intent: str = "signin",
    settings: Settings = Depends(get_settings_dep),
    auth: AuthService = Depends(get_auth),
) -> Response:
    """Begin a Google sign-in (or, signed in, connect Google). Redirects to Google."""
    if not settings.google_enabled:
        return RedirectResponse(_app_url(settings, "/login", error="google_unavailable"),
                                status_code=303)
    user_id: str | None = None
    if intent == "link":
        current = identity_of(request)
        if current is None:
            return RedirectResponse(_app_url(settings, "/login", next="/account"), status_code=303)
        user_id = current.user_id
    else:
        intent = "signin"

    flow = new_flow(next_path=_safe_next(next), intent=intent, user_id=user_id)
    google = GoogleOAuth(settings, transport=getattr(request.app.state, "google_transport", None))
    response = RedirectResponse(google.authorization_url(flow), status_code=303)
    # SameSite=Lax, whatever the session cookie uses: Google's redirect back is a
    # cross-site top-level GET, which Lax allows and Strict would silently drop.
    response.set_cookie(
        cookies.oauth_flow_cookie_name(settings), seal_flow(flow, auth.secret),
        max_age=FLOW_TTL_S, path="/", domain=settings.cookie_domain,
        secure=settings.secure_cookies, httponly=True, samesite="lax",
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_settings_dep),
    auth: AuthService = Depends(get_auth),
    meta: RequestMeta = Depends(get_meta),
) -> Response:
    """Google sends the browser back here. Verify, sign in or link, then redirect."""
    flow = open_flow(request.cookies.get(cookies.oauth_flow_cookie_name(settings)), auth.secret)
    failure_page = "/account" if flow and flow.intent == "link" else "/login"

    def fail(reason: str) -> RedirectResponse:
        param = "google_error" if failure_page == "/account" else "error"
        return _redirect(_app_url(settings, failure_page, **{param: reason}), settings)

    if error:
        # The person closed the chooser or declined consent. Not an attack, not logged.
        return fail("google_cancelled" if error == "access_denied" else "google_failed")
    if flow is None:
        return fail("google_expired")
    if not state or not code or not hmac.compare_digest(state, flow.state):
        auth.store.record_event(event="login.google", outcome="state_mismatch", ip=meta.ip,
                                user_agent=meta.user_agent)
        return fail("google_expired")

    google = GoogleOAuth(settings, transport=getattr(request.app.state, "google_transport", None))
    try:
        profile = google.exchange(code=code, flow=flow)
        if flow.intent == "link":
            current = identity_of(request)
            # The session that finishes a link must be the one that started it.
            if current is None or current.user_id != flow.user_id:
                return fail("google_expired")
            auth.link_google(current.user_id, profile, meta=meta)
            return _redirect(_app_url(settings, "/account", google="connected"), settings)
        signed_in = auth.sign_in_with_google(profile, meta=meta)
    except AppError as exc:
        auth.store.record_event(event="login.google", outcome=exc.code, ip=meta.ip,
                                user_agent=meta.user_agent)
        return fail(exc.code)

    destination = "/account?change=1" if signed_in.user.get("must_change_password") else flow.next_path
    response = _redirect(_app_url(settings, destination), settings)
    # Whatever this browser was signed in as before, it is now this account.
    auth.sign_out(cookies.read_session_token(request, settings), meta=meta)
    cookies.issue_session(response, settings, signed_in.token)
    return response


@router.post("/password-strength")
def password_strength(password: str = Body(embed=True, max_length=256)) -> dict[str, Any]:
    """Advisory scoring for the sign-up meter. Nothing is stored and nothing is logged."""
    result = security.strength(password)
    return {"score": result.score, "label": result.label, "suggestions": list(result.suggestions)}


__all__ = ["router"]
