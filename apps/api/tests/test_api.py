from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import api as api_module
from rail_waitlist import services as services_module
from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    AdminAccount,
    IdempotencyRecord,
    NotificationChannel,
    OutboxEvent,
    ProviderExecutionLease,
    RailProviderAccount,
    ReservationAttempt,
    SeatObservation,
    TimetableSeatEvidence,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.policy import build_watch_dedupe_key
from rail_waitlist.providers import MockProviderAdapter
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.schemas import (
    ProviderCapabilities,
    SeatAvailability,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
    WatchUpdate,
)
from rail_waitlist.services import transition_watch, update_watch
from rail_waitlist.srt_provider_adapter_contract import SrtTimetableTrain
from rail_waitlist.timetable_snapshot_cache import TimetableSnapshotCache


def watch_payload(**overrides):
    travel_date = overrides.get(
        "travel_date", (date.today() + timedelta(days=7)).isoformat()
    )
    payload = {
        "provider": "mock",
        "origin": "서울",
        "origin_node_id": "N-SEOUL",
        "destination": "부산",
        "destination_node_id": "N-BUSAN",
        "travel_date": travel_date,
        "time_from": "08:00:00",
        "time_to": "12:00:00",
        "passenger_count": 1,
        "train_numbers": ["KTX-001"],
        "mode": "official",
    }
    payload.update(overrides)
    if "candidates" not in overrides:
        payload["candidates"] = [
            {
                "train_number": "KTX-001",
                "departure_at": f"{travel_date}T08:30:00+09:00",
                "arrival_at": f"{travel_date}T11:00:00+09:00",
                "seat_class": "standard",
                "priority": 1,
            }
        ]
    return payload


class ImmediateReservationCapabilityAdapter:
    provider = Provider.KORAIL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=Provider.KORAIL,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=True,
            experimental=True,
            enabled=True,
            note="test reservation capability",
        )


async def test_verified_auto_reservation_start_enqueues_one_immediate_watch_task(
    app,
    client,
    monkeypatch,
) -> None:
    adapter = ImmediateReservationCapabilityAdapter()
    monkeypatch.setattr(services_module, "get_execution_provider", lambda _provider: adapter)
    monkeypatch.setattr(api_module, "get_execution_provider", lambda _provider: adapter)
    enqueued: list[str] = []
    monkeypatch.setattr(
        api_module,
        "_enqueue_immediate_watch_processing",
        lambda watch_id: enqueued.append(watch_id) or True,
    )
    departure_at = datetime.combine(
        date.today() + timedelta(days=2),
        time(8, 30),
        tzinfo=timezone(timedelta(hours=9)),
    ).astimezone(timezone.utc)
    async with app.state.test_session_factory() as session:
        account = RailProviderAccount(
            provider=Provider.KORAIL,
            credentials_ciphertext="opaque-test-ciphertext",
            enabled=True,
            credential_version=1,
            last_auth_status="authenticated",
            last_authenticated_at=datetime.now(timezone.utc),
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(8, 0),
            time_to=time(12, 0),
            passenger_count=1,
            status=WatchStatus.DRAFT,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            dedupe_key="verified-auto-reservation-immediate-start",
        )
        watch.candidates.append(
            WatchCandidate(
                train_number="00043",
                departure_at=departure_at,
                arrival_at=departure_at + timedelta(hours=1),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        session.add_all([account, watch])
        await session.commit()
        watch_id = watch.id

    headers = {"Idempotency-Key": f"watch-start:{watch_id}"}
    first = await client.post(f"/api/v1/watches/{watch_id}/start", headers=headers)
    second = await client.post(f"/api/v1/watches/{watch_id}/start", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "scheduled"
    assert second.status_code == 200
    assert enqueued == [watch_id]


async def test_health_and_safe_provider_contract(client):
    health = await client.get("/health")
    assert health.json() == {"status": "ok", "experimental_rail_enabled": False}
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).json() == {"status": "ready"}

    response = await client.get("/api/v1/providers")
    assert response.status_code == 200
    korail = next(
        item
        for item in response.json()
        if item["provider"] == "korail" and not item["experimental"]
    )
    srt = next(
        item
        for item in response.json()
        if item["provider"] == "srt" and not item["experimental"]
    )
    assert korail["timetable"] is True
    assert korail["seat_monitoring"] is False
    assert korail["reservation_once"] is False
    assert srt["timetable"] is True
    assert srt["seat_monitoring"] is False
    assert srt["reservation_once"] is False


async def test_watch_reservation_policy_defaults_and_can_be_updated(client):
    created = await client.post("/api/v1/watches", json=watch_payload())
    assert created.status_code == 201, created.text
    assert created.json()["reservation_policy"] == "notify_only"

    updated = await client.patch(
        f"/api/v1/watches/{created.json()['id']}",
        json={"reservation_policy": "reserve_once_before_payment"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["reservation_policy"] == "reserve_once_before_payment"

    listed = await client.get("/api/v1/watches")
    assert listed.status_code == 200
    assert listed.json()[0]["reservation_policy"] == "reserve_once_before_payment"


async def test_active_watch_allows_only_reservation_policy_updates(client):
    created = await client.post("/api/v1/watches", json=watch_payload())
    assert created.status_code == 201, created.text
    watch_id = created.json()["id"]
    started = await client.post(
        f"/api/v1/watches/{watch_id}/start",
        headers={"Idempotency-Key": f"start-{watch_id}"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "scheduled"

    policy_update = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"reservation_policy": "reserve_once_before_payment"},
    )
    assert policy_update.status_code == 200, policy_update.text
    assert policy_update.json()["reservation_policy"] == "reserve_once_before_payment"

    disallowed_update = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"passenger_count": 2},
    )
    assert disallowed_update.status_code == 409
    assert "reservation_policy" in disallowed_update.json()["detail"]

    mixed_update = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"reservation_policy": "notify_only", "passenger_count": 2},
    )
    assert mixed_update.status_code == 409


async def test_enabling_one_time_policy_arms_seat_found_watch_without_clearing_attempt_fence(
    app,
    client,
    monkeypatch,
):
    adapter = ImmediateReservationCapabilityAdapter()
    monkeypatch.setattr(api_module, "get_execution_provider", lambda _provider: adapter)
    enqueued: list[str] = []
    monkeypatch.setattr(
        api_module,
        "_enqueue_immediate_watch_processing",
        lambda watch_id: enqueued.append(watch_id) or True,
    )
    departure_at = datetime.combine(
        date.today() + timedelta(days=2),
        time(8, 30),
        tzinfo=timezone(timedelta(hours=9)),
    ).astimezone(timezone.utc)
    future_check_at = datetime.now(timezone.utc) + timedelta(hours=1)
    async with app.state.test_session_factory() as session:
        account = RailProviderAccount(
            provider=Provider.KORAIL,
            credentials_ciphertext="opaque-test-ciphertext",
            enabled=True,
            credential_version=1,
            last_auth_status="authenticated",
            last_authenticated_at=datetime.now(timezone.utc),
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(8, 0),
            time_to=time(12, 0),
            passenger_count=1,
            status=WatchStatus.SEAT_FOUND,
            mode="official",
            reservation_policy=ReservationPolicy.NOTIFY_ONLY,
            reservation_attempted=True,
            next_check_at=future_check_at,
            dedupe_key="seat-found-policy-switch-with-existing-fence",
        )
        candidate = WatchCandidate(
            train_number="00043",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        attempt_started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        candidate.reservation_attempt = ReservationAttempt(
            idempotency_key="existing-reservation-fence",
            started_at=attempt_started_at,
            outcome=ReservationOutcome.UNKNOWN,
            finished_at=datetime.now(timezone.utc),
        )
        watch.candidates.append(candidate)
        session.add_all([account, watch])
        await session.commit()
        watch_id = watch.id
        attempt_id = candidate.reservation_attempt.id

    enabled_at = datetime.now(timezone.utc)
    updated = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"reservation_policy": "reserve_once_before_payment"},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["reservation_policy"] == "reserve_once_before_payment"
    next_check_at = datetime.fromisoformat(updated.json()["next_check_at"])
    if next_check_at.tzinfo is None:
        next_check_at = next_check_at.replace(tzinfo=timezone.utc)
    assert next_check_at <= datetime.now(timezone.utc)
    assert next_check_at >= enabled_at
    assert updated.json()["reservation_attempted"] is True
    assert enqueued == [watch_id]

    async with app.state.test_session_factory() as session:
        attempts = list((await session.scalars(select(ReservationAttempt))).all())
        assert [attempt.id for attempt in attempts] == [attempt_id]
        assert attempts[0].outcome is ReservationOutcome.UNKNOWN


async def test_watch_explicit_one_time_reservation_policy_survives_create_and_reload(client):
    created = await client.post(
        "/api/v1/watches",
        json=watch_payload(reservation_policy="reserve_once_before_payment"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["reservation_policy"] == "reserve_once_before_payment"

    listed = await client.get("/api/v1/watches")
    assert listed.status_code == 200
    restored = next(item for item in listed.json() if item["id"] == created.json()["id"])
    assert restored["reservation_policy"] == "reserve_once_before_payment"


async def test_watch_rejects_unknown_reservation_policy(client):
    response = await client.post(
        "/api/v1/watches",
        json=watch_payload(reservation_policy="pay_automatically"),
    )
    assert response.status_code == 422


async def test_ui_preferences_are_authenticated_and_persisted(app, client, public_client):
    assert (await public_client.get("/api/v1/preferences/ui")).status_code == 401
    async with app.state.test_session_factory() as session:
        session.add(AdminAccount(username="admin", password_hash="not-a-real-password-hash"))
        await session.commit()

    initial = await client.get("/api/v1/preferences/ui")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json()["timetable_refresh_interval_seconds"] == 5
    assert initial.json()["observation_interval_seconds"] == 5

    updated = await client.patch(
        "/api/v1/preferences/ui",
        json={"timetable_refresh_interval_seconds": 15},
    )
    assert updated.status_code == 200
    assert updated.json()["timetable_refresh_interval_seconds"] == 15
    assert updated.json()["observation_interval_seconds"] == 5

    reloaded = await client.get("/api/v1/preferences/ui")
    assert reloaded.json()["timetable_refresh_interval_seconds"] == 15


async def test_ui_preferences_reject_an_unsafe_refresh_interval(app, client):
    async with app.state.test_session_factory() as session:
        session.add(AdminAccount(username="admin", password_hash="not-a-real-password-hash"))
        await session.commit()

    for interval in (4, 301):
        response = await client.patch(
            "/api/v1/preferences/ui",
            json={"timetable_refresh_interval_seconds": interval},
        )
        assert response.status_code == 422

    for interval in (0, 601):
        response = await client.patch(
            "/api/v1/preferences/ui",
            json={"observation_interval_seconds": interval},
        )
        assert response.status_code == 422


async def test_observation_preferences_reschedule_idle_watches_but_not_leased_provider(
    app, client
):
    now = datetime.now(UTC)
    departure_at = now + timedelta(hours=2)
    leased_next_check = now + timedelta(minutes=10)
    async with app.state.test_session_factory() as session:
        session.add(
            AdminAccount(username="admin", password_hash="not-a-real-password-hash")
        )
        leased_watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            destination="서울",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            status=WatchStatus.WATCHING,
            dedupe_key="leased-balanced-watch",
            next_check_at=leased_next_check,
        )
        leased_watch.candidates.append(
            WatchCandidate(
                train_number="00001",
                departure_at=departure_at,
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="observed",
            )
        )
        focused_watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            destination="수서",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            status=WatchStatus.WATCHING,
            seat_observation_mode=SeatObservationMode.FOCUSED,
            focused_observation_interval_seconds=25,
            dedupe_key="idle-focused-watch",
            next_check_at=now + timedelta(minutes=10),
        )
        focused_watch.candidates.append(
            WatchCandidate(
                train_number="00301",
                departure_at=departure_at,
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="observed",
            )
        )
        balanced_watch = Watch(
            provider=Provider.MOCK,
            origin="대전",
            destination="서울",
            travel_date=(now + timedelta(days=7)).date(),
            time_from=time(8),
            time_to=time(12),
            status=WatchStatus.WATCHING,
            seat_observation_mode=SeatObservationMode.BALANCED,
            dedupe_key="idle-balanced-watch-with-expired-candidate",
            next_check_at=now + timedelta(minutes=10),
        )
        balanced_watch.candidates.extend(
            [
                WatchCandidate(
                    train_number="expired",
                    departure_at=now + timedelta(hours=1),
                    seat_class=SeatClass.STANDARD,
                    priority=1,
                    state="expired",
                ),
                WatchCandidate(
                    train_number="active",
                    departure_at=now + timedelta(days=7),
                    seat_class=SeatClass.STANDARD,
                    priority=2,
                    state="observed",
                ),
            ]
        )
        session.add_all(
            [
                leased_watch,
                focused_watch,
                balanced_watch,
                ProviderExecutionLease(
                    provider=Provider.KORAIL,
                    account_scope="anonymous/public",
                    owner_token="in-flight-test-owner",
                    fencing_token=1,
                    expires_at=now + timedelta(minutes=2),
                ),
            ]
        )
        await session.commit()
        leased_watch_id = leased_watch.id
        focused_watch_id = focused_watch.id
        balanced_watch_id = balanced_watch.id

    response = await client.patch(
        "/api/v1/preferences/ui",
        json={"observation_interval_seconds": 5},
    )
    assert response.status_code == 200, response.text
    assert response.json()["observation_interval_seconds"] == 5

    async with app.state.test_session_factory() as session:
        leased = await session.get(Watch, leased_watch_id)
        focused = await session.get(Watch, focused_watch_id)
        balanced = await session.get(Watch, balanced_watch_id)
        assert leased is not None and focused is not None and balanced is not None
        assert leased.next_check_at == leased_next_check.replace(tzinfo=None)
        focused_next = focused.next_check_at
        assert focused_next is not None
        if focused_next.tzinfo is None:
            focused_next = focused_next.replace(tzinfo=UTC)
        elapsed = (focused_next - datetime.now(UTC)).total_seconds()
        assert 4 <= elapsed <= 6
        balanced_next = balanced.next_check_at
        assert balanced_next is not None
        if balanced_next.tzinfo is None:
            balanced_next = balanced_next.replace(tzinfo=UTC)
        balanced_elapsed = (balanced_next - datetime.now(UTC)).total_seconds()
        assert 4 <= balanced_elapsed <= 6


async def test_legacy_split_preference_payload_is_accepted_but_does_not_change_cadence(
    app, client
):
    async with app.state.test_session_factory() as session:
        session.add(
            AdminAccount(
                username="admin",
                password_hash="not-a-real-password-hash",
                observation_interval_seconds=5,
            )
        )
        await session.commit()

    response = await client.patch(
        "/api/v1/preferences/ui",
        json={
            "balanced_observation_interval_seconds": 120,
            "focused_observation_interval_seconds": 20,
        },
    )
    assert response.status_code == 200
    assert response.json()["observation_interval_seconds"] == 5


async def test_mock_station_catalog_endpoint(client):
    response = await client.get("/api/v1/stations", params={"provider": "mock"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "mock"
    assert payload["source"] == "mock"
    assert payload["catalog_scope"] == "mock"
    assert payload["provider_membership"] == "mock"
    assert {station["name"] for station in payload["stations"]} == {
        "서울",
        "수서",
        "대전",
        "부산",
    }


async def test_official_station_catalog_endpoint_maps_missing_key_to_503(
    client, monkeypatch
):
    from rail_waitlist import api

    class MissingKeyAdapter(MockProviderAdapter):
        async def stations(self):
            from rail_waitlist.providers import ProviderUnavailable

            raise ProviderUnavailable("TAGO service key is not configured")

    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: MissingKeyAdapter())
    response = await client.get("/api/v1/stations", params={"provider": "korail"})
    assert response.status_code == 503
    assert response.json()["detail"] == "TAGO service key is not configured"


async def test_official_station_catalog_without_snapshot_maps_collection_failure_to_503(
    app, client
):
    from rail_waitlist.providers import ProviderUnavailable

    previous = app.state.station_catalog_service

    class FailingStationCatalogService:
        async def get_catalog(self, provider):
            raise ProviderUnavailable("TAGO station catalog is unavailable")

    app.state.station_catalog_service = FailingStationCatalogService()
    try:
        response = await client.get("/api/v1/stations", params={"provider": "korail"})
    finally:
        app.state.station_catalog_service = previous

    assert response.status_code == 503
    assert response.json()["detail"] == "TAGO station catalog is unavailable"


def official_timetable_item(
    *,
    provider: Provider = Provider.KORAIL,
    timetable_source: str = "official_provider",
) -> TimetableItem:
    observed_at = datetime.fromisoformat("2026-08-01T07:59:00+09:00")
    provenance = SeatAvailabilityProvenance(
        kind="official_provider",
        source="official-live-test",
        observed_at=observed_at,
    )
    return TimetableItem(
        provider=provider,
        train_number="101",
        train_type="KTX" if provider is Provider.KORAIL else "SRT",
        origin="서울" if provider is Provider.KORAIL else "수서",
        destination="부산",
        departure_at=datetime.fromisoformat("2026-08-01T08:30:00+09:00"),
        arrival_at=datetime.fromisoformat("2026-08-01T11:10:00+09:00"),
        adult_fare=59_800,
        timetable_source=timetable_source,
        timetable_retrieved_at=observed_at,
        availability=SeatAvailability(
            status="available",
            source="official-live-test",
            observed_at=observed_at,
        ),
        seat_classes=[
            SeatClassAvailability(
                seat_class="standard",
                status="available",
                provenance=provenance,
                fare=59_800,
            ),
            SeatClassAvailability(
                seat_class="first",
                status="sold_out",
                provenance=provenance,
            ),
        ],
        official_booking_url=(
            "https://www.korail.com/ticket/search/general"
            if provider is Provider.KORAIL
            else "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do"
        ),
    )


def tago_timetable_item() -> TimetableItem:
    unknown = SeatAvailabilityProvenance(
        kind="not_observed",
        reason="public_api_not_available",
    )
    return official_timetable_item(timetable_source="TAGO").model_copy(
        update={
            "availability": SeatAvailability(status="unknown"),
            "seat_classes": [
                SeatClassAvailability(
                    seat_class="standard",
                    status="unknown",
                    provenance=unknown,
                ),
                SeatClassAvailability(
                    seat_class="first",
                    status="unknown",
                    provenance=unknown,
                ),
            ],
        }
    )


async def test_korail_live_timetable_without_station_node_ids_does_not_call_tago(
    client, monkeypatch
):
    from rail_waitlist import api

    captured = None

    class CapturingLiveSource:
        async def search_timetable(self, **kwargs):
            nonlocal captured
            captured = kwargs
            return [official_timetable_item()]

    def must_not_call_tago(provider):
        raise AssertionError("successful KORAIL live timetable must not call TAGO")

    app = client._transport.app
    previous = app.state.korail_browser_seat_source
    app.state.korail_browser_seat_source = CapturingLiveSource()
    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_tago)
    try:
        response = await client.get(
            "/api/v1/timetables",
            params={
                "provider": "korail",
                "origin": "서울",
                "destination": "부산",
                "departure_from": "2026-08-01T08:00:00+09:00",
                "departure_to": "2026-08-01T12:00:00+09:00",
            },
        )
    finally:
        app.state.korail_browser_seat_source = previous

    assert response.status_code == 200
    assert response.json()[0]["timetable_source"] == "official_provider"
    assert response.json()[0]["seat_classes"][0]["status"] == "available"
    assert captured == {
        "origin": "서울",
        "destination": "부산",
        "departure_from": datetime.fromisoformat("2026-08-01T08:00:00+09:00"),
        "departure_to": datetime.fromisoformat("2026-08-01T12:00:00+09:00"),
        "passenger_count": 1,
    }


async def test_timetable_snapshot_is_cache_only_after_a_successful_timetable_request(
    client, monkeypatch
):
    from rail_waitlist import api

    params = {
        "provider": "mock",
        "origin": "서울",
        "destination": "부산",
        "departure_from": "2026-08-01T08:00:00+09:00",
        "departure_to": "2026-08-01T12:00:00+09:00",
        "origin_node_id": "MOCK-SEOUL",
        "destination_node_id": "MOCK-BUSAN",
    }
    populated = await client.get("/api/v1/timetables", params=params)
    assert populated.status_code == 200
    assert populated.json()

    def must_not_call_provider(provider):
        raise AssertionError("cache-only endpoint must not load a provider")

    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_provider)
    snapshot = await client.get("/api/v1/timetable-snapshots", params=params)

    assert snapshot.status_code == 200
    assert snapshot.headers["cache-control"] == "no-store"
    assert snapshot.json() == populated.json()


async def test_timetable_snapshot_returns_404_without_a_successful_source_request(
    client, monkeypatch
):
    from rail_waitlist import api

    def must_not_call_provider(provider):
        raise AssertionError("cache-only endpoint must not load a provider")

    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_provider)
    response = await client.get(
        "/api/v1/timetable-snapshots",
        params={
            "provider": "mock",
            "origin": "서울",
            "destination": "부산",
            "departure_from": "2026-08-01T08:00:00+09:00",
            "departure_to": "2026-08-01T12:00:00+09:00",
        },
    )

    assert response.status_code == 404


async def test_timetable_snapshot_revalidates_cached_journey_in_the_background(
    app, client, monkeypatch
):
    from rail_waitlist import api

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    cache = TimetableSnapshotCache(
        refresh_interval=timedelta(seconds=60),
        clock=lambda: now,
    )
    app.state.timetable_snapshot_cache = cache
    calls = 0

    class RefreshingAdapter(MockProviderAdapter):
        async def timetable(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            items = await super().timetable(*args, **kwargs)
            return [item.model_copy(update={"train_number": f"MOCK-{calls}"}) for item in items]

    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: RefreshingAdapter())
    params = {
        "provider": "mock",
        "origin": "서울",
        "destination": "부산",
        "departure_from": "2026-08-01T08:00:00+09:00",
        "departure_to": "2026-08-01T12:00:00+09:00",
    }
    initial = await client.get("/api/v1/timetables", params=params)
    assert initial.status_code == 200
    assert calls == 1

    now += timedelta(seconds=60)
    cached = await client.get("/api/v1/timetable-snapshots", params=params)
    assert cached.status_code == 200
    assert cached.json() == initial.json()
    await cache.drain_pending_refreshes()

    refreshed = await client.get("/api/v1/timetable-snapshots", params=params)
    assert refreshed.status_code == 200
    assert calls == 2
    assert refreshed.json()[0]["train_number"] == "MOCK-2"


async def test_srt_timetable_uses_live_primary_without_tago(app, client, monkeypatch):
    from rail_waitlist import api

    captured = None

    class CapturingSeatSource:
        async def search_timetable(self, **kwargs):
            nonlocal captured
            captured = kwargs
            return [
                SrtTimetableTrain(
                    train_number="321",
                    train_type="SRT",
                    origin="수서",
                    destination="부산",
                    departure_at=datetime.fromisoformat("2026-08-01T08:30:00+09:00"),
                    arrival_at=datetime.fromisoformat("2026-08-01T11:10:00+09:00"),
                    standard_status="available",
                    first_status="sold_out",
                    observed_at=datetime.fromisoformat("2026-08-01T07:59:00+09:00"),
                    adult_fare=52_600,
                )
            ]

    def must_not_call_tago(provider):
        raise AssertionError("successful SRT live timetable must not call TAGO")

    previous = app.state.srt_seat_source
    app.state.srt_seat_source = CapturingSeatSource()
    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_tago)
    try:
        response = await client.get(
            "/api/v1/timetables",
            params={
                "provider": "srt",
                "origin": "수서",
                "destination": "부산",
                "departure_from": "2026-08-01T08:00:00+09:00",
                "departure_to": "2026-08-01T12:00:00+09:00",
                "origin_node_id": "N1",
                "destination_node_id": "N3",
            },
        )
    finally:
        app.state.srt_seat_source = previous

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["provider"] == "srt"
    assert payload["train_number"] == "321"
    assert payload["timetable_source"] == "official_provider"
    assert payload["seat_classes"][0]["status"] == "available"
    assert captured == {
        "origin": "수서",
        "destination": "부산",
        "departure_from": datetime.fromisoformat("2026-08-01T08:00:00+09:00"),
        "departure_to": datetime.fromisoformat("2026-08-01T12:00:00+09:00"),
        "passenger_count": 1,
    }


async def test_watch_read_projects_expired_confirmed_hold_as_ended(app, client):
    created = await client.post("/api/v1/watches", json=watch_payload())
    assert created.status_code == 201, created.text
    candidate_id = created.json()["candidates"][0]["id"]
    started_at = datetime(2030, 8, 1, 8, 22, 1, tzinfo=UTC)
    deadline = started_at + timedelta(minutes=9)
    reconciled_at = deadline + timedelta(minutes=1)

    async with app.state.test_session_factory() as session:
        session.add(
            ReservationAttempt(
                candidate_id=candidate_id,
                attempt_sequence=1,
                episode_key="expired-confirmed-hold",
                idempotency_key="expired-confirmed-hold",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=2),
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                payment_deadline=deadline,
                confirmation_outcome=(
                    ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
                ),
                confirmation_source="srt.reservations",
                confirmation_observed_at=reconciled_at,
                last_reconciled_at=reconciled_at,
                post_deadline_reconciled_at=reconciled_at,
            )
        )
        await session.commit()

    response = await client.get(f"/api/v1/watches/{created.json()['id']}")
    assert response.status_code == 200, response.text
    attempt = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert attempt["confirmation_outcome"] == "confirmed_payment_required"
    assert attempt["payment_hold_end_reason"] == "confirmed_payment_deadline_elapsed"
    assert attempt["retryable"] is False
    assert attempt["manual_check_required"] is False
    assert attempt["retry_condition"] is None


@pytest.mark.parametrize(
    ("provider", "origin", "source_attribute"),
    [
        (Provider.KORAIL, "서울", "korail_browser_seat_source"),
        (Provider.SRT, "수서", "srt_seat_source"),
    ],
)
async def test_official_live_timetable_does_not_require_tago_station_nodes(
    app,
    client,
    monkeypatch,
    provider,
    origin,
    source_attribute,
):
    from rail_waitlist import api

    class LiveSource:
        async def search_timetable(self, **kwargs):
            if provider is Provider.KORAIL:
                return [official_timetable_item()]
            return [
                SrtTimetableTrain(
                    train_number="321",
                    train_type="SRT",
                    origin="수서",
                    destination="부산",
                    departure_at=datetime.fromisoformat("2026-08-01T08:30:00+09:00"),
                    arrival_at=datetime.fromisoformat("2026-08-01T11:10:00+09:00"),
                    standard_status="available",
                    first_status="sold_out",
                    observed_at=datetime.fromisoformat("2026-08-01T07:59:00+09:00"),
                    adult_fare=52_600,
                )
            ]

    def must_not_call_tago(provider):
        raise AssertionError("successful official live timetable must not call TAGO")

    monkeypatch.setattr(app.state, source_attribute, LiveSource())
    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_tago)
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": provider.value,
            "origin": origin,
            "destination": "부산",
            "departure_from": "2026-08-01T08:00:00+09:00",
            "departure_to": "2026-08-01T12:00:00+09:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["provider"] == provider.value
    assert payload["timetable_source"] == "official_provider"
    assert [seat["status"] for seat in payload["seat_classes"]] == [
        "available",
        "sold_out",
    ]
    assert all(seat["registration_evidence_id"] is None for seat in payload["seat_classes"])


async def test_live_timetable_failure_falls_back_to_tago_once_without_live_retry(
    app, client, monkeypatch
):
    from rail_waitlist import api
    from rail_waitlist.korail_browser_seat_source import KorailBrowserTimetableUnavailable

    live_calls = 0
    tago_calls = 0

    class UnavailableLiveSource:
        async def search_timetable(self, **kwargs):
            nonlocal live_calls
            live_calls += 1
            raise KorailBrowserTimetableUnavailable("official live source unavailable")

    class CapturingTagoAdapter:
        async def timetable(self, **kwargs):
            nonlocal tago_calls
            tago_calls += 1
            return [tago_timetable_item()]

    previous = app.state.korail_browser_seat_source
    app.state.korail_browser_seat_source = UnavailableLiveSource()
    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: CapturingTagoAdapter())
    try:
        response = await client.get(
            "/api/v1/timetables",
            params={
                "provider": "korail",
                "origin": "서울",
                "destination": "부산",
                "departure_from": "2026-08-01T08:00:00+09:00",
                "departure_to": "2026-08-01T12:00:00+09:00",
                "origin_node_id": "N1",
                "destination_node_id": "N3",
            },
        )
    finally:
        app.state.korail_browser_seat_source = previous

    assert response.status_code == 200
    assert response.json()[0]["timetable_source"] == "TAGO"
    assert live_calls == 1
    assert tago_calls == 1


async def test_timetable_returns_safe_503_when_live_and_tago_are_unavailable(
    app, client, monkeypatch
):
    from rail_waitlist import api
    from rail_waitlist.korail_browser_seat_source import KorailBrowserTimetableUnavailable
    from rail_waitlist.providers import ProviderUnavailable

    class UnavailableLiveSource:
        async def search_timetable(self, **kwargs):
            raise KorailBrowserTimetableUnavailable("official live source unavailable")

    class UnavailableTagoAdapter:
        async def timetable(self, **kwargs):
            raise ProviderUnavailable("official timetable sources are unavailable")

    previous = app.state.korail_browser_seat_source
    app.state.korail_browser_seat_source = UnavailableLiveSource()
    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: UnavailableTagoAdapter())
    try:
        response = await client.get(
            "/api/v1/timetables",
            params={
                "provider": "korail",
                "origin": "서울",
                "destination": "부산",
                "departure_from": "2026-08-01T08:00:00+09:00",
                "departure_to": "2026-08-01T12:00:00+09:00",
                "origin_node_id": "N1",
                "destination_node_id": "N3",
            },
        )
    finally:
        app.state.korail_browser_seat_source = previous

    assert response.status_code == 503
    assert response.json() == {"detail": "official timetable sources are unavailable"}


async def test_srt_timetable_does_not_expose_route_outside_server_source_roster(
    app, client, monkeypatch
):
    from rail_waitlist import api
    from rail_waitlist.domain import Provider
    from rail_waitlist.providers import OfficialTimetableAdapter

    class RejectingTagoClient:
        async def timetable(self, *args, **kwargs):
            raise AssertionError("unsupported SRT route must not reach TAGO timetable")

    class FakeStationCatalogService:
        tago_client = RejectingTagoClient()

        async def get_catalog(self, provider):
            assert provider is Provider.SRT
            return object()

    class UnsupportedRouteLiveSource:
        async def search_timetable(self, **kwargs):
            return []

    previous_source = app.state.srt_seat_source
    app.state.station_catalog_service = FakeStationCatalogService()
    app.state.srt_seat_source = UnsupportedRouteLiveSource()
    monkeypatch.setattr(
        api,
        "get_timetable_provider",
        lambda provider: OfficialTimetableAdapter(
            Provider.SRT, tago_client=RejectingTagoClient()
        ),
    )
    try:
        response = await client.get(
            "/api/v1/timetables",
            params={
                "provider": "srt",
                "origin": "대전",
                "destination": "서울",
                "departure_from": "2026-08-01T12:00:00+09:00",
                "departure_to": "2026-08-01T18:00:00+09:00",
                "origin_node_id": "N-DAEJEON",
                "destination_node_id": "N-SEOUL",
            },
        )
    finally:
        app.state.srt_seat_source = previous_source

    assert response.status_code == 200
    assert response.json() == []


async def test_authenticated_seat_status_refresh_never_uses_server_korail_source(
    app, client, monkeypatch
):
    from rail_waitlist import api

    class KorailTimetableAdapter(MockProviderAdapter):
        async def timetable(
            self,
            origin,
            destination,
            departure_from,
            origin_node_id=None,
            destination_node_id=None,
            departure_to=None,
        ):
            return await super().timetable(
                origin,
                destination,
                departure_from,
                departure_to=departure_to,
            )

    class RejectingSeatSource:
        async def overlay(self, items, **kwargs):
            raise AssertionError("KORAIL direct source must not be called")

    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: KorailTimetableAdapter())
    app.state.korail_seat_source = RejectingSeatSource()
    request_data = {
        "provider": "korail",
        "origin": "대전",
        "destination": "서울",
        "departure_from": "2026-08-01T08:00:00+09:00",
        "departure_to": "2026-08-01T12:00:00+09:00",
        "origin_node_id": "N1",
        "destination_node_id": "N3",
        "passenger_count": 2,
    }
    response = await client.post("/api/v1/seat-status/refresh", json=request_data)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()

    def must_not_call_provider(provider):
        raise AssertionError("cache-only endpoint must not load a provider")

    monkeypatch.setattr(api, "get_timetable_provider", must_not_call_provider)
    snapshot = await client.get("/api/v1/timetable-snapshots", params=request_data)
    assert snapshot.status_code == 200
    assert snapshot.json() == response.json()


async def test_seat_status_refresh_is_authenticated(public_client):
    response = await public_client.post(
        "/api/v1/seat-status/refresh",
        json={
            "provider": "korail",
            "origin": "대전",
            "destination": "서울",
            "departure_from": "2026-08-01T08:00:00+09:00",
            "departure_to": "2026-08-01T12:00:00+09:00",
            "origin_node_id": "N1",
            "destination_node_id": "N3",
        },
    )
    assert response.status_code == 401


async def test_seat_status_source_status_is_authenticated(public_client):
    response = await public_client.get("/api/v1/seat-status/status")
    assert response.status_code == 401


async def test_seat_status_source_status_exposes_only_safe_cooldown_metadata(app, client):
    from rail_waitlist.seat_status_cooldown import MemoryCooldownStore

    cooldown_store = MemoryCooldownStore()
    await cooldown_store.set("korail-browser", "provider_access_restricted", 120)
    app.state.seat_status_cooldown_store = cooldown_store

    response = await client.get("/api/v1/seat-status/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    korail, srt = response.json()
    assert set(korail) == {
        "provider",
        "source",
        "state",
        "cause",
        "retry_after_seconds",
    }
    assert korail["provider"] == "korail"
    assert korail["source"] == "korail_browser"
    assert korail["state"] == "cooldown"
    assert korail["cause"] == "provider_access_restricted"
    assert 0 < korail["retry_after_seconds"] <= 120
    assert srt == {
        "provider": "srt",
        "source": "srt_live",
        "state": "ready",
        "cause": None,
        "retry_after_seconds": None,
    }


async def test_timetable_rejects_invalid_passenger_count(client):
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "mock",
            "origin": "서울",
            "destination": "부산",
            "departure_from": "2026-08-01T08:00:00+09:00",
            "departure_to": "2026-08-01T12:00:00+09:00",
            "passenger_count": 10,
        },
    )

    assert response.status_code == 422


async def test_timetable_endpoint_requires_complete_departure_window(client):
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "mock",
            "origin": "서울",
            "destination": "부산",
            "departure_from": "2026-08-01T08:00:00+09:00",
        },
    )

    assert response.status_code == 422


async def test_mock_timetable_endpoint_covers_the_inclusive_departure_window(client):
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "mock",
            "origin": "서울",
            "destination": "부산",
            "departure_from": "2026-08-01T08:00:00+09:00",
            "departure_to": "2026-08-01T12:00:00+09:00",
        },
    )

    assert response.status_code == 200
    departures = [item["departure_at"] for item in response.json()]
    assert len(departures) == 7
    assert departures[0] == "2026-08-01T08:00:00+09:00"
    assert departures[-1] == "2026-08-01T12:00:00+09:00"


async def test_watch_crud_transition_and_idempotency(client, db_engine):
    headers = {"Idempotency-Key": "create-seoul-busan"}
    first = await client.post("/api/v1/watches", json=watch_payload(), headers=headers)
    second = await client.post("/api/v1/watches", json=watch_payload(), headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    watch_id = first.json()["id"]

    started = await client.post(f"/api/v1/watches/{watch_id}/start")
    assert started.json()["status"] == "scheduled"
    paused = await client.post(f"/api/v1/watches/{watch_id}/pause")
    assert paused.json()["status"] == "paused"
    cancelled = await client.post(f"/api/v1/watches/{watch_id}/cancel")
    assert cancelled.json()["status"] == "expired"
    deleted = await client.delete(f"/api/v1/watches/{watch_id}")
    assert deleted.status_code == 204
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert list((await session.scalars(select(WatchCandidate))).all()) == []


async def test_concurrent_watch_create_reuses_one_idempotent_resource(
    client, db_engine
):
    headers = {"Idempotency-Key": "concurrent-create-seoul-busan"}
    first, second = await asyncio.gather(
        client.post("/api/v1/watches", json=watch_payload(), headers=headers),
        client.post("/api/v1/watches", json=watch_payload(), headers=headers),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Watch)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.scope == "watch.create",
                    IdempotencyRecord.key == "concurrent-create-seoul-busan",
                )
            )
            == 1
        )


async def test_watch_persists_station_nodes_and_ordered_candidates(client, db_engine):
    payload = watch_payload(
        travel_date="2030-08-01",
        train_numbers=["KTX-001", "KTX-003"],
        candidates=[
            {
                "train_number": "KTX-003",
                "departure_at": "2030-08-01T10:30:00+09:00",
                "arrival_at": None,
                "seat_class": "standard",
                "priority": 2,
            },
            {
                "train_number": "KTX-001",
                "departure_at": "2030-08-01T08:30:00+09:00",
                "arrival_at": "2030-08-01T11:00:00+09:00",
                "seat_class": "standard",
                "priority": 1,
            },
        ],
    )
    response = await client.post("/api/v1/watches", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["origin_node_id"] == "N-SEOUL"
    assert body["destination_node_id"] == "N-BUSAN"
    assert [item["train_number"] for item in body["candidates"]] == [
        "KTX-001",
        "KTX-003",
    ]
    assert [item["priority"] for item in body["candidates"]] == [1, 2]
    assert all(item["id"] for item in body["candidates"])
    assert datetime.fromisoformat(body["candidates"][0]["departure_at"]) == (
        datetime.fromisoformat(payload["candidates"][1]["departure_at"]).astimezone(
            timezone.utc
        )
    )

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(WatchCandidate).order_by(WatchCandidate.priority)
                )
            ).all()
        )
    assert [(row.train_number, row.priority) for row in rows] == [
        ("KTX-001", 1),
        ("KTX-003", 2),
    ]


async def test_watch_read_includes_latest_observation_separately_from_registration_evidence(
    app, client
):
    created = await client.post("/api/v1/watches", json=watch_payload())
    assert created.status_code == 201
    body = created.json()
    candidate_id = body["candidates"][0]["id"]
    observed_at = datetime(2030, 8, 1, 0, 15, tzinfo=timezone.utc)

    async with app.state.test_session_factory() as session:
        evidence = TimetableSeatEvidence(
            evidence_hash="a" * 64,
            provider=Provider.KORAIL,
            origin_node_id="N-SEOUL",
            destination_node_id="N-BUSAN",
            canonical_train_number="KTX-001",
            departure_at=datetime(2030, 8, 1, 0, tzinfo=timezone.utc),
            passenger_count=1,
            seat_class=SeatClass.STANDARD,
            status=SeatObservationStatus.AVAILABLE,
            provenance_kind="official_provider",
            source="authorized-provider",
            observed_at=observed_at - timedelta(minutes=1),
            fresh_until=observed_at + timedelta(minutes=1),
            reason=None,
            registration_allowed=True,
            registration_valid_until=observed_at + timedelta(minutes=5),
        )
        candidate = await session.get(WatchCandidate, candidate_id)
        assert candidate is not None
        candidate.registration_evidence = evidence
        session.add(
            SeatObservation(
                candidate_id=candidate_id,
                status=SeatObservationStatus.SOLD_OUT,
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=1),
            )
        )
        watch = await session.get(Watch, body["id"])
        assert watch is not None
        watch.updated_at = observed_at + timedelta(hours=2)
        await session.commit()

    response = await client.get(f"/api/v1/watches/{body['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert datetime.fromisoformat(payload["last_checked_at"]) == observed_at
    assert datetime.fromisoformat(payload["updated_at"]) != observed_at
    candidate = payload["candidates"][0]
    assert candidate["registration_evidence"]["status"] == "available"
    assert candidate["latest_observation"] == {
        "status": "sold_out",
        "source": "mock",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "fresh_until": (observed_at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "error_category": None,
    }

    listed = await client.get("/api/v1/watches")
    assert listed.status_code == 200
    assert datetime.fromisoformat(listed.json()[0]["last_checked_at"]) == observed_at


async def test_watches_list_includes_an_independent_latest_observation_per_candidate(app, client):
    travel_date = (date.today() + timedelta(days=7)).isoformat()
    created = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            train_numbers=["KTX-001", "KTX-003"],
            candidates=[
                {
                    "train_number": "KTX-001",
                    "departure_at": f"{travel_date}T08:30:00+09:00",
                    "arrival_at": f"{travel_date}T11:00:00+09:00",
                    "seat_class": "standard",
                    "priority": 1,
                },
                {
                    "train_number": "KTX-003",
                    "departure_at": f"{travel_date}T09:30:00+09:00",
                    "arrival_at": f"{travel_date}T12:00:00+09:00",
                    "seat_class": "standard",
                    "priority": 2,
                },
            ],
        ),
    )
    assert created.status_code == 201
    candidate_ids = [candidate["id"] for candidate in created.json()["candidates"]]
    first_observed_at = datetime(2030, 8, 1, 0, 15, tzinfo=timezone.utc)
    second_observed_at = first_observed_at + timedelta(minutes=1)
    async with app.state.test_session_factory() as session:
        session.add_all(
            [
                SeatObservation(
                    candidate_id=candidate_ids[0],
                    status=SeatObservationStatus.AVAILABLE,
                    source="mock",
                    observed_at=first_observed_at - timedelta(minutes=1),
                    fresh_until=first_observed_at,
                ),
                SeatObservation(
                    candidate_id=candidate_ids[0],
                    status=SeatObservationStatus.SOLD_OUT,
                    source="mock",
                    observed_at=first_observed_at,
                    fresh_until=first_observed_at + timedelta(minutes=1),
                ),
                SeatObservation(
                    candidate_id=candidate_ids[1],
                    status=SeatObservationStatus.LIMITED,
                    source="mock",
                    observed_at=second_observed_at,
                    fresh_until=second_observed_at + timedelta(minutes=1),
                ),
            ]
        )
        await session.commit()

    listed = await client.get("/api/v1/watches")
    assert listed.status_code == 200
    candidates = listed.json()[0]["candidates"]
    assert [candidate["latest_observation"]["status"] for candidate in candidates] == [
        "sold_out",
        "limited",
    ]
    assert datetime.fromisoformat(listed.json()[0]["last_checked_at"]) == second_observed_at


async def test_watch_reads_include_latest_reservation_attempt_policy_per_candidate(app, client):
    travel_date = (datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=7)).isoformat()
    created = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            reservation_policy="reserve_once_before_payment",
            travel_date=travel_date,
            time_to="13:00:00",
            train_numbers=["KTX-001", "KTX-003", "KTX-005", "KTX-007"],
            candidates=[
                {
                    "train_number": train_number,
                    "departure_at": f"{travel_date}T{8 + priority:02d}:30:00+09:00",
                    "arrival_at": f"{travel_date}T{10 + priority:02d}:00:00+09:00",
                    "seat_class": "standard",
                    "priority": priority,
                }
                for priority, train_number in enumerate(
                    ["KTX-001", "KTX-003", "KTX-005", "KTX-007"], start=1
                )
            ],
        ),
    )
    assert created.status_code == 201, created.text
    candidate_ids = [candidate["id"] for candidate in created.json()["candidates"]]
    started_at = datetime(2030, 8, 1, 0, 15, tzinfo=UTC)

    async with app.state.test_session_factory() as session:
        session.add_all(
            [
                ReservationAttempt(
                    candidate_id=candidate_ids[0],
                    attempt_sequence=1,
                    episode_key="not-available-episode",
                    idempotency_key="not-available-attempt",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=4),
                    outcome=ReservationOutcome.NOT_AVAILABLE,
                ),
                ReservationAttempt(
                    candidate_id=candidate_ids[1],
                    attempt_sequence=1,
                    episode_key="failed-episode",
                    idempotency_key="failed-attempt",
                    started_at=started_at + timedelta(minutes=1),
                    finished_at=started_at + timedelta(minutes=1, seconds=3),
                    outcome=ReservationOutcome.FAILED,
                ),
                ReservationAttempt(
                    candidate_id=candidate_ids[2],
                    attempt_sequence=1,
                    episode_key="unknown-old-episode",
                    idempotency_key="unknown-old-attempt",
                    started_at=started_at + timedelta(minutes=3),
                    finished_at=started_at + timedelta(minutes=3, seconds=2),
                    outcome=ReservationOutcome.FAILED,
                ),
                ReservationAttempt(
                    candidate_id=candidate_ids[2],
                    attempt_sequence=2,
                    episode_key="unknown-latest-episode",
                    idempotency_key="unknown-latest-attempt",
                    started_at=started_at + timedelta(minutes=2),
                    finished_at=started_at + timedelta(minutes=2, seconds=5),
                    outcome=ReservationOutcome.UNKNOWN,
                ),
            ]
        )
        await session.commit()

    response = await client.get(f"/api/v1/watches/{created.json()['id']}")
    assert response.status_code == 200, response.text
    attempts = [
        candidate["latest_reservation_attempt"]
        for candidate in response.json()["candidates"]
    ]
    assert attempts[0] == {
        "outcome": "not_available",
        "confirmation_outcome": None,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": (started_at + timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
        "post_deadline_reconciled_at": None,
        "payment_hold_end_reason": None,
        "retryable": True,
        "manual_check_required": False,
        "retry_condition": "new_availability_episode",
    }
    assert attempts[1]["outcome"] == "failed"
    assert attempts[1]["retryable"] is False
    assert attempts[1]["manual_check_required"] is True
    assert attempts[1]["retry_condition"] is None
    assert attempts[2]["outcome"] == "unknown"
    assert attempts[2]["started_at"] == (started_at + timedelta(minutes=2)).isoformat().replace(
        "+00:00", "Z"
    )
    assert attempts[2]["retryable"] is False
    assert attempts[2]["manual_check_required"] is True
    assert attempts[3] is None

    listed = await client.get("/api/v1/watches")
    assert listed.status_code == 200
    listed_candidates = listed.json()[0]["candidates"]
    assert [candidate["latest_reservation_attempt"] for candidate in listed_candidates] == attempts


async def test_watch_read_projects_ended_srt_374_hold_as_new_episode_retry(app, client):
    travel_date = (datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=7)).isoformat()
    created = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            reservation_policy="reserve_once_before_payment",
            travel_date=travel_date,
            time_from="22:00:00",
            time_to="23:59:00",
            train_numbers=["374"],
            candidates=[
                {
                    "train_number": "374",
                    "departure_at": f"{travel_date}T22:52:00+09:00",
                    "arrival_at": f"{travel_date}T23:39:00+09:00",
                    "seat_class": "standard",
                    "priority": 1,
                }
            ],
        ),
    )
    assert created.status_code == 201, created.text
    candidate_id = created.json()["candidates"][0]["id"]
    started_at = datetime(2030, 8, 1, 8, 22, 1, tzinfo=UTC)
    reconciled_at = started_at + timedelta(minutes=12)

    async with app.state.test_session_factory() as session:
        session.add(
            ReservationAttempt(
                candidate_id=candidate_id,
                attempt_sequence=1,
                episode_key="srt-374-availability-episode",
                idempotency_key="srt-374-payment-hold",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=2),
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
                confirmation_source="srt.reservations",
                confirmation_observed_at=reconciled_at,
                last_reconciled_at=reconciled_at,
                post_deadline_reconciled_at=reconciled_at,
            )
        )
        await session.commit()

    response = await client.get(f"/api/v1/watches/{created.json()['id']}")
    assert response.status_code == 200, response.text
    attempt = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert attempt == {
        "outcome": "payment_required",
        "confirmation_outcome": "not_found",
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": (started_at + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        "post_deadline_reconciled_at": reconciled_at.isoformat().replace("+00:00", "Z"),
        "payment_hold_end_reason": "confirmed_payment_hold_no_longer_present",
        "retryable": True,
        "manual_check_required": False,
        "retry_condition": "new_availability_episode",
    }


@pytest.mark.parametrize(
    ("reservation_policy", "expected_retryable", "expected_retry_condition"),
    [
        ("reserve_once_before_payment", True, "new_availability_episode"),
        ("notify_only", False, None),
    ],
)
async def test_watch_read_projects_elapsed_confirmed_hold_by_policy(
    app,
    client,
    reservation_policy,
    expected_retryable,
    expected_retry_condition,
):
    travel_date = (datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=7)).isoformat()
    created = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            reservation_policy=reservation_policy,
            travel_date=travel_date,
            time_from="22:00:00",
            time_to="23:59:00",
            train_numbers=["370"],
            candidates=[
                {
                    "train_number": "370",
                    "departure_at": f"{travel_date}T22:06:00+09:00",
                    "arrival_at": f"{travel_date}T23:12:00+09:00",
                    "seat_class": "standard",
                    "priority": 1,
                }
            ],
        ),
    )
    assert created.status_code == 201, created.text
    candidate_id = created.json()["candidates"][0]["id"]
    started_at = datetime(2030, 8, 1, 8, 1, tzinfo=UTC)
    deadline = started_at + timedelta(minutes=10)
    reconciled_at = deadline + timedelta(minutes=1)

    async with app.state.test_session_factory() as session:
        session.add(
            ReservationAttempt(
                candidate_id=candidate_id,
                attempt_sequence=1,
                episode_key="srt-370-availability-episode",
                idempotency_key="srt-370-expired-confirmed-payment-hold",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=2),
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                payment_deadline=deadline,
                confirmation_outcome=(
                    ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
                ),
                confirmation_source="srtrain-reservation-list",
                confirmation_observed_at=reconciled_at,
                last_reconciled_at=reconciled_at,
                post_deadline_reconciled_at=reconciled_at,
            )
        )
        await session.commit()

    response = await client.get(f"/api/v1/watches/{created.json()['id']}")
    assert response.status_code == 200, response.text
    attempt = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert attempt["outcome"] == "payment_required"
    assert attempt["confirmation_outcome"] == "confirmed_payment_required"
    assert attempt["post_deadline_reconciled_at"] == reconciled_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert attempt["payment_hold_end_reason"] == "confirmed_payment_deadline_elapsed"
    assert attempt["retryable"] is expected_retryable
    assert attempt["manual_check_required"] is False
    assert attempt["retry_condition"] == expected_retry_condition


@pytest.mark.parametrize(
    ("outcome", "confirmation_outcome", "post_deadline_reconciled_at"),
    [
        (
            ReservationOutcome.PAYMENT_REQUIRED,
            None,
            datetime(2030, 8, 1, 0, 30, tzinfo=UTC),
        ),
        (ReservationOutcome.PAYMENT_REQUIRED, ReservationConfirmationOutcome.NOT_FOUND, None),
        (
            ReservationOutcome.PAYMENT_REQUIRED,
            ReservationConfirmationOutcome.INCONCLUSIVE,
            datetime(2030, 8, 1, 0, 30, tzinfo=UTC),
        ),
        (
            ReservationOutcome.FAILED,
            ReservationConfirmationOutcome.NOT_FOUND,
            datetime(2030, 8, 1, 0, 30, tzinfo=UTC),
        ),
    ],
)
async def test_watch_read_keeps_incomplete_payment_hold_end_evidence_fail_closed(
    app,
    client,
    outcome,
    confirmation_outcome,
    post_deadline_reconciled_at,
):
    created = await client.post("/api/v1/watches", json=watch_payload())
    assert created.status_code == 201, created.text
    candidate_id = created.json()["candidates"][0]["id"]
    started_at = datetime(2030, 8, 1, 0, 15, tzinfo=UTC)

    async with app.state.test_session_factory() as session:
        attempt = ReservationAttempt(
            candidate_id=candidate_id,
            attempt_sequence=1,
            episode_key=f"incomplete-{confirmation_outcome}-{post_deadline_reconciled_at}",
            idempotency_key=f"incomplete-{confirmation_outcome}-{post_deadline_reconciled_at}",
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=2),
            outcome=outcome,
            post_deadline_reconciled_at=post_deadline_reconciled_at,
        )
        if confirmation_outcome is not None:
            attempt.confirmation_outcome = confirmation_outcome
            attempt.confirmation_source = "provider.reservations"
            attempt.confirmation_observed_at = started_at + timedelta(minutes=1)
            attempt.last_reconciled_at = started_at + timedelta(minutes=1)
        session.add(attempt)
        await session.commit()

    response = await client.get(f"/api/v1/watches/{created.json()['id']}")
    assert response.status_code == 200, response.text
    attempt_payload = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert attempt_payload["retryable"] is False
    assert attempt_payload["manual_check_required"] is (outcome is ReservationOutcome.FAILED)
    assert attempt_payload["retry_condition"] is None


async def test_official_watch_fails_closed_without_station_node_identity(client):
    response = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            provider="korail", origin_node_id=None, destination_node_id=None
        ),
    )
    assert response.status_code == 422
    assert "station node IDs" in response.text


async def test_watch_rejects_invalid_candidate_priority(client):
    travel_date = (date.today() + timedelta(days=7)).isoformat()
    response = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            candidates=[
                {
                    "train_number": "KTX-001",
                    "departure_at": f"{travel_date}T08:30:00+09:00",
                    "arrival_at": f"{travel_date}T11:00:00+09:00",
                    "seat_class": "standard",
                    "priority": 2,
                }
            ]
        ),
    )
    assert response.status_code == 422
    assert "contiguous from 1" in response.text


async def test_watch_rejects_candidate_outside_travel_date_or_time_window(client):
    travel_date = date.today() + timedelta(days=7)
    next_date = travel_date + timedelta(days=1)
    base = {
        "train_number": "KTX-001",
        "arrival_at": f"{next_date.isoformat()}T11:00:00+09:00",
        "seat_class": "standard",
        "priority": 1,
    }
    wrong_date = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            travel_date=travel_date.isoformat(),
            candidates=[
                {
                    **base,
                    "departure_at": f"{next_date.isoformat()}T08:30:00+09:00",
                }
            ],
        ),
    )
    outside_window = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            travel_date=travel_date.isoformat(),
            candidates=[
                {
                    **base,
                    "departure_at": f"{travel_date.isoformat()}T07:30:00+09:00",
                }
            ],
        ),
    )

    assert wrong_date.status_code == 422
    assert "travel_date" in wrong_date.text
    assert outside_window.status_code == 422
    assert "time window" in outside_window.text


async def test_watch_update_rejects_candidate_inconsistency(client):
    created = await client.post("/api/v1/watches", json=watch_payload())
    watch_id = created.json()["id"]

    seat_mismatch = await client.patch(
        f"/api/v1/watches/{watch_id}", json={"seat_class": "first"}
    )
    train_mismatch = await client.patch(
        f"/api/v1/watches/{watch_id}", json={"train_numbers": ["KTX-999"]}
    )
    time_mismatch = await client.patch(
        f"/api/v1/watches/{watch_id}", json={"time_from": "09:00:00"}
    )
    unchanged = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"seat_class": "standard", "train_numbers": ["KTX-001"]},
    )

    assert seat_mismatch.status_code == 422
    assert train_mismatch.status_code == 422
    assert time_mismatch.status_code == 422
    assert unchanged.status_code == 200, unchanged.text


async def test_experimental_mode_is_disabled(client):
    response = await client.post(
        "/api/v1/watches", json=watch_payload(provider="srt", mode="experimental")
    )
    assert response.status_code == 403


async def test_watch_rejects_past_travel_date_but_allows_today(client):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    past = await client.post(
        "/api/v1/watches",
        json=watch_payload(travel_date=(today - timedelta(days=1)).isoformat()),
    )
    assert past.status_code == 422

    current = await client.post(
        "/api/v1/watches",
        json=watch_payload(travel_date=today.isoformat()),
    )
    assert current.status_code == 201


async def test_watch_rejects_unknown_seat_class(client):
    response = await client.post(
        "/api/v1/watches", json=watch_payload(seat_class="premium_unknown")
    )
    assert response.status_code == 422

    created = await client.post("/api/v1/watches", json=watch_payload())
    updated = await client.patch(
        f"/api/v1/watches/{created.json()['id']}",
        json={"seat_class": "premium_unknown"},
    )
    assert updated.status_code == 422


async def test_watch_update_rejects_null_and_unknown_notification_channel(client):
    created = await client.post("/api/v1/watches", json=watch_payload())
    watch_id = created.json()["id"]
    assert (
        await client.patch(f"/api/v1/watches/{watch_id}", json={"time_from": None})
    ).status_code == 422
    invalid = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"notification_channel_ids": ["missing-channel"]},
    )
    assert invalid.status_code == 422


async def test_metrics_are_exposed(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "rail_waitlist_http_requests_total" in response.text


async def test_webhook_channel_rejects_private_target(client):
    response = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "generic_webhook",
            "name": "unsafe",
            "config": {"url": "https://127.0.0.1/internal"},
        },
    )
    assert response.status_code == 422


async def test_webpush_public_key_uses_server_configuration(client):
    from rail_waitlist.config import get_settings

    settings = get_settings()
    previous = settings.webpush_vapid_public_key
    settings.webpush_vapid_public_key = "test-public-vapid-key"
    try:
        response = await client.get("/api/v1/notifications/web-push/public-key")
    finally:
        settings.webpush_vapid_public_key = previous
    assert response.json() == {"public_key": "test-public-vapid-key"}


async def test_notification_secret_is_redacted_and_test_is_outboxed(client, db_engine):
    response = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "내 텔레그램",
            "config": {"bot_token": "very-secret-token", "chat_id": "123"},
        },
    )
    assert response.status_code == 201
    assert "config" not in response.json()
    channel_id = response.json()["id"]
    fetched = await client.get(f"/api/v1/notifications/channels/{channel_id}")
    assert fetched.json()["configured"] is True

    queued = await client.post(f"/api/v1/notifications/channels/{channel_id}/test-send")
    assert queued.status_code == 202
    assert queued.json()["queued"] is True

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = await session.get(NotificationChannel, channel_id)
        assert "very-secret-token" not in channel.config_ciphertext
        events = list((await session.scalars(select(OutboxEvent))).all())
        assert any(event.event_type == "notification.test_requested" for event in events)


async def test_mock_reservation_can_only_be_attempted_once(client, db_engine):
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=watching")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=seat_found")
    reserving = await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=reserving")
    assert reserving.status_code == 200
    second = await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=reserving")
    assert second.status_code == 409
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        attempts = list((await session.scalars(select(ReservationAttempt))).all())
        assert len(attempts) == 1
        assert attempts[0].outcome is ReservationOutcome.PENDING


async def test_mock_payment_deadline_is_returned(client, db_engine):
    deadline = datetime.now(timezone.utc) + timedelta(minutes=20)
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=watching")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=seat_found")
    result = await client.post(
        f"/api/v1/watches/{watch_id}/mock-transition",
        params={"target": "payment_required", "payment_deadline": deadline.isoformat()},
    )
    assert result.status_code == 200
    assert datetime.fromisoformat(result.json()["payment_deadline"]) == deadline
    assert result.json()["status"] == "payment_required"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        history = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory)
                    .where(WatchTransitionHistory.watch_id == watch_id)
                    .order_by(WatchTransitionHistory.created_at)
                )
            ).all()
        )
        assert any(
            item.from_status is WatchStatus.RESERVING
            and item.to_status is WatchStatus.PAYMENT_REQUIRED
            for item in history
        )

    duplicate = await client.post(
        f"/api/v1/watches/{watch_id}/mock-transition",
        params={
            "target": "payment_required",
            "payment_deadline": (deadline + timedelta(minutes=5)).isoformat(),
        },
    )
    assert duplicate.status_code == 409


async def test_payment_deadlines_require_an_explicit_timezone(client):
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    update = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"payment_deadline": "2026-08-01T12:00:00"},
    )
    assert update.status_code == 422

    await client.post(f"/api/v1/watches/{watch_id}/start")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=watching")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=seat_found")
    transition = await client.post(
        f"/api/v1/watches/{watch_id}/mock-transition",
        params={"target": "payment_required", "payment_deadline": "2026-08-01T12:00:00"},
    )
    assert transition.status_code == 422


async def test_active_watch_can_enable_bounded_focused_observation(client):
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    started = await client.post(f"/api/v1/watches/{watch_id}/start")
    assert started.status_code == 200

    focused = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={
            "seat_observation_mode": "focused",
            "focused_observation_interval_seconds": 20,
        },
    )

    assert focused.status_code == 200
    assert focused.json()["seat_observation_mode"] == "focused"
    assert focused.json()["focused_observation_interval_seconds"] == 20
    assert focused.json()["next_check_at"] is not None

    below_safe_floor = await client.patch(
        f"/api/v1/watches/{watch_id}",
        json={"focused_observation_interval_seconds": 19},
    )
    assert below_safe_floor.status_code == 422


async def test_focused_observation_is_limited_to_three_watches_per_provider(client):
    for index in range(3):
        created = await client.post(
            "/api/v1/watches",
            json=watch_payload(
                provider="mock",
                seat_observation_mode="focused",
                focused_observation_interval_seconds=20 + index,
            ),
        )
        assert created.status_code == 201, created.text

    rejected = await client.post(
        "/api/v1/watches",
        json=watch_payload(
            provider="mock",
            seat_observation_mode="focused",
            focused_observation_interval_seconds=25,
        ),
    )

    assert rejected.status_code == 409
    assert "up to 3" in rejected.json()["detail"]


async def test_transition_refreshes_stale_watch_before_idempotent_commit(
    client, db_engine
):
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as first_session, factory() as stale_session:
        first = await first_session.get(Watch, watch_id)
        stale = await stale_session.get(Watch, watch_id)
        assert first.status is WatchStatus.DRAFT
        assert stale.status is WatchStatus.DRAFT
        await transition_watch(
            first_session, first, WatchStatus.SCHEDULED, "same-transition-key"
        )
        result = await transition_watch(
            stale_session, stale, WatchStatus.SCHEDULED, "same-transition-key"
        )
        assert result.status is WatchStatus.SCHEDULED

    async with factory() as session:
        histories = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory).where(
                        WatchTransitionHistory.watch_id == watch_id,
                        WatchTransitionHistory.to_status == WatchStatus.SCHEDULED,
                    )
                )
            ).all()
        )
        keys = list(
            (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.scope == "watch.transition.scheduled",
                        IdempotencyRecord.key == "same-transition-key",
                    )
                )
            ).all()
        )
        assert len(histories) == 1
        assert len(keys) == 1


async def test_update_refreshes_stale_watch_before_rebuilding_dedupe_key(
    client, db_engine
):
    created = await client.post("/api/v1/watches", json=watch_payload(provider="mock"))
    watch_id = created.json()["id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as first_session, factory() as stale_session:
        first = await first_session.get(Watch, watch_id)
        stale = await stale_session.get(Watch, watch_id)
        await update_watch(first_session, first, WatchUpdate(passenger_count=2))
        await update_watch(stale_session, stale, WatchUpdate(time_to=time(11, 30)))

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)
        assert persisted.passenger_count == 2
        assert persisted.time_to == time(11, 30)
        assert persisted.dedupe_key == build_watch_dedupe_key(
            persisted.provider,
            persisted.origin,
            persisted.destination,
            persisted.travel_date,
            persisted.time_from,
            persisted.time_to,
            persisted.seat_class,
            persisted.passenger_count,
            persisted.train_numbers,
            persisted.origin_node_id,
            persisted.destination_node_id,
        )
