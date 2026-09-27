# growth-monitor-api (Track C)

FastAPI backend per `03-Backend-API-Roadmap.md`. Base `/v1`, JSON, RFC3339 UTC.

## Quickstart (local, SQLite)
```bash
cp .env.example .env   # set JWT_SECRET (32+), ADMIN_ENROLMENT_CODE
pip install -r requirements.txt
alembic upgrade head
python seed.py
uvicorn app.main:app --reload --port 8000
# GET http://localhost:8000/health
```

## Prod (Postgres)
```bash
export DATABASE_URL=postgresql+psycopg://cnams:pw@db:5432/cnams
docker compose up --build
alembic upgrade head
```

## Auth flow (§3)
1. `POST /v1/auth/device/register {enrolment_code, serial}` -> `{device_id, device_secret}`
2. `POST /v1/auth/token {worker_id, pin, device_serial, device_secret}` -> `{access_token, refresh_token}`
3. `POST /v1/auth/refresh`, `POST /v1/auth/revoke`
Access 15m, refresh 30d rotated; reuse revokes family.

## Sync (§3)
`POST /v1/sync/batch` — max 50 records / 256KB, per-record `accepted|duplicate|conflict|rejected`, HTTP 200 always. LWW on `updated_at`, loser -> `measurement_audit`. Accepts both `id` and mobile-compat `entity_id`, returns `status` string + `code` int + `server_time`.

## Dashboard / export / data-quality
`GET /v1/dashboard/summary?scope=` → `total, screened_this_month, flagged (latest sam/mam), overdue (no measurement 30d), engine_mismatch`.
`GET /v1/export/csv?scope=&from=&to=` → Poshan column order (mobile `csv_export.dart`), RFC4180 quoting, PII initials for supervisor/CDPO.
`GET /v1/data-quality?scope=` → `engine_mismatch, implausible, out_of_range, stale_devices (>14d)`.

## Privacy / hardening (C8–C9)
Names encrypted at rest (Fernet `NAME_ENC_KEY`, dev `plain:` marker + legacy raw readable). `DELETE /children/{id}` purges PII, keeps shell. `python -m app.retention` purges expired refresh + 90d `sync_log`. Rate limits 20/min auth, 200/min other (429 + Retry-After). JSON access logs, no PII. Error taxonomy: `validation_error` 422, `rate_limited` 429, per-record never 500.

## Tests
```bash
JWT_SECRET=test-secret-min-32-chars-0123456789 ADMIN_ENROLMENT_CODE=ADMIN-DEV-001 DATABASE_URL="sqlite:///./test.db" python -m pytest tests/ -v
```
4 tests: health, auth guard, register/token/refresh/reuse, sync idempotency.

## Notes
- Secrets env-only, fail-fast. Logs never include names (record UUID only).
- `engine_mismatch` live via real LMS port; official WHO `tables.json` still placeholder → fail-safe `indeterminate` until Gate G2 drop-in.
- Dhara (`cnams_dhara`) will replace LWW sync with lattice adapter in Phase 4 — this service keeps thin `schema_binding/routes/review_queue` boundary.

## Ops
See `RUNBOOK.md` (redeploy, TLS, backups, alerts, cost ≈ ₹8–11k/yr). Scripts: `scripts/backup.sh` (nightly, 30d retention), `scripts/restore.sh` (quarterly drill). Study export: `GET /v1/export/paired-csv` (client-vs-server z per measurement).
