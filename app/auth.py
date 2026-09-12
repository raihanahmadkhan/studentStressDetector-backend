"""Google OIDC sign-in and revocable, opaque application sessions.

The signed ``oidc_state`` cookie is only a short-lived login transaction. The
application cookie holds a random token; only its SHA-256 hash is stored in SQL.
Google access/ID tokens are never stored or sent to the frontend.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import ipaddress
import secrets
from uuid import uuid4

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DatabaseSession
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.models import Session as AuthSession, User


router = APIRouter(tags=["authentication"])
SESSION_COOKIE = "wellbeing_session"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_ISSUERS = (GOOGLE_ISSUER, "accounts.google.com")
OIDC_LOGIN_TTL_SECONDS = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _oidc_configured() -> bool:
    settings = get_settings()
    return bool(settings.oidc_client_id and settings.oidc_client_secret)


@lru_cache(maxsize=1)
def _oidc_client():
    settings = get_settings()
    oauth = OAuth()
    return oauth.register(
        "google",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=f"{GOOGLE_ISSUER}/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid",
            "code_challenge_method": "S256",
            "timeout": 10.0,
        },
    )


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _dev_login_available(request: Request) -> bool:
    settings = get_settings()
    return bool(
        settings.enable_dev_auth
        and settings.app_env == "development"
        and request.client
        and _is_loopback(request.client.host)
        and _is_loopback(request.url.hostname)
    )


def _require_origin(request: Request) -> None:
    # Reject missing/null origins as well as other sites. Non-browser clients
    # must deliberately supply the same origin and CSRF contract as the SPA.
    if request.headers.get("origin") != str(get_settings().frontend_origin).rstrip("/"):
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "This request came from an untrusted origin.")


def get_current_user(
    request: Request, db: DatabaseSession = Depends(get_db)
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 256:
        raise ApiError(401, "AUTH_REQUIRED", "Sign in to continue.")
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == _token_hash(token),
            AuthSession.expires_at > _now(),
        )
    )
    if session is None:
        raise ApiError(401, "AUTH_REQUIRED", "Your session has expired. Sign in again.")
    user = db.get(User, session.user_id)
    if user is None:
        raise ApiError(401, "AUTH_REQUIRED", "Sign in to continue.")
    request.state.session = session
    return user


def require_csrf(request: Request, user: User = Depends(get_current_user)) -> User:
    _require_origin(request)
    provided = request.headers.get("x-csrf-token", "")
    expected = request.state.session.csrf_token
    if not provided or len(provided) > 256 or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise ApiError(403, "CSRF_INVALID", "Refresh the page and try again.")
    return user


def _user_payload(user: User, session: AuthSession) -> dict:
    return {
        "id": str(user.id),
        "timezone": user.timezone,
        "history_version": user.history_version,
        "csrf_token": session.csrf_token,
        "llm_consent": user.llm_consent,
    }


def _start_session(
    request: Request, response: Response, db: DatabaseSession, issuer: str, subject: str
) -> tuple[User, AuthSession]:
    # The issuer/subject uniqueness constraint resolves concurrent first logins.
    db.execute(
        insert(User)
        .values(id=uuid4(), oidc_issuer=issuer, oidc_subject=subject)
        .on_conflict_do_nothing(index_elements=[User.oidc_issuer, User.oidc_subject])
    )
    user = db.scalar(
        select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
    )
    previous_token = request.cookies.get(SESSION_COOKIE)
    if previous_token and len(previous_token) <= 256:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(previous_token)))
    raw_token = secrets.token_urlsafe(32)
    settings = get_settings()
    session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        csrf_token=secrets.token_urlsafe(32),
        created_at=_now(),
        expires_at=_now() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return user, session


@router.get("/api/auth/config")
def auth_config(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {
        "oidc_enabled": _oidc_configured(),
        "dev_login_enabled": _dev_login_available(request),
    }


@router.get("/api/auth/login")
async def login(request: Request):
    if not _oidc_configured():
        raise ApiError(
            503, "AUTH_NOT_CONFIGURED", "Sign-in is temporarily unavailable. Please try again later."
        )
    # Clear previous login transactions to bound cookie size. Authlib stores its
    # state and PKCE verifier in this same temporary signed session cookie.
    request.session.clear()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    request.session.update(
        oidc_expected_state=state, oidc_expected_nonce=nonce, oidc_started_at=_now().timestamp()
    )
    try:
        response = await _oidc_client().authorize_redirect(
            request,
            # Public browser origin is explicit. Never derive the callback from
            # Render's upstream Host or untrusted forwarded-host headers.
            get_settings().frontend_origin + "/api/auth/callback",
            state=state,
            nonce=nonce,
        )
    except Exception:
        # Provider/library failures are isolated here; never return tokens,
        # provider error descriptions, URLs with codes, or client secrets.
        request.session.clear()
        raise ApiError(503, "AUTH_PROVIDER_UNAVAILABLE", "Sign-in is temporarily unavailable.") from None
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/auth/callback", name="oidc_callback")
async def oidc_callback(request: Request, db: DatabaseSession = Depends(get_db)):
    if not _oidc_configured():
        raise ApiError(
            503, "AUTH_NOT_CONFIGURED", "Sign-in is temporarily unavailable. Please try again later."
        )
    expected_state = request.session.get("oidc_expected_state")
    expected_nonce = request.session.get("oidc_expected_nonce")
    started_at = request.session.get("oidc_started_at")
    actual_state = request.query_params.get("state", "")
    age = _now().timestamp() - started_at if isinstance(started_at, (int, float)) else -1
    if (
        not isinstance(expected_state, str)
        or not isinstance(expected_nonce, str)
        or len(actual_state) > 256
        or not secrets.compare_digest(expected_state.encode(), actual_state.encode())
        or not 0 <= age <= OIDC_LOGIN_TTL_SECONDS
    ):
        request.session.clear()
        raise ApiError(400, "AUTHENTICATION_FAILED", "The sign-in attempt expired or could not be verified.")
    try:
        token = await _oidc_client().authorize_access_token(
            request,
            claims_options={
                "iss": {"essential": True, "values": list(GOOGLE_ISSUERS)},
                "sub": {"essential": True},
                "aud": {"essential": True},
                "exp": {"essential": True},
                "iat": {"essential": True},
                "nonce": {"essential": True},
            },
            leeway=30,
        )
        # Authlib verifies JWT signature, audience, expiry and nonce. These
        # application checks also reject an absent ID token or unusual issuer.
        claims = token.get("userinfo")
        if not token.get("id_token") or not isinstance(claims, dict):
            raise ValueError("Missing verified identity")
        subject = claims.get("sub")
        if (
            claims.get("iss") not in GOOGLE_ISSUERS
            or not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
            or claims.get("nonce") != expected_nonce
        ):
            raise ValueError("Invalid verified identity")
    except Exception:
        request.session.clear()
        raise ApiError(400, "AUTHENTICATION_FAILED", "Sign-in could not be verified. Please try again.") from None
    request.session.clear()
    response = RedirectResponse(str(get_settings().frontend_origin).rstrip("/"), status_code=303)
    await run_in_threadpool(_start_session, request, response, db, GOOGLE_ISSUER, subject)
    return response


@router.post("/api/auth/dev-login")
def dev_login(request: Request, response: Response, db: DatabaseSession = Depends(get_db)):
    if not _dev_login_available(request):
        raise ApiError(404, "NOT_FOUND", "This endpoint is unavailable.")
    _require_origin(request)
    user, session = _start_session(request, response, db, "local-development", "local-student")
    return _user_payload(user, session)


@router.get("/api/me")
def me(request: Request, response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return _user_payload(user, request.state.session)


@router.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(require_csrf),
    db: DatabaseSession = Depends(get_db),
):
    db.execute(delete(AuthSession).where(AuthSession.id == request.state.session.id))
    db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=get_settings().cookie_secure, httponly=True, samesite="lax"
    )
    request.session.clear()
    response.headers["Cache-Control"] = "no-store"
