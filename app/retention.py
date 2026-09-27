"""Retention purge: expired refresh tokens + old sync_log. Run nightly."""
from datetime import datetime, timedelta, timezone

from app import models
from app.db import SessionLocal


def purge(days_sync_log: int = 90) -> dict:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rt = db.query(models.RefreshToken).filter(models.RefreshToken.expires_at < now).delete()
        sl = db.query(models.SyncLog).filter(
            models.SyncLog.received_at < now - timedelta(days=days_sync_log)).delete()
        db.commit()
        return {"refresh_tokens": rt, "sync_log": sl}
    finally:
        db.close()


if __name__ == "__main__":
    print(purge())
