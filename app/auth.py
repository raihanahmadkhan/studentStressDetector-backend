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
import unicodedata
from uuid import uuid4

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
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


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(strict=True, min_length=1, max_length=80)

    @field_validator('display_name', mode='before')
    @classmethod
    def clean_name(cls, value):
        if isinstance(value, str):
            if any(unicodedata.category(c) in ('Cc', 'Cs') or c in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069' for c in value):
                raise ValueError('Display name must not contain control characters.')
            return value.strip()
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _oidc_configured() -> bool:
    settings = get_settings()
    return bool(settings.oidc_client_id and settings.oidc_client_secret)


def _frontend_redirect(outcome: str) -> RedirectResponse:
    # An explicit destination query prevents proxy query-string passthrough.
    # This marker carries no identity or authorization data; the SPA removes it.
    origin = str(get_settings().frontend_origin).rstrip("/")
    marker = "signed_in=1" if outcome == "success" else "sign_in=" + ("cancelled" if outcome == "cancelled" else "failed")
    response = RedirectResponse(f"{origin}/?{marker}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


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
            "scope": "openid email profile",
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
        "display_name": user.display_name,
        "google_email": user.google_email,
    }


def _start_session(
    request: Request, response: Response, db: DatabaseSession, issuer: str, subject: str,
    profile: dict | None = None,
) -> tuple[User, AuthSession]:
    # The issuer/subject uniqueness constraint resolves concurrent first logins.
    db.execute(
        insert(User)
        .values(id=uuid4(), oidc_issuer=issuer, oidc_subject=subject)
        .on_conflict_do_nothing(index_elements=[User.oidc_issuer, User.oidc_subject])
    )
    user = db.scalar(
        select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject).with_for_update()
    )
    if profile is not None and issuer == GOOGLE_ISSUER:
        # Only claims from the verified ID token reach this function. Email is
        # display information; the issuer/subject pair remains the identity.
        email = profile.get('email')
        user.google_email = email if (profile.get('email_verified') is True
            and isinstance(email, str) and 1 <= len(email) <= 320
            and '@' in email and not any(c.isspace() or unicodedata.category(c).startswith('C') for c in email)) else None
        if user.display_name is None:
            try:
                user.display_name = AccountProfile(display_name=profile.get('name')).display_name
            except ValueError:
                pass  # Missing or unsuitable provider names never block sign-in.
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
        len(request.query_params.getlist('state')) != 1
        or (('error' in request.query_params) and (len(request.query_params.getlist('error')) != 1 or not request.query_params['error'] or 'code' in request.query_params))
        or (('error' not in request.query_params) and (len(request.query_params.getlist('code')) != 1 or not 1 <= len(request.query_params.get('code', '')) <= 4096))
        or len(request.query_params.getlist('iss')) > 1
        or ('iss' in request.query_params and request.query_params['iss'] not in GOOGLE_ISSUERS)
        or not isinstance(expected_state, str)
        or not isinstance(expected_nonce, str)
        or len(actual_state) > 256
        or not secrets.compare_digest(expected_state.encode(), actual_state.encode())
        or not 0 <= age <= OIDC_LOGIN_TTL_SECONDS
    ):
        request.session.clear()
        raise ApiError(400, "AUTHENTICATION_FAILED", "The sign-in attempt expired or could not be verified.")
    # Validate the login transaction even when consent is declined. Never reflect
    # provider descriptions or exchange a token on this path.
    if 'error' in request.query_params:
        request.session.clear()
        return _frontend_redirect('cancelled' if request.query_params['error'] == 'access_denied' else 'failed')
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
    response = _frontend_redirect("success")
    await run_in_threadpool(_start_session, request, response, db, GOOGLE_ISSUER, subject, claims)
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


@router.patch('/api/account')
def update_account(payload: AccountProfile, request: Request,
                   user: User = Depends(require_csrf), db: DatabaseSession = Depends(get_db)):
    user.display_name = payload.display_name
    db.commit()
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
