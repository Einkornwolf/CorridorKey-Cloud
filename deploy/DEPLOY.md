# CorridorKey Cloud Deployment Guide

## Quick Start

```bash
cd deploy

# 1. Create your .env from the example
cp .env.example .env

# 2. Generate secrets and paste them into .env
openssl rand -hex 32   # → POSTGRES_PASSWORD
openssl rand -hex 32   # → JWT_SECRET, CK_JWT_SECRET, GOTRUE_JWT_SECRET (all same value)

# 3. Set CK_AUTH_ENABLED=true in .env

# 4. Start the stack
docker compose -f docker-compose.dev.yml --env-file .env up -d --build

# 5. Create the first admin user
./create-admin.sh
```

## Environment Configuration

Everything lives in a single `.env` file. Key rules:

- **No quotes** around values: `JWT_SECRET=abc123` not `JWT_SECRET="abc123"`
- **No trailing spaces** after values
- **Unix line endings** (LF). Windows editors may save as CRLF which breaks env var parsing.
  Check with: `file .env` — should say "ASCII text", not "ASCII text, with CRLF line terminators"
  Fix with: `dos2unix .env` or `sed -i 's/\r$//' .env`
- **Three secrets must match**: `JWT_SECRET`, `CK_JWT_SECRET`, and `GOTRUE_JWT_SECRET` must all be the same value. GoTrue signs JWTs with it, CorridorKey validates with it.

### Required Variables (for auth mode)

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | Database password (all Supabase services use this) |
| `JWT_SECRET` | JWT signing secret (shared between GoTrue and CK) |
| `CK_JWT_SECRET` | Same as JWT_SECRET (CK reads this name) |
| `CK_AUTH_ENABLED` | `true` to enable login/auth |
| `CK_GOTRUE_INTERNAL_URL` | `http://supabase-auth:9999` (Docker internal) |
| `CK_DATABASE_URL` | `postgresql://postgres:<pw>@supabase-db:5432/corridorkey` |
| `CK_MIGRATION_URL` | `postgresql://supabase_admin:<pw>@supabase-db:5432/corridorkey` |
| `SERVICE_ROLE_KEY` | Supabase admin API key (for create-admin.sh) |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CK_METRICS_ENABLED` | `false` | Enable Prometheus /metrics endpoint |
| `CK_LOG_FORMAT` | `text` | `json` for Loki/structured logging |
| `CK_DOCS_PUBLIC` | auto | `true`/`false` to override API docs access |
| `CK_STORAGE_BACKEND` | `local` | `s3` for S3-compatible storage |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Docker Network                                      │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │  supabase-db │  │ supabase-auth│  │ corridorkey │ │
│  │  (Postgres)  │←─│   (GoTrue)   │  │    -web     │ │
│  │  :5432       │  │  :9999       │←─│  :3000      │ │
│  └──────────────┘  └──────────────┘  └──────┬──────┘ │
│                                              │        │
└──────────────────────────────────────────────┼────────┘
                                               │
                                          Port 3000
                                          (or via Caddy)
```

- **supabase-db**: Postgres 15 with Supabase extensions. Stores auth users and CK app state.
- **supabase-auth**: GoTrue — handles login, signup, JWT issuance.
- **corridorkey-web**: FastAPI server + Svelte SPA. All browser traffic goes here.

## Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.dev.yml` | Full stack: CK + Supabase (builds from source) |
| `docker-compose.web.yml` | CK web server only (uses pre-built image) |
| `docker-compose.node.yml` | Render farm node agent |
| `docker-compose.node-hardened.yml` | Hardened node (read-only, dropped caps) |
| `docker-compose.monitoring.yml` | Prometheus + Grafana + Loki |
| `docker-compose.caddy.yml` | TLS/HTTPS via Caddy |
| `docker-compose.supabase.yml` | Supabase stack only (no CK) |

Compose files are **composable**:
```bash
# Dev + monitoring
docker compose -f docker-compose.dev.yml -f docker-compose.monitoring.yml --env-file .env up -d

# Production: pre-built image + Caddy HTTPS
docker compose -f docker-compose.web.yml -f docker-compose.caddy.yml --env-file .env up -d
```

## First-Time Setup

### 1. Generate Supabase API Keys

You need ANON_KEY and SERVICE_ROLE_KEY. Generate them using the JWT_SECRET:

```bash
# Using the Supabase key generator:
# https://supabase.com/docs/guides/self-hosting#api-keys

# Or manually with jwt-cli:
# ANON_KEY: role=anon
# SERVICE_ROLE_KEY: role=service_role
```

### 2. Create Admin User

After the stack is up:
```bash
./create-admin.sh
```

This creates the first `platform_admin` user via GoTrue's admin API, bypassing the `DISABLE_SIGNUP=true` restriction.

### 3. Invite Users

1. Log in as admin at `http://localhost:3000/login`
2. Go to Admin → Users → Generate Invite Link
3. Share the link with your team
4. Approve users in the Admin → Users → Pending section

## Docker Swarm Deployment

Swarm handles env vars differently from regular Compose. Key differences:

### Use `env_file:` in the service definition

`--env-file` on the CLI does variable **substitution** in the compose file. To get variables **into the container**, use `env_file:` inside the service:

```yaml
services:
  corridorkey-web:
    env_file:
      - .env
    environment:
      # Swarm prefers mapping format over list format
      CK_CLIPS_DIR: /app/Projects
```

### Use mapping format for environment

```yaml
# ✅ Works in Swarm
environment:
  CK_AUTH_ENABLED: ${CK_AUTH_ENABLED}

# ❌ Can break in Swarm
environment:
  - CK_AUTH_ENABLED=${CK_AUTH_ENABLED:-false}
```

### Database volumes

- Use **local volumes** for Postgres, not NFS. Databases on NFS have fsync/lock issues.
- Swarm volumes survive `docker stack rm`. To truly reset the DB:
  ```bash
  docker stack rm mystack
  docker volume ls | grep db
  docker volume rm mystack_supabase-db-data
  ```
- The Postgres init scripts only run on a **fresh volume**. If you change `POSTGRES_PASSWORD` after first init, you must nuke the volume.

### Troubleshooting: Role passwords not set

If GoTrue fails with `password authentication failed for supabase_auth_admin`, the `POSTGRES_PASSWORD` didn't propagate during DB init. Fix:

```bash
docker exec <postgres_container> psql -U supabase_admin -d corridorkey -c "
  ALTER ROLE supabase_auth_admin WITH PASSWORD '<POSTGRES_PASSWORD>';
  ALTER ROLE authenticator WITH PASSWORD '<POSTGRES_PASSWORD>';
"
```

Then restart GoTrue.

## Monitoring Setup

### With the bundled stack

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.monitoring.yml --env-file .env up -d
```

Set `CK_METRICS_ENABLED=true` in `.env`. Access:
- Grafana: `http://localhost:3001` (admin/admin — change in `.env`)
- Prometheus: `http://localhost:9090`

### With an external Prometheus

Add to your existing `prometheus.yml`:
```yaml
- job_name: corridorkey
  metrics_path: /metrics
  scrape_interval: 15s
  static_configs:
    - targets: ['YOUR_CK_IP:3000']
```

Import dashboards from `deploy/monitoring/grafana/dashboards/` into your Grafana.

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| 404 on `/login` | `CK_AUTH_ENABLED` not reaching container | Check for CRLF line endings in `.env`. Run `dos2unix .env` |
| `auth_enabled: false` but env is set | Trailing `\r` in env value | Same: `dos2unix .env` |
| GoTrue: `password authentication failed` | DB volume has stale data | Nuke volume and redeploy |
| GoTrue: `role does not exist` | Not using `supabase/postgres` image | Must use `supabase/postgres:15.6.1.143`, not `postgres:15` |
| `Skipping initialization` | Volume already exists | `docker volume rm <volume>` |
| `no such image` after prune | Swarm doesn't auto-pull | `docker pull <image>` before deploy |
