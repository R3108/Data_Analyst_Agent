"""Sign in with Google, end to end, against a stand-in for Google's token endpoint.

The browser leg is replayed by hand: `/start` is called without following the
redirect, the `state` and `nonce` are read out of the URL it points at, and the
callback is then called the way Google would call it. The token endpoint is an
`httpx.MockTransport` that mints an ID token for whatever claims the test sets.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import TEST_PASSWORD, ScriptedLLM, sign_in

CLIENT_ID = "test-client.apps.googleusercontent.com"


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


class FakeGoogle:
    """The token endpoint. Tests set `claims`; `nonce` defaults to the flow's own."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.nonce: str | None = None
        self.status = 200
        self.requests: list[dict[str, list[str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.requests.append(form)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_grant"})
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "g-123",
            "email": "ada@gmail.com", "email_verified": True, "name": "Ada Lovelace",
            "iat": now, "exp": now + 3600, "nonce": self.nonce, **self.claims,
        }
        token = f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.signature"
        return httpx.Response(200, json={"id_token": token, "access_token": "x"})


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


def make_client(settings, google: FakeGoogle, **overrides: Any) -> TestClient:
    settings = settings.model_copy(update={
        "google_client_id": CLIENT_ID, "google_client_secret": "shh", **overrides,
    })
    app = create_app(settings=settings, llm=ScriptedLLM({}),
                     google_transport=httpx.MockTransport(google.handler))
    return TestClient(app)


@pytest.fixture
def client(settings, google) -> TestClient:
    return make_client(settings, google)


def start(client: TestClient, google: FakeGoogle, **params: str) -> dict[str, str]:
    response = client.get("/api/auth/google/start", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    query = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    if google.nonce is None:
        google.nonce = query.get("nonce")
    return query


def finish(client: TestClient, query: dict[str, str], **overrides: str) -> httpx.Response:
    params = {"code": "auth-code", "state": query["state"], **overrides}
    return client.get("/api/auth/google/callback", params=params, follow_redirects=False)


def google_sign_in(client: TestClient, google: FakeGoogle, **claims: Any) -> httpx.Response:
    google.nonce = None
    google.claims = claims
    return finish(client, start(client, google))


def location(response: httpx.Response) -> tuple[str, dict[str, str]]:
    parts = urlsplit(response.headers["location"])
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


def current_user(client: TestClient) -> dict[str, Any] | None:
    return client.get("/api/auth/session").json()["user"]


# --------------------------------------------------------------------------- basics


def test_the_config_says_whether_google_is_available(settings, google):
    assert make_client(settings, google).get("/api/auth/config").json()["google_enabled"] is True
    plain = TestClient(create_app(settings=settings, llm=ScriptedLLM({})))
    assert plain.get("/api/auth/config").json()["google_enabled"] is False


def test_start_redirects_to_google_with_pkce_state_and_nonce(client, google):
    query = start(client, google)
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == "http://localhost:3000/api/auth/google/callback"
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid email profile"
    assert len(query["state"]) >= 32 and len(query["nonce"]) >= 32
    assert "client_secret" not in query


def test_a_disabled_deployment_sends_the_browser_back_with_a_reason(settings, google):
    plain = TestClient(create_app(settings=settings, llm=ScriptedLLM({})))
    response = plain.get("/api/auth/google/start", follow_redirects=False)
    assert location(response) == ("/login", {"error": "google_unavailable"})


def test_a_new_google_user_gets_an_account_and_a_session(client, google):
    response = google_sign_in(client, google)
    assert location(response) == ("/", {})
    user = current_user(client)
    assert user["email"] == "ada@gmail.com"
    assert user["name"] == "Ada Lovelace"
    assert user["role"] == "admin"  # first account on the deployment
    assert user["has_password"] is False
    # The code was redeemed with the PKCE verifier and the secret, server to server.
    sent = google.requests[-1]
    assert sent["code_verifier"][0] and sent["client_secret"] == ["shh"]


def test_the_session_cookie_is_http_only_and_the_flow_cookie_is_spent(client, google):
    response = google_sign_in(client, google)
    set_cookies = response.headers.get_list("set-cookie")
    session = next(c for c in set_cookies if c.startswith("numera_session="))
    assert "HttpOnly" in session
    assert any(c.startswith("numera_oauth=") and "Max-Age=0" in c for c in set_cookies)


def test_signing_in_again_finds_the_account_by_subject_not_email(client, google):
    google_sign_in(client, google)
    first = current_user(client)
    client.cookies.clear()
    google_sign_in(client, google, email="ada.lovelace@gmail.com")
    assert current_user(client)["id"] == first["id"]


# ------------------------------------------------------------------ forged callbacks


def test_a_callback_with_the_wrong_state_is_refused(client, google):
    query = start(client, google)
    response = finish(client, query, state="not-the-state")
    assert location(response) == ("/login", {"error": "google_expired"})
    assert current_user(client) is None
    assert google.requests == []  # the code was never even redeemed


def test_a_callback_without_the_flow_cookie_is_refused(client, google):
    query = start(client, google)
    client.cookies.clear()
    assert location(finish(client, query)) == ("/login", {"error": "google_expired"})


def test_a_tampered_flow_cookie_is_refused(client, google):
    query = start(client, google)
    value = client.cookies.get("numera_oauth")
    client.cookies.set("numera_oauth", value[:-3] + "AAA")
    assert location(finish(client, query)) == ("/login", {"error": "google_expired"})


@pytest.mark.parametrize("claims, reason", [
    ({"aud": "someone-else.apps.googleusercontent.com"}, "google_bad_audience"),
    ({"iss": "https://evil.example"}, "google_bad_issuer"),
    ({"nonce": "replayed"}, "google_bad_nonce"),
    ({"exp": 1}, "google_token_expired"),
    ({"email_verified": False}, "google_email_unverified"),
])
def test_an_unacceptable_id_token_is_refused(client, google, claims, reason):
    response = google_sign_in(client, google, **claims)
    assert location(response) == ("/login", {"error": reason})
    assert current_user(client) is None


def test_a_rejected_code_is_reported_without_detail(client, google):
    google.status = 400
    assert location(google_sign_in(client, google)) == ("/login", {"error": "google_exchange_failed"})


def test_declining_consent_is_a_cancel_not_an_error(client, google):
    start(client, google)
    response = client.get("/api/auth/google/callback", params={"error": "access_denied"},
                          follow_redirects=False)
    assert location(response) == ("/login", {"error": "google_cancelled"})


def test_next_cannot_become_an_open_redirect(client, google):
    for hostile in ("https://evil.example", "//evil.example", "/\\evil.example"):
        google.nonce, google.claims = None, {}
        query = start(client, google, next=hostile)
        assert location(finish(client, query))[0] == "/"
        client.cookies.clear()
    google.nonce = None
    query = start(client, google, next="/account")
    assert location(finish(client, query))[0] == "/account"


# --------------------------------------------------------------------- existing accounts


def test_a_gmail_address_links_to_the_existing_account_and_ends_its_sessions(settings, google):
    owner = make_client(settings, google)
    sign_in(owner, email="ada@gmail.com", name="Ada")
    before = current_user(owner)

    browser = TestClient(owner.app)  # same deployment, a different browser
    google_sign_in(browser, google)

    assert current_user(browser)["id"] == before["id"]
    # Whoever registered the address first has been signed out.
    assert current_user(owner) is None


def test_an_address_google_is_not_the_authority_for_is_not_linked(settings, google):
    owner = make_client(settings, google)
    sign_in(owner, email="ada@example.com", name="Ada")
    browser = TestClient(owner.app)
    response = google_sign_in(browser, google, email="ada@example.com")
    assert location(response) == ("/login", {"error": "google_link_required"})
    assert current_user(browser) is None
    assert current_user(owner) is not None


def test_a_workspace_domain_is_authoritative(settings, google):
    owner = make_client(settings, google)
    sign_in(owner, email="ada@acme.com", name="Ada")
    browser = TestClient(owner.app)
    google_sign_in(browser, google, email="ada@acme.com", hd="acme.com")
    assert current_user(browser)["email"] == "ada@acme.com"


def test_a_suspended_account_cannot_sign_in_with_google(settings, google):
    admin = make_client(settings, google)
    sign_in(admin, email="admin@example.com")
    member = TestClient(admin.app)
    google_sign_in(member, google)
    member_id = current_user(member)["id"]
    assert admin.patch(f"/api/admin/users/{member_id}", json={"status": "suspended"}).status_code == 200
    member.cookies.clear()
    assert location(google_sign_in(member, google)) == ("/login", {"error": "account_suspended"})


# ------------------------------------------------------------------- sign-up policy


def test_closed_registration_applies_to_google_too(settings, google):
    client = make_client(settings, google, registration_enabled=False)
    sign_in(client, email="admin@example.com")  # the first account is always allowed
    stranger = TestClient(client.app)
    assert location(google_sign_in(stranger, google)) == ("/login", {"error": "registration_closed"})


def test_the_domain_restriction_applies_to_google_too(settings, google):
    client = make_client(settings, google, registration_allowed_domains="acme.com")
    assert location(google_sign_in(client, google)) == ("/login", {"error": "domain_not_allowed"})


# ------------------------------------------------------------- managing the connection


def test_a_google_only_account_can_set_a_password_then_disconnect(client, google):
    google_sign_in(client, google)
    client.headers["X-CSRF-Token"] = client.cookies.get("numera_csrf")

    methods = client.get("/api/auth/methods").json()
    assert methods["password"] is False and methods["google"]["email"] == "ada@gmail.com"

    # Disconnecting now would leave no way in.
    refused = client.delete("/api/auth/google")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "last_sign_in_method"

    changed = client.post("/api/auth/change-password", json={"new_password": TEST_PASSWORD})
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["has_password"] is True
    client.headers["X-CSRF-Token"] = client.cookies.get("numera_csrf")

    after = client.delete("/api/auth/google")
    assert after.status_code == 200 and after.json()["google"] is None
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "ada@gmail.com",
                                                "password": TEST_PASSWORD}).status_code == 200


def test_a_password_account_can_connect_google_from_its_account_page(settings, google):
    client = make_client(settings, google)
    sign_in(client, email="ada@example.com", name="Ada")
    google.nonce, google.claims = None, {"email": "ada.personal@gmail.com"}
    query = start(client, google, intent="link")
    assert location(finish(client, query)) == ("/account", {"google": "connected"})
    assert client.get("/api/auth/methods").json()["google"]["email"] == "ada.personal@gmail.com"

    # And that Google account now signs in to it.
    other = TestClient(client.app)
    google_sign_in(other, google, email="ada.personal@gmail.com")
    assert current_user(other)["email"] == "ada@example.com"


def test_a_link_cannot_be_finished_by_a_different_session(settings, google):
    first = make_client(settings, google)
    sign_in(first, email="one@example.com")
    query = start(first, google, intent="link")
    flow_cookie = first.cookies.get("numera_oauth")

    second = TestClient(first.app)
    sign_in(second, email="two@example.com")
    second.cookies.set("numera_oauth", flow_cookie)
    assert location(finish(second, query)) == ("/account", {"google_error": "google_expired"})


def test_one_google_account_cannot_be_connected_to_two_accounts(settings, google):
    client = make_client(settings, google)
    google_sign_in(client, google)  # g-123 now belongs to ada@gmail.com
    other = TestClient(client.app)
    sign_in(other, email="bob@example.com")
    google.nonce, google.claims = None, {}
    query = start(other, google, intent="link")
    assert location(finish(other, query)) == ("/account", {"google_error": "google_in_use"})
