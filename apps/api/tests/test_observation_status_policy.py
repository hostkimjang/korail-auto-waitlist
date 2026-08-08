from __future__ import annotations

import ast
from pathlib import Path

from rail_waitlist import services
from rail_waitlist.domain import SeatObservationStatus
from rail_waitlist.observations import group_application, recording_application, status_policy

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "rail_waitlist"


def test_observation_status_policy_preserves_the_exact_status_matrix() -> None:
    seat_found = frozenset(
        {
            SeatObservationStatus.AVAILABLE,
            SeatObservationStatus.LIMITED,
            SeatObservationStatus.STANDING_PLUS_SEAT,
        }
    )
    actionable = seat_found | {SeatObservationStatus.WAITLIST_AVAILABLE}

    assert status_policy.SEAT_FOUND_STATUSES == seat_found
    assert status_policy.ACTIONABLE_SEAT_STATUSES == actionable
    for status in SeatObservationStatus:
        assert (status in status_policy.SEAT_FOUND_STATUSES) is (status in seat_found)
        assert (status in status_policy.ACTIONABLE_SEAT_STATUSES) is (status in actionable)


def test_observation_status_consumers_share_the_exact_canonical_objects() -> None:
    for module in (recording_application, group_application, services):
        assert module.SEAT_FOUND_STATUSES is status_policy.SEAT_FOUND_STATUSES
        assert module.ACTIONABLE_SEAT_STATUSES is status_policy.ACTIONABLE_SEAT_STATUSES


def test_observation_status_consumers_do_not_redeclare_the_policy_sets() -> None:
    for relative_path in (
        "services.py",
        "observations/recording_application.py",
        "observations/group_application.py",
    ):
        module_path = SOURCE_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        assigned_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }

        assert "SEAT_FOUND_STATUSES" not in assigned_names
        assert "ACTIONABLE_SEAT_STATUSES" not in assigned_names
