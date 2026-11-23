#!/usr/bin/env bash
# PRISM restore from a directory produced by backup.sh. DESTRUCTIVE: replaces current data.
# Usage: scripts/restore.sh ./backups/prism-YYYYmmdd-HHMMSS
set -euo pipefail
SRC="${1:?usage: restore.sh <backup_dir>}"
( cd "$SRC" && sha256sum -c SHA256SUMS )
read -r -p "This replaces the current PRISM database and files. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || { echo "aborted"; exit 1; }
source .env
docker compose stop api worker proxy
echo "→ restoring database"
docker compose exec -T db pg_restore -U "${POSTGRES_USER:-prism}" -d "${POSTGRES_DB:-prism}" --clean --if-exists < "$SRC/db.dump"
echo "→ restoring data volume"
docker compose run --rm --no-deps --user root -v "$(realpath "$SRC")":/backup --entrypoint "" worker \
  sh -c 'find /data -mindepth 1 -delete && tar -C /data -xzf /backup/data.tar.gz'
docker compose up -d
echo "✓ restore complete"
