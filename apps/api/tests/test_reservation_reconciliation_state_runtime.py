from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist.reservations import reconciliation_state_runtime as runtime_module
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)
from rail_waitlist.watch_management.models import (
    ReservationAttempt,
    Watch,
    WatchCandidate,
)

NOW = datetime(2026, 8, 8, tzinfo=UTC)


async def test_runtime_resolves_current_dependencies_and_preserves_application_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = RuntimeError("state application failed")

    async def transition(*_args: object, **_kwargs: object) -> Watch:
        return cast(Watch, object())

    async def outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    def confirmation(*_args: object, **_kwargs: object) -> None:
        return None

    def utc_instant(value: datetime) -> datetime:
        return value

    async def application(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs
        raise expected

    monkeypatch.setattr(runtime_module, "apply_watch_transition", transition)
    monkeypatch.setattr(runtime_module, "add_outbox_event", outbox)
    monkeypatch.setattr(runtime_module, "record_reservation_confirmation", confirmation)
    monkeypatch.setattr(runtime_module, "_utc_instant", utc_instant)
    monkeypatch.setattr(
        runtime_module,
        "apply_reservation_reconciliation_application",
        application,
    )

    with pytest.raises(RuntimeError) as caught:
        await runtime_module.apply_reservation_reconciliation(
            cast(AsyncSession, object()),
            cast(Watch, object()),
            cast(WatchCandidate, object()),
            cast(ReservationAttempt, object()),
            cast(ReservationConfirmationResult, object()),
            reconciled_at=NOW,
        )

    assert caught.value is expected
    kwargs = cast(dict[str, object], captured["kwargs"])
    dependencies = kwargs["dependencies"]
    assert isinstance(dependencies, ReservationReconciliationStateDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.record_reservation_confirmation is confirmation
    assert dependencies.utc_instant is utc_instant
