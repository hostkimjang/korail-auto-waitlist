r"""설치본의 역 목록 캐시 내보내기로 API 패키지용 내장 역 목록 스냅샷을 만든다.

입력은 ``station_catalog_cache`` 정규 행 하나를 담은 JSON 객체다(schema_version,
station_count, retrieved_at, refresh_after, payload). 실행 중인 설치에서 읽기 전용으로 내보낸다.

    docker exec <postgres 컨테이너> psql -U rail_waitlist -d rail_waitlist -At -c \
      "SELECT json_build_object('schema_version', schema_version,
        'station_count', station_count, 'retrieved_at', retrieved_at,
        'refresh_after', refresh_after, 'payload', payload)
       FROM station_catalog_cache WHERE cache_key = 'tago_station_catalog_all'" > cache.json

그다음 ``apps/api``에서 실행한다. 검증(노드 ID 중복 없음, 표시 역 ⊆ 식별 역, 0개 아님,
TAGO·코레일 역 안내 출처와 수집 시각 존재)을 모두 통과해야 기본 경로의 파일을 덮어쓴다.

    uv run --python 3.12 --frozen python scripts/build_bundled_station_catalog.py cache.json

``--output PATH``로 다른 위치에 쓸 수 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rail_waitlist.timetable_management.bundled_station_catalog import (
    BUNDLED_STATION_CATALOG_FORMAT,
    BUNDLED_STATION_CATALOG_FORMAT_VERSION,
    BUNDLED_STATION_CATALOG_RESOURCE,
    BUNDLED_STATION_CATALOG_SOURCE,
    BundledStationCatalogError,
    parse_bundled_station_catalog,
)
from rail_waitlist.timetable_management.catalog_application import (
    STATION_CATALOG_SCHEMA_VERSION,
)

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = API_ROOT.joinpath(
    "src", "rail_waitlist", "timetable_management", *BUNDLED_STATION_CATALOG_RESOURCE
)
STATION_FIELDS = ("node_id", "name", "city_code", "city_name")
STATION_LIST_KEYS = frozenset({"identity_stations", "display_stations"})


def _utc_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise BundledStationCatalogError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BundledStationCatalogError(f"{field} must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundledStationCatalogError(f"{field} must include a timezone")
    return parsed.astimezone(UTC).isoformat()


def _station_rows(value: object, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise BundledStationCatalogError(f"payload.{field} must be a list")
    rows: list[dict[str, object]] = []
    for station in value:
        if not isinstance(station, dict):
            raise BundledStationCatalogError(f"payload.{field} contains an invalid station")
        rows.append({key: station.get(key) for key in STATION_FIELDS})
    return rows


def build_document(export: object) -> dict[str, object]:
    """Keep only reviewed public fields from a station_catalog_cache export."""
    if not isinstance(export, dict):
        raise BundledStationCatalogError("export must be a JSON object")
    if export.get("schema_version") != STATION_CATALOG_SCHEMA_VERSION:
        raise BundledStationCatalogError(
            f"export schema_version must be {STATION_CATALOG_SCHEMA_VERSION}"
        )
    payload = export.get("payload")
    if not isinstance(payload, dict):
        raise BundledStationCatalogError("export payload must be a JSON object")
    visibility = payload.get("visibility")
    if not isinstance(visibility, dict):
        raise BundledStationCatalogError("payload.visibility must be a JSON object")
    identity_stations = _station_rows(payload.get("stations"), "stations")
    display_stations = _station_rows(payload.get("display_stations"), "display_stations")
    if export.get("station_count") != len(identity_stations):
        raise BundledStationCatalogError("station_count does not match payload.stations")

    document: dict[str, object] = {
        "format": BUNDLED_STATION_CATALOG_FORMAT,
        "format_version": BUNDLED_STATION_CATALOG_FORMAT_VERSION,
        "station_catalog_schema_version": STATION_CATALOG_SCHEMA_VERSION,
        "source": BUNDLED_STATION_CATALOG_SOURCE,
        "retrieved_at": _utc_timestamp(export.get("retrieved_at"), "retrieved_at"),
        "visibility": {
            "source": visibility.get("source"),
            "url": visibility.get("url"),
            "retrieved_at": _utc_timestamp(
                visibility.get("retrieved_at"), "payload.visibility.retrieved_at"
            ),
            "etag": visibility.get("etag"),
            "last_modified": visibility.get("last_modified"),
        },
        "identity_stations": identity_stations,
        "display_stations": display_stations,
    }
    parse_bundled_station_catalog(document, schema_version=STATION_CATALOG_SCHEMA_VERSION)
    return document


def render_document(document: dict[str, object]) -> str:
    """Render one station per line so snapshot refreshes stay reviewable in diffs."""
    lines = ["{"]
    entries = list(document.items())
    for index, (key, value) in enumerate(entries):
        separator = "," if index < len(entries) - 1 else ""
        if key in STATION_LIST_KEYS and isinstance(value, list):
            lines.append(f"  {json.dumps(key)}: [")
            for station_index, station in enumerate(value):
                station_separator = "," if station_index < len(value) - 1 else ""
                lines.append(f"    {json.dumps(station, ensure_ascii=False)}{station_separator}")
            lines.append(f"  ]{separator}")
            continue
        rendered = json.dumps(value, ensure_ascii=False, indent=2).replace("\n", "\n  ")
        lines.append(f"  {json.dumps(key)}: {rendered}{separator}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_document(document: dict[str, object], output: Path) -> None:
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(render_document(document), encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="내장 역 목록 스냅샷을 만듭니다.")
    parser.add_argument("input", type=Path, help="station_catalog_cache 행 JSON 파일")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="저장할 스냅샷 경로")
    arguments = parser.parse_args(argv)
    try:
        export: object = json.loads(arguments.input.read_text(encoding="utf-8"))
        document = build_document(export)
        write_document(document, arguments.output)
    except (OSError, ValueError) as error:
        print(f"내장 역 목록 스냅샷 생성 실패: {error}", file=sys.stderr)
        return 2
    catalog = parse_bundled_station_catalog(document, schema_version=STATION_CATALOG_SCHEMA_VERSION)
    print(
        "내장 역 목록 스냅샷을 저장했습니다: "
        f"식별 역 {len(catalog.identity_stations)}개, "
        f"표시 역 {len(catalog.display_stations)}개, "
        f"수집 시각 {catalog.retrieved_at.isoformat()} → {arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
