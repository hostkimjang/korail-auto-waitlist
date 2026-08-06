from __future__ import annotations

import os

_TEST_DISABLE_SANDBOX_ENV = "KORAIL_BROWSER_TEST_DISABLE_SANDBOX"


def isolated_test_chromium_arguments() -> tuple[str, ...]:
    """Relax Chromium's sandbox only in the isolated browser-test container."""
    if os.environ.get(_TEST_DISABLE_SANDBOX_ENV, "").strip().lower() == "true":
        return ("--no-sandbox",)
    return ()
