# API reference

Base: `/api`. Interactive docs at `/api/docs` (Swagger UI). All endpoints require the
session cookie except the OAuth login/callback. Responses use this error envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Item not found.", "details": {} } }
```

## Auth

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/login` | `{url}` Google authorization URL (sets `oauth_state` cookie) |
| GET | `/api/auth/callback` | OAuth callback → sets session cookie, 302 → `/` |
| POST | `/api/auth/logout` | Clears session (audits `LOGOUT`) → 204 |
| GET | `/api/auth/me` | `{user, unread_notifications}` |
| GET | `/api/auth/connection` | Connected Google account + scopes |

## Projects

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects` | List `ProjectSummary[]` |
| POST | `/api/projects` | Create (ADMIN/PROJECT_MANAGER): `{name, google_folder_id, description?, color?}` → `ProjectDetail` |
| GET | `/api/projects/{id}` | Detail: `{...summary, role, members, sync, stats}` |
| PATCH | `/api/projects/{id}` | Update name/description/color/status → `ProjectDetail` |
| DELETE | `/api/projects/{id}` | Soft-delete → 204 |
| GET | `/api/projects/{id}/members` | `MemberOut[]` |
| POST | `/api/projects/{id}/members` | `{email, role}` (role: PROJECT_MANAGER/EDITOR/VIEWER) |
| PATCH | `/api/projects/{id}/members/{user_id}` | Change role → `MemberOut[]` |
| DELETE | `/api/projects/{id}/members/{user_id}` | Remove → `MemberOut[]` |
| GET | `/api/projects/{id}/sync` | `SyncStatusOut` |
| POST | `/api/projects/{id}/sync` | Trigger a sync now → `SyncStatusOut` |
| GET | `/api/projects/{id}/scan-progress` | `ScanProgress` (poll while scanning) |
| GET | `/api/projects/{id}/stats` | `ProjectStats` |

## Explorer

All under `/api/projects/{project_id}`. Mutations execute on the **original Drive**.

| Method | Path | Description |
|---|---|---|
| GET | `/explorer?folder_id=&q=` | Current folder listing; `q` = search within project |
| GET | `/explorer/item/{drive_id}` | Single node detail |
| POST | `/folders` | `{parent_id?, name}` → node (201) |
| POST | `/upload?folder_id=` | multipart upload → node (201) |
| POST | `/rename` | `{drive_id, name}` → node |
| POST | `/move` | `{drive_id, parent_id?}` → node |
| POST | `/trash` | `{drive_id}` → trashes in Drive |

response: `ExplorerResponse` = `{project_id, current, breadcrumbs, folders[], files[],
total_folders, total_files, search_active}`. Nodes: `ExplorerNode`.

## Activities

`/api/projects/{project_id}/activities`

- `GET ""` → `ActivityPage {items, total, page, per_page, filters}`
  Filters: `user_id, action, actor, date_from, date_to, file, folder, ext, q`; sort `sort`
  (detected_at/target_name/action/actor_name) + `order`.
- `GET /users` → distinct actors for filter dropdowns.

## Notifications

| Method | Path | Description |
|---|---|---|
| GET | `/api/notifications?page=&per_page=&sort=&order=` | `NotificationPage {items, total, unread, page, per_page}`; `unread=1` filters |
| GET | `/api/notifications/unread-count` | `{total}` |
| POST | `/api/notifications/{id}/read` | Mark read → `NotificationOut` |
| POST | `/api/notifications/read-all` | → 204 |

## Reports

`/api/projects/{project_id}/reports`

- `GET ""?date_from=&date_to=&days=` → `ReportsResponse`
  `{overview, activity:{by_day,by_user,by_action,by_folder}, files:{by_extension,by_folder,recent_files}}`
- `GET /export?format=csv|xlsx|pdf` → file download.

## Search

`GET /api/projects/{project_id}/search?q=` → `{items[], total, q}` — files, folders, activities.

## Admin (ADMIN only)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | `AdminUser[]` (includes projects_count) |
| PATCH | `/api/admin/users/{id}` | `{role?, is_active?}` |
| GET | `/api/admin/audit?page=&per_page=` | `AuditPage` |
| GET | `/api/admin/worker` | `WorkerStatus {running, interval_seconds, enabled, last_loop_at}` |

## Settings

| Method | Path | Description |
|---|---|---|
| GET | `/api/settings` | `{theme}` |
| PATCH | `/api/settings` | `{theme: "light"\|"dark"}` |

## Webhooks

| Method | Path | Description |
|---|---|---|
| POST | `/api/webhooks/drive` | Google Drive push notification receipt (validates `X-Goog-*`) |

## Health

`GET /api/health` → `{status:"ok", app, version}`