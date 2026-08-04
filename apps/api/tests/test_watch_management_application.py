from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rail_waitlist.domain import Provider, ReservationPolicy, WatchStatus
from rail_waitlist.watch_management import application


class ReservationCapabilityAdapter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def capabilities(self) -> SimpleNamespace:
        return SimpleNamespace(reservation_once=self.enabled)


def watch_state(**overrides) -> SimpleNamespace:
    values = {
        "provider": Provider.KORAIL,
        "reservation_policy": ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
        "status": WatchStatus.SEAT_FOUND,
        "next_check_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install_dispatch_dependencies(
    monkeypatch,
    *,
    authenticated: bool,
    reservation_once: bool,
) -> list[str]:
    calls: list[str] = []

    async def has_account(_session, _provider) -> bool:
        calls.append("account")
        return authenticated

    def execution_provider(_provider) -> ReservationCapabilityAdapter:
        calls.append("capability")
        return ReservationCapabilityAdapter(reservation_once)

    monkeypatch.setattr(application, "has_authenticated_provider_account", has_account)
    monkeypatch.setattr(application, "get_execution_provider", execution_provider)
    return calls


@pytest.mark.parametrize(
    (
        "requested_policy",
        "watch_overrides",
        "authenticated",
        "reservation_once",
        "expected",
        "expected_calls",
    ),
    [
        (None, {}, True, True, False, []),
        (ReservationPolicy.NOTIFY_ONLY, {}, True, True, False, []),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {"reservation_policy": ReservationPolicy.NOTIFY_ONLY},
            True,
            True,
            False,
            [],
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {"status": WatchStatus.WATCHING},
            True,
            True,
            False,
            [],
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {"provider": Provider.MOCK},
            True,
            True,
            False,
            [],
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {},
            False,
            True,
            False,
            ["account"],
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {},
            True,
            False,
            False,
            ["account", "capability"],
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            {},
            True,
            True,
            True,
            ["account", "capability"],
        ),
    ],
)
async def test_policy_update_immediate_processing_decision(
    monkeypatch,
    requested_policy,
    watch_overrides,
    authenticated,
    reservation_once,
    expected,
    expected_calls,
) -> None:
    calls = install_dispatch_dependencies(
        monkeypatch,
        authenticated=authenticated,
        reservation_once=reservation_once,
    )

    result = await application.should_enqueue_after_policy_update(
        object(),
        requested_policy,
        watch_state(**watch_overrides),
    )

    assert result is expected
    assert calls == expected_calls


@pytest.mark.parametrize(
    (
        "previous_status",
        "watch_overrides",
        "authenticated",
        "reservation_once",
        "expected",
        "expected_calls",
    ),
    [
        (WatchStatus.SCHEDULED, {"status": WatchStatus.SCHEDULED}, True, True, False, []),
        (WatchStatus.DRAFT, {"status": WatchStatus.WATCHING}, True, True, False, []),
        (
            WatchStatus.DRAFT,
            {"status": WatchStatus.SCHEDULED, "next_check_at": None},
            True,
            True,
            False,
            [],
        ),
        (
            WatchStatus.DRAFT,
            {"status": WatchStatus.SCHEDULED, "provider": Provider.MOCK},
            True,
            True,
            False,
            [],
        ),
        (
            WatchStatus.DRAFT,
            {
                "status": WatchStatus.SCHEDULED,
                "reservation_policy": ReservationPolicy.NOTIFY_ONLY,
            },
            True,
            True,
            False,
            [],
        ),
        (
            WatchStatus.DRAFT,
            {"status": WatchStatus.SCHEDULED},
            False,
            True,
            False,
            ["account"],
        ),
        (
            WatchStatus.DRAFT,
            {"status": WatchStatus.SCHEDULED},
            True,
            False,
            False,
            ["account", "capability"],
        ),
        (
            WatchStatus.DRAFT,
            {"status": WatchStatus.SCHEDULED},
            True,
            True,
            True,
            ["account", "capability"],
        ),
    ],
)
async def test_start_immediate_processing_decision(
    monkeypatch,
    previous_status,
    watch_overrides,
    authenticated,
    reservation_once,
    expected,
    expected_calls,
) -> None:
    calls = install_dispatch_dependencies(
        monkeypatch,
        authenticated=authenticated,
        reservation_once=reservation_once,
    )

    result = await application.should_enqueue_after_start(
        object(),
        previous_status,
        watch_state(**watch_overrides),
    )

    assert result is expected
    assert calls == expected_calls
