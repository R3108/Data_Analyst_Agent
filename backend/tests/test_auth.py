"""Accounts, sessions, CSRF, roles and the isolation between two people's data.

The tests that matter most here are the negative ones. Anyone can check that a correct
password signs you in; what a sellable deployment needs to know is that a wrong one
does not, that a second user cannot read the first's datasets, that a stolen cookie
stops working when the password changes, and that a cross-site form post is refused.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import Settings
from app.main import create_app
from tests.conftest import TEST_PASSWORD, ScriptedLLM, sign_in


@pytest.fixture
def app_client(settings):
    with TestClient(create_app(settings, llm=object())) as client:
        yield client


def register(client: TestClient, email: str, *, password: str = TEST_PASSWORD,
             name: str = "Person") -> dict:
    response = client.post("/api/auth/register",
                           json={"email": email, "password": password, "name": name},
                           headers=csrf(client))
    # Mirror what the browser app does: once a session cookie exists, every write on
    # this client carries the matching CSRF token. Without it the *next* call fails the
    # double-submit check, which is the behaviour, not a test artefact.
    _sync_csrf(client)
    return {"status": response.status_code, "body": response.json(), "response": response}


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("numera_csrf") or ""}


def _sync_csrf(client: TestClient) -> None:
    token = client.cookies.get("numera_csrf")
    if token:
        client.headers["X-CSRF-Token"] = token
    else:
        client.headers.pop("X-CSRF-Token", None)


# ------------------------------------------------------------------- registration


def test_registering_creates_an_account_and_signs_it_in(app_client):
    result = register(app_client, "ada@example.com", name="Ada Lovelace")
    assert result["status"] == 201
    user = result["body"]["user"]
    assert user["email"] == "ada@example.com"
    assert user["name"] == "Ada Lovelace"
    # The very first account on an empty deployment runs it.
    assert user["role"] == "admin"
    # Nothing secret is ever echoed back — no hash, and certainly no plaintext.
    assert "password_hash" not in user
    assert TEST_PASSWORD not in str(result["body"])
    assert app_client.cookies.get("numera_session")
    assert app_client.get("/api/auth/session").json()["user"]["id"] == user["id"]


def test_the_session_cookie_is_http_only_so_a_script_cannot_read_it(app_client):
    response = register(app_client, "ada@example.com")["response"]
    session_cookie = next(
        header for header in response.headers.get_list("set-cookie")
        if header.startswith("numera_session=")
    )
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    # The CSRF cookie is deliberately readable — the app has to echo it in a header.
    csrf_cookie = next(
        header for header in response.headers.get_list("set-cookie")
        if header.startswith("numera_csrf=")
    )
    assert "HttpOnly" not in csrf_cookie


def test_the_second_account_is_an_ordinary_user(app_client):
    register(app_client, "ada@example.com")
    app_client.post("/api/auth/logout", headers=csrf(app_client))
    assert register(app_client, "grace@example.com")["body"]["user"]["role"] == "user"


def test_a_duplicate_email_is_refused(app_client):
    register(app_client, "ada@example.com")
    result = register(app_client, "Ada@Example.com ")  # case and spacing do not matter
    assert result["status"] == 409
    assert result["body"]["error"]["code"] == "email_taken"


def test_a_weak_password_is_refused_with_a_usable_reason(app_client):
    result = register(app_client, "ada@example.com", password="short")
    assert result["status"] == 422
    assert result["body"]["error"]["code"] == "weak_password"
    assert str(security.MIN_PASSWORD_LENGTH) in result["body"]["error"]["message"]


def test_a_password_containing_the_email_is_refused(app_client):
    result = register(app_client, "lovelace@example.com", password="lovelace-and-more-text")
    assert result["status"] == 422
    assert result["body"]["error"]["code"] == "weak_password"


def test_rejected_attempts_do_not_count_against_the_sign_up_limit(tmp_path):
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40, register_max_per_hour=2)
    with TestClient(create_app(settings, llm=object())) as client:
        # Somebody fighting with the password rules is not creating accounts, and must
        # not be locked out of signing up because of it.
        for _ in range(6):
            assert register(client, "ada@example.com", password="short")["status"] == 422
        assert register(client, "ada@example.com")["status"] == 201


def test_registration_can_be_closed_after_the_first_account(tmp_path):
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40, registration_enabled=False)
    with TestClient(create_app(settings, llm=object())) as client:
        # The first account is always allowed, or a closed deployment is unreachable.
        assert register(client, "ada@example.com")["status"] == 201
        client.post("/api/auth/logout", headers=csrf(client))
        blocked = register(client, "eve@example.com")
        assert blocked["status"] == 403
        assert blocked["body"]["error"]["code"] == "registration_closed"


def test_signup_can_be_limited_to_a_domain(tmp_path):
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40,
                        registration_allowed_domains="acme.com")
    with TestClient(create_app(settings, llm=object())) as client:
        assert register(client, "someone@elsewhere.com")["status"] == 403
        assert register(client, "someone@acme.com")["status"] == 201


# ------------------------------------------------------------------------- sign-in


def test_sign_in_and_sign_out(app_client):
    register(app_client, "ada@example.com")
    app_client.post("/api/auth/logout", headers=csrf(app_client))
    assert app_client.get("/api/auth/session").json()["user"] is None

    response = app_client.post("/api/auth/login",
                               json={"email": "ada@example.com", "password": TEST_PASSWORD})
    assert response.status_code == 200
    assert app_client.get("/api/auth/session").json()["user"]["email"] == "ada@example.com"


def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(app_client):
    register(app_client, "ada@example.com")
    app_client.post("/api/auth/logout", headers=csrf(app_client))

    wrong = app_client.post("/api/auth/login",
                            json={"email": "ada@example.com", "password": "definitely-not-it"})
    unknown = app_client.post("/api/auth/login",
                              json={"email": "nobody@example.com", "password": TEST_PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    # Same code and same wording: the response cannot be used to enumerate accounts.
    assert wrong.json() == unknown.json()


def test_repeated_failures_lock_the_account_without_saying_so(tmp_path):
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40,
                        lockout_threshold=3, login_max_attempts=100)
    with TestClient(create_app(settings, llm=object())) as client:
        register(client, "ada@example.com")
        client.post("/api/auth/logout", headers=csrf(client))
        for _ in range(3):
            client.post("/api/auth/login",
                        json={"email": "ada@example.com", "password": "wrong-password-here"})
        # Even the *right* password is refused while the lock holds, and the message is
        # the same one a wrong password gets.
        locked = client.post("/api/auth/login",
                             json={"email": "ada@example.com", "password": TEST_PASSWORD})
        assert locked.status_code == 401
        assert locked.json()["error"]["code"] == "invalid_credentials"


def test_sign_in_attempts_are_rate_limited(tmp_path):
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40, login_max_attempts=3)
    with TestClient(create_app(settings, llm=object())) as client:
        register(client, "ada@example.com")
        client.post("/api/auth/logout", headers=csrf(client))
        codes = [
            client.post("/api/auth/login",
                        json={"email": "ada@example.com", "password": "nope-nope-nope"}).status_code
            for _ in range(6)
        ]
        assert 429 in codes
        limited = client.post("/api/auth/login",
                              json={"email": "ada@example.com", "password": "nope-nope-nope"})
        assert limited.status_code == 429
        assert limited.headers["Retry-After"]


# ---------------------------------------------------------------- protected routes


def test_every_data_route_requires_a_session(app_client):
    for path in ("/api/datasets", "/api/sessions", "/api/boards", "/api/monitors",
                 "/api/activity", "/api/usage", "/api/me"):
        response = app_client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "not_authenticated"


def test_health_and_share_stay_reachable_without_a_session(app_client):
    health = app_client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["authenticated"] is False
    # Anonymous health is liveness only — no model name, no limits, no alert config.
    assert "model" not in health.json()
    # An unknown share link is a 404, never a 401: it is a public endpoint.
    assert app_client.get("/api/share/usr_nope.whatever").status_code == 404


def test_a_revoked_session_stops_working_immediately(app_client):
    register(app_client, "ada@example.com")
    assert app_client.get("/api/datasets").status_code == 200
    app_client.post("/api/auth/sessions/revoke-all", headers=csrf(app_client))
    assert app_client.get("/api/datasets").status_code == 401


def test_changing_the_password_invalidates_other_devices(settings):
    with TestClient(create_app(settings, llm=object())) as first, \
         TestClient(create_app(settings, llm=object())) as second:
        register(first, "ada@example.com")
        second.post("/api/auth/login",
                    json={"email": "ada@example.com", "password": TEST_PASSWORD})
        assert second.get("/api/datasets").status_code == 200

        changed = first.post("/api/auth/change-password", headers=csrf(first), json={
            "current_password": TEST_PASSWORD, "new_password": "a-brand-new-long-passphrase",
        })
        assert changed.status_code == 200
        # The device that made the change keeps a freshly rotated session…
        assert first.get("/api/datasets").status_code == 200
        # …and every other one is signed out.
        assert second.get("/api/datasets").status_code == 401


def test_the_wrong_current_password_cannot_change_it(app_client):
    register(app_client, "ada@example.com")
    response = app_client.post("/api/auth/change-password", headers=csrf(app_client), json={
        "current_password": "not-the-current-one", "new_password": "a-brand-new-long-passphrase",
    })
    assert response.status_code == 401


# -------------------------------------------------------------------------- CSRF


def test_a_write_without_the_csrf_header_is_refused(app_client):
    register(app_client, "ada@example.com")
    # Exactly what a cross-site form post looks like: the browser attaches the session
    # cookie by itself, and the attacker cannot add the header.
    app_client.headers.pop("X-CSRF-Token", None)
    response = app_client.post("/api/boards", json={"title": "Forged"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_a_write_with_a_mismatched_csrf_token_is_refused(app_client):
    register(app_client, "ada@example.com")
    response = app_client.post("/api/boards", json={"title": "Forged"},
                               headers={"X-CSRF-Token": "not-the-one-in-the-cookie"})
    assert response.status_code == 403


def test_a_write_from_an_untrusted_origin_is_refused(app_client):
    sign_in(app_client, email="ada@example.com")
    response = app_client.post("/api/boards", json={"title": "Forged"},
                               headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"


def test_reads_need_no_csrf_token(app_client):
    register(app_client, "ada@example.com")
    assert app_client.get("/api/datasets").status_code == 200


# -------------------------------------------------------------- data isolation


def test_two_accounts_cannot_see_each_others_data(settings, tiny_csv_bytes):
    llm = ScriptedLLM({})
    with TestClient(create_app(settings, llm=llm)) as ada, \
         TestClient(create_app(settings, llm=llm)) as grace:
        sign_in(ada, email="ada@example.com", name="Ada")
        sign_in(grace, email="grace@example.com", name="Grace")

        uploaded = ada.post("/api/datasets",
                            files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
        board = ada.post("/api/boards", json={"title": "Ada's numbers"}).json()

        # Ada sees her own work.
        assert [d["id"] for d in ada.get("/api/datasets").json()] == [uploaded["id"]]
        # Grace's workspace is a different database entirely, not a filtered view of one.
        assert grace.get("/api/datasets").json() == []
        assert grace.get("/api/boards").json() == []
        # And addressing Ada's rows directly does not reach them either.
        assert grace.get(f"/api/datasets/{uploaded['id']}").status_code == 404
        assert grace.get(f"/api/boards/{board['id']}").status_code == 404
        assert grace.delete(f"/api/datasets/{uploaded['id']}",
                            headers=csrf(grace)).status_code == 404


def test_each_account_gets_its_own_database_file(settings, tiny_csv_bytes):
    with TestClient(create_app(settings, llm=object())) as ada, \
         TestClient(create_app(settings, llm=object())) as grace:
        first = sign_in(ada, email="ada@example.com").get("/api/me").json()["id"]
        second = sign_in(grace, email="grace@example.com").get("/api/me").json()["id"]
        # A workspace is built on first use, so touch one for each account.
        ada.get("/api/datasets")
        grace.get("/api/datasets")

    assert first != second
    for user_id in (first, second):
        assert (settings.users_dir / user_id / "numera.db").exists()
    # Nothing is left in the shared root but the control database and the user tree.
    assert not (settings.data_dir / "numera.db").exists()


def test_an_existing_single_user_database_is_adopted_by_the_first_account(settings, tiny_csv_bytes):
    """Upgrading a pre-multi-user install must not orphan the work already in it."""
    # Build a single-tenant workspace the way the old version laid it out: a numera.db
    # and a datasets/ directory directly under DATA_DIR.
    legacy = settings.data_dir
    legacy.mkdir(parents=True, exist_ok=True)
    from app.db import Database
    from app.services.datasets import DatasetService

    service = DatasetService(settings, Database(settings.database_path))
    record = service.ingest_stream("sales.csv", io.BytesIO(tiny_csv_bytes))
    assert (legacy / "numera.db").exists()

    with TestClient(create_app(settings, llm=object())) as client:
        sign_in(client, email="ada@example.com")
        user_id = client.get("/api/me").json()["id"]

        adopted = client.get("/api/datasets").json()
        assert [d["id"] for d in adopted] == [record["id"]]
        # The files moved rather than being copied, so there is one of everything.
        assert (settings.users_dir / user_id / "numera.db").exists()
        assert not (legacy / "numera.db").exists()
        # And the cleaned table came with it, so the data is still readable.
        assert client.get(f"/api/datasets/{record['id']}/preview?limit=2").status_code == 200


def test_a_share_link_is_public_but_scoped_to_its_owner(settings, tiny_csv_bytes):
    with TestClient(create_app(settings, llm=object())) as ada, \
         TestClient(create_app(settings, llm=object())) as anonymous:
        sign_in(ada, email="ada@example.com")
        board = ada.post("/api/boards", json={"title": "Quarterly"}).json()
        token = ada.post(f"/api/boards/{board['id']}/share", headers=csrf(ada)).json()["token"]

        # The owner's account id prefixes the token, which is how a signed-out request
        # finds the right workspace.
        assert token.startswith("usr_") and "." in token
        shared = anonymous.get(f"/api/share/{token}")
        assert shared.status_code == 200
        assert shared.json()["title"] == "Quarterly"

        # Revoking it closes the link for everybody.
        ada.delete(f"/api/boards/{board['id']}/share", headers=csrf(ada))
        assert anonymous.get(f"/api/share/{token}").status_code == 404


# ------------------------------------------------------------------ roles & admin


def test_admin_routes_are_closed_to_ordinary_users(settings):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as member:
        sign_in(owner, email="ada@example.com")       # first account → admin
        sign_in(member, email="grace@example.com")    # second → user

        assert owner.get("/api/admin/overview").status_code == 200
        forbidden = member.get("/api/admin/overview")
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "admin_required"
        assert member.get("/api/admin/users").status_code == 403


def test_an_admin_sees_accounts_and_usage_but_not_contents(settings, tiny_csv_bytes):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as member:
        sign_in(owner, email="ada@example.com")
        sign_in(member, email="grace@example.com")
        member.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")})

        users = owner.get("/api/admin/users").json()
        grace = next(u for u in users if u["email"] == "grace@example.com")
        assert grace["usage"]["datasets"] == 1
        assert grace["usage"]["storage_bytes"] > 0
        # Metadata only — no route returns another account's rows.
        assert "profile" not in grace["usage"]
        assert "password_hash" not in grace


def test_an_admin_can_suspend_an_account_and_end_its_sessions(settings):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as member:
        sign_in(owner, email="ada@example.com")
        sign_in(member, email="grace@example.com")
        member_id = member.get("/api/me").json()["id"]
        assert member.get("/api/datasets").status_code == 200

        owner.patch(f"/api/admin/users/{member_id}", headers=csrf(owner),
                    json={"status": "suspended"})
        assert member.get("/api/datasets").status_code == 401
        refused = member.post("/api/auth/login",
                              json={"email": "grace@example.com", "password": TEST_PASSWORD})
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "account_suspended"


def test_the_last_administrator_cannot_be_demoted(settings):
    with TestClient(create_app(settings, llm=object())) as owner:
        sign_in(owner, email="ada@example.com")
        own_id = owner.get("/api/me").json()["id"]
        response = owner.patch(f"/api/admin/users/{own_id}", headers=csrf(owner),
                               json={"role": "user"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "last_admin"


def test_a_demotion_takes_effect_on_the_next_request(settings):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as second:
        sign_in(owner, email="ada@example.com")
        sign_in(second, email="grace@example.com")
        second_id = second.get("/api/me").json()["id"]

        owner.patch(f"/api/admin/users/{second_id}", headers=csrf(owner), json={"role": "admin"})
        second.post("/api/auth/login",
                    json={"email": "grace@example.com", "password": TEST_PASSWORD})
        assert second.get("/api/admin/overview").status_code == 200

        owner.patch(f"/api/admin/users/{second_id}", headers=csrf(owner), json={"role": "user"})
        # The role change signed the session out rather than leaving it admin-shaped.
        assert second.get("/api/admin/overview").status_code == 401


def test_an_admin_created_account_must_choose_its_own_password(settings):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as invited:
        sign_in(owner, email="ada@example.com")
        created = owner.post("/api/admin/users", headers=csrf(owner), json={
            "email": "newhire@example.com", "password": "temporary-starter-phrase",
            "name": "New Hire", "role": "user",
        })
        assert created.status_code == 201

        invited.post("/api/auth/login", json={"email": "newhire@example.com",
                                              "password": "temporary-starter-phrase"})
        # The temporary password buys exactly one thing: the chance to replace it.
        blocked = invited.get("/api/datasets")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "password_change_required"
        assert invited.get("/api/me").json()["must_change_password"] is True

        invited.headers["X-CSRF-Token"] = invited.cookies.get("numera_csrf")
        invited.post("/api/auth/change-password", json={
            "current_password": "temporary-starter-phrase",
            "new_password": "a-password-of-their-own-choosing",
        })
        invited.headers["X-CSRF-Token"] = invited.cookies.get("numera_csrf")
        assert invited.get("/api/datasets").status_code == 200


def test_deleting_an_account_erases_its_workspace(settings, tiny_csv_bytes):
    with TestClient(create_app(settings, llm=object())) as owner, \
         TestClient(create_app(settings, llm=object())) as member:
        sign_in(owner, email="ada@example.com")
        sign_in(member, email="grace@example.com")
        member.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")})
        member_id = member.get("/api/me").json()["id"]
        workspace = settings.users_dir / member_id
        assert workspace.exists()

        # The email has to be typed back: this erases somebody's work.
        mistyped = owner.delete(f"/api/admin/users/{member_id}?confirm_email=wrong@example.com",
                                headers=csrf(owner))
        assert mistyped.status_code == 403
        assert workspace.exists()

        deleted = owner.delete(f"/api/admin/users/{member_id}?confirm_email=grace@example.com",
                               headers=csrf(owner))
        assert deleted.status_code == 200
        assert not workspace.exists()
        assert member.get("/api/datasets").status_code == 401


def test_an_admin_cannot_delete_their_own_account(settings):
    with TestClient(create_app(settings, llm=object())) as owner:
        sign_in(owner, email="ada@example.com")
        own_id = owner.get("/api/me").json()["id"]
        response = owner.delete(f"/api/admin/users/{own_id}?confirm_email=ada@example.com",
                                headers=csrf(owner))
        assert response.status_code == 403


# ------------------------------------------------------------------ password reset


@pytest.fixture
def reset_settings(tmp_path):
    return Settings(_env_file=None, environment="test", data_dir=tmp_path / "data",
                    openai_api_key="k", auth_secret="x" * 40, expose_reset_link=True,
                    public_base_url="http://localhost:3000")


def test_a_reset_link_sets_a_new_password_once(reset_settings):
    with TestClient(create_app(reset_settings, llm=object())) as client:
        register(client, "ada@example.com")
        client.post("/api/auth/logout", headers=csrf(client))

        asked = client.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
        assert asked.status_code == 200
        token = asked.json()["reset_link"].split("token=")[1]

        changed = client.post("/api/auth/reset-password",
                              json={"token": token, "password": "a-fresh-long-passphrase"})
        assert changed.status_code == 200
        assert client.get("/api/auth/session").json()["user"]["email"] == "ada@example.com"

        # Single use: the same link cannot be replayed.
        _sync_csrf(client)
        replayed = client.post("/api/auth/reset-password",
                               json={"token": token, "password": "yet-another-passphrase"})
        assert replayed.status_code == 401
        assert replayed.json()["error"]["code"] == "invalid_reset_token"


def test_an_unknown_address_gets_the_same_answer(reset_settings):
    with TestClient(create_app(reset_settings, llm=object())) as client:
        register(client, "ada@example.com")
        known = client.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
        unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        assert known.status_code == unknown.status_code == 200
        assert known.json()["message"] == unknown.json()["message"]
        # The link is only ever present for an address that exists, and only on a
        # development server that has opted in.
        assert "reset_link" not in unknown.json()


def test_a_forged_reset_token_is_refused(reset_settings):
    with TestClient(create_app(reset_settings, llm=object())) as client:
        register(client, "ada@example.com")
        response = client.post("/api/auth/reset-password",
                               json={"token": security.new_token(), "password": "long-enough-phrase"})
        assert response.status_code == 401


def test_a_reset_link_is_never_exposed_in_production(tmp_path):
    settings = Settings(_env_file=None, environment="production", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40, expose_reset_link=True,
                        cookie_secure=False, cors_origins="http://testserver")
    with TestClient(create_app(settings, llm=object())) as client:
        register(client, "ada@example.com")
        response = client.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
        assert response.status_code == 200
        assert "reset_link" not in response.json()


# ------------------------------------------------------------------- stored secrets


def test_no_password_is_stored_in_a_readable_form(app_client, settings):
    register(app_client, "ada@example.com")
    raw = settings.control_database_path.read_bytes()
    assert TEST_PASSWORD.encode() not in raw
    # And the session cookie's value is not sitting in the table either.
    assert app_client.cookies.get("numera_session").encode() not in raw


def test_production_refuses_to_start_without_a_secret(tmp_path):
    settings = Settings(_env_file=None, environment="production", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret=None)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app(settings, llm=object())


def test_production_hides_the_interactive_docs(tmp_path):
    settings = Settings(_env_file=None, environment="production", data_dir=tmp_path / "data",
                        openai_api_key="k", auth_secret="x" * 40, cookie_secure=False)
    with TestClient(create_app(settings, llm=object())) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
