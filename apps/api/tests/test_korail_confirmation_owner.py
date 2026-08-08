from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

import rail_waitlist.korail_reservation_confirmation as legacy
import rail_waitlist.reservations.provider_confirmation.korail as canonical
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

API_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SYMBOLS = (
    "KORAIL_CONFIRMATION_SOURCE",
    "KORAIL_RESERVATION_HANDOFF_URL",
    "KORAIL_RESERVATION_LIST_SOURCE",
    "KorailSameSessionDetailConfirmationAdapter",
    "KorailSameSessionDetailEvidence",
    "KorailSameSessionDetailProbe",
    "normalize_korail_same_session_detail",
)
EVIDENCE_FIELDS = [
    "observed_at",
    "credential_version",
    "exact_identity_matched",
    "payment_pending_markers_present",
    "seat_class_matched",
    "passenger_count_matched",
    "seat_class_match_required",
    "official_list_read_completed",
    "official_list_target_absent",
    "auth_required",
    "provider_blocked",
    "payment_deadline",
    "source",
]
OBSERVED_AT = datetime(2030, 8, 1, 3, tzinfo=UTC)
PAYMENT_DEADLINE = datetime(2030, 8, 1, 4, tzinfo=UTC)


def _target(
    provider: Provider = Provider.KORAIL,
    *,
    credential_version: int = 7,
) -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-1",
        candidate_id="candidate-1",
        provider=provider,
        train_number="001",
        origin="대전",
        destination="서울",
        departure_at=datetime(2030, 8, 1, 3, tzinfo=UTC),
        arrival_at=datetime(2030, 8, 1, 4, tzinfo=UTC),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        credential_version=credential_version,
    )


def _evidence(**overrides: object) -> canonical.KorailSameSessionDetailEvidence:
    values: dict[str, object] = {
        "observed_at": OBSERVED_AT,
        "credential_version": 7,
        "exact_identity_matched": False,
        "payment_pending_markers_present": False,
    }
    values.update(overrides)
    return canonical.KorailSameSessionDetailEvidence(**values)


def test_korail_confirmation_legacy_facade_has_exact_canonical_exports() -> None:
    assert legacy.__all__ == PUBLIC_SYMBOLS
    assert all(getattr(legacy, name) is getattr(canonical, name) for name in PUBLIC_SYMBOLS)
    assert canonical.KorailSameSessionDetailEvidence.__module__ == canonical.__name__
    assert canonical.KorailSameSessionDetailProbe.__module__ == canonical.__name__
    assert canonical.KorailSameSessionDetailConfirmationAdapter.__module__ == canonical.__name__
    assert canonical.normalize_korail_same_session_detail.__module__ == canonical.__name__
    assert canonical.KORAIL_CONFIRMATION_SOURCE == "korail-same-session-detail"
    assert canonical.KORAIL_RESERVATION_LIST_SOURCE == "korail-reservation-list"
    assert canonical.KORAIL_RESERVATION_HANDOFF_URL == (
        "https://www.korail.com/ticket/reservation/list"
    )


def test_korail_confirmation_dataclass_shapes_and_defaults_are_preserved() -> None:
    evidence_fields = fields(canonical.KorailSameSessionDetailEvidence)
    adapter_fields = fields(canonical.KorailSameSessionDetailConfirmationAdapter)

    assert [field.name for field in evidence_fields] == EVIDENCE_FIELDS
    assert [field.name for field in adapter_fields] == ["probe"]
    assert canonical.KorailSameSessionDetailEvidence.__dataclass_params__.frozen is True
    assert canonical.KorailSameSessionDetailConfirmationAdapter.__dataclass_params__.frozen is False
    assert tuple(canonical.KorailSameSessionDetailEvidence.__slots__) == tuple(EVIDENCE_FIELDS)
    assert canonical.KorailSameSessionDetailConfirmationAdapter.__slots__ == ("probe",)

    detail = _evidence()
    assert detail.seat_class_matched is False
    assert detail.passenger_count_matched is False
    assert detail.seat_class_match_required is True
    assert detail.official_list_read_completed is False
    assert detail.official_list_target_absent is False
    assert detail.auth_required is False
    assert detail.provider_blocked is False
    assert detail.payment_deadline is None
    assert detail.source == canonical.KORAIL_CONFIRMATION_SOURCE


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"observed_at": datetime(2030, 8, 1, 3)}, "observed_at must include a timezone"),
        (
            {"payment_deadline": datetime(2030, 8, 1, 4)},
            "payment_deadline must include a timezone",
        ),
        ({"source": "unknown"}, "unsupported KORAIL confirmation evidence source"),
        (
            {"official_list_target_absent": True},
            "official list target absence requires a completed official list read",
        ),
        (
            {
                "source": canonical.KORAIL_RESERVATION_LIST_SOURCE,
                "official_list_target_absent": True,
            },
            "official list target absence requires a completed official list read",
        ),
    ],
)
def test_korail_confirmation_evidence_preserves_existing_rejections(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _evidence(**overrides)


def test_korail_confirmation_evidence_does_not_gain_new_policy_validation() -> None:
    permissive = _evidence(
        credential_version=-1,
        exact_identity_matched=True,
        payment_pending_markers_present=True,
        seat_class_matched=True,
        passenger_count_matched=True,
        seat_class_match_required=False,
        official_list_read_completed=True,
        auth_required=True,
        provider_blocked=True,
        payment_deadline=datetime(2029, 1, 1, tzinfo=UTC),
    )

    assert permissive.auth_required is True
    assert permissive.provider_blocked is True
    assert permissive.payment_deadline < permissive.observed_at


def test_korail_confirmation_rejects_non_korail_before_evidence_flags() -> None:
    with pytest.raises(
        ValueError,
        match="KORAIL detail confirmation received a non-KORAIL target",
    ):
        canonical.normalize_korail_same_session_detail(
            _target(Provider.SRT),
            _evidence(provider_blocked=True, auth_required=True),
        )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            _evidence(
                provider_blocked=True,
                auth_required=True,
                exact_identity_matched=True,
                payment_pending_markers_present=True,
                seat_class_matched=True,
                passenger_count_matched=True,
            ),
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        ),
        (
            _evidence(
                auth_required=True,
                exact_identity_matched=True,
                payment_pending_markers_present=True,
                seat_class_matched=True,
                passenger_count_matched=True,
            ),
            ReservationConfirmationOutcome.AUTH_REQUIRED,
        ),
        (
            _evidence(
                credential_version=8,
                source=canonical.KORAIL_RESERVATION_LIST_SOURCE,
                official_list_read_completed=True,
                official_list_target_absent=True,
            ),
            ReservationConfirmationOutcome.INCONCLUSIVE,
        ),
        (
            _evidence(
                exact_identity_matched=True,
                payment_pending_markers_present=True,
                seat_class_matched=True,
                passenger_count_matched=True,
                payment_deadline=PAYMENT_DEADLINE,
            ),
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        ),
        (
            _evidence(
                source=canonical.KORAIL_RESERVATION_LIST_SOURCE,
                exact_identity_matched=True,
                payment_pending_markers_present=True,
                seat_class_match_required=False,
                passenger_count_matched=True,
            ),
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        ),
        (
            _evidence(
                source=canonical.KORAIL_RESERVATION_LIST_SOURCE,
                official_list_read_completed=True,
                official_list_target_absent=True,
            ),
            ReservationConfirmationOutcome.NOT_FOUND,
        ),
        (_evidence(), ReservationConfirmationOutcome.INCONCLUSIVE),
    ],
)
def test_korail_confirmation_preserves_fail_closed_precedence(
    evidence: canonical.KorailSameSessionDetailEvidence,
    expected: ReservationConfirmationOutcome,
) -> None:
    result = canonical.normalize_korail_same_session_detail(_target(), evidence)

    assert result.outcome is expected
    assert result.source == evidence.source
    assert result.observed_at is evidence.observed_at
    assert result.permits_automatic_reservation_retry is False
    if expected is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        assert result.official_handoff_url == canonical.KORAIL_RESERVATION_HANDOFF_URL
        assert result.payment_deadline is evidence.payment_deadline
    else:
        assert result.official_handoff_url is None
        assert result.payment_deadline is None


def test_korail_confirmation_prefers_positive_over_contradictory_list_absence() -> None:
    result = canonical.normalize_korail_same_session_detail(
        _target(),
        _evidence(
            source=canonical.KORAIL_RESERVATION_LIST_SOURCE,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_match_required=False,
            passenger_count_matched=True,
            official_list_read_completed=True,
            official_list_target_absent=True,
        ),
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED


def test_korail_confirmation_validates_the_exact_handoff_at_call_time(monkeypatch) -> None:
    calls: list[tuple[Provider, str]] = []

    def require_url(provider: Provider, value: str) -> str:
        calls.append((provider, value))
        return value

    monkeypatch.setattr(canonical, "require_official_handoff_url", require_url)
    evidence = _evidence(
        exact_identity_matched=True,
        payment_pending_markers_present=True,
        seat_class_matched=True,
        passenger_count_matched=True,
    )
    result = canonical.normalize_korail_same_session_detail(_target(), evidence)

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert calls == [(Provider.KORAIL, canonical.KORAIL_RESERVATION_HANDOFF_URL)]

    monkeypatch.setattr(canonical, "KORAIL_RESERVATION_HANDOFF_URL", "https://example.com/pay")
    with pytest.raises(ValueError, match="provider allowlist"):
        canonical.normalize_korail_same_session_detail(_target(), evidence)


class _Probe:
    def __init__(self, evidence: canonical.KorailSameSessionDetailEvidence) -> None:
        self.evidence = evidence
        self.targets: list[ReservationConfirmationTarget] = []

    async def read_detail(
        self,
        target: ReservationConfirmationTarget,
    ) -> canonical.KorailSameSessionDetailEvidence:
        self.targets.append(target)
        return self.evidence


async def test_korail_confirmation_adapter_uses_probe_once_and_current_normalizer(
    monkeypatch,
) -> None:
    target = _target()
    evidence = _evidence()
    probe = _Probe(evidence)
    expected = ReservationConfirmationResult(
        provider=Provider.KORAIL,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source=canonical.KORAIL_CONFIRMATION_SOURCE,
        observed_at=OBSERVED_AT,
    )
    calls: list[tuple[ReservationConfirmationTarget, object]] = []

    def normalize(
        received_target: ReservationConfirmationTarget,
        received_evidence: canonical.KorailSameSessionDetailEvidence,
    ) -> ReservationConfirmationResult:
        calls.append((received_target, received_evidence))
        return expected

    monkeypatch.setattr(canonical, "normalize_korail_same_session_detail", normalize)
    adapter = canonical.KorailSameSessionDetailConfirmationAdapter(probe)

    assert await adapter.confirm(target) is expected
    assert len(probe.targets) == 1
    assert probe.targets[0] is target
    assert len(calls) == 1
    assert calls[0][0] is target
    assert calls[0][1] is evidence


async def test_korail_confirmation_adapter_propagates_probe_errors() -> None:
    error = RuntimeError("probe failed")

    class FailingProbe:
        async def read_detail(
            self,
            _target: ReservationConfirmationTarget,
        ) -> canonical.KorailSameSessionDetailEvidence:
            raise error

    adapter = canonical.KorailSameSessionDetailConfirmationAdapter(FailingProbe())
    with pytest.raises(RuntimeError) as caught:
        await adapter.confirm(_target())
    assert caught.value is error


async def test_korail_confirmation_adapter_propagates_current_normalizer_error(
    monkeypatch,
) -> None:
    target = _target()
    evidence = _evidence()
    error = RuntimeError("normalizer failed")

    def fail(
        received_target: ReservationConfirmationTarget,
        received_evidence: canonical.KorailSameSessionDetailEvidence,
    ) -> ReservationConfirmationResult:
        assert received_target is target
        assert received_evidence is evidence
        raise error

    monkeypatch.setattr(canonical, "normalize_korail_same_session_detail", fail)
    adapter = canonical.KorailSameSessionDetailConfirmationAdapter(_Probe(evidence))

    with pytest.raises(RuntimeError) as caught:
        await adapter.confirm(target)
    assert caught.value is error


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "legacy-first", "reader-first", "browser-first", "sidecar-first"],
)
def test_korail_confirmation_import_orders_keep_one_owner(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.reservations.provider_confirmation.korail as canonical
elif sys.argv[1] == "legacy-first":
    import rail_waitlist.korail_reservation_confirmation as legacy
elif sys.argv[1] == "reader-first":
    from rail_waitlist import korail_pydoll_confirmation_reader as reader
elif sys.argv[1] == "browser-first":
    from rail_waitlist import korail_pydoll_browser as browser
else:
    from rail_waitlist.korail_sidecar import http

import rail_waitlist.korail_reservation_confirmation as legacy
import rail_waitlist.reservations.provider_confirmation.korail as canonical
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_confirmation_reader as reader
from rail_waitlist.korail_sidecar import http

print(json.dumps({
    "facade": all(getattr(legacy, name) is getattr(canonical, name) for name in legacy.__all__),
    "browser": browser.KorailSameSessionDetailEvidence is canonical.KorailSameSessionDetailEvidence,
    "reader": reader.KorailSameSessionDetailEvidence is canonical.KorailSameSessionDetailEvidence,
    "sidecar": (
        http.normalize_korail_same_session_detail
        is canonical.normalize_korail_same_session_detail
    ),
    "evidence_module": canonical.KorailSameSessionDetailEvidence.__module__,
    "function_module": canonical.normalize_korail_same_session_detail.__module__,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "browser": True,
        "evidence_module": "rail_waitlist.reservations.provider_confirmation.korail",
        "facade": True,
        "function_module": "rail_waitlist.reservations.provider_confirmation.korail",
        "reader": True,
        "sidecar": True,
    }
