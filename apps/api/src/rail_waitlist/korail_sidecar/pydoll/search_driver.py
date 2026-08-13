"""Drive one KORAIL timetable search form and its read-only result DOM."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from datetime import time as clock_time
from typing import Any, Literal, Protocol, cast

from ..browser_contracts import BrowserSourceUnavailable
from ..browser_protection import protection_trigger_from_text
from ..browser_service_availability import provider_unavailable_trigger_from_page
from .page_contracts import (
    PydollIssuedTicketListSnapshot,
    PydollIssuedTicketSummary,
    PydollPageSnapshot,
    PydollReservationListSnapshot,
    PydollSeatBox,
    PydollTrainRow,
)
from .search_snapshot_policy import (
    advance_search_expansion,
    begin_search_expansion,
)

__all__ = (
    "Any",
    "Awaitable",
    "BrowserSourceUnavailable",
    "Callable",
    "Collection",
    "EvaluateText",
    "EvaluateValue",
    "ExecuteScript",
    "Mapping",
    "Protocol",
    "PydollPageSnapshot",
    "PydollSearchDomDriver",
    "PydollSeatBox",
    "PydollTrainRow",
    "QueryElement",
    "SearchControlState",
    "SearchDomCompatibilityPort",
    "SearchHourCandidate",
    "SnapshotMerge",
    "SnapshotStop",
    "SnapshotTransform",
    "TrainRowIdentity",
    "advance_search_expansion",
    "annotations",
    "begin_search_expansion",
    "dataclass",
    "date",
    "protection_trigger_from_text",
    "re",
)


class SearchControlState(Protocol):
    enabled: bool
    aria_disabled: str
    disabled_attribute: bool
    classes: tuple[str, ...]
    container_classes: tuple[str, ...]
    slide_classes: tuple[str, ...]
    read_error: bool


@dataclass(frozen=True)
class SearchHourCandidate:
    element: Any
    hour: int
    state: SearchControlState


class SearchDomCompatibilityPort(Protocol):
    async def current_schedule(self) -> tuple[date, int]: ...

    async def _find_exact_visible(
        self,
        selector: str,
        text: str,
        *,
        scope: Any = None,
    ) -> Any: ...

    async def _wait_for_dialog(self, marker: str) -> Any: ...

    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]: ...

    async def _wait_for_exact_text(
        self,
        selector: str,
        text: str,
        *,
        scope: Any = None,
    ) -> Any: ...

    async def _wait_for_value(
        self,
        selector: str,
        expected: str,
        *,
        contains: bool = False,
    ) -> None: ...

    async def _click_exact_text(self, selector: str, text: str) -> None: ...

    async def _wait_for_enabled_exact_text(
        self,
        selector: str,
        text: str,
        *,
        scope: Any,
        failure_stage: str,
        accepted_labels: tuple[str, ...] = (),
    ) -> Any: ...

    async def _wait_for_visible_elements(
        self,
        selector: str,
        *,
        scope: Any = None,
        failure_stage: str,
    ) -> list[Any]: ...

    async def _read_hour_candidates(
        self,
        selector: str,
        *,
        scope: Any,
        visible_only: bool = True,
    ) -> list[SearchHourCandidate]: ...

    def _current_hour_window(
        self,
        candidates: list[SearchHourCandidate],
    ) -> list[SearchHourCandidate]: ...

    def _hour_window_signature(
        self,
        candidates: list[SearchHourCandidate],
    ) -> tuple[object, ...]: ...

    def _is_exact_hour_catalog(self, candidates: list[SearchHourCandidate]) -> bool: ...

    def _is_soft_dom_hour(self, candidate: SearchHourCandidate) -> bool: ...

    def _is_soft_adjacent_hour(
        self,
        candidates: list[SearchHourCandidate],
        current_window: list[SearchHourCandidate],
        target: SearchHourCandidate,
    ) -> bool: ...

    async def _click_hour_and_confirm(self, candidate: SearchHourCandidate) -> bool: ...

    def _is_exact_selected_hour(
        self,
        candidates: list[SearchHourCandidate],
        target_elements: list[SearchHourCandidate],
        *,
        target_date_is_selected: bool,
        pre_picker_hour_matches: bool,
    ) -> bool: ...

    async def _find_hour_navigation_control(
        self,
        direction: str,
        *,
        scope: Any,
    ) -> Any | None: ...

    async def _wait_for_hour_window_change(
        self,
        dialog: Any,
        before: tuple[int, ...],
        direction: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool: ...

    async def _swipe_hour_carousel(self, dialog: Any, direction: str) -> None: ...

    async def _navigate_hour_carousel_by_keyboard(
        self,
        dialog: Any,
        direction: str,
    ) -> bool: ...

    async def _log_hour_window_navigation_failure(
        self,
        dialog: Any,
        before: tuple[int, ...],
    ) -> None: ...

    async def _wait_for_schedule_date(self, travel_date: date) -> None: ...

    async def _wait_for_schedule(self, travel_date: date, departure_hour: int) -> None: ...

    async def _snapshot(self) -> PydollPageSnapshot: ...

    async def _wait_for_result_growth(
        self,
        previous_rows: set[tuple[str, str, str]],
    ) -> tuple[PydollPageSnapshot, bool]: ...


class QueryElement(Protocol):
    def __call__(self, selector: str, **options: object) -> Awaitable[Any]: ...


class ExecuteScript(Protocol):
    def __call__(self, script: str, *, return_by_value: bool) -> Awaitable[object]: ...


type EvaluateValue = Callable[[str], Awaitable[object]]
type EvaluateText = Callable[[str], Awaitable[str]]
type SnapshotTransform = Callable[[PydollPageSnapshot], PydollPageSnapshot]
type SnapshotMerge = Callable[[PydollPageSnapshot, PydollPageSnapshot], PydollPageSnapshot]
type SnapshotStop = Callable[[PydollPageSnapshot], bool]
type TrainRowIdentity = Callable[[PydollTrainRow], tuple[str, str, str]]


class PydollSearchDomDriver:
    """Own exact search-form state transitions and read-only result observation."""

    def __init__(
        self,
        *,
        port: SearchDomCompatibilityPort,
        timeout_seconds: float,
        query: QueryElement,
        execute_script: ExecuteScript,
        evaluate_value: EvaluateValue,
        evaluate_text: EvaluateText,
        is_submitted: Callable[[], bool],
        mark_submitted: Callable[[], None],
        network_responses: Callable[[], Collection[tuple[int, str]]],
        deduplicate_snapshot: SnapshotTransform,
        merge_page_snapshots: SnapshotMerge,
        snapshot_requires_expansion_stop: SnapshotStop,
        train_row_identity: TrainRowIdentity,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        protection_surface_selector: str,
    ) -> None:
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._query = query
        self._execute_script = execute_script
        self._evaluate_value = evaluate_value
        self._evaluate_text = evaluate_text
        self._is_submitted = is_submitted
        self._mark_submitted = mark_submitted
        self._network_responses = network_responses
        self._deduplicate_snapshot = deduplicate_snapshot
        self._merge_page_snapshots = merge_page_snapshots
        self._snapshot_requires_expansion_stop = snapshot_requires_expansion_stop
        self._train_row_identity = train_row_identity
        self._monotonic = monotonic
        self._sleep = sleep
        self._protection_surface_selector = protection_surface_selector

    async def choose_station(self, kind: str, station: str) -> None:
        station_names = {"departure": "txtGoStart", "arrival": "txtGoEnd"}
        station_triggers = {"departure": "출발역 선택", "arrival": "도착역 선택"}
        trigger = await self._port._find_exact_visible("a", station_triggers[kind])
        await trigger.click()
        dialog = await self._port._wait_for_dialog("기차역 조회")
        try:
            target = await self._port._find_exact_visible("a", station, scope=dialog)
        except LookupError:
            inputs = await self._port._visible_elements(
                "input[title='역명을 입력해주세요']",
                scope=dialog,
            )
            if len(inputs) != 1:
                raise BrowserSourceUnavailable("station_search_input") from None
            await inputs[0].clear()
            await inputs[0].type_text(station)
            search = await self._port._find_exact_visible("button", "검색", scope=dialog)
            await search.click()
            target = await self._port._wait_for_exact_text("a", station, scope=dialog)
        await target.click()
        await self._port._wait_for_value(f"input[name='{station_names[kind]}']", station)

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        applied_date, applied_hour = await self._port.current_schedule()
        target_date_was_selected = applied_date == travel_date
        pre_picker_hour_matches = applied_hour == departure_hour
        trigger = await self._query("a[title='출발일']", timeout=self._timeout_seconds)
        await trigger.click()
        dialog = await self._port._wait_for_dialog("날짜 선택")
        target_month = f"{travel_date.year}. {travel_date.month:02d}."
        target_slide = None
        await self._port._wait_for_visible_elements(
            ".datepk_wrap .slick-slide.slick-active",
            scope=dialog,
            failure_stage="departure_date_controls",
        )
        for _ in range(25):
            active_slides = await self._port._visible_elements(
                ".datepk_wrap .slick-slide.slick-active",
                scope=dialog,
            )
            for slide in active_slides:
                label = await slide.query("p.date", raise_exc=False)
                if label is not None and (await label.text).strip() == target_month:
                    target_slide = slide
                    break
            if target_slide is not None:
                break
            current = await dialog.query(".datepk_wrap .slick-current p.date")
            current_match = re.fullmatch(r"(\d{4})\.\s*(\d{2})\.", (await current.text).strip())
            if current_match is None:
                raise BrowserSourceUnavailable("departure_month_navigate")
            current_month = (int(current_match.group(1)), int(current_match.group(2)))
            direction = (
                ".slick-next"
                if current_month < (travel_date.year, travel_date.month)
                else ".slick-prev"
            )
            arrow = await dialog.query(
                f".datepk_wrap button{direction}:not(.slick-disabled)",
                raise_exc=False,
            )
            if arrow is None:
                raise BrowserSourceUnavailable("departure_month_navigate")
            await arrow.click()
            await self._sleep(0.3)
        if target_slide is None:
            raise BrowserSourceUnavailable("departure_month_find")
        if not target_date_was_selected:
            day = await self._port._wait_for_enabled_exact_text(
                ".datepicker a",
                str(travel_date.day),
                scope=target_slide,
                failure_stage="departure_date_disabled",
                accepted_labels=(f"{travel_date.day}출발일", f"{travel_date.day} 출발일"),
            )
            await day.click()
            # The official picker can retain the previous service date's hour state.
            # Commit and verify only the date before reopening the hour controls.
            apply_button = await self._port._find_exact_visible("button", "적용", scope=dialog)
            await apply_button.click()
            await self._port._wait_for_schedule_date(travel_date)
            _, applied_hour = await self._port.current_schedule()
            pre_picker_hour_matches = applied_hour == departure_hour
            trigger = await self._query("a[title='출발일']", timeout=self._timeout_seconds)
            await trigger.click()
            dialog = await self._port._wait_for_dialog("날짜 선택")
            target_date_was_selected = True

        seen_signatures: set[tuple[object, ...]] = set()
        await self._port._wait_for_visible_elements(
            ".slideWrap .slick-slide.slick-active a",
            scope=dialog,
            failure_stage="departure_hour_controls",
        )
        for _ in range(24):
            candidates = await self._port._read_hour_candidates(
                ".slideWrap .slick-slide.slick-active a",
                scope=dialog,
            )
            all_candidates = await self._port._read_hour_candidates(
                ".slideWrap .slick-slide a",
                scope=dialog,
                visible_only=False,
            )
            current_window = self._port._current_hour_window(candidates)
            signature = self._port._hour_window_signature(current_window)
            if not current_window or signature in seen_signatures:
                raise BrowserSourceUnavailable("departure_hour_navigate")
            seen_signatures.add(signature)

            active_targets = [
                candidate for candidate in candidates if candidate.hour == departure_hour
            ]
            all_targets = [
                candidate for candidate in all_candidates if candidate.hour == departure_hour
            ]
            current_targets = [
                candidate for candidate in current_window if candidate.hour == departure_hour
            ]
            # React Slick retains the complete hour catalog outside the clipped
            # viewport. Validate it, but click only after the target is active.
            if (
                not active_targets
                and self._port._is_exact_hour_catalog(all_candidates)
                and (len(all_targets) != 1 or not self._port._is_soft_dom_hour(all_targets[0]))
            ):
                raise BrowserSourceUnavailable("departure_hour_disabled")
            if (
                len(active_targets) == 1
                and self._port._is_soft_adjacent_hour(
                    candidates,
                    current_window,
                    active_targets[0],
                )
                and await self._port._click_hour_and_confirm(active_targets[0])
            ):
                # KORAIL marks the adjacent active group with soft aria-disabled.
                # Accept it only when a live click produces the ``current`` marker.
                break
            for candidate in current_targets:
                if candidate.state.enabled:
                    if not await self._port._click_hour_and_confirm(candidate):
                        raise BrowserSourceUnavailable("departure_hour_navigate")
                    break
            else:
                already_selected = self._port._is_exact_selected_hour(
                    current_window,
                    current_targets,
                    target_date_is_selected=target_date_was_selected,
                    pre_picker_hour_matches=pre_picker_hour_matches,
                )
                if already_selected:
                    break
                if current_targets:
                    raise BrowserSourceUnavailable("departure_hour_disabled")
                current_hours = tuple(candidate.hour for candidate in current_window)
                if departure_hour < min(current_hours):
                    direction = ".slick-prev"
                elif departure_hour > max(current_hours):
                    direction = ".slick-next"
                else:
                    raise BrowserSourceUnavailable("departure_hour_navigate")
                arrow = await self._port._find_hour_navigation_control(direction, scope=dialog)
                if arrow is not None:
                    await arrow.click()
                    if await self._port._wait_for_hour_window_change(
                        dialog,
                        current_hours,
                        direction,
                        timeout_seconds=1,
                    ):
                        continue
                # Keep the date carousel out of this path. Reproduce a pointer drag
                # only inside the unique time viewport, with release owned by the
                # browser's low-level CDP primitive even under cancellation.
                await self._port._swipe_hour_carousel(dialog, direction)
                if await self._port._wait_for_hour_window_change(
                    dialog,
                    current_hours,
                    direction,
                ):
                    continue
                # Keyboard navigation is the final official-control path. It is not
                # a DOM mutation fallback and still requires live window readback.
                if await self._port._navigate_hour_carousel_by_keyboard(
                    dialog,
                    direction,
                ) and await self._port._wait_for_hour_window_change(
                    dialog,
                    current_hours,
                    direction,
                ):
                    continue
                await self._port._log_hour_window_navigation_failure(dialog, current_hours)
                raise BrowserSourceUnavailable("departure_hour_navigate")
            break
        else:
            raise BrowserSourceUnavailable("departure_hour_find")
        apply_button = await self._port._find_exact_visible("button", "적용", scope=dialog)
        await apply_button.click()
        await self._port._wait_for_schedule(travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        station_names = {"departure": "txtGoStart", "arrival": "txtGoEnd"}
        return str(await self._evaluate_value(f"input[name='{station_names[kind]}']")).strip()

    async def current_schedule(self) -> tuple[date, int]:
        value = str(await self._evaluate_value("#startDate")).strip()
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\([^)]*\)\s+(\d{2}):00", value)
        if match is None:
            raise BrowserSourceUnavailable("departure_current_date")
        return date.fromisoformat(match.group(1)), int(match.group(2))

    async def current_passenger(self) -> str:
        value = await self._evaluate_text("a.data.btn_pop")
        if not value:
            value = str(await self._evaluate_value("#passenger, #labelple"))
        return " ".join(value.split())

    async def submit_once(self) -> None:
        if self._is_submitted():
            raise BrowserSourceUnavailable("submit_button")
        # Latch before awaiting the click so an uncertain result cannot resubmit.
        self._mark_submitted()
        await self._port._click_exact_text("button", "열차 조회")

    async def wait_for_result(self) -> PydollPageSnapshot:
        deadline = self._monotonic() + self._timeout_seconds
        last = await self._port._snapshot()
        while self._monotonic() < deadline:
            trigger = protection_trigger_from_text(last.body_text)
            unavailable_trigger = provider_unavailable_trigger_from_page(
                last.url,
                last.body_text,
                has_result_rows=bool(last.rows),
            )
            if (
                trigger is not None
                or unavailable_trigger is not None
                or last.rows
                or last.network_responses
            ):
                return last
            if re.search(r"조회\s*결과(?:가)?\s*(?:없|0건)", last.body_text):
                raise BrowserSourceUnavailable("wait_result")
            await self._sleep(0.25)
            last = await self._port._snapshot()
        raise BrowserSourceUnavailable("wait_result")

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot:
        state = begin_search_expansion(
            snapshot,
            deduplicate_snapshot=self._deduplicate_snapshot,
            row_identity=self._train_row_identity,
        )
        for _ in range(max(0, max_actions)):
            if self._snapshot_requires_expansion_stop(state.accumulated):
                break
            try:
                more = await self._port._find_exact_visible("a", "더보기")
            except LookupError:
                break
            await more.click()
            candidate, progressed = await self._port._wait_for_result_growth(
                set(state.seen_identities)
            )
            transition = advance_search_expansion(
                state,
                candidate,
                observed_growth=progressed,
                merge_snapshots=self._merge_page_snapshots,
                row_identity=self._train_row_identity,
                snapshot_requires_stop=self._snapshot_requires_expansion_stop,
            )
            state = transition.state
            if transition.stop_reason is not None:
                break
        return state.accumulated

    async def wait_for_result_growth(
        self,
        previous_rows: set[tuple[str, str, str]],
    ) -> tuple[PydollPageSnapshot, bool]:
        deadline = self._monotonic() + min(self._timeout_seconds, 10)
        last = await self._port._snapshot()
        while True:
            if self._snapshot_requires_expansion_stop(last):
                return last, False
            current_rows = {self._train_row_identity(row) for row in last.rows}
            if current_rows - previous_rows:
                return last, True
            if self._monotonic() >= deadline:
                return last, False
            await self._sleep(0.25)
            last = await self._port._snapshot()

    async def snapshot(self) -> PydollPageSnapshot:
        script = """
            (() => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              return {
                body: document.body?.innerText || '',
                url: window.location.href,
                title: document.title || '',
                reservationRows: (() => {
                  const normalized = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                  const action = Array.from(document.querySelectorAll('button,a'))
                    .filter(visible)
                    .filter((item) => normalized(item.innerText) === '결제/발권');
                  const rows = [];
                  for (const control of action) {
                    let current = control.parentElement;
                    let best = null;
                    for (let depth = 0; current && depth < 9; depth += 1) {
                      const text = current.innerText || '';
                      if (text.includes('예약취소') && text.includes('예약변경') &&
                          text.includes('결제/발권') && text.includes('→') &&
                          (text.match(/\\b\\d{2}:\\d{2}\\b/g) || []).length >= 2) {
                        best = current;
                        break;
                      }
                      current = current.parentElement;
                    }
                    if (best && !rows.includes(best)) rows.push(best);
                  }
                  return rows.map((row) => row.innerText || '');
                })(),
                protectionTexts: Array.from(document.querySelectorAll(
                  __PROTECTION_SURFACE_SELECTOR__
                )).filter(visible).map((item) => item.innerText),
                rows: Array.from(document.querySelectorAll('li.tckList')).filter(visible)
                  .map((row) => ({
                    kind: row.querySelector('.tck_inner .tit_box')?.innerText || '',
                    number: row.querySelector('.tck_inner .tit_box .num')?.innerText || '',
                    route: row.querySelector('.tck_inner .data_box.right')?.innerText || '',
                    fullText: row.innerText || '',
                    seats: Array.from(row.querySelectorAll('.tck_inner .price_box'))
                      .filter(visible).map((box) => ({
                        text: box.innerText,
                        classes: Array.from(new Set([
                          ...box.classList,
                          ...Array.from(box.querySelectorAll('.sold_out,.sold_out_soon'))
                            .flatMap((item) => Array.from(item.classList)),
                        ])),
                      })),
                  })),
              };
            })()
            """.replace(
            "__PROTECTION_SURFACE_SELECTOR__",
            repr(self._protection_surface_selector),
        )
        response = await self._execute_script(script, return_by_value=True)
        if not isinstance(response, Mapping):
            raise TypeError("script response is not a mapping")
        value = response["result"]["result"]["value"]
        return PydollPageSnapshot(
            body_text=str(value["body"]),
            url=str(value["url"]),
            title=str(value["title"]),
            reservation_rows=tuple(str(item) for item in value["reservationRows"]),
            protection_texts=tuple(str(item) for item in value["protectionTexts"]),
            network_responses=tuple(self._network_responses()),
            rows=tuple(
                PydollTrainRow(
                    kind_text=str(row["kind"]),
                    train_number=str(row["number"]),
                    route_text=str(row["route"]),
                    seats=tuple(
                        PydollSeatBox(
                            text=str(box["text"]),
                            classes=frozenset(str(item) for item in box["classes"]),
                        )
                        for box in row["seats"]
                    ),
                    full_text=str(row["fullText"]),
                )
                for row in value["rows"]
            ),
        )

    async def issued_ticket_snapshot(self) -> PydollIssuedTicketListSnapshot:
        """Read only redacted, card-scoped MyTicket fields from the current page."""

        script = r"""
            (() => {
              const normalized = (value) => (value || '').replace(/\s+/g, ' ').trim();
              const visible = (element) => {
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const serviceDate = (value) => {
                const match = normalized(value).match(
                  /(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일/
                );
                if (!match) return null;
                return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}`;
              };
              const serviceTime = (value) => {
                const match = normalized(value).match(/(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)/);
                if (!match) return null;
                const hour = Number(match[1]);
                const minute = Number(match[2]);
                if (hour > 23 || minute > 59) return null;
                return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
              };
              const station = (element) => normalized(element?.innerText).replace(/역$/, '');
              const cards = Array.from(
                document.querySelectorAll('.tck_info-wrap.type_default')
              ).filter(visible);
              const tickets = cards.map((card) => {
                const journeys = Array.from(card.querySelectorAll('.train_list-wrap'))
                  .filter(visible);
                const seatLists = Array.from(card.querySelectorAll(
                  'ul.list-table[data-krl-name="ticketSeatInfo"]'
                )).filter(visible);
                if (journeys.length !== 1 || seatLists.length !== 1) return null;
                const journey = journeys[0];
                const trainText = normalized(
                  journey.querySelector('.my-ticket__trn-ticket-trn-name')?.innerText
                );
                const trainMatch = trainText.match(/\d{1,5}/);
                const seatText = normalized(seatLists[0].innerText);
                const carMatch = seatText.match(
                  /(?:호차번호|호차)\s*[:：]?\s*(\d{1,3})(?:\s*호차)?/
                ) || seatText.match(/(?:^|\s)(\d{1,3})\s*호차(?:\s|$)/);
                const seatMatch = seatText.match(
                  /(?:좌석번호|좌석)\s*[:：]?\s*(\d{1,3}\s*[A-D])(?:\s|$)/i
                );
                const ticketCountText = normalized(
                  card.querySelector('.my-ticket__trn-ticket-ticket-num .data')?.innerText
                );
                const groupText = normalized(
                  card.querySelector('.tck_group-count')?.innerText
                );
                const passengerMatch = ticketCountText.match(/^(\d{1,2})(?:\s*(?:명|매|석))?$/)
                  || groupText.match(/(\d{1,2})\s*(?:명|매|석)/);
                const routeText = normalized(
                  journey.querySelector('.route_wrap')?.innerText
                );
                const seatClass = routeText.includes('특실')
                  ? 'first'
                  : (routeText.includes('일반실') ? 'standard' : null);
                const cardText = normalized(card.innerText);
                const transferred = (
                  card.querySelector('.tit_wrap .gift') !== null
                  || /받은\s*승차권/.test(cardText)
                  || /(?:전송|전달)\s*(?:받은|된)\s*승차권/.test(cardText)
                  || /승차권을\s*(?:전송|전달)\s*받/.test(cardText)
                );
                return {
                  serviceDate: serviceDate(card.querySelector('.tit_wrap .date')?.innerText),
                  trainNumber: trainMatch ? trainMatch[0] : null,
                  origin: station(journey.querySelector('.top_box .st_box strong.name')),
                  destination: station(journey.querySelector('.top_box .en_box strong.name')),
                  departureTime: serviceTime(
                    journey.querySelector('.top_box .st_box strong.time')?.innerText
                  ),
                  arrivalTime: serviceTime(
                    journey.querySelector('.top_box .en_box strong.time')?.innerText
                  ),
                  seatClass,
                  passengerCount: passengerMatch ? Number(passengerMatch[1]) : null,
                  carNumber: carMatch ? carMatch[1] : null,
                  seatNumber: seatMatch ? seatMatch[1].replace(/\s+/g, '').toUpperCase() : null,
                  returned: /반환\s*완료/.test(cardText),
                  operationStopped: /운행\s*중지/.test(cardText),
                  transferred,
                };
              });
              const pageText = document.body?.innerText || '';
              const protectionDetected = (
                /code\s*:?\s*-?\s*(?:8002|8003|1405)|macro_err1|captcha|netfunnel/i.test(
                  pageText
                )
                || /비정상\s*접근|미허가\s*도구/i.test(pageText)
              );
              return {
                url: window.location.href,
                cardCount: cards.length,
                emptyStateVisible: Array.from(document.querySelectorAll(
                  '.tck_confirm_no-data .wrapTop .tit'
                )).filter(visible).some((item) => (
                  normalized(item.innerText) === '발권하신 승차권이 없습니다.'
                )),
                protectionDetected,
                tickets,
              };
            })()
        """
        response = await self._execute_script(script, return_by_value=True)
        return _issued_ticket_snapshot_from_script_response(
            response,
            network_responses=tuple(self._network_responses()),
        )

    async def reservation_list_snapshot(self) -> PydollReservationListSnapshot:
        """Read every visible reservation card through a redacted completeness boundary."""

        script = r"""
            (() => {
              const normalized = (value) => (value || '').replace(/\s+/g, ' ').trim();
              const visible = (element) => {
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const identityBearing = (element) => {
                const text = normalized(element?.innerText);
                const controls = Array.from(element?.querySelectorAll?.('button,a') || [])
                  .filter(visible).map((item) => normalized(item.innerText));
                return /(?:→|->)/.test(text)
                  && /20\d{2}(?:년|[-./])/.test(text)
                  && (text.match(/(?<!\d)\d{1,2}:\d{2}(?!\d)/g) || []).length >= 2
                  && (
                    /(?:KTX(?:-[가-힣A-Za-z]+)?|ITX-[가-힣A-Za-z]+|새마을|무궁화|누리로)\s*0*\d{1,5}/i
                      .test(text)
                    || ['예약취소', '예약변경', '결제/발권']
                      .some((label) => controls.includes(label))
                  );
              };
              const allIdentityElements = Array.from(document.querySelectorAll(
                '.tck_info-wrap, li, article, [role="listitem"], section, div'
              )).filter(visible).filter(identityBearing);
              const seeds = allIdentityElements.filter((element) => (
                !allIdentityElements.some((candidate) => (
                  candidate !== element && element.contains(candidate)
                ))
              ));
              const cards = seeds.map((seed) => {
                let current = seed;
                for (let depth = 0; current?.parentElement && depth < 9; depth += 1) {
                  const parent = current.parentElement;
                  const containedSeedCount = seeds.filter((candidate) => (
                    parent.contains(candidate)
                  )).length;
                  if (containedSeedCount !== 1) break;
                  const controls = Array.from(parent.querySelectorAll('button,a'))
                    .filter(visible).map((item) => normalized(item.innerText));
                  if (identityBearing(parent) && ['예약취소', '예약변경', '결제/발권']
                    .every((label) => controls.includes(label))) {
                    current = parent;
                    break;
                  }
                  if (!identityBearing(parent)) break;
                  current = parent;
                }
                return current;
              }).filter((card, index, values) => values.indexOf(card) === index);
              const redact = (card) => {
                const text = normalized(card.innerText);
                const train = text.match(
                  /(?:KTX(?:-[가-힣A-Za-z]+)?|ITX-[가-힣A-Za-z]+|새마을|무궁화|누리로)\s*0*\d{1,5}/i
                );
                const route = text.match(
                  /([가-힣A-Za-z0-9().-]{1,30})역?\s*(?:→|->)\s*([가-힣A-Za-z0-9().-]{1,30})역?/
                );
                const serviceDate = text.match(
                  /20\d{2}(?:년\s*\d{1,2}월\s*\d{1,2}일|[-./]\s*\d{1,2}[-./]\s*\d{1,2})/
                );
                const times = text.match(/(?<!\d)\d{1,2}:\d{2}(?!\d)/g) || [];
                const passenger = text.match(/\d{1,2}\s*(?:명|매)/);
                if (!train || !route || !serviceDate || times.length < 2 || !passenger) {
                  return null;
                }
                const controls = Array.from(card.querySelectorAll('button,a'))
                  .filter(visible).map((item) => normalized(item.innerText));
                const actions = ['예약취소', '예약변경', '결제/발권']
                  .filter((label) => controls.includes(label));
                const deadline = text.match(
                  /결제\s*(?:기한|마감)\s*[:：]?\s*20\d{2}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2}일?(?:\s*[.]\s*|\s+)\d{1,2}:\d{2}/
                );
                return [
                  train[0], `${route[1]} → ${route[2]}`, serviceDate[0],
                  `${times[0].trim()} → ${times[1].trim()}`, passenger[0],
                  ...actions, ...(deadline ? [deadline[0]] : []),
                ].join(' ');
              };
              const pageText = document.body?.innerText || '';
              const visibleTexts = Array.from(document.querySelectorAll('h1,h2,h3,p,div,li'))
                .filter(visible).map((item) => normalized(item.innerText));
              return {
                url: window.location.href,
                cardCount: cards.length,
                rows: cards.map(redact),
                pageMarkerVisible: visibleTexts.some((text) => (
                  /예약\s*승차권\s*조회|승차권\s*예약\s*조회/.test(text)
                )),
                explicitEmptyVisible: visibleTexts.some((text) => (
                  /예약(?:된|\s*승차권|\s*내역).{0,20}(?:없어요|없습니다|없음)|조회된\s*예약.{0,20}없/.test(text)
                )),
                loadingVisible: Array.from(document.querySelectorAll(
                  '[aria-busy="true"],[role="progressbar"],.loading,.spinner,.skeleton'
                )).some(visible),
                protectionDetected: (
                  /code\s*:?\s*-?\s*(?:8002|8003|1405)|macro_err1|captcha|netfunnel/i
                    .test(pageText)
                ),
              };
            })()
        """
        response = await self._execute_script(script, return_by_value=True)
        return _reservation_list_snapshot_from_script_response(
            response,
            network_responses=tuple(self._network_responses()),
        )


_ISSUED_TICKET_FIELDS = frozenset(
    {
        "serviceDate",
        "trainNumber",
        "origin",
        "destination",
        "departureTime",
        "arrivalTime",
        "seatClass",
        "passengerCount",
        "carNumber",
        "seatNumber",
        "returned",
        "operationStopped",
        "transferred",
    }
)
_REDACTED_RESERVATION_ROW = re.compile(
    r"^(?:KTX(?:-[가-힣A-Za-z]+)?|ITX-[가-힣A-Za-z]+|새마을|무궁화|누리로)\s*0*\d{1,5} "
    r"[가-힣A-Za-z0-9().-]{1,30}\s*→\s*[가-힣A-Za-z0-9().-]{1,30} "
    r"20\d{2}(?:년\s*\d{1,2}월\s*\d{1,2}일|[-./]\s*\d{1,2}[-./]\s*\d{1,2}) "
    r"\d{1,2}:\d{2}\s*→\s*\d{1,2}:\d{2} \d{1,2}\s*(?:명|매)"
    r"(?: 예약취소)?(?: 예약변경)?(?: 결제/발권)?"
    r"(?: 결제\s*(?:기한|마감)\s*[:：]?\s*20\d{2}[-./년]\s*\d{1,2}"
    r"[-./월]\s*\d{1,2}일?(?:\s*[.]\s*|\s+)\d{1,2}:\d{2})?$"
)


def _reservation_list_snapshot_from_script_response(
    response: object,
    *,
    network_responses: tuple[tuple[int, str], ...],
) -> PydollReservationListSnapshot:
    if not isinstance(response, Mapping):
        raise TypeError("reservation-list script response is not a mapping")
    try:
        value = response["result"]["result"]["value"]
    except (KeyError, TypeError) as error:
        raise TypeError("reservation-list script response has an invalid shape") from error
    if not isinstance(value, Mapping) or set(value) != {
        "url",
        "cardCount",
        "rows",
        "pageMarkerVisible",
        "explicitEmptyVisible",
        "loadingVisible",
        "protectionDetected",
    }:
        raise TypeError("reservation-list script value has an invalid shape")
    url = value.get("url")
    card_count = value.get("cardCount")
    raw_rows = value.get("rows")
    flags = tuple(
        value.get(name)
        for name in (
            "pageMarkerVisible",
            "explicitEmptyVisible",
            "loadingVisible",
            "protectionDetected",
        )
    )
    if not isinstance(url, str) or len(url) > 2048:
        raise TypeError("reservation-list URL is invalid")
    if isinstance(card_count, bool) or not isinstance(card_count, int) or card_count < 0:
        raise TypeError("reservation-list card count is invalid")
    if not isinstance(raw_rows, list) or len(raw_rows) != card_count:
        raise TypeError("reservation-list rows are invalid")
    if any(not isinstance(flag, bool) for flag in flags):
        raise TypeError("reservation-list page flags are invalid")
    rows = tuple(
        row
        for row in raw_rows
        if isinstance(row, str)
        and len(row) <= 500
        and _REDACTED_RESERVATION_ROW.fullmatch(row) is not None
    )
    malformed_card_count = card_count - len(rows)
    page_marker_visible, explicit_empty_visible, loading_visible, protection_detected = flags
    assert isinstance(page_marker_visible, bool)
    assert isinstance(explicit_empty_visible, bool)
    assert isinstance(loading_visible, bool)
    assert isinstance(protection_detected, bool)
    return PydollReservationListSnapshot(
        url=url,
        reservation_rows=rows,
        rendered_card_count=card_count,
        malformed_card_count=malformed_card_count,
        page_marker_visible=page_marker_visible,
        explicit_empty_visible=explicit_empty_visible,
        loading_visible=loading_visible,
        protection_detected=protection_detected,
        network_responses=network_responses,
    )


def _issued_ticket_snapshot_from_script_response(
    response: object,
    *,
    network_responses: tuple[tuple[int, str], ...],
) -> PydollIssuedTicketListSnapshot:
    if not isinstance(response, Mapping):
        raise TypeError("issued-ticket script response is not a mapping")
    outer = response.get("result")
    if not isinstance(outer, Mapping):
        raise TypeError("issued-ticket script result is not a mapping")
    inner = outer.get("result")
    if not isinstance(inner, Mapping):
        raise TypeError("issued-ticket script inner result is not a mapping")
    value = inner.get("value")
    if not isinstance(value, Mapping) or set(value) != {
        "url",
        "cardCount",
        "emptyStateVisible",
        "protectionDetected",
        "tickets",
    }:
        raise TypeError("issued-ticket script value has an invalid shape")

    url = value.get("url")
    card_count = value.get("cardCount")
    empty_state_visible = value.get("emptyStateVisible")
    protection_detected = value.get("protectionDetected")
    raw_tickets = value.get("tickets")
    if not isinstance(url, str) or len(url) > 2048:
        raise TypeError("issued-ticket URL is invalid")
    if isinstance(card_count, bool) or not isinstance(card_count, int) or card_count < 0:
        raise TypeError("issued-ticket card count is invalid")
    if not isinstance(empty_state_visible, bool) or not isinstance(protection_detected, bool):
        raise TypeError("issued-ticket page flags are invalid")
    if not isinstance(raw_tickets, list) or len(raw_tickets) != card_count:
        raise TypeError("issued-ticket cards are invalid")

    tickets: list[PydollIssuedTicketSummary] = []
    malformed_card_count = 0
    for item in raw_tickets:
        parsed = _issued_ticket_from_value(item)
        if parsed is None:
            malformed_card_count += 1
        else:
            tickets.append(parsed)
    if malformed_card_count:
        tickets = []
        malformed_card_count = card_count
    return PydollIssuedTicketListSnapshot(
        url=url,
        tickets=tuple(tickets),
        rendered_card_count=card_count,
        malformed_card_count=malformed_card_count,
        empty_state_visible=empty_state_visible,
        protection_detected=protection_detected,
        network_responses=network_responses,
    )


def _issued_ticket_from_value(value: object) -> PydollIssuedTicketSummary | None:
    if not isinstance(value, Mapping) or set(value) != _ISSUED_TICKET_FIELDS:
        return None
    string_fields = {
        name: value.get(name)
        for name in (
            "serviceDate",
            "trainNumber",
            "origin",
            "destination",
            "departureTime",
            "arrivalTime",
            "seatClass",
            "carNumber",
            "seatNumber",
        )
    }
    if any(not isinstance(field, str) for field in string_fields.values()):
        return None
    passenger_count = value.get("passengerCount")
    flags = (
        value.get("returned"),
        value.get("operationStopped"),
        value.get("transferred"),
    )
    if (
        isinstance(passenger_count, bool)
        or not isinstance(passenger_count, int)
        or any(not isinstance(flag, bool) for flag in flags)
    ):
        return None
    try:
        service_date_text = string_fields["serviceDate"]
        departure_time_text = string_fields["departureTime"]
        arrival_time_text = string_fields["arrivalTime"]
        train_number = string_fields["trainNumber"]
        origin = string_fields["origin"]
        destination = string_fields["destination"]
        seat_class = string_fields["seatClass"]
        car_number = string_fields["carNumber"]
        seat_number = string_fields["seatNumber"]
        assert isinstance(service_date_text, str)
        assert isinstance(departure_time_text, str)
        assert isinstance(arrival_time_text, str)
        assert isinstance(train_number, str)
        assert isinstance(origin, str)
        assert isinstance(destination, str)
        assert isinstance(seat_class, str)
        assert isinstance(car_number, str)
        assert isinstance(seat_number, str)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", service_date_text) is None:
            return None
        if (
            re.fullmatch(r"\d{2}:\d{2}", departure_time_text) is None
            or re.fullmatch(r"\d{2}:\d{2}", arrival_time_text) is None
        ):
            return None
        if re.fullmatch(r"\d{1,5}", train_number) is None:
            return None
        if seat_class not in {"standard", "first"}:
            return None
        returned, operation_stopped, transferred = flags
        if (
            not isinstance(returned, bool)
            or not isinstance(operation_stopped, bool)
            or not isinstance(transferred, bool)
        ):
            return None
        return PydollIssuedTicketSummary(
            service_date=date.fromisoformat(service_date_text),
            train_number=train_number,
            origin=origin.strip(),
            destination=destination.strip(),
            departure_time=clock_time.fromisoformat(departure_time_text),
            arrival_time=clock_time.fromisoformat(arrival_time_text),
            seat_class=cast(Literal["standard", "first"], seat_class),
            passenger_count=passenger_count,
            car_number=car_number.strip(),
            seat_number=seat_number.strip().upper(),
            returned=returned,
            operation_stopped=operation_stopped,
            transferred=transferred,
        )
    except (TypeError, ValueError):
        return None
