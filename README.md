# DriveDoc Control

Cloud-based **Project Folder Management + Document Control + Google Drive Monitoring** platform.

DriveDoc connects a Google Drive folder to a project and continuously monitors it.
Every change (create, modify, move, rename, trash, restore) is detected, recorded as an
**activity**, surfaced as a **notification**, and summarized in **reports** (with
CSV / Excel / PDF export) — while **Google Drive remains the single source of truth**
for files.

```
Google Drive  ──▶  Monitor (Drive API changes feed)  ──▶  activities + notifications
       │                                                       │
       └──▶  Explorer / mutations (create, upload, move…)      └──▶ reports / export
```

## Feature map

| Area | What you get |
|---|---|
| Projects | Connect folders, assign roles, soft-delete, color-coding |
| Dashboard | Stats, sync status + manual "Sync now", recent activity, members |
| Explorer | Unify view of folders/files for team-wide document control |
| Mutations | Create folder, upload, rename, move, trash — executed on the ORIGINAL Drive with the acting user's token |
| Activity | Filterable, searchable timeline with per-project path + actor |
| Notifications | One per monitoring activity per member, read/unread |
| Reports | Overview metrics, activity by day/user/action, files by type/folder, recent files, CSV/XLSX/PDF export |
| Admin | User roles & activation, audit log, monitor worker status |
| Settings | Theme (dark/light), Google connection info |

## Quick start

```bash
# 1. Python 3.12+ (recommend 3.12/3.13)
py -m venv .venv
.\.venv\Scripts\activate
pip install -r backend/requirements.txt

# 2. (Optional) configure .env — see .env.example; needed for real Google login

# 3. Run
py run.py                     # or: uvicorn app.main:app --app-dir backend --port 8000
```

Open http://localhost:8000 — API docs at http://localhost:8000/api/docs.

Without Google OAuth credentials the app starts and is fully testable; sign-in requires
a Google Cloud OAuth client (see `docs/GOOGLE_OAUTH_SETUP.md`).

## Tests

```bash
py -m pytest backend/tests -q     # 41 tests, uses an in-memory fake Drive
```

## Repo layout

```
backend/
  app/
    main.py            FastAPI app, monitor thread, error handlers, static mount
    models.py          SQLAlchemy schema (12+ tables)
    routers/           auth, projects, explorer, activities, notifications,
                       reports, search, admin, settings, webhooks
    services/          google/oauth.py, google/drive.py, sync, monitor, explorer,
                       report_service, export, notification, audit
    deps.py, security.py, schemas.py, errors.py, config.py, database.py
  tests/               pytest suite + FakeDrive double
frontend/              Vanilla-JS SPA served by the API (no build step)
database/schema.sql    Explicit PostgreSQL schema
docs/                  Architecture, setup, OAuth, DB, API, deployment, decisions
docker-compose.yml     app + PostgreSQL for production-ish local runs
```

## Documentation

- `docs/ARCHITECTURE.md` — system design, flow, monitoring model
- `docs/SETUP.md` — environment setup, config reference
- `docs/GOOGLE_OAUTH_SETUP.md` — Google Cloud OAuth client creation
- `docs/DATABASE.md` — schema and data flow
- `docs/API.md` — endpoint reference
- `docs/DEPLOYMENT.md` — Docker, PostgreSQL, production checklist
- `docs/DECISIONS.md` — technology decisions & trade-offs