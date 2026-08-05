from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from rail_waitlist.korail_browser_automation import (
    BrowserProtectionDetected,
    BrowserRateLimited,
)
from rail_waitlist.korail_pydoll_contracts import PydollPageSnapshot, PydollTrainRow
from rail_waitlist.korail_pydoll_page_safety import assert_pydoll_response_allowed

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


def test_page_safety_does_not_reverse_depend_on_browser_facade() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_pydoll_page_safety.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules
