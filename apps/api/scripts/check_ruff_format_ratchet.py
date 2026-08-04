from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = PROJECT_ROOT / "ruff-format-legacy.txt"
RUFF_VERSION = "0.12.12"
WOULD_REFORMAT = re.compile(r"^Would reformat: (.+)$", re.MULTILINE)


def normalize_relative_path(raw_path: str) -> str:
    normalized = raw_path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"프로젝트 상대 경로가 아닙니다: {raw_path}")
    return path.as_posix()


def read_allowlist() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            digest, raw_path = line.split(maxsplit=1)
            relative_path = normalize_relative_path(raw_path)
        except ValueError as exc:
            raise ValueError(f"{ALLOWLIST_PATH.name}:{line_number}: {exc}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{ALLOWLIST_PATH.name}:{line_number}: SHA-256 해시 형식이 아닙니다.")
        if relative_path in entries:
            raise ValueError(
                f"{ALLOWLIST_PATH.name}:{line_number}: 경로가 중복되었습니다: {relative_path}"
            )
        entries[relative_path] = digest
    return entries


def file_sha256(relative_path: str) -> str:
    data = (PROJECT_ROOT / relative_path).read_bytes()
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def run_ruff_format_check() -> tuple[int, str, set[str]]:
    uvx = shutil.which("uvx")
    if uvx is None:
        raise RuntimeError("uvx를 찾을 수 없습니다.")
    completed = subprocess.run(
        [uvx, "--from", f"ruff=={RUFF_VERSION}", "ruff", "format", "--check", "."],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    paths = {normalize_relative_path(match) for match in WOULD_REFORMAT.findall(output)}
    return completed.returncode, output, paths


def main() -> int:
    try:
        allowlist = read_allowlist()
        returncode, output, unformatted = run_ruff_format_check()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Ruff format ratchet을 실행하지 못했습니다: {exc}", file=sys.stderr)
        return 2

    if returncode not in {0, 1} or (returncode == 1 and not unformatted):
        print(output, file=sys.stderr)
        print("Ruff format 검사 결과를 해석하지 못했습니다.", file=sys.stderr)
        return 2

    allowed_paths = set(allowlist)
    unexpected = sorted(unformatted - allowed_paths)
    stale = sorted(allowed_paths - unformatted)
    changed = sorted(
        path for path in unformatted & allowed_paths if file_sha256(path) != allowlist[path]
    )

    if unexpected or stale or changed:
        if unexpected:
            print("새로운 미포맷 파일(먼저 `ruff format <경로>`를 실행하세요):", file=sys.stderr)
            for path in unexpected:
                print(f"  - {path}", file=sys.stderr)
        if changed:
            print("수정되었지만 여전히 미포맷인 legacy 파일:", file=sys.stderr)
            for path in changed:
                print(f"  - {path}", file=sys.stderr)
        if stale:
            print(
                "더 이상 미포맷이 아닌 stale allowlist 항목(목록에서 제거하세요):", file=sys.stderr
            )
            for path in stale:
                print(f"  - {path}", file=sys.stderr)
        return 1

    print(f"Ruff format ratchet 통과: legacy 미포맷 파일 {len(unformatted)}개를 격리했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
