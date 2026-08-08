"""Own pure KORAIL Pydoll search snapshot aggregation and expansion-stop policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from .page_contracts import PydollPageSnapshot, PydollTrainRow
from .page_safety import classify_pydoll_page_block

type TrainRowIdentity = tuple[str, str, str]
type RowWindow = frozenset[TrainRowIdentity]
type ExpansionStopReason = Literal["blocked", "stalled", "repeated_window"]
type SnapshotTransform = Callable[[PydollPageSnapshot], PydollPageSnapshot]
type SnapshotMerge = Callable[
    [PydollPageSnapshot, PydollPageSnapshot],
    PydollPageSnapshot,
]
type SnapshotStop = Callable[[PydollPageSnapshot], bool]
type RowIdentity = Callable[[PydollTrainRow], TrainRowIdentity]


@dataclass(frozen=True)
class SearchExpansionState:
    accumulated: PydollPageSnapshot
    seen_identities: frozenset[TrainRowIdentity]
    seen_windows: frozenset[RowWindow]


@dataclass(frozen=True)
class SearchExpansionTransition:
    state: SearchExpansionState
    stop_reason: ExpansionStopReason | None


def train_row_identity(row: PydollTrainRow) -> tuple[str, str, str]:
    return (
        " ".join(row.kind_text.split()),
        " ".join(row.train_number.split()),
        " ".join(row.route_text.split()),
    )


def deduplicate_search_snapshot(snapshot: PydollPageSnapshot) -> PydollPageSnapshot:
    return merge_search_snapshots(
        PydollPageSnapshot(body_text=snapshot.body_text, rows=()),
        snapshot,
    )


def merge_search_snapshots(
    accumulated: PydollPageSnapshot,
    candidate: PydollPageSnapshot,
) -> PydollPageSnapshot:
    rows = list(accumulated.rows)
    positions = {train_row_identity(row): index for index, row in enumerate(rows)}
    for row in candidate.rows:
        identity = train_row_identity(row)
        existing = positions.get(identity)
        if existing is None:
            positions[identity] = len(rows)
            rows.append(row)
        else:
            rows[existing] = row
    return replace(
        candidate,
        rows=tuple(rows),
        protection_texts=tuple(
            dict.fromkeys((*accumulated.protection_texts, *candidate.protection_texts))
        ),
        network_responses=tuple(
            dict.fromkeys((*accumulated.network_responses, *candidate.network_responses))
        ),
    )


def snapshot_requires_expansion_stop(snapshot: PydollPageSnapshot) -> bool:
    return classify_pydoll_page_block(snapshot) is not None


def begin_search_expansion(
    snapshot: PydollPageSnapshot,
    *,
    deduplicate_snapshot: SnapshotTransform,
    row_identity: RowIdentity,
) -> SearchExpansionState:
    current = deduplicate_snapshot(snapshot)
    current_identities = frozenset(row_identity(row) for row in current.rows)
    return SearchExpansionState(
        accumulated=current,
        seen_identities=current_identities,
        seen_windows=frozenset((current_identities,)),
    )


def advance_search_expansion(
    state: SearchExpansionState,
    candidate: PydollPageSnapshot,
    *,
    observed_growth: bool,
    merge_snapshots: SnapshotMerge,
    row_identity: RowIdentity,
    snapshot_requires_stop: SnapshotStop,
) -> SearchExpansionTransition:
    """Merge the latest window, then decide whether another DOM action is safe."""

    candidate_window = frozenset(row_identity(row) for row in candidate.rows)
    repeated_window = candidate_window in state.seen_windows
    new_identities = candidate_window - state.seen_identities
    accumulated = merge_snapshots(state.accumulated, candidate)
    next_state = SearchExpansionState(
        accumulated=accumulated,
        seen_identities=state.seen_identities.union(candidate_window),
        seen_windows=state.seen_windows.union((candidate_window,)),
    )
    if snapshot_requires_stop(accumulated):
        stop_reason: ExpansionStopReason | None = "blocked"
    elif repeated_window:
        stop_reason = "repeated_window"
    elif not observed_growth or not new_identities:
        stop_reason = "stalled"
    else:
        stop_reason = None
    return SearchExpansionTransition(next_state, stop_reason)
