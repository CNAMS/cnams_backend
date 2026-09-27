"""Sync + core data routes — roadmap §3. Idempotent, per-record, never 500."""
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.deps import Principal, get_current_principal, require_roles, scope_centre_ids
from app.schemas import (
    ChildOut,
    ChildUpsert,
    MeasurementUpsert,
    RecordResult,
    ReferralOutcomeIn,
    SyncBatchIn,
    SyncBatchOut,
)
from app.zscore import recompute

router = APIRouter(tags=["sync"])


def _initials(name: str) -> str:
    parts = name.strip().split()
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def _enc(name: str) -> bytes:
    from app.crypto import encrypt_name
    return encrypt_name(name)


def _dec(b: bytes) -> str:
    from app.crypto import decrypt_name
    return decrypt_name(b)


def _hash_name(name: str, centre_id: str) -> str:
    import hashlib as _h
    return _h.sha256(f"{centre_id}|{name.strip().lower()}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---- bootstrap ----
@router.get("/bootstrap")
def bootstrap(pr: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    from app.config import settings
    return {
        "centre_id": pr.centre_id,
        "role": pr.role,
        "reference_table_version": settings.reference_table_version,
        "server_time": _now().isoformat(),
    }


# ---- sync/batch ----
@router.post("/sync/batch", response_model=SyncBatchOut)
def sync_batch(
    body: SyncBatchIn,
    pr: Principal = Depends(require_roles("aww", "admin")),
    db: Session = Depends(get_db),
):
    if len(body.records) > 50:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "max 50 records")
    if len(body.model_dump_json()) > 256 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "max 256KB per batch")
    results: list[RecordResult] = []
    accepted = rejected = conflicts = 0
    for rec in body.records:
        try:
            rid = rec.record_id()
        except ValueError as e:
            results.append(RecordResult(id="unknown", status="rejected", code=400, error=str(e)))
            rejected += 1
            continue
        try:
            if rec.entity == "child":
                st = _upsert_child(db, rec.payload, rid, pr)
            elif rec.entity == "measurement":
                st = _upsert_measurement(db, rec.payload, rid, pr)
            elif rec.entity == "referral":
                st = _upsert_referral(db, rec.payload, rid)
            else:
                st = ("rejected", f"unknown entity {rec.entity}", None)
        except HTTPException as e:
            db.rollback()
            st = ("rejected", str(e.detail), None)
        except Exception as e:  # per-record, never fail batch
            db.rollback()
            st = ("rejected", f"{type(e).__name__}", None)
        status_s, err, server_ts = st
        code = {"accepted": 200, "duplicate": 200, "conflict": 409, "rejected": 422}[status_s]
        if status_s == "accepted":
            accepted += 1
        elif status_s == "conflict":
            conflicts += 1
        elif status_s == "rejected":
            rejected += 1
        else:
            accepted += 1  # duplicate counts as ok
        results.append(RecordResult(id=rid, entity_id=rid, status=status_s, code=code,
                                    server_updated_at=server_ts, error=err))
    # sync_log (device may be None in tests)
    try:
        db.add(models.SyncLog(device_id=pr.device_id, worker_id=pr.worker_id,
                              batch_size=len(body.records), accepted=accepted,
                              rejected=rejected, conflicts=conflicts))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return SyncBatchOut(results=results, server_time=_now())


def _upsert_child(db: Session, p: dict, rid: str, pr: Principal):
    try:
        data = dict(p)
        data.setdefault("id", rid)
        cu = ChildUpsert(**data)
    except Exception as e:
        return ("rejected", f"validation: {e}", None)
    if cu.sex not in ("M", "F"):
        return ("rejected", "sex must be M|F", None)
    if pr.role == "aww" and cu.centre_id != pr.centre_id:
        return ("rejected", "cross-centre denied", None)
    centre = db.get(models.Centre, cu.centre_id)
    if centre is None:
        return ("rejected", "unknown centre", None)
    existing = db.get(models.Child, cu.id)
    ts = _aware(cu.updated_at) or _now()
    if existing:
        if _aware(existing.updated_at) and _aware(existing.updated_at) >= ts:  # type: ignore[operator]
            return ("duplicate", None, existing.updated_at)
        # LWW: snapshot losers? children overwrite (measurements use audit)
        existing.centre_id = cu.centre_id
        existing.name_enc = _enc(cu.name)
        existing.name_hash = _hash_name(cu.name, cu.centre_id)
        existing.sex = cu.sex
        existing.dob = cu.dob
        existing.dob_precision = cu.dob_precision
        existing.consent_status = cu.consent_status
        existing.consent_form_ref = cu.consent_form_ref
        existing.updated_at = ts
        db.commit()
        return ("conflict", None, ts)
    if db.query(models.Child).filter_by(centre_id=cu.centre_id, icds_id=cu.icds_id).first() and cu.icds_id:
        return ("rejected", "icds_id taken in centre", None)
    db.add(models.Child(id=cu.id, centre_id=cu.centre_id, icds_id=cu.icds_id,
                        name_enc=_enc(cu.name), name_hash=_hash_name(cu.name, cu.centre_id),
                        sex=cu.sex, dob=cu.dob, dob_precision=cu.dob_precision,
                        consent_status=cu.consent_status, consent_form_ref=cu.consent_form_ref,
                        created_at=ts, updated_at=ts))
    db.commit()
    return ("accepted", None, ts)


def _upsert_measurement(db: Session, p: dict, rid: str, pr: Principal):
    try:
        data = dict(p)
        data.setdefault("id", rid)
        mu = MeasurementUpsert(**data)
    except Exception as e:
        return ("rejected", f"validation: {e}", None)
    if mu.age_days < 0 or mu.age_days > 2200:
        return ("rejected", "age_days out of range", None)
    child = db.get(models.Child, mu.child_id)
    if child is None:
        return ("rejected", "unknown child", None)
    if pr.role == "aww" and child.centre_id != pr.centre_id:
        return ("rejected", "cross-centre denied", None)
    ts = _aware(mu.updated_at) or _aware(mu.measured_at) or _now()
    existing = db.get(models.Measurement, mu.id)
    z = recompute({**p, "age_days": mu.age_days, "weight_g": mu.weight_g, "length_mm": mu.length_mm,
                   "position": mu.position, "client_waz": mu.client_waz, "client_haz": mu.client_haz,
                   "client_whz": mu.client_whz, "muac_mm": mu.muac_mm, "oedema": mu.oedema}, child.sex)
    if existing:
        if _aware(existing.updated_at) and _aware(existing.updated_at) >= ts:  # type: ignore[operator]
            return ("duplicate", None, existing.updated_at)
        # snapshot loser -> audit (FR-SRV-4), LWW
        db.add(models.MeasurementAudit(measurement_id=existing.id, version=existing.version,
               snapshot={"weight_g": existing.weight_g, "length_mm": existing.length_mm,
                         "muac_mm": existing.muac_mm, "waz": existing.waz, "haz": existing.haz,
                         "whz": existing.whz, "classification": existing.classification},
               reason="conflict-lww"))
        for k in ("weight_g", "length_mm", "muac_mm", "position", "oedema", "source"):
            setattr(existing, k, getattr(mu, k, getattr(existing, k)))
        existing.waz, existing.haz, existing.whz, existing.maz = z["waz"], z["haz"], z["whz"], z["maz"]
        existing.classification = z["classification"]
        existing.client_waz, existing.client_haz, existing.client_whz = mu.client_waz, mu.client_haz, mu.client_whz
        existing.engine_mismatch = z["engine_mismatch"]
        existing.flags = {"flags": z.get("flags", [])}
        existing.engine_version = z["engine_version"]
        existing.app_version = mu.app_version
        existing.version += 1
        existing.updated_at = ts
        db.commit()
        return ("conflict", None, ts)
    db.add(models.Measurement(
        id=mu.id, child_id=mu.child_id, worker_id=pr.worker_id,
        device_id=pr.device_id, measured_at=mu.measured_at, age_days=mu.age_days,
        weight_g=mu.weight_g, length_mm=mu.length_mm, muac_mm=mu.muac_mm,
        position=mu.position, oedema=mu.oedema, source=mu.source,
        waz=z["waz"], haz=z["haz"], whz=z["whz"], maz=z["maz"],
        classification=z["classification"], client_waz=mu.client_waz,
        client_haz=mu.client_haz, client_whz=mu.client_whz,
        engine_mismatch=z["engine_mismatch"], flags={"flags": z.get("flags", [])}, engine_version=z["engine_version"],
        app_version=mu.app_version, version=1, updated_at=ts))
    db.commit()
    return ("accepted", None, ts)


def _upsert_referral(db: Session, p: dict, rid: str):
    m = db.get(models.Measurement, p.get("measurement_id", ""))
    if m is None:
        return ("rejected", "unknown measurement", None)
    if db.get(models.Referral, rid):
        return ("duplicate", None, None)
    try:
        db.add(models.Referral(id=rid, measurement_id=m.id, referred_to=p.get("referred_to"),
               referred_at=p.get("referred_at", _now())))
        db.commit()
        return ("accepted", None, None)
    except Exception as e:
        db.rollback()
        return ("rejected", str(e), None)


# ---- children delta + history ----
@router.get("/children", response_model=list[ChildOut])
def list_children(centre_id: str | None = Query(default=None), since: datetime | None = None,
                  pr: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    allowed = scope_centre_ids(pr, db, centre_id)
    q = db.query(models.Child).filter(models.Child.deleted_at.is_(None))
    if allowed is not None:
        q = q.filter(models.Child.centre_id.in_(allowed))
    if since:
        q = q.filter(models.Child.updated_at > since)
    out: list[ChildOut] = []
    for c in q.limit(500).all():
        name = _dec(c.name_enc)
        # PII rule §4: supervisor/cdpo see initials only
        if pr.role in ("supervisor", "cdpo") and not (pr.role == "supervisor" and False):
            # supervisor sees own sector fully? spec says initials only — apply initials
            name = _initials(name)
        out.append(ChildOut(id=c.id, centre_id=c.centre_id, icds_id=c.icds_id, name=name,
                            sex=c.sex, dob=c.dob, updated_at=c.updated_at))
    return out


@router.get("/children/{cid}/measurements")
def child_measurements(cid: str, pr: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    c = db.get(models.Child, cid)
    if c is None or c.deleted_at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "child not found")
    allowed = scope_centre_ids(pr, db, c.centre_id)
    if allowed is not None and c.centre_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "denied")
    rows = db.query(models.Measurement).filter_by(child_id=cid).order_by(models.Measurement.measured_at.desc()).all()
    return [{"id": m.id, "measured_at": m.measured_at, "age_days": m.age_days,
             "weight_g": m.weight_g, "length_mm": m.length_mm, "muac_mm": m.muac_mm,
             "waz": m.waz, "haz": m.haz, "whz": m.whz, "classification": m.classification,
             "engine_mismatch": m.engine_mismatch, "version": m.version} for m in rows]


@router.delete("/children/{cid}")
def delete_child(cid: str, pr: Principal = Depends(require_roles("supervisor", "cdpo", "admin")),
                 db: Session = Depends(get_db)):
    c = db.get(models.Child, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "child not found")
    # FR-SRV-8: hard-delete PII, retain anonymised measurement shell
    c.name_enc = b""
    c.name_hash = hashlib.sha256(f"deleted|{cid}".encode()).hexdigest()
    c.consent_status = "withdrawn"
    c.deleted_at = _now()
    db.commit()
    return {"deleted": True}


# ---- referrals ----
@router.post("/referrals/{rid}/outcome")
def referral_outcome(rid: str, body: ReferralOutcomeIn,
                     pr: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    r = db.get(models.Referral, rid)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "referral not found")
    r.outcome = body.outcome
    r.outcome_recorded_at = _now()
    db.commit()
    return {"ok": True}


# ---- dashboard + export ----
@router.get("/dashboard/summary")
def dashboard_summary(scope: str | None = None, pr: Principal = Depends(get_current_principal),
                      db: Session = Depends(get_db)):
    from datetime import date
    allowed = scope_centre_ids(pr, db, scope)
    cq = db.query(models.Child).filter(models.Child.deleted_at.is_(None))
    if allowed is not None:
        cq = cq.filter(models.Child.centre_id.in_(allowed))
    total = cq.count()
    today = date.today()
    month_start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    mq = db.query(models.Measurement)
    if allowed is not None:
        mq = mq.join(models.Child, models.Measurement.child_id == models.Child.id).filter(models.Child.centre_id.in_(allowed))
    screened_ids = {r[0] for r in mq.filter(models.Measurement.measured_at >= month_start).with_entities(models.Measurement.child_id).all()}
    # flagged: latest measurement per child in scope is sam/mam
    latest_cls: dict[str, str | None] = {}
    latest_ts: dict[str, object] = {}
    for cid_, cls_, ts_ in mq.with_entities(models.Measurement.child_id, models.Measurement.classification, models.Measurement.measured_at).all():
        ats = _aware(ts_) if ts_ is not None else None
        if cid_ not in latest_cls or (ats is not None and (latest_ts.get(cid_) is None or ats > latest_ts[cid_])):  # type: ignore[operator]
            latest_cls[cid_] = cls_
            latest_ts[cid_] = ats
    flagged = sum(1 for v in latest_cls.values() if v in ("sam", "mam"))
    # overdue: child with no measurement in 30d or never
    cutoff = _now() - timedelta(days=30)
    recent_ids = {r[0] for r in mq.filter(models.Measurement.measured_at >= cutoff).with_entities(models.Measurement.child_id).all()}
    overdue = total - len(recent_ids & set(r[0] for r in cq.with_entities(models.Child.id).all())) if total else 0
    mism = mq.filter(models.Measurement.engine_mismatch.is_(True)).count()
    return {"total": total, "screened_this_month": len(screened_ids), "flagged": flagged,
            "overdue": overdue, "engine_mismatch": mism, "server_time": _now().isoformat()}


@router.get("/export/csv")
def export_csv(scope: str | None = None, from_: str | None = Query(default=None, alias="from"),
               to: str | None = Query(default=None),
               pr: Principal = Depends(get_current_principal),
               db: Session = Depends(get_db)):
    """Poshan column order (mobile csv_export.dart), RFC4180 quoting, scope+date filters."""
    from fastapi.responses import PlainTextResponse
    allowed = scope_centre_ids(pr, db, scope)
    q = db.query(models.Measurement, models.Child).join(models.Child, models.Measurement.child_id == models.Child.id)
    if allowed is not None:
        q = q.filter(models.Child.centre_id.in_(allowed))
    if from_:
        try:
            q = q.filter(models.Measurement.measured_at >= datetime.fromisoformat(from_))
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad from date")
    if to:
        try:
            q = q.filter(models.Measurement.measured_at <= datetime.fromisoformat(to))
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad to date")
    cols = ["icds_id", "child_name", "sex", "dob", "age_days", "measured_at", "weight_kg",
            "length_cm", "muac_cm", "oedema", "waz", "haz", "whz", "classification"]

    def esc(v: str) -> str:
        return f'"{v.replace(chr(34), chr(34)*2)}"' if any(c in v for c in (",", '"', "\n", "\r")) else v

    def fmt(v, div, d):
        return "" if v is None else f"{v / div:.{d}f}"

    lines = [",".join(cols)]
    for m, c in q.order_by(models.Measurement.measured_at).limit(5000).all():
        name = _dec(c.name_enc)
        if pr.role in ("supervisor", "cdpo"):
            name = _initials(name)
        dob = c.dob.isoformat() if hasattr(c.dob, "isoformat") else str(c.dob)
        cells = [c.icds_id or "", name, c.sex, dob, str(m.age_days),
                 m.measured_at.isoformat() if hasattr(m.measured_at, "isoformat") else str(m.measured_at),
                 fmt(m.weight_g, 1000, 2), fmt(m.length_mm, 10, 1), fmt(m.muac_mm, 10, 1),
                 "1" if m.oedema else "0",
                 "" if m.waz is None else f"{m.waz:.2f}",
                 "" if m.haz is None else f"{m.haz:.2f}",
                 "" if m.whz is None else f"{m.whz:.2f}", m.classification or ""]
        lines.append(",".join(esc(x) for x in cells))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv")


@router.get("/data-quality")
def data_quality(scope: str | None = None, pr: Principal = Depends(get_current_principal),
                 db: Session = Depends(get_db)):
    """Mismatches, implausible/out-of-range flags, stale devices."""
    allowed = scope_centre_ids(pr, db, scope)
    mq = db.query(models.Measurement)
    if allowed is not None:
        mq = mq.join(models.Child, models.Measurement.child_id == models.Child.id).filter(models.Child.centre_id.in_(allowed))
    mism = [{"id": m.id, "child_id": m.child_id, "classification": m.classification}
            for m in mq.filter(models.Measurement.engine_mismatch.is_(True)).limit(200).all()]
    implaus, oor = [], []
    for m in mq.limit(2000).all():
        fl = (m.flags or {}).get("flags", []) if isinstance(m.flags, dict) else []
        if any("implausible" in f for f in fl):
            implaus.append({"id": m.id, "flags": fl})
        if any("out_of_range" in f for f in fl):
            oor.append({"id": m.id, "flags": fl})
    stale_cut = _now() - timedelta(days=14)
    stale = []
    if allowed is None:
        devq = db.query(models.Device)
    else:
        devq = db.query(models.Device).filter(models.Device.centre_id.in_(allowed))
    for d in devq.all():
        last = db.query(models.SyncLog).filter_by(device_id=d.id).order_by(models.SyncLog.received_at.desc()).first()
        last_ts = _aware(last.received_at) if last else None
        if last is None or (last_ts and last_ts < stale_cut):
            stale.append({"serial": d.serial, "last_sync": last.received_at.isoformat() if last else None})
    return {"engine_mismatch": mism, "implausible": implaus, "out_of_range": oor,
            "stale_devices": stale, "server_time": _now().isoformat()}


@router.get("/export/paired-csv")
def export_paired_csv(scope: str | None = None, pr: Principal = Depends(get_current_principal),
                      db: Session = Depends(get_db)):
    """Agreement-study dataset (C11): client vs server z-scores per measurement."""
    from fastapi.responses import PlainTextResponse
    allowed = scope_centre_ids(pr, db, scope)
    q = db.query(models.Measurement)
    if allowed is not None:
        q = q.join(models.Child, models.Measurement.child_id == models.Child.id).filter(models.Child.centre_id.in_(allowed))
    cols = ["measurement_id", "child_id", "age_days", "weight_g", "length_mm", "muac_mm",
            "waz_client", "waz_server", "haz_client", "haz_server", "whz_client", "whz_server",
            "mismatch", "classification", "engine_version", "app_version"]

    def num(v):
        return "" if v is None else f"{v:.4f}"

    lines = [",".join(cols)]
    for m in q.order_by(models.Measurement.measured_at).limit(5000).all():
        lines.append(",".join([m.id, m.child_id, str(m.age_days), str(m.weight_g or ""), str(m.length_mm or ""),
                               str(m.muac_mm or ""), num(m.client_waz), num(m.waz), num(m.client_haz),
                               num(m.haz), num(m.client_whz), num(m.whz),
                               "1" if m.engine_mismatch else "0", m.classification or "",
                               m.engine_version or "", m.app_version or ""]))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv")
