from __future__ import annotations

import pytest

from rail_waitlist.korail_sidecar.chromium_launch import isolated_test_chromium_arguments


@pytest.mark.parametrize("value", ["", "false", "1", "yes"])
def test_chromium_sandbox_remains_enabled_without_exact_test_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_TEST_DISABLE_SANDBOX", value)

    assert isolated_test_chromium_arguments() == ()


def test_isolated_browser_test_can_disable_chromium_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_TEST_DISABLE_SANDBOX", "true")

    assert isolated_test_chromium_arguments() == ("--no-sandbox",)
