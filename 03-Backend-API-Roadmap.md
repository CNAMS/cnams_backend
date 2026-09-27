# Track C — Backend API Roadmap

**Owns:** `FR-SRV-1..10`, server-side mirror of `FR-ALG`
**Team:** 2 (backend + data)
**Repo:** `growth-monitor-api`
**Stack:** FastAPI (Python 3.11+) · Pydantic v2 · PostgreSQL 15 · SQLAlchemy 2.0 · Alembic · pytest

---

## 1. Responsibilities

The backend is deliberately *not* on the critical path of a measurement. Its jobs are:

1. Durable storage of the longitudinal record beyond the phone
2. **Independent recomputation** of every z-score as a correctness check on the app (`FR-SRV-3`)
3. Aggregation for the supervisor dashboard
4. Audit trail and consent-withdrawal deletion
5. Multi-device reconciliation when two phones cover the same centre

If the server is down for a week, field operations continue unaffected. Design to that.

---

## 2. Database schema

```sql
CREATE TABLE blocks (
  id UUID PRIMARY KEY, name TEXT NOT NULL, district TEXT NOT NULL, state TEXT NOT NULL
);

CREATE TABLE centres (
  id UUID PRIMARY KEY,
  block_id UUID NOT NULL REFERENCES blocks(id),
  name TEXT NOT NULL, icds_code TEXT UNIQUE, sector TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workers (
  id UUID PRIMARY KEY,
  centre_id UUID NOT NULL REFERENCES centres(id),
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('aww','supervisor','cdpo','admin')),
  pin_hash TEXT NOT NULL,              -- argon2
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  id UUID PRIMARY KEY,
  serial TEXT UNIQUE NOT NULL,
  centre_id UUID REFERENCES centres(id),
  firmware_version TEXT,
  last_calibrated_at DATE,
  cal_slope DOUBLE PRECISION, cal_offset DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE children (
  id UUID PRIMARY KEY,                 -- client-generated, accepted as-is
  centre_id UUID NOT NULL REFERENCES centres(id),
  icds_id TEXT,
  name_enc BYTEA NOT NULL,             -- encrypted at rest
  name_hash TEXT NOT NULL,             -- salted hash, for dedupe without exposing the name
  sex CHAR(1) NOT NULL CHECK (sex IN ('M','F')),
  dob DATE NOT NULL,
  dob_precision TEXT NOT NULL,
  consent_status TEXT NOT NULL,
  consent_form_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  deleted_at TIMESTAMPTZ,
  UNIQUE (centre_id, icds_id)
);

CREATE TABLE measurements (
  id UUID PRIMARY KEY,                 -- client-generated; this is the idempotency key
  child_id UUID NOT NULL REFERENCES children(id),
  worker_id UUID NOT NULL REFERENCES workers(id),
  device_id UUID REFERENCES devices(id),
  measured_at TIMESTAMPTZ NOT NULL,
  age_days INT NOT NULL,
  weight_g INT, length_mm INT, muac_mm INT,
  position TEXT, oedema BOOLEAN NOT NULL DEFAULT false,
  source TEXT NOT NULL,
  waz REAL, haz REAL, whz REAL, maz REAL,
  classification TEXT,
  client_waz REAL, client_haz REAL, client_whz REAL,   -- as submitted
  engine_mismatch BOOLEAN NOT NULL DEFAULT false,      -- FR-SRV-3
  flags JSONB NOT NULL DEFAULT '{}',
  engine_version TEXT NOT NULL, app_version TEXT NOT NULL,
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE measurement_audit (
  audit_id BIGSERIAL PRIMARY KEY,
  measurement_id UUID NOT NULL,
  version INT NOT NULL,
  snapshot JSONB NOT NULL,
  superseded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reason TEXT
);

CREATE TABLE referrals (
  id UUID PRIMARY KEY,
  measurement_id UUID NOT NULL REFERENCES measurements(id),
  referred_to TEXT, referred_at TIMESTAMPTZ NOT NULL,
  outcome TEXT NOT NULL DEFAULT 'pending', outcome_recorded_at TIMESTAMPTZ
);

CREATE TABLE sync_log (
  id BIGSERIAL PRIMARY KEY,
  device_id UUID, worker_id UUID,
  batch_size INT, accepted INT, rejected INT, conflicts INT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_meas_child_time ON measurements(child_id, measured_at DESC);
CREATE INDEX idx_meas_centre_time ON measurements(child_id) INCLUDE (classification);
CREATE INDEX idx_meas_mismatch ON measurements(engine_mismatch) WHERE engine_mismatch;
```

**Note on `measurement_audit`:** nothing is ever overwritten in place. On update, the prior row is snapshotted into the audit table and `version` increments. This is what makes `FR-SRV-4` true rather than aspirational, and it is the difference between a research-grade dataset and a spreadsheet.

---

## 3. API contract

Base: `/v1`. All responses JSON. All timestamps RFC 3339 UTC.

### Auth

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/device/register` | One-time enrolment with an admin-issued code; returns device credentials |
| POST | `/auth/token` | Worker PIN + device credential → access (15 min) + refresh (30 day) |
| POST | `/auth/refresh` | Rotate refresh token |
| POST | `/auth/revoke` | Revoke a lost device |

Access token is a JWT carrying `worker_id`, `centre_id`, `role`, `device_id`. Refresh tokens are rotated on every use and stored hashed; reuse of a consumed refresh token revokes the family (detects a cloned device).

### Core

| Method | Path | Purpose | Req |
|---|---|---|---|
| GET | `/bootstrap` | Centre roster, config, reference-table version, server time | FR-SRV-5 |
| POST | `/sync/batch` | Idempotent batch upsert | FR-SRV-2 |
| GET | `/children?centre_id=&since=` | Roster delta for multi-device centres | FR-SRV-9 |
| GET | `/children/{id}/measurements` | Longitudinal history | — |
| POST | `/referrals/{id}/outcome` | Record follow-up | FR-APP-9 |
| DELETE | `/children/{id}` | Consent withdrawal; hard-deletes PII, retains anonymised measurement shell | FR-SRV-8 |
| GET | `/dashboard/summary?scope=` | The four dashboard numbers | FR-SRV-6 |
| GET | `/export/csv?scope=&from=&to=` | Access-controlled export | FR-SRV-7 |
| GET | `/health` | Liveness + DB check | — |

### `POST /sync/batch`

```jsonc
// request
{
  "device_serial": "GM-003",
  "records": [
    {
      "entity": "measurement",
      "op": "upsert",
      "id": "9f1c...-uuid",           // idempotency key
      "updated_at": "2026-08-14T06:12:03Z",
      "payload": { /* full snapshot */ }
    }
  ]
}

// response — per record, never all-or-nothing
{
  "results": [
    { "id": "9f1c...", "status": "accepted" },
    { "id": "a22e...", "status": "duplicate" },
    { "id": "b31f...", "status": "conflict", "server_updated_at": "..." },
    { "id": "c40a...", "status": "rejected", "error": "age_days out of range" }
  ],
  "server_time": "2026-08-14T06:12:09Z"
}
```

Rules:
- Max 50 records or 256 KB per batch.
- Same `id` submitted twice → `duplicate`, no new row, HTTP 200. Never 500.
- Conflict → last-write-wins on `updated_at`, loser snapshotted to `measurement_audit`.
- Partial success is normal. One bad record must never fail the batch.
- On accept, the server recomputes z-scores from the raw measurements. Disagreement with `client_waz/haz/whz` beyond 0.01 sets `engine_mismatch = true` and raises an alert. **This is your early-warning system for a firmware or app defect in the field.**

---

## 4. Authorisation matrix (`FR-SRV-9`)

| Endpoint | AWW | Supervisor | CDPO | Admin |
|---|---|---|---|---|
| `POST /sync/batch` (own centre) | ✔ | — | — | ✔ |
| `GET /children` (own centre) | ✔ | ✔ sector | ✔ block | ✔ |
| Child name / PII | ✔ own centre | ✖ initials only | ✖ initials only | ✔ |
| `GET /dashboard/summary` | own centre | sector | block | all |
| `DELETE /children/{id}` | request only | ✔ | ✔ | ✔ |
| `GET /export/csv` | own centre | sector | block | all |

Enforce in a dependency, not in each handler. Write one test per cell of this table — an authorisation matrix with no tests is a claim, not a control.

---

## 5. Sprint plan

### Semester 1

| Weeks | Sprint | Deliverable | Exit |
|---|---|---|---|
| 1–2 | C0 Setup | FastAPI skeleton, Docker Compose (api + postgres), CI running pytest | `/health` green in CI |
| 3–4 | C1 Schema | Alembic migrations, SQLAlchemy models, seed script | Migrations up/down cleanly |
| 5–6 | C2 Auth | Device registration, JWT, refresh rotation, PIN hashing | Auth integration tests pass |
| 7–8 | **C3 Z-score (Python)** | Reference implementation + WHO Anthro validation harness | **Gate G2** with Track B |
| 9–10 | C4 Sync | `/sync/batch`, idempotency, conflict policy, audit table | Replay + conflict tests pass |
| 11–12 | C5 Aggregates | Dashboard summary queries | Correct against a seeded fixture |
| 13–14 | C6 Deploy | Staging deployment, TLS, backups, migration runbook | Reachable from a phone on 2G |
| 15–16 | C7 Review | API docs (OpenAPI), Sem 1 report | Docs published |

### Semester 2

| Weeks | Sprint | Deliverable | Exit |
|---|---|---|---|
| 1–2 | C8 Hardening | Rate limits, payload caps, structured logging with no PII, error taxonomy | Load test 100 rps |
| 3–4 | C9 Privacy | Field encryption for names, deletion endpoint, retention job | **Gate G4**; ethics review sign-off |
| 5–6 | C10 Ops | Nightly backup + restore drill, uptime monitor, alerting on `engine_mismatch` | Restore drill documented |
| 7–8 | C11 Study support | Export pipeline for the agreement study; analysis notebook scaffold | Clean paired dataset out |
| 9–12 | C12 Field response | Defect fixes; multi-device reconciliation at shared centres | Zero data-integrity defects |
| 13–14 | C13 Handover | Single-command redeploy, runbook, named custodian, cost note | Another person deploys it from the doc |

---

## 6. Testing

| Level | Coverage |
|---|---|
| Unit | Z-score engine (shared corpus with Dart), age computation, validators |
| Contract | Every endpoint against its OpenAPI schema |
| Integration | Full sync flow against a real Postgres in CI (testcontainers or a CI service) |
| Idempotency | Same batch submitted 3×, with interleaved conflicts |
| Authorisation | One test per cell of the matrix above |
| Migration | Up/down on a seeded database, no data loss |
| Restore | Quarterly: wipe staging, restore from backup, verify row counts |

Target: >85% line coverage on `core/` and 100% on the z-score module. Coverage elsewhere is not worth arguing about.

---

## 7. Operations and cost

- **Hosting:** small VPS or managed PaaS in an Indian region (`CON-4`). Sized for ~3 centres — 1 vCPU / 1 GB is ample.
- **Backups:** nightly `pg_dump` to object storage, 30-day retention, monthly restore drill. An untested backup is not a backup.
- **Secrets:** environment variables only, fail-fast on missing values at startup. No secret in the repo, no secret in a config file, ever.
- **Logging:** structured JSON; child names and IDs never logged. Log the record UUID, never the child.
- **Handover cost note:** state the annual running cost explicitly in the handover document, plus the fact that the system remains fully functional offline if the server is switched off (`NFR` sustainability).
