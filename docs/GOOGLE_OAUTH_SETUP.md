# Google OAuth setup

DriveDoc uses **Google sign-in with workspace authorization** so mutations are executed
with each user's own permissions.

## Create the OAuth client

1. Go to https://console.cloud.google.com/apis/credentials
2. **Create Credentials â†’ OAuth client ID** â†’ Web application.
3. Authorized JavaScript origins: `http://localhost:8000`
4. Authorized redirect URIs: `http://localhost:8000/api/auth/callback`
   (use your real public base URL in production instead).
5. Copy the client ID and secret into `.env`:

```
GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=YOUR_CLIENT_SECRET
OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/callback
```

Default scopes (`.env`):

```
GOOGLE_SCOPES=openid email profile https://www.googleapis.com/auth/drive
```

`https://www.googleapis.com/auth/drive` is the full Drive API scope. It lets the app
read metadata and perform mutations **on files the user owns or has been granted access
to** via your OAuth-consented account. You can tighten it later (e.g. `.readonly`) â€” but
then create/upload/move buttons will fail, which is expected.

## Sensitive scope / unverified app notice

`https://www.googleapis.com/auth/drive` is a sensitive scope. For personal use that is
fine; the consent screen will warn about an unverified app. To remove the warning for a
public product, complete Google's **OAuth app verification** process
(https://support.google.com/cloud/answer/9110914).

If the app is "internal" (Google Workspace), no verification is needed at all â€” internal
apps are exempt.

## How the flow works

1. SPA calls `GET /api/auth/login` â†’ server stores a random `state` in a short-lived
   cookie and returns the Google authorization URL.
2. User consents â†’ Google redirects to `/api/auth/callback?code=â€¦&state=â€¦`.
3. Server verifies `state`, exchanges the code, fetches profile, creates/updates the
   `User`, encrypts tokens (`google_tokens.access_token/refresh_token/id_token` with
   Fernet from `SECRET_KEY`), sets the session cookie, 302 â†’ `/`.
4. `GET /api/auth/me` round-trips identity + unread count; SPA renders the shell.

## Notes

- Only one token row per user/account; re-auth overwrites.
- Offline refresh tokens require `access_type=offline` + `prompt=consent` (the server
  does this) so the background monitor can refresh without user presence.
- `GET /api/auth/connection` shows the stored account and scopes; `POST
  /api/auth/logout` rotates the session cookie (Google tokens are not revoked, so the
  reverse refresh only occurs if the app uses them).
