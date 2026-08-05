"""Drive one KORAIL timetable search form and its read-only result DOM."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from .korail_browser_automation import BrowserSourceUnavailable, protection_trigger_from_text
from .korail_pydoll_contracts import PydollPageSnapshot, PydollSeatBox, PydollTrainRow


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
            if trigger is not None or last.rows or last.network_responses:
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
        current = self._deduplicate_snapshot(snapshot)
        accumulated = current
        for _ in range(max(0, max_actions)):
            if self._snapshot_requires_expansion_stop(current):
                break
            try:
                more = await self._port._find_exact_visible("a", "더보기")
            except LookupError:
                break
            previous_rows = {self._train_row_identity(row) for row in current.rows}
            await more.click()
            candidate, progressed = await self._port._wait_for_result_growth(previous_rows)
            accumulated = self._merge_page_snapshots(accumulated, candidate)
            current = candidate
            if self._snapshot_requires_expansion_stop(candidate) or not progressed:
                break
        return accumulated

    async def wait_for_result_growth(
        self,
        previous_rows: set[tuple[str, str, str]],
    ) -> tuple[PydollPageSnapshot, bool]:
        deadline = self._monotonic() + min(self._timeout_seconds, 10)
        last = await self._port._snapshot()
        while self._monotonic() < deadline:
            if self._snapshot_requires_expansion_stop(last):
                return last, False
            current_rows = {self._train_row_identity(row) for row in last.rows}
            if current_rows - previous_rows:
                return last, True
            await self._sleep(0.25)
            last = await self._port._snapshot()
        return last, False

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
            network_responses=tuple(sorted(self._network_responses())),
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
