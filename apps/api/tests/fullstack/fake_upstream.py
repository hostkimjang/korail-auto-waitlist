from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _tago_response(items: list[dict[str, str]], *, paginated: bool) -> dict[str, object]:
    body: dict[str, object] = {"items": {"item": items}}
    if paginated:
        body.update({"numOfRows": max(1, len(items)), "pageNo": 1, "totalCount": len(items)})
    return {"response": {"header": {"resultCode": "00"}, "body": body}}


def _station_roster() -> dict[str, object]:
    names = ["서울", "수서", "대전", "부산"]
    names.extend(f"검증역{index:03d}" for index in range(1, 247))
    return {
        "stns": {
            "stn": [
                {"stn_cd": f"E2E{index:03d}", "stn_nm": name}
                for index, name in enumerate(names, start=1)
            ]
        }
    }


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "railwait-e2e-fixture"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._json({"status": "ok"})
            return
        if parsed.path == "/station_data.json":
            self._json(_station_roster())
            return
        if parsed.path == "/tago/GetCtyCodeList":
            self._json(
                _tago_response(
                    [
                        {"citycode": "11", "cityname": "서울특별시"},
                        {"citycode": "26", "cityname": "부산광역시"},
                    ],
                    paginated=False,
                )
            )
            return
        if parsed.path == "/tago/GetCtyAcctoTrainSttnList":
            city_code = parse_qs(parsed.query).get("cityCode", [""])[0]
            stations = {
                "11": [
                    {"nodeid": "N-SEOUL", "nodename": "서울"},
                    {"nodeid": "N-SUSEO", "nodename": "수서"},
                ],
                "26": [{"nodeid": "N-BUSAN", "nodename": "부산"}],
            }.get(city_code, [])
            self._json(_tago_response(stations, paginated=True))
            return
        if parsed.path == "/tago/GetStrtpntAlocFndTrainInfo":
            query = parse_qs(parsed.query)
            service_date = query.get("depPlandTime", [""])[0]
            if len(service_date) != 8 or not service_date.isdigit():
                self._json({"detail": "invalid service date"}, HTTPStatus.BAD_REQUEST)
                return
            origin = "수서" if query.get("depPlaceId", [""])[0] == "N-SUSEO" else "서울"
            self._json(
                _tago_response(
                    [
                        {
                            "trainno": "9001",
                            "traingradename": "KTX",
                            "depplandtime": f"{service_date}130000",
                            "arrplandtime": f"{service_date}153000",
                            "depplacename": origin,
                            "arrplacename": "부산",
                            "adultcharge": "59800",
                        },
                        {
                            "trainno": "9002",
                            "traingradename": "SRT",
                            "depplandtime": f"{service_date}131000",
                            "arrplandtime": f"{service_date}154000",
                            "depplacename": origin,
                            "arrplacename": "부산",
                            "adultcharge": "52900",
                        },
                        {
                            "trainno": "9003",
                            "traingradename": "SRT",
                            "depplandtime": f"{service_date}133000",
                            "arrplandtime": f"{service_date}160000",
                            "depplacename": origin,
                            "arrplacename": "부산",
                            "adultcharge": "52900",
                        },
                    ],
                    paginated=True,
                )
            )
            return
        if parsed.path == "/srt/search":
            query = parse_qs(parsed.query)
            departure = query.get("dep", [""])[0]
            destination = query.get("arr", [""])[0]
            service_date = query.get("date", [""])[0]
            if departure != "수서" or destination != "부산":
                self._json({"trains": []})
                return
            if len(service_date) != 8 or not service_date.isdigit():
                self._json({"detail": "invalid service date"}, HTTPStatus.BAD_REQUEST)
                return
            worker_observation = (
                query.get("time", [""])[0] == "000000"
                and query.get("time_limit", [""])[0] == "235959"
            )
            self._json(
                {
                    "trains": [
                        {
                            "train_number": "9002",
                            "dep_date": service_date,
                            "dep_time": "131000",
                            "arr_date": service_date,
                            "arr_time": "154000",
                            "general_seat_state": ("예약가능" if worker_observation else "매진"),
                            "special_seat_state": "매진",
                            "reserve_wait_possible_code": "",
                        },
                        {
                            "train_number": "9003",
                            "dep_date": service_date,
                            "dep_time": "133000",
                            "arr_date": service_date,
                            "arr_time": "160000",
                            "general_seat_state": "매진",
                            "special_seat_state": "매진",
                            "reserve_wait_possible_code": "9",
                        },
                    ],
                }
            )
            return
        self._json({"detail": "not found"}, HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8001), FixtureHandler).serve_forever()
