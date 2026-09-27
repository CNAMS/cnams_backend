#!/usr/bin/env bash
# Nightly backup: pg_dump (prod) or sqlite copy (dev) -> backups/, 30-day retention.
# Roadmap §7: nightly pg_dump to object storage, 30-day retention, monthly restore drill.
set -e
OUT="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
if [ -n "${DATABASE_URL:-}" ] && [[ "$DATABASE_URL" == postgresql* ]]; then
  # expects pg_dump with PG* env or a postgres:// URL via pg-connection-string parsing by pg_dump? Use python bridge:
  python3 - "$OUT" "$TS" <<'EOF'
import os, subprocess, sys
url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
out, ts = sys.argv[1], sys.argv[2]
dst = f"{out}/cnams-{ts}.sql.gz"
p1 = subprocess.Popen(["pg_dump", url], stdout=subprocess.PIPE)
with open(dst, "wb") as f:
    subprocess.run(["gzip", "-c"], stdin=p1.stdout, stdout=f, check=True)
print(dst)
EOF
else
  DB="${SQLITE_DB:-./cnams.db}"
  DST="$OUT/cnams-$TS.db"
  cp "$DB" "$DST"
  gzip -f "$DST"
  echo "$DST.gz"
fi
find "$OUT" -type f -mtime +30 -delete
echo "retention: 30 days enforced"
