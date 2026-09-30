from __future__ import annotations

from dataclasses import fields

import pytest

from rail_waitlist.korail_sidecar.pydoll.network_evidence import (
    PydollBusinessProtection,
    PydollBusinessProviderUnavailable,
    PydollBusinessServerError,
    classify_pydoll_business_response_failure,
    pydoll_network_response_evidence,
)

_PUBLIC_TEST_URL = "https://www.korail.com/web_s/public-fixture?_qzj=test-fixture"


def _response_event(
    *, status: object = 500, resource_type: object = "XHR", url: object = _PUBLIC_TEST_URL
) -> dict[str, object]:
    return {"params": {"type": resource_type, "response": {"status": status, "url": url}}}


@pytest.mark.parametrize("resource_type, expected_resource", [("XHR", "xhr"), ("Fetch", "fetch")])
@pytest.mark.parametrize("status", [500, 502.0, 503, 599])
def test_official_business_5xx_is_sanitized_source_failure(
    resource_type: str, expected_resource: str, status: int | float
) -> None:
    result = classify_pydoll_business_response_failure(
        _response_event(status=status, resource_type=resource_type)
    )

    assert isinstance(result, PydollBusinessServerError)
    assert (result.status, result.resource_type, result.kind) == (
        int(status),
        expected_resource,
        "server_error",
    )
    assert {field.name for field in fields(result)} == {"status", "resource_type", "kind"}
    assert "test-fixture" not in repr(result)


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {},
        {"params": []},
        {"params": {"response": []}},
        *[
            _response_event(status=value)
            for value in (True, "500", 500.5, float("nan"), float("inf"))
        ],
        *[_response_event(status=value) for value in (200, 403, 429, 499, 600)],
        *[
            _response_event(resource_type=value)
            for value in ("Document", "Image", "Font", "xhr", None)
        ],
        *[
            _response_event(url=value)
            for value in (
                "https://www.korail.com/images/public-fixture.png",
                "https://www.korail.com/ticket/search/list",
                "https://www.korail.com/api/analytics",
                "https://www.korail.com/web_s/",
                "https://third-party.example/web_s/public-fixture",
                "http://www.korail.com/web_s/public-fixture",
                "https://www.korail.com:8443/web_s/public-fixture",
                "https://user:fixture@www.korail.com/web_s/public-fixture",
                "https://www.korail.com/web_s/public-fixture#fragment",
                "https://www.korail.com:invalid/web_s/public-fixture",
                "https://[invalid/web_s/public-fixture",
                None,
            )
        ],
    ],
)
def test_unrelated_errors_and_malformed_events_do_not_become_business_evidence(
    event: object,
) -> None:
    assert classify_pydoll_business_response_failure(event) is None


def test_protection_body_has_priority_over_service_outage_and_ordinary_5xx() -> None:
    body = "captcha 서비스를 일시 중지 승차권 예약 및 발매 서비스 public-body-fixture".encode()

    result = classify_pydoll_business_response_failure(_response_event(), response_body=body)

    assert isinstance(result, PydollBusinessProtection)
    assert result.trigger == "marker_captcha"
    assert "public-body-fixture" not in repr(result)
    assert "test-fixture" not in repr(result)


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_official_service_outage_body_is_classified_in_memory(encoding: str) -> None:
    body = "서비스를 일시 중지 승차권 예약 및 발매 서비스".encode(encoding)

    result = classify_pydoll_business_response_failure(_response_event(), response_body=body)

    assert isinstance(result, PydollBusinessProviderUnavailable)
    assert result.trigger == "service_outage_page"


def test_body_markers_cannot_expand_scope_to_assets_or_rate_limit_events() -> None:
    body = b"captcha"

    assert (
        classify_pydoll_business_response_failure(
            _response_event(url="https://www.korail.com/image.png"), response_body=body
        )
        is None
    )
    assert (
        classify_pydoll_business_response_failure(_response_event(status=429), response_body=body)
        is None
    )


def test_oversize_body_cannot_be_used_as_classification_evidence() -> None:
    body = b"captcha" + bytes(2 * 1024 * 1024)

    result = classify_pydoll_business_response_failure(_response_event(), response_body=body)

    assert isinstance(result, PydollBusinessServerError)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_response_event(status="429", resource_type=" Fetch ", url=None), (429, "fetch")),
        (_response_event(status=403, resource_type="Document", url=None), (403, "document")),
        (_response_event(status=403, resource_type="XHR"), None),
        (_response_event(status=429, resource_type="Font"), None),
        (_response_event(status=500), (500, "business_xhr")),
        (_response_event(status=503, resource_type="Fetch"), (503, "business_fetch")),
        (_response_event(status=500, url="https://www.korail.com/image.png"), None),
        (_response_event(status=float("inf")), None),
        (None, None),
    ],
)
def test_normalization_preserves_old_signals_and_tags_only_official_business_errors(
    event: object, expected: tuple[int, str] | None
) -> None:
    assert pydoll_network_response_evidence(event) == expected
