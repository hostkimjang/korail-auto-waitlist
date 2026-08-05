from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from rail_waitlist.korail_browser_mode_smoke import (
    parser,
    require_private_output_platform,
    secure_output_file,
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
