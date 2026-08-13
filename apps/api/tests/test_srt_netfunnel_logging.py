from __future__ import annotations

import inspect
import logging
import traceback

import pytest
from SRT.errors import SRTNetFunnelError
from SRT.netfunnel import NetFunnelHelper

from rail_waitlist.provider_adapters import srt_netfunnel_logging as logging_module
from rail_waitlist.provider_adapters.srt_netfunnel_logging import LoggingNetFunnelHelper
from rail_waitlist.provider_call_context import bind_provider_call_id, new_log_id

LOGGER_NAME = "rail_waitlist.srt_provider_adapter"


def test_queue_logs_entry_changed_count_and_release_without_provider_material(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([10.0, 10.75])
    helper = LoggingNetFunnelHelper(
        flow="accountless",
        monotonic=lambda: next(clock),
    )
    delegated: list[tuple[str, str]] = []

    def vendor_wait(_self: NetFunnelHelper, key: str, nwait: str) -> str:
        delegated.append((key, nwait))
        return key

    def vendor_generate(_self: NetFunnelHelper, _use_cache: bool) -> str:
        helper._wait_until_complete("secret-entry", "3")
        helper._wait_until_complete("secret-next", "2")
        return "secret-pass"

    monkeypatch.setattr(NetFunnelHelper, "_wait_until_complete", vendor_wait)
    monkeypatch.setattr(NetFunnelHelper, "generate_netfunnel_key", vendor_generate)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert helper.generate_netfunnel_key(use_cache=False) == "secret-pass"

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert delegated == [("secret-entry", "3"), ("secret-next", "2")]
    assert sum("대기열에 들어갑니다" in message for message in messages) == 1
    assert sum("대기 인원이 변경되었습니다" in message for message in messages) == 1
    assert sum("대기열이 끝나" in message for message in messages) == 1
    assert "waiting_count=3" in messages[0]
    assert any("waiting_count=2" in message for message in messages)
    assert any("elapsed_ms=750" in message for message in messages)
    assert "secret-" not in "\n".join(messages)
    assert all(record.levelno == logging.INFO for record in caplog.records)


def test_queue_lifecycle_logs_keep_bound_provider_call_id(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_call_id = new_log_id()
    helper = LoggingNetFunnelHelper(flow="accountless")

    monkeypatch.setattr(
        NetFunnelHelper,
        "_wait_until_complete",
        lambda _self, key, _nwait: key,
    )

    def vendor_generate(_self: NetFunnelHelper, _use_cache: bool) -> str:
        helper._wait_until_complete("secret-entry", "2")
        return "secret-pass"

    monkeypatch.setattr(NetFunnelHelper, "generate_netfunnel_key", vendor_generate)

    with (
        bind_provider_call_id(provider_call_id),
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
    ):
        assert helper.generate_netfunnel_key(use_cache=False) == "secret-pass"

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert messages
    assert all(f"provider_call_id={provider_call_id}" in message for message in messages)


def test_queue_failure_is_sanitized_without_claiming_release(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([20.0, 20.25])
    helper = LoggingNetFunnelHelper(
        flow="authenticated",
        monotonic=lambda: next(clock),
    )

    monkeypatch.setattr(
        NetFunnelHelper,
        "_wait_until_complete",
        lambda _self, key, _nwait: key,
    )

    def vendor_generate(_self: NetFunnelHelper, _use_cache: bool) -> str:
        helper._wait_until_complete("queue-secret-key", "1")
        raise RuntimeError("provider response with queue-secret-key")

    monkeypatch.setattr(NetFunnelHelper, "generate_netfunnel_key", vendor_generate)

    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        pytest.raises(
            SRTNetFunnelError,
        ) as raised,
    ):
        helper.generate_netfunnel_key(use_cache=False)

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert any("event=provider_queue_failed" in message for message in messages)
    assert not any("대기열이 끝나" in message for message in messages)
    assert "queue-secret-key" not in "\n".join(messages)
    rendered = "".join(traceback.format_exception(raised.value))
    assert str(raised.value) == "NetFunnel request failed"
    assert raised.value.__cause__ is None
    assert "queue-secret-key" not in rendered


def test_immediate_admission_does_not_invent_queue_lifecycle_logs(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = LoggingNetFunnelHelper(flow="accountless")
    monkeypatch.setattr(
        NetFunnelHelper,
        "generate_netfunnel_key",
        lambda _self, _use_cache: "immediate-pass",
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert helper.generate_netfunnel_key(use_cache=False) == "immediate-pass"

    assert not [record for record in caplog.records if record.name == LOGGER_NAME]


def test_waiting_count_is_bounded_before_logging(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = LoggingNetFunnelHelper(flow="accountless")
    monkeypatch.setattr(
        NetFunnelHelper,
        "_wait_until_complete",
        lambda _self, key, _nwait: key,
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert (
            helper._wait_until_complete(
                "secret-key",
                "7\nforged_event=credential-leak",
            )
            == "secret-key"
        )

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert messages
    assert "waiting_count=unknown" in messages[0]
    assert "forged_event" not in "\n".join(messages)
    assert "secret-key" not in "\n".join(messages)


def test_helper_delegates_vendor_wire_and_polling_implementation() -> None:
    source = inspect.getsource(logging_module)
    vendor_get_source = inspect.getsource(NetFunnelHelper._get_netfunnel_key)
    vendor_wait_source = inspect.getsource(NetFunnelHelper._wait_until_complete)

    assert "def _get_netfunnel_key" not in source
    assert "session.get" not in source
    assert "NETFUNNEL_URL" not in source
    assert "OP_CODE" not in source
    assert "NetFunnelResponse" not in source
    assert "time.sleep" not in source
    assert "super().generate_netfunnel_key" in source
    assert "super()._wait_until_complete" in source
    assert "self._wait_until_complete(" in vendor_get_source
    assert "self._wait_until_complete(" in vendor_wait_source


def test_vendor_failure_before_queue_closes_exception_material_and_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = LoggingNetFunnelHelper(flow="accountless")
    secret = "queue-secret-key"

    def vendor_generate(_self: NetFunnelHelper, _use_cache: bool) -> str:
        raise RuntimeError(f"provider response with {secret}")

    monkeypatch.setattr(NetFunnelHelper, "generate_netfunnel_key", vendor_generate)

    with pytest.raises(SRTNetFunnelError) as raised:
        helper.generate_netfunnel_key(use_cache=False)

    rendered = "".join(traceback.format_exception(raised.value))
    assert str(raised.value) == "NetFunnel request failed"
    assert raised.value.__cause__ is None
    assert secret not in rendered
