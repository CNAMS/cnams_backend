"""Seed minimal fixture: block -> centre -> workers -> device. Idempotent."""
import uuid
from datetime import datetime, timezone

from app import models
from app.db import SessionLocal, engine, Base
from app.security import hash_pin, sha256_hex

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    if db.query(models.Block).count() == 0:
        b = models.Block(id=str(uuid.uuid4()), name="Block-A", district="D1", state="MP")
        db.add(b)
        db.commit()
    else:
        b = db.query(models.Block).first()
    if db.query(models.Centre).count() == 0:
        c = models.Centre(id=str(uuid.uuid4()), block_id=b.id, name="Centre-1", icds_code="ICDS001", sector="S1")
        db.add(c)
        db.commit()
    else:
        c = db.query(models.Centre).first()
    if db.query(models.Worker).filter_by(name="test-aww").first() is None:
        db.add(models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="test-aww", role="aww", pin_hash=hash_pin("1234")))
    if db.query(models.Worker).filter_by(name="test-admin").first() is None:
        db.add(models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="test-admin", role="admin", pin_hash=hash_pin("admin123")))
    if db.query(models.Device).filter_by(serial="GM-003").first() is None:
        db.add(models.Device(id=str(uuid.uuid4()), serial="GM-003", centre_id=c.id, firmware_version="v1.3.0",
                             device_key_hash=sha256_hex("dev-secret-003")))
    db.commit()
    print(f"seeded centre={c.id}")
finally:
    db.close()
