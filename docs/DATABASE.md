# Database

SQLAlchemy ORM with a portable schema. Storage is metadata + application state only;
**files are never copied**.

## Engines

- **Default (dev):** SQLite at `backend/data/app.db`, WAL journaling, foreign keys ON.
- **Production:** PostgreSQL via `DATABASE_URL=postgresql+psycopg2://…`.
  `database/schema.sql` documents the exact Postgres DDL; `docker-compose.yml` mounts it
  as init script.

## Tables

| Table | Purpose |
|---|---|
| `users` | App accounts (Google-authenticated). `role`: ADMIN / PROJECT_MANAGER / EDITOR / VIEWER |
| `google_accounts` | Linked Google identities |
| `google_tokens` | OAuth tokens **encrypted at rest** |
| `projects` | One per connected Drive folder (`google_folder_id` unique) |
| `project_members` | user × project with a project-level role |
| `folders` | Snapshot of Drive folders inside a project root (drive_id, parent, path, depth) |
| `files` | Snapshot of Drive files (drive_id, folder, name, type, size, path, times, `lastModifyingUser`, capabilities, checksum) |
| `activities` | Immutable log: who/what/when + path-at-detection + dedupe `activity_key` |
| `notifications` | Per-user notification rows linking to an activity |
| `audit_logs` | App actions (login, admin changes, mutations) for tracing |
| `sync_states` | Per-project sync cursor machine (`start_page_token`, `last_cursor`, progress, errors) |
| `watch_channels` | Push notification channels (single-tenant; state ACTIVE/EXPIRED/STOPPED) |
| `settings` | key/value app settings |

## Data flow

```
               monitor thread
    Drive changes.list ──────────▶ activities (dedupe key = action+drive_id+detected_at)
         │                              │
         ▼                              ▼
  folders/files snapshot        notifications (per member, configurable)
```

- `files.drive_id` is globally unique (Drive guarantees it), giving idempotent upserts.
- `folders.path`/`files.path` are materialized during sync (`A/B/file.txt`) so reports
  are fast and show the location at the time of the change.
- `files.checksum` = SHA-1 of `id:md5:modifiedTime` for cheap change detection.
- `activities.activity_key` unique → deduping replay-safe syncs.

## Integrity notes

- `projects` are soft-deleted (`deleted_at`) — real Drive files are never touched on
  project delete.
- `deleted_at` filters all reads; recursive deletes cascade child rows via FK.
- JSON columns carry per-file Drive capabilities / activity details (portable JSON,
  `JSONB` in Postgres).

## Concurrency

- `scanning_now` + `SYNC_INTERVAL_SECONDS` guard rails prevent overlapping scans.
- The monitor commits the cursor and the activity batch in the same transaction, so a
  crash mid-loop never loses a detected change (at-most-once, replay-idempotent).
- SQLite: WAL mode + busy_timeout for concurrent readers (test runs are serialized).