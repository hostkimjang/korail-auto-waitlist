"""Compatibility facade for KORAIL Pydoll page-safety policy."""

from __future__ import annotations

from .korail_sidecar.pydoll import page_safety as _owner

annotations = _owner.annotations
logging = _owner.logging
AUTOMATION_GENERIC_PROTECTION_TRIGGERS = _owner.AUTOMATION_GENERIC_PROTECTION_TRIGGERS
BrowserProtectionDetected = _owner.BrowserProtectionDetected
BrowserRateLimited = _owner.BrowserRateLimited
is_rate_limit_response = _owner.is_rate_limit_response
protection_trigger_from_http_response = _owner.protection_trigger_from_http_response
protection_trigger_from_text = _owner.protection_trigger_from_text
PydollPageSnapshot = _owner.PydollPageSnapshot
GENERIC_PROTECTION_TRIGGERS = _owner.GENERIC_PROTECTION_TRIGGERS
assert_pydoll_response_allowed = _owner.assert_pydoll_response_allowed
_log_protection_snapshot = _owner._log_protection_snapshot
