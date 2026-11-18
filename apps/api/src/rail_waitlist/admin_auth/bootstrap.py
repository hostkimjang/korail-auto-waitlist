"""Create the first administrator over stdin after migrations have completed."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, BinaryIO, Literal, TextIO

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .schemas import UsernamePasswordCredentials

MAX_INPUT_BYTES = 4096
ERROR_MESSAGE = "Administrator bootstrap failed. Check input and database readiness.\n"
BootstrapStatus = Literal["created", "already_configured"]
BootstrapResult = Literal["created", "already_configured", "not_configured"]


async def bootstrap_account(
    session: AsyncSession, credentials: UsernamePasswordCredentials
) -> BootstrapStatus:
    from sqlalchemy.exc import IntegrityError

    from .account_application import add_initial_account, count_accounts

    if await count_accounts(session):
        return "already_configured"
    await add_initial_account(session, credentials)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A competing installer or web registration may have committed first. Never reset it.
        if await count_accounts(session):
            return "already_configured"
        raise
    return "created"


async def _run(credentials: UsernamePasswordCredentials | None) -> BootstrapResult:
    from ..database import SessionFactory, engine
    from .account_application import count_accounts

    try:
        async with SessionFactory() as session:
            if credentials is None:
                return "already_configured" if await count_accounts(session) else "not_configured"
            return await bootstrap_account(session, credentials)
    finally:
        await engine.dispose()


def main(
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    try:
        from .schemas import UsernamePasswordCredentials

        source = stdin if stdin is not None else sys.stdin.buffer
        payload = source.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError("input exceeds limit")
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict) and set(data) == {"check_only"} and data["check_only"] is True:
            credentials = None
        elif isinstance(data, dict) and set(data) == {"username", "password"}:
            credentials = UsernamePasswordCredentials.model_validate(data)
        else:
            raise ValueError("invalid input fields")
        status = asyncio.run(_run(credentials))
    except Exception:
        # Validation and database exceptions can contain credentials, SQL parameters or DSNs.
        errors.write(ERROR_MESSAGE)
        return 1
    output.write(json.dumps({"status": status}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
