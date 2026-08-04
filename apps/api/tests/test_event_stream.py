from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rail_waitlist.event_stream import http as event_http
from rail_waitlist.models import OutboxEvent


class DisconnectAfterPolls:
    def __init__(self, polls: int = 1) -> None:
        self.polls = polls
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.polls


class ScalarRows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, *, previous=None, rows=(), error: Exception | None = None) -> None:
        self.previous = previous
        self.rows = rows
        self.error = error
        self.entered = False
        self.exited = False
        self.exit_error_type = None
        self.queries = []

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, error_type, _error, _traceback) -> None:
        self.exited = True
        self.exit_error_type = error_type

    async def get(self, _model, _identity):
        return self.previous

    async def scalars(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return ScalarRows(self.rows)


class FakeSessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self.sessions = list(sessions)
        self.created: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = self.sessions.pop(0)
        self.created.append(session)
        return session


async def no_sleep(_seconds: float) -> None:
    return None


async def collect_stream(stream) -> list[str]:
    return [item async for item in stream]


async def test_event_stream_uses_fresh_history_and_poll_sessions(monkeypatch) -> None:
    cursor_time = datetime.now(timezone.utc)
    history_session = FakeSession(previous=SimpleNamespace(created_at=cursor_time))
    poll_session = FakeSession()
    factory = FakeSessionFactory(history_session, poll_session)
    monkeypatch.setattr(event_http, "SessionFactory", factory)
    monkeypatch.setattr(event_http.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        event_http,
        "get_settings",
        lambda: SimpleNamespace(sse_poll_seconds=0),
    )

    items = await collect_stream(
        event_http._stream_events(DisconnectAfterPolls(), "previous-event")
    )

    assert items == [": keepalive\n\n"]
    assert factory.created == [history_session, poll_session]
    assert all(session.entered and session.exited for session in factory.created)
    query_text = str(poll_session.queries[0])
    assert "outbox_events.created_at >" in query_text
    assert "outbox_events.id >" in query_text


async def test_event_stream_emits_wire_events_and_closes_poll_session(monkeypatch) -> None:
    event = OutboxEvent(
        id="event-1",
        aggregate_type="watch",
        aggregate_id="watch-1",
        event_type="watch.updated",
        payload={"watch_id": "watch-1"},
        created_at=datetime.now(timezone.utc),
    )
    poll_session = FakeSession(rows=[event])
    monkeypatch.setattr(event_http, "SessionFactory", FakeSessionFactory(poll_session))
    monkeypatch.setattr(event_http.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        event_http,
        "get_settings",
        lambda: SimpleNamespace(sse_poll_seconds=0),
    )

    items = await collect_stream(event_http._stream_events(DisconnectAfterPolls(), None))

    assert items == [event_http.event_wire(event)]
    assert items[0].startswith("id: event-1\nevent: watch.updated\ndata: ")
    assert items[0].endswith("\n\n")
    assert poll_session.exited is True


async def test_event_stream_closes_fresh_session_when_polling_fails(monkeypatch) -> None:
    poll_session = FakeSession(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(event_http, "SessionFactory", FakeSessionFactory(poll_session))

    with pytest.raises(RuntimeError, match="database unavailable"):
        await anext(event_http._stream_events(DisconnectAfterPolls(), None))

    assert poll_session.entered is True
    assert poll_session.exited is True
    assert poll_session.exit_error_type is RuntimeError


async def test_event_stream_opens_and_closes_a_fresh_session_for_every_poll(monkeypatch) -> None:
    poll_sessions = [FakeSession(), FakeSession(), FakeSession()]
    factory = FakeSessionFactory(*poll_sessions)
    monkeypatch.setattr(event_http, "SessionFactory", factory)
    monkeypatch.setattr(event_http.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        event_http,
        "get_settings",
        lambda: SimpleNamespace(sse_poll_seconds=0),
    )

    items = await collect_stream(event_http._stream_events(DisconnectAfterPolls(3), None))

    assert items == [": keepalive\n\n"] * 3
    assert factory.created == poll_sessions
    assert all(session.entered and session.exited for session in poll_sessions)
    assert all(len(session.queries) == 1 for session in poll_sessions)


async def test_events_response_preserves_stream_headers_and_last_event_id(monkeypatch) -> None:
    captured = {}

    async def body():
        yield ": keepalive\n\n"

    def fake_stream(request, last_event_id):
        captured["request"] = request
        captured["last_event_id"] = last_event_id
        return body()

    request = SimpleNamespace()
    monkeypatch.setattr(event_http, "_stream_events", fake_stream)

    response = await event_http.events(request, "event-previous")
    items = [item async for item in response.body_iterator]

    assert captured == {"request": request, "last_event_id": "event-previous"}
    assert items == [": keepalive\n\n"]
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    route = next(route for route in event_http.router.routes if route.path == "/api/v1/events")
    last_event_header = next(
        field for field in route.dependant.header_params if field.name == "last_event_id"
    )
    assert last_event_header.alias == "Last-Event-ID"
