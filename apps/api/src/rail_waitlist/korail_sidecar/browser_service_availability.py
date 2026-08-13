"""Classify explicit KORAIL service-outage pages without retaining provider content."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from .browser_contracts import BrowserSourceUnavailable

ProviderUnavailableTrigger = Literal["maintenance_page", "service_outage_page"]

_OFFICIAL_HOSTS = frozenset(
    {"korail.com", "www.korail.com", "letskorail.com", "www.letskorail.com"}
)
_MAINTENANCE_PATH = "/rejectservice_job.html"
_SERVICE_SUSPENSION = re.compile(r"서비스를\s*일시\s*중지", re.IGNORECASE)
_TICKET_SALES = re.compile(r"승차권\s*예약\s*및\s*발매\s*서비스", re.IGNORECASE)


class BrowserProviderUnavailable(BrowserSourceUnavailable):
    """An explicit provider-wide outage, projected publicly as ``source_unavailable``."""

    def __init__(
        self,
        trigger: ProviderUnavailableTrigger,
        stage: str = "unspecified",
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        if retry_after_seconds is not None and not 1 <= retry_after_seconds <= 86400:
            raise ValueError("retry_after_seconds must be between 1 and 86400")
        self.trigger = trigger
        self.retry_after_seconds = retry_after_seconds
        super().__init__(stage)

    def with_retry_after(self, seconds: int) -> BrowserProviderUnavailable:
        return BrowserProviderUnavailable(
            self.trigger,
            self.stage,
            retry_after_seconds=seconds,
        )


def provider_unavailable_trigger_from_page(
    url: str,
    body_text: str,
    *,
    has_result_rows: bool = False,
) -> ProviderUnavailableTrigger | None:
    """Recognize only high-confidence official paths or paired suspension markers."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        parsed = None
    try:
        official_page = (
            parsed is not None
            and parsed.scheme == "https"
            and (parsed.hostname or "").lower().rstrip(".") in _OFFICIAL_HOSTS
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        official_page = False
    if (
        official_page
        and parsed is not None
        and (
            parsed.path.rstrip("/").rsplit("/", 1)[-1].casefold()
            == _MAINTENANCE_PATH.rsplit("/", 1)[-1].casefold()
        )
    ):
        return "maintenance_page"
    if (
        official_page
        and not has_result_rows
        and _SERVICE_SUSPENSION.search(body_text)
        and _TICKET_SALES.search(body_text)
    ):
        return "service_outage_page"
    return None


def decode_provider_page_text(body: bytes) -> str:
    """Decode bounded response bytes only for in-memory marker classification."""

    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="ignore")
