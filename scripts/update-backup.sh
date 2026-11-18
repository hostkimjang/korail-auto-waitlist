#!/bin/sh
# Called only after the host manager has closed admission and verified drain.
set -eu
set -o pipefail
umask 077
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required}"
: "${BACKUP_AGE_IDENTITY_BASE64:?BACKUP_AGE_IDENTITY_BASE64 is required}"
: "${RAILWAIT_UPDATE_JOB_ID:?RAILWAIT_UPDATE_JOB_ID is required}"
case "$RAILWAIT_UPDATE_JOB_ID" in *[!a-f0-9]*|'') exit 2 ;; esac
[ "${#RAILWAIT_UPDATE_JOB_ID}" -eq 32 ] || exit 2
export PGPASSWORD
backup_dir="${BACKUP_DIR:-/backups}"
work="$(mktemp -d /tmp/railwait-update-backup.XXXXXX)"
dump="$backup_dir/update-$RAILWAIT_UPDATE_JOB_ID.dump.age"
settings="$backup_dir/settings-$RAILWAIT_UPDATE_JOB_ID.env.age"
cleanup() {
  rm -f "$work/identity" "$work/settings" "$work/settings.dec"
  rmdir "$work"
  rm -f "$dump.partial" "$settings.partial"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$backup_dir"
[ ! -e "$dump" ] && [ ! -e "$settings" ] || exit 2
printf '%s' "$BACKUP_AGE_IDENTITY_BASE64" | base64 -d > "$work/identity"
# Plaintext exists only in the container tmpfs and is removed on every exit.
cat > "$work/settings"
[ -s "$work/settings" ] || exit 2
age -r "$BACKUP_AGE_RECIPIENT" -o "$settings.partial" "$work/settings"
age -d -i "$work/identity" -o "$work/settings.dec" "$settings.partial"
cmp -s "$work/settings" "$work/settings.dec"
# Read every archive entry through pg_restore; no restored SQL is persisted.
pg_dump --format=custom --no-owner --no-privileges \
  | age -r "$BACKUP_AGE_RECIPIENT" -o "$dump.partial"
age -d -i "$work/identity" "$dump.partial" | pg_restore --file /dev/null
mv "$settings.partial" "$settings"
mv "$dump.partial" "$dump"
printf '%s\n' '{"schema":1,"backup_verified":true}'
