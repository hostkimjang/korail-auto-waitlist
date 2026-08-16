from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from typing import cast as _cast
from zoneinfo import ZoneInfo

from korail2 import AdultPassenger, Korail  # type: ignore[import-untyped]
from korail2.korail2 import KorailError, NoResultsError  # type: ignore[import-untyped]
from pydantic import AnyHttpUrl as _AnyHttpUrl
from requests import PreparedRequest as _PreparedRequest
from requests import RequestException
from requests import Response as _Response
from requests.adapters import HTTPAdapter

from ..domain import SeatClass as _SeatClass
from ..seat_status_cooldown import CooldownStore, MemoryCooldownStore
from ..timetable_management.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatAvailabilityStatus,
    SeatClassAvailability,
    TimetableItem,
)

SOURCE_NAME = "korail2-0.4.0-accountless"
KOREA = ZoneInfo("Asia/Seoul")
PROTECTION_MARKERS = (
    "-8002",
    "-8003",
    "macro_err1",
    "captcha",
    "netfunnel",
    "비정상 접근",
    "미허가 도구",
)


class _KorailTrain(Protocol):
    train_no: str
    dep_date: str
    dep_time: str
    general_seat: str
    special_seat: str
    wait_reserve_flag: int | None
    reserve_possible_name: str | None


class _KorailClient(Protocol):
    def search_train(
        self,
        dep: str,
        arr: str,
        date: str,
        time: str,
        *,
        passengers: list[object],
        include_no_seats: bool,
        include_waiting_list: bool,
    ) -> list[_KorailTrain]: ...


KorailClientFactory = Callable[[], _KorailClient]
PassengerFactory = Callable[[int], object]


class _DefaultTimeoutAdapter(HTTPAdapter):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        request: _PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float | None, float | None] | None = None,
        verify: bool | str = True,
        cert: str | tuple[str, str] | None = None,
        proxies: dict[str, str] | None = None,
    ) -> _Response:
        if timeout is None:
            timeout = self.timeout_seconds
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


@dataclass(frozen=True)
class KorailSeatSnapshot:
    train_number: str
    departure_date: str
    departure_time: str
    standard_status: SeatAvailabilityStatus
    first_status: SeatAvailabilityStatus
    observed_at: datetime


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    snapshots: tuple[KorailSeatSnapshot, ...]


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


def _default_client_factory(timeout_seconds: float) -> _KorailClient:
    client = Korail("", "", auto_login=False, want_feedback=False)
    adapter = _DefaultTimeoutAdapter(timeout_seconds)
    # korail2 exposes no public transport hook, so timeout policy is attached to its session.
    client._session.mount("https://", adapter)
    return _cast(_KorailClient, client)


def normalize_train_number(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.lstrip("0") or "0"


def normalize_date(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(8)[-8:]


def normalize_time(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(6)[-6:]


def map_korail_seat_state(code: object, label: object = "") -> SeatAvailabilityStatus:
    normalized_label = "".join(str(label).split()).casefold()
    if "예약대기" in normalized_label:
        return "waitlist_available"
    if "매진임박" in normalized_label:
        return "limited"
    if "입석+좌석" in normalized_label or "입석+예매" in normalized_label:
        return "standing_plus_seat"
    if normalized_label in {"입석", "일반실입석", "입석예매", "일반실입석예매"}:
        return "standing_only"
    normalized_code = str(code).strip()
    if normalized_code == "11":
        return "available"
    if normalized_code == "13":
        return "sold_out"
    if normalized_code == "00":
        return "not_offered"
    return "unknown"


class KorailLiveSeatSource:
    def __init__(
        self,
        *,
        enabled: bool,
        cache_ttl_seconds: int,
        timeout_seconds: float,
        rate_limit_cooldown_seconds: int,
        protection_cooldown_seconds: int,
        client_factory: KorailClientFactory | None = None,
        passenger_factory: PassengerFactory = AdultPassenger,
        monotonic: Callable[[], float] = time.monotonic,
        cooldown_store: CooldownStore | None = None,
    ) -> None:
        self.enabled = enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.protection_cooldown_seconds = protection_cooldown_seconds
        socket_timeout = max(1.0, timeout_seconds - 1.0)
        self._client_factory = client_factory or (lambda: _default_client_factory(socket_timeout))
        self._passenger_factory = passenger_factory
        self._monotonic = monotonic
        self._cache: dict[tuple[str, str, str, str, str, int], _CacheEntry] = {}
        self._inflight: dict[
            tuple[str, str, str, str, str, int], asyncio.Task[tuple[KorailSeatSnapshot, ...]]
        ] = {}
        self._state_lock = asyncio.Lock()
        self._provider_gate = asyncio.Semaphore(1)
        self._cooldown_store = cooldown_store or MemoryCooldownStore(monotonic)
        self._failure_count = 0

    async def overlay(
        self,
        items: list[TimetableItem],
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]:
        if not items:
            return items
        if all(
            seat.provenance.kind != "not_observed" or seat.status != "unknown"
            for item in items
            for seat in item.seat_classes
        ):
            return items
        if not self.enabled:
            return self._mark_not_observed(items, "source_not_configured")
        local_from = departure_from.astimezone(KOREA)
        local_to = departure_to.astimezone(KOREA)
        if local_from.date() != local_to.date():
            return self._mark_not_observed(items, "source_unavailable")
        try:
            snapshots = await self._search(
                origin.strip(),
                destination.strip(),
                local_from.strftime("%Y%m%d"),
                local_from.strftime("%H%M%S"),
                local_to.strftime("%H%M%S"),
                passenger_count,
            )
        except _ProviderCooldown as error:
            return self._mark_not_observed(items, error.reason)
        except NoResultsError:
            snapshots = ()
        except KorailError as error:
            reason = self._reason_for_error(error)
            await self._open_cooldown(reason, error)
            return self._mark_not_observed(items, reason)
        except (
            TimeoutError,
            OSError,
            RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            await self._open_cooldown("source_unavailable", error)
            return self._mark_not_observed(items, "source_unavailable")

        by_identity = {
            (snapshot.train_number, snapshot.departure_date, snapshot.departure_time): snapshot
            for snapshot in snapshots
        }
        overlaid: list[TimetableItem] = []
        for item in items:
            local_departure = item.departure_at.astimezone(KOREA)
            snapshot = by_identity.get(
                (
                    normalize_train_number(item.train_number),
                    local_departure.strftime("%Y%m%d"),
                    local_departure.strftime("%H%M%S"),
                )
            )
            if snapshot is None:
                overlaid.extend(self._mark_not_observed([item], "no_exact_match"))
                continue
            overlaid.append(
                item.model_copy(
                    update={"seat_classes": self._seat_classes(snapshot, item.official_booking_url)}
                )
            )
        return overlaid

    async def _search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
        passenger_count: int,
    ) -> tuple[KorailSeatSnapshot, ...]:
        key = (
            origin,
            destination,
            departure_date,
            departure_from,
            departure_to,
            passenger_count,
        )
        now = self._monotonic()
        async with self._state_lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.snapshots
            cooldown = await self._cooldown_store.get("korail")
            if cooldown is not None:
                raise _ProviderCooldown(cooldown.reason)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._load(
                        key,
                        origin,
                        destination,
                        departure_date,
                        departure_from,
                        departure_to,
                        passenger_count,
                    )
                )
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def _load(
        self,
        key: tuple[str, str, str, str, str, int],
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
        passenger_count: int,
    ) -> tuple[KorailSeatSnapshot, ...]:
        current_task = asyncio.current_task()
        try:
            async with self._provider_gate:
                try:
                    trains = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._search_sync,
                            origin,
                            destination,
                            departure_date,
                            departure_from,
                            passenger_count,
                        ),
                        timeout=self.timeout_seconds,
                    )
                except NoResultsError:
                    # A valid empty result is cacheable. Without this, every timetable
                    # refresh repeats the same provider call even though identical
                    # positive results are protected by the TTL cache.
                    trains = []
            observed_at = datetime.now(UTC)
            snapshots = tuple(
                self._snapshot(train, observed_at)
                for train in trains
                if departure_from <= normalize_time(train.dep_time) <= departure_to
            )
            async with self._state_lock:
                self._failure_count = 0
                self._cache[key] = _CacheEntry(
                    expires_at=self._monotonic() + self.cache_ttl_seconds,
                    snapshots=snapshots,
                )
            return snapshots
        finally:
            async with self._state_lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    def _search_sync(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        passenger_count: int,
    ) -> list[_KorailTrain]:
        client = self._client_factory()
        return client.search_train(
            origin,
            destination,
            departure_date,
            departure_from,
            passengers=[self._passenger_factory(passenger_count)],
            include_no_seats=True,
            include_waiting_list=True,
        )

    @staticmethod
    def _snapshot(train: _KorailTrain, observed_at: datetime) -> KorailSeatSnapshot:
        standard_status = map_korail_seat_state(train.general_seat, train.reserve_possible_name)
        if train.wait_reserve_flag == 9:
            standard_status = "waitlist_available"
        return KorailSeatSnapshot(
            train_number=normalize_train_number(train.train_no),
            departure_date=normalize_date(train.dep_date),
            departure_time=normalize_time(train.dep_time),
            standard_status=standard_status,
            # ``h_rsv_psb_nm`` is a train-level reservation label and KORAIL's
            # standing/wait-list states concern the general-class flow. Applying
            # that label to the special-class code can turn a sold-out special
            # class into a false positive such as ``standing_plus_seat``.
            first_status=map_korail_seat_state(train.special_seat),
            observed_at=observed_at,
        )

    def _reason_for_error(self, error: KorailError) -> SeatAvailabilityNotObservedReason:
        text = f"{getattr(error, 'code', '')} {getattr(error, 'msg', '')}".casefold()
        if any(marker.casefold() in text for marker in PROTECTION_MARKERS):
            return "provider_access_restricted"
        # NoResultsError is handled separately. A structured rejection without a protection
        # marker is a provider failure, but must not create the six-hour anti-abuse cooldown.
        return "source_unavailable"

    async def _open_cooldown(
        self,
        reason: SeatAvailabilityNotObservedReason,
        error: Exception | None = None,
    ) -> None:
        self._failure_count += 1
        text = str(error or "").casefold()
        if reason == "provider_access_restricted":
            duration = self.protection_cooldown_seconds
        elif "429" in text:
            duration = self.rate_limit_cooldown_seconds
        else:
            duration = min(30 * (2 ** (self._failure_count - 1)), 600)
        await self._cooldown_store.set("korail", reason, duration)

    @staticmethod
    def _mark_not_observed(
        items: list[TimetableItem], reason: SeatAvailabilityNotObservedReason
    ) -> list[TimetableItem]:
        marked: list[TimetableItem] = []
        for item in items:
            seats = []
            for seat in item.seat_classes:
                if seat.status == "unknown" and seat.provenance.kind == "not_observed":
                    seats.append(
                        seat.model_copy(
                            update={
                                "provenance": SeatAvailabilityProvenance(
                                    kind="not_observed", reason=reason
                                ),
                                "actions": [],
                            }
                        )
                    )
                else:
                    seats.append(seat)
            marked.append(item.model_copy(update={"seat_classes": seats}))
        return marked

    @staticmethod
    def _seat_classes(
        snapshot: KorailSeatSnapshot, official_booking_url: _AnyHttpUrl
    ) -> list[SeatClassAvailability]:
        result: list[SeatClassAvailability] = []
        seat_states: tuple[tuple[_SeatClass, SeatAvailabilityStatus], ...] = (
            (_SeatClass.STANDARD, snapshot.standard_status),
            (_SeatClass.FIRST, snapshot.first_status),
        )
        for seat_class, status in seat_states:
            actions: list[SeatAvailabilityAction] = []
            if status in {"available", "limited", "standing_plus_seat", "standing_only"}:
                actions.extend(
                    [
                        SeatAvailabilityAction(kind="official_check", url=official_booking_url),
                        SeatAvailabilityAction(kind="add_to_watch"),
                    ]
                )
            elif status == "waitlist_available":
                actions.extend(
                    [
                        SeatAvailabilityAction(kind="official_waitlist", url=official_booking_url),
                        SeatAvailabilityAction(kind="add_to_watch"),
                    ]
                )
            elif status == "sold_out":
                actions.append(SeatAvailabilityAction(kind="add_to_watch"))
            result.append(
                SeatClassAvailability(
                    seat_class=seat_class,
                    status=status,
                    provenance=SeatAvailabilityProvenance(
                        kind="official_provider",
                        source=SOURCE_NAME,
                        observed_at=snapshot.observed_at,
                    ),
                    actions=actions,
                )
            )
        return result
