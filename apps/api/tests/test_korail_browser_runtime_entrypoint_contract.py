from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPOSITORY_ROOT / "compose.yml"
GUI_COMPOSE_PATH = REPOSITORY_ROOT / "compose.korail-gui.yml"
RUNTIME_ENTRYPOINT_PATH = (
    REPOSITORY_ROOT / "apps" / "api" / "scripts" / "start-korail-browser-runtime.sh"
)


def _adapter_block(compose: str) -> str:
    match = re.search(
        r"^  korail-browser-adapter:\n(?P<body>.*?)(?=^  [a-z0-9-]+:|\Z)",
        compose,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None
    return match.group()


def test_default_adapter_uses_internal_non_headless_display_without_publishing_novnc() -> None:
    adapter = _adapter_block(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert 'KORAIL_BROWSER_GUI_ENABLED: "${KORAIL_BROWSER_GUI_ENABLED:-true}"' in adapter
    assert 'KORAIL_NOVNC_ENABLED: "false"' in adapter
    assert "DISPLAY: :99" in adapter
    assert "XAUTHORITY: /tmp/korail-gui/Xauthority" in adapter
    assert "6080:6080" not in adapter
    assert "KORAIL_NOVNC_PASSWORD_FILE" not in adapter


def test_gui_override_alone_owns_loopback_novnc_and_password_secret() -> None:
    override = GUI_COMPOSE_PATH.read_text(encoding="utf-8")

    assert 'KORAIL_BROWSER_GUI_ENABLED: "true"' in override
    assert 'KORAIL_NOVNC_ENABLED: "true"' in override
    assert "KORAIL_NOVNC_PASSWORD_FILE: /run/secrets/korail_novnc_password" in override
    assert '"127.0.0.1:${KORAIL_NOVNC_PORT:-6080}:6080"' in override
    assert "korail_novnc_password" in override


def test_runtime_starts_novnc_only_when_a_password_file_is_configured() -> None:
    entrypoint = RUNTIME_ENTRYPOINT_PATH.read_text(encoding="utf-8")

    assert 'novnc_enabled="${KORAIL_NOVNC_ENABLED:-false}"' in entrypoint
    assert 'password_file="${KORAIL_NOVNC_PASSWORD_FILE:-}"' in entrypoint
    viewer_guards = re.findall(
        r'if \[\[ "\$\{novnc_enabled\}" == "true" \]\]; then(?P<body>.*?)\nfi',
        entrypoint,
        flags=re.DOTALL,
    )
    viewer_guard = next((body for body in viewer_guards if "x11vnc " in body), None)
    assert viewer_guard is not None
    assert "websockify " in viewer_guard
    assert "VNC/noVNC proxy did not become ready" in viewer_guard
    assert "GUI mode requires a readable KORAIL_NOVNC_PASSWORD_FILE" not in entrypoint
    assert "KORAIL_NOVNC_ENABLED=true requires KORAIL_BROWSER_GUI_ENABLED=true" in entrypoint


def _run_entrypoint(*, gui_enabled: str, novnc_enabled: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "apps/api/scripts/start-korail-browser-runtime.sh", "true"],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "KORAIL_BROWSER_GUI_ENABLED": gui_enabled,
            "KORAIL_NOVNC_ENABLED": novnc_enabled,
        },
        capture_output=True,
        check=False,
        errors="replace",
        text=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows bash does not inherit test env reliably")
def test_runtime_executes_headless_command_when_viewer_is_disabled() -> None:
    result = _run_entrypoint(gui_enabled="false", novnc_enabled="false")

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="Windows bash does not inherit test env reliably")
def test_runtime_rejects_novnc_when_browser_gui_is_disabled() -> None:
    result = _run_entrypoint(gui_enabled="false", novnc_enabled="true")

    assert result.returncode == 64
    assert result.stderr == "KORAIL_NOVNC_ENABLED=true requires KORAIL_BROWSER_GUI_ENABLED=true\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows bash does not inherit test env reliably")
def test_runtime_rejects_novnc_without_a_readable_secret() -> None:
    result = _run_entrypoint(gui_enabled="true", novnc_enabled="true")

    assert result.returncode == 64
    assert (
        result.stderr == "KORAIL_NOVNC_ENABLED=true requires a readable "
        "KORAIL_NOVNC_PASSWORD_FILE\n"
    )
