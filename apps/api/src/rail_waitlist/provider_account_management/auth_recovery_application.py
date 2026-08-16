from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Provider,
    ReservationOutcome,
    ReservationResultReasonCode,
    WatchStatus,
)
from ..reservations.progress_timing_policy import (
    has_persisted_reservation_requested_progress,
)
from ..reservations.provider_confirmation.contracts import ReservationConfirmationOutcome
from ..watch_management.models import (
    ReservationAttempt,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)

RECONCILIATION_AUTH_TRANSITION_OUTCOMES = {
    "reservation_reconciliation_auth_required": ReservationConfirmationOutcome.AUTH_REQUIRED,
    "reservation_reconciliation_provider_blocked": (
        ReservationConfirmationOutcome.PROVIDER_BLOCKED
    ),
}


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch: ...


@dataclass(frozen=True, slots=True)
class ProviderAuthRecoveryDependencies:
    apply_watch_transition: ApplyWatchTransition


async def resume_watches_after_verified_provider_login(
    session: AsyncSession,
    provider: Provider,
    authenticated_at: datetime,
    *,
    credential_version: int | None = None,
    dependencies: ProviderAuthRecoveryDependencies,
) -> list[str]:
    """Resume authentication-stalled watches inside the caller-owned account transaction."""
    watch_ids = list(
        (
            await session.scalars(
                select(Watch.id).where(
                    Watch.provider == provider,
                    Watch.status.in_(
                        [
                            WatchStatus.AUTH_REQUIRED,
                            WatchStatus.PAYMENT_REQUIRED,
                        ]
                    ),
                )
            )
        ).all()
    )
    resumed: list[str] = []
    for watch_id in watch_ids:
        watch = await session.scalar(
            select(Watch)
            .where(
                Watch.id == watch_id,
                Watch.status.in_(
                    [
                        WatchStatus.AUTH_REQUIRED,
                        WatchStatus.PAYMENT_REQUIRED,
                    ]
                ),
            )
            .with_for_update()
        )
        if watch is None:
            continue
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch.id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        if latest_transition is None:
            continue

        transition_at = latest_transition.created_at
        if transition_at.tzinfo is None or transition_at.utcoffset() is None:
            transition_at = transition_at.replace(tzinfo=UTC)
        verified_at = authenticated_at
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            verified_at = verified_at.replace(tzinfo=UTC)
        if watch.status is WatchStatus.PAYMENT_REQUIRED and credential_version is not None:
            latest_payment_attempt = await session.scalar(
                select(ReservationAttempt)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(WatchCandidate.watch_id == watch.id)
                .order_by(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                )
                .limit(1)
                .with_for_update(of=ReservationAttempt)
            )
            payment_confirmation_observed_at = (
                latest_payment_attempt.confirmation_observed_at
                if latest_payment_attempt is not None
                else None
            )
            if payment_confirmation_observed_at is not None and (
                payment_confirmation_observed_at.tzinfo is None
                or payment_confirmation_observed_at.utcoffset() is None
            ):
                payment_confirmation_observed_at = payment_confirmation_observed_at.replace(
                    tzinfo=UTC
                )
            if (
                latest_payment_attempt is not None
                and latest_payment_attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
                and latest_payment_attempt.confirmation_outcome
                in {
                    ReservationConfirmationOutcome.AUTH_REQUIRED,
                    ReservationConfirmationOutcome.PROVIDER_BLOCKED,
                }
                and latest_payment_attempt.reconciliation_resolution is None
                and latest_payment_attempt.credential_version == credential_version
                and payment_confirmation_observed_at is not None
                and payment_confirmation_observed_at < verified_at
            ):
                latest_payment_attempt.next_reconcile_at = verified_at
                resumed.append(watch.id)
            continue
        auth_failure_transition = latest_transition.reason in {
            "reservation_auth_required",
            "reservation_provider_blocked",
        }
        auth_failure_reverified = auth_failure_transition and transition_at <= verified_at
        preflight_auth_reverified = (
            latest_transition.reason
            in {
                "provider_account_not_authenticated_before_reservation",
                "provider_account_provider_blocked_before_observation",
            }
            and transition_at <= verified_at
        )
        non_auth_unknown = latest_transition.reason == "reservation_unknown"
        reconciliation_confirmation = RECONCILIATION_AUTH_TRANSITION_OUTCOMES.get(
            latest_transition.reason
        )
        legacy_post_request_attempt: ReservationAttempt | None = None
        if auth_failure_transition and transition_at <= verified_at:
            legacy_post_request_attempt = await session.scalar(
                select(ReservationAttempt)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(WatchCandidate.watch_id == watch.id)
                .order_by(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                )
                .limit(1)
                .with_for_update(of=ReservationAttempt)
            )
            legacy_outcome = (
                legacy_post_request_attempt.outcome
                if legacy_post_request_attempt is not None
                else None
            )
            if (
                legacy_post_request_attempt is not None
                and legacy_outcome
                in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }
                and has_persisted_reservation_requested_progress(
                    legacy_post_request_attempt.progress_stages
                )
            ):
                # Older workers persisted a post-dispatch AUTH/BLOCK as a
                # retryable terminal outcome.  Normalize only on a same-generation
                # verified login so that the next action is a read-only official
                # reconciliation rather than another reservation command.
                auth_failure_reverified = False
                expected_confirmation = (
                    ReservationConfirmationOutcome.AUTH_REQUIRED
                    if legacy_outcome is ReservationOutcome.AUTH_REQUIRED
                    else ReservationConfirmationOutcome.PROVIDER_BLOCKED
                )
                if (
                    credential_version is not None
                    and legacy_post_request_attempt.credential_version == credential_version
                    and transition_at < verified_at
                ):
                    legacy_post_request_attempt.outcome = ReservationOutcome.UNKNOWN
                    legacy_post_request_attempt.result_reason_code = (
                        ReservationResultReasonCode.AUTHENTICATION_REQUIRED
                        if expected_confirmation is ReservationConfirmationOutcome.AUTH_REQUIRED
                        else ReservationResultReasonCode.PROVIDER_BLOCKED
                    )
                    legacy_post_request_attempt.confirmation_outcome = expected_confirmation
                    legacy_post_request_attempt.confirmation_diagnostic_code = None
                    legacy_post_request_attempt.confirmation_source = (
                        "legacy-post-request-auth-signal"
                    )
                    legacy_post_request_attempt.confirmation_observed_at = (
                        legacy_post_request_attempt.finished_at
                        or legacy_post_request_attempt.started_at
                    )
                    legacy_post_request_attempt.reconciliation_resolution = None
                    legacy_post_request_attempt.next_reconcile_at = None
                    reconciliation_confirmation = expected_confirmation
        reconciliation_auth_reverified = False
        if reconciliation_confirmation is not None and credential_version is not None:
            latest_attempt = await session.scalar(
                select(ReservationAttempt)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(WatchCandidate.watch_id == watch.id)
                .order_by(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                )
                .limit(1)
                .with_for_update(of=ReservationAttempt)
            )
            confirmation_observed_at = (
                latest_attempt.confirmation_observed_at if latest_attempt is not None else None
            )
            if confirmation_observed_at is not None and (
                confirmation_observed_at.tzinfo is None
                or confirmation_observed_at.utcoffset() is None
            ):
                confirmation_observed_at = confirmation_observed_at.replace(tzinfo=UTC)
            reconciliation_auth_reverified = bool(
                latest_attempt is not None
                and latest_attempt.outcome is ReservationOutcome.UNKNOWN
                and latest_attempt.confirmation_outcome is reconciliation_confirmation
                and latest_attempt.reconciliation_resolution is None
                and latest_attempt.credential_version == credential_version
                and confirmation_observed_at is not None
                and transition_at < verified_at
                and confirmation_observed_at < verified_at
                and latest_attempt.reconciliation_attempt_count <= 6
            )
            if reconciliation_auth_reverified and latest_attempt is not None:
                if latest_attempt.reconciliation_attempt_count == 6:
                    # AUTH/BLOCK never consumed evidence budget. Normalize rows written
                    # before that rule once, then resume the sixth official read.
                    latest_attempt.reconciliation_attempt_count = 5
                latest_attempt.next_reconcile_at = verified_at
        if not (
            auth_failure_reverified
            or preflight_auth_reverified
            or non_auth_unknown
            or reconciliation_auth_reverified
        ):
            continue

        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(WatchCandidate.watch_id == watch.id)
                )
            ).all()
        )
        for candidate in candidates:
            if legacy_post_request_attempt is not None:
                candidate.manual_rearm_source_attempt_id = None
                candidate.manual_rearm_authorized_at = None
                if (
                    reconciliation_auth_reverified
                    and candidate.id == legacy_post_request_attempt.candidate_id
                    and candidate.state == "failed"
                ):
                    candidate.state = "observed"
                    candidate.suppressed_by_candidate_id = None
            if candidate.state != "failed":
                continue
            attempt = await session.scalar(
                select(ReservationAttempt)
                .where(ReservationAttempt.candidate_id == candidate.id)
                .order_by(ReservationAttempt.attempt_sequence.desc())
                .limit(1)
            )
            if reconciliation_auth_reverified:
                continue
            if auth_failure_reverified:
                if attempt is not None and attempt.outcome in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }:
                    candidate.state = "observed"
            elif (
                non_auth_unknown
                and attempt is not None
                and attempt.outcome is ReservationOutcome.UNKNOWN
            ):
                candidate.state = "observed"
            # ``provider_account_not_authenticated_before_reservation`` is recorded
            # before ``begin_reservation_attempt``. Keep its candidate state intact:
            # there is no completed attempt to clear and the next observation decides
            # whether the still-unclaimed initial episode can be attempted.

        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.SCHEDULED,
            reason=(
                (
                    (
                        "provider_login_reverified_for_reservation_reconciliation_after_block"
                        if reconciliation_confirmation
                        is ReservationConfirmationOutcome.PROVIDER_BLOCKED
                        else "provider_login_reverified_for_reservation_reconciliation"
                    )
                    if reconciliation_auth_reverified
                    else (
                        "provider_login_reverified_after_provider_block"
                        if latest_transition.reason == "reservation_provider_blocked"
                        else "provider_login_reverified"
                    )
                )
                if auth_failure_reverified or reconciliation_auth_reverified
                else (
                    (
                        "provider_login_reverified_before_observation"
                        if latest_transition.reason.endswith("_before_observation")
                        else "provider_login_reverified_before_reservation"
                    )
                    if preflight_auth_reverified
                    else "reservation_unknown_monitoring_resumed"
                )
            ),
        )
        resumed.append(watch.id)
    return resumed
