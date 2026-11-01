#!/usr/bin/env bash
set -euo pipefail

data_dir="${ARENA_DATA_DIR:-$HOME/.pal-arena/server}"
backup_dir="${ARENA_BACKUP_DIR:-$HOME/.pal-arena/backups}"
retention_days="${ARENA_BACKUP_RETENTION_DAYS:-14}"
program="${ARENA_PROGRAM:-/opt/agent-cooperation/.venv/bin/pal-arena}"

install -d -m 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/arena-$stamp.zip"
"$program" maintenance backup --data "$data_dir" --output "$archive"
chmod 600 "$archive"
find "$backup_dir" -type f -name 'arena-*.zip' -mtime "+$retention_days" -delete
echo "Created $archive"
