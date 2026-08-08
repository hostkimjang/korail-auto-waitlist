from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_auth.models import AdminAccount, AdminSession
from .admin_auth.schemas import AuthStatus, LoginResult, UsernamePasswordCredentials
from .config import Settings, get_settings
from .database import get_session

SESSION_COOKIE = "rail_admin_session"
CSRF_COOKIE = "rail_csrf"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REGISTRATION_ATTEMPTS_PER_MINUTE = 5
LOGIN_ATTEMPTS_PER_MINUTE = 10
INVALID_LOGIN_DETAIL = "invalid username or password"

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]

# PasswordHasher uses Argon2id by default. The dummy hash makes unknown-user and wrong-password
# paths perform the same expensive verification operation without storing a second account.
password_hasher = PasswordHasher(type=Type.ID)
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-account-password")


class AuthRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, bucket: str, request: Request, limit: int) -> None:
        # X-Forwarded-For is intentionally ignored; trusted-proxy handling belongs at the edge.
        client_ip = request.client.host if request.client else "unknown"
        key = (bucket, client_ip)
        now = time.monotonic()
        async with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= limit:
                retry_after = max(1, int(60 - (now - attempts[0])))
                raise HTTPException(
                    429,
                    "too many authentication attempts",
                    headers={"Retry-After": str(retry_after)},
                )
            attempts.append(now)


auth_rate_limiter = AuthRateLimiter()


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def keyed_hash(purpose: str, value: str, settings: Settings | None = None) -> str:
    key = (settings or get_settings()).session_signing_key()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def sign_session_token(raw: str, settings: Settings | None = None) -> str:
    signature = hmac.new(
        (settings or get_settings()).session_signing_key(), raw.encode(), hashlib.sha256
    ).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{raw}.{encoded}"


def verify_session_token(token: str, settings: Settings | None = None) -> str | None:
    try:
        raw, supplied = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = sign_session_token(raw, settings).rsplit(".", 1)[1]
    return raw if hmac.compare_digest(supplied, expected) else None


def require_trusted_origin(request: Request, settings: Settings | None = None) -> None:
    configured = settings or get_settings()
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in configured.auth_allowed_origins:
        raise HTTPException(403, "untrusted or missing Origin")


async def resolve_admin_session(
    session: AsyncSession, token: str | None, *, touch: bool = False
) -> AdminSession | None:
    if not token:
        return None
    raw = verify_session_token(token)
    if raw is None:
        return None
    admin_session = await session.scalar(
        select(AdminSession).where(AdminSession.token_hash == keyed_hash("session", raw))
    )
    if (
        admin_session is None
        or admin_session.revoked_at is not None
        or ensure_aware(admin_session.expires_at) <= utcnow()
    ):
        return None
    if touch:
        admin_session.last_seen_at = utcnow()
    return admin_session


async def require_admin(
    request: Request,
    session: SessionDependency,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> AdminSession:
    admin_session = await resolve_admin_session(session, session_cookie, touch=True)
    if admin_session is None:
        raise HTTPException(401, "admin authentication required")
    if request.method in UNSAFE_METHODS:
        require_trusted_origin(request)
        if (
            not csrf_cookie
            or not csrf_header
            or not hmac.compare_digest(csrf_cookie, csrf_header)
            or not hmac.compare_digest(admin_session.csrf_hash, keyed_hash("csrf", csrf_header))
        ):
            raise HTTPException(403, "CSRF validation failed")
    await session.commit()
    return admin_session


def set_auth_cookies(
    response: Response, token: str, csrf: str, expires_at: datetime, settings: Settings
) -> None:
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        expires=expires_at,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        expires=expires_at,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


def prepare_session(session: AsyncSession) -> tuple[AdminSession, str, str]:
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    record = AdminSession(
        token_hash=keyed_hash("session", raw),
        csrf_hash=keyed_hash("csrf", csrf),
        expires_at=utcnow() + timedelta(hours=settings.auth_session_hours),
    )
    session.add(record)
    return record, sign_session_token(raw), csrf


async def count_accounts(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(AdminAccount)) or 0)


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(password_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(await asyncio.to_thread(password_hasher.verify, password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@router.get("/status", response_model=AuthStatus)
async def auth_status(
    session: SessionDependency,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthStatus:
    count = await count_accounts(session)
    current = await resolve_admin_session(session, session_cookie)
    registration_allowed = count == 0 and get_settings().auth_initial_registration_enabled
    return AuthStatus(
        configured=count > 0,
        authenticated=current is not None,
        registration_allowed=registration_allowed,
        session_expires_at=current.expires_at if current else None,
    )


@router.post("/register", response_model=LoginResult)
async def register(
    data: UsernamePasswordCredentials,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> LoginResult:
    require_trusted_origin(request)
    await auth_rate_limiter.check("registration", request, REGISTRATION_ATTEMPTS_PER_MINUTE)
    if await count_accounts(session):
        raise HTTPException(409, "admin account is already configured")
    if not get_settings().auth_initial_registration_enabled:
        raise HTTPException(403, "initial administrator registration is disabled")

    account = AdminAccount(
        singleton_slot=1,
        username=data.username,
        password_hash=await hash_password(data.password),
    )
    session.add(account)
    admin_session, signed_token, csrf = prepare_session(session)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "admin account is already configured") from None

    set_auth_cookies(response, signed_token, csrf, admin_session.expires_at, get_settings())
    return LoginResult(authenticated=True, expires_at=admin_session.expires_at)


@router.post("/login", response_model=LoginResult)
async def login(
    data: UsernamePasswordCredentials,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> LoginResult:
    require_trusted_origin(request)
    await auth_rate_limiter.check("login", request, LOGIN_ATTEMPTS_PER_MINUTE)
    account = await session.scalar(
        select(AdminAccount).where(AdminAccount.username == data.username).with_for_update()
    )
    password_hash = account.password_hash if account is not None else DUMMY_PASSWORD_HASH
    verified = await verify_password(password_hash, data.password)
    if account is None or not verified:
        raise HTTPException(401, INVALID_LOGIN_DETAIL)

    if await asyncio.to_thread(password_hasher.check_needs_rehash, account.password_hash):
        account.password_hash = await hash_password(data.password)
        account.password_changed_at = utcnow()
    account.last_login_at = utcnow()
    admin_session, signed_token, csrf = prepare_session(session)
    await session.commit()
    set_auth_cookies(response, signed_token, csrf, admin_session.expires_at, get_settings())
    return LoginResult(authenticated=True, expires_at=admin_session.expires_at)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session: SessionDependency,
    current: Annotated[AdminSession, Depends(require_admin)],
) -> Response:
    current.revoked_at = utcnow()
    await session.commit()
    clear_auth_cookies(response, get_settings())
    response.status_code = 204
    return response
