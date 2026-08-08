from __future__ import annotations

import json
import pickle
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

import pytest
from sqlalchemy import Enum
from sqlalchemy.orm import configure_mappers

import rail_waitlist.models as central_models
import rail_waitlist.reservation_confirmation as legacy
from rail_waitlist.database import Base
from rail_waitlist.reservations.provider_confirmation import contracts as canonical
from rail_waitlist.watch_management import models as watch_models

API_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MEMBERS = (
    ("CONFIRMED_PAYMENT_REQUIRED", "confirmed_payment_required"),
    ("NOT_FOUND", "not_found"),
    ("AUTH_REQUIRED", "auth_required"),
    ("PROVIDER_BLOCKED", "provider_blocked"),
    ("INCONCLUSIVE", "inconclusive"),
)


def test_confirmation_outcome_has_one_canonical_enum_and_legacy_alias() -> None:
    outcome = canonical.ReservationConfirmationOutcome

    assert legacy.ReservationConfirmationOutcome is outcome
    assert issubclass(outcome, StrEnum)
    assert tuple((member.name, member.value) for member in outcome) == EXPECTED_MEMBERS
    assert outcome.__module__ == "rail_waitlist.reservations.provider_confirmation.contracts"


def test_confirmation_outcome_pickle_round_trip_uses_the_canonical_identity() -> None:
    for member in canonical.ReservationConfirmationOutcome:
        assert pickle.loads(pickle.dumps(member)) is member


def test_reservation_attempt_column_uses_the_canonical_enum_contract() -> None:
    configure_mappers()
    column = watch_models.ReservationAttempt.__table__.c.confirmation_outcome

    assert isinstance(column.type, Enum)
    assert column.type.enum_class is canonical.ReservationConfirmationOutcome
    assert not column.type.native_enum
    assert column.type.name == "reservationconfirmationoutcome"
    assert column.type.enums == [name for name, _value in EXPECTED_MEMBERS]
    assert column.nullable
    assert Base.metadata.tables["reservation_attempts"] is watch_models.ReservationAttempt.__table__
    assert central_models.ReservationAttempt is watch_models.ReservationAttempt
    assert (
        sum(mapper.class_ is watch_models.ReservationAttempt for mapper in Base.registry.mappers)
        == 1
    )


def test_canonical_contract_import_does_not_load_schema_or_legacy_confirmation_hubs() -> None:
    script = """
import json
import sys
from rail_waitlist.reservations.provider_confirmation import contracts
print(json.dumps({
    "legacy": "rail_waitlist.reservation_confirmation" in sys.modules,
    "schemas": "rail_waitlist.schemas" in sys.modules,
    "module": contracts.ReservationConfirmationOutcome.__module__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )

    assert json.loads(completed.stdout) == {
        "legacy": False,
        "schemas": False,
        "module": "rail_waitlist.reservations.provider_confirmation.contracts",
    }


@pytest.mark.parametrize(
    "order",
    (
        "canonical-first",
        "legacy-first",
        "models-first",
        "schemas-first",
        "watch-schemas-first",
    ),
)
def test_confirmation_outcome_import_orders_keep_one_enum_mapper_and_table(order: str) -> None:
    script = """
import json
import sys
from sqlalchemy.orm import configure_mappers

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical
    import rail_waitlist.reservation_confirmation as legacy
    from rail_waitlist.watch_management import models as watch_models
    import rail_waitlist.models as central_models
elif order == "legacy-first":
    import rail_waitlist.reservation_confirmation as legacy
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical
    import rail_waitlist.models as central_models
    from rail_waitlist.watch_management import models as watch_models
elif order == "models-first":
    import rail_waitlist.models as central_models
    from rail_waitlist.watch_management import models as watch_models
    import rail_waitlist.reservation_confirmation as legacy
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical
elif order == "schemas-first":
    import rail_waitlist.schemas
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical
    import rail_waitlist.reservation_confirmation as legacy
    from rail_waitlist.watch_management import models as watch_models
    import rail_waitlist.models as central_models
else:
    from rail_waitlist.watch_management import schemas
    import rail_waitlist.models as central_models
    from rail_waitlist.watch_management import models as watch_models
    import rail_waitlist.reservation_confirmation as legacy
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical

from rail_waitlist.database import Base
configure_mappers()
outcome = canonical.ReservationConfirmationOutcome
column = watch_models.ReservationAttempt.__table__.c.confirmation_outcome
print(json.dumps({
    "identity": legacy.ReservationConfirmationOutcome is outcome,
    "module": outcome.__module__,
    "column": column.type.enum_class is outcome,
    "central": central_models.ReservationAttempt is watch_models.ReservationAttempt,
    "table": (
        Base.metadata.tables["reservation_attempts"]
        is watch_models.ReservationAttempt.__table__
    ),
    "mappers": sum(
        mapper.class_ is watch_models.ReservationAttempt
        for mapper in Base.registry.mappers
    ),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, order],
        cwd=API_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )

    assert json.loads(completed.stdout) == {
        "identity": True,
        "module": "rail_waitlist.reservations.provider_confirmation.contracts",
        "column": True,
        "central": True,
        "table": True,
        "mappers": 1,
    }
