from __future__ import annotations

import re

from .browser_contracts import ProtectionTrigger

PROTECTION_MARKERS: tuple[tuple[ProtectionTrigger, re.Pattern[str]], ...] = (
    ("marker_code_8002", re.compile(r"code\s*:?\s*-?\s*8002", re.IGNORECASE)),
    ("marker_code_8003", re.compile(r"code\s*:?\s*-?\s*8003", re.IGNORECASE)),
    ("marker_code_1405", re.compile(r"code\s*:?\s*-?\s*1405", re.IGNORECASE)),
    ("marker_macro_err1", re.compile(r"macro_err1", re.IGNORECASE)),
    ("marker_captcha", re.compile(r"captcha", re.IGNORECASE)),
    ("marker_netfunnel", re.compile(r"netfunnel", re.IGNORECASE)),
    (
        "marker_abnormal_access",
        re.compile(r"비정상\s*접근", re.IGNORECASE),
    ),
    ("marker_unauthorized_tool", re.compile(r"미허가\s*도구", re.IGNORECASE)),
)
GENERIC_PROTECTION_TRIGGERS = frozenset({"marker_abnormal_access", "marker_unauthorized_tool"})
RATE_LIMIT_RESOURCE_TYPES = frozenset({"document", "fetch", "xhr"})
REPLAY_PROTECTION_MARKERS: tuple[tuple[ProtectionTrigger, re.Pattern[str]], ...] = (
    ("marker_code_1405", re.compile(r"(?<!\d)-?1405(?!\d)", re.IGNORECASE)),
    ("marker_code_8002", re.compile(r"(?<!\d)-?8002(?!\d)", re.IGNORECASE)),
    ("marker_code_8003", re.compile(r"(?<!\d)-?8003(?!\d)", re.IGNORECASE)),
    ("marker_macro_err1", re.compile(r"macro_err", re.IGNORECASE)),
    ("marker_captcha", re.compile(r"captcha", re.IGNORECASE)),
    ("marker_netfunnel", re.compile(r"netfunnel", re.IGNORECASE)),
    ("marker_unauthorized_tool", re.compile(r"미허가", re.IGNORECASE)),
    (
        "marker_abnormal_access",
        re.compile(r"(?:비정상\s*접근|이용\s*제한)", re.IGNORECASE),
    ),
)
REPLAY_PROTECTION_TRIGGER_ALIASES: dict[str, ProtectionTrigger] = {
    "http_403": "http_403_business",
    "http_403_business": "http_403_business",
    "code_1405": "marker_code_1405",
    "code_8002": "marker_code_8002",
    "code_8003": "marker_code_8003",
    "macro_err": "marker_macro_err1",
    "captcha": "marker_captcha",
    "netfunnel": "marker_netfunnel",
    "unauthorized": "marker_unauthorized_tool",
    "restricted": "marker_abnormal_access",
    "marker_code_1405": "marker_code_1405",
    "marker_code_8002": "marker_code_8002",
    "marker_code_8003": "marker_code_8003",
    "marker_macro_err1": "marker_macro_err1",
    "marker_captcha": "marker_captcha",
    "marker_netfunnel": "marker_netfunnel",
    "marker_unauthorized_tool": "marker_unauthorized_tool",
    "marker_abnormal_access": "marker_abnormal_access",
}


def protection_trigger_from_http_response(
    status: int, resource_type: str
) -> ProtectionTrigger | None:
    if status != 403:
        return None
    if resource_type == "document":
        return "http_403_main"
    return "http_403_subresource"


def is_rate_limit_response(status: int, resource_type: str) -> bool:
    return status == 429 and resource_type in RATE_LIMIT_RESOURCE_TYPES


def protection_trigger_from_text(value: str) -> ProtectionTrigger | None:
    for trigger, pattern in PROTECTION_MARKERS:
        if pattern.search(value):
            return trigger
    return None


def protection_trigger_from_replay_text(value: str) -> ProtectionTrigger | None:
    """Classify structured business-response text, including bare provider codes."""

    for trigger, pattern in REPLAY_PROTECTION_MARKERS:
        if pattern.search(value):
            return trigger
    return None


def normalize_replay_protection_trigger(trigger: str) -> ProtectionTrigger:
    """Map legacy replay diagnostics into the shared sanitized vocabulary."""

    return REPLAY_PROTECTION_TRIGGER_ALIASES.get(trigger, "marker_abnormal_access")
