"""Pydantic v2 schemas — /v1 JSON, RFC3339 UTC."""
from datetime import date, datetime

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: str
    db: str
    time: datetime


# --- auth ---
class DeviceRegisterIn(BaseModel):
    enrolment_code: str
    serial: str
    centre_id: str | None = None
    firmware_version: str | None = None


class DeviceRegisterOut(BaseModel):
    device_id: str
    device_secret: str


class TokenIn(BaseModel):
    worker_id: str | None = None
    pin: str | None = None
    # compat: allow centre login sketch
    centre_id: str | None = None
    device_serial: str | None = None
    device_secret: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class RevokeIn(BaseModel):
    device_serial: str | None = None
    device_id: str | None = None


# --- sync/batch (canonical spec + mobile compat) ---
class SyncRecordIn(BaseModel):
    entity: str = "measurement"
    op: str = "upsert"
    # canonical id / compat entity_id
    id: str | None = None
    entity_id: str | None = None
    updated_at: datetime | None = None
    payload: dict = Field(default_factory=dict)

    def record_id(self) -> str:
        rid = self.id or self.entity_id
        if not rid:
            raise ValueError("record id required (id|entity_id)")
        return rid


class SyncBatchIn(BaseModel):
    device_serial: str | None = None
    records: list[SyncRecordIn] = Field(max_length=50)


class RecordResult(BaseModel):
    id: str
    entity_id: str | None = None
    status: str  # accepted|duplicate|conflict|rejected
    code: int = 200  # compat int for mobile statusFromCode
    server_updated_at: datetime | None = None
    error: str | None = None


class SyncBatchOut(BaseModel):
    results: list[RecordResult]
    server_time: datetime


# --- children ---
class ChildUpsert(BaseModel):
    id: str
    centre_id: str
    icds_id: str | None = None
    name: str  # plaintext in, encrypted at rest
    sex: str  # M|F
    dob: date
    dob_precision: str = "exact"
    consent_status: str = "given"
    consent_form_ref: str | None = None
    updated_at: datetime | None = None


class ChildOut(BaseModel):
    id: str
    centre_id: str
    icds_id: str | None
    name: str  # decrypted for own-centre/admin, initials otherwise (handled in route)
    sex: str
    dob: date
    updated_at: datetime


# --- measurements ---
class MeasurementUpsert(BaseModel):
    id: str
    child_id: str
    measured_at: datetime
    age_days: int
    weight_g: int | None = None
    length_mm: int | None = None
    muac_mm: int | None = None
    position: str | None = None
    oedema: bool = False
    source: str = "device"
    client_waz: float | None = None
    client_haz: float | None = None
    client_whz: float | None = None
    app_version: str = "0.1.0"
    updated_at: datetime | None = None


# --- referrals ---
class ReferralOutcomeIn(BaseModel):
    outcome: str  # attended|not_attended|unknown
