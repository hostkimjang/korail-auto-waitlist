from __future__ import annotations

import importlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist.korail_browser_mode_smoke import (
    parser,
    require_private_output_platform,
    secure_output_file,
)


@pytest.mark.skipif(
    os.getenv("KORAIL_BROWSER_IMAGE_TEST") != "true",
    reason="Checks the built browser test image, not the host Python environment",
)
def test_browser_image_uses_an_isolated_python_environment() -> None:
    environment = Path(os.environ["VIRTUAL_ENV"])
    assert sys.prefix != sys.base_prefix
    assert Path(sys.prefix) == environment
    assert Path(sys.executable).is_relative_to(environment / "bin")

    for name in ("pydoll", "playwright", "uvicorn", "pytest"):
        module = importlib.import_module(name)
        assert module.__file__ is not None
        assert Path(module.__file__).is_relative_to(environment)

    for command in ("uvicorn", "pytest"):
        executable = shutil.which(command)
        assert executable is not None
        assert Path(executable).is_relative_to(environment / "bin")
        subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            timeout=10,
        )


def test_smoke_help_warns_that_captures_may_be_sensitive() -> None:
    assert "Captures may contain sensitive data" in parser().description


def test_private_output_platform_fails_closed_outside_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")

    with pytest.raises(RuntimeError, match="POSIX runtime"):
        require_private_output_platform()


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not expose POSIX mode bits")
def test_secure_output_file_restricts_permissions(tmp_path: Path) -> None:
    output = tmp_path / "capture.png"
    output.write_bytes(b"capture")
    output.chmod(0o666)

    secure_output_file(output)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_secure_output_file_removes_capture_when_chmod_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "capture.png"
    output.write_bytes(b"capture")

    def fail_chmod(self: Path, mode: int) -> None:
        raise OSError("permission update failed")

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    with pytest.raises(OSError, match="permission update failed"):
        secure_output_file(output)

    assert not output.exists()
