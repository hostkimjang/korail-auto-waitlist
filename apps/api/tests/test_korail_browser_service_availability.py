from __future__ import annotations

import pytest

from rail_waitlist.korail_sidecar.browser_service_availability import (
    BrowserProviderUnavailable,
    decode_provider_page_text,
    provider_unavailable_trigger_from_page,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.korail.com/rejectservice_job.html",
        "https://www.korail.com/service/rejectservice_JOB.HTML?from=search",
    ],
)
def test_official_maintenance_path_is_high_confidence_outage_evidence(url: str) -> None:
    assert provider_unavailable_trigger_from_page(url, "") == "maintenance_page"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.korail.com/rejectservice_job.html",
        "https://evil.example/rejectservice_job.html",
        "https://www.korail.com/rejectservice_job.html.txt",
        "https://user@www.korail.com/rejectservice_job.html",
        "https://www.korail.com:444/rejectservice_job.html",
    ],
)
def test_maintenance_path_requires_exact_official_https_page(url: str) -> None:
    assert provider_unavailable_trigger_from_page(url, "") is None


def test_official_body_requires_both_service_suspension_markers() -> None:
    body = "보다 편리한 서비스를 제공하기 위해 서비스를 일시중지합니다. 승차권 예약 및 발매서비스"
    assert (
        provider_unavailable_trigger_from_page(
            "https://www.korail.com/ticket/search/general",
            body,
        )
        == "service_outage_page"
    )
    assert (
        provider_unavailable_trigger_from_page(
            "https://evil.example/notice",
            body,
        )
        is None
    )
    assert (
        provider_unavailable_trigger_from_page(
            "https://www.korail.com/ticket/search/general",
            body,
            has_result_rows=True,
        )
        is None
    )
    assert (
        provider_unavailable_trigger_from_page(
            "https://www.korail.com/ticket/search/general",
            "서비스를 일시중지합니다.",
        )
        is None
    )


def test_provider_unavailable_error_keeps_only_closed_diagnostics() -> None:
    error = BrowserProviderUnavailable("maintenance_page", "wait_result")
    retriable = error.with_retry_after(300)

    assert (retriable.reason, retriable.trigger, retriable.stage) == (
        "source_unavailable",
        "maintenance_page",
        "wait_result",
    )
    assert retriable.retry_after_seconds == 300
    with pytest.raises(ValueError, match="between 1 and 86400"):
        error.with_retry_after(86401)


def test_legacy_korean_page_encoding_is_decoded_for_marker_classification() -> None:
    body = "서비스를 일시중지합니다. 승차권 예약 및 발매서비스".encode("cp949")

    assert (
        provider_unavailable_trigger_from_page(
            "https://www.korail.com/ticket/search/general",
            decode_provider_page_text(body),
        )
        == "service_outage_page"
    )
