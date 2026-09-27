"""C4 — one test per cell of roadmap §4 matrix + PII rule."""
import os
import uuid
from datetime import date, datetime, timezone

os.environ.setdefault("JWT_SECRET", "test-secret-min-32-chars-0123456789")
os.environ.setdefault("ADMIN_ENROLMENT_CODE", "ADMIN-DEV-001")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_pin, sha256_hex  # noqa: E402


def _seed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    b1 = models.Block(id=str(uuid.uuid4()), name="B1", district="D", state="S")
    b2 = models.Block(id=str(uuid.uuid4()), name="B2", district="D", state="S")
    db.add_all([b1, b2])
    db.commit()
    c1 = models.Centre(id=str(uuid.uuid4()), block_id=b1.id, name="C1", icds_code=f"A-{uuid.uuid4().hex[:5]}", sector="SEC1")
    c2 = models.Centre(id=str(uuid.uuid4()), block_id=b1.id, name="C2", icds_code=f"B-{uuid.uuid4().hex[:5]}", sector="SEC2")
    c3 = models.Centre(id=str(uuid.uuid4()), block_id=b2.id, name="C3", icds_code=f"C-{uuid.uuid4().hex[:5]}", sector="SEC3")
    db.add_all([c1, c2, c3])
    db.commit()
    mk = lambda name, role, cen: models.Worker(id=str(uuid.uuid4()), centre_id=cen, name=name, role=role, pin_hash=hash_pin("pw1234"))
    w_aww = mk("aww1", "aww", c1.id)
    w_sup = mk("sup1", "supervisor", c1.id)
    w_cdpo = mk("cdpo1", "cdpo", c1.id)
    w_admin = mk("adm1", "admin", c1.id)
    db.add_all([w_aww, w_sup, w_cdpo, w_admin])
    db.commit()
    devs = {}
    for i, w in enumerate([w_aww, w_sup, w_cdpo, w_admin]):
        s = f"GS-{w.role}-{uuid.uuid4().hex[:4]}"
        db.add(models.Device(id=str(uuid.uuid4()), serial=s, centre_id=c1.id, device_key_hash=sha256_hex(f"sec-{i}")))
        devs[w.role] = (s, f"sec-{i}", w.id)
    # child in c1
    now = datetime.now(timezone.utc)
    ch = models.Child(id=str(uuid.uuid4()), centre_id=c1.id, name_enc=b"Asha Kumari", name_hash="h",
                      sex="F", dob=date(2022, 1, 1), dob_precision="exact", consent_status="given",
                      created_at=now, updated_at=now)
    db.add(ch)
    db.commit()
    ids = {"c1": c1.id, "c2": c2.id, "c3": c3.id, "child": ch.id}
    db.close()
    return ids, devs


def _tok(cl, serial, sec, wid):
    r = cl.post("/v1/auth/token", json={"worker_id": wid, "pin": "pw1234", "device_serial": serial, "device_secret": sec})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_matrix():
    ids, devs = _seed()
    cl = TestClient(app)
    H = {role: _tok(cl, s, sec, wid) for role, (s, sec, wid) in devs.items()}

    # POST /sync/batch: aww+admin ok, supervisor denied
    assert cl.post("/v1/sync/batch", json={"records": []}, headers=H["aww"]).status_code == 200
    assert cl.post("/v1/sync/batch", json={"records": []}, headers=H["admin"]).status_code == 200
    assert cl.post("/v1/sync/batch", json={"records": []}, headers=H["supervisor"]).status_code == 403

    # GET /children scoping
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["aww"]).status_code == 200
    assert cl.get(f"/v1/children?centre_id={ids['c2']}", headers=H["aww"]).status_code == 403  # aww cross-centre
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["supervisor"]).status_code == 200
    assert cl.get(f"/v1/children?centre_id={ids['c2']}", headers=H["supervisor"]).status_code == 403  # cross-sector
    assert cl.get(f"/v1/children?centre_id={ids['c2']}", headers=H["cdpo"]).status_code == 200  # same block
    assert cl.get(f"/v1/children?centre_id={ids['c3']}", headers=H["cdpo"]).status_code == 403  # cross-block
    assert cl.get(f"/v1/children?centre_id={ids['c3']}", headers=H["admin"]).status_code == 200

    # PII: aww full, supervisor/cdpo initials, admin full
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["aww"]).json()[0]["name"] == "Asha Kumari"
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["supervisor"]).json()[0]["name"] == "AK"
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["cdpo"]).json()[0]["name"] == "AK"
    assert cl.get(f"/v1/children?centre_id={ids['c1']}", headers=H["admin"]).json()[0]["name"] == "Asha Kumari"

    # DELETE: aww denied, sup/cdpo/admin ok (use fresh child per role)
    for role, expect in (("aww", 403), ("supervisor", 200), ("cdpo", 200), ("admin", 200)):
        db = SessionLocal()
        now = datetime.now(timezone.utc)
        nid = str(uuid.uuid4())
        db.add(models.Child(id=nid, centre_id=ids["c1"], name_enc=b"Temp", name_hash="h", sex="M",
                            dob=date(2022, 1, 1), dob_precision="exact", consent_status="given",
                            created_at=now, updated_at=now))
        db.commit()
        db.close()
        assert cl.delete(f"/v1/children/{nid}", headers=H[role]).status_code == expect, role

    # dashboard + export scoping mirrors children
    assert cl.get(f"/v1/dashboard/summary?scope={ids['c1']}", headers=H["aww"]).status_code == 200
    assert cl.get(f"/v1/dashboard/summary?scope={ids['c2']}", headers=H["aww"]).status_code == 403
    assert cl.get(f"/v1/export/csv?scope={ids['c1']}", headers=H["aww"]).status_code == 200
    assert cl.get(f"/v1/export/csv?scope={ids['c2']}", headers=H["aww"]).status_code == 403

    # unauthenticated denied
    assert cl.get("/v1/bootstrap").status_code in (401, 403)
    assert cl.get("/v1/children").status_code in (401, 403)
