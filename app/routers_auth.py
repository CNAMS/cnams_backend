"""Auth routes — roadmap §3. Device register, token, refresh, revoke."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import get_db
from app.schemas import DeviceRegisterIn, DeviceRegisterOut, RefreshIn, RevokeIn, TokenIn, TokenOut
from app.security import (
    create_access_token,
    hash_pin,
    new_device_secret,
    new_refresh_token,
    sha256_hex,
    verify_pin,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/device/register", response_model=DeviceRegisterOut)
def device_register(body: DeviceRegisterIn, db: Session = Depends(get_db)):
    if body.enrolment_code != settings.admin_enrolment_code:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad enrolment code")
    existing = db.query(models.Device).filter_by(serial=body.serial).first()
    if existing and not existing.revoked:
        raise HTTPException(status.HTTP_409_CONFLICT, "serial already enrolled")
    secret = new_device_secret()
    dev = existing or models.Device(id=str(uuid.uuid4()), serial=body.serial)
    dev.centre_id = body.centre_id
    dev.firmware_version = body.firmware_version
    dev.device_key_hash = sha256_hex(secret)
    dev.revoked = False
    dev.status = "active"
    db.add(dev)
    db.commit()
    return DeviceRegisterOut(device_id=dev.id, device_secret=secret)


@router.post("/token", response_model=TokenOut)
def token(body: TokenIn, db: Session = Depends(get_db)):
    if not body.device_serial or not body.device_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device credential required")
    dev = db.query(models.Device).filter_by(serial=body.device_serial).first()
    if dev is None or dev.revoked or dev.device_key_hash != sha256_hex(body.device_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad device credential")
    if not body.worker_id or not body.pin:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "worker PIN required")
    worker = db.get(models.Worker, body.worker_id)
    if worker is None or not worker.active or not verify_pin(worker.pin_hash, body.pin):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad worker PIN")
    access = create_access_token(worker.id, worker.centre_id, worker.role, dev.id)
    fam = str(uuid.uuid4())
    rt = new_refresh_token()
    db.add(models.RefreshToken(
        worker_id=worker.id, device_id=dev.id, token_hash=sha256_hex(rt),
        family_id=fam, expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    ))
    db.commit()
    return TokenOut(access_token=access, refresh_token=rt)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    h = sha256_hex(body.refresh_token)
    row = db.query(models.RefreshToken).filter_by(token_hash=h).first()
    exp = row.expires_at if row else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if row is None or row.revoked or (exp is not None and exp < datetime.now(timezone.utc)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad refresh")
    if row.consumed:
        # reuse = clone -> revoke whole family
        db.query(models.RefreshToken).filter_by(family_id=row.family_id).update({"revoked": True})
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh reuse detected")
    row.consumed = True
    worker = db.get(models.Worker, row.worker_id)
    dev = db.get(models.Device, row.device_id)
    if worker is None or not worker.active or dev is None or dev.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "revoked")
    access = create_access_token(worker.id, worker.centre_id, worker.role, dev.id)
    rt = new_refresh_token()
    db.add(models.RefreshToken(
        worker_id=worker.id, device_id=dev.id, token_hash=sha256_hex(rt),
        family_id=row.family_id, expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    ))
    db.commit()
    return TokenOut(access_token=access, refresh_token=rt)


@router.post("/revoke")
def revoke(body: RevokeIn, db: Session = Depends(get_db)):
    q = db.query(models.Device)
    dev = None
    if body.device_id:
        dev = db.get(models.Device, body.device_id)
    elif body.device_serial:
        dev = q.filter_by(serial=body.device_serial).first()
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    dev.revoked = True
    db.query(models.RefreshToken).filter_by(device_id=dev.id).update({"revoked": True})
    db.commit()
    return {"revoked": True}
