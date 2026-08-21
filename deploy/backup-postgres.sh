#!/usr/bin/env bash
set -euo pipefail

project_dir=/opt/school-display/app
backup_dir=/opt/school-display/backups
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/postgres-$timestamp.dump"
temporary="$target.tmp"

umask 077
mkdir -p "$backup_dir"
cd "$project_dir"

cleanup() {
  rm -f -- "$temporary"
}
trap cleanup EXIT

docker compose -f compose.production.yaml exec -T postgres \
  sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$temporary"

test -s "$temporary"
docker compose -f compose.production.yaml exec -T postgres \
  pg_restore --list < "$temporary" >/dev/null
mv "$temporary" "$target"
trap - EXIT

find "$backup_dir" -maxdepth 1 -type f -name 'postgres-*.dump' -mtime +7 -delete
printf 'Created %s\n' "$target"
