from __future__ import annotations

from .schemas import TimetableItem


def apply_watch_registration_capability(
    items: list[TimetableItem], *, seat_monitoring_enabled: bool
) -> list[TimetableItem]:
    """Keep observed seat status while removing actions that cannot execute."""
    if seat_monitoring_enabled:
        return items

    result: list[TimetableItem] = []
    for item in items:
        seat_classes = []
        for seat in item.seat_classes:
            actions = [action for action in seat.actions if action.kind != "add_to_watch"]
            seat_classes.append(
                seat.model_copy(
                    update={
                        "actions": actions,
                        "registration_evidence_id": None,
                    }
                )
            )
        result.append(item.model_copy(update={"seat_classes": seat_classes}))
    return result
