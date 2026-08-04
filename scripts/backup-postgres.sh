#!/bin/sh
set -eu
set -o pipefail

mode="${1:-daemon}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required}"

export PGPASSWORD
backup_dir="${BACKUP_DIR:-/backups}"
interval="${BACKUP_INTERVAL_SECONDS:-86400}"
retention="${BACKUP_RETENTION_DAYS:-14}"

backup_once() {
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  target="${backup_dir}/${PGDATABASE}_${timestamp}.dump.age"
  temporary="${target}.partial"

  mkdir -p "$backup_dir"
  umask 077
  trap 'rm -f "$temporary"' EXIT INT TERM
  pg_dump --format=custom --no-owner --no-privileges \
    | age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$temporary"
  mv "$temporary" "$target"
  trap - EXIT INT TERM
  find "$backup_dir" -type f -name '*.dump.age' -mtime "+$retention" -delete
  printf '%s\n' "$target"
}

case "$mode" in
  once)
    backup_once
    ;;
  daemon)
    while :; do
      backup_once
      sleep "$interval"
    done
    ;;
  *)
    echo "usage: backup-postgres.sh [once|daemon]" >&2
    exit 2
    ;;
esac
