from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_LOGGER = logging.getLogger("rail_waitlist.migration_logging_contract")


def test_alembic_upgrade_keeps_preexisting_application_loggers_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-logging-isolation.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    previous_disabled = APPLICATION_LOGGER.disabled
    APPLICATION_LOGGER.disabled = False

    try:
        command.upgrade(config, "head")

        assert APPLICATION_LOGGER.disabled is False
    finally:
        APPLICATION_LOGGER.disabled = previous_disabled
        get_settings.cache_clear()
