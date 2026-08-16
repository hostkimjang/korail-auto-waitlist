COMPOSE := docker compose -f compose.yml

.PHONY: config build up down status logs drain-status migrate configure-browser experimental monitoring ntfy backup restore verify verify-ops verify-api verify-browser verify-web-core verify-web

verify: config verify-ops verify-browser verify-api verify-web

verify-ops:
	bash scripts/test-ops.sh
	bash scripts/test-backup-restore.sh

verify-api:
	cd apps/api && uv lock --check && uv run --python 3.12 --frozen --extra test --extra browser pytest && uvx --from ruff==0.12.12 ruff check --select E,F,I . && uv run --python 3.12 --frozen --extra test python scripts/check_ruff_format_ratchet.py && uv run --python 3.12 --frozen --extra test --extra browser mypy

verify-browser:
	$(COMPOSE) --profile test build korail-browser-adapter-test
	$(COMPOSE) --profile test run --rm --no-deps korail-browser-adapter-test

verify-web-core:
	cd apps/web && npm run verify:core

verify-web:
	cd apps/web && npm run verify

config:
	$(COMPOSE) config --quiet

build:
	bash scripts/ops.sh build

up:
	bash scripts/ops.sh up

down:
	bash scripts/ops.sh down

status:
	bash scripts/ops.sh status

logs:
	bash scripts/ops.sh logs

drain-status:
	bash scripts/ops.sh drain-status

migrate:
	bash scripts/ops.sh migrate

configure-browser:
	bash scripts/ops.sh configure-browser

experimental:
	bash scripts/ops.sh experimental

monitoring:
	bash scripts/ops.sh monitoring

ntfy:
	bash scripts/ops.sh ntfy

backup:
	bash scripts/ops.sh backup

restore:
	@test -n "$(FILE)" || (echo "FILE=/backups/<name>.dump.age 를 지정하세요" && exit 1)
	bash scripts/ops.sh restore "$(FILE)"
