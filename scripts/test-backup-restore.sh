#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
test_id="$$-${RANDOM}"
network_name="rail-backup-restore-test-${test_id}"
volume_name="rail-backup-restore-test-${test_id}"
postgres_name="rail-backup-restore-postgres-${test_id}"
backup_image="rail-waitlist-backup:local"
database_name="restore_contract"
database_user="restore_contract"
database_password="restore-contract-password"

cleanup() {
    docker rm --force "$postgres_name" >/dev/null 2>&1 || true
    docker volume rm "$volume_name" >/dev/null 2>&1 || true
    docker network rm "$network_name" >/dev/null 2>&1 || true
}

trap cleanup EXIT
cd "$repo_root"

fail() {
    echo "암호화 백업·복원 계약 검증 실패: $*" >&2
    exit 1
}

docker build --file infra/backup/Dockerfile --tag "$backup_image" .
docker network create "$network_name" >/dev/null
docker volume create "$volume_name" >/dev/null
docker run --detach --name "$postgres_name" --network "$network_name" \
    --env "POSTGRES_DB=${database_name}" \
    --env "POSTGRES_USER=${database_user}" \
    --env "POSTGRES_PASSWORD=${database_password}" \
    postgres:16-alpine >/dev/null

postgres_ready=false
for _ in $(seq 1 30); do
    if docker exec "$postgres_name" pg_isready \
        --username "$database_user" --dbname "$database_name" >/dev/null 2>&1; then
        postgres_ready=true
        break
    fi
    sleep 1
done
if [[ "$postgres_ready" != true ]]; then
    fail '일회용 PostgreSQL이 30초 안에 준비되지 않았습니다.'
fi

docker exec --env "PGPASSWORD=${database_password}" "$postgres_name" \
    psql --username "$database_user" --dbname "$database_name" \
    --set ON_ERROR_STOP=1 \
    --command "CREATE TABLE restore_contract (value text NOT NULL); INSERT INTO restore_contract VALUES ('before-backup');" \
    >/dev/null

key_output="$(
    docker run --rm --read-only --tmpfs /tmp:size=16m,mode=1777 \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --entrypoint /bin/sh "$backup_image" -eu -c 'age-keygen 2>&1'
)"
identity="$(printf '%s\n' "$key_output" | sed -n '/^AGE-SECRET-KEY-/p')"
recipient="$(printf '%s\n' "$key_output" | sed -n 's/^Public key: //p')"
[[ -n "$identity" ]] || fail '일회용 age identity를 생성하지 못했습니다.'
[[ -n "$recipient" ]] || fail '일회용 age recipient를 생성하지 못했습니다.'
unset key_output

backup_file="$(
    docker run --rm --read-only --tmpfs /tmp:size=128m,mode=1777 \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --network "$network_name" \
        --mount "type=volume,src=${volume_name},dst=/backups" \
        --env "PGHOST=${postgres_name}" \
        --env 'PGPORT=5432' \
        --env "PGDATABASE=${database_name}" \
        --env "PGUSER=${database_user}" \
        --env "PGPASSWORD=${database_password}" \
        --env "BACKUP_AGE_RECIPIENT=${recipient}" \
        "$backup_image" once
)"
[[ "$backup_file" == /backups/*.dump.age ]] || fail '암호화 백업 파일 경로가 잘못됐습니다.'

docker exec --env "PGPASSWORD=${database_password}" "$postgres_name" \
    psql --username "$database_user" --dbname "$database_name" \
    --set ON_ERROR_STOP=1 \
    --command "UPDATE restore_contract SET value = 'after-backup';" >/dev/null

restore_output="$(
    docker run --rm --read-only --tmpfs /tmp:size=256m,mode=1777 \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --network "$network_name" \
        --mount "type=volume,src=${volume_name},dst=/backups,readonly" \
        --env "PGHOST=${postgres_name}" \
        --env 'PGPORT=5432' \
        --env "PGDATABASE=${database_name}" \
        --env "PGUSER=${database_user}" \
        --env "PGPASSWORD=${database_password}" \
        --env "BACKUP_AGE_IDENTITY=${identity}" \
        --env 'RESTORE_CONFIRM=RESTORE' \
        --env "BACKUP_FILE=${backup_file}" \
        --entrypoint /scripts/restore-postgres.sh \
        "$backup_image"
)"
printf '%s\n' "$restore_output" | grep -Fq "restore completed: ${backup_file}" || \
    fail '복원 스크립트가 완료 근거를 남기지 않았습니다.'

restored_value="$(
    docker exec --env "PGPASSWORD=${database_password}" "$postgres_name" \
        psql --username "$database_user" --dbname "$database_name" \
        --tuples-only --no-align --set ON_ERROR_STOP=1 \
        --command 'SELECT value FROM restore_contract;'
)"
[[ "$restored_value" == 'before-backup' ]] || \
    fail '복원된 데이터가 백업 시점과 일치하지 않습니다.'

unset identity recipient
echo 'Alpine 암호화 백업·복원 round-trip 계약 검증을 통과했습니다.'
