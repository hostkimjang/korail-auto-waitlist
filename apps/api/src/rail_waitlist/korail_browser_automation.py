from __future__ import annotations

import asyncio as asyncio
import ipaddress as ipaddress
import logging as logging
import re as re
import time as time
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from datetime import UTC as UTC
from datetime import date as date
from datetime import datetime as datetime
from datetime import time as clock_time  # noqa: F401 -- compatibility export.
from datetime import timedelta as timedelta
from typing import Literal as Literal
from typing import Protocol as Protocol
from urllib.parse import urlsplit as urlsplit
from zoneinfo import ZoneInfo as ZoneInfo

from pydantic import BaseModel as BaseModel
from pydantic import ConfigDict as ConfigDict
from pydantic import Field as Field
from pydantic import field_validator as field_validator
from pydantic import model_validator as model_validator

from .korail_sidecar.browser_contracts import SOURCE_NAME as SOURCE_NAME
from .korail_sidecar.browser_contracts import AdapterErrorReason as AdapterErrorReason
from .korail_sidecar.browser_contracts import AdapterModel as AdapterModel
from .korail_sidecar.browser_contracts import BrowserAdapterError as BrowserAdapterError
from .korail_sidecar.browser_contracts import BrowserClient as BrowserClient
from .korail_sidecar.browser_contracts import (
    BrowserProtectionDetected as BrowserProtectionDetected,
)
from .korail_sidecar.browser_contracts import BrowserRateLimited as BrowserRateLimited
from .korail_sidecar.browser_contracts import (
    BrowserSeatSearchRequest as BrowserSeatSearchRequest,
)
from .korail_sidecar.browser_contracts import (
    BrowserSeatSearchResult as BrowserSeatSearchResult,
)
from .korail_sidecar.browser_contracts import (
    BrowserSourceUnavailable as BrowserSourceUnavailable,
)
from .korail_sidecar.browser_contracts import BrowserTrainSnapshot as BrowserTrainSnapshot
from .korail_sidecar.browser_contracts import KorailTrainType as KorailTrainType
from .korail_sidecar.browser_contracts import ProtectionTrigger as ProtectionTrigger
from .korail_sidecar.browser_contracts import SeatStatus as SeatStatus
from .korail_sidecar.browser_page_contracts import (
    FULLSTACK_E2E_PAGE_URL as FULLSTACK_E2E_PAGE_URL,
)
from .korail_sidecar.browser_page_contracts import (
    OFFICIAL_KORAIL_SEARCH_URL as OFFICIAL_KORAIL_SEARCH_URL,
)
from .korail_sidecar.browser_protection import (
    GENERIC_PROTECTION_TRIGGERS as GENERIC_PROTECTION_TRIGGERS,
)
from .korail_sidecar.browser_protection import PROTECTION_MARKERS as PROTECTION_MARKERS
from .korail_sidecar.browser_protection import (
    RATE_LIMIT_RESOURCE_TYPES as RATE_LIMIT_RESOURCE_TYPES,
)
from .korail_sidecar.browser_protection import (
    is_rate_limit_response as is_rate_limit_response,
)
from .korail_sidecar.browser_protection import (
    protection_trigger_from_http_response as protection_trigger_from_http_response,
)
from .korail_sidecar.browser_protection import (
    protection_trigger_from_text as protection_trigger_from_text,
)
from .korail_sidecar.direct_cdp import DirectCdpLaunchError as DirectCdpLaunchError
from .korail_sidecar.direct_cdp import open_direct_cdp_browser as open_direct_cdp_browser
from .korail_sidecar.playwright.client import (
    PROTECTION_SURFACE_SELECTOR as PROTECTION_SURFACE_SELECTOR,
)
from .korail_sidecar.playwright.client import (
    PlaywrightKorailBrowserClient as PlaywrightKorailBrowserClient,
)
from .korail_sidecar.playwright.client import logger as logger
from .korail_sidecar.playwright.client import probe_chromium as probe_chromium
from .korail_sidecar.playwright.result_reader import ROUTE_HEADING as ROUTE_HEADING
from .korail_sidecar.playwright.result_reader import (
    _normalize_station as _normalize_station,
)
from .korail_sidecar.playwright.result_reader import (
    _normalize_train_number as _normalize_train_number,
)
from .korail_sidecar.search_coordinator import (
    KorailBrowserAutomation as KorailBrowserAutomation,
)
from .korail_sidecar.search_coordinator import _CacheEntry as _CacheEntry
from .korail_sidecar.search_coordinator import _Cooldown as _Cooldown
from .korail_sidecar.search_result_policy import (
    ADULT_FARE_PATTERN as ADULT_FARE_PATTERN,
)
from .korail_sidecar.search_result_policy import (
    DELAY_ESTIMATE_PATTERN as DELAY_ESTIMATE_PATTERN,
)
from .korail_sidecar.search_result_policy import KST as KST
from .korail_sidecar.search_result_policy import (
    OFFICIAL_TRAIN_TYPE_PATTERN as OFFICIAL_TRAIN_TYPE_PATTERN,
)
from .korail_sidecar.search_result_policy import (
    is_supported_korail_train_kind as is_supported_korail_train_kind,
)
from .korail_sidecar.search_result_policy import (
    parse_expected_delay_minutes as parse_expected_delay_minutes,
)
from .korail_sidecar.search_result_policy import (
    parse_official_train_type as parse_official_train_type,
)
from .korail_sidecar.search_result_policy import (
    parse_unambiguous_adult_fare as parse_unambiguous_adult_fare,
)
from .korail_sidecar.search_result_policy import service_datetimes as service_datetimes
from .korail_sidecar.search_result_policy import status_from_seat_box as status_from_seat_box
from .korail_sidecar.search_result_policy import (
    visible_departure_matches as visible_departure_matches,
)
from .provider_registry.korail_search_url_policy import (
    validate_korail_general_search_url as validate_korail_general_search_url,
)
