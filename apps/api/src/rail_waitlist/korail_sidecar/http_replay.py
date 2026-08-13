from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Self
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from .browser_contracts import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
    ProtectionTrigger,
    SeatStatus,
)
from .browser_protection import protection_trigger_from_replay_text
from .browser_service_availability import (
    ProviderUnavailableTrigger,
    decode_provider_page_text,
    provider_unavailable_trigger_from_page,
)
from .search_result_policy import parse_official_train_type, parse_unambiguous_adult_fare

OFFICIAL_HOST = "www.korail.com"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 20
KST = ZoneInfo("Asia/Seoul")

# HTTPX normally logs the full request URL at INFO. The official business path is
# ephemeral replay material, so keep transport diagnostics at warning-or-higher.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_REQUIRED_FIELDS = frozenset({"txtGoStart", "txtGoEnd", "txtGoAbrdDt", "txtGoHour", "txtPsgFlg_1"})
_SESSION_MARKERS = (
    re.compile(r"\blog[ -]?in\b", re.IGNORECASE),
    re.compile(r"로그인"),
    re.compile(r"세션\s*(?:만료|종료)"),
)
_FORBIDDEN_REQUEST_HEADERS = frozenset(
    {"connection", "content-length", "cookie", "host", "proxy-authorization", "transfer-encoding"}
)
_SIMPLE_COOKIE_DOMAIN = ".korail.com"

ReplayErrorReason = Literal[
    "lease_invalid",
    "session_invalid",
    "provider_access_restricted",
    "rate_limited",
    "source_unavailable",
    "invalid_capture",
    "invalid_response",
]
LeaseValidator = Callable[[], bool | Awaitable[bool]]


class KorailHttpReplayError(RuntimeError):
    """A deliberately low-cardinality error that never carries captured provider data."""

    def __init__(self, reason: ReplayErrorReason) -> None:
        self.reason = reason
        super().__init__(reason)


class KorailHttpReplayLeaseInvalid(KorailHttpReplayError):
    def __init__(self) -> None:
        super().__init__("lease_invalid")


class KorailHttpReplaySessionInvalid(KorailHttpReplayError):
    def __init__(self) -> None:
        super().__init__("session_invalid")


class KorailHttpReplayProtectionDetected(KorailHttpReplayError):
    def __init__(self, trigger: str = "http_403_business") -> None:
        self.trigger = trigger
        super().__init__("provider_access_restricted")


class KorailHttpReplayRateLimited(KorailHttpReplayError):
    def __init__(self) -> None:
        super().__init__("rate_limited")


class KorailHttpReplaySourceUnavailable(KorailHttpReplayError):
    def __init__(self) -> None:
        super().__init__("source_unavailable")


class KorailHttpReplayProviderUnavailable(KorailHttpReplaySourceUnavailable):
    def __init__(self, trigger: ProviderUnavailableTrigger) -> None:
        self.trigger = trigger
        super().__init__()


class KorailHttpReplayInvalidCapture(KorailHttpReplayError):
    def __init__(self, stage: str = "unspecified") -> None:
        self.stage = (
            stage
            if stage
            in {
                "unspecified",
                "business_url",
                "headers",
                "multipart",
                "route",
                "passenger",
                "date_hour",
                "request_missing",
                "request_sequence",
                "cookies",
            }
            else "unspecified"
        )
        super().__init__("invalid_capture")


class KorailHttpReplayInvalidResponse(KorailHttpReplaySourceUnavailable):
    """A typed ordinary-invalid response, distinct from protection and throttling."""

    def __init__(self) -> None:
        KorailHttpReplayError.__init__(self, "invalid_response")


@dataclass(frozen=True)
class _FieldSpan:
    start: int
    end: int


@dataclass(frozen=True)
class HttpReplayRequest:
    method: Literal["POST"] = field(default="POST", init=False)
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class _CapturedRequest:
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)
    date_span: _FieldSpan = field(repr=False)
    hour_span: _FieldSpan = field(repr=False)

    def materialize(self, travel_date: date, cursor: str) -> HttpReplayRequest:
        date_bytes = travel_date.strftime("%Y%m%d").encode("ascii")
        cursor_bytes = cursor.encode("ascii")
        if len(date_bytes) != self.date_span.end - self.date_span.start:
            raise KorailHttpReplayInvalidCapture()
        if len(cursor_bytes) != self.hour_span.end - self.hour_span.start:
            raise KorailHttpReplayInvalidCapture()
        replacements = sorted(
            ((self.date_span, date_bytes), (self.hour_span, cursor_bytes)),
            key=lambda item: item[0].start,
            reverse=True,
        )
        body = self.body
        for span, value in replacements:
            body = body[: span.start] + value + body[span.end :]
        return HttpReplayRequest(url=self.url, headers=self.headers, content=body)


@dataclass(frozen=True)
class _CapturedCookie:
    name: str = field(repr=False)
    value: str = field(repr=False)
    domain: str = field(repr=False)
    path: str = field(repr=False)


@dataclass(frozen=True)
class KorailHttpReplayPlan:
    """Ephemeral replay material. TTL and persistence are intentionally caller-owned."""

    origin: str
    destination: str
    captured_request_count: int
    _request: _CapturedRequest = field(repr=False)
    _cookies: tuple[_CapturedCookie, ...] = field(repr=False)

    def materialize(self, request: BrowserSeatSearchRequest) -> HttpReplayRequest:
        self._validate_request(request)
        cursor = f"{request.departure_from.hour:02d}0000"
        return self._request.materialize(request.travel_date, cursor)

    def _materialize_cursor(
        self, request: BrowserSeatSearchRequest, cursor: str
    ) -> HttpReplayRequest:
        self._validate_request(request)
        if re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d00", cursor) is None:
            raise KorailHttpReplayInvalidCapture()
        return self._request.materialize(request.travel_date, cursor)

    def _validate_request(self, request: BrowserSeatSearchRequest) -> None:
        if request.passenger_count != 1:
            raise KorailHttpReplayInvalidCapture()
        if _normalize_station(request.origin) != self.origin:
            raise KorailHttpReplayInvalidCapture()
        if _normalize_station(request.destination) != self.destination:
            raise KorailHttpReplayInvalidCapture()

    def _cookie_jar(self) -> httpx.Cookies:
        jar = httpx.Cookies()
        for cookie in self._cookies:
            jar.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        return jar


def build_korail_http_replay_plan(
    network_events: Sequence[Mapping[str, Any]],
    cookies: Sequence[Mapping[str, Any]] | Mapping[str, str],
    *,
    origin: str,
    destination: str,
    captured_date: date | None = None,
) -> KorailHttpReplayPlan:
    """Build an in-memory plan from CDP/Pydoll request events and cookie dictionaries."""

    normalized_origin = _normalize_station(origin)
    normalized_destination = _normalize_station(destination)
    if (
        not normalized_origin
        or not normalized_destination
        or normalized_origin == normalized_destination
    ):
        raise KorailHttpReplayInvalidCapture()

    requests: list[_CapturedRequest] = []
    stage = "unspecified"
    try:
        for event in network_events:
            candidate = _request_candidate(event)
            if candidate is None:
                continue
            url_value = candidate.get("url")
            method = candidate.get("method")
            if not isinstance(url_value, str) or not isinstance(method, str):
                continue
            if method.upper() != "POST" or "/web_s/" not in url_value:
                continue
            stage = "business_url"
            if _event_was_redirected(event):
                raise KorailHttpReplayInvalidCapture()
            _validate_business_url(url_value)
            stage = "headers"
            headers = _captured_headers(candidate.get("headers"))
            content_type = _header_value(headers, "content-type")
            stage = "multipart"
            boundary = _multipart_boundary(content_type)
            body = _captured_body(candidate.get("postData"))
            field_spans = _multipart_field_spans(body, boundary)
            if not _REQUIRED_FIELDS.issubset(field_spans):
                raise KorailHttpReplayInvalidCapture()
            stage = "route"
            _validate_captured_route(body, field_spans, normalized_origin, normalized_destination)
            stage = "passenger"
            passenger_span = _one_span(field_spans, "txtPsgFlg_1")
            if body_slice(body, passenger_span) != b"1":
                raise KorailHttpReplayInvalidCapture()
            stage = "date_hour"
            date_span = _one_span(field_spans, "txtGoAbrdDt")
            hour_span = _one_span(field_spans, "txtGoHour")
            _validate_captured_date_hour(body, date_span, hour_span)
            if captured_date is not None:
                expected_date = captured_date.strftime("%Y%m%d").encode("ascii")
                if body_slice(body, date_span) != expected_date:
                    raise KorailHttpReplayInvalidCapture()
            captured = _CapturedRequest(
                url=url_value,
                headers=headers,
                body=body,
                date_span=date_span,
                hour_span=hour_span,
            )
            requests.append(captured)
            # One exact initial request is sufficient. Pagination is derived from
            # each response cursor, so later UI requests are not template inputs.
            break
    except KorailHttpReplayInvalidCapture as error:
        if error.stage != "unspecified":
            raise
        raise KorailHttpReplayInvalidCapture(stage) from None

    if not requests:
        raise KorailHttpReplayInvalidCapture("request_missing")
    baseline = requests[0]

    try:
        captured_cookies = _captured_cookies(cookies)
    except KorailHttpReplayInvalidCapture:
        raise KorailHttpReplayInvalidCapture("cookies") from None
    if not captured_cookies:
        raise KorailHttpReplaySessionInvalid()
    return KorailHttpReplayPlan(
        origin=normalized_origin,
        destination=normalized_destination,
        captured_request_count=len(requests),
        _request=baseline,
        _cookies=captured_cookies,
    )


class KorailHttpReplayClient:
    def __init__(
        self,
        plan: KorailHttpReplayPlan,
        timeout_seconds: float = 25.0,
        *,
        lease_is_current: LeaseValidator | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._plan = plan
        self._lease_is_current = lease_is_current
        self._client = httpx.AsyncClient(
            cookies=plan._cookie_jar(),
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
            transport=transport,
        )
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._client.aclose())
        pending_cancellation: asyncio.CancelledError | None = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as error:
                if pending_cancellation is None:
                    pending_cancellation = error
        self._close_task.result()
        self._closed = True
        if pending_cancellation is not None:
            raise pending_cancellation

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        if self._closed:
            raise KorailHttpReplaySessionInvalid()
        await self._assert_lease_current()
        cursor = f"{request.departure_from.hour:02d}0000"
        snapshots: dict[tuple[str, datetime], BrowserTrainSnapshot] = {}
        for _page_number in range(MAX_PAGES):
            await self._assert_lease_current()
            raw_request = self._plan._materialize_cursor(request, cursor)
            payload = await self._post(raw_request)
            page = _parse_page(payload, request)
            for snapshot in page.trains:
                departure_time = snapshot.departure_at.timetz().replace(tzinfo=None)
                if request.departure_from <= departure_time <= request.departure_to:
                    snapshots[(snapshot.train_number, snapshot.departure_at)] = snapshot
            if not page.has_next:
                break
            if page.last_departure is None:
                raise KorailHttpReplayInvalidResponse()
            next_departure = page.last_departure + timedelta(minutes=1)
            if next_departure.date() != request.travel_date:
                break
            next_cursor = next_departure.strftime("%H%M00")
            if next_cursor <= cursor:
                raise KorailHttpReplayInvalidResponse()
            cursor = next_cursor
        else:
            raise KorailHttpReplaySourceUnavailable()
        await self._assert_lease_current()
        trains = sorted(snapshots.values(), key=lambda item: (item.departure_at, item.train_number))
        return BrowserSeatSearchResult(
            origin=self._plan.origin,
            destination=self._plan.destination,
            travel_date=request.travel_date,
            passenger_count=1,
            observed_at=datetime.now(UTC),
            trains=trains,
        )

    async def _assert_lease_current(self) -> None:
        if self._lease_is_current is None:
            return
        current = self._lease_is_current()
        if inspect.isawaitable(current):
            current = await current
        if current is not True:
            raise KorailHttpReplayLeaseInvalid()

    async def _post(self, request: HttpReplayRequest) -> Mapping[str, Any]:
        try:
            async with self._client.stream(
                request.method,
                request.url,
                headers=request.headers,
                content=request.content,
            ) as response:
                if response.status_code == 403:
                    raise KorailHttpReplayProtectionDetected()
                if response.status_code == 429:
                    raise KorailHttpReplayRateLimited()
                if response.status_code == 401:
                    raise KorailHttpReplaySessionInvalid()
                if response.is_redirect and _is_session_redirect(response):
                    raise KorailHttpReplaySessionInvalid()
                if response.is_redirect:
                    target = urljoin(request.url, response.headers.get("location", ""))
                    unavailable_trigger = provider_unavailable_trigger_from_page(target, "")
                    if unavailable_trigger is not None:
                        raise KorailHttpReplayProviderUnavailable(unavailable_trigger)
                    raise KorailHttpReplaySourceUnavailable()
                if 400 <= response.status_code < 500:
                    raise KorailHttpReplaySourceUnavailable()
                if response.status_code != 503 and (
                    response.status_code < 200 or response.status_code >= 300
                ):
                    raise KorailHttpReplaySourceUnavailable()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise KorailHttpReplaySourceUnavailable()
                    body.extend(chunk)
        except KorailHttpReplayError:
            raise
        except httpx.HTTPError:
            raise KorailHttpReplaySourceUnavailable() from None
        unavailable_trigger = provider_unavailable_trigger_from_page(
            str(response.url),
            decode_provider_page_text(bytes(body)),
        )
        if unavailable_trigger is not None:
            raise KorailHttpReplayProviderUnavailable(unavailable_trigger)
        if response.status_code == 503:
            raise KorailHttpReplaySourceUnavailable()
        marker = _protection_marker(bytes(body))
        if marker is not None:
            raise KorailHttpReplayProtectionDetected(marker)
        if _session_marker(bytes(body), response.headers.get("content-type", "")):
            raise KorailHttpReplaySessionInvalid()
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KorailHttpReplayInvalidResponse() from None
        if not isinstance(payload, Mapping):
            raise KorailHttpReplayInvalidResponse()
        return payload


@dataclass(frozen=True)
class _ParsedPage:
    trains: tuple[BrowserTrainSnapshot, ...]
    has_next: bool
    last_departure: datetime | None


def _parse_page(payload: Mapping[str, Any], request: BrowserSeatSearchRequest) -> _ParsedPage:
    if payload.get("strResult") != "SUCC":
        marker = _protection_marker(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if marker is not None:
            raise KorailHttpReplayProtectionDetected(marker)
        raise KorailHttpReplayInvalidResponse()
    next_flag = payload.get("h_next_pg_flg")
    if next_flag not in {"Y", "N"}:
        raise KorailHttpReplayInvalidResponse()
    infos = payload.get("trn_infos")
    if not isinstance(infos, Mapping):
        raise KorailHttpReplayInvalidResponse()
    raw_rows = infos.get("trn_info")
    if isinstance(raw_rows, Mapping):
        rows: Sequence[Mapping[str, Any]] = (raw_rows,)
    elif isinstance(raw_rows, list) and all(isinstance(row, Mapping) for row in raw_rows):
        rows = raw_rows
    else:
        raise KorailHttpReplayInvalidResponse()

    trains: list[BrowserTrainSnapshot] = []
    last_departure: datetime | None = None
    for row in rows:
        departure = _row_departure(row, request.travel_date)
        if last_departure is None or departure > last_departure:
            last_departure = departure
        train_kind = _required_string(row, "h_trn_clsf_nm")
        train_type = parse_official_train_type(train_kind)
        if train_type is None or not _is_ktx_family(train_kind):
            continue
        if _normalize_station(_required_string(row, "h_dpt_rs_stn_nm")) != request.origin:
            raise KorailHttpReplayInvalidResponse()
        if _normalize_station(_required_string(row, "h_arv_rs_stn_nm")) != request.destination:
            raise KorailHttpReplayInvalidResponse()
        arrival = _row_arrival(row, departure)
        standard_text = _seat_string(row, "h_gen_rsv_nm")
        trains.append(
            BrowserTrainSnapshot(
                train_number=_normalize_train_number(_required_string(row, "h_trn_no")),
                train_type=train_type,
                departure_at=departure,
                arrival_at=arrival,
                adult_fare=parse_unambiguous_adult_fare(standard_text),
                standard=_seat_status(standard_text),
                first=_seat_status(_seat_string(row, "h_spe_rsv_nm")),
                expected_delay_minutes=_expected_delay_minutes(row.get("h_expn_dpt_dlay_tnum")),
            )
        )
    return _ParsedPage(tuple(trains), next_flag == "Y", last_departure)


def _row_departure(row: Mapping[str, Any], requested_date: date) -> datetime:
    departure = _row_datetime(row, "h_dpt_dt", "h_dpt_tm")
    if departure.date() != requested_date:
        raise KorailHttpReplayInvalidResponse()
    return departure


def _row_arrival(row: Mapping[str, Any], departure: datetime) -> datetime:
    arrival = _row_datetime(row, "h_arv_dt", "h_arv_tm")
    if arrival <= departure:
        raise KorailHttpReplayInvalidResponse()
    return arrival


def _row_datetime(
    row: Mapping[str, Any],
    date_field: str,
    time_field: str,
) -> datetime:
    date_text = _required_string(row, date_field)
    time_text = _required_string(row, time_field)
    if re.fullmatch(r"\d{8}", date_text) is None:
        raise KorailHttpReplayInvalidResponse()
    if re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d", time_text) is None:
        raise KorailHttpReplayInvalidResponse()
    try:
        service_date = date.fromisoformat(f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}")
    except ValueError:
        raise KorailHttpReplayInvalidResponse() from None
    return datetime.combine(
        service_date,
        time(int(time_text[:2]), int(time_text[2:4]), int(time_text[4:])),
        tzinfo=KST,
    )


def _expected_delay_minutes(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    normalized = str(value).strip()
    if re.fullmatch(r"\d{1,3}", normalized) is None:
        return None
    delay = int(normalized)
    return delay if delay > 0 else None


def _seat_status(value: str) -> SeatStatus:
    normalized = " ".join(value.split()).casefold()
    if not normalized:
        return "not_offered"
    if re.search(r"예약\s*대기", normalized):
        return "waitlist_available"
    if re.search(r"매진\s*임박|좌석\s*(?:부족|소량)", normalized):
        return "limited"
    if re.search(r"입석\s*\+\s*(?:좌석|예매)", normalized):
        return "standing_plus_seat"
    if re.search(r"매진", normalized):
        return "sold_out"
    if re.fullmatch(r"(?:-|–|—|없음|해당\s*없음|미운행|미운영|특실\s*없음)", normalized):
        return "not_offered"
    if re.search(r"(?:예약|예매)\s*가능|좌석\s*(?:있음|많음)", normalized):
        return "available"
    raise KorailHttpReplayInvalidResponse()


def _request_candidate(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload: Mapping[str, Any] = event
    params = event.get("params")
    if isinstance(params, Mapping):
        payload = params
    request = payload.get("request")
    if isinstance(request, Mapping):
        return request
    if "url" in payload and "method" in payload:
        return payload
    return None


def _event_was_redirected(event: Mapping[str, Any]) -> bool:
    params = event.get("params")
    payload = params if isinstance(params, Mapping) else event
    return payload.get("redirectResponse") is not None


def _validate_business_url(value: str) -> None:
    if len(value) > 4096 or any(character in value for character in "\r\n\x00"):
        raise KorailHttpReplayInvalidCapture()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise KorailHttpReplayInvalidCapture() from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != OFFICIAL_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/web_s/")
        or parsed.path.startswith("//")
    ):
        raise KorailHttpReplayInvalidCapture()


def _captured_headers(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise KorailHttpReplayInvalidCapture()
    headers: list[tuple[str, str]] = []
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise KorailHttpReplayInvalidCapture()
        name = raw_name.strip().lower()
        if not name or name.startswith(":") or "\r" in name or "\n" in name:
            raise KorailHttpReplayInvalidCapture()
        if "\r" in raw_value or "\n" in raw_value:
            raise KorailHttpReplayInvalidCapture()
        if name in _FORBIDDEN_REQUEST_HEADERS:
            continue
        headers.append((name, raw_value))
    if not headers:
        raise KorailHttpReplayInvalidCapture()
    return tuple(headers)


def _header_value(headers: tuple[tuple[str, str], ...], name: str) -> str:
    values = [value for header_name, value in headers if header_name == name]
    if len(values) != 1:
        raise KorailHttpReplayInvalidCapture()
    return values[0]


def _multipart_boundary(content_type: str) -> bytes:
    match = re.fullmatch(
        r"\s*multipart/form-data\s*;\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))\s*",
        content_type,
        re.IGNORECASE,
    )
    if match is None:
        raise KorailHttpReplayInvalidCapture()
    boundary_text = match.group(1) or match.group(2)
    try:
        boundary = boundary_text.encode("ascii")
    except UnicodeEncodeError:
        raise KorailHttpReplayInvalidCapture() from None
    if not 1 <= len(boundary) <= 70 or any(byte < 33 or byte > 126 for byte in boundary):
        raise KorailHttpReplayInvalidCapture()
    return boundary


def _captured_body(value: object) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        return value
    raise KorailHttpReplayInvalidCapture()


def _multipart_field_spans(body: bytes, boundary: bytes) -> dict[str, list[_FieldSpan]]:
    delimiter = b"--" + boundary
    if not body.startswith(delimiter + b"\r\n") or not body.endswith(delimiter + b"--\r\n"):
        raise KorailHttpReplayInvalidCapture()
    spans: dict[str, list[_FieldSpan]] = {}
    position = len(delimiter) + 2
    final_start = len(body) - len(delimiter) - 4
    while position < final_start:
        header_end = body.find(b"\r\n\r\n", position, final_start)
        if header_end < 0:
            raise KorailHttpReplayInvalidCapture()
        header_block = body[position:header_end]
        disposition = re.search(
            rb'(?:^|\r\n)Content-Disposition:\s*form-data;\s*name="([^"\r\n]+)"\s*$',
            header_block,
            re.IGNORECASE,
        )
        if disposition is None:
            raise KorailHttpReplayInvalidCapture()
        try:
            name = disposition.group(1).decode("ascii")
        except UnicodeDecodeError:
            raise KorailHttpReplayInvalidCapture() from None
        value_start = header_end + 4
        next_delimiter = body.find(b"\r\n" + delimiter, value_start)
        if next_delimiter < 0:
            raise KorailHttpReplayInvalidCapture()
        spans.setdefault(name, []).append(_FieldSpan(value_start, next_delimiter))
        after_delimiter = next_delimiter + 2 + len(delimiter)
        if body[after_delimiter : after_delimiter + 2] == b"--":
            position = final_start
        elif body[after_delimiter : after_delimiter + 2] == b"\r\n":
            position = after_delimiter + 2
        else:
            raise KorailHttpReplayInvalidCapture()
    return spans


def _one_span(spans: Mapping[str, list[_FieldSpan]], field_name: str) -> _FieldSpan:
    matches = spans.get(field_name, [])
    if len(matches) != 1:
        raise KorailHttpReplayInvalidCapture()
    return matches[0]


def _validate_captured_route(
    body: bytes,
    spans: Mapping[str, list[_FieldSpan]],
    origin: str,
    destination: str,
) -> None:
    try:
        captured_origin = body_slice(body, _one_span(spans, "txtGoStart")).decode("utf-8")
        captured_destination = body_slice(body, _one_span(spans, "txtGoEnd")).decode("utf-8")
    except UnicodeDecodeError:
        raise KorailHttpReplayInvalidCapture() from None
    if _normalize_station(captured_origin) != origin:
        raise KorailHttpReplayInvalidCapture()
    if _normalize_station(captured_destination) != destination:
        raise KorailHttpReplayInvalidCapture()


def _validate_captured_date_hour(
    body: bytes,
    date_span: _FieldSpan,
    hour_span: _FieldSpan,
) -> None:
    date_value = body_slice(body, date_span)
    hour_value = body_slice(body, hour_span)
    if re.fullmatch(rb"\d{8}", date_value) is None:
        raise KorailHttpReplayInvalidCapture()
    if re.fullmatch(rb"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d", hour_value) is None:
        raise KorailHttpReplayInvalidCapture()
    try:
        date_text = date_value.decode("ascii")
        date.fromisoformat(f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}")
    except ValueError:
        raise KorailHttpReplayInvalidCapture() from None


def body_slice(body: bytes, span: _FieldSpan) -> bytes:
    return body[span.start : span.end]


def _captured_cookies(
    cookies: Sequence[Mapping[str, Any]] | Mapping[str, str],
) -> tuple[_CapturedCookie, ...]:
    if isinstance(cookies, Mapping):
        rows: Sequence[Mapping[str, Any]] = tuple(
            {"name": name, "value": value} for name, value in cookies.items()
        )
    else:
        rows = cookies
    captured: list[_CapturedCookie] = []
    for row in rows:
        name = row.get("name")
        value = row.get("value")
        domain = row.get("domain", _SIMPLE_COOKIE_DOMAIN)
        path = row.get("path", "/")
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not isinstance(domain, str)
            or not isinstance(path, str)
        ):
            continue
        normalized_domain = domain.casefold().lstrip(".")
        if normalized_domain not in {OFFICIAL_HOST, "korail.com"}:
            continue
        if not name or any(character in name for character in "\r\n;="):
            raise KorailHttpReplayInvalidCapture()
        if "\r" in value or "\n" in value:
            raise KorailHttpReplayInvalidCapture()
        if not path.startswith("/"):
            continue
        captured.append(_CapturedCookie(name, value, domain, path))
    return tuple(captured)


def _normalize_station(value: str) -> str:
    return " ".join(value.split()).removesuffix("역")


def _is_ktx_family(value: str) -> bool:
    normalized = " ".join(value.split()).replace("–", "-").replace("—", "-").casefold()
    return re.fullmatch(r"ktx(?:-?(?:산천|청룡))?", normalized) is not None


def _required_string(row: Mapping[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise KorailHttpReplayInvalidResponse()
    return value


def _seat_string(row: Mapping[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or len(value) > 100:
        raise KorailHttpReplayInvalidResponse()
    return value


def _normalize_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise KorailHttpReplayInvalidResponse()
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"


def _protection_marker(body: bytes) -> ProtectionTrigger | None:
    text = body.decode("utf-8", errors="ignore")
    return protection_trigger_from_replay_text(text)


def _session_marker(body: bytes, content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type not in {"text/html", "application/xhtml+xml"}:
        return False
    text = body.decode("utf-8", errors="ignore")
    lowered = text.casefold()
    has_html_document = "<html" in lowered or "<!doctype html" in lowered
    has_login_control = bool(
        re.search(
            r"(?:type\s*=\s*['\"]password['\"]|"
            r"(?:id|class|action)\s*=\s*['\"][^'\"]*\blogin\b)",
            text,
            re.IGNORECASE,
        )
    )
    return bool(
        has_html_document
        and has_login_control
        and any(pattern.search(text) is not None for pattern in _SESSION_MARKERS)
    )


def _is_session_redirect(response: httpx.Response) -> bool:
    location = response.headers.get("location")
    if not location:
        return False
    try:
        request = response.request
    except RuntimeError:
        return False
    try:
        parsed = urlsplit(urljoin(str(request.url), location))
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != OFFICIAL_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    segments = tuple(segment for segment in parsed.path.casefold().split("/") if segment)
    return any(
        segment == marker or segment.startswith(f"{marker}.")
        for segment in segments
        for marker in ("login", "member", "auth")
    )


# Stable integration surface. The longer names remain source-compatible for focused callers.
HttpReplayLeaseInvalid = KorailHttpReplayLeaseInvalid
HttpReplaySessionInvalid = KorailHttpReplaySessionInvalid
HttpReplayProtectionDetected = KorailHttpReplayProtectionDetected
HttpReplayRateLimited = KorailHttpReplayRateLimited
HttpReplaySourceUnavailable = KorailHttpReplaySourceUnavailable
HttpReplayProviderUnavailable = KorailHttpReplayProviderUnavailable
HttpReplayInvalidCapture = KorailHttpReplayInvalidCapture
HttpReplayInvalidResponse = KorailHttpReplayInvalidResponse
HttpReplayPlan = KorailHttpReplayPlan


def build_http_replay_plan(
    events: Sequence[Mapping[str, Any]],
    cookies: Sequence[Mapping[str, Any]] | Mapping[str, str],
    origin: str,
    destination: str,
    captured_date: date,
) -> KorailHttpReplayPlan:
    return build_korail_http_replay_plan(
        events,
        cookies,
        origin=origin,
        destination=destination,
        captured_date=captured_date,
    )
