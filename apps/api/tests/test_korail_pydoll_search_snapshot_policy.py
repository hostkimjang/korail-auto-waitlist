from __future__ import annotations

import inspect
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_pydoll_browser as legacy
from rail_waitlist.korail_sidecar.pydoll import search_snapshot_policy as owner
from rail_waitlist.korail_sidecar.pydoll.page_contracts import (
    PydollPageSnapshot,
    PydollSeatBox,
    PydollTrainRow,
)
from rail_waitlist.korail_sidecar.pydoll.search_driver import PydollSearchDomDriver

API_ROOT = Path(__file__).resolve().parents[1]
ALIASES = {
    "_train_row_identity": owner.train_row_identity,
    "_deduplicate_snapshot": owner.deduplicate_search_snapshot,
    "_merge_page_snapshots": owner.merge_search_snapshots,
    "_snapshot_requires_expansion_stop": owner.snapshot_requires_expansion_stop,
}


def _row(
    number: str,
    *,
    kind: str = "KTX",
    route: str = "서울 → 부산(14:00 ~ 16:30)",
    seat_text: str = "일반실 매진",
    full_text: str = "old",
) -> PydollTrainRow:
    return PydollTrainRow(
        kind_text=kind,
        train_number=number,
        route_text=route,
        seats=(PydollSeatBox(seat_text, frozenset()),),
        full_text=full_text,
    )


def test_legacy_private_helpers_are_exact_aliases_and_old_pickles_restore() -> None:
    expected_parameters = {
        "_train_row_identity": ("row",),
        "_deduplicate_snapshot": ("snapshot",),
        "_merge_page_snapshots": ("accumulated", "candidate"),
        "_snapshot_requires_expansion_stop": ("snapshot",),
    }
    for legacy_name, canonical in ALIASES.items():
        assert getattr(legacy, legacy_name) is canonical
        assert canonical.__module__ == owner.__name__
        assert tuple(inspect.signature(canonical).parameters) == expected_parameters[legacy_name]
        payload = f"crail_waitlist.korail_pydoll_browser\n{legacy_name}\n.".encode()
        assert pickle.loads(payload) is canonical


def test_owner_import_is_passive_before_the_browser_facade_loads() -> None:
    script = """
import sys
from rail_waitlist.korail_sidecar.pydoll import search_snapshot_policy
print('rail_waitlist.korail_pydoll_browser' in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "False"


def test_session_keeps_constructor_time_policy_callback_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks = {
        "_train_row_identity": lambda row: (row.kind_text, row.train_number, row.route_text),
        "_deduplicate_snapshot": lambda snapshot: snapshot,
        "_merge_page_snapshots": lambda _accumulated, candidate: candidate,
        "_snapshot_requires_expansion_stop": lambda _snapshot: False,
    }
    for name, callback in callbacks.items():
        monkeypatch.setattr(legacy, name, callback)

    session = legacy._PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )

    driver = session._search_driver
    for name, callback in callbacks.items():
        assert getattr(driver, name) is callback
    parameters = inspect.signature(PydollSearchDomDriver.__init__).parameters
    assert tuple(parameters)[-7:-3] == (
        "deduplicate_snapshot",
        "merge_page_snapshots",
        "snapshot_requires_expansion_stop",
        "train_row_identity",
    )


def test_snapshot_merge_preserves_order_last_wins_and_latest_envelope() -> None:
    old = _row(" KTX   043 ", seat_text="일반실 매진", full_text="old")
    other = _row("47", route="서울 → 부산(15:00 ~ 17:30)")
    updated = _row("KTX 043", seat_text="일반실 예약 가능", full_text="new")
    latest = _row("KTX 043", seat_text="일반실 좌석 얼마 없음", full_text="latest")
    added = _row("49", route="서울 → 부산(16:00 ~ 18:30)")

    accumulated = PydollPageSnapshot(
        body_text="first",
        rows=(old, other),
        protection_texts=("surface-a",),
        network_responses=((429, "fetch"),),
        url="https://first.invalid",
        title="first",
        reservation_rows=("first",),
    )
    candidate = PydollPageSnapshot(
        body_text="latest body",
        rows=(updated, added, latest),
        protection_texts=("surface-a", "surface-b"),
        network_responses=((403, "document"),),
        url="https://latest.invalid",
        title="latest",
        reservation_rows=("latest",),
    )

    merged = owner.merge_search_snapshots(accumulated, candidate)

    assert owner.train_row_identity(old) == owner.train_row_identity(updated)
    assert merged.rows == (latest, other, added)
    assert merged.body_text == "latest body"
    assert merged.protection_texts == ("surface-a", "surface-b")
    assert merged.network_responses == ((429, "fetch"), (403, "document"))
    assert (merged.url, merged.title, merged.reservation_rows) == (
        "https://latest.invalid",
        "latest",
        ("latest",),
    )
    deduplicated = owner.deduplicate_search_snapshot(candidate)
    assert deduplicated.rows == (latest, added)
    assert (deduplicated.url, deduplicated.title, deduplicated.reservation_rows) == (
        "https://latest.invalid",
        "latest",
        ("latest",),
    )


def test_expansion_transition_merges_latest_row_before_stopping_repeated_window() -> None:
    original = _row("43", full_text="old")
    second = _row("47", route="서울 → 부산(15:00 ~ 17:30)")
    updated = _row("43", seat_text="일반실 예약 가능", full_text="latest")
    state = owner.begin_search_expansion(
        PydollPageSnapshot("A", (original,)),
        deduplicate_snapshot=owner.deduplicate_search_snapshot,
        row_identity=owner.train_row_identity,
    )

    continued = owner.advance_search_expansion(
        state,
        PydollPageSnapshot("B", (second,)),
        observed_growth=True,
        merge_snapshots=owner.merge_search_snapshots,
        row_identity=owner.train_row_identity,
        snapshot_requires_stop=owner.snapshot_requires_expansion_stop,
    )
    stopped = owner.advance_search_expansion(
        continued.state,
        PydollPageSnapshot("A latest", (updated,)),
        observed_growth=False,
        merge_snapshots=owner.merge_search_snapshots,
        row_identity=owner.train_row_identity,
        snapshot_requires_stop=owner.snapshot_requires_expansion_stop,
    )

    assert continued.stop_reason is None
    assert stopped.stop_reason == "repeated_window"
    assert stopped.state.accumulated.rows == (updated, second)


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (PydollPageSnapshot("정상 결과", (_row("43"),)), False),
        (PydollPageSnapshot("CODE -8002", (_row("43"),)), True),
        (PydollPageSnapshot("비정상 접근 안내", (_row("43"),)), False),
        (PydollPageSnapshot("비정상 접근 안내", ()), True),
        (
            PydollPageSnapshot(
                "비정상 접근 안내",
                (_row("43"),),
                protection_texts=("비정상 접근",),
            ),
            True,
        ),
        (
            PydollPageSnapshot(
                "정상 결과",
                (_row("43"),),
                network_responses=((429, "fetch"),),
            ),
            True,
        ),
        (
            PydollPageSnapshot(
                "정상 결과",
                (_row("43"),),
                network_responses=((429, "font"), (403, "xhr")),
            ),
            False,
        ),
    ],
)
def test_expansion_stop_policy_preserves_protection_evidence_rules(
    snapshot: PydollPageSnapshot,
    expected: bool,
) -> None:
    assert owner.snapshot_requires_expansion_stop(snapshot) is expected
