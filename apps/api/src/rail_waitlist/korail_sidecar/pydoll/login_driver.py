"""Drive one KORAIL Pydoll login DOM without owning session lifecycle state."""

from __future__ import annotations

import contextlib as _contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
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

    def __init__(
        self,
        *,
        port: LoginWorkflowCompatibilityPort,
        page_url: str,
        timeout_ms: int,
        timeout_seconds: float,
        go_to: LoginGoTo,
        execute_script: LoginExecuteScript,
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
    ) -> None:
        self._port = port
        self._page_url = page_url
        self._timeout_ms = timeout_ms
        self._timeout_seconds = timeout_seconds
        self._go_to = go_to
        self._execute_script = execute_script
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
        self._submission_context: (
            _contextlib.AbstractAsyncContextManager[_submission_owner.PydollLoginSubmission] | None
        ) = None
        self._submission: _submission_owner.PydollLoginSubmission | None = None

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        try:
            return await self._ensure_authenticated(credential)
        finally:
            await self.close_submission_observer()

    async def _ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        attempt = _LocalLoginAttemptState()
        if await self._port._login_step(
            "login_session_probe",
            self._port._has_authenticated_header(),
        ):
            return await self._port._confirm_authenticated_search(attempt)
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
        await self.close_submission_observer()
        return await self._port._confirm_authenticated_search(attempt)

    async def authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        try:
            return await self._authenticate_in_place(credential, attempt)
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
        if not await self._port._submit_login_form(credential):
            return False
        return await self._port._wait_for_login_authentication(attempt)

    async def submit_login_form(self, credential: KorailCredentialInput) -> bool:
        tab = await self._port._login_step(
            "login_method_tab",
            self._port._wait_for_unique_login_method_tab(credential.login_method),
        )
        if tab is None:
            return False
        await self._port._login_step("login_method_select", tab.click())
        controls = await self._port._login_step(
            "login_controls",
            self._port._wait_for_login_controls(credential.login_method),
        )
        if controls is None:
            return False
        login_id, password, submit = controls

        await self._port._login_step("login_identity_clear", login_id.clear())
        await self._port._login_step(
            "login_identity_input",
            login_id.type_text(credential.login_id),
        )
        await self._port._login_step("login_password_clear", password.clear())
        await self._port._login_step(
            "login_password_input",
            password.type_text(credential.password),
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
        while self._monotonic() < deadline:
            snapshot = await self._observed_login_step("login_result_snapshot", self._snapshot())
            submission = self._submission.snapshot() if self._submission is not None else None
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
                    raise self._login_response_failure(submission) from None
                raise
            if submission is None:
                raise BrowserSourceUnavailable("login_response")
            if submission.status == 429:
                raise BrowserRateLimited()
            if submission.status == 403:
                raise BrowserProtectionDetected(stage="login_response")
            if submission.state in {"failed", "ambiguous"}:
                raise self._login_response_failure(submission)
            if not submission.safe_to_probe:
                await self._sleep(0.1)
                continue
            authenticated_header = await self._observed_login_step(
                "login_result_header",
                self._port._has_authenticated_header(),
            )
            if authenticated_header:
                self._event_logger.info("KORAIL login session marker stage=login_page present=true")
                return True
            if not attempt.post_submit_check_attempted:
                attempt.post_submit_check_attempted = True
                try:
                    attempt.post_submit_authenticated = bool(
                        await self._observed_login_step(
                            "login_page_session_check",
                            self._port._probe_official_authenticated_session(),
                        )
                    )
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
                if attempt.post_submit_authenticated:
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
        if not attempt.post_submit_authenticated:
            # An unavailable probe does not establish a rejected credential.
            # Explicit negative results are recorded separately below.
            if official_session_unavailable:
                raise self._login_response_failure(submission)
        return False

    @staticmethod
    def _login_response_failure(
        submission: _submission_owner.LoginSubmissionSnapshot | None,
    ) -> BrowserSourceUnavailable:
        if (
            submission is not None
            and submission.state != "missing"
            and submission.failure != "missing"
        ):
            return _submission_owner.PydollLoginResponseUnavailable()
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
                  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
                      || typeof payload.strResult !== 'string'
                      || (payload.h_msg_cd != null && typeof payload.h_msg_cd !== 'string')) {
                    return { outcome: 'source_unavailable' };
                  }
                  return {
                    outcome:
                      payload?.strResult === 'SUCC' && !payload?.h_msg_cd
                        ? 'authenticated'
                        : 'logged_out',
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
        password_selector = "input#password[name='password'][type='password']"
        while self._monotonic() < deadline:
            panels = await self._visible_elements(".tabPage.active[role='tabpanel']")
            if len(panels) == 1:
                panel = panels[0]
                identities = await self._visible_elements(
                    login_method.identity_selector,
                    scope=panel,
                )
                passwords = await self._visible_elements(password_selector, scope=panel)
                submits = [
                    control
                    for control in await self._visible_elements(
                        "button,[role='button']",
                        scope=panel,
                    )
                    if " ".join(str(await control.text).split()) == "로그인"
                ]
                if len(identities) == len(passwords) == len(submits) == 1:
                    return identities[0], passwords[0], submits[0]
            await self._sleep(0.1)
        return None
