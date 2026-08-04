from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from requests import RequestException
from SRT import SRTError
from SRT.errors import SRTNetFunnelError

from .domain import Provider, SeatClass
from .schemas import (
    ObservationErrorCategory,
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatAvailabilityStatus,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    TimetableItem,
)
from .seat_status_cooldown import CooldownStore, MemoryCooldownStore
from .srt_station_roster import load_srt_station_roster

SOURCE_NAME = "srtrain-2.6.7-accountless"
KOREA = ZoneInfo("Asia/Seoul")


class SrtLiveTimetableUnavailable(RuntimeError):
    """The official SRT source could not provide a trustworthy timetable."""


class _SrtTrain(Protocol):
    train_name: str
    train_number: str
    dep_date: str
    dep_time: str
    dep_station_name: str
    dep_station_code: str
    arr_date: str
    arr_time: str
    arr_station_name: str
    arr_station_code: str
    general_seat_state: str
    special_seat_state: str
    reserve_wait_possible_code: str


class _SrtClient(Protocol):
    def search_train(
        self,
        dep: str,
        arr: str,
        date: str | None = None,
        time: str | None = None,
        time_limit: str | None = None,
        available_only: bool = True,
    ) -> list[_SrtTrain]: ...


class _SrTrainCodeAwareClient(Protocol):
    def _search_train(
        self,
        dep: str,
        arr: str,
        date: str | None = None,
        time: str | None = None,
        time_limit: str | None = None,
        arr_code: str | None = None,
        dep_code: str | None = None,
        available_only: bool = True,
        use_netfunnel_cache: bool = True,
    ) -> list[_SrtTrain]: ...


SrtClientFactory = Callable[[], _SrtClient]


@dataclass(frozen=True)
class SrtSeatSnapshot:
    train_number: str
    official_train_number: str
    train_type: str | None
    origin: str | None
    destination: str | None
    departure_date: str
    departure_time: str
    arrival_date: str | None
    arrival_time: str | None
    standard_status: SeatAvailabilityStatus
    first_status: SeatAvailabilityStatus
    observed_at: datetime
    delay_minutes: int | None
    adult_fare: int | None


@dataclass(frozen=True)
class SrtOfficialTimetableTrain:
    train_number: str
    train_type: str
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    standard_status: SeatAvailabilityStatus
    first_status: SeatAvailabilityStatus
    observed_at: datetime
    delay_minutes: int | None
    adult_fare: int | None
    source: str = SOURCE_NAME


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    snapshots: tuple[SrtSeatSnapshot, ...]


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


class _AccountlessSrtClient:
    """Use SRTrain's normal request flow with the current official station codes."""

    def __init__(self, client: _SrTrainCodeAwareClient) -> None:
        self._client = client

    def search_train(
        self,
        dep: str,
        arr: str,
        date: str | None = None,
        time: str | None = None,
        time_limit: str | None = None,
        available_only: bool = True,
    ) -> list[_SrtTrain]:
        roster = load_srt_station_roster()
        dep_code = roster.station_code(dep)
        arr_code = roster.station_code(arr)
        if dep_code is None or arr_code is None or dep_code == arr_code:
            raise ValueError("unsupported SRT route")
        return self._client._search_train(
            dep=dep,
            arr=arr,
            date=date,
            time=time,
            time_limit=time_limit,
            arr_code=arr_code,
            dep_code=dep_code,
            available_only=available_only,
            use_netfunnel_cache=True,
        )


def _default_client_factory() -> _SrtClient:
    from SRT import SRT

    # Search is intentionally accountless. Constructing a fresh client per upstream query
    # keeps NetFunnel lifecycle inside SRTrain's normal issue/wait/complete implementation.
    return _AccountlessSrtClient(SRT("", "", auto_login=False, verbose=False))


def normalize_srt_train_number(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.lstrip("0") or "0"


def normalize_srt_date(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(8)[-8:]


def normalize_srt_time(value: object) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) <= 4:
        return f"{digits.zfill(4)}00"
    return digits.zfill(6)[-6:]


def map_srt_seat_state(value: object) -> SeatAvailabilityStatus:
    normalized = "".join(str(value).split())
    if "예약가능" in normalized:
        return "available"
    if "매진" in normalized:
        return "sold_out"
    if normalized in {"-", "없음", "해당없음", "미운영", "운영안함", "좌석없음"}:
        return "not_offered"
    return "unknown"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_date(value: object) -> str | None:
    normalized = _optional_text(value)
    if normalized is None or not normalized.isdigit():
        return None
    return normalized if len(normalized) == 8 else None


def _optional_time(value: object) -> str | None:
    normalized = _optional_text(value)
    if normalized is None or not normalized.isdigit():
        return None
    if len(normalized) == 4:
        return f"{normalized}00"
    return normalized if len(normalized) == 6 else None


def _optional_nonnegative_int(value: object, maximum: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    normalized = str(value).replace(",", "").strip()
    if not normalized.isdigit():
        return None
    result = int(normalized)
    if maximum is not None and result > maximum:
        return None
    return result


def _official_datetime(date_value: str, time_value: str) -> datetime:
    return datetime.strptime(f"{date_value}{time_value}", "%Y%m%d%H%M%S").replace(tzinfo=KOREA)


def _snapshot_station_name(
    train: _SrtTrain,
    *,
    name_attribute: str,
    code_attribute: str,
    expected_name: str,
) -> str | None:
    raw_name = _optional_text(getattr(train, name_attribute, None))
    if raw_name is not None and not raw_name.startswith("알 수 없는 역 코드"):
        return raw_name
    roster = load_srt_station_roster()
    expected_code = roster.station_code(expected_name)
    raw_code = _optional_text(getattr(train, code_attribute, None))
    if expected_code is not None and raw_code == expected_code:
        return expected_name
    return raw_name


class SrtLiveSeatSource:
    def __init__(
        self,
        *,
        enabled: bool,
        cache_ttl_seconds: int,
        client_factory: SrtClientFactory = _default_client_factory,
        monotonic: Callable[[], float] = time.monotonic,
        timeout_seconds: float = 8,
        rate_limit_cooldown_seconds: int = 1800,
        protection_cooldown_seconds: int = 300,
        cooldown_store: CooldownStore | None = None,
        source_name: str = SOURCE_NAME,
    ) -> None:
        self.enabled = enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self._client_factory = client_factory
        self._monotonic = monotonic
        self.timeout_seconds = timeout_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.protection_cooldown_seconds = protection_cooldown_seconds
        self._cache: dict[tuple[str, str, str, str, str, int], _CacheEntry] = {}
        self._inflight: dict[
            tuple[str, str, str, str, str, int], asyncio.Task[tuple[SrtSeatSnapshot, ...]]
        ] = {}
        self._state_lock = asyncio.Lock()
        self._provider_gate = asyncio.Semaphore(1)
        self._upstream_tasks: set[asyncio.Task[list[_SrtTrain]]] = set()
        self._cooldown_store = cooldown_store or MemoryCooldownStore(monotonic)
        self._failure_count = 0
        self.source_name = source_name

    async def observation_deferred_until(self) -> datetime | None:
        """Expose the shared source hold so workers can defer without writing errors."""
        if not self.enabled:
            return None
        cooldown = await self._cooldown_store.get("srt")
        if cooldown is None:
            return None
        return datetime.now(UTC) + timedelta(seconds=max(1, cooldown.retry_after_seconds))

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        """Observe one SRT candidate without widening the worker request contract.

        Worker observations intentionally reuse the same accountless batch search used by
        timetable overlay. Exact candidate matching happens after that single shared query;
        unsupported or ambiguous requests fail closed without estimating availability.
        """
        if not self.enabled:
            return self._observation_error(request, "provider_unavailable")
        if request.provider != Provider.SRT or request.passenger_count != 1:
            return self._observation_error(request, "provider_unavailable")
        if request.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}:
            return self._observation_error(request, "provider_unavailable")

        local_departure = request.departure_at.astimezone(KOREA)
        try:
            roster = load_srt_station_roster()
            provider_origin = roster.provider_name(origin)
            provider_destination = roster.provider_name(destination)
            if (
                provider_origin is None
                or provider_destination is None
                or provider_origin == provider_destination
            ):
                return self._observation_error(request, "provider_unavailable")
            snapshots = await self._search(
                provider_origin,
                provider_destination,
                local_departure.strftime("%Y%m%d"),
                # A worker cycle can contain several selected trains on the same route.
                # Query the service day once so every exact candidate lookup shares the
                # same singleflight/cache entry instead of issuing one request per train.
                "000000",
                "235959",
                request.passenger_count,
            )
        except _ProviderCooldown:
            return self._observation_error(request, "provider_unavailable")
        except SRTNetFunnelError as error:
            await self._open_cooldown("provider_access_restricted", error)
            return self._observation_error(request, "provider_unavailable")
        except TimeoutError as error:
            await self._open_cooldown("source_unavailable", error)
            return self._observation_error(request, "timeout")
        except ValueError:
            return self._observation_error(request, "provider_unavailable")
        except (
            AttributeError,
            KeyError,
            OSError,
            RequestException,
            RuntimeError,
            SRTError,
            TypeError,
        ) as error:
            await self._open_cooldown("source_unavailable", error)
            return self._observation_error(request, "provider_unavailable")

        identity = (
            normalize_srt_train_number(request.train_number),
            local_departure.strftime("%Y%m%d"),
            local_departure.strftime("%H%M%S"),
        )
        snapshot = next(
            (
                item
                for item in snapshots
                if (item.train_number, item.departure_date, item.departure_time) == identity
            ),
            None,
        )
        if snapshot is None:
            return self._observation_error(request, "provider_unavailable")

        status = (
            snapshot.standard_status
            if request.seat_class == SeatClass.STANDARD
            else snapshot.first_status
        )
        if status == "unknown":
            return self._observation_error(request, "schema_mismatch")
        freshness_seconds = max(0, min(self.cache_ttl_seconds, 30))
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=status,
                source=self.source_name,
                observed_at=snapshot.observed_at,
                fresh_until=snapshot.observed_at + timedelta(seconds=freshness_seconds),
            )
        ]

    def _observation_error(
        self,
        request: SeatObservationRequest,
        error_category: ObservationErrorCategory,
    ) -> list[SeatObservationResult]:
        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status="error",
                source=self.source_name,
                observed_at=observed_at,
                fresh_until=observed_at,
                error_category=error_category,
            )
        ]

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
        if not self.enabled:
            return self._mark_not_observed(items, "source_not_configured")
        # SRTrain 2.6.7 accountless schedule search fixes psgNum to one internally.
        # A positive status must therefore not be presented as multi-passenger evidence.
        if passenger_count != 1:
            return self._mark_not_observed(items, "passenger_count_not_supported")

        local_from = departure_from.astimezone(KOREA)
        local_to = departure_to.astimezone(KOREA)
        if local_from.date() != local_to.date():
            return self._mark_not_observed(items, "source_unavailable")

        try:
            roster = load_srt_station_roster()
            provider_origin = roster.provider_name(origin)
            provider_destination = roster.provider_name(destination)
            if provider_origin is None or provider_destination is None:
                return self._mark_not_observed(items, "unsupported_route")
            snapshots = await self._search(
                provider_origin,
                provider_destination,
                local_from.strftime("%Y%m%d"),
                local_from.strftime("%H%M%S"),
                local_to.strftime("%H%M%S"),
                passenger_count,
            )
        except _ProviderCooldown as error:
            return self._mark_not_observed(items, error.reason)
        except SRTNetFunnelError as error:
            await self._open_cooldown("provider_access_restricted", error)
            return self._mark_not_observed(items, "provider_access_restricted")
        except ValueError:
            # SRTrain rejects station names outside its maintained intercity catalog
            # with ValueError before issuing a provider request.
            return self._mark_not_observed(items, "unsupported_route")
        except (
            TimeoutError,
            AttributeError,
            KeyError,
            OSError,
            RequestException,
            RuntimeError,
            SRTError,
            TypeError,
        ) as error:
            # Seat status is optional enrichment. TAGO timetable results remain authoritative
            # and usable when the accountless source or normal NetFunnel flow is unavailable.
            await self._open_cooldown("source_unavailable", error)
            return self._mark_not_observed(items, "source_unavailable")

        by_identity = {
            (snapshot.train_number, snapshot.departure_date, snapshot.departure_time): snapshot
            for snapshot in snapshots
        }
        overlaid: list[TimetableItem] = []
        for item in items:
            local_departure = item.departure_at.astimezone(KOREA)
            identity = (
                normalize_srt_train_number(item.train_number),
                local_departure.strftime("%Y%m%d"),
                local_departure.strftime("%H%M%S"),
            )
            snapshot = by_identity.get(identity)
            if snapshot is None:
                overlaid.extend(self._mark_not_observed([item], "no_exact_match"))
                continue
            overlaid.append(
                item.model_copy(
                    update={
                        "seat_classes": self._seat_classes(snapshot, str(item.official_booking_url))
                    }
                )
            )
        return overlaid

    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[SrtOfficialTimetableTrain]:
        """Return official accountless SRT timetable rows for one exact KST window."""
        if not self.enabled:
            raise SrtLiveTimetableUnavailable("SRT timetable source is disabled")
        if passenger_count != 1:
            raise ValueError("SRT accountless timetable search supports one passenger")
        for value in (departure_from, departure_to):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("SRT timetable search requires timezone-aware bounds")

        local_from = departure_from.astimezone(KOREA)
        local_to = departure_to.astimezone(KOREA)
        if local_from.date() != local_to.date() or local_to <= local_from:
            raise ValueError("SRT timetable search requires one valid KST service-day window")

        roster = load_srt_station_roster()
        provider_origin = roster.provider_name(origin)
        provider_destination = roster.provider_name(destination)
        if (
            provider_origin is None
            or provider_destination is None
            or provider_origin == provider_destination
        ):
            raise ValueError("unsupported SRT route")

        try:
            snapshots = await self._search(
                provider_origin,
                provider_destination,
                local_from.strftime("%Y%m%d"),
                local_from.strftime("%H%M%S"),
                local_to.strftime("%H%M%S"),
                passenger_count,
            )
        except SRTNetFunnelError as error:
            await self._open_cooldown("provider_access_restricted", error)
            raise SrtLiveTimetableUnavailable("SRT provider access is restricted") from error
        except TimeoutError as error:
            await self._open_cooldown("source_unavailable", error)
            raise SrtLiveTimetableUnavailable("SRT timetable source timed out") from error
        except _ProviderCooldown as error:
            raise SrtLiveTimetableUnavailable("SRT timetable source is cooling down") from error
        except (
            AttributeError,
            KeyError,
            OSError,
            RequestException,
            RuntimeError,
            SRTError,
            TypeError,
        ) as error:
            await self._open_cooldown("source_unavailable", error)
            raise SrtLiveTimetableUnavailable("SRT timetable source is unavailable") from error

        result: list[SrtOfficialTimetableTrain] = []
        for snapshot in snapshots:
            if (
                snapshot.train_type is None
                or snapshot.origin != provider_origin
                or snapshot.destination != provider_destination
                or snapshot.arrival_date is None
                or snapshot.arrival_time is None
            ):
                continue
            try:
                departure_at = _official_datetime(snapshot.departure_date, snapshot.departure_time)
                arrival_at = _official_datetime(snapshot.arrival_date, snapshot.arrival_time)
                if not local_from <= departure_at <= local_to:
                    continue
                result.append(
                    SrtOfficialTimetableTrain(
                        train_number=snapshot.official_train_number,
                        train_type=snapshot.train_type,
                        origin=snapshot.origin,
                        destination=snapshot.destination,
                        departure_at=departure_at,
                        arrival_at=arrival_at,
                        standard_status=snapshot.standard_status,
                        first_status=snapshot.first_status,
                        observed_at=snapshot.observed_at,
                        delay_minutes=snapshot.delay_minutes,
                        adult_fare=snapshot.adult_fare,
                    )
                )
            except ValueError:
                # One malformed official row must not erase valid rows in the same response.
                continue
        return result

    @staticmethod
    def _mark_not_observed(
        items: list[TimetableItem], reason: SeatAvailabilityNotObservedReason
    ) -> list[TimetableItem]:
        marked: list[TimetableItem] = []
        for item in items:
            seat_classes = [
                seat.model_copy(
                    update={
                        "provenance": SeatAvailabilityProvenance(
                            kind="not_observed",
                            reason=reason,
                        )
                    }
                )
                if seat.status == "unknown" and seat.provenance.kind == "not_observed"
                else seat
                for seat in item.seat_classes
            ]
            marked.append(item.model_copy(update={"seat_classes": seat_classes}))
        return marked

    async def _search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
        passenger_count: int,
    ) -> tuple[SrtSeatSnapshot, ...]:
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
            cooldown = await self._cooldown_store.get("srt")
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
    ) -> tuple[SrtSeatSnapshot, ...]:
        current_task = asyncio.current_task()
        try:
            upstream_task = asyncio.create_task(
                self._search_with_provider_gate(
                    origin,
                    destination,
                    departure_date,
                    departure_from,
                    departure_to,
                )
            )
            self._upstream_tasks.add(upstream_task)
            upstream_task.add_done_callback(self._release_upstream_task)
            trains = await asyncio.wait_for(
                asyncio.shield(upstream_task),
                timeout=self.timeout_seconds,
            )
            observed_at = datetime.now(UTC)
            snapshots_list: list[SrtSeatSnapshot] = []
            for train in trains:
                try:
                    snapshots_list.append(
                        self._snapshot(
                            train,
                            observed_at,
                            expected_origin=origin,
                            expected_destination=destination,
                        )
                    )
                except (AttributeError, TypeError, ValueError):
                    # One malformed train must not erase valid observations from the same batch.
                    continue
            snapshots = tuple(snapshots_list)
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

    async def drain_pending_calls(self) -> None:
        """Wait until every started synchronous provider call has actually returned.

        ``asyncio.to_thread`` cannot stop its worker thread when an asyncio timeout fires.
        The task therefore remains owned by this source until the synchronous call exits;
        callers must drain it before releasing an external execution lease or closing Redis.
        """
        while self._upstream_tasks:
            pending = tuple(self._upstream_tasks)
            await asyncio.gather(
                *(asyncio.shield(task) for task in pending),
                return_exceptions=True,
            )

    async def _search_with_provider_gate(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
    ) -> list[_SrtTrain]:
        # Keep the provider-wide permit until the real thread returns, even if the
        # observation coroutine has already reported a timeout to its caller.
        async with self._provider_gate:
            return await asyncio.to_thread(
                self._search_sync,
                origin,
                destination,
                departure_date,
                departure_from,
                departure_to,
            )

    def _release_upstream_task(self, task: asyncio.Task[list[_SrtTrain]]) -> None:
        self._upstream_tasks.discard(task)
        if task.cancelled():
            return
        # Retrieving the exception avoids an unhandled-task warning when the public
        # observation already returned a categorized timeout or provider error.
        task.exception()

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
        await self._cooldown_store.set("srt", reason, duration)

    def _search_sync(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
    ) -> list[_SrtTrain]:
        client = self._client_factory()
        return client.search_train(
            origin,
            destination,
            departure_date,
            departure_from,
            time_limit=departure_to,
            available_only=False,
        )

    @staticmethod
    def _snapshot(
        train: _SrtTrain,
        observed_at: datetime,
        *,
        expected_origin: str,
        expected_destination: str,
    ) -> SrtSeatSnapshot:
        standard_status = map_srt_seat_state(train.general_seat_state)
        if "9" in str(train.reserve_wait_possible_code):
            standard_status = "waitlist_available"
        return SrtSeatSnapshot(
            train_number=normalize_srt_train_number(train.train_number),
            official_train_number=str(train.train_number).strip(),
            train_type=_optional_text(getattr(train, "train_name", None)),
            # SRTrain 2.6.7 labels newly added cross-operation station codes as
            # unknown. Recover a name only when the official row code matches the
            # exact code used by this request; malformed or mismatched rows remain
            # incomplete and are discarded by the strict caller.
            origin=_snapshot_station_name(
                train,
                name_attribute="dep_station_name",
                code_attribute="dep_station_code",
                expected_name=expected_origin,
            ),
            destination=_snapshot_station_name(
                train,
                name_attribute="arr_station_name",
                code_attribute="arr_station_code",
                expected_name=expected_destination,
            ),
            departure_date=normalize_srt_date(train.dep_date),
            departure_time=normalize_srt_time(train.dep_time),
            arrival_date=_optional_date(getattr(train, "arr_date", None)),
            arrival_time=_optional_time(getattr(train, "arr_time", None)),
            standard_status=standard_status,
            first_status=map_srt_seat_state(train.special_seat_state),
            observed_at=observed_at,
            delay_minutes=_optional_nonnegative_int(getattr(train, "delay_minutes", None), 999),
            adult_fare=_optional_nonnegative_int(getattr(train, "adult_fare", None)),
        )

    def _seat_classes(
        self, snapshot: SrtSeatSnapshot, official_booking_url: str
    ) -> list[SeatClassAvailability]:
        result: list[SeatClassAvailability] = []
        for seat_class, status in (
            ("standard", snapshot.standard_status),
            ("first", snapshot.first_status),
        ):
            actions = [SeatAvailabilityAction(kind="official_check", url=official_booking_url)]
            if status not in {"not_offered", "departed", "out_of_service"}:
                actions.append(SeatAvailabilityAction(kind="add_to_watch"))
            result.append(
                SeatClassAvailability(
                    seat_class=seat_class,
                    status=status,
                    provenance=SeatAvailabilityProvenance(
                        kind="official_provider",
                        source=self.source_name,
                        observed_at=snapshot.observed_at,
                    ),
                    actions=actions,
                )
            )
        return result
