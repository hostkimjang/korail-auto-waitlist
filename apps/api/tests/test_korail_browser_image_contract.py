import re
from pathlib import Path
from string import Template

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.browser"


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_browser_image_selects_official_chrome_package_for_supported_architecture(
    architecture: str,
) -> None:
    dockerfile = _dockerfile_text()
    url_match = re.search(
        r"https://dl\.google\.com/linux/direct/"
        r"google-chrome-stable_current_\$\{TARGETARCH\}\.deb",
        dockerfile,
    )

    assert "ARG TARGETARCH" in dockerfile
    assert url_match is not None
    assert Template(url_match.group()).substitute(TARGETARCH=architecture) == (
        "https://dl.google.com/linux/direct/"
        f"google-chrome-stable_current_{architecture}.deb"
    )
    assert "KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome" in dockerfile


def test_browser_image_rejects_unsupported_architectures() -> None:
    dockerfile = _dockerfile_text()
    architecture_case = re.search(
        r'case "\$\{TARGETARCH\}" in(?P<body>.*?)esac',
        dockerfile,
        flags=re.DOTALL,
    )

    assert architecture_case is not None
    case_body = architecture_case.group("body")
    supported_branch = re.search(r"(?P<architectures>[a-z0-9|]+)\)\s*;;", case_body)
    assert supported_branch is not None
    assert supported_branch.group("architectures").split("|") == ["amd64", "arm64"]
    assert re.search(
        r'\*\)\s*echo "Unsupported architecture: \$\{TARGETARCH\}" >&2;\s*'
        r"exit 64\s*;;",
        case_body,
    )
