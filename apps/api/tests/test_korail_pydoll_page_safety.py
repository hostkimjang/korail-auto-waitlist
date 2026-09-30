from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from rail_waitlist.korail_browser_automation import (
    BrowserProtectionDetected,
    BrowserRateLimited,
)
from rail_waitlist.korail_sidecar.browser_service_availability import (
    BrowserProviderUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.page_contracts import (
    PydollPageSnapshot,
    PydollTrainRow,
)
from rail_waitlist.korail_sidecar.pydoll.page_safety import (
    assert_pydoll_response_allowed,
    classify_pydoll_page_block,
)

EVENT_LOGGER = logging.getLogger("rail_waitlist.korail_pydoll_browser")
VISIBLE_ROW = PydollTrainRow("KTX", "43", "서울 → 부산(15:00 ~ 17:30)", ())


@pytest.mark.parametrize(
    ("snapshot", "expected_exception", "expected_trigger"),
    [
        (
            PydollPageSnapshot("결과", (), network_responses=((429, "fetch"),)),
            BrowserRateLimited,
            None,
        ),
        (
            PydollPageSnapshot("결과", (), network_responses=((403, "document"),)),
            BrowserProtectionDetected,
            "http_403_main",
        ),
        (
            PydollPageSnapshot("CODE -8003", (VISIBLE_ROW,)),
            BrowserProtectionDetected,
            "marker_code_8003",
        ),
        (
            PydollPageSnapshot("비정상 접근입니다", ()),
            BrowserProtectionDetected,
            "marker_abnormal_access",
        ),
        (
            PydollPageSnapshot(
                "비정상 접근입니다",
                (VISIBLE_ROW,),
                protection_texts=("비정상 접근입니다",),
            ),
            BrowserProtectionDetected,
            "marker_abnormal_access",
        ),
    ],
)
def test_page_safety_maps_blocking_evidence_to_existing_adapter_errors(
    snapshot: PydollPageSnapshot,
    expected_exception: type[Exception],
    expected_trigger: str | None,
) -> None:
    block = classify_pydoll_page_block(snapshot)
    assert block is not None
    assert block.kind == (
        "rate_limited" if expected_exception is BrowserRateLimited else "protection"
    )
    assert block.trigger == expected_trigger

    with pytest.raises(expected_exception) as raised:
        assert_pydoll_response_allowed(snapshot, "wait_result", event_logger=EVENT_LOGGER)

    assert raised.value.reason in {"rate_limited", "provider_access_restricted"}
    if isinstance(raised.value, BrowserProtectionDetected):
        assert raised.value.stage == "wait_result"
        assert raised.value.trigger == expected_trigger


@pytest.mark.parametrize(
    "snapshot",
    [
        PydollPageSnapshot("정상 결과", (VISIBLE_ROW,)),
        PydollPageSnapshot("결과", (VISIBLE_ROW,), network_responses=((429, "font"),)),
        PydollPageSnapshot("결과", (VISIBLE_ROW,), network_responses=((403, "xhr"),)),
        PydollPageSnapshot("비정상 접근 안내가 포함된 정상 결과", (VISIBLE_ROW,)),
    ],
)
def test_page_safety_preserves_benign_rows_and_subresource_distinctions(
    snapshot: PydollPageSnapshot,
) -> None:
    assert classify_pydoll_page_block(snapshot) is None
    assert_pydoll_response_allowed(snapshot, "wait_result", event_logger=EVENT_LOGGER)


@pytest.mark.parametrize(
    ("network_responses", "expected_exception"),
    [
        (((403, "document"), (429, "fetch")), BrowserProtectionDetected),
        (((429, "fetch"), (403, "document")), BrowserRateLimited),
    ],
)
def test_page_safety_preserves_first_matching_network_evidence_order(
    network_responses: tuple[tuple[int, str], ...],
    expected_exception: type[Exception],
) -> None:
    snapshot = PydollPageSnapshot("결과", (), network_responses=network_responses)
    block = classify_pydoll_page_block(snapshot)

    assert block is not None
    assert block.kind == (
        "rate_limited" if expected_exception is BrowserRateLimited else "protection"
    )

    with pytest.raises(expected_exception):
        assert_pydoll_response_allowed(snapshot, "wait_result", event_logger=EVENT_LOGGER)


def test_page_safety_logs_only_sanitized_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=EVENT_LOGGER.name)
    snapshot = PydollPageSnapshot(
        "CODE -8002 secret-body",
        (),
        protection_texts=("CODE -8002 secret-surface",),
        network_responses=((403, "document"),),
    )

    with pytest.raises(BrowserProtectionDetected):
        assert_pydoll_response_allowed(snapshot, "authenticate", event_logger=EVENT_LOGGER)

    assert "stage=authenticate trigger=http_403_main" in caplog.text
    assert "rows=0 visible_surfaces=1 marker_surfaces=0 network=((403, 'document'),)" in caplog.text
    assert "secret-body" not in caplog.text
    assert "secret-surface" not in caplog.text


def test_page_safety_classifies_maintenance_without_logging_page_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=EVENT_LOGGER.name)
    snapshot = PydollPageSnapshot(
        "서비스를 일시중지합니다 secret-body 승차권 예약 및 발매서비스",
        (),
        url="https://www.korail.com/rejectservice_job.html",
    )

    block = classify_pydoll_page_block(snapshot)
    assert block is not None
    assert (block.kind, block.trigger) == ("provider_unavailable", "maintenance_page")
    with pytest.raises(BrowserProviderUnavailable) as raised:
        assert_pydoll_response_allowed(snapshot, "wait_result", event_logger=EVENT_LOGGER)

    assert raised.value.reason == "source_unavailable"
    assert raised.value.stage == "wait_result"
    assert "stage=wait_result trigger=maintenance_page rows=0 network_count=0" in caplog.text
    assert "secret-body" not in caplog.text


def test_page_safety_does_not_reverse_depend_on_browser_facade() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "page_safety.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules


@pytest.mark.parametrize("rows", [(), (VISIBLE_ROW,)])
@pytest.mark.parametrize("resource_type", ["business_xhr", "business_fetch"])
def test_business_5xx_rejects_empty_and_partial_lists_without_exposing_content(
    rows: tuple[PydollTrainRow, ...],
    resource_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=EVENT_LOGGER.name)
    snapshot = PydollPageSnapshot(
        "public-body-fixture",
        rows,
        network_responses=((500, resource_type),),
        url="https://www.korail.com/ticket/search/list?fixture=test",
    )

    block = classify_pydoll_page_block(snapshot)
    assert block is not None
    assert (block.kind, block.trigger) == ("provider_unavailable", "business_server_error")
    with pytest.raises(BrowserProviderUnavailable) as raised:
        assert_pydoll_response_allowed(snapshot, "expand_results", event_logger=EVENT_LOGGER)

    assert raised.value.reason == "source_unavailable"
    assert raised.value.stage == "business_response"
    assert f"status=500 resource_type={resource_type} rows={len(rows)}" in caplog.text
    assert "public-body-fixture" not in caplog.text
    assert "fixture=test" not in caplog.text


@pytest.mark.parametrize("resource_type", ["xhr", "fetch", "document", "image", "font"])
def test_untagged_5xx_does_not_become_business_source_failure(resource_type: str) -> None:
    snapshot = PydollPageSnapshot("정상", (VISIBLE_ROW,), network_responses=((500, resource_type),))

    assert classify_pydoll_page_block(snapshot) is None


@pytest.mark.parametrize(
    ("body", "url", "network", "expected_kind"),
    [
        ("결과", "", ((429, "fetch"),), "rate_limited"),
        ("결과", "", ((403, "document"),), "protection"),
        ("captcha", "", (), "protection"),
        ("점검", "https://www.korail.com/rejectservice_job.html", (), "provider_unavailable"),
    ],
)
def test_protection_and_maintenance_remain_prior_to_business_5xx(
    body: str, url: str, network: tuple[tuple[int, str], ...], expected_kind: str
) -> None:
    snapshot = PydollPageSnapshot(
        body, (), network_responses=((500, "business_xhr"), *network), url=url
    )

    block = classify_pydoll_page_block(snapshot)

    assert block is not None
    assert block.kind == expected_kind
