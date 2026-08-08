from __future__ import annotations

from typing import cast

import pytest

from rail_waitlist.domain import Provider
from rail_waitlist.observations.due_pipeline_application import DuePipelineDependencies
from rail_waitlist.observations.due_runtime import (
    DueSweepRuntimeDependencies,
    process_due_watches,
)


@pytest.mark.parametrize(
    ("korail_enabled", "expected_providers"),
    [
        (False, [Provider.SRT]),
        (True, [Provider.SRT, Provider.KORAIL]),
    ],
)
async def test_due_sweep_preserves_gate_pipeline_and_metric_order(
    korail_enabled: bool,
    expected_providers: list[Provider],
) -> None:
    events: list[object] = []
    pipeline_dependencies = cast(DuePipelineDependencies, object())

    def current_gate() -> bool:
        events.append("gate")
        return korail_enabled

    def select(*, korail_background_enabled: bool) -> list[Provider]:
        events.append(("select", korail_background_enabled))
        return expected_providers

    def build_pipeline_dependencies() -> DuePipelineDependencies:
        events.append("dependencies")
        return pipeline_dependencies

    async def process(
        providers_to_arm,
        *,
        dependencies: DuePipelineDependencies,
    ) -> int:
        events.append(("process", providers_to_arm, dependencies))
        return 4

    def record_group_count(value: int) -> None:
        events.append(("metric", value))

    result = await process_due_watches(
        dependencies=DueSweepRuntimeDependencies(
            korail_background_enabled=current_gate,
            select_provider_arm_targets=select,
            process_due_pipeline=process,
            due_pipeline_dependencies=build_pipeline_dependencies,
            record_group_count=record_group_count,
        )
    )

    assert result == 4
    assert events == [
        "gate",
        ("select", korail_enabled),
        "dependencies",
        ("process", expected_providers, pipeline_dependencies),
        ("metric", 4),
    ]


async def test_due_sweep_gate_failure_skips_selection_pipeline_and_metric() -> None:
    expected = LookupError("gate failed")
    calls: list[str] = []

    def fail_gate() -> bool:
        raise expected

    def unexpected(*_args, **_kwargs):
        calls.append("unexpected")
        raise AssertionError("downstream dependency must not run")

    with pytest.raises(LookupError) as caught:
        await process_due_watches(
            dependencies=DueSweepRuntimeDependencies(
                korail_background_enabled=fail_gate,
                select_provider_arm_targets=unexpected,
                process_due_pipeline=unexpected,
                due_pipeline_dependencies=unexpected,
                record_group_count=unexpected,
            )
        )

    assert caught.value is expected
    assert calls == []


async def test_due_sweep_pipeline_failure_does_not_publish_a_metric() -> None:
    expected = RuntimeError("pipeline failed")
    metric_values: list[int] = []

    async def fail_pipeline(*_args, **_kwargs) -> int:
        raise expected

    with pytest.raises(RuntimeError) as caught:
        await process_due_watches(
            dependencies=DueSweepRuntimeDependencies(
                korail_background_enabled=lambda: False,
                select_provider_arm_targets=lambda **_kwargs: [Provider.SRT],
                process_due_pipeline=fail_pipeline,
                due_pipeline_dependencies=lambda: cast(DuePipelineDependencies, object()),
                record_group_count=metric_values.append,
            )
        )

    assert caught.value is expected
    assert metric_values == []
