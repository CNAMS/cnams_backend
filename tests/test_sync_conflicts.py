"""C4 — sync idempotency/conflict/partial-success per roadmap §3 + FR-SRV-2/4."""
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


def _fresh():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    b = models.Block(id=str(uuid.uuid4()), name="B", district="D", state="S")
    db.add(b)
    db.commit()
    c = models.Centre(id=str(uuid.uuid4()), block_id=b.id, name="C", icds_code=f"I-{uuid.uuid4().hex[:6]}", sector="S1")
    db.add(c)
    db.commit()
    w = models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="aww", role="aww", pin_hash=hash_pin("1234"))
    db.add(w)
    db.commit()
    serial = f"GM-{uuid.uuid4().hex[:6]}"
    db.add(models.Device(id=str(uuid.uuid4()), serial=serial, centre_id=c.id, device_key_hash=sha256_hex("s3")))
    db.commit()
    wid, cid = w.id, c.id
    db.close()
    cl = TestClient(app)
    r = cl.post("/v1/auth/token", json={"worker_id": wid, "pin": "1234", "device_serial": serial, "device_secret": "s3"})
    assert r.status_code == 200, r.text
    return cl, {"Authorization": f"Bearer {r.json()['access_token']}"}, cid, serial


def _child_batch(cid, child_id, meas_id, ts, **mkw):
    return {"records": [
        {"entity": "child", "op": "upsert", "id": child_id, "updated_at": ts,
         "payload": {"id": child_id, "centre_id": cid, "name": "Asha", "sex": "F", "dob": "2022-01-01",
                     "dob_precision": "exact", "consent_status": "given", "updated_at": ts}},
        {"entity": "measurement", "op": "upsert", "id": meas_id, "updated_at": ts,
         "payload": {"id": meas_id, "child_id": child_id, "measured_at": ts, "age_days": 400,
                     "weight_g": 9000, "length_mm": 720, "source": "device", "app_version": "0.1.0",
                     "updated_at": ts, **mkw}},
    ]}


def test_replay_3x_idempotent():
    cl, H, cid, serial = _fresh()
    c1, m1 = str(uuid.uuid4()), str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    b = _child_batch(cid, c1, m1, ts)
    assert [x["status"] for x in cl.post("/v1/sync/batch", json=b, headers=H).json()["results"]] == ["accepted", "accepted"]
    for _ in range(2):
        r = cl.post("/v1/sync/batch", json=b, headers=H)
        assert r.status_code == 200  # never 500
        assert set(x["status"] for x in r.json()["results"]) == {"duplicate"}


def test_lww_conflict_audits_loser():
    cl, H, cid, serial = _fresh()
    c1, m1 = str(uuid.uuid4()), str(uuid.uuid4())
    t0 = datetime.now(timezone.utc)
    b0 = _child_batch(cid, c1, m1, t0.isoformat(), weight_g=9000)
    assert cl.post("/v1/sync/batch", json=b0, headers=H).status_code == 200
    # older -> duplicate, no new row/version
    bold = _child_batch(cid, c1, m1, (t0 - timedelta(hours=1)).isoformat(), weight_g=8000)
    assert set(x["status"] for x in cl.post("/v1/sync/batch", json=bold, headers=H).json()["results"]) == {"duplicate"}
    # newer -> conflict + audit + version 2
    bnew = _child_batch(cid, c1, m1, (t0 + timedelta(hours=1)).isoformat(), weight_g=9500)
    res = cl.post("/v1/sync/batch", json=bnew, headers=H).json()["results"]
    assert {x["status"] for x in res} == {"conflict"}
    db = SessionLocal()
    m = db.get(models.Measurement, m1)
    assert m.version == 2 and m.weight_g == 9500
    audits = db.query(models.MeasurementAudit).filter_by(measurement_id=m1).all()
    assert len(audits) == 1 and audits[0].snapshot["weight_g"] == 9000
    db.close()


def test_partial_success_one_bad_record():
    cl, H, cid, serial = _fresh()
    c1, m1, m2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    b = _child_batch(cid, c1, m1, ts)
    b["records"].append({"entity": "measurement", "op": "upsert", "id": m2, "updated_at": ts,
                         "payload": {"id": m2, "child_id": c1, "measured_at": ts, "age_days": 99999,
                                     "weight_g": 9000, "source": "device", "app_version": "0.1.0"}})
    r = cl.post("/v1/sync/batch", json=b, headers=H)
    assert r.status_code == 200
    by_id = {x["id"]: x["status"] for x in r.json()["results"]}
    assert by_id[m1] == "accepted" and by_id[m2] == "rejected"


def test_batch_cap_50():
    cl, H, cid, serial = _fresh()
    ts = datetime.now(timezone.utc).isoformat()
    recs = [{"entity": "child", "op": "upsert", "id": str(uuid.uuid4()), "updated_at": ts,
             "payload": {"id": str(uuid.uuid4()), "centre_id": cid, "name": "x", "sex": "M",
                         "dob": "2022-01-01", "dob_precision": "exact", "consent_status": "given"}} for _ in range(51)]
    # Pydantic max_length=50 -> 422; manual guard -> 413. Either proves the cap.
    assert cl.post("/v1/sync/batch", json={"records": recs}, headers=H).status_code in (413, 422)


def test_muac_sam_without_tables_and_flags_stored():
    cl, H, cid, serial = _fresh()
    c1, m1 = str(uuid.uuid4()), str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    b = _child_batch(cid, c1, m1, ts, muac_mm=110)
    assert cl.post("/v1/sync/batch", json=b, headers=H).status_code == 200
    db = SessionLocal()
    m = db.get(models.Measurement, m1)
    assert m.classification == "sam"  # MUAC needs no LMS table
    assert m.engine_mismatch is False
    db.close()
