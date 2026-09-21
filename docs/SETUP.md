# Setup

## Requirements

- Python 3.12+ (3.13/3.14 fine). `py` launcher is used in examples because the default
  `python` on some Windows machines is older; any is fine as long as you stay on one.
- Optional: PostgreSQL 16 (only if you want PostgreSQL locally; SQLite works out of the box).
- Optional: Docker Desktop for `docker-compose.yml`.

## 1. Install dependencies

```bash
py -m pip install -r backend/requirements.txt
```

## 2. Configure environment

```bash
copy .env.example .env     # Windows
# cp .env.example .env     # macOS/Linux
```

Edit `.env` (see `docs/DECISIONS.md` and `docs/GOOGLE_OAUTH_SETUP.md` for details):

| Variable | Meaning | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL. PostgreSQL + psycopg2, or SQLite | `sqlite:///<repo>/backend/data/app.db` |
| `SECRET_KEY` | Signing/encryption secret. Empty → auto-generated & persisted to `backend/data/secret.key` | auto |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth credentials (required for real Google sign-in) | empty |
| `OAUTH_REDIRECT_URI` | Must match the authorized redirect URI in Google Cloud | `http://localhost:8000/api/auth/callback` |
| `GOOGLE_SCOPES` | Space-separated scopes | `openid email profile https://www.googleapis.com/auth/drive` |
| `MONITOR_ENABLED` | Start the background monitor thread | `true` |
| `SYNC_INTERVAL_SECONDS` | Poll cadence | `60` |
| `SESSION_COOKIE_SECURE` | Set `true` behind HTTPS | `false` |
| `SESSION_COOKIE_SAMESITE` | `lax` (default) or `strict`/`none` | `lax` |
| `WEBHOOK_BASE_URL` | Public HTTPS base for Drive push channels | empty (polling only) |

## 3. Run

```bash
# from the repo root
py run.py

# or explicitly
py -m uvicorn app.main:app --app-dir backend --port 8000
```

- App: http://localhost:8000
- Interactive API docs: http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health

The database (SQLite) and tables are created automatically on first start.

## 4. First user

With Google OAuth configured, sign in with a Google account. Every new user starts as
`VIEWER`. There is no public registration endpoint by design.

**Bootstrap an admin from the console:**

```bash
py -c "from app.main import app; from app.database import SessionLocal; from app.models import User; db=SessionLocal(); db.query(User).filter(User.email=='you@example.com').update({'role':'ADMIN'}); db.commit()"
```

Run this from the `backend/` directory so `app.*` imports resolve.

## 5. Test

```bash
cd backend
py -m pytest tests -q
```

## Troubleshooting

- **`ModuleNotFoundError: app`** — run uvicorn with `--app-dir backend`, or run from the
  `backend/` directory.
- **OAuth redirect blocked** — confirm the exact URI (scheme, host, port, path) is listed
  in Google Cloud as an Authorized redirect URI.
- **Sessions drop on every reload** — if using `SameSite=strict` the SPA flow breaks; use
  `lax`. If behind HTTPS set `SESSION_COOKIE_SECURE=true` (browsers discard secure cookies
  over plain HTTP).