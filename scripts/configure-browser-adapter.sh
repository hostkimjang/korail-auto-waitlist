#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
env_file="${repo_root}/.env"
include_srt_provider_adapter=false
temporary_paths=()

cleanup_temporary_paths() {
    local path
    for path in "${temporary_paths[@]}"; do
        rm -f -- "$path"
    done
}

trap cleanup_temporary_paths EXIT

usage() {
    cat <<'EOF'
사용법: bash scripts/configure-browser-adapter.sh [--env-file PATH] [--include-srt-provider-adapter]
EOF
}

while (($# > 0)); do
    case "$1" in
        --env-file)
            if (($# < 2)); then
                echo '--env-file 뒤에 경로를 지정하세요.' >&2
                exit 2
            fi
            env_file="$2"
            shift 2
            ;;
        --include-srt-provider-adapter)
            include_srt_provider_adapter=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "알 수 없는 옵션입니다: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$env_file" ]]; then
    echo '.env가 없습니다. 먼저 .env.example을 복사하고 필수 값을 설정하세요.' >&2
    exit 1
fi

get_dotenv_value() {
    local name="$1"
    awk -v name="$name" '
        $0 ~ "^[[:space:]]*" name "[[:space:]]*=" {
            value = $0
            sub("^[[:space:]]*" name "[[:space:]]*=[[:space:]]*", "", value)
            sub("\\r$", "", value)
            if (value ~ /^".*"$/ || value ~ /^\047.*\047$/) {
                value = substr(value, 2, length(value) - 2)
            }
            print value
            exit
        }
    ' "$env_file"
}

write_dotenv_updates() {
    local updates_path
    local output_path
    updates_path="$(mktemp "${env_file}.updates.XXXXXX")"
    output_path="$(mktemp "${env_file}.tmp.XXXXXX")"
    temporary_paths+=("$updates_path" "$output_path")
    chmod 0600 "$updates_path" "$output_path"

    {
        printf 'EXPERIMENTAL_RAIL_ENABLED=true\n'
        printf 'KORAIL_BROWSER_ADAPTER_ENABLED=true\n'
        printf 'KORAIL_SEAT_MONITORING_ENABLED=true\n'
        printf 'KORAIL_BROWSER_ADAPTER_TOKEN=%s\n' "$korail_token"
        if [[ "$include_srt_provider_adapter" == true ]]; then
            printf 'SRT_PROVIDER_ADAPTER_ENABLED=true\n'
            printf 'SRT_SEAT_STATUS_ENABLED=true\n'
            printf 'SRT_SEAT_MONITORING_ENABLED=true\n'
            printf 'SRT_PROVIDER_ADAPTER_TOKEN=%s\n' "$srt_token"
        fi
    } > "$updates_path"

    awk -v updates_path="$updates_path" '
        BEGIN {
            while ((getline update < updates_path) > 0) {
                separator = index(update, "=")
                key = substr(update, 1, separator - 1)
                replacements[key] = substr(update, separator + 1)
                order[++update_count] = key
            }
            close(updates_path)
        }
        {
            sub("\\r$", "")
            candidate = $0
            sub("^[[:space:]]*", "", candidate)
            separator = index(candidate, "=")
            if (separator > 0) {
                key = substr(candidate, 1, separator - 1)
                sub("[[:space:]]*$", "", key)
                if (key in replacements) {
                    print key "=" replacements[key]
                    seen[key] = 1
                    next
                }
            }
            print
        }
        END {
            for (index_value = 1; index_value <= update_count; index_value += 1) {
                key = order[index_value]
                if (!(key in seen)) {
                    print key "=" replacements[key]
                }
            }
        }
    ' "$env_file" > "$output_path"

    mv -- "$output_path" "$env_file"
    rm -f -- "$updates_path"
}

new_internal_adapter_token() {
    LC_ALL=C head -c 48 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'
}

korail_token="$(get_dotenv_value 'KORAIL_BROWSER_ADAPTER_TOKEN')"
if ((${#korail_token} < 32)); then
    korail_token="$(new_internal_adapter_token)"
fi

configured_providers='KORAIL'
if [[ "$include_srt_provider_adapter" == true ]]; then
    srt_token="$(get_dotenv_value 'SRT_PROVIDER_ADAPTER_TOKEN')"
    if ((${#srt_token} < 32)); then
        srt_token="$(new_internal_adapter_token)"
    fi

    configured_providers='KORAIL·SRT'
fi

write_dotenv_updates

echo "서버 관리형 ${configured_providers} 어댑터 설정을 .env에 적용했습니다. 비밀값은 출력하지 않았습니다."
