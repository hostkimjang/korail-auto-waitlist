from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..domain import Provider
from .due_pipeline_application import DuePipelineDependencies


class ProviderArmTargetSelector(Protocol):
    def __call__(self, *, korail_background_enabled: bool) -> list[Provider]: ...


class DuePipelineProcessor(Protocol):
    async def __call__(
        self,
        providers_to_arm: Sequence[Provider],
        *,
        dependencies: DuePipelineDependencies,
    ) -> int: ...


@dataclass(frozen=True)
class DueSweepRuntimeDependencies:
    korail_background_enabled: Callable[[], bool]
    select_provider_arm_targets: ProviderArmTargetSelector
    process_due_pipeline: DuePipelineProcessor
    due_pipeline_dependencies: Callable[[], DuePipelineDependencies]
    record_group_count: Callable[[int], None]


async def process_due_watches(*, dependencies: DueSweepRuntimeDependencies) -> int:
    """Coordinate one due sweep without owning settings, Celery, or metrics globals."""

    providers_to_arm = dependencies.select_provider_arm_targets(
        korail_background_enabled=dependencies.korail_background_enabled()
    )
    group_count = await dependencies.process_due_pipeline(
        providers_to_arm,
        dependencies=dependencies.due_pipeline_dependencies(),
    )
    dependencies.record_group_count(group_count)
    return group_count
