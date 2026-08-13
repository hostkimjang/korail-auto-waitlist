from __future__ import annotations

import asyncio
import logging as _logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from typing import cast as _cast
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl as _AnyHttpUrl
from requests import RequestException
from SRT import SRTError  # type: ignore[import-untyped]
from SRT.errors import SRTNetFunnelError  # type: ignore[import-untyped]

from ..domain import Provider, SeatClass
from ..domain import SeatObservationStatus as _SeatObservationStatus
from ..observations.contracts import (
    ObservationErrorCategory,
    SeatObservationRequest,
    SeatObservationResult,
)
from ..provider_call_context import bind_provider_call_id, current_request_id, new_log_id
from ..seat_status_cooldown import CooldownStore, MemoryCooldownStore
from ..timetable_management.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatAvailabilityStatus,
    SeatClassAvailability,
    TimetableItem,
)
from .srt_identity import normalize_srt_date as normalize_srt_date
from .srt_identity import normalize_srt_time as normalize_srt_time
from .srt_identity import (
    normalize_srt_train_number as normalize_srt_train_number,
)
from .srt_netfunnel_logging import LoggingNetFunnelHelper as _LoggingNetFunnelHelper
from .srt_station_roster import (
    SrtStationRosterUnavailable,
    load_srt_station_roster,
)

SOURCE_NAME = "srtrain-2.6.7-accountless"
KOREA = ZoneInfo("Asia/Seoul")
_LOGGER = _logging.getLogger("rail_waitlist.srt_provider_adapter")


class SrtLiveTimetableUnavailable(RuntimeError):
    """The official SRT source could not provide a trustworthy timetable."""


class _SrtTrain(Protocol):
    train_name: str
    train_number: str
    dep_date: str
    dep_time: str
    dep_station_name: str
    arr_date: str
    arr_time: str
    arr_station_name: str
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
    ) -> Sequence[_SrtTrain]: ...


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


_SearchKey = tuple[str, str, str, str, str, int]


@dataclass
class _InflightCall:
    provider_call_id: str
    task: asyncio.Task[tuple[SrtSeatSnapshot, ...]] | None = None
    waiter_deadlines: dict[object, float] = field(default_factory=dict)
    request_ids: set[str] = field(default_factory=set)
    provider_started: bool = False
    first_timeout_at: float | None = None
    late_completion_logged: bool = False


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


class _CallerTimeout(TimeoutError):
    """The local caller's observation budget expired, not the provider operation."""


class _NoActiveWaiters(RuntimeError):
    """A queued read-only provider call lost every live caller before it could start."""


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
    return _AccountlessSrtClient(
        SRT(
            "",
            "",
            auto_login=False,
            verbose=False,
            netfunnel_helper=_LoggingNetFunnelHelper(flow="accountless"),
        )
    )


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
        timeout_seconds: float = 25,
        rate_limit_cooldown_seconds: int = 300,
        protection_cooldown_seconds: int = 60,
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
        self._cache: dict[_SearchKey, _CacheEntry] = {}
        self._inflight: dict[_SearchKey, _InflightCall] = {}
        self._read_only_request_calls: dict[str, set[str]] = {}
        self._state_lock = asyncio.Lock()
        self._provider_gate = asyncio.Semaphore(1)
        self._upstream_tasks: set[asyncio.Task[tuple[SrtSeatSnapshot, ...]]] = set()
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
        except _CallerTimeout:
            return self._observation_error(request, "timeout")
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
                status=_cast(_SeatObservationStatus, status),
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
                status=_cast(_SeatObservationStatus, "error"),
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
        except _CallerTimeout:
            return self._mark_not_observed(items, "source_unavailable")
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

        try:
            roster = load_srt_station_roster()
        except SrtStationRosterUnavailable as error:
            raise SrtLiveTimetableUnavailable("SRT station roster is unavailable") from error
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
        except _CallerTimeout as error:
            raise SrtLiveTimetableUnavailable("SRT timetable source timed out") from error
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
        key: _SearchKey = (
            origin,
            destination,
            departure_date,
            departure_from,
            departure_to,
            passenger_count,
        )
        request_id = current_request_id() or new_log_id()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        waiter = object()
        now = self._monotonic()
        async with self._state_lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.snapshots
            cooldown = await self._cooldown_store.get("srt")
            if cooldown is not None:
                raise _ProviderCooldown(cooldown.reason)
            record = self._inflight.get(key)
            created = record is None
            if record is None:
                record = _InflightCall(provider_call_id=new_log_id())
                task = asyncio.create_task(
                    self._load(
                        key,
                        record,
                        origin,
                        destination,
                        departure_date,
                        departure_from,
                        departure_to,
                    ),
                )
                record.task = task
                self._inflight[key] = record
                self._upstream_tasks.add(task)
                task.add_done_callback(self._release_upstream_task)
            record.waiter_deadlines[waiter] = deadline
            record.request_ids.add(request_id)
            self._read_only_request_calls.setdefault(request_id, set()).add(record.provider_call_id)
            shared_task = record.task
        if shared_task is None:
            raise RuntimeError("SRT inflight provider task is unavailable")

        if created:
            _LOGGER.info(
                "SRT 운영사 조회 작업을 생성합니다 "
                "event=provider_call_created request_id=%s provider_call_id=%s",
                request_id,
                record.provider_call_id,
            )
        else:
            _LOGGER.info(
                "진행 중인 SRT 운영사 조회를 공유합니다 "
                "event=provider_singleflight_joined request_id=%s provider_call_id=%s",
                request_id,
                record.provider_call_id,
            )

        outcome = "failed"
        timeout_scope = asyncio.timeout_at(deadline)
        try:
            try:
                async with timeout_scope:
                    snapshots = await asyncio.shield(shared_task)
            except TimeoutError as error:
                if not timeout_scope.expired():
                    raise
                outcome = "timeout"
                await self._record_caller_timeout(record, request_id=request_id)
                raise _CallerTimeout from error
            except _NoActiveWaiters as error:
                outcome = "timeout"
                await self._record_caller_timeout(record, request_id=request_id)
                raise _CallerTimeout from error
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            outcome = "success"
            return snapshots
        finally:
            await self._release_waiter(key, record, waiter, request_id=request_id)
            _LOGGER.debug(
                "SRT 운영사 조회 대기자를 종료합니다 "
                "event=provider_waiter_completed outcome=%s request_id=%s provider_call_id=%s",
                outcome,
                request_id,
                record.provider_call_id,
            )

    async def _load(
        self,
        key: _SearchKey,
        record: _InflightCall,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
    ) -> tuple[SrtSeatSnapshot, ...]:
        outcome = "failed"
        try:
            trains = await self._search_with_provider_gate(
                key,
                record,
                origin,
                destination,
                departure_date,
                departure_from,
                departure_to,
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
            outcome = "success"
            return snapshots
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            await self._finish_provider_call(key, record, outcome=outcome)

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

    async def read_only_call_pending(self, request_id: str) -> bool:
        """Return whether this request still owns an unfinished provider call."""

        async with self._state_lock:
            return bool(self._read_only_request_calls.get(request_id))

    async def _search_with_provider_gate(
        self,
        key: _SearchKey,
        record: _InflightCall,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
    ) -> list[_SrtTrain]:
        # Keep the provider-wide permit until the real thread returns, even if the
        # observation coroutine has already reported a timeout to its caller.
        async with self._provider_gate:
            loop = asyncio.get_running_loop()
            async with self._state_lock:
                current = self._inflight.get(key)
                has_live_waiter = any(
                    deadline > loop.time() for deadline in record.waiter_deadlines.values()
                )
                if current is not record or not has_live_waiter:
                    raise _NoActiveWaiters
                record.provider_started = True

            with bind_provider_call_id(record.provider_call_id):
                started_at = self._monotonic()
                _LOGGER.info(
                    "SRT 운영사 조회를 시작합니다 event=provider_query_started provider_call_id=%s",
                    record.provider_call_id,
                )
                try:
                    trains = await asyncio.to_thread(
                        self._search_sync,
                        origin,
                        destination,
                        departure_date,
                        departure_from,
                        departure_to,
                    )
                except Exception:
                    _LOGGER.warning(
                        "SRT 운영사 조회를 마쳤습니다 "
                        "event=provider_query_completed outcome=failed "
                        "provider_call_id=%s elapsed_ms=%s",
                        record.provider_call_id,
                        max(0, round((self._monotonic() - started_at) * 1000)),
                    )
                    raise
                _LOGGER.info(
                    "SRT 운영사 조회를 마쳤습니다 "
                    "event=provider_query_completed outcome=success "
                    "provider_call_id=%s train_count=%s elapsed_ms=%s",
                    record.provider_call_id,
                    len(trains),
                    max(0, round((self._monotonic() - started_at) * 1000)),
                )
                return trains

    async def _record_caller_timeout(
        self,
        record: _InflightCall,
        *,
        request_id: str,
    ) -> None:
        timeout_at = self._monotonic()
        late_outcome: str | None = None
        async with self._state_lock:
            if record.first_timeout_at is None:
                record.first_timeout_at = timeout_at
            task = record.task
            provider_started = record.provider_started
            upstream_still_running = task is not None and not task.done()
            if (
                provider_started
                and task is not None
                and task.done()
                and not record.late_completion_logged
            ):
                record.late_completion_logged = True
                late_outcome = self._completed_task_outcome(task)

        phase = "provider_io" if provider_started else "provider_gate_wait"
        _LOGGER.warning(
            "SRT 운영사 조회 작업 제한 시간을 초과했습니다 "
            "event=provider_call_timed_out provider_call_id=%s request_id=%s "
            "phase=%s timeout_seconds=%s upstream_still_running=%s",
            record.provider_call_id,
            request_id,
            phase,
            self.timeout_seconds,
            str(upstream_still_running).lower(),
        )
        if late_outcome is not None:
            self._log_completion_after_timeout(
                provider_call_id=record.provider_call_id,
                outcome=late_outcome,
                timeout_at=record.first_timeout_at or timeout_at,
            )

    async def _release_waiter(
        self,
        key: _SearchKey,
        record: _InflightCall,
        waiter: object,
        *,
        request_id: str,
    ) -> None:
        task_to_cancel: asyncio.Task[tuple[SrtSeatSnapshot, ...]] | None = None
        async with self._state_lock:
            record.waiter_deadlines.pop(waiter, None)
            task = record.task
            if (
                not record.waiter_deadlines
                and not record.provider_started
                and task is not None
                and not task.done()
            ):
                if self._inflight.get(key) is record:
                    self._inflight.pop(key, None)
                self._unlink_read_only_request_calls(record)
                task_to_cancel = task
        if task_to_cancel is not None:
            task_to_cancel.cancel()
            _LOGGER.info(
                "대기자가 없는 SRT 운영사 조회를 시작 전에 취소합니다 "
                "event=provider_call_abandoned_before_start "
                "request_id=%s provider_call_id=%s",
                request_id,
                record.provider_call_id,
            )

    async def _finish_provider_call(
        self,
        key: _SearchKey,
        record: _InflightCall,
        *,
        outcome: str,
    ) -> None:
        timeout_at: float | None = None
        async with self._state_lock:
            if (
                record.provider_started
                and record.first_timeout_at is not None
                and not record.late_completion_logged
            ):
                record.late_completion_logged = True
                timeout_at = record.first_timeout_at
            if self._inflight.get(key) is record:
                self._inflight.pop(key, None)
            self._unlink_read_only_request_calls(record)
        if timeout_at is not None:
            self._log_completion_after_timeout(
                provider_call_id=record.provider_call_id,
                outcome=outcome,
                timeout_at=timeout_at,
            )

    def _unlink_read_only_request_calls(self, record: _InflightCall) -> None:
        for request_id in record.request_ids:
            provider_call_ids = self._read_only_request_calls.get(request_id)
            if provider_call_ids is None:
                continue
            provider_call_ids.discard(record.provider_call_id)
            if not provider_call_ids:
                self._read_only_request_calls.pop(request_id, None)

    def _release_upstream_task(
        self,
        task: asyncio.Task[tuple[SrtSeatSnapshot, ...]],
    ) -> None:
        self._upstream_tasks.discard(task)
        if task.cancelled():
            return
        # Retrieving the exception avoids an unhandled-task warning when the public
        # observation already returned a categorized timeout or provider error.
        task.exception()

    @staticmethod
    def _completed_task_outcome(task: asyncio.Task[tuple[SrtSeatSnapshot, ...]]) -> str:
        if task.cancelled():
            return "cancelled"
        return "success" if task.exception() is None else "failed"

    def _log_completion_after_timeout(
        self,
        *,
        provider_call_id: str,
        outcome: str,
        timeout_at: float,
    ) -> None:
        _LOGGER.info(
            "SRT 운영사 조회가 호출자 시간 초과 뒤 종료되었습니다 "
            "event=provider_call_finished_after_timeout "
            "outcome=%s provider_call_id=%s elapsed_after_timeout_ms=%s",
            outcome,
            provider_call_id,
            max(0, round((self._monotonic() - timeout_at) * 1000)),
        )

    async def _open_cooldown(
        self,
        reason: SeatAvailabilityNotObservedReason,
        error: Exception | None = None,
    ) -> None:
        self._failure_count += 1
        try:
            text = str(error or "").casefold()
        except (TypeError, ValueError):
            # Some third-party exceptions retain a non-string provider error as their
            # message. Classification must remain fail-closed without rendering it.
            text = ""
        if reason == "provider_access_restricted":
            duration = self.protection_cooldown_seconds
        elif "429" in text:
            duration = self.rate_limit_cooldown_seconds
        else:
            duration = min(30 * (2 ** (self._failure_count - 1)), 600)
        await self._cooldown_store.set("srt", reason, duration)
        _LOGGER.warning(
            "SRT 운영사 요청을 일시 중단합니다 "
            "event=provider_cooldown_opened reason=%s duration_seconds=%s",
            reason,
            duration,
        )

    def _search_sync(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        departure_from: str,
        departure_to: str,
    ) -> list[_SrtTrain]:
        client = self._client_factory()
        return list(
            client.search_train(
                origin,
                destination,
                departure_date,
                departure_from,
                time_limit=departure_to,
                available_only=False,
            )
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
            actions = [
                SeatAvailabilityAction(
                    kind="official_check",
                    url=_cast(_AnyHttpUrl, official_booking_url),
                )
            ]
            if status not in {"not_offered", "departed", "out_of_service"}:
                actions.append(SeatAvailabilityAction(kind="add_to_watch"))
            result.append(
                SeatClassAvailability(
                    seat_class=_cast(SeatClass, seat_class),
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
