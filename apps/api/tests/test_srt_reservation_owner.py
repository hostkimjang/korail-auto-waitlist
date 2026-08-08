from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import srt_reservation as legacy
from rail_waitlist.srt_sidecar import reservation as owner

API_ROOT = Path(__file__).resolve().parents[1]
OWNER_MODULE = "rail_waitlist.srt_sidecar.reservation"
PUBLIC_SYMBOLS = {
    "Adult",
    "Callable",
    "KOREA",
    "Literal",
    "Lock",
    "Protocol",
    "RequestException",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationOutcome",
    "ReservationRequest",
    "ReservationResult",
    "SRT",
    "SRTError",
    "SRTLoginError",
    "SRTNetFunnelError",
    "SRTNotLoggedInError",
    "SRTResponseError",
    "SRT_RESERVATION_HANDOFF_URL",
    "SRT_RESERVATION_LIST_SOURCE",
    "SRT_RESERVATION_SOURCE",
    "SeatClass",
    "SeatType",
    "SrtClientFactory",
    "SrtReservationCredentials",
    "SrtReservationExecutor",
    "SrtReservationListEvidence",
    "SrtReservationRecord",
    "SrtSessionActorSnapshot",
    "SrtSessionActorState",
    "SrtStationRosterUnavailable",
    "StrEnum",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "default_srt_reservation_executor",
    "field",
    "hashlib",
    "load_srt_station_roster",
    "normalize_srt_date",
    "normalize_srt_reservation_records",
    "normalize_srt_time",
    "normalize_srt_train_number",
    "re",
    "time",
    "verify_srt_credentials_once",
}


def test_srt_reservation_facade_keeps_the_exact_public_surface() -> None:
    assert {name for name in vars(legacy) if not name.startswith("_")} == (
        PUBLIC_SYMBOLS | {"annotations"}
    )
    assert {name for name in vars(owner) if not name.startswith("_")} == (
        PUBLIC_SYMBOLS | {"annotations"}
    )
    for symbol in PUBLIC_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(owner, symbol)

    assert owner.SrtReservationCredentials.__module__ == OWNER_MODULE
    assert owner.SrtReservationExecutor.__module__ == OWNER_MODULE
    assert owner.verify_srt_credentials_once.__module__ == OWNER_MODULE
    assert owner.default_srt_reservation_executor.__module__ == OWNER_MODULE


def test_srt_reservation_facade_is_assignment_only() -> None:
    path = API_ROOT / "src" / "rail_waitlist" / "srt_reservation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name != "annotations"
    }
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in PUBLIC_SYMBOLS
    }

    assert definitions == set()
    assert imports == {("srt_sidecar", 1, "reservation", "_reservation")}
    assert set(assignments) == PUBLIC_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_reservation"
        assert value.attr == symbol


def test_legacy_and_canonical_factories_share_one_process_singleton() -> None:
    canonical = owner.default_srt_reservation_executor()

    assert legacy.default_srt_reservation_executor() is canonical
    assert isinstance(canonical, owner.SrtReservationExecutor)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            "gASVPwAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwZU3J0UmVzZXJ2YXRpb25DcmVkZW50aWFsc5STlC4=",
            owner.SrtReservationCredentials,
        ),
        (
            "gASVPAAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwWU3J0UmVzZXJ2YXRpb25FeGVjdXRvcpSTlC4=",
            owner.SrtReservationExecutor,
        ),
        (
            "gASVQQAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwbdmVyaWZ5X3NydF9jcmVkZW50aWFsc19vbmNllJOULg==",
            owner.verify_srt_credentials_once,
        ),
        (
            "gASVRgAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwgZGVmYXVsdF9zcnRfcmVzZXJ2YXRpb25fZXhlY3V0b3KUk5Qu",
            owner.default_srt_reservation_executor,
        ),
    ],
)
def test_pre_move_srt_reservation_globals_restore_to_the_owner(
    payload: str,
    expected: object,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is expected


@pytest.mark.parametrize("first_import", ["owner", "legacy", "service", "adapter"])
def test_srt_reservation_import_orders_keep_identity_and_singleton(first_import: str) -> None:
    script = r"""
import json
import sys

first = sys.argv[1]
if first == "owner":
    from rail_waitlist.srt_sidecar import reservation as imported
elif first == "legacy":
    from rail_waitlist import srt_reservation as imported
elif first == "service":
    from rail_waitlist import srt_provider_adapter_service as imported
else:
    from rail_waitlist.provider_adapters import srt_execution as imported

legacy_was_loaded = "rail_waitlist.srt_reservation" in sys.modules
from rail_waitlist import srt_reservation as legacy
from rail_waitlist.srt_sidecar import reservation as owner

print(json.dumps({
    "identity": all([
        legacy.SrtReservationCredentials is owner.SrtReservationCredentials,
        legacy.SrtReservationExecutor is owner.SrtReservationExecutor,
        legacy.verify_srt_credentials_once is owner.verify_srt_credentials_once,
        legacy.default_srt_reservation_executor is owner.default_srt_reservation_executor,
    ]),
    "singleton": (
        legacy.default_srt_reservation_executor()
        is owner.default_srt_reservation_executor()
    ),
    "legacy_was_loaded": legacy_was_loaded,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, first_import],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["identity"] is True
    assert result["singleton"] is True
    if first_import in {"owner", "service", "adapter"}:
        assert result["legacy_was_loaded"] is False


def test_legacy_dependency_reassignment_does_not_mutate_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = owner.load_srt_station_roster

    monkeypatch.setattr(legacy, "load_srt_station_roster", object())

    assert owner.load_srt_station_roster is canonical


def test_reservation_fencing_script_uses_the_canonical_constant() -> None:
    path = API_ROOT / "scripts" / "check_reservation_credential_fencing_postgres.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "SRT_RESERVATION_SOURCE"
    }

    assert imports == {("rail_waitlist.srt_sidecar.reservation", 0, "SRT_RESERVATION_SOURCE", None)}
