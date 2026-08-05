COMPOSE := docker compose -f compose.yml

.PHONY: config build up down status logs migrate experimental monitoring ntfy backup restore verify verify-api verify-browser verify-web

verify: config verify-browser verify-api verify-web

verify-api:
	cd apps/api && uv lock --check && uv run --extra test pytest && uvx --from ruff==0.12.12 ruff check --select E,F,I . && uv run --extra test python scripts/check_ruff_format_ratchet.py && uv run --frozen --extra test mypy

verify-browser:
	$(COMPOSE) --profile test build korail-browser-adapter-test
	$(COMPOSE) --profile test run --rm --no-deps korail-browser-adapter-test

verify-web:
	cd apps/web && npm run verify

config:
	$(COMPOSE) config --quiet

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

migrate:
	$(COMPOSE) run --rm migration

experimental:
	$(COMPOSE) --profile experimental-rail up -d --build api worker korail-browser-adapter experimental-rail

monitoring:
	$(COMPOSE) --profile monitoring up -d prometheus grafana

ntfy:
	$(COMPOSE) --profile ntfy up -d ntfy

backup:
	$(COMPOSE) --profile backup run --rm backup once

restore:
	@test -n "$(FILE)" || (echo "FILE=/backups/<name>.dump.age 를 지정하세요" && exit 1)
	$(COMPOSE) --profile restore run --rm -e RESTORE_CONFIRM=RESTORE -e BACKUP_FILE=$(FILE) restore
