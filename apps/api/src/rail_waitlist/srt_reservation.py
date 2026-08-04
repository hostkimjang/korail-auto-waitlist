from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Lock
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from requests import RequestException
from SRT import (
    SRT,
    Adult,
    SeatType,
    SRTError,
    SRTLoginError,
    SRTNotLoggedInError,
    SRTResponseError,
)
from SRT.errors import SRTNetFunnelError

from .domain import ReservationOutcome, SeatClass
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .schemas import ReservationRequest, ReservationResult
from .srt_reservation_confirmation import (
    SRT_RESERVATION_LIST_SOURCE,
    SrtReservationListEvidence,
    SrtReservationRecord,
    normalize_srt_reservation_records,
)
from .srt_seat_source import normalize_srt_date, normalize_srt_time, normalize_srt_train_number
from .srt_station_roster import load_srt_station_roster

KOREA = ZoneInfo("Asia/Seoul")
SRT_RESERVATION_SOURCE = "srtrain-2.6.7-reservation"
SRT_RESERVATION_HANDOFF_URL = (
    "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
)


class SrtReservationCredentials(Protocol):
    login_id: str
    password: str
    credential_version: int
    login_method: Literal["membership_number", "email", "phone"]


class _SrtTrain(Protocol):
    train_number: str
    dep_date: str
    dep_time: str
    dep_station_name: str
    arr_station_name: str

    def general_seat_available(self) -> bool: ...

    def special_seat_available(self) -> bool: ...


class _SrtReservation(Protocol):
    train_number: str
    dep_date: str
    dep_time: str
    dep_station_name: str
    arr_station_name: str
    payment_date: str
    payment_time: str
    paid: bool
    seat_count: int | str
    tickets: list["_SrtTicket"]


class _SrtTicket(Protocol):
    seat_type_code: str


class _SrtClient(Protocol):
    is_login: bool

    def search_train(
        self,
        dep: str,
        arr: str,
        date: str | None = None,
        time: str | None = None,
        time_limit: str | None = None,
        available_only: bool = True,
    ) -> list[_SrtTrain]: ...

    def reserve(
        self,
        train: _SrtTrain,
        passengers: list[Adult] | None = None,
        special_seat: SeatType = SeatType.GENERAL_FIRST,
        window_seat: bool | None = None,
    ) -> _SrtReservation: ...

    def get_reservations(self, paid_only: bool = False) -> list[_SrtReservation]: ...


SrtClientFactory = Callable[[str, str], _SrtClient]

_MEMBERSHIP_NUMBER_PATTERN = re.compile(r"\d{10}")
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_PATTERN = re.compile(r"010\d{8}")


class SrtSessionActorState(StrEnum):
    COLD = "cold"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    STALE = "stale"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SrtSessionActorSnapshot:
    state: SrtSessionActorState
    credential_generation: int | None
    created_at_monotonic: float | None
    last_verified_at_monotonic: float | None
    last_used_at_monotonic: float | None
    local_reuse_until_monotonic: float | None
    locally_reusable: bool


def _credential_fingerprint(credentials: SrtReservationCredentials) -> bytes:
    digest = hashlib.sha256(b"rail-waitlist:srt-session-credential:v1\0")
    for value in (
        credentials.login_method,
        credentials.login_id,
        credentials.password,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _canonical_login_id(credentials: SrtReservationCredentials) -> str:
    """Return the identifier shape SRTrain uses to select the official login type.

    SRTrain 2.6.7 infers ``srchDvCd`` from the identifier. In particular, it only
    recognizes a phone number when hyphens are present. Keep that inference explicit at
    this boundary so an unhyphenated phone number is never submitted as a membership ID.
    """

    login_id = credentials.login_id
    if credentials.login_method == "membership_number":
        if _MEMBERSHIP_NUMBER_PATTERN.fullmatch(login_id) is None:
            raise ValueError("SRT membership number must contain exactly 10 digits")
        return login_id
    if credentials.login_method == "email":
        normalized = login_id.strip()
        if _EMAIL_PATTERN.fullmatch(normalized) is None:
            raise ValueError("SRT email login identifier is invalid")
        return normalized
    if credentials.login_method == "phone":
        digits = login_id.strip()
        if _PHONE_PATTERN.fullmatch(digits) is None:
            raise ValueError("SRT phone login identifier must be an 11-digit 010 number")
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    raise ValueError("unsupported SRT login method")


def _default_client_factory(login_id: str, password: str) -> _SrtClient:
    return SRT(login_id, password, auto_login=True, verbose=False)


def verify_srt_credentials_once(
    credentials: SrtReservationCredentials,
    client_factory: SrtClientFactory = _default_client_factory,
) -> bool:
    """Attempt one SRT login without searching or reserving a train."""
    login_id = _canonical_login_id(credentials)
    return bool(client_factory(login_id, credentials.password).is_login)


def _payment_deadline(reservation: _SrtReservation) -> datetime | None:
    raw_date = "".join(character for character in reservation.payment_date if character.isdigit())
    raw_time = "".join(character for character in reservation.payment_time if character.isdigit())
    if len(raw_date) != 8 or len(raw_time) < 4:
        return None
    try:
        return datetime.strptime(f"{raw_date}{raw_time[:4]}", "%Y%m%d%H%M").replace(tzinfo=KOREA)
    except ValueError:
        return None


def _matches_request(item: _SrtTrain | _SrtReservation, request: ReservationRequest) -> bool:
    departure = request.departure_at.astimezone(KOREA)
    return (
        normalize_srt_train_number(item.train_number)
        == normalize_srt_train_number(request.train_number)
        and normalize_srt_date(item.dep_date) == departure.strftime("%Y%m%d")
        and normalize_srt_time(item.dep_time) == departure.strftime("%H%M%S")
        and item.dep_station_name == request.origin
        and item.arr_station_name == request.destination
    )


def _reservation_seat_class(reservation: _SrtReservation) -> SeatClass | None:
    codes = {str(ticket.seat_type_code).strip() for ticket in reservation.tickets}
    if codes == {"1"}:
        return SeatClass.STANDARD
    if codes == {"2"}:
        return SeatClass.FIRST
    return None


def _reservation_passenger_count(reservation: _SrtReservation) -> int | None:
    try:
        count = int(reservation.seat_count)
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


@dataclass
class _ActiveSrtSession:
    client: _SrtClient
    credential_version: int
    credential_fingerprint: bytes = field(repr=False)
    created_at: float
    last_verified_at: float
    last_used_at: float


class SrtReservationExecutor:
    """Keep one authenticated SRT session per worker process and reserve at most once.

    Search may reinitialize an expired session once because it has no booking side effect.
    The reserve call itself is never replayed: any ambiguous result is returned as UNKNOWN
    so the persisted reservation-attempt fence requires manual confirmation.
    """

    def __init__(
        self,
        client_factory: SrtClientFactory = _default_client_factory,
        *,
        session_reuse_ttl_seconds: float = 300,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if session_reuse_ttl_seconds <= 0:
            raise ValueError("session_reuse_ttl_seconds must be positive")
        self._client_factory = client_factory
        self._session_reuse_ttl_seconds = session_reuse_ttl_seconds
        self._monotonic = monotonic
        self._active_session: _ActiveSrtSession | None = None
        self._session_actor_state = SrtSessionActorState.COLD
        self._session_actor_generation: int | None = None
        self._session_actor_created_at: float | None = None
        self._session_actor_last_verified_at: float | None = None
        self._session_actor_last_used_at: float | None = None
        self._lock = Lock()

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: SrtReservationCredentials,
    ) -> ReservationResult:
        return await asyncio.to_thread(self._reserve_sync, request, credentials)

    async def verify_credentials(self, credentials: SrtReservationCredentials) -> bool:
        """Prove supplied credentials in one fresh login without search or reservation."""

        try:
            return await asyncio.to_thread(self._verify_sync, credentials, True)
        except (SRTLoginError, SRTNotLoggedInError):
            return False

    async def prewarm_credentials(self, credentials: SrtReservationCredentials) -> bool:
        """Ensure a locally reusable session, logging in at most once when it is absent."""

        try:
            return await asyncio.to_thread(self._verify_sync, credentials, False)
        except (SRTLoginError, SRTNotLoggedInError):
            return False

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
        credentials: SrtReservationCredentials,
    ) -> ReservationConfirmationResult:
        """Read the current unpaid reservation list without reserving, cancelling, or paying."""

        return await asyncio.to_thread(self._confirm_reservation_sync, target, credentials)

    def session_snapshot(self) -> SrtSessionActorSnapshot:
        """Return process-local actor telemetry without credential-derived material."""

        active = self._active_session
        state = self._session_actor_state
        local_reuse_until = None
        locally_reusable = False
        if active is not None:
            local_reuse_until = active.last_used_at + self._session_reuse_ttl_seconds
            locally_reusable = (
                self._monotonic() < local_reuse_until
                and active.client.is_login
                and state is SrtSessionActorState.READY
            )
            if state is SrtSessionActorState.READY and not locally_reusable:
                state = SrtSessionActorState.STALE
        return SrtSessionActorSnapshot(
            state=state,
            credential_generation=self._session_actor_generation,
            created_at_monotonic=self._session_actor_created_at,
            last_verified_at_monotonic=self._session_actor_last_verified_at,
            last_used_at_monotonic=self._session_actor_last_used_at,
            local_reuse_until_monotonic=local_reuse_until,
            locally_reusable=locally_reusable,
        )

    def _verify_sync(
        self,
        credentials: SrtReservationCredentials,
        force_fresh: bool,
    ) -> bool:
        with self._lock:
            if force_fresh and self._active_session is not None:
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.STALE
            self._client_for(credentials)
            return True

    def _new_client(self, credentials: SrtReservationCredentials) -> _SrtClient:
        now = self._monotonic()
        self._active_session = None
        self._session_actor_state = SrtSessionActorState.AUTHENTICATING
        self._session_actor_generation = credentials.credential_version
        self._session_actor_created_at = now
        self._session_actor_last_used_at = now
        try:
            login_id = _canonical_login_id(credentials)
            client = self._client_factory(login_id, credentials.password)
            if not client.is_login:
                raise SRTLoginError("login failed")
        except (SRTLoginError, SRTNotLoggedInError):
            self._session_actor_state = SrtSessionActorState.AUTH_REQUIRED
            raise
        except SRTNetFunnelError:
            self._session_actor_state = SrtSessionActorState.BLOCKED
            raise
        except Exception:
            self._session_actor_state = SrtSessionActorState.STALE
            raise
        verified_at = self._monotonic()
        self._active_session = _ActiveSrtSession(
            client=client,
            credential_version=credentials.credential_version,
            credential_fingerprint=_credential_fingerprint(credentials),
            created_at=now,
            last_verified_at=verified_at,
            last_used_at=verified_at,
        )
        self._session_actor_last_verified_at = verified_at
        self._session_actor_last_used_at = verified_at
        self._session_actor_state = SrtSessionActorState.READY
        return client

    def _client_for(self, credentials: SrtReservationCredentials) -> _SrtClient:
        active = self._active_session
        now = self._monotonic()
        if (
            active is None
            or active.credential_version != credentials.credential_version
            or active.credential_fingerprint != _credential_fingerprint(credentials)
            or now - active.last_used_at >= self._session_reuse_ttl_seconds
            or not active.client.is_login
            or self._session_actor_state is not SrtSessionActorState.READY
        ):
            if active is not None:
                self._active_session = None
                self._session_actor_state = (
                    SrtSessionActorState.AUTH_REQUIRED
                    if not active.client.is_login
                    else SrtSessionActorState.STALE
                )
            return self._new_client(credentials)
        active.last_used_at = now
        self._session_actor_last_used_at = now
        return active.client

    def _search_exact(
        self,
        client: _SrtClient,
        request: ReservationRequest,
    ) -> list[_SrtTrain]:
        roster = load_srt_station_roster()
        origin = roster.provider_name(request.origin)
        destination = roster.provider_name(request.destination)
        if origin is None or destination is None or origin == destination:
            return []
        departure = request.departure_at.astimezone(KOREA)
        trains = client.search_train(
            origin,
            destination,
            departure.strftime("%Y%m%d"),
            "000000",
            "235959",
            available_only=False,
        )
        return [train for train in trains if _matches_request(train, request)]

    def _confirm_reservation_sync(
        self,
        target: ReservationConfirmationTarget,
        credentials: SrtReservationCredentials,
    ) -> ReservationConfirmationResult:
        observed_at = datetime.now(KOREA)
        with self._lock:
            try:
                client = self._client_for(credentials)
                records = tuple(
                    SrtReservationRecord(
                        train_number=item.train_number,
                        departure_date=item.dep_date,
                        departure_time=item.dep_time,
                        origin=item.dep_station_name,
                        destination=item.arr_station_name,
                        payment_date=item.payment_date,
                        payment_time=item.payment_time,
                        paid=bool(item.paid),
                        seat_class=_reservation_seat_class(item),
                        passenger_count=_reservation_passenger_count(item),
                    )
                    for item in client.get_reservations(paid_only=False)
                )
                evidence = SrtReservationListEvidence(
                    observed_at=observed_at,
                    credential_version=credentials.credential_version,
                    records=records,
                )
            except (SRTLoginError, SRTNotLoggedInError):
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.AUTH_REQUIRED
                evidence = SrtReservationListEvidence(
                    observed_at=observed_at,
                    credential_version=credentials.credential_version,
                    auth_required=True,
                )
            except SRTNetFunnelError:
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.BLOCKED
                evidence = SrtReservationListEvidence(
                    observed_at=observed_at,
                    credential_version=credentials.credential_version,
                    provider_blocked=True,
                )
            except (RequestException, SRTError, TypeError, ValueError):
                self._session_actor_state = SrtSessionActorState.STALE
                return ReservationConfirmationResult(
                    provider=target.provider,
                    outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                    source=SRT_RESERVATION_LIST_SOURCE,
                    observed_at=observed_at,
                )
        return normalize_srt_reservation_records(target, evidence)

    def _reserve_sync(
        self,
        request: ReservationRequest,
        credentials: SrtReservationCredentials,
    ) -> ReservationResult:
        observed_at = datetime.now(KOREA)
        with self._lock:
            try:
                client = self._client_for(credentials)
                try:
                    matches = self._search_exact(client, request)
                except (SRTLoginError, SRTNotLoggedInError):
                    # Search is read-only. Reinitialize once, then continue without a loop.
                    self._active_session = None
                    self._session_actor_state = SrtSessionActorState.AUTH_REQUIRED
                    client = self._new_client(credentials)
                    matches = self._search_exact(client, request)
            except (SRTLoginError, SRTNotLoggedInError):
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.AUTH_REQUIRED
                return ReservationResult(
                    outcome=ReservationOutcome.AUTH_REQUIRED,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            except SRTNetFunnelError:
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.BLOCKED
                return ReservationResult(
                    outcome=ReservationOutcome.PROVIDER_BLOCKED,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            except (RequestException, SRTError, ValueError):
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.STALE
                return ReservationResult(
                    outcome=ReservationOutcome.FAILED,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )

            if len(matches) != 1:
                return ReservationResult(
                    outcome=(
                        ReservationOutcome.NOT_AVAILABLE
                        if not matches
                        else ReservationOutcome.UNKNOWN
                    ),
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )

            train = matches[0]
            if request.seat_class == SeatClass.STANDARD:
                available = train.general_seat_available()
                seat_type = SeatType.GENERAL_ONLY
            elif request.seat_class == SeatClass.FIRST:
                available = train.special_seat_available()
                seat_type = SeatType.SPECIAL_ONLY
            else:
                available = False
                seat_type = SeatType.GENERAL_ONLY
            if not available:
                return ReservationResult(
                    outcome=ReservationOutcome.NOT_AVAILABLE,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )

            try:
                reservation = client.reserve(
                    train,
                    passengers=[Adult(request.passenger_count)],
                    special_seat=seat_type,
                )
            except (SRTLoginError, SRTNotLoggedInError):
                # A failure after the single reserve call is never retried automatically.
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.AUTH_REQUIRED
                return ReservationResult(
                    outcome=ReservationOutcome.UNKNOWN,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            except SRTNetFunnelError:
                self._active_session = None
                self._session_actor_state = SrtSessionActorState.BLOCKED
                return ReservationResult(
                    outcome=ReservationOutcome.PROVIDER_BLOCKED,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            except SRTResponseError:
                return ReservationResult(
                    outcome=ReservationOutcome.UNKNOWN,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            except (RequestException, SRTError, ValueError):
                return ReservationResult(
                    outcome=ReservationOutcome.UNKNOWN,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )

            if (
                reservation.paid
                or not _matches_request(reservation, request)
                or _reservation_seat_class(reservation) is not request.seat_class
                or _reservation_passenger_count(reservation) != request.passenger_count
            ):
                return ReservationResult(
                    outcome=ReservationOutcome.UNKNOWN,
                    source=SRT_RESERVATION_SOURCE,
                    observed_at=observed_at,
                )
            deadline = _payment_deadline(reservation)
            if deadline is not None and deadline <= observed_at:
                deadline = None
            return ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source=SRT_RESERVATION_SOURCE,
                observed_at=observed_at,
                payment_deadline=deadline,
                official_handoff_url=SRT_RESERVATION_HANDOFF_URL,
            )


_default_executor: SrtReservationExecutor | None = None


def default_srt_reservation_executor() -> SrtReservationExecutor:
    global _default_executor
    if _default_executor is None:
        _default_executor = SrtReservationExecutor()
    return _default_executor
