from __future__ import annotations

import ast
import asyncio
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
import rail_waitlist.korail_pydoll_reservation_actor as reservation_actor_module
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    KorailReservationOutcome,
    KorailReservationRequest,
    KorailReservationResult,
    KorailReservationSeatClass,
    KorailSessionActorState,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
)
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationProgress,
    KorailReservationProgressCallback,
)


class _ReservationSession:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.closed = 0

    async def open(self) -> PydollPageSnapshot:
        self.events.append("open")
        return PydollPageSnapshot("search", ())

    async def ensure_authenticated(self, _credential: KorailCredentialInput) -> bool:
        self.events.append("authenticate")
        return True

    async def choose_station(self, kind: str, station: str) -> None:
        self.events.append(f"station:{kind}:{station}")

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.events.append(f"schedule:{travel_date.isoformat()}:{departure_hour}")

    async def submit_once(self) -> None:
        self.events.append("submit")

    async def wait_for_result(self) -> PydollPageSnapshot:
        self.events.append("wait")
        return PydollPageSnapshot("result", ())

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        _max_actions: int,
    ) -> PydollPageSnapshot:
        self.events.append("expand")
        return snapshot

    async def reserve_once(
        self,
        _request: KorailReservationRequest,
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        self.events.append("reserve")
        return KorailReservationResult(
            outcome=KorailReservationOutcome.PAYMENT_REQUIRED,
            reason="payment_required",
            seat_clicked=True,
            reservation_clicked=True,
        )


class _ProgressThenSourceUnavailableSession(_ReservationSession):
    def __init__(self, *, error_stage: str = "session_keepalive") -> None:
        super().__init__()
        self.error_stage = error_stage
        self.received_progress_callback = False
        self.progress = (
            KorailReservationProgress(
                "target_rechecked",
                datetime(2026, 8, 13, 6, 54, 40, 500_000, tzinfo=UTC),
            ),
            KorailReservationProgress(
                "seat_selected",
                datetime(2026, 8, 13, 6, 54, 40, 600_000, tzinfo=UTC),
            ),
            KorailReservationProgress(
                "reservation_requested",
                datetime(2026, 8, 13, 6, 54, 40, 700_000, tzinfo=UTC),
            ),
        )

    async def reserve_once(
        self,
        _request: KorailReservationRequest,
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        self.events.append("reserve")
        self.received_progress_callback = on_progress is not None
        assert on_progress is not None
        for progress in self.progress:
            on_progress(progress)
        raise BrowserSourceUnavailable(self.error_stage)


class _ReservationContext:
    def __init__(self, session: _ReservationSession) -> None:
        self.session = session

    async def __aenter__(self) -> _ReservationSession:
        return self.session

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.session.closed += 1


class _BlockingReservationSession(_ReservationSession):
    def __init__(self) -> None:
        super().__init__()
        self.wait_started = asyncio.Event()

    async def wait_for_result(self) -> PydollPageSnapshot:
        self.events.append("wait")
        self.wait_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled reservation wait unexpectedly resumed")


def _request() -> KorailReservationRequest:
    return KorailReservationRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 8),
        train_number="85",
        train_type="KTX",
        departure_time=time(14, 11),
        arrival_time=time(16, 52),
        seat_class=KorailReservationSeatClass.GENERAL,
        credential=KorailCredentialInput(
            login_id="fixture-account",
            password="fixture-password",
            version="credential-v1",
        ),
    )


@pytest.mark.asyncio
async def test_reservation_uses_facade_callbacks_captured_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safety_stages: list[str] = []
    identity_stages: list[str] = []

    def safety_guard(_snapshot: PydollPageSnapshot, stage: str) -> None:
        safety_stages.append(stage)

    async def identity_guard(
        _session: object,
        _request: KorailReservationRequest,
        stage: str,
    ) -> None:
        identity_stages.append(stage)

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_response_allowed",
        staticmethod(safety_guard),
    )
    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_reservation_identity",
        staticmethod(identity_guard),
    )
    monkeypatch.setattr(
        browser_module,
        "_snapshot_has_unique_reservation_target",
        lambda _snapshot, _request: True,
    )
    session = _ReservationSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _ReservationContext(session),  # type: ignore[arg-type]
    )

    result = await client.reserve_once(_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.session_ready_at is not None
    assert safety_stages == ["load_page", "wait_result"]
    assert identity_stages == ["pre_submit_identity_check"]
    assert session.events == [
        "open",
        "authenticate",
        "station:departure:서울",
        "station:arrival:부산",
        "schedule:2026-08-08:14",
        "submit",
        "wait",
        "reserve",
    ]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_reservation_cancellation_marks_session_stale_and_closes_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def identity_guard(
        _session: object,
        _request: KorailReservationRequest,
        _stage: str,
    ) -> None:
        return None

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_reservation_identity",
        staticmethod(identity_guard),
    )
    session = _BlockingReservationSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _ReservationContext(session),  # type: ignore[arg-type]
    )

    task = asyncio.create_task(client.reserve_once(_request()))
    await asyncio.wait_for(session.wait_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.session_snapshot().state is KorailSessionActorState.STALE
    assert session.closed == 1


@pytest.mark.parametrize(
    "with_external_callback",
    [False, True],
    ids=["without_external_callback", "with_external_callback"],
)
@pytest.mark.asyncio
async def test_reservation_preserves_inner_progress_when_source_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_external_callback: bool,
) -> None:
    async def identity_guard(
        _session: object,
        _request: KorailReservationRequest,
        _stage: str,
    ) -> None:
        return None

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_reservation_identity",
        staticmethod(identity_guard),
    )
    session = _ProgressThenSourceUnavailableSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _ReservationContext(session),  # type: ignore[arg-type]
    )
    observed_progress: list[KorailReservationProgress] = []

    if with_external_callback:
        result = await client.reserve_once(_request(), on_progress=observed_progress.append)
    else:
        result = await client.reserve_once(_request())

    assert result.outcome is KorailReservationOutcome.FAILED
    assert result.reason == "source_unavailable:session_keepalive"
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert result.session_ready_at is not None
    assert result.target_rechecked_at == session.progress[0].occurred_at
    assert result.seat_selected_at == session.progress[1].occurred_at
    assert result.reservation_requested_at == session.progress[2].occurred_at
    assert session.received_progress_callback is True
    assert [progress.stage for progress in observed_progress] == (
        [
            "authenticated_session_ready",
            "target_rechecked",
            "seat_selected",
            "reservation_requested",
        ]
        if with_external_callback
        else []
    )
    assert session.closed == 1


@pytest.mark.asyncio
async def test_reservation_does_not_expose_an_unsafe_source_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def identity_guard(
        _session: object,
        _request: KorailReservationRequest,
        _stage: str,
    ) -> None:
        return None

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_reservation_identity",
        staticmethod(identity_guard),
    )
    session = _ProgressThenSourceUnavailableSession(
        error_stage="credential=fixture-password",
    )
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _ReservationContext(session),  # type: ignore[arg-type]
    )

    result = await client.reserve_once(_request())

    assert result.reason == "source_unavailable:reserve_once"
    assert "fixture-password" not in result.reason


def test_browser_keeps_reservation_contract_compatibility_exports() -> None:
    assert browser_module.KorailReservationSeatClass is (
        reservation_actor_module.KorailReservationSeatClass
    )
    assert (
        browser_module.KorailReservationOutcome is reservation_actor_module.KorailReservationOutcome
    )
    assert (
        browser_module.KorailReservationRequest is reservation_actor_module.KorailReservationRequest
    )
    assert (
        browser_module.KorailReservationResult is reservation_actor_module.KorailReservationResult
    )


def test_reservation_actor_has_no_facade_or_peer_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "reservation_actor.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules.isdisjoint(
        {
            "korail_pydoll_browser",
            "korail_pydoll_confirmation_reader",
            "korail_pydoll_http_replay",
            "korail_pydoll_page_safety",
            "korail_pydoll_search_actor",
        }
    )
