from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rail_waitlist.auth import CSRF_COOKIE, SESSION_COOKIE, keyed_hash, sign_session_token
from rail_waitlist.config import get_settings
from rail_waitlist.database import Base, get_session
from rail_waitlist.main import create_app
from rail_waitlist.models import AdminSession


@pytest.fixture(autouse=True)
def isolate_test_origin():
    """Keep repository .env values from changing deterministic request tests."""
    settings = get_settings()
    previous_origins = settings.auth_allowed_origins
    feature_flag_names = (
        "experimental_rail_enabled",
        "srt_seat_status_enabled",
        "srt_seat_monitoring_enabled",
        "srt_reservation_once_enabled",
        "srt_provider_adapter_enabled",
        "korail_browser_bridge_enabled",
        "korail_browser_adapter_enabled",
        "korail_seat_monitoring_enabled",
        "korail_reservation_once_enabled",
    )
    previous_feature_flags = {
        name: getattr(settings, name) for name in feature_flag_names
    }
    settings.auth_allowed_origins = ["http://localhost:3000"]
    for name in feature_flag_names:
        setattr(settings, name, False)
    try:
        yield
    finally:
        settings.auth_allowed_origins = previous_origins
        for name, value in previous_feature_flags.items():
            setattr(settings, name, value)


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app = create_app(session_factory=session_factory)
    app.dependency_overrides[get_session] = override_session
    app.state.test_session_factory = session_factory
    return app


@pytest_asyncio.fixture
async def public_client(app):
    settings = get_settings()
    previous = settings.auth_initial_registration_enabled
    settings.auth_initial_registration_enabled = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        yield client
    settings.auth_initial_registration_enabled = previous


@pytest_asyncio.fixture
async def registration_enabled():
    settings = get_settings()
    previous = settings.auth_initial_registration_enabled
    settings.auth_initial_registration_enabled = True
    yield
    settings.auth_initial_registration_enabled = previous


@pytest_asyncio.fixture
async def client(app):
    raw = "test-session-token"
    csrf = "test-csrf-token"
    async with app.state.test_session_factory() as session:
        session.add(
            AdminSession(
                token_hash=keyed_hash("session", raw),
                csrf_hash=keyed_hash("csrf", csrf),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
        cookies={SESSION_COOKIE: sign_session_token(raw), CSRF_COOKIE: csrf},
    ) as authenticated:
        yield authenticated
