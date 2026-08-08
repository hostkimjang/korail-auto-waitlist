from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain import WatchStatus
from ..schema_base import ApiModel

RATE_LIMIT_COOLDOWN = timedelta(minutes=30)
BLOCK_COOLDOWN = timedelta(minutes=5)
PROTECTION_SIGNALS = frozenset(
    {
        "-8002",
        "-8003",
        "403",
        "abnormal_access",
        "access_denied",
        "automation_detected",
        "bot_challenge",
        "captcha",
        "code_-8002",
        "code_-8003",
        "korail_-8002",
        "korail_-8003",
        "macro_err1",
        "netfunnel",
        "queue_challenge",
    }
)


class ErrorPolicyResult(ApiModel):
    status: WatchStatus
    cooldown_seconds: int | None
    requires_manual_resume: bool
    official_handoff_required: bool = False
    reason: str


def classify_provider_failure(code: int | str, now: datetime | None = None) -> ErrorPolicyResult:
    now = now or datetime.now(timezone.utc)
    normalized = str(code).strip().casefold().replace(" ", "_")
    if normalized == "429":
        return ErrorPolicyResult(
            status=WatchStatus.COOLDOWN,
            cooldown_seconds=int(RATE_LIMIT_COOLDOWN.total_seconds()),
            requires_manual_resume=False,
            official_handoff_required=False,
            reason="provider_rate_limited",
        )
    if normalized in PROTECTION_SIGNALS:
        return ErrorPolicyResult(
            status=WatchStatus.AUTH_REQUIRED,
            cooldown_seconds=int(BLOCK_COOLDOWN.total_seconds()),
            requires_manual_resume=True,
            official_handoff_required=True,
            reason="provider_block_or_challenge",
        )
    if normalized in {"401", "auth", "login_failed"}:
        return ErrorPolicyResult(
            status=WatchStatus.AUTH_REQUIRED,
            cooldown_seconds=None,
            requires_manual_resume=True,
            official_handoff_required=False,
            reason="provider_authentication_required",
        )
    return ErrorPolicyResult(
        status=WatchStatus.FAILED,
        cooldown_seconds=None,
        requires_manual_resume=True,
        official_handoff_required=False,
        reason="provider_request_failed",
    )


def cooldown_until(result: ErrorPolicyResult, now: datetime | None = None) -> datetime | None:
    if result.cooldown_seconds is None:
        return None
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=result.cooldown_seconds)
