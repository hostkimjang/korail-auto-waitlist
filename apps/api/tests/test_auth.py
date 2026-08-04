from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from rail_waitlist.config import get_settings
from rail_waitlist.models import AdminAccount

CREDENTIALS = {"username": "Rail.Admin", "password": "correct horse battery staple"}


async def register(public_client):
    return await public_client.post("/api/v1/auth/register", json=CREDENTIALS)


async def test_initial_registration_is_disabled_by_default(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        status = await client.get("/api/v1/auth/status")
        registration = await client.post("/api/v1/auth/register", json=CREDENTIALS)

    assert status.json()["configured"] is False
    assert status.json()["registration_allowed"] is False
    assert registration.status_code == 403
    assert registration.json() == {
        "detail": "initial administrator registration is disabled"
    }


async def test_register_persists_single_admin_and_authenticates(public_client, app):
    initial = (await public_client.get("/api/v1/auth/status")).json()
    assert initial == {
        "configured": False,
        "authenticated": False,
        "registration_allowed": True,
        "session_expires_at": None,
    }

    result = await register(public_client)

    assert result.status_code == 200
    assert result.json()["authenticated"] is True
    status = (await public_client.get("/api/v1/auth/status")).json()
    assert status["configured"] is True
    assert status["authenticated"] is True
    assert status["registration_allowed"] is False

    async with app.state.test_session_factory() as session:
        account = await session.scalar(select(AdminAccount))
        assert account is not None
        assert account.username == "rail.admin"
        assert account.password_hash != CREDENTIALS["password"]
        assert account.password_hash.startswith("$argon2id$")


async def test_second_registration_is_rejected_without_replacing_account(public_client, app):
    assert (await register(public_client)).status_code == 200
    get_settings().auth_initial_registration_enabled = False

    second = await public_client.post(
        "/api/v1/auth/register",
        json={"username": "someone.else", "password": "another valid password"},
    )

    assert second.status_code == 409
    async with app.state.test_session_factory() as session:
        accounts = list((await session.scalars(select(AdminAccount))).all())
        assert len(accounts) == 1
        assert accounts[0].username == "rail.admin"


async def test_concurrent_first_registration_creates_one_account_and_one_session(
    app, registration_enabled
):
    async def attempt(client_ip: str, username: str):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(client_ip, 1234)),
            base_url="https://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json={"username": username, "password": "correct horse battery staple"},
            )

    responses = await asyncio.gather(
        attempt("198.51.100.51", "first-admin"),
        attempt("198.51.100.52", "second-admin"),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert "rail_admin_session" not in rejected.headers.get("set-cookie", "")
    async with app.state.test_session_factory() as session:
        accounts = list((await session.scalars(select(AdminAccount))).all())
        assert len(accounts) == 1


async def test_logout_login_and_csrf(public_client):
    await register(public_client)
    assert (await public_client.get("/api/v1/providers")).status_code == 200
    assert (await public_client.post("/api/v1/auth/logout")).status_code == 403

    csrf = public_client.cookies.get("rail_csrf")
    logout = await public_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert logout.status_code == 204
    assert (await public_client.get("/api/v1/providers")).status_code == 401

    login = await public_client.post(
        "/api/v1/auth/login",
        json={"username": "  RAIL.ADMIN  ", "password": CREDENTIALS["password"]},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert (await public_client.get("/api/v1/providers")).status_code == 200


async def test_wrong_and_unknown_credentials_return_same_generic_error(public_client):
    await register(public_client)
    public_client.cookies.clear()

    wrong_password = await public_client.post(
        "/api/v1/auth/login",
        json={"username": "rail.admin", "password": "this password is incorrect"},
    )
    unknown_user = await public_client.post(
        "/api/v1/auth/login",
        json={"username": "unknown", "password": "this password is incorrect"},
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {
        "detail": "invalid username or password"
    }


async def test_unconfigured_login_uses_generic_unauthorized_response(public_client):
    response = await public_client.post(
        "/api/v1/auth/login",
        json={"username": "unknown", "password": "this password is incorrect"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid username or password"}


async def test_credentials_are_validated(public_client):
    short_username = await public_client.post(
        "/api/v1/auth/register",
        json={"username": "a", "password": "correct horse battery staple"},
    )
    invalid_username = await public_client.post(
        "/api/v1/auth/register",
        json={"username": "관리자", "password": "correct horse battery staple"},
    )
    short_password = await public_client.post(
        "/api/v1/auth/register",
        json={"username": "admin", "password": "too-short"},
    )
    assert short_username.status_code == 422
    assert invalid_username.status_code == 422
    assert short_password.status_code == 422


async def test_mutating_auth_endpoints_reject_missing_origin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        registration = await client.post("/api/v1/auth/register", json=CREDENTIALS)
        login = await client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert registration.status_code == 403
    assert login.status_code == 403


async def test_registration_is_rate_limited_by_direct_client_ip(app, registration_enabled):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("198.51.100.41", 1234)),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        responses = [
            await client.post(
                "/api/v1/auth/register",
                json=CREDENTIALS,
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            for index in range(6)
        ]
    assert responses[0].status_code == 200
    assert [response.status_code for response in responses[1:5]] == [409] * 4
    assert responses[5].status_code == 429
    assert int(responses[5].headers["Retry-After"]) >= 1


async def test_login_is_rate_limited_by_direct_client_ip(app):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("198.51.100.42", 1234)),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        responses = [
            await client.post(
                "/api/v1/auth/login",
                json={"username": "unknown", "password": "this password is incorrect"},
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            for index in range(11)
        ]
    assert [response.status_code for response in responses[:10]] == [401] * 10
    assert responses[10].status_code == 429
