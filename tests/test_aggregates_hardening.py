"""C5/C8/C9 — dashboard, export, data-quality, crypto, retention, contract."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-min-32-chars-0123456789")
os.environ.setdefault("ADMIN_ENROLMENT_CODE", "ADMIN-DEV-001")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.crypto import decrypt_name, encrypt_name  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.retention import purge  # noqa: E402
from app.security import hash_pin, sha256_hex  # noqa: E402


def _seed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    b = models.Block(id=str(uuid.uuid4()), name="B", district="D", state="S")
    db.add(b)
    db.commit()
    c = models.Centre(id=str(uuid.uuid4()), block_id=b.id, name="C", icds_code="E1", sector="S1")
    db.add(c)
    db.commit()
    w = models.Worker(id=str(uuid.uuid4()), centre_id=c.id, name="aww", role="aww", pin_hash=hash_pin("1234"))
    db.add(w)
    db.commit()
    serial = f"GE-{uuid.uuid4().hex[:5]}"
    dev = models.Device(id=str(uuid.uuid4()), serial=serial, centre_id=c.id, device_key_hash=sha256_hex("s3"))
    db.add(dev)
    db.commit()
    ids = (c.id, w.id, dev.id, serial)
    db.close()
    cl = TestClient(app, raise_server_exceptions=False)
    # bypass rate limiter state across tests
    app.middleware_stack = None
    cl = TestClient(app, raise_server_exceptions=False)
    r = cl.post("/v1/auth/token", json={"worker_id": ids[1], "pin": "1234", "device_serial": serial, "device_secret": "s3"})
    assert r.status_code == 200, r.text
    return cl, {"Authorization": f"Bearer {r.json()['access_token']}"}, ids


def _sync_child(cl, H, cid, name="Asha"):
    c1, m1 = str(uuid.uuid4()), str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    b = {"records": [
        {"entity": "child", "op": "upsert", "id": c1, "updated_at": ts,
         "payload": {"id": c1, "centre_id": cid, "name": name, "sex": "F", "dob": "2022-01-01",
                     "dob_precision": "exact", "consent_status": "given", "updated_at": ts}},
        {"entity": "measurement", "op": "upsert", "id": m1, "updated_at": ts,
         "payload": {"id": m1, "child_id": c1, "measured_at": ts, "age_days": 400, "weight_g": 9000,
                     "length_mm": 720, "muac_mm": 110, "source": "device", "app_version": "0.1.0", "updated_at": ts}},
    ]}
    r = cl.post("/v1/sync/batch", json=b, headers=H)
    assert r.status_code == 200, r.text
    return c1, m1


def test_dashboard_four_numbers():
    cl, H, (cid, _, _, _) = _seed()
    c1, _ = _sync_child(cl, H, cid)
    r = cl.get(f"/v1/dashboard/summary?scope={cid}", headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 1 and j["screened_this_month"] == 1
    assert j["flagged"] == 1  # muac 110 -> sam latest
    assert j["overdue"] == 0 and "server_time" in j


def test_export_poshan_order_and_filters():
    cl, H, (cid, _, _, _) = _seed()
    _sync_child(cl, H, cid)
    r = cl.get(f"/v1/export/csv?scope={cid}", headers=H)
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0].split(",") == ["icds_id", "child_name", "sex", "dob", "age_days", "measured_at",
                                   "weight_kg", "length_cm", "muac_cm", "oedema", "waz", "haz", "whz", "classification"]
    assert "9.00" in lines[1] and "sam" in lines[1]
    past = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert cl.get(f"/v1/export/csv?scope={cid}&from={past}", headers=H).text.strip().count("\n") == 0
    assert cl.get(f"/v1/export/csv?scope={cid}&from=bad", headers=H).status_code == 422


def test_data_quality_lists_mismatch():
    cl, H, (cid, _, _, _) = _seed()
    _sync_child(cl, H, cid)
    r = cl.get(f"/v1/data-quality?scope={cid}", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"engine_mismatch", "implausible", "out_of_range", "stale_devices"}


def test_crypto_roundtrip_and_legacy():
    assert decrypt_name(encrypt_name("Asha Kumari")) == "Asha Kumari"
    assert decrypt_name(b"Asha") == "Asha"  # C1 legacy raw
    os.environ.pop("NAME_ENC_KEY", None)


def test_crypto_fernet_when_key_set():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["NAME_ENC_KEY"] = key
    try:
        from importlib import reload
        import app.config as cfg
        reload(cfg)
        import app.crypto as cr
        reload(cr)
        blob = cr.encrypt_name("Meera")
        assert blob.startswith(b"enc:") and cr.decrypt_name(blob) == "Meera"
    finally:
        os.environ.pop("NAME_ENC_KEY", None)
        reload(cfg)


def test_retention_purges():
    _seed()
    db = SessionLocal()
    old = datetime.now(timezone.utc) - timedelta(days=100)
    db.add(models.SyncLog(device_id=None, worker_id=None, batch_size=1, accepted=1, rejected=0, conflicts=0))
    db.commit()
    db.query(models.SyncLog).update({"received_at": old})
    db.commit()
    db.close()
    out = purge(days_sync_log=90)
    assert out["sync_log"] >= 1


def test_openapi_contract_has_routes():
    cl, _, _ = _seed()
    r = cl.get("/openapi.json")
    paths = r.json()["paths"]
    for p in ("/v1/auth/device/register", "/v1/auth/token", "/v1/auth/refresh", "/v1/auth/revoke",
              "/v1/sync/batch", "/v1/bootstrap", "/v1/children", "/v1/dashboard/summary",
              "/v1/export/csv", "/v1/export/paired-csv", "/v1/data-quality", "/health"):
        assert p in paths, p


def test_256kb_cap():
    cl, H, (cid, _, _, _) = _seed()
    big = "x" * (260 * 1024)
    r = cl.post("/v1/sync/batch", json={"records": [{"entity": "child", "op": "upsert", "id": str(uuid.uuid4()),
              "payload": {"id": str(uuid.uuid4()), "centre_id": cid, "name": big, "sex": "M",
                          "dob": "2022-01-01", "dob_precision": "exact", "consent_status": "given"}}]}, headers=H)
    assert r.status_code in (413, 422)
