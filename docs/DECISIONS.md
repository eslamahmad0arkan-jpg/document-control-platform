# Decisions

Record of technology and design decisions with the reasons and alternatives considered.

## 1. Backend: Python + FastAPI

- **Why:** fast to build, first-class async + OpenAPI docs (`/api/docs` for free),
  Pydantic validation, huge ecosystem (SQLAlchemy, google-api-client not even needed —
  Django + DRF also fine). Straightforward to run on the target dev box (Python 3.14).
- **Alternatives:** Node/Express (see #2), Laravel/PHP, Django+DRF, ASP.NET Core.
- **Trade-off:** one worker-monitor process must be respected at scale (#: DEPLOYMENT).

## 2. Frontend: vanilla JS ES-module SPA (no Node, no build step)

- **Why:** the environment has **no usable Node.js** (`winget install node` required
  elevation and hung), so a Vite/React pipeline was impossible to build or verify here.
  A vanilla module SPA with a hash router keeps the same UX (dashboard, explorer,
  charts) with zero toolchain, is served by FastAPI's static mount, and everything is
  testable through the same API.
- **Charts:** hand-rolled SVG (donut, bars) — no chart library needed, no bundle.
- **Trade-off:** loses React ergonomics/ecosystem; fine as long as the app stays at this
  scope. If the team later wants React, the API contract (`docs/API.md`) is unchanged.

## 3. Database: SQLite locally, PostgreSQL-ready everywhere else

- **Why:** the machine has **no PostgreSQL installed** and installing it required
  elevation. `models.py` uses only portable SQLAlchemy types; `DATABASE_URL` switches
  engine. `database/schema.sql` + Docker Compose provide production Postgres.
- **Trade-off:** SQLite is right for a single-user dev run; concurrency/features (JSONB,
  RLS) need Postgres — provided as first-class via Docker.

## 4. Google Drive as the source of truth; DB stores only a metadata snapshot

- File binaries are never copied. Mutations hit the **original Drive file** using the
  acting user's token. This avoids storage explosion and makes Google's permissions the
  real authorization layer.
- Trade-off: external changes outside any project root are invisible, and you need a
  Drive identity with access to each project root (or a shared service account).

## 5. Monitoring: Drive `changes.list` feed (polling) + optional push channels

- The change feed gives a compact per-project list of changed ids; full metadata is
  fetched per id. Diffing against the snapshot yields actions and paths.
- **Push:** Drive `watch` endpoints expire (~1 h) and need a public HTTPS
  `WEBHOOK_BASE_URL`; used only when configured. Polling always remains the correctness
  layer.
- **Workspace Events API rejected** as a requirement: it needs a Workspace admin +
  domain-wide delegation, beyond typical OAuth consent.
- **Alternative rejected:** Drive REST `list` full scans on every tick — too expensive
  for big roots; a full scan is used only on first sync or when the change-feed cursor
  is lost, and the changes feed drives steady-state monitoring.

## 6. Security model

- Sessions: signed JWT in `HttpOnly` cookie, SameSite configurable. Role always read
  from the DB — forged role claims are ignored.
- Google tokens encrypted at rest (Fernet key from `SECRET_KEY`).
- OAuth `state` bound to cookie; authorization-code reuse rejected.
- Project access resolved from membership on every request (`get_project_for_user`);
  IDs are never a trust boundary.
- App roles (ADMIN/MANAGER/EDITOR/VIEWER) limit UI actions; Drive enforces its own
  permission on every write.

## 7. Actional model & dedup

- Activities are keyed (`activity_key`) and the sync cursor is committed in the same
  transaction as the activity batch ⇒ replay-safe, at-most-once delivery.
- Actor = `lastModifyingUser` from Drive; fallback = the user who triggered a sync or
  the sync identity override (POST `/sync {actor}`).

## 8. Authentication: Google OAuth as the single login

- No username/password or self-registration in v1. Users appear when they sign in with
  Google; admins are bootstrapped in console (`docs/SETUP.md` step 4).
- The `password_hash` column stays reserved for a future email/password fallback or
  service accounts. Migrating users across Gmail ↔ Workspace would need them.

## 9. Reporting & export

- Reports are computed on demand (aggregation queries), not stored. Formats: CSV/XLSX
  (openpyxl)/PDF (reportlab) generated server-side; single dependency each.

## 10. Environment constraint decisions

- `py` launcher used for commands because default `python` = 3.13 while deps were
  installed into 3.14 (`py`).
- Tests use an in-memory `FakeDrive` double and run against a temp SQLite DB with
  `MONITOR_ENABLED=false`, so CI needs no Google credentials or network.
- Chart rendering is client-side SVG so headless test runs stay fast.