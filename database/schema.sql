-- DriveDoc Control — PostgreSQL schema
-- This mirrors backend/app/models.py. The app also auto-creates tables via
-- SQLAlchemy (Base.metadata.create_all); this file is the explicit, reviewed
-- reference for production deployments and migrations.

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(320) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL DEFAULT '',
    picture         TEXT,
    google_sub      VARCHAR(191) UNIQUE,
    role            VARCHAR(32) NOT NULL DEFAULT 'VIEWER',
    theme           VARCHAR(16) NOT NULL DEFAULT 'light',
    password_hash   VARCHAR(255),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub);

CREATE TABLE IF NOT EXISTS google_accounts (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    google_sub  VARCHAR(191) NOT NULL UNIQUE,
    email       VARCHAR(320) NOT NULL,
    name        VARCHAR(255),
    picture     TEXT
);

CREATE TABLE IF NOT EXISTS google_tokens (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id    INTEGER NOT NULL REFERENCES google_accounts(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    id_token      TEXT,
    scope         TEXT,
    expires_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_token_user_account UNIQUE (user_id, account_id)
);
CREATE INDEX IF NOT EXISTS ix_google_tokens_user_id ON google_tokens (user_id);

CREATE TABLE IF NOT EXISTS projects (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    description       TEXT,
    google_folder_id  VARCHAR(191) NOT NULL UNIQUE,
    color             VARCHAR(16),
    status            VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_by        INTEGER NOT NULL REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_projects_name ON projects (name);

CREATE TABLE IF NOT EXISTS project_members (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        VARCHAR(32) NOT NULL DEFAULT 'VIEWER',
    added_by    INTEGER REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_member_project_user UNIQUE (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_project_members_project_id ON project_members (project_id);
CREATE INDEX IF NOT EXISTS ix_project_members_user_id ON project_members (user_id);

CREATE TABLE IF NOT EXISTS folders (
    id                SERIAL PRIMARY KEY,
    drive_id          VARCHAR(191) NOT NULL UNIQUE,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_folder_id  VARCHAR(191),
    db_parent_id      INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    name              VARCHAR(1024) NOT NULL,
    path              TEXT NOT NULL DEFAULT '',
    depth             INTEGER NOT NULL DEFAULT 0,
    created_time      TIMESTAMPTZ,
    modified_time     TIMESTAMPTZ,
    trashed           BOOLEAN NOT NULL DEFAULT FALSE,
    last_synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_folders_project_parent ON folders (project_id, parent_folder_id);
CREATE INDEX IF NOT EXISTS ix_folders_path ON folders (path);
CREATE INDEX IF NOT EXISTS ix_folders_drive_id ON folders (drive_id);
CREATE INDEX IF NOT EXISTS ix_folders_parent_folder_id ON folders (parent_folder_id);

CREATE TABLE IF NOT EXISTS files (
    id                SERIAL PRIMARY KEY,
    drive_id          VARCHAR(191) NOT NULL UNIQUE,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    folder_drive_id   VARCHAR(191),
    db_folder_id      INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    name              VARCHAR(1024) NOT NULL,
    mime_type         VARCHAR(255) NOT NULL DEFAULT '',
    extension         VARCHAR(64),
    size              INTEGER DEFAULT 0,
    path              TEXT NOT NULL DEFAULT '',
    drive_url         TEXT,
    created_time      TIMESTAMPTZ,
    modified_time     TIMESTAMPTZ,
    modified_by_email VARCHAR(320),
    modified_by_name  VARCHAR(255),
    trashed           BOOLEAN NOT NULL DEFAULT FALSE,
    capabilities      JSONB,
    checksum          VARCHAR(64),
    last_synced_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_files_project_folder ON files (project_id, folder_drive_id);
CREATE INDEX IF NOT EXISTS ix_files_path ON files (path);
CREATE INDEX IF NOT EXISTS ix_files_drive_id ON files (drive_id);
CREATE INDEX IF NOT EXISTS ix_files_name ON files (name);
CREATE INDEX IF NOT EXISTS ix_files_extension ON files (extension);

CREATE TABLE IF NOT EXISTS activities (
    id            SERIAL PRIMARY KEY,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR(32) NOT NULL,
    target_type   VARCHAR(16) NOT NULL,
    target_name   VARCHAR(1024) NOT NULL,
    drive_id      VARCHAR(191),
    file_id       INTEGER REFERENCES files(id) ON DELETE SET NULL,
    folder_id     INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    file_path     TEXT,
    folder_path   TEXT,
    actor_email   VARCHAR(320),
    actor_name    VARCHAR(255),
    details       JSONB,
    source        VARCHAR(32) NOT NULL DEFAULT 'GOOGLE_CHANGES',
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    activity_key  VARCHAR(40) UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_activities_project_time ON activities (project_id, detected_at);
CREATE INDEX IF NOT EXISTS ix_activities_drive_id ON activities (drive_id);
CREATE INDEX IF NOT EXISTS ix_activities_action ON activities (action);

CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    activity_id  INTEGER REFERENCES activities(id) ON DELETE CASCADE,
    type         VARCHAR(32) NOT NULL,
    title        VARCHAR(255) NOT NULL,
    description  TEXT,
    link         TEXT,
    is_read      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_notification_user_activity UNIQUE (user_id, activity_id)
);
CREATE INDEX IF NOT EXISTS ix_notifications_user_read ON notifications (user_id, is_read);

CREATE TABLE IF NOT EXISTS audit_logs (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    action        VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32),
    resource_id   VARCHAR(191),
    details       JSONB,
    ip            VARCHAR(64),
    user_agent    VARCHAR(512),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs (created_at);

CREATE TABLE IF NOT EXISTS sync_states (
    id                   SERIAL PRIMARY KEY,
    project_id           INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    start_page_token     TEXT,
    last_cursor          TEXT,
    last_full_scan_at    TIMESTAMPTZ,
    last_incremental_at  TIMESTAMPTZ,
    scanning_now         BOOLEAN NOT NULL DEFAULT FALSE,
    scan_progress        INTEGER NOT NULL DEFAULT 0,
    scan_total           INTEGER NOT NULL DEFAULT 0,
    scan_done            INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    consecutive_errors   INTEGER NOT NULL DEFAULT 0,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sync_project UNIQUE (project_id)
);

CREATE TABLE IF NOT EXISTS watch_channels (
    id               SERIAL PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    channel_id       VARCHAR(191) NOT NULL UNIQUE,
    resource_id      VARCHAR(191),
    resource_kind    VARCHAR(64),
    address          TEXT,
    state            VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    expiration       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_renewed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_watch_channels_project_id ON watch_channels (project_id);

CREATE TABLE IF NOT EXISTS settings (
    key         VARCHAR(128) PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Document control: file locks & comments
CREATE TABLE IF NOT EXISTS file_locks (
    id                 SERIAL PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drive_id           VARCHAR(191) NOT NULL,
    file_id            INTEGER REFERENCES files(id) ON DELETE CASCADE,
    locked_by_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    comment            TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at        TIMESTAMPTZ,
    released_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_file_locks_project_id ON file_locks (project_id);
CREATE INDEX IF NOT EXISTS ix_file_locks_drive_id   ON file_locks (drive_id);
CREATE INDEX IF NOT EXISTS ix_file_locks_active     ON file_locks (drive_id, released_at);

CREATE TABLE IF NOT EXISTS file_comments (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drive_id    VARCHAR(191) NOT NULL DEFAULT '',
    file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES file_comments(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,
    mentions    JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_file_comments_file   ON file_comments (project_id, drive_id, created_at);
CREATE INDEX IF NOT EXISTS ix_file_comments_parent ON file_comments (parent_id);

CREATE TABLE IF NOT EXISTS file_comment_attachments (
    id           SERIAL PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    comment_id   INTEGER NOT NULL REFERENCES file_comments(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename     VARCHAR(255) NOT NULL,
    stored_path  VARCHAR(512) NOT NULL,
    size         INTEGER NOT NULL,
    content_type VARCHAR(191),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_comment_attach_comment ON file_comment_attachments (comment_id);
CREATE INDEX IF NOT EXISTS ix_comment_attach_project ON file_comment_attachments (project_id);

CREATE TABLE IF NOT EXISTS file_approvals (
    id                    SERIAL PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drive_id              VARCHAR(191) NOT NULL,
    file_id               INTEGER REFERENCES files(id) ON DELETE CASCADE,
    requested_by_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reviewer_user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status                VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    comment               TEXT,
    decided_by_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decided_comment       TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at            TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_file_approvals_state ON file_approvals (project_id, status);
CREATE INDEX IF NOT EXISTS ix_file_approvals_file  ON file_approvals (project_id, drive_id);
