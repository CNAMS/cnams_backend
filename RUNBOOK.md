# Runbook — growth-monitor-api (C13 handover)

Single-command redeploy, ops, and cost. Another person deploys from this doc alone.

## 1. Single-command redeploy
```bash
cp .env.example .env   # fill JWT_SECRET (32+), ADMIN_ENROLMENT_CODE, POSTGRES_PASSWORD, NAME_ENC_KEY
docker compose up --build -d
docker compose exec api alembic upgrade head
curl -s http://localhost:8000/health   # {"status":"ok","db":"up"}
```
API docs: `http://localhost:8000/docs` (OpenAPI, published from code — no separate doc to drift).

Key generation:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"        # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # NAME_ENC_KEY
```

## 2. Migrations
```bash
alembic upgrade head        # deploy (also runs automatically in compose entrypoint)
alembic downgrade base      # wipe dev only — never on prod
alembic revision --autogenerate -m "desc"   # schema change flow
```
Current head: `6966efc41cd1` (initial schema, roadmap §2 + `refresh_tokens`).

## 3. TLS (staging)
Terminate at reverse proxy (Caddy example, 2 lines):
```
api.example.in {
    reverse_proxy localhost:8000
}
```
Caddy provisions/renews certs automatically. App itself stays HTTP behind it. Phones must see a trusted cert or sync fails closed (outbox preserved, retried).

## 4. Backups (§7, C10)
Nightly cron on host:
```cron
0 2 * * * cd /srv/cnams_backend && BACKUP_DIR=/var/backups/cnams ./scripts/backup.sh
```
- Postgres: `pg_dump | gzip` per night; SQLite dev: file copy + gzip.
- 30-day retention enforced by the script. Copy `/var/backups/cnams` to object storage (e.g. `aws s3 sync`) for off-machine copies.
- Restore drill (quarterly, roadmap §6): `RESTORE_FILE=... ./scripts/restore.sh` prints per-table row counts; compare against pre-wipe counts. An untested backup is not a backup.

## 5. Monitoring & alerts
- Uptime: poll `GET /health` every minute; page when `db != up` twice in a row.
- Data alert: `GET /v1/data-quality` — page supervisor channel when `engine_mismatch` non-empty (means app/firmware defect in the field, FR-SRV-3). Review `implausible` / `stale_devices` weekly.
- Logs: JSON on stdout (`method/path/status/duration_ms` only — names never logged). Ship to any log aggregator.

## 6. Cost note (annual, ~3 centres, India region)
- 1 vCPU / 1 GB VPS (e.g. Hetzner CX22-class / DigitalOcean s-1vcpu-1gb, ap-south): ~₹6–9k/yr.
- Object storage for backups (<10 GB with 30d gzip dumps): ~₹1k/yr.
- Domain + DNS: ~₹1k/yr. TLS: free (Caddy/Let's Encrypt).
- **Total ≈ ₹8–11k/yr (~$100–130).** No per-seat, per-sync, or vendor fees.
- Sustainability: if the server is switched off, field work continues fully offline (outbox on phones, drift to dhara sessions later). Switching it back on needs only §1 + restore.

## 7. Custodian handover checklist
- [ ] `.env` secrets set from §1 (none in repo, ever)
- [ ] `docker compose up` green + `/health` ok from a phone on 2G
- [ ] Nightly backup cron active, one restore drill completed and row counts recorded
- [ ] Uptime monitor + mismatch alert wired
- [ ] WHO `tables.json` drop-in status known (currently placeholder → engine fail-safe `indeterminate`; Gate G2 needs official coefficients)
