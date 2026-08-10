from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPOSITORY_ROOT / "compose.yml"
SECCOMP_PROFILE_PATH = REPOSITORY_ROOT / "infra" / "docker" / "playwright-seccomp-v1.55.0.json"
SECCOMP_COMPOSE_OPTION = "seccomp:./infra/docker/playwright-seccomp-v1.55.0.json"
NAMESPACE_SYSCALLS = {"clone", "setns", "unshare"}
PLAYWRIGHT_V1_55_0_SECCOMP_SHA256 = (
    "cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849"
)


def _seccomp_profile() -> dict[str, Any]:
    payload = json.loads(SECCOMP_PROFILE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _compose_service_block(service_name: str) -> str:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(service_name)}:\n(?P<body>.*?)(?=^  [a-z0-9-]+:|\Z)",
        compose,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None
    return match.group()


def _service_list(service_block: str, field_name: str) -> list[str]:
    match = re.search(
        rf"^    {re.escape(field_name)}:\n(?P<items>(?:^      - .+\n)+)",
        service_block,
        flags=re.MULTILINE,
    )
    assert match is not None
    return [line.removeprefix("      - ") for line in match.group("items").splitlines()]


def test_playwright_seccomp_profile_supports_arm_user_namespaces() -> None:
    canonical_bytes = SECCOMP_PROFILE_PATH.read_bytes().replace(b"\r\n", b"\n")
    profile = _seccomp_profile()

    assert hashlib.sha256(canonical_bytes).hexdigest() == PLAYWRIGHT_V1_55_0_SECCOMP_SHA256
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert any(entry.get("architecture") == "SCMP_ARCH_AARCH64" for entry in profile["archMap"])
    assert any(
        syscall.get("action") == "SCMP_ACT_ALLOW"
        and NAMESPACE_SYSCALLS <= set(syscall.get("names", []))
        and syscall.get("args") == []
        and syscall.get("includes") == {}
        and syscall.get("excludes") == {}
        for syscall in profile["syscalls"]
    )


def test_only_korail_browser_adapter_uses_pinned_seccomp_with_minimal_chroot_capability() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    adapter = _compose_service_block("korail-browser-adapter")

    assert compose.count(SECCOMP_COMPOSE_OPTION) == 1
    assert "    user: pwuser\n" in adapter
    assert "    read_only: true\n" in adapter
    assert _service_list(adapter, "cap_drop") == ["ALL"]
    assert _service_list(adapter, "cap_add") == ["SYS_CHROOT"]
    assert _service_list(adapter, "security_opt") == [
        "no-new-privileges:true",
        SECCOMP_COMPOSE_OPTION,
    ]
    assert "unconfined" not in adapter.lower()
    assert "SYS_ADMIN" not in adapter
