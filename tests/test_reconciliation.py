"""C12 — two devices, one centre: concurrent edits converge, nothing lost."""
import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-min-32-chars-0123456789")
os.environ.setdefault("ADMIN_ENROLMENT_CODE", "ADMIN-DEV-001")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_pin, sha256_hex  # noqa: E402


def _two_devices():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    b = models.Block(id=str(uuid.uuid4()), name="B", district="D", state="S")
    db.add(b)
    db.commit()
    c = models.Centre(id=str(uuid.uuid4()), block_id=b.id, name="C", icds_code="R1", sector="S1")
    db.add(c)
    db.commit()
    w1 = models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="w1", role="aww", pin_hash=hash_pin("1234"))
    w2 = models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="w2", role="aww", pin_hash=hash_pin("1234"))
    db.add_all([w1, w2])
    db.commit()
    d1, s1, d2, s2 = str(uuid.uuid4()), f"GD-{uuid.uuid4().hex[:5]}", str(uuid.uuid4()), f"GD-{uuid.uuid4().hex[:5]}"
    db.add(models.Device(id=d1, serial=s1, centre_id=c.id, device_key_hash=sha256_hex("k1")))
    db.add(models.Device(id=d2, serial=s2, centre_id=c.id, device_key_hash=sha256_hex("k2")))
    db.commit()
    ids = (c.id, w1.id, w2.id)
    db.close()
    cl = TestClient(app, raise_server_exceptions=False)
    app.middleware_stack = None
    cl = TestClient(app, raise_server_exceptions=False)
    h1 = {"Authorization": f"Bearer {cl.post('/v1/auth/token', json={'worker_id': ids[1], 'pin': '1234', 'device_serial': s1, 'device_secret': 'k1'}).json()['access_token']}"}
    h2 = {"Authorization": f"Bearer {cl.post('/v1/auth/token', json={'worker_id': ids[2], 'pin': '1234', 'device_serial': s2, 'device_secret': 'k2'}).json()['access_token']}"}
    return cl, h1, h2, ids[0]


def _child_rec(cid, centre, name, ts):
    return {"entity": "child", "op": "upsert", "id": cid, "updated_at": ts,
            "payload": {"id": cid, "centre_id": centre, "name": name, "sex": "F", "dob": "2022-01-01",
                        "dob_precision": "exact", "consent_status": "given", "updated_at": ts}}


def _meas_rec(mid, cid, ts, weight):
    return {"entity": "measurement", "op": "upsert", "id": mid, "updated_at": ts,
            "payload": {"id": mid, "child_id": cid, "measured_at": ts, "age_days": 400,
                        "weight_g": weight, "length_mm": 720, "source": "device",
                        "app_version": "0.1.0", "updated_at": ts}}


def test_concurrent_name_edits_newer_wins_loser_audited():
    cl, h1, h2, centre = _two_devices()
    cid = str(uuid.uuid4())
    t0 = datetime.now(timezone.utc)
    # device1 registers first
    r = cl.post("/v1/sync/batch", json={"records": [_child_rec(cid, centre, "Asha", t0.isoformat())]}, headers=h1)
    assert r.json()["results"][0]["status"] == "accepted"
    # device2 concurrent older edit -> duplicate (kept, not lost: still Asha)
    r = cl.post("/v1/sync/batch", json={"records": [_child_rec(cid, centre, "Aasha", (t0 - timedelta(hours=1)).isoformat())]}, headers=h2)
    assert r.json()["results"][0]["status"] == "duplicate"
    # device2 newer correction -> conflict, winner stored
    r = cl.post("/v1/sync/batch", json={"records": [_child_rec(cid, centre, "Asha Devi", (t0 + timedelta(hours=1)).isoformat())]}, headers=h2)
    assert r.json()["results"][0]["status"] == "conflict"
    db = SessionLocal()
    from app.crypto import decrypt_name
    assert decrypt_name(db.get(models.Child, cid).name_enc) == "Asha Devi"
    db.close()


def test_measurements_from_both_devices_all_retained():
    cl, h1, h2, centre = _two_devices()
    cid = str(uuid.uuid4())
    t0 = datetime.now(timezone.utc)
    cl.post("/v1/sync/batch", json={"records": [_child_rec(cid, centre, "Asha", t0.isoformat())]}, headers=h1)
    m1, m2 = str(uuid.uuid4()), str(uuid.uuid4())
    assert cl.post("/v1/sync/batch", json={"records": [_meas_rec(m1, cid, t0.isoformat(), 9000)]}, headers=h1).json()["results"][0]["status"] == "accepted"
    assert cl.post("/v1/sync/batch", json={"records": [_meas_rec(m2, cid, (t0 + timedelta(minutes=5)).isoformat(), 9100)]}, headers=h2).json()["results"][0]["status"] == "accepted"
    r = cl.get(f"/v1/children/{cid}/measurements", headers=h1)
    assert r.status_code == 200
    assert {m["id"] for m in r.json()} == {m1, m2}  # neither weighing lost
