"""Compatibility facade for the KORAIL sidecar Chromium launch policy."""

from __future__ import annotations

from .korail_sidecar import chromium_launch as _owner

os = _owner.os
_TEST_DISABLE_SANDBOX_ENV = _owner._TEST_DISABLE_SANDBOX_ENV
isolated_test_chromium_arguments = _owner.isolated_test_chromium_arguments
