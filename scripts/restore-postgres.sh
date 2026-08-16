#!/bin/sh
set -eu

: "${BACKUP_FILE:?BACKUP_FILE must be an absolute path under /backups}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${BACKUP_AGE_IDENTITY:?BACKUP_AGE_IDENTITY is required}"

if [ "${RESTORE_CONFIRM:-}" != "RESTORE" ]; then
  echo "restore refused: set RESTORE_CONFIRM=RESTORE" >&2
  exit 2
fi

case "$BACKUP_FILE" in
  /backups/*.dump.age) ;;
  *)
    echo "restore refused: BACKUP_FILE must match /backups/*.dump.age" >&2
    exit 2
    ;;
esac

if [ ! -f "$BACKUP_FILE" ]; then
  echo "backup not found: $BACKUP_FILE" >&2
  exit 1
fi

export PGPASSWORD
umask 077
temporary="$(mktemp /tmp/rail-restore.dump.XXXXXX)"
identity_file="$(mktemp /tmp/rail-age-identity.XXXXXX)"
trap 'rm -f "$temporary" "$identity_file"' EXIT INT TERM
printf '%s\n' "$BACKUP_AGE_IDENTITY" > "$identity_file"

age --decrypt --identity "$identity_file" --output "$temporary" "$BACKUP_FILE"
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction \
  --exit-on-error --dbname "$PGDATABASE" "$temporary"

echo "restore completed: $BACKUP_FILE"
