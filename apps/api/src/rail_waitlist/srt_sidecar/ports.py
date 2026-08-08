from __future__ import annotations

from typing import Protocol


class EnvironmentReader(Protocol):
    def __call__(self, name: str, default: str | None = None) -> str | None: ...


class RedisResource(Protocol):
    async def aclose(self) -> None: ...
