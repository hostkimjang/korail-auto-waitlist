#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
cd "$repo_root"

compose_arguments=(compose -f compose.yml)
running_services=()

usage() {
    cat <<'EOF'
사용법: bash scripts/ops.sh <명령> [복원 파일]

명령:
  config, build, up, down, status, logs, drain-status, migrate
  configure-browser, experimental, monitoring, ntfy, backup, restore
  verify, verify-api, verify-browser, verify-web
EOF
}

compose() {
    docker "${compose_arguments[@]}" "$@"
}

contains_service() {
    local expected="$1"
    local service
    for service in "${running_services[@]}"; do
        if [[ "$service" == "$expected" ]]; then
            return 0
        fi
    done
    return 1
}

load_running_services() {
    local output
    output="$(compose --profile '*' ps --status running --services)"
    running_services=()
    if [[ -n "$output" ]]; then
        mapfile -t running_services <<< "$output"
    fi
}

verify_web() {
    (
        cd apps/web
        npm run verify
    )
}

verify_api() {
    if ! command -v uv >/dev/null 2>&1; then
        echo 'API 검증에는 uv가 필요합니다: https://docs.astral.sh/uv/' >&2
        return 1
    fi

    (
        cd apps/api
        uv lock --check
        uv run --extra test pytest
        uvx --from ruff==0.12.12 ruff check --select E,F,I .
        uv run --extra test python scripts/check_ruff_format_ratchet.py
        uv run --frozen --extra test --extra browser mypy
    )
}

verify_browser_adapter() {
    compose --profile test build korail-browser-adapter-test
    compose --profile test run --rm --no-deps korail-browser-adapter-test
}

verify_linux_operations() {
    bash "${script_dir}/test-ops.sh"
}

configure_browser_adapter() {
    local include_srt="${1:-false}"
    local arguments=()
    if [[ "$include_srt" == true ]]; then
        arguments+=(--include-srt-provider-adapter)
    fi
    bash "${script_dir}/configure-browser-adapter.sh" "${arguments[@]}"
}

show_celery_active_task_summary() {
    local probe_service=''
    local candidate
    load_running_services

    for candidate in worker experimental-rail notification-worker maintenance-worker; do
        if contains_service "$candidate"; then
            probe_service="$candidate"
            break
        fi
    done

    if [[ -z "$probe_service" ]]; then
        echo '실행 중인 Celery worker가 없습니다.'
        return 0
    fi

    compose --profile experimental-rail exec -T "$probe_service" python -c '
import json
import subprocess
import sys

result = subprocess.run(
    [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "inspect",
        "active",
        "--json",
        "--timeout",
        "5",
    ],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(f"Celery active-task inspection failed with exit code {result.returncode}", file=sys.stderr)
    raise SystemExit(result.returncode)

snapshot = json.loads(result.stdout or "{}")
if not snapshot:
    print("응답한 Celery worker가 없습니다.")
    raise SystemExit(0)

for node, raw_tasks in snapshot.items():
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    names = sorted(
        {
            task.get("name")
            for task in tasks
            if isinstance(task, dict) and isinstance(task.get("name"), str)
        }
    )
    summary = ", ".join(names) if names else "없음"
    print(f"{node}: 진행 중 {len(tasks)}건 ({summary})")
'
}

stop_services_for_graceful_recreate() {
    local include_experimental_rail="$1"
    shift
    local profile_arguments=("$@")
    local experimental_services=(experimental-rail korail-browser-adapter srt-provider-adapter)
    local maintenance_services=(backup restore)
    local service
    local stage_services
    local services_to_stop=()

    if [[ "$include_experimental_rail" != true ]]; then
        for service in "${experimental_services[@]}"; do
            if contains_service "$service"; then
                echo '실험 철도 서비스가 실행 중입니다. 전체 revision을 안전하게 맞추려면 experimental 명령을 사용하세요.' >&2
                return 1
            fi
        done
    fi

    for service in "${maintenance_services[@]}"; do
        if contains_service "$service"; then
            echo '백업 또는 복원이 실행 중입니다. 완료를 확인한 뒤 재배포하세요.' >&2
            return 1
        fi
    done

    for stage_services in 'proxy' 'scheduler' 'worker experimental-rail notification-worker maintenance-worker' 'api'; do
        services_to_stop=()
        for service in $stage_services; do
            if contains_service "$service"; then
                services_to_stop+=("$service")
            fi
        done
        if ((${#services_to_stop[@]} > 0)); then
            compose "${profile_arguments[@]}" stop --timeout 300 "${services_to_stop[@]}" || return $?
        fi
    done
}

restore_drained_services() {
    local profile_arguments=("$@")
    local service
    local services_to_restore=()
    local ordered_services=(proxy scheduler worker experimental-rail notification-worker maintenance-worker api)

    for service in "${ordered_services[@]}"; do
        if contains_service "$service"; then
            services_to_restore+=("$service")
        fi
    done
    if ((${#services_to_restore[@]} > 0)); then
        echo '재배포가 완료되지 않아 중지했던 서비스의 실행 상태를 복구합니다.' >&2
        if ! compose "${profile_arguments[@]}" start "${services_to_restore[@]}"; then
            echo '중지했던 서비스의 실행 상태 복구도 실패했습니다. 중간 종료 상태를 확인하세요.' >&2
        fi
    fi
}

show_experimental_service_states() {
    local services=(
        proxy web api scheduler worker notification-worker maintenance-worker postgres redis
        experimental-rail korail-browser-adapter srt-provider-adapter
    )

    echo '실험 프로필 서비스가 제한 시간 안에 준비되지 않았습니다. 현재 상태:' >&2
    if ! compose --profile experimental-rail ps --all \
        --format 'table {{.Service}}\t{{.State}}\t{{.Health}}' \
        "${services[@]}" >&2; then
        echo '서비스 상태를 조회하지 못했습니다.' >&2
    fi
}

graceful_recreate() {
    local include_experimental_rail="$1"
    shift
    local profile_arguments=("$@")
    local up_arguments=(up --detach --force-recreate)
    local stop_status

    if [[ "$include_experimental_rail" == true ]]; then
        up_arguments+=(--wait --wait-timeout 180)
    fi

    show_celery_active_task_summary
    load_running_services
    if stop_services_for_graceful_recreate "$include_experimental_rail" "${profile_arguments[@]}"; then
        if compose "${profile_arguments[@]}" "${up_arguments[@]}"; then
            return 0
        else
            stop_status=$?
            if [[ "$include_experimental_rail" == true ]]; then
                show_experimental_service_states
            fi
        fi
    else
        stop_status=$?
    fi

    restore_drained_services "${profile_arguments[@]}"
    return "$stop_status"
}

safe_down() {
    local service
    load_running_services
    for service in backup restore; do
        if contains_service "$service"; then
            echo '백업 또는 복원이 실행 중입니다. 완료를 확인한 뒤 서비스를 내리세요.' >&2
            return 1
        fi
    done
    compose --profile '*' down
}

preflight_backup_file() {
    local backup_file="$1"
    compose --profile restore run --rm --no-deps \
        -e "BACKUP_FILE=${backup_file}" \
        --entrypoint /bin/sh \
        restore -eu -c '
identity_file="$(mktemp /tmp/rail-age-identity-preflight.XXXXXX)"
trap '\''rm -f "$identity_file"'\'' EXIT INT TERM
printf "%s\n" "$BACKUP_AGE_IDENTITY" > "$identity_file"
age --decrypt --identity "$identity_file" "$BACKUP_FILE" > /dev/null
'
}

restore_backup() {
    local backup_file="$1"
    local maintenance_services=(proxy api worker notification-worker maintenance-worker scheduler experimental-rail)
    local services_to_restore=()
    local service
    local stop_status

    if [[ ! "$backup_file" =~ ^/backups/[^/]+\.dump\.age$ ]]; then
        echo '복원할 /backups/<파일>.dump.age 경로를 지정하세요.' >&2
        return 2
    fi

    preflight_backup_file "$backup_file"
    load_running_services
    for service in backup restore; do
        if contains_service "$service"; then
            echo '백업 또는 다른 복원이 실행 중입니다. 완료를 확인한 뒤 복원하세요.' >&2
            return 1
        fi
    done
    for service in "${maintenance_services[@]}"; do
        if contains_service "$service"; then
            services_to_restore+=("$service")
        fi
    done

    echo '복원 중 외부 요청과 background write를 막기 위해 실행 중인 API/worker 계열을 정지합니다.'
    if stop_services_for_graceful_recreate true --profile experimental-rail; then
        :
    else
        stop_status=$?
        restore_drained_services --profile experimental-rail
        return "$stop_status"
    fi

    if compose --profile restore run --rm \
        -e RESTORE_CONFIRM=RESTORE \
        -e "BACKUP_FILE=${backup_file}" \
        restore && compose run --rm migration; then
        if ((${#services_to_restore[@]} > 0)); then
            if ! compose --profile experimental-rail start "${services_to_restore[@]}"; then
                echo '복원과 migration은 성공했지만 기존 서비스 시작에 실패했습니다. maintenance 상태를 확인하세요.' >&2
                return 1
            fi
        fi
        return 0
    fi

    echo '복원 또는 migration 검증이 실패해 서비스는 maintenance 상태로 유지됩니다. 원인을 해결한 뒤 수동으로 시작하세요.' >&2
    return 1
}

command_name="${1:-status}"
if (($# > 0)); then
    shift
fi

case "$command_name" in
    config)
        compose config --quiet
        ;;
    verify)
        compose config --quiet
        verify_linux_operations
        verify_browser_adapter
        verify_api
        verify_web
        ;;
    verify-api)
        verify_api
        ;;
    verify-browser)
        verify_browser_adapter
        ;;
    verify-web)
        verify_web
        ;;
    build)
        compose build
        ;;
    up)
        compose config --quiet
        compose build
        graceful_recreate false
        ;;
    down)
        safe_down
        ;;
    status)
        compose ps
        ;;
    logs)
        compose logs --follow --tail=200
        ;;
    drain-status)
        show_celery_active_task_summary
        ;;
    migrate)
        compose run --rm migration
        ;;
    configure-browser)
        configure_browser_adapter false
        ;;
    experimental)
        configure_browser_adapter true
        compose --profile experimental-rail config --quiet
        compose --profile experimental-rail build
        graceful_recreate true --profile experimental-rail
        ;;
    monitoring)
        compose --profile monitoring up --detach prometheus grafana
        ;;
    ntfy)
        compose --profile ntfy up --detach ntfy
        ;;
    backup)
        compose --profile backup run --rm backup once
        ;;
    restore)
        restore_backup "${1:-}"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo "알 수 없는 명령입니다: ${command_name}" >&2
        usage >&2
        exit 2
        ;;
esac
