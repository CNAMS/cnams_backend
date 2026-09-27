#!/usr/bin/env bash
# Restore drill (quarterly per roadmap §6): wipe staging, restore, verify row counts.
# Usage: RESTORE_FILE=backups/cnams-<ts>.db.gz ./scripts/restore.sh   (sqlite dev)
#        RESTORE_FILE=backups/cnams-<ts>.sql.gz ./scripts/restore.sh  (postgres)
set -e
: "${RESTORE_FILE:?set RESTORE_FILE}"
if [[ "$RESTORE_FILE" == *.db.gz ]]; then
  gunzip -c "$RESTORE_FILE" > ./cnams.restored.db
  echo "restored sqlite -> ./cnams.restored.db"
  python3 - ./cnams.restored.db <<'EOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
for t in ("blocks","centres","workers","devices","children","measurements","measurement_audit","referrals","sync_log"):
    try:
        print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    except Exception as e:
        print(t, "MISSING", e)
EOF
else
  : "${DATABASE_URL:?set DATABASE_URL}"
  url="${DATABASE_URL/postgresql+psycopg:\/\//postgresql:\/\/}"
  gunzip -c "$RESTORE_FILE" | psql "$url" -v ON_ERROR_STOP=1 -q
  echo "restored postgres from $RESTORE_FILE"
fi
