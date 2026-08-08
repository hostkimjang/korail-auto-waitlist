from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

import rail_waitlist.korail_browser_seat_source as legacy_source
from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.korail_sidecar.contracts import KorailReserveOnceResult
from rail_waitlist.provider_account_management.contracts import ProviderCredentials
from rail_waitlist.provider_adapters import korail_browser_reservation_policy as policy
from rail_waitlist.reservations.contracts import ReservationRequest

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "provider_adapters" / "korail_browser_reservation_policy.py"
LEGACY_PATH = SOURCE_ROOT / "korail_browser_seat_source.py"
OWNER_MODULE = "rail_waitlist.provider_adapters.korail_browser_reservation_policy"
OWNER_DEFINITIONS = {
    "build_reservation_request",
    "project_reservation_failure",
    "project_reservation_result",
}
EXPECTED_ALL = (
    "build_reservation_request",
    "project_reservation_failure",
    "project_reservation_result",
)
OBSERVED_AT = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)


def _reservation_request() -> ReservationRequest:
    return ReservationRequest(
        provider=Provider.KORAIL,
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin="서울",
        destination="부산",
        train_number="0043",
        departure_at=datetime(2026, 8, 3, 6, 45, tzinfo=UTC),
        arrival_at=datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        candidate_id="candidate-policy",
        idempotency_key="reserve:candidate-policy",
    )


def _credentials() -> ProviderCredentials:
    return ProviderCredentials(
        login_method="phone",
        login_id="fixture-login-identifier",
        password="fixture-login-password",
        credential_version=7,
    )


def _safe_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _wire_result(
    outcome: str,
    *,
    reservation_clicked: bool = False,
    progress_times: tuple[datetime, ...] = (),
) -> KorailReserveOnceResult:
    padded = (*progress_times, None, None, None, None)
    return KorailReserveOnceResult(
        outcome=outcome,
        reason=f"fixture_{outcome}",
        seat_clicked=bool(progress_times) or reservation_clicked,
        reservation_clicked=reservation_clicked,
        session_ready_at=padded[0],
        target_rechecked_at=padded[1],
        seat_selected_at=padded[2],
        reservation_requested_at=padded[3],
    )


def test_reservation_policy_has_exact_pure_owner_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.level, node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assigned_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    expected_imports = {
        (0, "__future__"),
        (0, "collections.abc"),
        (0, "datetime"),
        (0, "pydantic"),
        (0, "zoneinfo"),
        (2, "domain"),
        (2, "korail_sidecar.contracts"),
        (2, "provider_account_management.contracts"),
        (2, "reservations.contracts"),
    }

    assert definitions == OWNER_DEFINITIONS
    assert policy.__all__ == EXPECTED_ALL
    assert "__all__" in assigned_names
    assert imports == expected_imports
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Name) and node.id.casefold() in {"logger", "transport"}
        for node in ast.walk(tree)
    )
    for name in OWNER_DEFINITIONS:
        assert getattr(policy, name).__module__ == OWNER_MODULE


def test_source_keeps_static_owner_identity_and_exact_legacy_surface() -> None:
    assert (
        legacy_source.KorailBrowserSeatSource._build_reservation_request
        is policy.build_reservation_request
    )
    assert (
        legacy_source.KorailBrowserSeatSource._project_reservation_failure
        is policy.project_reservation_failure
    )
    assert (
        legacy_source.KorailBrowserSeatSource._project_reservation_result
        is policy.project_reservation_result
    )
    assert len({name for name in vars(legacy_source) if not name.startswith("_")}) == 56
    assert (
        len(
            {
                name
                for name in vars(legacy_source)
                if name.startswith("_") and not name.startswith("__")
            }
        )
        == 10
    )
    assert not hasattr(legacy_source, "__all__")
    assert not hasattr(legacy_source, "_auth_policy_owner")
    assert not hasattr(legacy_source, "_observation_policy_owner")
    assert not hasattr(legacy_source, "_reservation_policy_owner")

    tree = ast.parse(LEGACY_PATH.read_text(encoding="utf-8"), filename=str(LEGACY_PATH))
    deleted_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Delete)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert deleted_names == {
        "_auth_policy_owner",
        "_observation_policy_owner",
        "_reservation_policy_owner",
        "_window_policy_owner",
    }


def test_supported_request_preserves_exact_kst_identity_class_and_credentials() -> None:
    credentials = _credentials()
    normalized_inputs: list[object] = []

    def normalize_train_number(value: object) -> str:
        normalized_inputs.append(value)
        return "43"

    for seat_class, expected_wire_class in (
        (SeatClass.STANDARD, "general"),
        (SeatClass.FIRST, "special"),
    ):
        request = _reservation_request().model_copy(update={"seat_class": seat_class})
        wire = policy.build_reservation_request(
            request,
            credentials,
            enabled=True,
            normalize_train_number=normalize_train_number,
        )

        assert wire is not None
        assert wire.origin == request.origin
        assert wire.destination == request.destination
        assert wire.travel_date.isoformat() == "2026-08-03"
        assert wire.train_number == "43"
        assert wire.train_type is None
        assert wire.departure_time == time(15, 45)
        assert wire.arrival_time == time(18, 30)
        assert wire.seat_class == expected_wire_class
        assert wire.credential.login_method == "phone"
        assert wire.credential.version == "7"
        assert _safe_digest(wire.credential.login_id.get_secret_value()) == _safe_digest(
            credentials.login_id
        )
        assert _safe_digest(wire.credential.password.get_secret_value()) == _safe_digest(
            credentials.password
        )

    assert normalized_inputs == ["0043", "0043"]


@pytest.mark.parametrize(
    ("enabled", "updates"),
    [
        (False, {}),
        (True, {"provider": Provider.SRT}),
        (True, {"arrival_at": None}),
        (True, {"passenger_count": 2}),
        (True, {"seat_class": "unsupported"}),
    ],
    ids=("disabled", "wrong-provider", "missing-arrival", "passenger-count", "seat-class"),
)
def test_unsupported_request_shapes_fail_closed_without_normalization(
    enabled: bool,
    updates: dict[str, object],
) -> None:
    normalizer_calls = 0

    def normalize_train_number(_value: object) -> str:
        nonlocal normalizer_calls
        normalizer_calls += 1
        return "43"

    result = policy.build_reservation_request(
        _reservation_request().model_copy(update=updates),
        _credentials(),
        enabled=enabled,
        normalize_train_number=normalize_train_number,
    )

    assert result is None
    assert normalizer_calls == 0


@pytest.mark.parametrize(
    ("provider_blocked", "expected"),
    [
        (False, ReservationOutcome.FAILED),
        (True, ReservationOutcome.PROVIDER_BLOCKED),
    ],
)
def test_adapter_failure_projection_is_fail_closed(
    provider_blocked: bool,
    expected: ReservationOutcome,
) -> None:
    result = policy.project_reservation_failure(
        OBSERVED_AT,
        provider_blocked=provider_blocked,
    )

    assert result.outcome is expected
    assert result.source == "korail-pydoll-reservation"
    assert result.observed_at == OBSERVED_AT
    assert result.official_handoff_url is None
    assert result.progress_stages == ()


PROGRESS_TIMES = (
    datetime(2026, 8, 3, 6, 45, 1, tzinfo=UTC),
    datetime(2026, 8, 3, 6, 45, 2, tzinfo=UTC),
    datetime(2026, 8, 3, 6, 45, 3, tzinfo=UTC),
    datetime(2026, 8, 3, 6, 45, 4, tzinfo=UTC),
)


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (
            _wire_result(
                "payment_required",
                reservation_clicked=True,
                progress_times=PROGRESS_TIMES,
            ),
            ReservationOutcome.PAYMENT_REQUIRED,
        ),
        (_wire_result("auth_required", reservation_clicked=True), ReservationOutcome.AUTH_REQUIRED),
        (_wire_result("consent_required", reservation_clicked=True), ReservationOutcome.UNKNOWN),
        (_wire_result("action_required", reservation_clicked=True), ReservationOutcome.UNKNOWN),
        (
            _wire_result("provider_blocked", reservation_clicked=True),
            ReservationOutcome.PROVIDER_BLOCKED,
        ),
        (_wire_result("unavailable", reservation_clicked=True), ReservationOutcome.NOT_AVAILABLE),
        (_wire_result("failed", reservation_clicked=True), ReservationOutcome.UNKNOWN),
        (_wire_result("failed"), ReservationOutcome.FAILED),
    ],
    ids=(
        "payment-priority",
        "auth-priority",
        "consent-manual",
        "action-manual",
        "provider-blocked-priority",
        "unavailable-priority",
        "post-click-unknown",
        "default-failed",
    ),
)
def test_wire_result_priority_and_progress_are_preserved(
    wire: KorailReserveOnceResult,
    expected: ReservationOutcome,
) -> None:
    result = policy.project_reservation_result(wire, observed_at=OBSERVED_AT)

    assert result.outcome is expected
    assert result.source == "korail-pydoll-reservation"
    assert result.observed_at == OBSERVED_AT
    if expected is ReservationOutcome.PAYMENT_REQUIRED:
        assert result.official_handoff_url is not None
        assert result.official_handoff_url.host == "www.korail.com"
        assert result.official_handoff_url.path == "/ticket/mypage/mykorail"
        assert [stage.stage for stage in result.progress_stages] == [
            "authenticated_session_ready",
            "target_rechecked",
            "seat_selected",
            "reservation_requested",
        ]
        assert tuple(stage.occurred_at for stage in result.progress_stages) == PROGRESS_TIMES
    else:
        assert result.official_handoff_url is None
        assert result.progress_stages == ()
