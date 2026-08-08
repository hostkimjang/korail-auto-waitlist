from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from rail_waitlist.models import (
    RailProviderAccount,
    ReservationAttempt,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.provider_account_management import application as account_application
from rail_waitlist.provider_account_management import auth_recovery_runtime
from rail_waitlist.provider_account_management.application import (
    get_enabled_provider_credentials,
    update_provider_auth_status,
)
from rail_waitlist.provider_account_management.login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
)
from rail_waitlist.provider_account_management.schemas import RailProviderAccountUpsert


@dataclass
class StubProviderLoginVerifier:
    outcome: ProviderLoginVerificationOutcome = ProviderLoginVerificationOutcome.AUTHENTICATED
    calls: list[tuple[Provider, str, str, int]] = field(default_factory=list)
    on_verify: Callable[[], Awaitable[None]] | None = None

    async def verify(self, provider, credentials):
        self.calls.append(
            (
                provider,
                credentials.login_method,
                credentials.login_id,
                credentials.credential_version,
            )
        )
        if self.on_verify is not None:
            await self.on_verify()
        return ProviderLoginVerification(self.outcome)


@pytest.fixture(autouse=True)
def verified_provider_login(app):
    verifier = StubProviderLoginVerifier()
    app.state.provider_login_verifier = verifier
    return verifier


class _UpsertSessionStub:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0

    async def scalar(self, _query):
        return None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def refresh(self, _value: object) -> None:
        self.refresh_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


async def test_provider_account_api_encrypts_and_redacts_credentials(
    client,
    db_engine,
    verified_provider_login,
):
    initial = await client.get("/api/v1/provider-accounts")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json() == [
        {
            "provider": "korail",
            "configured": False,
            "enabled": False,
            "login_method": None,
            "masked_login_id": None,
            "credential_version": 0,
            "last_auth_status": "not_checked",
            "last_authenticated_at": None,
            "updated_at": None,
        },
        {
            "provider": "srt",
            "configured": False,
            "enabled": False,
            "login_method": None,
            "masked_login_id": None,
            "credential_version": 0,
            "last_auth_status": "not_checked",
            "last_authenticated_at": None,
            "updated_at": None,
        },
    ]

    created = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "  korail-user  ",
            "password": "secret-password-with-space ",
            "enabled": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["provider"] == "korail"
    assert body["configured"] is True
    assert body["enabled"] is True
    assert body["login_method"] == "membership_number"
    assert body["masked_login_id"] == "k*********r"
    assert body["credential_version"] == 1
    assert verified_provider_login.calls[-1][3] == 1
    assert body["last_auth_status"] == "authenticated"
    assert body["last_authenticated_at"] is not None
    assert "password" not in body
    assert "login_id" not in body
    assert "secret-password" not in created.text

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        account = await session.scalar(select(RailProviderAccount))
        assert account is not None
        assert "korail-user" not in account.credentials_ciphertext
        assert "secret-password" not in account.credentials_ciphertext
        credentials = await get_enabled_provider_credentials(session, Provider.KORAIL)
        assert credentials is not None
        assert credentials.login_method == "membership_number"
        assert credentials.login_id == "korail-user"
        assert credentials.password == "secret-password-with-space "
        assert credentials.credential_version == 1


async def test_provider_account_upsert_versions_disable_and_delete(
    client,
    db_engine,
    verified_provider_login,
):
    first = await client.put(
        "/api/v1/provider-accounts/srt",
        json={
            "login_method": "membership_number",
            "login_id": "1234567890",
            "password": "first-password",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["credential_version"] == 1
    assert verified_provider_login.calls[-1][3] == 1

    second = await client.put(
        "/api/v1/provider-accounts/srt",
        json={
            "login_method": "email",
            "login_id": "new-srt-user@example.com",
            "password": "second-password",
            "enabled": False,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["credential_version"] == 2
    assert verified_provider_login.calls[-1][3] == 2
    assert second.json()["enabled"] is False
    assert second.json()["last_auth_status"] == "authenticated"
    assert second.json()["last_authenticated_at"] is not None

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await get_enabled_provider_credentials(session, Provider.SRT) is None

    deleted = await client.delete("/api/v1/provider-accounts/srt")
    assert deleted.status_code == 204
    repeated = await client.delete("/api/v1/provider-accounts/srt")
    assert repeated.status_code == 204

    listed = await client.get("/api/v1/provider-accounts")
    srt = next(row for row in listed.json() if row["provider"] == "srt")
    assert srt["configured"] is False
    assert srt["credential_version"] == 0


async def test_verified_login_resumes_only_stale_authentication_watch_without_retrying(
    client,
    app,
) -> None:
    created = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "1234567890",
            "password": "first-password",
        },
    )
    assert created.status_code == 200

    departure_at = datetime.now(UTC) + timedelta(days=2)
    async with app.state.test_session_factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="부산",
            destination_node_id="NAT014445",
            travel_date=departure_at.date(),
            time_from=departure_at.time().replace(tzinfo=None),
            time_to=(departure_at + timedelta(hours=2)).time().replace(tzinfo=None),
            train_numbers=["00035"],
            status=WatchStatus.AUTH_REQUIRED,
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            reservation_attempted=True,
            dedupe_key="auth-required-before-verified-login",
        )
        candidate = WatchCandidate(
            train_number="00035",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1, minutes=30),
            seat_class="standard",
            priority=1,
            state="failed",
        )
        attempt_started_at = datetime.now(UTC) - timedelta(seconds=1)
        candidate.reservation_attempt = ReservationAttempt(
            idempotency_key="reserve:auth-required-before-verified-login",
            outcome=ReservationOutcome.AUTH_REQUIRED,
            started_at=attempt_started_at,
            finished_at=attempt_started_at + timedelta(milliseconds=100),
        )
        watch.candidates.append(candidate)
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.RESERVING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="reservation_auth_required",
                created_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id
        attempt_id = candidate.reservation_attempt.id

    reverified = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "1234567890",
            "password": "second-password",
        },
    )
    assert reverified.status_code == 200

    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt).where(
                        ReservationAttempt.candidate_id == candidate.id
                    )
                )
            ).all()
        )
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch_id)
            .order_by(WatchTransitionHistory.created_at.desc())
        )

        assert watch.status is WatchStatus.SCHEDULED
        assert watch.reservation_attempted is True
        assert candidate.state == "observed"
        assert len(attempts) == 1
        assert attempts[0].id == attempt_id
        assert attempts[0].outcome is ReservationOutcome.AUTH_REQUIRED
        assert latest_transition.reason == "provider_login_reverified"


async def test_provider_account_api_rejects_unsupported_provider(client):
    response = await client.put(
        "/api/v1/provider-accounts/mock",
        json={
            "login_method": "membership_number",
            "login_id": "mock-user",
            "password": "not-stored",
        },
    )
    assert response.status_code == 422


async def test_provider_auth_status_preserves_last_success_timestamp(client, db_engine):
    created = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "korail-user",
            "password": "secret-password",
        },
    )
    assert created.status_code == 200, created.text

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        authenticated = await update_provider_auth_status(
            session,
            Provider.KORAIL,
            "authenticated",
        )
        assert authenticated is not None
        assert authenticated.last_auth_status == "authenticated"
        assert authenticated.last_authenticated_at is not None
        successful_at = authenticated.last_authenticated_at

        failed = await update_provider_auth_status(
            session,
            Provider.KORAIL,
            "auth_required",
        )
        assert failed is not None
        assert failed.last_auth_status == "auth_required"
        assert failed.last_authenticated_at == successful_at
        assert "password" not in failed.model_dump()

        missing = await update_provider_auth_status(
            session,
            Provider.SRT,
            "authenticated",
        )
        assert missing is None


async def test_stale_reservation_auth_result_does_not_demote_newer_credentials(
    client,
    db_engine,
):
    created = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "korail-user",
            "password": "secret-password",
        },
    )
    assert created.status_code == 200, created.text
    stale_version = created.json()["credential_version"]

    replaced = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "korail-user",
            "password": "new-secret-password",
        },
    )
    assert replaced.status_code == 200, replaced.text
    current_version = replaced.json()["credential_version"]
    assert current_version == stale_version + 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        unchanged = await update_provider_auth_status(
            session,
            Provider.KORAIL,
            "auth_required",
            expected_credential_version=stale_version,
        )
        assert unchanged is not None
        assert unchanged.credential_version == current_version
        assert unchanged.last_auth_status == "authenticated"

        current = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert current is not None
        assert current.credential_version == current_version
        assert current.last_auth_status == "authenticated"


async def test_failed_login_is_not_saved_and_does_not_replace_existing_credentials(
    client,
    db_engine,
    verified_provider_login,
):
    first = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "existing-user",
            "password": "existing-password",
        },
    )
    assert first.status_code == 200

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        before = await session.scalar(select(RailProviderAccount))
        assert before is not None
        original_ciphertext = before.credentials_ciphertext
        original_version = before.credential_version

    verified_provider_login.outcome = ProviderLoginVerificationOutcome.AUTH_REQUIRED
    rejected = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "email",
            "login_id": "wrong@example.com",
            "password": "wrong-password",
        },
    )

    assert rejected.status_code == 422
    assert "wrong-password" not in rejected.text
    async with factory() as session:
        after = await session.scalar(select(RailProviderAccount))
        assert after is not None
        assert after.credentials_ciphertext == original_ciphertext
        assert after.credential_version == original_version


async def test_provider_account_update_conflicts_when_generation_changes_during_verification(
    client,
    db_engine,
    verified_provider_login,
) -> None:
    created = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "existing-user",
            "password": "existing-password",
        },
    )
    assert created.status_code == 200

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def change_generation() -> None:
        async with factory() as concurrent_session:
            account = await concurrent_session.scalar(select(RailProviderAccount))
            assert account is not None
            account.credential_version += 1
            await concurrent_session.commit()

    verified_provider_login.on_verify = change_generation
    rejected = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "email",
            "login_id": "replacement@example.com",
            "password": "replacement-password",
        },
    )

    assert rejected.status_code == 409
    assert rejected.json() == {
        "detail": "철도 계정이 로그인 확인 중 변경되었습니다. 다시 시도해 주세요."
    }
    assert "replacement-password" not in rejected.text
    async with factory() as session:
        credentials = await get_enabled_provider_credentials(session, Provider.KORAIL)
        assert credentials is not None
        assert credentials.login_id == "existing-user"
        assert credentials.credential_version == 2


async def test_provider_account_validation_never_reflects_the_password(client):
    oversized_password = "sensitive-fixture-" + ("x" * 256)

    response = await client.put(
        "/api/v1/provider-accounts/korail",
        json={
            "login_method": "membership_number",
            "login_id": "fixture-member",
            "password": oversized_password,
        },
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "request_validation_failed"}
    assert oversized_password not in response.text


def test_provider_credential_decryption_is_redacted_and_infers_legacy_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = SimpleNamespace(credentials_ciphertext="opaque-fixture", credential_version=7)

    def fail_decryption(_ciphertext: str) -> dict[str, str]:
        raise RuntimeError("sensitive fixture detail")

    monkeypatch.setattr(account_application.secret_box, "decrypt_dict", fail_decryption)
    with pytest.raises(RuntimeError) as decryption_error:
        account_application._decrypt_credentials(account)
    assert str(decryption_error.value) == "stored rail provider credentials cannot be decrypted"
    assert "sensitive fixture detail" not in str(decryption_error.value)

    monkeypatch.setattr(
        account_application.secret_box,
        "decrypt_dict",
        lambda _ciphertext: {"login_id": "fixture-member", "password": ""},
    )
    with pytest.raises(RuntimeError) as invalid_error:
        account_application._decrypt_credentials(account)
    assert str(invalid_error.value) == "stored rail provider credentials are invalid"

    cases = [
        ("legacy@example.com", "email"),
        ("010-1234-5678", "phone"),
        ("1234567890", "membership_number"),
    ]
    for login_id, expected_method in cases:
        monkeypatch.setattr(
            account_application.secret_box,
            "decrypt_dict",
            lambda _ciphertext, login_id=login_id: {
                "login_id": login_id,
                "password": "fixture-password",
            },
        )
        credentials = account_application._decrypt_credentials(account)
        assert credentials.login_method == expected_method
        assert credentials.credential_version == 7
        assert login_id not in repr(credentials)
        assert "fixture-password" not in repr(credentials)


async def test_first_insert_integrity_error_rolls_back_as_generation_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _UpsertSessionStub()
    expected = IntegrityError("insert", {}, RuntimeError("duplicate provider"))

    async def fail_resume(*_args, **_kwargs) -> None:
        raise expected

    monkeypatch.setattr(
        auth_recovery_runtime,
        "resume_watches_after_verified_provider_login",
        fail_resume,
    )
    monkeypatch.setattr(
        account_application.secret_box,
        "encrypt_dict",
        lambda _payload: "encrypted-fixture",
    )
    data = RailProviderAccountUpsert(
        login_method="membership_number",
        login_id="fixture-member",
        password="fixture-password",
    )

    with pytest.raises(account_application.ProviderAccountGenerationConflict) as raised:
        await account_application.upsert_provider_account(
            session,
            Provider.KORAIL,
            data,
            verified_credential_version=1,
        )

    assert raised.value.__cause__ is expected
    assert str(raised.value) == "rail provider account changed during login verification"
    assert len(session.added) == 1
    assert session.rollback_calls == 1
    assert session.commit_calls == 0
    assert session.refresh_calls == 0


async def test_first_insert_resume_cancellation_propagates_without_commit_or_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _UpsertSessionStub()
    expected = asyncio.CancelledError()

    async def cancel_resume(*_args, **_kwargs) -> None:
        raise expected

    monkeypatch.setattr(
        auth_recovery_runtime,
        "resume_watches_after_verified_provider_login",
        cancel_resume,
    )
    monkeypatch.setattr(
        account_application.secret_box,
        "encrypt_dict",
        lambda _payload: "encrypted-fixture",
    )
    data = RailProviderAccountUpsert(
        login_method="membership_number",
        login_id="fixture-member",
        password="fixture-password",
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await account_application.upsert_provider_account(
            session,
            Provider.SRT,
            data,
            verified_credential_version=1,
        )

    assert raised.value is expected
    assert len(session.added) == 1
    assert session.commit_calls == 0
    assert session.refresh_calls == 0
