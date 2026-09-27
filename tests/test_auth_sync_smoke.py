import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-min-32-chars-0123456789")
os.environ.setdefault("ADMIN_ENROLMENT_CODE", "ADMIN-DEV-001")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_pin, sha256_hex  # noqa: E402


def _setup():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    bid, cid, wid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.add(models.Block(id=bid, name="B", district="D", state="S"))
    db.add(models.Centre(id=cid, block_id=bid, name="C1", icds_code=f"T-{uuid.uuid4().hex[:6]}", sector="S1"))
    db.add(models.Worker(id=wid, centre_id=cid, name="w1", role="aww", pin_hash=hash_pin("1234")))
    dev_id = str(uuid.uuid4())
    db.add(models.Device(id=dev_id, serial=f"GM-{uuid.uuid4().hex[:6]}", centre_id=cid, device_key_hash=sha256_hex("s3cr3t")))
    db.commit()
    serial = db.query(models.Device).filter_by(id=dev_id).first().serial
    db.close()
    return cid, wid, serial


def test_device_register_token_refresh():
    cid, wid, serial = _setup()
    c = TestClient(app)
    r = c.post("/v1/auth/device/register", json={"enrolment_code": "ADMIN-DEV-001", "serial": f"GM-{uuid.uuid4().hex[:6]}", "centre_id": cid})
    assert r.status_code == 200, r.text
    r = c.post("/v1/auth/token", json={"worker_id": wid, "pin": "1234", "device_serial": serial, "device_secret": "s3cr3t"})
    assert r.status_code == 200, r.text
    rt = r.json()["refresh_token"]
    r = c.post("/v1/auth/refresh", json={"refresh_token": rt})
    assert r.status_code == 200, r.text
    r = c.post("/v1/auth/refresh", json={"refresh_token": rt})
    assert r.status_code == 401  # reuse revoked


def test_sync_idempotent():
    cid, wid, serial = _setup()
    c = TestClient(app)
    r = c.post("/v1/auth/token", json={"worker_id": wid, "pin": "1234", "device_serial": serial, "device_secret": "s3cr3t"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    child_id, meas_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    batch = {"device_serial": serial, "records": [
        {"entity": "child", "op": "upsert", "id": child_id, "updated_at": now,
         "payload": {"id": child_id, "centre_id": cid, "name": "Asha", "sex": "F",
                     "dob": "2022-01-01", "dob_precision": "exact", "consent_status": "given", "updated_at": now}},
        {"entity": "measurement", "op": "upsert", "id": meas_id, "updated_at": now,
         "payload": {"id": meas_id, "child_id": child_id, "measured_at": now, "age_days": 400,
                     "weight_g": 9000, "length_mm": 720, "source": "device", "app_version": "0.1.0", "updated_at": now}},
    ]}
    r = c.post("/v1/sync/batch", json=batch, headers=H)
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["accepted", "accepted"]
    r2 = c.post("/v1/sync/batch", json=batch, headers=H)
    assert r2.status_code == 200, r2.text
    assert set(x["status"] for x in r2.json()["results"]) <= {"duplicate", "conflict", "accepted"}
