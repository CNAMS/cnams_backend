from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = "up"
    except Exception:
        db_ok = "down"
    return HealthOut(status="ok" if db_ok == "up" else "degraded", db=db_ok, time=datetime.now(timezone.utc))
