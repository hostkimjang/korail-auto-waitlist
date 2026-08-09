#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
temporary_dir="$(mktemp -d)"
trace_file="${temporary_dir}/docker.trace"
stdout_file="${temporary_dir}/stdout"
stderr_file="${temporary_dir}/stderr"

cleanup() {
    rm -rf -- "$temporary_dir"
}

trap cleanup EXIT
cd "$repo_root"

export OPS_SMOKE_CASE='normal'
export OPS_SMOKE_TRACE="$trace_file"

docker() {
    local rendered="$*"
    printf '%s\n' "$rendered" >> "$OPS_SMOKE_TRACE"

    if [[ "$rendered" == *'ps --status running --services'* ]]; then
        printf '%s\n' proxy scheduler worker notification-worker api
        if [[ "$OPS_SMOKE_CASE" == experimental* || "$OPS_SMOKE_CASE" == restore-* ]]; then
            printf '%s\n' experimental-rail korail-browser-adapter srt-provider-adapter
        fi
        if [[ "$OPS_SMOKE_CASE" == *-maintenance ]]; then
            printf '%s\n' backup
        fi
        return 0
    fi

    if [[ "$OPS_SMOKE_CASE" == *stop-failure && "$rendered" == *'stop --timeout 300 scheduler'* ]]; then
        return 17
    fi
    if [[ "$OPS_SMOKE_CASE" == recreate-failure && "$rendered" == *'up --detach --force-recreate'* ]]; then
        return 23
    fi
    if [[ "$OPS_SMOKE_CASE" == experimental-unhealthy && "$rendered" == *'up --detach --force-recreate --wait --wait-timeout 180'* ]]; then
        return 41
    fi
    if [[ "$OPS_SMOKE_CASE" == experimental-unhealthy && "$rendered" == *'ps --all --format'* ]]; then
        printf '%s\n' \
            'SERVICE                     STATE      HEALTH' \
            'proxy                       running    healthy' \
            'web                         running    healthy' \
            'api                         running    healthy' \
            'scheduler                   running    healthy' \
            'worker                      running    healthy' \
            'notification-worker         running    healthy' \
            'postgres                    running    healthy' \
            'redis                       running    healthy' \
            'experimental-rail           running    healthy' \
            'korail-browser-adapter      running    unhealthy' \
            'srt-provider-adapter        running    healthy'
        return 0
    fi
    if [[ "$OPS_SMOKE_CASE" == restore-migration-failure && "$rendered" == *'run --rm migration'* ]]; then
        return 31
    fi
    return 0
}

bash() {
    if [[ "${1:-}" == */configure-browser-adapter.sh ]]; then
        printf 'configure-browser %s\n' "$*" >> "$OPS_SMOKE_TRACE"
        return 0
    fi
    /bin/bash "$@"
}

export -f docker bash

fail() {
    echo "Linux 운영 스크립트 계약 검증 실패: $*" >&2
    exit 1
}

run_ops() {
    local expected_status="$1"
    shift
    local actual_status
    : > "$trace_file"
    : > "$stdout_file"
    : > "$stderr_file"

    set +e
    /bin/bash scripts/ops.sh "$@" > "$stdout_file" 2> "$stderr_file"
    actual_status=$?
    set -e

    if [[ "$actual_status" -ne "$expected_status" ]]; then
        cat "$stdout_file" >&2
        cat "$stderr_file" >&2
        fail "'$*' 종료 코드가 ${expected_status}가 아니라 ${actual_status}입니다."
    fi
}

assert_trace_contains() {
    local pattern="$1"
    grep -Fq -- "$pattern" "$trace_file" || fail "호출 기록에 '$pattern'이 없습니다."
}

assert_trace_excludes() {
    local pattern="$1"
    if grep -Fq -- "$pattern" "$trace_file"; then
        fail "호출하면 안 되는 '$pattern'이 기록됐습니다."
    fi
}

assert_trace_order() {
    local previous_line=0
    local pattern
    local line
    for pattern in "$@"; do
        line="$(grep -Fnm1 -- "$pattern" "$trace_file" | cut -d: -f1)"
        if [[ -z "$line" ]]; then
            fail "호출 순서를 확인할 '$pattern'이 없습니다."
        fi
        if ((line <= previous_line)); then
            fail "'$pattern' 호출 순서가 올바르지 않습니다."
        fi
        previous_line="$line"
    done
}

OPS_SMOKE_CASE='normal'
run_ops 0 up
assert_trace_order \
    'compose -f compose.yml build' \
    'stop --timeout 300 proxy' \
    'stop --timeout 300 scheduler' \
    'stop --timeout 300 worker notification-worker' \
    'stop --timeout 300 api' \
    'up --detach --force-recreate'
assert_trace_excludes '--wait'

OPS_SMOKE_CASE='stop-failure'
run_ops 17 up
assert_trace_excludes 'up --detach --force-recreate'
assert_trace_contains 'start proxy scheduler worker notification-worker api'

OPS_SMOKE_CASE='recreate-failure'
run_ops 23 up
assert_trace_contains 'up --detach --force-recreate'
assert_trace_contains 'start proxy scheduler worker notification-worker api'

OPS_SMOKE_CASE='experimental'
run_ops 0 experimental
assert_trace_contains 'configure-browser'
assert_trace_order \
    '--profile experimental-rail config --quiet' \
    '--profile experimental-rail build' \
    '--profile experimental-rail stop --timeout 300 proxy' \
    '--profile experimental-rail stop --timeout 300 scheduler' \
    '--profile experimental-rail stop --timeout 300 worker experimental-rail notification-worker' \
    '--profile experimental-rail stop --timeout 300 api' \
    '--profile experimental-rail up --detach --force-recreate --wait --wait-timeout 180'

OPS_SMOKE_CASE='experimental-unhealthy'
run_ops 41 experimental
assert_trace_order \
    '--profile experimental-rail up --detach --force-recreate --wait --wait-timeout 180' \
    '--profile experimental-rail ps --all --format' \
    '--profile experimental-rail start proxy scheduler worker experimental-rail notification-worker api'
assert_trace_contains 'proxy web api scheduler worker notification-worker postgres redis experimental-rail korail-browser-adapter srt-provider-adapter'
grep -Fq '실험 프로필 서비스가 제한 시간 안에 준비되지 않았습니다. 현재 상태:' "$stderr_file" || \
    fail '실험 프로필 준비 실패 설명이 stderr에 없습니다.'
grep -Fq 'korail-browser-adapter      running    unhealthy' "$stderr_file" || \
    fail '준비 실패한 adapter 상태가 stderr에 없습니다.'

OPS_SMOKE_CASE='restore-success'
run_ops 0 restore /backups/test.dump.age
assert_trace_order \
    '--profile restore run --rm --no-deps -e BACKUP_FILE=/backups/test.dump.age --entrypoint /bin/sh restore' \
    '--profile experimental-rail stop --timeout 300 proxy' \
    '--profile experimental-rail stop --timeout 300 scheduler' \
    '--profile experimental-rail stop --timeout 300 worker experimental-rail notification-worker' \
    '--profile experimental-rail stop --timeout 300 api' \
    '--profile restore run --rm -e RESTORE_CONFIRM=RESTORE -e BACKUP_FILE=/backups/test.dump.age restore' \
    'run --rm migration' \
    '--profile experimental-rail start proxy api worker notification-worker scheduler experimental-rail'

OPS_SMOKE_CASE='restore-migration-failure'
run_ops 1 restore /backups/test.dump.age
assert_trace_contains 'run --rm migration'
assert_trace_excludes '--profile experimental-rail start proxy api worker notification-worker scheduler experimental-rail'

OPS_SMOKE_CASE='restore-stop-failure'
run_ops 17 restore /backups/test.dump.age
assert_trace_excludes '-e RESTORE_CONFIRM=RESTORE'
assert_trace_contains '--profile experimental-rail start proxy scheduler worker experimental-rail notification-worker api'

OPS_SMOKE_CASE='restore-maintenance'
run_ops 1 restore /backups/test.dump.age
assert_trace_excludes '-e RESTORE_CONFIRM=RESTORE'

OPS_SMOKE_CASE='normal'
run_ops 2 restore ../test.dump.age
if [[ -s "$trace_file" ]]; then
    fail '잘못된 복원 경로가 Docker 호출 전에 거절되지 않았습니다.'
fi

OPS_SMOKE_CASE='normal'
run_ops 0 down
assert_trace_contains "--profile * down"

OPS_SMOKE_CASE='down-maintenance'
run_ops 1 down
assert_trace_excludes "--profile * down"

unset -f docker bash
export -n OPS_SMOKE_CASE OPS_SMOKE_TRACE

test_env_file="${temporary_dir}/test.env"
cp .env.example "$test_env_file"
/bin/bash scripts/configure-browser-adapter.sh \
    --env-file "$test_env_file" \
    --include-srt-provider-adapter > /dev/null

grep -q '^EXPERIMENTAL_RAIL_ENABLED=true$' "$test_env_file"
grep -q '^KORAIL_BROWSER_ADAPTER_ENABLED=true$' "$test_env_file"
grep -q '^SRT_PROVIDER_ADAPTER_ENABLED=true$' "$test_env_file"

korail_token="$(sed -n 's/^KORAIL_BROWSER_ADAPTER_TOKEN=//p' "$test_env_file")"
srt_token="$(sed -n 's/^SRT_PROVIDER_ADAPTER_TOKEN=//p' "$test_env_file")"
if ((${#korail_token} < 32 || ${#srt_token} < 32)); then
    fail 'adapter token이 32자보다 짧습니다.'
fi
if [[ "$(stat -c '%a' "$test_env_file")" != '600' ]]; then
    fail '생성된 .env 권한이 0600이 아닙니다.'
fi

echo 'Linux 운영 스크립트 계약 검증을 통과했습니다.'
