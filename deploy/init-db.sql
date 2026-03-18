-- CorridorKey database initialization.
-- Runs automatically on first Postgres container start via
-- /docker-entrypoint-initdb.d/ mount.
--
-- Creates a dedicated 'ck' schema for CorridorKey application tables,
-- separate from Supabase's auth/storage/public schemas.

-- Create schema owned by postgres (the role our app connects as)
CREATE SCHEMA IF NOT EXISTS ck AUTHORIZATION postgres;

-- Grant full access to the postgres role (the app's connection user)
GRANT USAGE ON SCHEMA ck TO postgres;
GRANT ALL PRIVILEGES ON SCHEMA ck TO postgres;

-- Ensure future tables/sequences also get proper grants
ALTER DEFAULT PRIVILEGES IN SCHEMA ck GRANT ALL ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA ck GRANT USAGE, SELECT ON SEQUENCES TO postgres;

-- Application tables
CREATE TABLE IF NOT EXISTS ck.settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ck.invite_tokens (
    token TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ck.job_history (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ck.gpu_credits (
    user_id TEXT PRIMARY KEY,
    contributed_seconds FLOAT DEFAULT 0,
    consumed_seconds FLOAT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Grant table and sequence access explicitly (in case DEFAULT PRIVILEGES
-- didn't apply because tables were created by a different role)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA ck TO postgres;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ck TO postgres;
