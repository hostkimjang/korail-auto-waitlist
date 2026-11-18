from __future__ import annotations

import importlib.util
import json
from importlib import resources
from pathlib import Path

import pytest

from rail_waitlist.timetable_management.bundled_station_catalog import (
    BUNDLED_STATION_CATALOG_PACKAGE,
    BUNDLED_STATION_CATALOG_RESOURCE,
    parse_bundled_station_catalog,
)
from rail_waitlist.timetable_management.catalog_application import (
    STATION_CATALOG_SCHEMA_VERSION,
)
from rail_waitlist.timetable_management.station_visibility import KORAIL_STATION_DATA_URL

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_bundled_station_catalog.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("build_bundled_station_catalog", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
build_script = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(build_script)


def station_row(node_id: str, name: str, **extra: object) -> dict[str, object]:
    return {
        "node_id": node_id,
        "name": name,
        "city_code": "11",
        "city_name": "서울특별시",
        **extra,
    }


def cache_export() -> dict[str, object]:
    seoul = station_row("N-SEOUL", "서울")
    return {
        "schema_version": STATION_CATALOG_SCHEMA_VERSION,
        "station_count": 2,
        "retrieved_at": "2026-11-18T21:35:53.64931+09:00",
        "refresh_after": "2026-11-19T21:35:53.64931+09:00",
        "payload": {
            "stations": [seoul, station_row("N-GWANGWOON", "광운대")],
            "display_stations": [seoul],
            "visibility": {
                "source": "korail_station_guide",
                "url": KORAIL_STATION_DATA_URL,
                "retrieved_at": "2026-11-18T12:35:55.072141+00:00",
                "etag": '"fixture"',
                "last_modified": "Tue, 13 Oct 2026 10:07:52 GMT",
            },
        },
    }


def write_export(path: Path, export: object) -> Path:
    path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")
    return path


def test_build_keeps_only_reviewed_fields_and_normalizes_provenance_times(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = cache_export()
    payload = export["payload"]
    assert isinstance(payload, dict)
    payload["stations"][0]["internal_note"] = "dropped"
    payload["visibility"]["debug"] = "dropped"
    input_path = write_export(tmp_path / "cache.json", export)
    output_path = tmp_path / "bundled_station_catalog.json"

    assert build_script.main([str(input_path), "--output", str(output_path)]) == 0

    text = output_path.read_text(encoding="utf-8")
    document = json.loads(text)
    assert document == {
        "format": "railwait.bundled_station_catalog",
        "format_version": 1,
        "station_catalog_schema_version": STATION_CATALOG_SCHEMA_VERSION,
        "source": "TAGO",
        "retrieved_at": "2026-11-18T12:35:53.649310+00:00",
        "visibility": {
            "source": "korail_station_guide",
            "url": KORAIL_STATION_DATA_URL,
            "retrieved_at": "2026-11-18T12:35:55.072141+00:00",
            "etag": '"fixture"',
            "last_modified": "Tue, 13 Oct 2026 10:07:52 GMT",
        },
        "identity_stations": [
            station_row("N-SEOUL", "서울"),
            station_row("N-GWANGWOON", "광운대"),
        ],
        "display_stations": [station_row("N-SEOUL", "서울")],
    }
    assert text == build_script.render_document(document)
    assert parse_bundled_station_catalog(document, schema_version=STATION_CATALOG_SCHEMA_VERSION)
    assert {path.name for path in tmp_path.iterdir()} == {
        "cache.json",
        "bundled_station_catalog.json",
    }
    captured = capsys.readouterr()
    assert "식별 역 2개, 표시 역 1개" in captured.out
    assert captured.err == ""


def mutate_export(change: str) -> dict[str, object]:
    export = cache_export()
    payload = export["payload"]
    assert isinstance(payload, dict)
    visibility = payload["visibility"]
    assert isinstance(visibility, dict)
    if change == "duplicate_identity":
        payload["stations"] = [station_row("N-SEOUL", "서울"), station_row("N-SEOUL", "서울")]
    elif change == "display_outside_identity":
        payload["display_stations"] = [station_row("N-OUTSIDE", "외부역")]
    elif change == "empty_display":
        payload["display_stations"] = []
    elif change == "empty_identity":
        payload["stations"] = []
        export["station_count"] = 0
    elif change == "station_count_mismatch":
        export["station_count"] = 3
    elif change == "schema_version":
        export["schema_version"] = STATION_CATALOG_SCHEMA_VERSION - 1
    elif change == "missing_retrieved_at":
        export.pop("retrieved_at")
    elif change == "naive_retrieved_at":
        export["retrieved_at"] = "2026-11-18T21:35:53"
    elif change == "missing_visibility_time":
        visibility.pop("retrieved_at")
    elif change == "unofficial_visibility_url":
        visibility["url"] = "http://fixture.internal/station_data.json"
    elif change == "missing_visibility_source":
        visibility.pop("source")
    else:
        raise AssertionError(change)
    return export


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_identity",
        "display_outside_identity",
        "empty_display",
        "empty_identity",
        "station_count_mismatch",
        "schema_version",
        "missing_retrieved_at",
        "naive_retrieved_at",
        "missing_visibility_time",
        "unofficial_visibility_url",
        "missing_visibility_source",
    ],
)
def test_invalid_exports_fail_without_replacing_the_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: str,
) -> None:
    input_path = write_export(tmp_path / "cache.json", mutate_export(change))
    output_path = tmp_path / "bundled_station_catalog.json"
    output_path.write_text("previous snapshot\n", encoding="utf-8")

    assert build_script.main([str(input_path), "--output", str(output_path)]) == 2

    assert output_path.read_text(encoding="utf-8") == "previous snapshot\n"
    assert {path.name for path in tmp_path.iterdir()} == {
        "cache.json",
        "bundled_station_catalog.json",
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("내장 역 목록 스냅샷 생성 실패: ")


def test_unreadable_export_reports_a_bounded_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "cache.json"
    input_path.write_text("{not json", encoding="utf-8")

    assert build_script.main([str(input_path), "--output", str(tmp_path / "out.json")]) == 2
    assert not (tmp_path / "out.json").exists()
    assert capsys.readouterr().err.startswith("내장 역 목록 스냅샷 생성 실패: ")


def test_default_output_is_the_packaged_snapshot_in_canonical_form() -> None:
    resource = resources.files(BUNDLED_STATION_CATALOG_PACKAGE).joinpath(
        *BUNDLED_STATION_CATALOG_RESOURCE
    )
    shipped = build_script.DEFAULT_OUTPUT.read_text(encoding="utf-8")

    assert resource.read_text(encoding="utf-8") == shipped
    assert build_script.render_document(json.loads(shipped)) == shipped
