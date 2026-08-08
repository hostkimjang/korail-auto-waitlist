"""Drive one KORAIL Pydoll login DOM without owning session lifecycle state."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
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

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
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
        return await self._port._confirm_authenticated_search(attempt)

    async def authenticate_in_place(
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
        await self._port._login_step("login_submit", submit.click())
        return True

    async def wait_for_login_authentication(
        self,
        attempt: LoginAttemptState | None = None,
    ) -> bool:
        submitted_at = self._monotonic()
        deadline = submitted_at + self._timeout_seconds
        attempt = attempt or _LocalLoginAttemptState()
        session_probe_delay = min(0.25, self._timeout_seconds / 4)
        while self._monotonic() < deadline:
            snapshot = await self._port._login_step("login_result_snapshot", self._snapshot())
            self._response_safety_guard(snapshot, "authenticate")
            authenticated_header = await self._port._login_step(
                "login_result_header",
                self._port._has_authenticated_header(),
            )
            if authenticated_header:
                self._event_logger.info("KORAIL login session marker stage=login_page present=true")
                return True
            elapsed = self._monotonic() - submitted_at
            if not attempt.post_submit_check_attempted and elapsed >= session_probe_delay:
                attempt.post_submit_check_attempted = True
                attempt.post_submit_authenticated = bool(
                    await self._port._login_step(
                        "login_page_session_check",
                        self._port._probe_official_authenticated_session(),
                    )
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
        return False

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
            attempt.post_submit_authenticated = bool(
                await self._port._login_step(
                    "login_search_session_check",
                    self._port._probe_official_authenticated_session(),
                )
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
                if (!response.ok) return false;
                const payload = await response.json();
                return payload?.strResult === 'SUCC' && !payload?.h_msg_cd;
              } catch (_) {
                return false;
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
            return False
        command_result = response.get("result")
        if not isinstance(command_result, Mapping):
            return False
        script_result = command_result.get("result")
        return isinstance(script_result, Mapping) and script_result.get("value") is True

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
