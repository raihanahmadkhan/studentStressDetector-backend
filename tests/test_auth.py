"""Authentication integration checks use real PostgreSQL and mocked OIDC HTTP.

No Google account or provider network access is needed. Authlib still performs
its actual state, PKCE and signed ID-token validation in the provider tests.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from joserfc import jwt
from joserfc.jwk import RSAKey
import pytest
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.config import Settings
from app.database import get_db
from app.errors import install_error_handlers
from app.models import Session as AuthSession, User


ORIGIN = "http://localhost:5173"


@pytest.fixture
def auth_settings(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="development",
        frontend_origin=ORIGIN,
        session_secret="test-only-auth-secret-at-least-32-characters",
        enable_dev_auth=True,
        cookie_secure=False,
        oidc_client_id="",
        oidc_client_secret="",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def auth_client(db, auth_settings):
    app = FastAPI()
    app.state.test_client_host = "127.0.0.1"

    @app.middleware("http")
    async def test_client_address(request, call_next):
        request.scope["client"] = (app.state.test_client_host, 43210)
        return await call_next(request)

    app.add_middleware(
        SessionMiddleware,
        secret_key=auth_settings.session_secret,
        session_cookie="oidc_state",
        max_age=600,
        same_site="lax",
        https_only=auth_settings.cookie_secure,
    )
    app.dependency_overrides[get_db] = lambda: db
    install_error_handlers(app)
    app.include_router(auth.router)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


def _dev_login(client):
    response = client.post("/api/auth/dev-login", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def test_config_does_not_expose_secrets_and_missing_oidc_is_helpful(auth_client):
    response = auth_client.get("/api/auth/config")
    assert response.json() == {"oidc_enabled": False, "dev_login_enabled": True}
    assert response.headers["cache-control"] == "no-store"
    response = auth_client.get("/api/auth/login")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"
    assert auth_client.get("/api/me").status_code == 401


def test_opaque_session_is_hashed_expiring_rotated_and_revoked(auth_client, db):
    first = _dev_login(auth_client)
    raw_token = auth_client.cookies.get(auth.SESSION_COOKIE)
    session = db.scalar(select(AuthSession))
    assert session.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    assert session.token_hash != raw_token
    assert session.csrf_token == first["csrf_token"]
    assert session.expires_at > datetime.now(timezone.utc)
    assert session.user_id == db.scalar(select(User.id))
    assert auth_client.get("/api/me").json() == first

    second = _dev_login(auth_client)
    assert second["id"] == first["id"]
    assert second["csrf_token"] != first["csrf_token"]
    assert len(db.scalars(select(User)).all()) == 1
    sessions = db.scalars(select(AuthSession)).all()
    assert len(sessions) == 1
    assert sessions[0].token_hash != session.token_hash

    response = auth_client.post(
        "/api/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": second["csrf_token"]},
    )
    assert response.status_code == 204
    assert db.scalars(select(AuthSession)).all() == []
    assert auth_client.get("/api/me").status_code == 401


def test_session_cookie_attributes(auth_client, auth_settings):
    auth_settings.cookie_secure = True
    response = auth_client.post("/api/auth/dev-login", headers={"Origin": ORIGIN})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Domain=" not in cookie
    assert "Max-Age=604800" in cookie


@pytest.mark.parametrize(
    "headers, expected_code",
    [
        ({}, "ORIGIN_NOT_ALLOWED"),
        ({"Origin": "https://other.example"}, "ORIGIN_NOT_ALLOWED"),
        ({"Origin": "null"}, "ORIGIN_NOT_ALLOWED"),
        ({"Origin": ORIGIN}, "CSRF_INVALID"),
        ({"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, "CSRF_INVALID"),
    ],
)
def test_logout_rejects_csrf_and_foreign_origins(auth_client, db, headers, expected_code):
    _dev_login(auth_client)
    response = auth_client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == expected_code
    assert len(db.scalars(select(AuthSession)).all()) == 1


def test_expired_and_forged_session_cookies_are_rejected(auth_client, db):
    _dev_login(auth_client)
    session = db.scalar(select(AuthSession))
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert auth_client.get("/api/me").status_code == 401
    auth_client.cookies.clear()
    auth_client.cookies.set(auth.SESSION_COOKIE, "a-forged-session")
    assert auth_client.get("/api/me").status_code == 401


@pytest.mark.parametrize("gate", ["flag", "environment", "client", "host"])
def test_development_login_is_strictly_local(auth_client, auth_settings, gate):
    if gate == "flag":
        auth_settings.enable_dev_auth = False
    elif gate == "environment":
        auth_settings.app_env = "test"
    elif gate == "client":
        auth_client.app.state.test_client_host = "203.0.113.1"
    headers = {"Origin": ORIGIN}
    if gate == "host":
        headers["Host"] = "public.example"
    response = auth_client.post("/api/auth/dev-login", headers=headers)
    assert response.status_code == 404


def test_development_login_requires_origin_and_ignores_supplied_identity(auth_client, db):
    assert auth_client.post("/api/auth/dev-login").status_code == 403
    response = auth_client.post(
        "/api/auth/dev-login", headers={"Origin": ORIGIN}, json={"user_id": "someone-else"}
    )
    assert response.status_code == 200
    assert db.scalar(select(User)).oidc_subject == "local-student"


@pytest.fixture(scope="module")
def oidc_key():
    return RSAKey.generate_key(2048, parameters={"kid": "test-key"}, private=True)


@pytest.fixture
def oidc_provider(auth_settings, monkeypatch, oidc_key):
    auth_settings.oidc_client_id = "test-client-id"
    auth_settings.oidc_client_secret = "test-client-secret"
    provider = {"nonce": "", "override": {}, "requests": [], "omit_id_token": False}

    def transport(request):
        provider["requests"].append(request)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": auth.GOOGLE_ISSUER,
                "authorization_endpoint": f"{auth.GOOGLE_ISSUER}/authorize",
                "token_endpoint": f"{auth.GOOGLE_ISSUER}/token",
                "jwks_uri": f"{auth.GOOGLE_ISSUER}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [oidc_key.as_dict(private=False)]})
        if request.url.path == "/token":
            if provider.get("unavailable"):
                return httpx.Response(503, json={"error": "private-provider-details"})
            claims = {
                "iss": auth.GOOGLE_ISSUER,
                "sub": "google-student-123",
                "aud": auth_settings.oidc_client_id,
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
                "nonce": provider["nonce"],
            }
            claims.update(provider["override"])
            token = {"access_token": "ephemeral-access-token", "token_type": "Bearer"}
            if not provider["omit_id_token"]:
                token["id_token"] = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, oidc_key)
            return httpx.Response(200, json=token)
        raise AssertionError(f"Unexpected provider request: {request.url}")

    oauth = OAuth()
    google = oauth.register(
        "google",
        client_id=auth_settings.oidc_client_id,
        client_secret=auth_settings.oidc_client_secret,
        server_metadata_url=f"{auth.GOOGLE_ISSUER}/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid",
            "code_challenge_method": "S256",
            "transport": httpx.MockTransport(transport),
        },
    )
    monkeypatch.setattr(auth, "_oidc_client", lambda: google)
    return provider


def _begin_oidc(client, provider):
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    params = parse_qs(urlparse(response.headers["location"]).query)
    provider["nonce"] = params["nonce"][0]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["scope"] == ["openid"]
    return params


def test_oidc_validates_real_signed_token_and_stores_only_application_session(auth_client, db, oidc_provider):
    params = _begin_oidc(auth_client, oidc_provider)
    response = auth_client.get(
        "/api/auth/callback", params={"state": params["state"][0], "code": "one-time-code"}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == ORIGIN
    user = db.scalar(select(User))
    assert user.oidc_issuer == auth.GOOGLE_ISSUER
    assert user.oidc_subject == "google-student-123"
    assert auth_client.get("/api/me").status_code == 200
    exchange = next(r for r in oidc_provider["requests"] if r.url.path == "/token")
    token_form = parse_qs(exchange.content.decode())
    assert token_form["code_verifier"][0]
    assert token_form["code"] == ["one-time-code"]
    assert "ephemeral-access-token" not in response.text
    assert "oidc_state" not in auth_client.cookies
    replay = auth_client.get(
        "/api/auth/callback", params={"state": params["state"][0], "code": "one-time-code"}, follow_redirects=False
    )
    assert replay.status_code == 400


@pytest.mark.parametrize(
    "invalid_claims",
    [
        {"aud": "another-client"},
        {"iss": "https://untrusted.example"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"sub": ""},
    ],
)
def test_oidc_rejects_invalid_signed_claims(auth_client, db, oidc_provider, invalid_claims):
    params = _begin_oidc(auth_client, oidc_provider)
    oidc_provider["override"] = invalid_claims
    response = auth_client.get(
        "/api/auth/callback", params={"state": params["state"][0], "code": "one-time-code"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert db.scalar(select(AuthSession)) is None
    assert db.scalar(select(User)) is None


@pytest.mark.parametrize("failure", ["state", "missing_id_token", "provider_down"])
def test_oidc_failures_do_not_create_session_or_expose_provider_errors(auth_client, db, oidc_provider, failure):
    params = _begin_oidc(auth_client, oidc_provider)
    state = params["state"][0]
    if failure == "state":
        state = "wrong-state"
    elif failure == "missing_id_token":
        oidc_provider["omit_id_token"] = True
    else:
        oidc_provider["unavailable"] = True
    response = auth_client.get(
        "/api/auth/callback", params={"state": state, "code": "one-time-code"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert "private-provider-details" not in json.dumps(response.json())
    assert db.scalar(select(AuthSession)) is None
    assert "oidc_state" not in auth_client.cookies
