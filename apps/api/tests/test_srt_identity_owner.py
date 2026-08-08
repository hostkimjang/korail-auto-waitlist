from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from rail_waitlist import srt_reservation, srt_reservation_confirmation, srt_seat_source
from rail_waitlist.provider_adapters import srt_identity as canonical

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SRT 00028", "28"),
        ("000", "0"),
        (None, "0"),
        (False, "0"),
        (-12.3, "123"),
        ("٠٠٢٨", "٠٠٢٨"),
    ],
)
def test_normalize_srt_train_number_preserves_permissive_contract(
    value: object,
    expected: str,
) -> None:
    assert canonical.normalize_srt_train_number(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-01", "20260801"),
        ("801", "00000801"),
        ("", "00000000"),
        (None, "00000000"),
        ("123456789", "23456789"),
        ("٢٠٢٦-٠٨-٠١", "٢٠٢٦٠٨٠١"),
    ],
)
def test_normalize_srt_date_preserves_permissive_contract(
    value: object,
    expected: str,
) -> None:
    assert canonical.normalize_srt_date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1237", "123700"),
        ("7", "000700"),
        ("12345", "012345"),
        ("12:37:00", "123700"),
        ("", "000000"),
        (None, "000000"),
        ("1234567", "234567"),
        ("١٢:٣٧", "١٢٣٧00"),
    ],
)
def test_normalize_srt_time_preserves_permissive_contract(
    value: object,
    expected: str,
) -> None:
    assert canonical.normalize_srt_time(value) == expected


@pytest.mark.parametrize(
    "normalizer",
    [
        canonical.normalize_srt_train_number,
        canonical.normalize_srt_date,
        canonical.normalize_srt_time,
    ],
)
def test_srt_identity_normalizers_propagate_str_error_identity(
    normalizer: Callable[[object], str],
) -> None:
    error = RuntimeError("string conversion failed")

    class FailingValue:
        def __str__(self) -> str:
            raise error

    with pytest.raises(RuntimeError) as caught:
        normalizer(FailingValue())
    assert caught.value is error


@pytest.mark.parametrize(
    ("normalizer", "expected"),
    [
        (canonical.normalize_srt_train_number, "28"),
        (canonical.normalize_srt_date, "00000028"),
        (canonical.normalize_srt_time, "002800"),
    ],
)
def test_srt_identity_normalizers_convert_input_to_string_once(
    normalizer: Callable[[object], str],
    expected: str,
) -> None:
    class CountingValue:
        calls = 0

        def __str__(self) -> str:
            self.calls += 1
            return "0028"

    value = CountingValue()
    assert normalizer(value) == expected
    assert value.calls == 1


def test_srt_identity_consumers_share_exact_canonical_objects() -> None:
    for symbol in (
        "normalize_srt_train_number",
        "normalize_srt_date",
        "normalize_srt_time",
    ):
        owner = getattr(canonical, symbol)
        assert getattr(srt_seat_source, symbol) is owner
        assert getattr(srt_reservation, symbol) is owner
        assert getattr(srt_reservation_confirmation, symbol) is owner
        assert owner.__module__ == "rail_waitlist.provider_adapters.srt_identity"


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "source-first", "reservation-first", "confirmation-first"],
)
def test_srt_identity_import_orders_keep_one_owner(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.provider_adapters import srt_identity as canonical
elif sys.argv[1] == "source-first":
    from rail_waitlist import srt_seat_source as source
elif sys.argv[1] == "reservation-first":
    from rail_waitlist import srt_reservation as reservation
else:
    from rail_waitlist import srt_reservation_confirmation as confirmation

from rail_waitlist.provider_adapters import srt_identity as canonical
from rail_waitlist import srt_reservation as reservation
from rail_waitlist import srt_reservation_confirmation as confirmation
from rail_waitlist import srt_seat_source as source

symbols = (
    "normalize_srt_train_number",
    "normalize_srt_date",
    "normalize_srt_time",
)
modules = (source, reservation, confirmation)
print(json.dumps({
    "identity": all(
        getattr(module, symbol) is getattr(canonical, symbol)
        for module in modules
        for symbol in symbols
    ),
    "modules": sorted({getattr(canonical, symbol).__module__ for symbol in symbols}),
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
        "identity": True,
        "modules": ["rail_waitlist.provider_adapters.srt_identity"],
    }
