from __future__ import annotations

import os

_TEST_DISABLE_SANDBOX_ENV = "KORAIL_BROWSER_TEST_DISABLE_SANDBOX"


def isolated_test_chromium_arguments() -> tuple[str, ...]:
    """Return the isolated browser-test container's explicit sandbox override."""
    if os.environ.get(_TEST_DISABLE_SANDBOX_ENV, "").strip().lower() == "true":
        return ("--no-sandbox",)
    return ()
