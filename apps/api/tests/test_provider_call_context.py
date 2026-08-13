from __future__ import annotations

from uuid import UUID

from rail_waitlist.provider_call_context import (
    bind_request_deadline,
    bind_request_deadline_at,
    bind_request_id,
    current_request_deadline,
    current_request_id,
    new_log_id,
    remaining_request_timeout_ms,
    validated_log_id,
)


def test_log_ids_are_canonical_lowercase_uuid4_hex() -> None:
    first = new_log_id()
    second = new_log_id()

    assert first != second
    assert len(first) == 32
    assert first == first.lower()
    assert UUID(hex=first).version == 4
    assert validated_log_id(first) == first


def test_invalid_log_ids_are_replaced_without_leaking_into_context() -> None:
    malicious = "bad-id\nrequest_id=ffffffffffffffffffffffffffffffff"

    assert validated_log_id(malicious) is None
    with bind_request_id(malicious) as generated:
        assert generated != malicious
        assert validated_log_id(generated) == generated
        assert current_request_id() == generated
    assert current_request_id() is None


def test_nested_deadline_preserves_shorter_budget_and_resets() -> None:
    clock = [10.0]

    def monotonic() -> float:
        return clock[0]

    assert current_request_deadline() is None
    with bind_request_deadline(10, monotonic=monotonic) as outer:
        assert outer == 20.0
        clock[0] = 11.0
        with bind_request_deadline(20, monotonic=monotonic) as inherited:
            assert inherited == outer
            assert remaining_request_timeout_ms(monotonic=monotonic) == 9000
        assert current_request_deadline() == outer
        with bind_request_deadline(2, monotonic=monotonic) as shorter:
            assert shorter == 13.0
        assert current_request_deadline() == outer
    assert current_request_deadline() is None
    assert remaining_request_timeout_ms(monotonic=monotonic) is None


def test_remaining_deadline_never_becomes_negative() -> None:
    clock = [3.0]

    def monotonic() -> float:
        return clock[0]

    with bind_request_deadline(1, monotonic=monotonic):
        clock[0] = 10.0
        assert remaining_request_timeout_ms(monotonic=monotonic) == 0


def test_absolute_deadline_does_not_extend_after_setup_delay() -> None:
    with bind_request_deadline_at(20.0) as outer:
        assert outer == 20.0
        with bind_request_deadline_at(30.0) as inherited:
            assert inherited == outer
        with bind_request_deadline_at(15.0) as shorter:
            assert shorter == 15.0

    assert current_request_deadline() is None
