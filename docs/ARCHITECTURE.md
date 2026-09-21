# Architecture

## Principles

1. **Google Drive is the single source of truth.** The database stores only a metadata
   snapshot (name, path, size, times, actor) plus application state. No binary copies.
2. **Mutations always happen on the original Drive** with the *acting user's* Google
   token, so Google's own permissions are enforced — DriveDoc can show you a viewer of
   a shared folder, but can never let you edit what Google forbids.
3. **Detection is diff-based and cursor-checkpointed.** The monitor compares the new
   Drive state to the stored snapshot and infers actions. The change-feed cursor is
   committed atomically with the recorded activities, so nothing is lost or repeated.
4. **App roles never exceed Google permissions.** `ADMIN / PROJECT_MANAGER / EDITOR /
   VIEWER` decide what the *UI* allows; Drive decides what is actually possible.

## Runtime components

```
                    ┌─────────────────────────────┐
                    │  FastAPI (uvicorn)          │
                    │  app.main:app               │
                    │  - REST routers (/api/...)  │
                    │  - static SPA mount (/)     │
                    └──────────┬──────────────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        │ auth+oauth           │ sync/monitor        │ explorer operations
        └──────────────────────┼─────────────────────│────┘
                               │                     │
                    ┌──────────▼──────────┐  ┌───────▼────────┐
                    │ PostgreSQL/SQLite   │  │ Google Drive   │
                    │ (SQLAlchemy)        │  │ API v3         │
                    └─────────────────────┘  └────────────────┘
```

### Process model

- **Web process** serves the API and the SPA.
- **Monitor background thread** (one per process; started in FastAPI lifespan when
  `MONITOR_ENABLED=true`). Each loop iterates projects, runs `incremental_sync`
  (or `full_scan` for first sync / cursor loss), then sleeps `SYNC_INTERVAL_SECONDS`.
  Errors are back-off limited and recorded in `sync_states`.

## Monitoring model

### Polling (always on)

`changes.list` with `pageToken` scoped to the project root gives a compact list of
changed `fileId`s. For each id the full `files.get` (metadata only) is fetched and the
snapshot is reconciled:

| Old (snapshot) | New (Drive) | Inferred action |
|---|---|---|
| — (missing) | file | `CREATED` |
| file | file (mtime/checksum changed) | `MODIFIED` |
| file | file (name changed) | `RENAMED` |
| file | file (parent changed) | `MOVED` |
| file | trashed | `TRASHED` |
| trashed | untrashed | `UNTRASHED` |
| — (missing) | folder | `CREATE_FOLDER` |

Nodes are upserted, and each recorded activity carries the **file/folder path at the
time of detection** so reports show where things were.

### Push channels (optional)

When `WEBHOOK_BASE_URL` is set, the monitor registers/renews Drive `watch` channels on
project roots (`POST /api/webhooks/drive` is the notification endpoint, keyed by
`X-Goog-Channel-ID`). Channels expire after ~1 hour and are renewed every loop, so it is
purely an acceleration layer; polling is the correctness layer.

**Workspace Events API** was considered and deliberately *not* required: it needs a
Google Workspace admin to grant domain-wide permission, which most teams won't have.
The change-feed approach works with plain OAuth consent.

## Security model

- Sessions: signed JWT in an `HttpOnly` cookie (`drivedoc_session`), SameSite + Secure
  configurable. Arbitrary `role` claims are rejected (role always read from DB).
- Google tokens encrypted at rest (Fernet, key derived from `SECRET_KEY`).
- OAuth state is bound to a short-lived cookie; reused authorization codes are rejected.
- Endpoint access: `get_project_for_user` resolves project + membership on every
  request → IDOR is structurally prevented (project `id` is never used as a trust
  boundary).
- Mutations check the UI role via `_can_edit`; Drive still enforces its own
  permissions, and Drive-side 403s are surfaced as friendly errors.
- Admin routes are guarded by `require_admin`.

## Error handling

FastAPI `exception_handler`s map `AppError` subclasses to their HTTP status
(e.g. `AuthError`→401, `ForbiddenError`→403, `NotFoundError`→404, `ValidationError_`→422,
`ConflictError`→409, `GoogleConnectionError`→502). Uncaught exceptions → JSON 500 +
server log. The SPA shows the `error.message` in a toast/error view.

## Frontend

Vanilla ES-module SPA (no framework, no build step) served by the static mount:

- `js/api.js` — fetch wrapper; throws `ApiError`, dispatches `app:unauthorized` on 401.
- `js/main.js` — bootstrap, hash router, shell (sidebar/topbar), theme, notification badge.
- `js/ui.js`, `js/charts.js` — DOM helpers, forms/modals/toasts, SVG charts (no chart lib).
- `js/pages/` — login, projects, project-dashboard, explorer, activities, notifications,
  reports, admin, settings.

The router is a tiny hash-based matcher (`#/projects/:id/explorer?folder=…`). Server-side
it stays behind `/api/**`; the SPA never trusts an unauthenticated render path because
every data call carries the session cookie.

## Request flow (example: create-project)

```
SPA → POST /api/projects {name, google_folder_id}
  → deps.get_current_user (JWT cookie → db user)
  → get_project_for_user (must exist), require_admin (ADMIN or PROJECT_MANAGER)
  → drive.get_file(google_folder_id)   [validates it's a folder]
  → db: create Project + membership (creator = PROJECT_MANAGER)
  → record_audit
  → response ProjectDetail
```