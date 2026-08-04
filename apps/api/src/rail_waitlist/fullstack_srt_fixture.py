from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class FixtureSrtTrain:
    train_number: str
    dep_date: str
    dep_time: str
    general_seat_state: str
    special_seat_state: str
    reserve_wait_possible_code: str


class FullstackSrtFixtureClient:
    """Deterministic SRTrain-shaped client available only at the fixed test origin."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def search_train(
        self,
        dep: str,
        arr: str,
        date: str | None = None,
        time: str | None = None,
        time_limit: str | None = None,
        available_only: bool = True,
    ) -> list[FixtureSrtTrain]:
        query = urlencode(
            {
                "dep": dep,
                "arr": arr,
                "date": date or "",
                "time": time or "",
                "time_limit": time_limit or "",
                "available_only": "1" if available_only else "0",
            }
        )
        request = Request(
            f"{self._endpoint}?{query}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=3) as response:  # noqa: S310 - fixed test URL
                payload = json.loads(response.read())
        except (OSError, ValueError, json.JSONDecodeError):
            raise RuntimeError("SRT full-stack fixture request failed") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("trains"), list):
            raise RuntimeError("SRT full-stack fixture response is invalid")
        trains: list[FixtureSrtTrain] = []
        for raw_train in payload["trains"]:
            if not isinstance(raw_train, dict):
                raise RuntimeError("SRT full-stack fixture train is invalid")
            try:
                trains.append(
                    FixtureSrtTrain(
                        train_number=str(raw_train["train_number"]),
                        dep_date=str(raw_train["dep_date"]),
                        dep_time=str(raw_train["dep_time"]),
                        general_seat_state=str(raw_train["general_seat_state"]),
                        special_seat_state=str(raw_train["special_seat_state"]),
                        reserve_wait_possible_code=str(
                            raw_train["reserve_wait_possible_code"]
                        ),
                    )
                )
            except KeyError:
                raise RuntimeError("SRT full-stack fixture train is incomplete") from None
        return trains


def fullstack_srt_client_factory(endpoint: str):
    return lambda: FullstackSrtFixtureClient(endpoint)
