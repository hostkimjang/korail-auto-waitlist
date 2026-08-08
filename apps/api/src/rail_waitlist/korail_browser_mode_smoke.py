from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
from collections import Counter
from datetime import date
from datetime import time as clock_time
from pathlib import Path
from typing import Any

from .korail_pydoll_browser import (
    PydollKorailBrowserClient,
    _PydollSession,
    _PydollSessionContext,
)
from .korail_sidecar.browser_contracts import BrowserAdapterError, BrowserSeatSearchRequest
from .korail_sidecar.pydoll.page_contracts import PydollPageSnapshot
from .provider_adapters.korail_search_bootstrap import KorailStationIdentityResolver


def require_private_output_platform() -> None:
    if os.name != "posix":
        raise RuntimeError(
            "browser-mode captures require a POSIX runtime; run this smoke inside "
            "the Linux browser container"
        )


def secure_output_directory(path: Path) -> None:
    path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise PermissionError(f"could not restrict output directory permissions: {path}")


def secure_output_file(path: Path) -> None:
    try:
        path.chmod(0o600)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PermissionError(f"could not restrict output file permissions: {path}")
    except OSError:
        path.unlink(missing_ok=True)
        raise


class CapturingPydollSession(_PydollSession):
    def __init__(
        self,
        page_url: str,
        timeout_ms: int,
        headless: bool,
        *,
        page_capture_path: Path,
        desktop_capture_path: Path | None,
        capture_failures: list[str],
    ) -> None:
        super().__init__(page_url, timeout_ms, headless)
        self._page_capture_path = page_capture_path
        self._desktop_capture_path = desktop_capture_path
        self._capture_failures = capture_failures

    async def _snapshot(self) -> PydollPageSnapshot:
        snapshot = await super()._snapshot()
        await self._capture_current_surface()
        return snapshot

    async def _capture_current_surface(self) -> None:
        tab = self._tab
        if tab is not None:
            try:
                await tab.take_screenshot(
                    path=str(self._page_capture_path),
                    beyond_viewport=False,
                )
                secure_output_file(self._page_capture_path)
            except Exception:
                self._capture_failures.append("page")
        if self._desktop_capture_path is not None:
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["scrot", "-o", str(self._desktop_capture_path)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                secure_output_file(self._desktop_capture_path)
            except (OSError, subprocess.CalledProcessError):
                self._capture_failures.append("desktop")


class HoldingSessionContext(_PydollSessionContext):
    def __init__(self, session: CapturingPydollSession, hold_seconds: int) -> None:
        super().__init__(session)
        self._hold_seconds = hold_seconds

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None:
        if self._hold_seconds:
            await asyncio.sleep(self._hold_seconds)
        return await super().__aexit__(exc_type, exc_value, traceback)


def parse_clock_time(value: str) -> clock_time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return clock_time(hour, minute)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("time must use HH:MM") from error


def file_evidence(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    payload = path.read_bytes()
    return {
        "name": path.name,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


async def run(args: argparse.Namespace) -> int:
    require_private_output_platform()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_output_directory(output_dir)
    page_capture_path = output_dir / f"{args.mode}-page.png"
    desktop_capture_path = output_dir / f"{args.mode}-desktop.png" if args.mode == "gui" else None
    capture_paths = [page_capture_path]
    if desktop_capture_path is not None:
        capture_paths.append(desktop_capture_path)
    for capture_path in capture_paths:
        capture_path.unlink(missing_ok=True)
    capture_failures: list[str] = []
    headless = args.mode == "headless"

    def session_factory(page_url: str, timeout_ms: int, factory_headless: bool):
        if factory_headless is not headless:
            raise RuntimeError("browser mode did not reach the Pydoll session factory")
        return HoldingSessionContext(
            CapturingPydollSession(
                page_url,
                timeout_ms,
                factory_headless,
                page_capture_path=page_capture_path,
                desktop_capture_path=desktop_capture_path,
                capture_failures=capture_failures,
            ),
            args.hold_seconds,
        )

    request = BrowserSeatSearchRequest(
        origin=args.origin,
        destination=args.destination,
        travel_date=args.travel_date,
        departure_from=args.departure_from,
        departure_to=args.departure_to,
        passenger_count=1,
    )
    client = PydollKorailBrowserClient(
        headless=headless,
        timeout_seconds=args.timeout_seconds,
        session_factory=session_factory,
        session_reuse_ttl_seconds=0,
        session_reuse_max_searches=1,
        station_identity_resolver=KorailStationIdentityResolver(),
    )
    exit_code = 0
    summary: dict[str, Any] = {
        "mode": args.mode,
        "query": {
            "origin": args.origin,
            "destination": args.destination,
            "travel_date": args.travel_date.isoformat(),
            "departure_from": args.departure_from.strftime("%H:%M"),
            "departure_to": args.departure_to.strftime("%H:%M"),
            "passenger_count": 1,
        },
    }
    try:
        result = await asyncio.wait_for(
            client.search(request), timeout=args.overall_timeout_seconds
        )
        statuses = Counter(
            status for train in result.trains for status in (train.standard, train.first)
        )
        summary.update(
            outcome="success",
            train_count=len(result.trains),
            seat_status_counts=dict(sorted(statuses.items())),
        )
    except BrowserAdapterError as error:
        exit_code = 2
        summary.update(
            outcome=error.reason,
            trigger=getattr(error, "trigger", None),
            stage=getattr(error, "stage", "unspecified"),
        )
    except TimeoutError:
        exit_code = 3
        summary.update(outcome="local_timeout")
    finally:
        await client.close()

    summary["page_capture"] = file_evidence(page_capture_path)
    summary["desktop_capture"] = (
        file_evidence(desktop_capture_path) if desktop_capture_path is not None else None
    )
    summary["capture_failures"] = sorted(set(capture_failures))
    summary_path = output_dir / f"{args.mode}-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    secure_output_file(summary_path)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return exit_code


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Run one local KORAIL browser-mode smoke. Captures may contain sensitive data."
        )
    )
    value.add_argument("--mode", choices=("gui", "headless"), required=True)
    value.add_argument("--origin", required=True)
    value.add_argument("--destination", required=True)
    value.add_argument("--travel-date", type=date.fromisoformat, required=True)
    value.add_argument("--departure-from", type=parse_clock_time, required=True)
    value.add_argument("--departure-to", type=parse_clock_time, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--hold-seconds", type=int, choices=range(61), default=0)
    value.add_argument("--timeout-seconds", type=float, default=30)
    value.add_argument("--overall-timeout-seconds", type=float, default=120)
    return value


def main() -> None:
    raise SystemExit(asyncio.run(run(parser().parse_args())))


if __name__ == "__main__":
    main()
