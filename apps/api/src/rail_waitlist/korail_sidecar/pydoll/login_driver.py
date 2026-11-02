"""Drive one KORAIL Pydoll login DOM without owning session lifecycle state."""

from __future__ import annotations

import asyncio
import contextlib as _contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from ..browser_service_availability import BrowserProviderUnavailable as _BrowserProviderUnavailable
from . import login_submission as _submission_owner
from .auth_contracts import KorailCredentialInput, KorailLoginMethod
from .page_contracts import PydollPageSnapshot

__all__ = [
    "Any",
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "Callable",
    "ExactTextWaiter",
    "ExactVisibleReader",
    "KorailCredentialInput",
    "KorailLoginMethod",
    "LoginAttemptState",
    "LoginExecuteScript",
    "LoginGoTo",
    "LoginWorkflowCompatibilityPort",
    "Mapping",
    "Protocol",
    "PydollLoginDomDriver",
    "PydollPageSnapshot",
    "ResponseSafetyGuard",
    "SnapshotReader",
    "VisibleElements",
    "annotations",
    "dataclass",
    "logging",
    "login_step",
]


class LoginAttemptState(Protocol):
    post_submit_check_attempted: bool
    post_submit_authenticated: bool


@dataclass
class _LocalLoginAttemptState:
    post_submit_check_attempted: bool = False
    post_submit_authenticated: bool = False


class LoginWorkflowCompatibilityPort(Protocol):
    async def _submit_login_form(self, credential: KorailCredentialInput) -> bool: ...

    async def _wait_for_login_authentication(
        self,
        attempt: LoginAttemptState | None = None,
    ) -> bool: ...

    async def _confirm_authenticated_search(self, attempt: LoginAttemptState) -> bool: ...

    async def _probe_official_authenticated_session(self) -> bool: ...

    async def _has_authenticated_header(self) -> bool: ...

    async def _wait_for_authenticated_header(self) -> bool: ...

    async def _login_step(self, stage: str, awaitable: Awaitable[Any]) -> Any: ...

    async def _wait_for_unique_login_method_tab(
        self,
        login_method: KorailLoginMethod,
    ) -> Any | None: ...

    async def _wait_for_login_controls(
        self,
        login_method: KorailLoginMethod,
    ) -> tuple[Any, Any, Any] | None: ...


class LoginGoTo(Protocol):
    def __call__(self, url: str, timeout: int) -> Awaitable[object]: ...


class LoginExecuteScript(Protocol):
    def __call__(
        self,
        script: str,
        *,
        return_by_value: bool,
        await_promise: bool,
        timeout: int,
    ) -> Awaitable[object]: ...


class _LoginExecuteCommand(Protocol):
    def __call__(self, command: dict[str, object]) -> Awaitable[object]: ...


class VisibleElements(Protocol):
    async def __call__(self, selector: str, *, scope: object = None) -> list[Any]: ...


type SnapshotReader = Callable[[], Awaitable[PydollPageSnapshot]]
type ExactVisibleReader = Callable[[str, str], Awaitable[bool]]
type ExactTextWaiter = Callable[[str, str], Awaitable[Any]]
type ResponseSafetyGuard = Callable[[PydollPageSnapshot, str], None]


async def login_step(stage: str, awaitable: Awaitable[Any]) -> Any:
    """Map browser-library failures to a secret-free, code-owned login stage."""

    try:
        return await awaitable
    except (
        BrowserProtectionDetected,
        BrowserRateLimited,
        BrowserSourceUnavailable,
    ):
        raise
    except Exception as error:
        raise BrowserSourceUnavailable(stage) from error


class PydollLoginDomDriver:
    """Own bounded login navigation, controls, and official session confirmation."""

    # Bounded 0.1 second polls for a protection-path body read to see loadingFinished.
    PROTECTION_RESPONSE_SETTLE_POLLS = 10

    def __init__(
        self,
        *,
        port: LoginWorkflowCompatibilityPort,
        page_url: str,
        timeout_ms: int,
        timeout_seconds: float,
        go_to: LoginGoTo,
        execute_script: LoginExecuteScript,
        execute_command: _LoginExecuteCommand,
        snapshot: SnapshotReader,
        visible_elements: VisibleElements,
        has_exact_visible: ExactVisibleReader,
        wait_for_exact_text: ExactTextWaiter,
        reset_search_state: Callable[[], None],
        response_safety_guard: ResponseSafetyGuard,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        event_logger: logging.Logger,
        observe_submission: Callable[
            [], _contextlib.AbstractAsyncContextManager[_submission_owner.PydollLoginSubmission]
        ],
        manual_session_only: bool = False,
    ) -> None:
        self._port = port
        self._page_url = page_url
        self._timeout_ms = timeout_ms
        self._timeout_seconds = timeout_seconds
        self._go_to = go_to
        self._execute_script = execute_script
        self._execute_command = execute_command
        self._snapshot = snapshot
        self._visible_elements = visible_elements
        self._has_exact_visible = has_exact_visible
        self._wait_for_exact_text = wait_for_exact_text
        self._reset_search_state = reset_search_state
        self._response_safety_guard = response_safety_guard
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_logger = event_logger
        self._observe_submission = observe_submission
        self._manual_session_only = manual_session_only
        self._submission_context: (
            _contextlib.AbstractAsyncContextManager[_submission_owner.PydollLoginSubmission] | None
        ) = None
        self._submission: _submission_owner.PydollLoginSubmission | None = None

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        try:
            authenticated = await self._ensure_authenticated(credential)
            diagnostics = self._terminal_submission_diagnostics(self._submission)
            await self.close_submission_observer()
            self._log_submission_terminal(authenticated, diagnostics)
            return authenticated
        except _submission_owner.PydollLoginResponseUnavailable:
            await self._observe_submission_failure()
            raise
        except (BrowserProtectionDetected, BrowserRateLimited) as error:
            await self._observe_submission_failure(error)
            raise
        finally:
            await self.close_submission_observer()

    async def _observe_submission_failure(self, error: Exception | None = None) -> None:
        owner = self._submission
        if owner is None:
            return
        if isinstance(error, BrowserProtectionDetected):
            await self._observe_protection_response(owner, error)
        header = "unavailable"
        try:
            # The actor closes this tab on failure. Read its existing DOM before
            # disposal; this never probes, retries, or turns a failure into success.
            async with asyncio.timeout(min(2.0, self._timeout_seconds)):
                authenticated = await self._port._has_authenticated_header()
            if type(authenticated) is bool:
                header = "present" if authenticated else "absent"
        except Exception:  # noqa: BLE001 -- diagnostic reads preserve the original failure.
            header = "unavailable"
        self._event_logger.info(
            "KORAIL login submission failure observation header=%s requests=%s",
            header,
            json.dumps([asdict(row) for row in owner.diagnostics()], sort_keys=True),
        )

    async def _observe_protection_response(
        self,
        owner: _submission_owner.PydollLoginSubmission,
        error: BrowserProtectionDetected,
    ) -> None:
        """Record the login response's own error code after a protection verdict.

        A page marker can raise before the completed login body is ever read, which left
        KORAIL's exact ``errCode`` unrecorded. This reads only the already received body
        through CDP, sends no provider request, and never changes the protection verdict.
        """

        outcome: str = "unavailable"
        try:
            # The marker can render before this observer handles loadingFinished.
            for _ in range(self.PROTECTION_RESPONSE_SETTLE_POLLS):
                if owner.snapshot().state not in {"posted", "in_flight"}:
                    break
                await self._sleep(0.1)
            async with asyncio.timeout(min(2.0, self._timeout_seconds)):
                outcome = await owner.classify_completed_response(self._execute_command)
        except Exception:  # noqa: BLE001 -- diagnostics preserve the original verdict.
            outcome = "unavailable"
        self._event_logger.warning(
            "KORAIL login protection response stage=%s trigger=%s "
            "response_auth_outcome=%s login_err_codes=%s",
            error.stage,
            error.trigger,
            outcome,
            owner.observed_err_codes(),
        )

    def _log_submission_terminal(
        self,
        authenticated: bool,
        diagnostics: list[dict[str, Any]] | None,
    ) -> None:
        if diagnostics is None:
            return
        self._event_logger.info(
            "KORAIL login submission terminal observation outcome=%s requests=%s",
            "authenticated" if authenticated else "auth_required",
            json.dumps(diagnostics, sort_keys=True),
        )

    @staticmethod
    def _terminal_submission_diagnostics(
        owner: _submission_owner.PydollLoginSubmission | None,
    ) -> list[dict[str, Any]] | None:
        if owner is None:
            return None
        return [asdict(row) for row in owner.diagnostics()]

    async def _ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        attempt = _LocalLoginAttemptState()
        if await self._port._login_step(
            "login_session_probe",
            self._port._has_authenticated_header(),
        ):
            return await self._port._confirm_authenticated_search(attempt)
        if self._manual_session_only:
            if await self._manual_session_is_authenticated():
                attempt.post_submit_check_attempted = True
                attempt.post_submit_authenticated = True
                return await self._port._confirm_authenticated_search(attempt)
            raise BrowserSourceUnavailable("manual_login_required")
        await self._port._login_step(
            "login_page_navigate",
            self._go_to(
                "https://www.korail.com/ticket/login",
                max(1, self._timeout_ms // 1000),
            ),
        )
        if not await self._port._submit_login_form(credential):
            return False
        if not await self._port._wait_for_login_authentication(attempt):
            return False
        diagnostics = self._terminal_submission_diagnostics(self._submission)
        await self.close_submission_observer()
        authenticated = await self._port._confirm_authenticated_search(attempt)
        self._log_submission_terminal(authenticated, diagnostics)
        return authenticated

    async def authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        try:
            authenticated = await self._authenticate_in_place(credential, attempt)
            diagnostics = self._terminal_submission_diagnostics(self._submission)
            await self.close_submission_observer()
            self._log_submission_terminal(authenticated, diagnostics)
            return authenticated
        except _submission_owner.PydollLoginResponseUnavailable:
            await self._observe_submission_failure()
            raise
        except (BrowserProtectionDetected, BrowserRateLimited) as error:
            await self._observe_submission_failure(error)
            raise
        finally:
            await self.close_submission_observer()

    async def _authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        if await self._port._login_step(
            "reservation_login_session_probe",
            self._port._has_authenticated_header(),
        ):
            return True
        if self._manual_session_only:
            if await self._manual_session_is_authenticated():
                return True
            raise BrowserSourceUnavailable("manual_login_required")
        if not await self._port._submit_login_form(credential):
            return False
        return await self._port._wait_for_login_authentication(attempt)

    async def _manual_session_is_authenticated(self) -> bool:
        """Accept a shared live session even when the retained tab DOM is stale."""

        try:
            authenticated = bool(
                await self._port._login_step(
                    "manual_login_session_check",
                    self._port._probe_official_authenticated_session(),
                )
            )
            self._event_logger.info(
                "KORAIL manual login session marker stage=official_session present=%s",
                str(authenticated).lower(),
            )
            return authenticated
        except BrowserSourceUnavailable:
            # The user may still be completing login in another tab. Keep the
            # dedicated browser alive and report the existing handoff state.
            self._event_logger.info(
                "KORAIL manual login session marker stage=official_session outcome=unavailable"
            )
            return False

    async def submit_login_form(self, credential: KorailCredentialInput) -> bool:
        tab = await self._port._login_step(
            "login_method_tab",
            self._port._wait_for_unique_login_method_tab(credential.login_method),
        )
        if tab is None:
            return False
        # Re-selecting an active method and clearing fresh fields emits unnecessary
        # UI events into the official login form. Preserve its initialized state.
        selected = await self._port._login_step(
            "login_method_select",
            self._find_login_controls(credential.login_method),
        )
        if selected is None:
            await self._port._login_step("login_method_select", tab.click())
        controls = await self._port._login_step(
            "login_controls",
            self._port._wait_for_login_controls(credential.login_method),
        )
        if controls is None:
            return False
        login_id, password, submit = controls

        identity_selector = credential.login_method.identity_selector
        password_selector = "input#password[name='password'][type='password']"
        await self._require_input_value(login_id, identity_selector, "", "login_identity_clear")
        await self._port._login_step(
            "login_identity_input",
            login_id.type_text(credential.login_id),
        )
        await self._require_input_value(password, password_selector, "", "login_password_clear")
        await self._port._login_step(
            "login_password_input",
            password.type_text(credential.password),
        )
        await self._require_input_value(
            login_id, identity_selector, credential.login_id, "login_input_mismatch"
        )
        await self._require_input_value(
            password, password_selector, credential.password, "login_input_mismatch"
        )
        await self.close_submission_observer()
        context = self._observe_submission()
        submission = await self._port._login_step("login_response", context.__aenter__())
        self._submission_context = context
        self._submission = submission
        try:
            submission.arm()
            await self._port._login_step("login_submit", submit.click())
        except BaseException:
            await self.close_submission_observer()
            raise
        return True

    async def _require_input_value(
        self, control: Any, selector: str, expected: str, stage: str
    ) -> None:
        """Attest the live input without returning its value or resetting its state."""

        result = await self._port._login_step(
            stage,
            control.execute_script(
                """function(selector, expected) {
                  const visible = e => {
                    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                    return r.width > 0 && r.height > 0 && s.display !== 'none'
                      && s.visibility !== 'hidden';
                  };
                  const panels = [...document.querySelectorAll(
                    '.tabPage.active[role=tabpanel]')].filter(visible);
                  const inputs = panels.length === 1
                    ? [...panels[0].querySelectorAll(selector)].filter(visible) : [];
                  return inputs.length === 1 && inputs[0] === this && this.isConnected
                    && !this.disabled && !this.readOnly && this.value === expected;
                }""",
                arguments=[{"value": selector}, {"value": expected}],
                return_by_value=True,
            ),
        )
        outer = result.get("result") if isinstance(result, Mapping) else None
        inner = outer.get("result", outer) if isinstance(outer, Mapping) else None
        if not isinstance(inner, Mapping) or inner.get("value") is not True:
            # A stale or unexpectedly populated form cannot authorize a credential POST.
            raise BrowserSourceUnavailable(stage)

    async def close_submission_observer(self) -> None:
        context = self._submission_context
        self._submission_context = None
        self._submission = None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def wait_for_login_authentication(
        self,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        submitted_at = self._monotonic()
        deadline = submitted_at + self._timeout_seconds
        attempt = attempt or _LocalLoginAttemptState()
        official_session_unavailable = False
        probe_owner: _submission_owner.PydollLoginSubmission | None = None
        probe_revision: int | None = None
        while self._monotonic() < deadline:
            snapshot = await self._observed_login_step("login_result_snapshot", self._snapshot())
            observed_submission = self._submission
            submission = observed_submission.snapshot() if observed_submission is not None else None
            verification_mode = (
                observed_submission.authentication_verification_mode()
                if observed_submission is not None
                else "blocked"
            )
            try:
                self._response_safety_guard(snapshot, "authenticate")
            except _BrowserProviderUnavailable as error:
                if (
                    submission is not None
                    and submission.state == "failed"
                    and submission.status is not None
                    and 500 <= submission.status <= 599
                    and error.trigger == "business_server_error"
                ):
                    if verification_mode == "pending":
                        await self._sleep(0.1)
                        continue
                    if verification_mode == "independent":
                        pass
                    else:
                        raise self._login_response_failure(submission) from None
                else:
                    raise
            if submission is None:
                raise BrowserSourceUnavailable("login_response")
            if submission.status == 429:
                raise BrowserRateLimited()
            if submission.status == 403:
                raise BrowserProtectionDetected(stage="login_response")
            if submission.state in {"failed", "ambiguous"}:
                if verification_mode == "pending":
                    await self._sleep(0.1)
                    continue
                if verification_mode != "independent":
                    raise self._login_response_failure(submission) from None
            elif not submission.safe_to_probe:
                await self._sleep(0.1)
                continue
            if observed_submission is None:
                raise BrowserSourceUnavailable("login_response")
            group_revision = observed_submission.group_revision
            authenticated_header = await self._observed_login_step(
                "login_result_header",
                self._port._has_authenticated_header(),
            )
            # A further UI request may arrive while the header read yields control.
            # Its completion is required before any session probe or success return.
            if not self._same_submission_group(
                observed_submission, group_revision, verification_mode
            ):
                await self._sleep(0.1)
                continue
            if verification_mode == "independent":
                if not authenticated_header:
                    await self._sleep(0.1)
                    continue
                if attempt.post_submit_check_attempted:
                    await self._sleep(0.1)
                    continue
                attempt.post_submit_check_attempted = True
                try:
                    probe_authenticated = bool(
                        await self._observed_login_step(
                            "login_page_session_check",
                            self._port._probe_official_authenticated_session(),
                        )
                    )
                except BrowserSourceUnavailable:
                    raise self._login_response_failure(submission) from None
                if not self._same_submission_group(
                    observed_submission, group_revision, verification_mode
                ):
                    official_session_unavailable = True
                    await self._sleep(0.1)
                    continue
                attempt.post_submit_authenticated = probe_authenticated
                probe_owner, probe_revision = observed_submission, group_revision
                self._event_logger.info(
                    "KORAIL login session marker stage=integration_failure_confirmation "
                    "header=true official_session=%s",
                    str(probe_authenticated).lower(),
                )
                if probe_authenticated:
                    return True
                raise self._login_response_failure(submission)
            if authenticated_header:
                self._event_logger.info("KORAIL login session marker stage=login_page present=true")
                return True
            if not attempt.post_submit_check_attempted:
                attempt.post_submit_check_attempted = True
                try:
                    probe_authenticated = bool(
                        await self._observed_login_step(
                            "login_page_session_check",
                            self._port._probe_official_authenticated_session(),
                        )
                    )
                    if self._same_submission_group(
                        observed_submission, group_revision, verification_mode
                    ):
                        attempt.post_submit_authenticated = probe_authenticated
                        probe_owner, probe_revision = observed_submission, group_revision
                        if not probe_authenticated:
                            body_outcome = await observed_submission.classify_completed_response(
                                self._execute_command
                            )
                            if body_outcome == "protection":
                                raise BrowserProtectionDetected(
                                    "marker_macro_err1", "login_response"
                                )
                            if not self._same_submission_group(
                                observed_submission, group_revision, verification_mode
                            ):
                                official_session_unavailable = True
                    else:
                        # A later POST invalidates either probe outcome, even if that
                        # request already finished. Keep the one-probe budget and await
                        # independent current header evidence; this is not a rejection.
                        attempt.post_submit_authenticated = False
                        official_session_unavailable = True
                except BrowserSourceUnavailable:
                    official_session_unavailable = True
                    # A 200 HTML/invalid loginCheck response cannot attest either login
                    # state. Continue bounded DOM polling after a submitted credential;
                    # explicit protection and rate-limit classifications still propagate.
                    self._event_logger.info(
                        "KORAIL login session marker stage=login_page_official_session "
                        "attempt=1 outcome=unavailable"
                    )
                self._event_logger.info(
                    "KORAIL login session marker stage=login_page_official_session "
                    "attempt=1 present=%s",
                    str(attempt.post_submit_authenticated).lower(),
                )
                if attempt.post_submit_authenticated and self._same_submission_group(
                    observed_submission, group_revision, verification_mode
                ):
                    return True
            await self._sleep(0.1)
        self._event_logger.info("KORAIL login session marker stage=login_page present=false")
        submission = self._submission.snapshot() if self._submission is not None else None
        if (
            submission is None
            or not submission.safe_to_probe
            or not attempt.post_submit_check_attempted
        ):
            raise self._login_response_failure(submission)
        # Only this submission group's explicit negative probe can reject a credential.
        # A stale or unavailable outcome must not become auth_required at the deadline.
        if (
            official_session_unavailable
            or probe_owner is None
            or probe_revision is None
            or not self._same_submission_group(probe_owner, probe_revision, "standard")
        ):
            raise self._login_response_failure(submission)
        return False

    def _same_submission_group(
        self,
        owner: _submission_owner.PydollLoginSubmission,
        revision: int,
        verification_mode: _submission_owner.LoginAuthenticationVerificationMode,
    ) -> bool:
        return (
            self._submission is owner
            and owner.group_revision == revision
            and owner.authentication_verification_mode() == verification_mode
        )

    @staticmethod
    def _login_response_failure(
        submission: _submission_owner.LoginSubmissionSnapshot | None,
    ) -> BrowserSourceUnavailable:
        if (
            submission is not None
            and submission.state != "missing"
            and submission.failure != "missing"
        ):
            return _submission_owner.PydollLoginResponseUnavailable(submission)
        return BrowserSourceUnavailable("login_response")

    async def _observed_login_step(self, stage: str, awaitable: Awaitable[Any]) -> Any:
        try:
            return await self._port._login_step(stage, awaitable)
        except _BrowserProviderUnavailable:
            raise
        except BrowserSourceUnavailable:
            submission = self._submission.snapshot() if self._submission is not None else None
            if (
                submission is None
                or submission.state == "missing"
                or submission.failure == "missing"
            ):
                raise
            if submission.status == 429:
                raise BrowserRateLimited() from None
            if submission.status == 403:
                raise BrowserProtectionDetected(stage="login_response") from None
            # A failed DOM read after dispatch cannot authorize a fresh credential POST.
            raise self._login_response_failure(submission) from None

    async def confirm_authenticated_search(self, attempt: LoginAttemptState) -> bool:
        await self._port._login_step(
            "login_return_search",
            self._go_to(
                self._page_url,
                max(1, self._timeout_ms // 1000),
            ),
        )
        self._reset_search_state()
        await self._port._login_step(
            "login_return_search",
            self._wait_for_exact_text("button", "열차 조회"),
        )
        if not attempt.post_submit_check_attempted:
            attempt.post_submit_check_attempted = True
            try:
                attempt.post_submit_authenticated = bool(
                    await self._port._login_step(
                        "login_search_session_check",
                        self._port._probe_official_authenticated_session(),
                    )
                )
            except BrowserSourceUnavailable:
                # The official probe is positive evidence only. Its unavailable form is
                # not a protection verdict, so keep the initial-login DOM confirmation.
                self._event_logger.info(
                    "KORAIL login session marker stage=official_session outcome=unavailable"
                )
        if attempt.post_submit_authenticated:
            self._event_logger.info(
                "KORAIL login session marker stage=official_session present=true"
            )
            return True
        authenticated = bool(
            await self._port._login_step(
                "login_search_session_probe",
                self._port._wait_for_authenticated_header(),
            )
        )
        self._event_logger.info(
            "KORAIL login session marker stage=search_page present=%s",
            str(authenticated).lower(),
        )
        return authenticated

    async def probe_official_authenticated_session(self) -> bool:
        script = """
            (async () => {
              try {
                const response = await fetch(
                  '/ebizweb/common/loginCheck?Device=BH&Version=999999999',
                  {
                    method: 'GET',
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: { Accept: 'application/json' },
                  },
                );
                if (response.status === 429) return { outcome: 'rate_limited' };
                if (response.status === 403) return { outcome: 'protected' };
                if (!response.ok) return { outcome: 'source_unavailable' };
                const contentType = response.headers.get('content-type') || '';
                const mime = contentType.split(';', 1)[0].trim().toLowerCase();
                // The official endpoint also serves its JSON as text/html.
                // Status, JSON parsing, and the response shape remain authoritative.
                if (mime !== 'application/json' && mime !== 'text/html') {
                  return { outcome: 'source_unavailable' };
                }
                try {
                  const payload = await response.json();
                  const observed = payload && typeof payload === 'object'
                      && !Array.isArray(payload)
                      && Object.prototype.hasOwnProperty.call(payload, 'errCode')
                    ? {
                        err_code: typeof payload.errCode === 'string'
                          ? payload.errCode.slice(0, 64)
                          : null,
                      }
                    : {};
                  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
                      || typeof payload.strResult !== 'string'
                      || (payload.h_msg_cd != null && typeof payload.h_msg_cd !== 'string')) {
                    return { outcome: 'source_unavailable', ...observed };
                  }
                  return {
                    outcome:
                      payload?.strResult === 'SUCC' && !payload?.h_msg_cd
                        ? 'authenticated'
                        : 'logged_out',
                    ...observed,
                  };
                } catch (_) {
                  return { outcome: 'source_unavailable' };
                }
              } catch (_) {
                return { outcome: 'source_unavailable' };
              }
            })()
        """
        response = await self._execute_script(
            script,
            return_by_value=True,
            await_promise=True,
            timeout=self._timeout_ms,
        )
        if not isinstance(response, Mapping):
            raise BrowserSourceUnavailable("session_keepalive")
        command_result = response.get("result")
        if not isinstance(command_result, Mapping):
            raise BrowserSourceUnavailable("session_keepalive")
        script_result = command_result.get("result")
        if not isinstance(script_result, Mapping):
            raise BrowserSourceUnavailable("session_keepalive")
        value = script_result.get("value")
        if not isinstance(value, Mapping):
            raise BrowserSourceUnavailable("session_keepalive")
        outcome = value.get("outcome")
        if "err_code" in value:
            # Only a code-shaped errCode is retained; the probe body itself stays in-page.
            self._event_logger.warning(
                "KORAIL login session probe response outcome=%s login_err_codes=%s",
                outcome
                if outcome in {"authenticated", "logged_out", "source_unavailable"}
                else "invalid",
                (_submission_owner.sanitized_login_err_code(value.get("err_code")),),
            )
        if outcome == "authenticated":
            return True
        if outcome == "logged_out":
            return False
        if outcome == "rate_limited":
            raise BrowserRateLimited()
        if outcome == "protected":
            raise BrowserProtectionDetected(stage="session_keepalive")
        raise BrowserSourceUnavailable("session_keepalive")

    async def has_authenticated_header(self) -> bool:
        return await self._has_exact_visible(
            "a.btnGoLogout,button.logoutBtn",
            "로그아웃",
        )

    async def wait_for_authenticated_header(self) -> bool:
        deadline = self._monotonic() + self._timeout_seconds
        while self._monotonic() < deadline:
            if await self._port._has_authenticated_header():
                return True
            await self._sleep(0.1)
        return False

    async def wait_for_unique_login_method_tab(
        self,
        login_method: KorailLoginMethod,
    ) -> Any | None:
        deadline = self._monotonic() + self._timeout_seconds
        while self._monotonic() < deadline:
            tabs = await self._visible_elements(login_method.tab_selector)
            if len(tabs) == 1:
                self._event_logger.info(
                    "KORAIL login control marker stage=login_method_tab outcome=ready"
                )
                return tabs[0]
            if len(tabs) > 1:
                self._event_logger.info(
                    "KORAIL login control marker stage=login_method_tab outcome=ambiguous"
                )
                return None
            await self._sleep(0.1)
        self._event_logger.info(
            "KORAIL login control marker stage=login_method_tab outcome=timeout"
        )
        return None

    async def wait_for_login_controls(
        self,
        login_method: KorailLoginMethod,
    ) -> tuple[Any, Any, Any] | None:
        deadline = self._monotonic() + self._timeout_seconds
        while self._monotonic() < deadline:
            controls = await self._find_login_controls(login_method)
            if controls is not None:
                return controls
            await self._sleep(0.1)
        return None

    async def _find_login_controls(
        self, login_method: KorailLoginMethod
    ) -> tuple[Any, Any, Any] | None:
        panels = await self._visible_elements(".tabPage.active[role='tabpanel']")
        if len(panels) != 1:
            return None
        panel = panels[0]
        identities = await self._visible_elements(login_method.identity_selector, scope=panel)
        passwords = await self._visible_elements(
            "input#password[name='password'][type='password']", scope=panel
        )
        submits = [
            control
            for control in await self._visible_elements("button,[role='button']", scope=panel)
            if " ".join(str(await control.text).split()) == "로그인"
        ]
        if len(identities) == len(passwords) == len(submits) == 1:
            return identities[0], passwords[0], submits[0]
        return None
