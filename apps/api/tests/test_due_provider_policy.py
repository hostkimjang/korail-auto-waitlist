from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import rail_waitlist.observations.due_provider_policy as policy_module
import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("korail_background_enabled", "expected"),
    [
        (False, [Provider.SRT]),
        (True, [Provider.SRT, Provider.KORAIL]),
    ],
)
def test_provider_arm_targets_preserve_exact_type_order_and_gate(
    korail_background_enabled: bool,
    expected: list[Provider],
) -> None:
    targets = policy_module.select_provider_arm_targets(
        korail_background_enabled=korail_background_enabled
    )

    assert type(targets) is list
    assert targets == expected
    assert Provider.MOCK not in targets


def test_provider_arm_targets_returns_a_fresh_list() -> None:
    first = policy_module.select_provider_arm_targets(korail_background_enabled=True)
    first.clear()

    assert policy_module.select_provider_arm_targets(korail_background_enabled=True) == [
        Provider.SRT,
        Provider.KORAIL,
    ]


async def test_worker_evaluates_current_gate_before_selecting_arm_targets(monkeypatch) -> None:
    settings = object()
    selected = [Provider.SRT]
    events: list[object] = []

    class Metric:
        def inc(self, value: int) -> None:
            events.append(("metric", value))

    def gate(received_settings: object) -> bool:
        assert received_settings is settings
        events.append("gate")
        return False

    def select(*, korail_background_enabled: bool) -> list[Provider]:
        events.append(("select", korail_background_enabled))
        return selected

    async def process(providers_to_arm, *, dependencies) -> int:
        events.append(("process", providers_to_arm, dependencies))
        return 3

    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_module, "korail_background_monitoring_enabled", gate)
    monkeypatch.setattr(worker_module, "select_provider_arm_targets_policy", select)
    monkeypatch.setattr(worker_module, "process_due_pipeline", process)
    monkeypatch.setattr(worker_module, "WATCH_GROUPS", Metric())

    assert await worker_module._process_due_watches() == 3

    assert events[:2] == ["gate", ("select", False)]
    assert events[2][0] == "process"
    assert events[2][1] is selected
    assert events[2][2].session_factory is worker_module.SessionFactory
    assert events[3] == ("metric", 3)


async def test_worker_does_not_process_or_increment_metrics_when_gate_fails(monkeypatch) -> None:
    expected = LookupError("gate failed")
    calls: list[str] = []

    def fail(_settings: object) -> bool:
        raise expected

    async def process(*_args, **_kwargs) -> int:
        calls.append("process")
        return 0

    class Metric:
        def inc(self, _value: int) -> None:
            calls.append("metric")

    monkeypatch.setattr(worker_module, "get_settings", lambda: object())
    monkeypatch.setattr(worker_module, "korail_background_monitoring_enabled", fail)
    monkeypatch.setattr(worker_module, "process_due_pipeline", process)
    monkeypatch.setattr(worker_module, "WATCH_GROUPS", Metric())

    with pytest.raises(LookupError) as caught:
        await worker_module._process_due_watches()

    assert caught.value is expected
    assert calls == []


def test_due_provider_policy_import_orders_preserve_worker_binding() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "policy-first":
    import rail_waitlist.observations.due_provider_policy as Policy
    import rail_waitlist.worker as Worker
else:
    import rail_waitlist.worker as Worker
    import rail_waitlist.observations.due_provider_policy as Policy

print(json.dumps({
    "binding": Worker.select_provider_arm_targets_policy is Policy.select_provider_arm_targets,
    "module": Policy.select_provider_arm_targets.__module__,
}, sort_keys=True))
"""

    for import_order in ("policy-first", "worker-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "binding": True,
            "module": "rail_waitlist.observations.due_provider_policy",
        }
