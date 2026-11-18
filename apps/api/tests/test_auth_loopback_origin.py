"""A browser on the server itself may use the loopback address after remote HTTPS is enabled.

The installer lists only the remote HTTPS origin once Tailscale access is configured. The API
also trusts a request whose Origin is exactly the loopback address the browser requested, and
does not mark cookies Secure for plain-HTTP loopback so every browser keeps the local session.
"""

from __future__ import annotations

import pytest
from httpx2 import ASGITransport, AsyncClient, Response

from rail_waitlist.config import get_settings

CREDENTIALS = {"username": "rail.admin", "password": "correct horse battery staple"}
REMOTE_ORIGIN = "https://railwait.example"


@pytest.fixture
def remote_only_settings(registration_enabled):
    settings = get_settings()
    previous = (settings.auth_allowed_origins, settings.auth_cookie_secure)
    settings.auth_allowed_origins = [REMOTE_ORIGIN]
    settings.auth_cookie_secure = True
    try:
        yield settings
    finally:
        settings.auth_allowed_origins, settings.auth_cookie_secure = previous


def client_for(app, base_url: str, origin: str, ip: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 1234)),
        base_url=base_url,
        headers={"Origin": origin},
    )


def auth_cookies(response: Response) -> list[str]:
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    return cookies


def is_secure(cookie: str) -> bool:
    return any(part.strip().lower() == "secure" for part in cookie.split(";")[1:])


async def test_loopback_same_origin_signs_in_and_saves_without_listing_it(
    app, remote_only_settings
):
    local = "http://127.0.0.1:8080"
    async with client_for(app, local, local, "198.51.100.61") as client:
        registered = await client.post("/api/v1/auth/register", json=CREDENTIALS)
        assert registered.status_code == 200
        assert not any(is_secure(cookie) for cookie in auth_cookies(registered))
        # Non-Secure cookies come back over plain HTTP, so the local session works.
        assert (await client.get("/api/v1/providers")).status_code == 200

        missing_csrf = await client.post("/api/v1/auth/logout")
        assert missing_csrf.status_code == 403
        assert missing_csrf.json() == {"detail": "CSRF validation failed"}

        csrf = client.cookies.get("rail_csrf")
        logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert logout.status_code == 204
        assert not any(is_secure(cookie) for cookie in auth_cookies(logout))

        login = await client.post("/api/v1/auth/login", json=CREDENTIALS)
        assert login.status_code == 200
        assert not any(is_secure(cookie) for cookie in auth_cookies(login))
        assert (await client.get("/api/v1/providers")).status_code == 200


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:9999"),
        ("http://127.0.0.1:8080", "http://localhost:8080"),
        ("http://rebind.example:8080", "http://rebind.example:8080"),
        ("http://192.168.0.10:8080", "http://192.168.0.10:8080"),
        ("http://127.0.0.1:8080", "null"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080/path"),
    ],
)
async def test_loopback_trust_requires_the_exact_requested_loopback_address(
    app, remote_only_settings, base_url, origin
):
    async with client_for(app, base_url, origin, "198.51.100.62") as client:
        response = await client.post("/api/v1/auth/login", json=CREDENTIALS)
    assert response.status_code == 403
    assert response.json() == {"detail": "untrusted or missing Origin"}


@pytest.mark.parametrize(
    "address", ["http://localhost:8080", "http://[::1]:8080", "http://127.0.0.2:8080"]
)
async def test_every_loopback_spelling_passes_the_origin_check(app, remote_only_settings, address):
    async with client_for(app, address, address, "198.51.100.63") as client:
        response = await client.post("/api/v1/auth/login", json=CREDENTIALS)
    # The origin is accepted; the request then fails only because no account exists.
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("fetch_site", "status"), [("cross-site", 403), ("same-site", 403), ("same-origin", 401)]
)
async def test_fetch_metadata_must_confirm_a_same_origin_loopback_request(
    app, remote_only_settings, fetch_site, status
):
    local = "http://127.0.0.1:8080"
    async with client_for(app, local, local, "198.51.100.65") as client:
        response = await client.post(
            "/api/v1/auth/login", json=CREDENTIALS, headers={"Sec-Fetch-Site": fetch_site}
        )
    # 401 means the origin passed and only the missing account stopped the login.
    assert response.status_code == status


@pytest.mark.parametrize("origin", [REMOTE_ORIGIN, "https://127.0.0.1:8443"])
async def test_https_origins_keep_secure_cookies(app, remote_only_settings, origin):
    async with client_for(app, origin, origin, "198.51.100.64") as client:
        registered = await client.post("/api/v1/auth/register", json=CREDENTIALS)
    assert registered.status_code == 200
    assert all(is_secure(cookie) for cookie in auth_cookies(registered))
