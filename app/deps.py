"""AuthZ dependency — one place per roadmap §4. One test per cell."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)

ROLES = ("aww", "supervisor", "cdpo", "admin")


class Principal:
    def __init__(self, worker_id: str, centre_id: str, role: str, device_id: str):
        self.worker_id = worker_id
        self.centre_id = centre_id
        self.role = role
        self.device_id = device_id


def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        p = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    worker = db.get(models.Worker, p.get("worker_id"))
    device = db.get(models.Device, p.get("device_id"))
    if worker is None or not worker.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown worker")
    if device is None or device.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device revoked")
    return Principal(p["worker_id"], p["centre_id"], p["role"], p["device_id"])


def require_roles(*roles: str):
    def _check(pr: Principal = Depends(get_current_principal)) -> Principal:
        if pr.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return pr

    return _check


def scope_centre_ids(pr: Principal, db: Session, requested: str | None) -> list[str] | None:
    """Return allowed centre ids for dashboard/export/children scoping.
    None = all (admin). Roadmap §4 matrix."""
    if pr.role == "admin":
        return None
    if pr.role == "aww":
        # own centre only
        if requested and requested != pr.centre_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-centre denied")
        return [pr.centre_id]
    # supervisor (sector) / cdpo (block): simplified to own centre until
    # sector/block columns are populated; explicit 403 on foreign centre.
    worker = db.get(models.Worker, pr.worker_id)
    own_centre = db.get(models.Centre, worker.centre_id) if worker else None
    if requested:
        c = db.get(models.Centre, requested)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "centre not found")
        if pr.role == "supervisor" and own_centre and c.sector != own_centre.sector:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-sector denied")
        if pr.role == "cdpo":
            cb = db.get(models.Centre, worker.centre_id) if worker else None
            ob = db.get(models.Block, cb.block_id) if cb else None
            rb = db.get(models.Block, c.block_id) if c else None
            if ob is None or rb is None or ob.id != rb.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-block denied")
        return [requested]
    # no filter given: return own centre scope (supervisor/cdpo narrow later)
    return [pr.centre_id] if pr.centre_id else []
