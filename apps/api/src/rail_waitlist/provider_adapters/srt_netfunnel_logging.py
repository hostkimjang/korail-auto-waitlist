from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal

from SRT.errors import SRTNetFunnelError  # type: ignore[import-untyped]
from SRT.netfunnel import NetFunnelHelper  # type: ignore[import-untyped]

from ..provider_call_context import current_provider_call_id

_LOGGER = logging.getLogger("rail_waitlist.srt_provider_adapter")


class LoggingNetFunnelHelper(NetFunnelHelper):  # type: ignore[misc]
    """Add secret-free lifecycle logs without changing SRTrain's queue behavior."""

    def __init__(
        self,
        *,
        flow: Literal["accountless", "authenticated"],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._flow = flow
        self._monotonic = monotonic
        self._queue_started_at: float | None = None
        self._last_waiting_count: int | None = None

    def generate_netfunnel_key(self, use_cache: bool) -> str:
        self._reset_queue_observation()
        try:
            key = str(super().generate_netfunnel_key(use_cache))
        except Exception:  # noqa: BLE001 -- vendor failures can retain raw provider material.
            if self._queue_started_at is not None:
                _LOGGER.warning(
                    "SRT 공식 접속 대기열 처리에 실패했습니다 "
                    "event=provider_queue_failed "
                    "provider_call_id=%s flow=%s outcome=failed elapsed_ms=%s",
                    self._log_provider_call_id(),
                    self._flow,
                    self._elapsed_ms(),
                )
            # SRTrain's exception messages may contain a NetFunnel response, request URL,
            # or issued key. Close the exception chain at this boundary as well as the log.
            raise SRTNetFunnelError("NetFunnel request failed") from None
        else:
            if self._queue_started_at is not None:
                # SRTrain returns here only after both queue admission and set-complete
                # succeeded. The provider operation can now continue with the issued key.
                _LOGGER.info(
                    "SRT 공식 접속 대기열이 끝나 운영사 요청을 계속합니다 "
                    "event=provider_queue_released "
                    "provider_call_id=%s flow=%s elapsed_ms=%s",
                    self._log_provider_call_id(),
                    self._flow,
                    self._elapsed_ms(),
                )
            return key
        finally:
            self._reset_queue_observation()

    def _wait_until_complete(self, key: str, nwait: str) -> str:
        """Observe vendor queue progress while leaving every request and retry to SRTrain."""

        waiting_count = self._safe_waiting_count(nwait)
        if self._queue_started_at is None:
            self._queue_started_at = self._monotonic()
            self._last_waiting_count = waiting_count
            _LOGGER.info(
                "SRT 공식 접속 대기열에 들어갑니다 "
                "event=provider_queue_entered provider_call_id=%s "
                "flow=%s waiting_count=%s",
                self._log_provider_call_id(),
                self._flow,
                "unknown" if waiting_count is None else waiting_count,
            )
        elif waiting_count != self._last_waiting_count:
            self._last_waiting_count = waiting_count
            _LOGGER.info(
                "SRT 공식 접속 대기 인원이 변경되었습니다 "
                "event=provider_queue_waiting_count_changed "
                "provider_call_id=%s flow=%s waiting_count=%s",
                self._log_provider_call_id(),
                self._flow,
                "unknown" if waiting_count is None else waiting_count,
            )
        # SRTrain owns the request parameters, response parsing, polling interval,
        # key transition, completion notification, and recursive continuation.
        return str(super()._wait_until_complete(key, nwait))

    @staticmethod
    def _safe_waiting_count(value: str) -> int | None:
        normalized = value.strip()
        if not normalized.isascii() or not normalized.isdigit() or len(normalized) > 7:
            return None
        return int(normalized)

    def _elapsed_ms(self) -> int:
        if self._queue_started_at is None:
            return 0
        return max(0, round((self._monotonic() - self._queue_started_at) * 1000))

    @staticmethod
    def _log_provider_call_id() -> str:
        return current_provider_call_id() or "unavailable"

    def _reset_queue_observation(self) -> None:
        self._queue_started_at = None
        self._last_waiting_count = None
