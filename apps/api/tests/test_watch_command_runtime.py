from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.watch_management.command_runtime as runtime_module
import rail_waitlist.watch_management.http as http_module
from rail_waitlist.domain import Provider
from rail_waitlist.watch_management.create_application import (
    WatchCreateDependencies,
    WatchCreateForbidden,
    WatchCreateValidationError,
    WatchRegistrationEvidenceExpired,
)
from rail_waitlist.watch_management.models import Watch
from rail_waitlist.watch_management.schemas import WatchCreate, WatchUpdate
from rail_waitlist.watch_management.update_application import (
    WatchCommandConflict,
    WatchCommandNotFound,
    WatchCommandValidationError,
    WatchUpdateDependencies,
)

NOW = datetime(2030, 8, 1, 2, 3, tzinfo=UTC)


def test_create_dependency_factory_reads_replaceable_runtime_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def request_hash(_value: object) -> str:
        return "hash"

    async def get_idempotent(*_args: object, **_kwargs: object) -> str | None:
        return None

    async def ensure_capacity(*_args: object, **_kwargs: object) -> None:
        return None

    def experimental_enabled() -> bool:
        return True

    async def validate_channels(*_args: object, **_kwargs: object) -> None:
        return None

    def dedupe(*_args: object) -> str:
        return "dedupe"

    async def remember(*_args: object, **_kwargs: object) -> None:
        return None

    async def outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    class TimetableProvider:
        @staticmethod
        def official_booking_url() -> str:
            return "https://current-provider.test/booking"

    class FrozenDateTime:
        @staticmethod
        def now(timezone: object) -> datetime:
            assert timezone is UTC
            return NOW

    monkeypatch.setattr(runtime_module, "request_hash", request_hash)
    monkeypatch.setattr(runtime_module, "get_idempotent_resource", get_idempotent)
    monkeypatch.setattr(runtime_module, "ensure_focused_observation_capacity", ensure_capacity)
    monkeypatch.setattr(runtime_module, "_experimental_rail_enabled", experimental_enabled)
    monkeypatch.setattr(runtime_module, "validate_channel_ids", validate_channels)
    monkeypatch.setattr(runtime_module, "build_watch_dedupe_key", dedupe)
    monkeypatch.setattr(runtime_module, "remember_idempotency", remember)
    monkeypatch.setattr(runtime_module, "add_outbox_event", outbox)
    monkeypatch.setattr(
        runtime_module,
        "get_timetable_provider",
        lambda provider: TimetableProvider() if provider is Provider.KORAIL else None,
    )
    monkeypatch.setattr(runtime_module, "datetime", FrozenDateTime)

    dependencies = runtime_module.watch_create_dependencies()

    assert isinstance(dependencies, WatchCreateDependencies)
    assert dependencies.request_hash is request_hash
    assert dependencies.get_idempotent_resource is get_idempotent
    assert dependencies.ensure_focused_observation_capacity is ensure_capacity
    assert dependencies.experimental_rail_enabled is experimental_enabled
    assert dependencies.validate_channel_ids is validate_channels
    assert dependencies.build_watch_dedupe_key is dedupe
    assert dependencies.remember_idempotency is remember
    assert dependencies.add_outbox_event is outbox
    assert dependencies.official_booking_url_for_provider(Provider.KORAIL) == (
        "https://current-provider.test/booking"
    )
    assert dependencies.now() is NOW


def test_update_dependency_factory_reads_replaceable_runtime_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def dedupe(*_args: object) -> str:
        return "dedupe"

    async def outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    async def validate_channels(*_args: object, **_kwargs: object) -> None:
        return None

    async def ensure_capacity(*_args: object, **_kwargs: object) -> None:
        return None

    class FrozenDateTime:
        @staticmethod
        def now(timezone: object) -> datetime:
            assert timezone is UTC
            return NOW

    monkeypatch.setattr(runtime_module, "build_watch_dedupe_key", dedupe)
    monkeypatch.setattr(runtime_module, "add_outbox_event", outbox)
    monkeypatch.setattr(runtime_module, "validate_channel_ids", validate_channels)
    monkeypatch.setattr(runtime_module, "ensure_focused_observation_capacity", ensure_capacity)
    monkeypatch.setattr(runtime_module, "datetime", FrozenDateTime)

    dependencies = runtime_module.watch_update_dependencies()

    assert isinstance(dependencies, WatchUpdateDependencies)
    assert dependencies.build_watch_dedupe_key is dedupe
    assert dependencies.add_outbox_event is outbox
    assert dependencies.validate_channel_ids is validate_channels
    assert dependencies.ensure_focused_observation_capacity is ensure_capacity
    assert dependencies.now() is NOW


async def test_create_runtime_preserves_arguments_and_uses_current_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    data = cast(WatchCreate, object())
    expected = cast(Watch, object())
    dependencies = cast(WatchCreateDependencies, object())
    captured: dict[str, object] = {}

    async def application(*args: object, **kwargs: object) -> Watch:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(runtime_module, "watch_create_dependencies", lambda: dependencies)
    monkeypatch.setattr(runtime_module, "create_watch_application", application)

    result = await runtime_module.create_watch(session, data, "create-key")

    assert result is expected
    assert captured == {
        "args": (session, data, "create-key"),
        "kwargs": {"dependencies": dependencies},
    }


async def test_update_runtime_preserves_arguments_and_uses_current_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    watch = cast(Watch, object())
    data = cast(WatchUpdate, object())
    expected = cast(Watch, object())
    dependencies = cast(WatchUpdateDependencies, object())
    captured: dict[str, object] = {}

    async def application(*args: object, **kwargs: object) -> Watch:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(runtime_module, "watch_update_dependencies", lambda: dependencies)
    monkeypatch.setattr(runtime_module, "update_watch_application", application)

    result = await runtime_module.update_watch(session, watch, data)

    assert result is expected
    assert captured == {
        "args": (session, watch, data),
        "kwargs": {"dependencies": dependencies},
    }


@pytest.mark.parametrize(
    ("command", "error"),
    [
        ("create", WatchCreateForbidden("disabled")),
        ("update", WatchCommandConflict("conflict")),
    ],
)
async def test_command_runtime_propagates_original_application_errors(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    error: RuntimeError,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise error

    if command == "create":
        monkeypatch.setattr(runtime_module, "create_watch_application", fail)
        call = runtime_module.create_watch(
            cast(AsyncSession, object()),
            cast(WatchCreate, object()),
        )
    else:
        monkeypatch.setattr(runtime_module, "update_watch_application", fail)
        call = runtime_module.update_watch(
            cast(AsyncSession, object()),
            cast(Watch, object()),
            cast(WatchUpdate, object()),
        )

    with pytest.raises(type(error)) as raised:
        await call

    assert raised.value is error
    assert not isinstance(raised.value, HTTPException)


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (WatchCreateForbidden("disabled"), 403, "disabled"),
        (WatchCreateValidationError("invalid"), 422, "invalid"),
        (WatchCommandValidationError("channel invalid"), 422, "channel invalid"),
        (
            WatchRegistrationEvidenceExpired(
                "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요."
            ),
            409,
            {
                "code": "registration_evidence_conflict",
                "reason": "expired",
                "message": "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
            },
        ),
        (WatchCommandConflict("focused conflict"), 409, "focused conflict"),
    ],
)
async def test_create_http_helper_preserves_exact_error_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
    detail: object,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise error

    monkeypatch.setattr(http_module, "create_watch", fail)

    with pytest.raises(HTTPException) as raised:
        await http_module._create_watch_or_http_error(
            cast(AsyncSession, object()),
            cast(WatchCreate, object()),
            "create-key",
        )

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (WatchCommandNotFound("watch not found"), 404),
        (WatchCommandConflict("update conflict"), 409),
        (WatchCommandValidationError("invalid update"), 422),
    ],
)
async def test_update_http_helper_preserves_exact_error_contract(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
) -> None:
    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise error

    monkeypatch.setattr(http_module, "update_watch", fail)

    with pytest.raises(HTTPException) as raised:
        await http_module._update_watch_or_http_error(
            cast(AsyncSession, object()),
            cast(Watch, object()),
            cast(WatchUpdate, object()),
        )

    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


@pytest.mark.parametrize("command", ["create", "update"])
async def test_http_helpers_do_not_translate_unrelated_failures(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    error = RuntimeError("database unavailable")

    async def fail(*_args: object, **_kwargs: object) -> Watch:
        raise error

    if command == "create":
        monkeypatch.setattr(http_module, "create_watch", fail)
        call = http_module._create_watch_or_http_error(
            cast(AsyncSession, object()),
            cast(WatchCreate, object()),
            None,
        )
    else:
        monkeypatch.setattr(http_module, "update_watch", fail)
        call = http_module._update_watch_or_http_error(
            cast(AsyncSession, object()),
            cast(Watch, object()),
            cast(WatchUpdate, object()),
        )

    with pytest.raises(RuntimeError, match="database unavailable") as raised:
        await call

    assert raised.value is error
