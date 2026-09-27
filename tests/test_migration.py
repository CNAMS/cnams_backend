"""Migration: upgrade idempotent on seeded DB; base->head restores schema (roadmap §6)."""
import os
import pathlib
import subprocess
import uuid

os.environ.setdefault("JWT_SECRET", "test-secret-min-32-chars-0123456789")
os.environ.setdefault("ADMIN_ENROLMENT_CODE", "ADMIN-DEV-001")

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(db: str, *args: str) -> str:
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}")
    out = subprocess.run(["alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr or out.stdout
    return out.stdout


def test_upgrade_idempotent_and_schema_restored(tmp_path):
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models

    db = str(tmp_path / "mig.db")
    _run(db, "upgrade", "head")
    eng = create_engine(f"sqlite:///{db}", future=True)
    S = sessionmaker(bind=eng, future=True)
    s = S()
    now = datetime.now(timezone.utc)
    b = models.Block(id=str(uuid.uuid4()), name="B", district="D", state="S")
    s.add(b)
    s.commit()
    cid = str(uuid.uuid4())
    s.add(models.Centre(id=cid, block_id=b.id, name="C", icds_code="M1", sector="S"))
    s.commit()
    s.close()
    # re-upgrade is a no-op, seed survives
    _run(db, "upgrade", "head")
    s = S()
    assert s.query(models.Block).count() == 1
    assert s.query(models.Centre).count() == 1
    s.close()
    eng.dispose()
    # full cycle restores schema and head revision
    _run(db, "downgrade", "base")
    _run(db, "upgrade", "head")
    eng = create_engine(f"sqlite:///{db}", future=True)
    S = sessionmaker(bind=eng, future=True)
    s = S()
    assert s.query(models.Block).count() >= 0  # schema exists
    s.close()
    eng.dispose()
    assert "6966efc41cd1" in _run(db, "current")
