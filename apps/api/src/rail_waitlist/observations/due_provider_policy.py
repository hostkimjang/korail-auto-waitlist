from __future__ import annotations

from ..domain import Provider


def select_provider_arm_targets(*, korail_background_enabled: bool) -> list[Provider]:
    """Return the ordered providers whose watches should be armed before a due sweep."""
    providers = [Provider.SRT]
    if korail_background_enabled:
        providers.append(Provider.KORAIL)
    return providers
