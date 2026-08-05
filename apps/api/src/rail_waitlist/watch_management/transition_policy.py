from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from ..domain import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, WatchStatus


class NextCheckPolicy(StrEnum):
    PRESERVE = "preserve"
    CLEAR = "clear"
    TRANSITION_AT_IF_SEAT_MONITORING = "transition_at_if_seat_monitoring"


@dataclass(frozen=True, slots=True)
class NoOpWatchTransition:
    status: WatchStatus
    kind: Literal["no_op"] = field(init=False, default="no_op")


@dataclass(frozen=True, slots=True)
class RejectedWatchTransition:
    previous_status: WatchStatus
    target_status: WatchStatus
    detail: str
    kind: Literal["rejected"] = field(init=False, default="rejected")


@dataclass(frozen=True, slots=True)
class AllowedWatchTransition:
    previous_status: WatchStatus
    target_status: WatchStatus
    next_check_policy: NextCheckPolicy
    clear_cooldown: bool
    kind: Literal["allowed"] = field(init=False, default="allowed")


type WatchTransitionDecision = (
    NoOpWatchTransition | RejectedWatchTransition | AllowedWatchTransition
)


@dataclass(frozen=True, slots=True)
class WatchTransitionIdentity:
    transition_at: datetime
    reason: str
    transition_token: str
    status_event_dedupe_key: str


def decide_watch_transition(
    current_status: WatchStatus,
    target_status: WatchStatus,
) -> WatchTransitionDecision:
    if current_status is target_status:
        return NoOpWatchTransition(status=current_status)
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        return RejectedWatchTransition(
            previous_status=current_status,
            target_status=target_status,
            detail=f"cannot transition {current_status.value} to {target_status.value}",
        )

    if target_status is WatchStatus.SCHEDULED:
        next_check_policy = NextCheckPolicy.TRANSITION_AT_IF_SEAT_MONITORING
    elif target_status is WatchStatus.PAUSED or target_status in TERMINAL_STATUSES:
        next_check_policy = NextCheckPolicy.CLEAR
    else:
        next_check_policy = NextCheckPolicy.PRESERVE
    return AllowedWatchTransition(
        previous_status=current_status,
        target_status=target_status,
        next_check_policy=next_check_policy,
        clear_cooldown=target_status is WatchStatus.SCHEDULED,
    )


def build_watch_transition_identity(
    transition: AllowedWatchTransition,
    *,
    watch_id: str,
    transition_at: datetime,
    reason: str | None,
) -> WatchTransitionIdentity:
    resolved_reason = (reason or f"transition_to_{transition.target_status.value}")[:160]
    transition_token = (
        f"{transition.previous_status.value}:"
        f"{transition.target_status.value}:"
        f"{transition_at.isoformat()}"
    )
    return WatchTransitionIdentity(
        transition_at=transition_at,
        reason=resolved_reason,
        transition_token=transition_token,
        status_event_dedupe_key=f"watch:{watch_id}:transition:{transition_token}",
    )
