from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_station_catalog.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("audit_station_catalog", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
audit_station_catalog = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(audit_station_catalog)


async def test_main_returns_setup_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_audit() -> int:
        raise RuntimeError("upstream failed with a secret-shaped detail")

    monkeypatch.setattr(audit_station_catalog, "audit", fail_audit)

    assert await audit_station_catalog.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "역 카탈로그 전수 점검 실패: RuntimeError\n"
    assert "secret-shaped" not in captured.err
