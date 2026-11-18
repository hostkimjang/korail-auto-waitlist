from __future__ import annotations

import argparse
import json
import sys


async def database_revision() -> str:
    from sqlalchemy import text

    from ..database import SessionFactory, engine

    try:
        async with SessionFactory() as session:
            revisions = list(
                (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
            )
        if len(revisions) != 1 or not isinstance(revisions[0], str) or not revisions[0]:
            raise ValueError("database revision unavailable")
        return revisions[0]
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify maintenance drain inside the container")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true")
    group.add_argument("--database-revision", action="store_true")
    arguments = parser.parse_args()
    status: dict[str, str | bool | int]
    try:
        from .gate import MaintenanceGate

        if arguments.database_revision:
            import asyncio

            status = {"schema": 1, "database_revision": asyncio.run(database_revision())}
        else:
            status = MaintenanceGate.configured().status()
    except Exception:
        print("Maintenance status unavailable.", file=sys.stderr)
        return 1
    print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
