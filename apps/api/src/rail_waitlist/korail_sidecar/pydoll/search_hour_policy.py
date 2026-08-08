"""Classify KORAIL search-hour picker state without browser I/O."""

from __future__ import annotations

from .search_driver import SearchControlState, SearchHourCandidate

__all__ = (
    "control_state_log_value",
    "current_hour_window",
    "has_disabled_class",
    "hour_window_signature",
    "is_exact_hour_catalog",
    "is_exact_selected_hour",
    "is_soft_adjacent_hour",
    "is_soft_aria_hour",
    "is_soft_dom_hour",
)


def has_disabled_class(tokens: tuple[str, ...]) -> bool:
    return bool({"disabled", "off", "slick-disabled"} & set(tokens))


def control_state_log_value(state: SearchControlState) -> tuple[object, ...]:
    return (
        state.enabled,
        state.aria_disabled,
        state.disabled_attribute,
        state.classes,
        state.container_classes,
        state.slide_classes,
        state.read_error,
    )


def current_hour_window(candidates: list[SearchHourCandidate]) -> list[SearchHourCandidate]:
    current_indexes = [
        index
        for index, candidate in enumerate(candidates)
        if "slick-current" in candidate.state.slide_classes
    ]
    if not current_indexes or current_indexes != list(
        range(current_indexes[0], current_indexes[-1] + 1)
    ):
        return []
    current_window: list[SearchHourCandidate] = []
    for candidate in candidates[current_indexes[0] :]:
        if "slick-current" not in candidate.state.slide_classes and not candidate.state.enabled:
            break
        current_window.append(candidate)
    return current_window


def hour_window_signature(candidates: list[SearchHourCandidate]) -> tuple[object, ...]:
    return tuple(
        (
            candidate.hour,
            control_state_log_value(candidate.state),
        )
        for candidate in candidates
    )


def is_soft_aria_hour(candidate: SearchHourCandidate) -> bool:
    state = candidate.state
    return (
        state.aria_disabled == "true"
        and not state.disabled_attribute
        and not has_disabled_class(state.classes)
        and not has_disabled_class(state.container_classes)
        and not has_disabled_class(state.slide_classes)
        and "slick-active" in state.slide_classes
        and not state.read_error
    )


def is_soft_dom_hour(candidate: SearchHourCandidate) -> bool:
    state = candidate.state
    return (
        state.aria_disabled == "true"
        and not state.disabled_attribute
        and not has_disabled_class(state.classes)
        and not has_disabled_class(state.container_classes)
        and not has_disabled_class(state.slide_classes)
        and "slick-slide" in state.slide_classes
        and "slick-cloned" not in state.slide_classes
        and not state.read_error
    )


def is_exact_hour_catalog(candidates: list[SearchHourCandidate]) -> bool:
    return len(candidates) == 24 and sorted(candidate.hour for candidate in candidates) == list(
        range(24)
    )


def is_soft_adjacent_hour(
    candidates: list[SearchHourCandidate],
    current_window: list[SearchHourCandidate],
    target: SearchHourCandidate,
) -> bool:
    if len(candidates) != 10 or len(current_window) != 5 or target in current_window:
        return False
    adjacent = candidates[len(current_window) :]
    return (
        len(adjacent) == 5
        and all(candidate.state.enabled for candidate in current_window)
        and all(is_soft_aria_hour(candidate) for candidate in adjacent)
        and target in adjacent
    )


def is_exact_selected_hour(
    candidates: list[SearchHourCandidate],
    target_elements: list[SearchHourCandidate],
    *,
    target_date_is_selected: bool,
    pre_picker_hour_matches: bool,
) -> bool:
    if not target_date_is_selected or not pre_picker_hour_matches:
        return False
    if len(target_elements) != 1 or len(candidates) < 2:
        return False
    target_state = target_elements[0].state
    if (
        target_state.aria_disabled != "true"
        or target_state.disabled_attribute
        or target_state.classes
        or has_disabled_class(target_state.container_classes)
        or has_disabled_class(target_state.slide_classes)
        or target_state.read_error
    ):
        return False
    target_hour = target_elements[0].hour
    return all(candidate.state.enabled for candidate in candidates if candidate.hour != target_hour)
