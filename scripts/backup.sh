#!/usr/bin/env bash
# PRISM backup: consistent Postgres dump + data volume (originals, parquet versions, workbooks).
# Usage: scripts/backup.sh [backup_dir]     (run from the repository root on the server)
set -euo pipefail
DEST="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/prism-$STAMP"
mkdir -p "$OUT"
source .env
echo "→ dumping database"
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-prism}" -d "${POSTGRES_DB:-prism}" --format=custom > "$OUT/db.dump"
echo "→ archiving data volume"
docker compose run --rm --no-deps --user root -v "$(realpath "$OUT")":/backup --entrypoint "" worker \
  tar -C /data -czf /backup/data.tar.gz .
sha256sum "$OUT"/db.dump "$OUT"/data.tar.gz > "$OUT/SHA256SUMS"
echo "✓ backup written to $OUT"
