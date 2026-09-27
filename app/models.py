"""SQLAlchemy models — mirrors roadmap §2 DDL exactly."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    REAL,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base

UUIDStr = String(36)
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)


class Centre(Base):
    __tablename__ = "centres"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    block_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("blocks.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    icds_code: Mapped[str | None] = mapped_column(Text, unique=True)
    sector: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    centre_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("centres.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # aww|supervisor|cdpo|admin (CHECK in migration)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    serial: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    centre_id: Mapped[str | None] = mapped_column(UUIDStr, ForeignKey("centres.id"))
    firmware_version: Mapped[str | None] = mapped_column(Text)
    last_calibrated_at: Mapped[date | None] = mapped_column(Date)
    cal_slope: Mapped[float | None] = mapped_column(Double)
    cal_offset: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    # enrolment / credential (D-08: server-issued id bound to key)
    device_key_hash: Mapped[str | None] = mapped_column(Text)  # sha256 of device secret
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)  # client-generated
    centre_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("centres.id"), nullable=False)
    icds_id: Mapped[str | None] = mapped_column(Text)
    name_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    name_hash: Mapped[str] = mapped_column(Text, nullable=False)
    sex: Mapped[str] = mapped_column(String(1), nullable=False)  # M|F
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    dob_precision: Mapped[str] = mapped_column(Text, nullable=False)
    consent_status: Mapped[str] = mapped_column(Text, nullable=False)
    consent_form_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Measurement(Base):
    __tablename__ = "measurements"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)  # idempotency key
    child_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("children.id"), nullable=False)
    worker_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("workers.id"), nullable=False)
    device_id: Mapped[str | None] = mapped_column(UUIDStr, ForeignKey("devices.id"))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    age_days: Mapped[int] = mapped_column(nullable=False)
    weight_g: Mapped[int | None] = mapped_column()
    length_mm: Mapped[int | None] = mapped_column()
    muac_mm: Mapped[int | None] = mapped_column()
    position: Mapped[str | None] = mapped_column(Text)
    oedema: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    waz: Mapped[float | None] = mapped_column(REAL)
    haz: Mapped[float | None] = mapped_column(REAL)
    whz: Mapped[float | None] = mapped_column(REAL)
    maz: Mapped[float | None] = mapped_column(REAL)
    classification: Mapped[str | None] = mapped_column(Text)
    client_waz: Mapped[float | None] = mapped_column(REAL)
    client_haz: Mapped[float | None] = mapped_column(REAL)
    client_whz: Mapped[float | None] = mapped_column(REAL)
    engine_mismatch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flags: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_meas_child_time", "child_id", "measured_at"),
        Index("idx_meas_mismatch", "engine_mismatch"),
    )


class MeasurementAudit(Base):
    __tablename__ = "measurement_audit"
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(UUIDStr, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    superseded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    measurement_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("measurements.id"), nullable=False)
    referred_to: Mapped[str | None] = mapped_column(Text)
    referred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """Refresh family for rotation + reuse detection. Not in roadmap DDL but required by §3."""
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=_uuid)
    worker_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("workers.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("devices.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # sha256
    family_id: Mapped[str] = mapped_column(UUIDStr, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SyncLog(Base):
    __tablename__ = "sync_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(UUIDStr)
    worker_id: Mapped[str | None] = mapped_column(UUIDStr)
    batch_size: Mapped[int | None] = mapped_column()
    accepted: Mapped[int | None] = mapped_column()
    rejected: Mapped[int | None] = mapped_column()
    conflicts: Mapped[int | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
