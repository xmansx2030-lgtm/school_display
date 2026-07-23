#!/bin/sh
set -eu

backup_dir=/data

if [ -z "${RESTIC_REPOSITORY:-}" ] || [ "${RESTIC_REPOSITORY:-}" = "CHANGE_ME" ]; then
  echo "RESTIC_REPOSITORY is not configured; off-site backup was not started." >&2
  exit 2
fi

if [ -z "${RESTIC_PASSWORD:-}" ] && [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
  echo "RESTIC_PASSWORD or RESTIC_PASSWORD_FILE is required." >&2
  exit 2
fi

if ! find "$backup_dir" -maxdepth 1 -type f -name 'postgres-*.dump' -print -quit | grep -q .; then
  echo "No PostgreSQL dump exists in $backup_dir." >&2
  exit 3
fi

# `snapshots` fails when the repository has not been initialized yet.
if ! restic snapshots --no-lock >/dev/null 2>&1; then
  restic init
fi

restic backup "$backup_dir" \
  --tag school-display-postgres \
  --host "${RESTIC_HOST:-school-display-production}"

maintenance_weekday="${RESTIC_MAINTENANCE_WEEKDAY:-7}"
current_weekday="$(date -u +%u)"

if [ "$current_weekday" = "$maintenance_weekday" ]; then
  # Prune and repository validation are substantially more expensive than the
  # incremental backup, so run them once a week instead of every night.
  restic forget \
    --tag school-display-postgres \
    --keep-daily "${RESTIC_KEEP_DAILY:-14}" \
    --keep-weekly "${RESTIC_KEEP_WEEKLY:-8}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" \
    --prune
  restic check
else
  restic forget \
    --tag school-display-postgres \
    --keep-daily "${RESTIC_KEEP_DAILY:-14}" \
    --keep-weekly "${RESTIC_KEEP_WEEKLY:-8}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}"
fi
