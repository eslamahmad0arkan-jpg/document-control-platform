# Deployment

## Docker (recommended for production-ish environments)

```bash
docker compose up -d --build
```

- Postgres 16 with `database/schema.sql` auto-applied on first boot.
- API on `:8000` with `MONITOR_ENABLED=true`.
- `.env` values flow through compose (see `SECRET_KEY`, `GOOGLE_CLIENT_*`).
- `backend/data` is mounted for the generated secret key + SQLite fallback.
- Behind a TLS reverse proxy set `SESSION_COOKIE_SECURE=true` and
  `SESSION_COOKIE_SAMESITE=lax`.

## Bare metal

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL=postgresql+psycopg2://drivedoc:PASSWORD@HOST:5432/drivedoc
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
export GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...
export OAUTH_REDIRECT_URI=https://your.domain/api/auth/callback
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --workers 1
```

> **One worker / one monitor.** The background monitor thread runs once per process.
> If you scale web workers, run the monitor in a **separate worker/process** (set
> `MONITOR_ENABLED` appropriately: sidecar process = `true`, API replicas = `false`),
> or move sync onto a task queue. The sync-stats endpoints still reflect whichever
> process owns it.

## Production checklist

- [ ] PostgreSQL with a dedicated user and rotated password; never commit `DATABASE_URL`.
- [ ] `SECRET_KEY` set to a long random value (not the auto-generated dev key).
- [ ] HTTPS everywhere; `SESSION_COOKIE_SECURE=true`.
- [ ] Google redirect URI updated to the real public host; app verification or Workspace
      "internal app" status if the Drive scope warning matters.
- [ ] `WEBHOOK_BASE_URL=https://your.domain` for push channels (optional; polling works
      without it).
- [ ] Backups of PostgreSQL; consider also backing up the secret key (token encryption
      becomes unrecoverable if lost).
- [ ] Capacity: the monitor fetches full metadata per changed id per cycle. Large
      project roots (10k+ files) benefit from raising `SYNC_INTERVAL_SECONDS` or using
      push channels to reduce polling cost.
- [ ] Logs: uvicorn access logs + `audit_logs` give an app-level trail; forward to your
      log aggregator.

## Scale / hardening notes

- Token encryption: Fernet key derived from `SECRET_KEY`; rotate by re-encrypting
  `google_tokens`.
- Rate limits: the Drive client retries with exponential backoff on 429/5xx and keeps a
  per-project token-bucket-style delay (`SYNC_DELAY_BETWEEN_REQUESTS_MS`).
- If a user's Drive access is revoked, sync degrades gracefully (per-project
  `last_error` + `consecutive_errors`) without crashing the loop.