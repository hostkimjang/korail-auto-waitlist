from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.provider_adapters.tago import TagoClient
from rail_waitlist.timetable_management.station_catalog_supplements import (
    REVIEWED_TAGO_STATION_SUPPLEMENTS,
    apply_reviewed_tago_station_supplements,
)
from rail_waitlist.timetable_management.station_visibility import (
    KorailStationVisibility,
    filter_station_items,
    index_unique_station_items_by_name,
    normalize_visibility_station_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

KNOWN_SCOPE_REVIEW = frozenset({"인천공항t1", "인천공항t2"})


def _normalized(values: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset(normalize_visibility_station_name(value) for value in values)


def _format_names(values: set[str] | frozenset[str]) -> str:
    return ", ".join(sorted(values)) if values else "없음"


def _load_settings() -> Settings:
    # Settings resolves its configured `.env` from the working directory. The audit
    # is normally launched under `apps/api`, so resolve it from the repository root.
    original_directory = Path.cwd()
    try:
        os.chdir(PROJECT_ROOT)
        return Settings()
    finally:
        os.chdir(original_directory)


async def audit() -> int:
    settings = _load_settings()
    if not settings.tago_key():
        print(
            "역 카탈로그 전수 점검 실패: TAGO_SERVICE_KEY가 설정되지 않았습니다.", file=sys.stderr
        )
        return 2

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as http_client:
        tago_client = TagoClient(settings, http_client)
        visibility = KorailStationVisibility(http_client)
        tago_catalog, korail_roster = await asyncio.gather(
            tago_client.fetch_station_catalog(Provider.KORAIL),
            visibility.load_roster(),
        )

    identity_stations = apply_reviewed_tago_station_supplements(
        tago_catalog.stations,
        korail_roster.station_codes,
    )
    display_stations = filter_station_items(identity_stations, korail_roster)
    tago_names_by_key = {
        normalized_name: station.name
        for normalized_name, station in index_unique_station_items_by_name(
            identity_stations
        ).items()
    }
    korail_source_names = korail_roster.names
    official_only = set(korail_source_names - tago_names_by_key.keys())
    tago_only_keys = set(tago_names_by_key.keys() - korail_source_names)
    tago_only_names = {tago_names_by_key[key] for key in tago_only_keys}

    known_scope_review = _normalized(KNOWN_SCOPE_REVIEW)
    unreviewed_official_only = official_only - known_scope_review
    active_supplements = {
        supplement.name
        for supplement in REVIEWED_TAGO_STATION_SUPPLEMENTS
        if korail_roster.station_codes.get(normalize_visibility_station_name(supplement.name))
        == supplement.korail_station_code
    }

    print("역 카탈로그 전수 점검")
    print(f"- TAGO 목록 원본 identity: {len(tago_catalog.stations)}개")
    print(
        f"- 실조회 검증 보정 identity {len(active_supplements)}개: "
        f"{_format_names(active_supplements)}"
    )
    print(f"- 조회 identity 합계: {len(identity_stations)}개")
    print(f"- KORAIL 공개 역 안내 원본: {korail_roster.source_count or '확인 불가'}개")
    print(f"- KORAIL 여정 후보(통근역 제외): {len(korail_source_names)}개")
    print(f"- 이름 대응 identity: {len(tago_names_by_key.keys() & korail_source_names)}개")
    print(f"- 여정 선택 노출: {len(display_stations)}개")
    print(f"- 노출 범위 검토 중: {_format_names(official_only & known_scope_review)}")
    print(f"- 새 KORAIL-only 불일치: {_format_names(unreviewed_official_only)}")
    print(f"- KORAIL 안내에 없는 TAGO 이름 {len(tago_only_names)}개:")
    print(f"  {_format_names(tago_only_names)}")
    print(f"- KORAIL Last-Modified: {korail_roster.last_modified or '제공되지 않음'}")

    if unreviewed_official_only:
        print("결과: 새 불일치 검토가 필요합니다.", file=sys.stderr)
        return 1
    print("결과: 확인된 이름 대응과 승인된 미해결 목록 범위에서 통과했습니다.")
    return 0


async def main() -> int:
    try:
        return await audit()
    except Exception as error:
        print(
            f"역 카탈로그 전수 점검 실패: {type(error).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
