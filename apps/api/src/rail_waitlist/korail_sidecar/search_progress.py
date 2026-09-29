"""Expose observed progress of an already running read-only KORAIL lookup."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SearchProgressState = Literal["idle", "searching", "official_queue"]


class OfficialQueueProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Measured by this service, not supplied by KORAIL's queue page.
    elapsed_wait_seconds: int = Field(ge=0, strict=True)


class SearchProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SearchProgressState
    queue: OfficialQueueProgress | None = None

    @model_validator(mode="after")
    def require_official_queue_for_details(self) -> "SearchProgress":
        if self.queue is not None and self.state != "official_queue":
            raise ValueError("queue details require an observed official queue")
        return self


IDLE_SEARCH_PROGRESS = SearchProgress(state="idle")
_progress_callback: ContextVar[Callable[[SearchProgress], None] | None] = ContextVar(
    "korail_search_progress_callback", default=None
)


@contextmanager
def bind_search_progress(callback: Callable[[SearchProgress], None]) -> Iterator[None]:
    token = _progress_callback.set(callback)
    try:
        yield
    finally:
        _progress_callback.reset(token)


def publish_search_progress(state: SearchProgressState) -> None:
    callback = _progress_callback.get()
    if callback is not None:
        callback(SearchProgress(state=state))
