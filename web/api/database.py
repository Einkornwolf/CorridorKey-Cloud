"""Database abstraction layer for CorridorKey cloud platform.

Provides a unified interface for persisting state. Two backends:
- JSONBackend: the existing JSON file (default, for local/no-auth setups)
- PostgresBackend: Supabase Postgres (for cloud platform with auth)

The active backend is selected by CK_AUTH_ENABLED. When auth is disabled,
the JSON backend is used. When auth is enabled and Supabase is configured,
Postgres is used.

Schema (Postgres, in 'ck' schema):
- ck.settings: key-value store for server settings
- ck.invite_tokens: invite tokens for signup
- ck.job_history: completed job records
- ck.gpu_credits: per-user GPU time tracking

Tables are created by deploy/init-db.sql on first container start.
The app falls back to creating the ck schema at runtime if needed.
Note: auth.users is managed by Supabase GoTrue, not by us.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Check if we should use Postgres
_USE_POSTGRES = os.environ.get("CK_AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
_PG_URL = os.environ.get("CK_DATABASE_URL", "")


class StorageBackend:
    """Abstract storage interface."""

    def get_setting(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    def set_setting(self, key: str, value: Any) -> None:
        raise NotImplementedError

    def get_all_settings(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_invite_tokens(self) -> dict[str, dict]:
        raise NotImplementedError

    def save_invite_token(self, token: str, data: dict) -> None:
        raise NotImplementedError

    def save_job_history(self, history: list[dict]) -> None:
        raise NotImplementedError

    def load_job_history(self) -> list[dict]:
        raise NotImplementedError


class JSONBackend(StorageBackend):
    """JSON file storage — the existing persist.py backend, wrapped."""

    def __init__(self):
        from . import persist

        self._persist = persist

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._persist.load_key(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._persist.save_key(key, value)

    def get_all_settings(self) -> dict[str, Any]:
        return self._persist.load_all()

    def get_invite_tokens(self) -> dict[str, dict]:
        return self._persist.load_key("invite_tokens", {})

    def save_invite_token(self, token: str, data: dict) -> None:
        invites = self.get_invite_tokens()
        invites[token] = data
        self._persist.save_key("invite_tokens", invites)

    def save_job_history(self, history: list[dict]) -> None:
        self._persist.save_job_history(history)

    def load_job_history(self) -> list[dict]:
        return self._persist.load_job_history()


class PostgresBackend(StorageBackend):
    """PostgreSQL storage via Supabase Postgres.

    Uses psycopg2 for direct connections. Tables are created on first use.
    """

    def __init__(self, database_url: str):
        self._url = database_url
        self._conn = None
        self._init_tables()

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            try:
                import psycopg2

                self._conn = psycopg2.connect(self._url, options="-c search_path=ck,public")
                self._conn.autocommit = True
            except ImportError:
                logger.error("psycopg2 not installed. Install with: pip install psycopg2-binary")
                raise
        return self._conn

    def _init_tables(self):
        """Verify the ck schema and tables exist.

        Tables are created by deploy/init-db.sql (runs on first container
        start via docker-entrypoint-initdb.d). For existing volumes, run:
            cat deploy/init-db.sql | docker exec -i <db-container> \\
                psql -U supabase_admin -d corridorkey

        If the ck schema exists, uses it. Otherwise tries to create tables
        directly (works on non-Supabase Postgres where the user has CREATE).
        """
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            # Check if ck schema exists
            cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'ck'")
            if cur.fetchone():
                logger.info("Using Postgres ck schema")
            else:
                # Try creating schema + tables (works on standard Postgres,
                # may fail on Supabase if init-db.sql hasn't been run)
                logger.info("ck schema not found — attempting to create")
                try:
                    cur.execute("CREATE SCHEMA IF NOT EXISTS ck")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ck.settings (
                            key TEXT PRIMARY KEY, value JSONB NOT NULL,
                            updated_at TIMESTAMPTZ DEFAULT NOW());
                        CREATE TABLE IF NOT EXISTS ck.invite_tokens (
                            token TEXT PRIMARY KEY, data JSONB NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW());
                        CREATE TABLE IF NOT EXISTS ck.job_history (
                            id SERIAL PRIMARY KEY, data JSONB NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW());
                        CREATE TABLE IF NOT EXISTS ck.gpu_credits (
                            user_id TEXT PRIMARY KEY,
                            contributed_seconds FLOAT DEFAULT 0,
                            consumed_seconds FLOAT DEFAULT 0,
                            updated_at TIMESTAMPTZ DEFAULT NOW());
                    """)
                    logger.info("Created ck schema and tables")
                except Exception as schema_err:
                    raise RuntimeError(
                        f"Cannot create ck schema ({schema_err}). "
                        "Run deploy/init-db.sql as supabase_admin — see the error above."
                    ) from schema_err
            cur.close()
        except RuntimeError:
            cur.close()
            raise
        except Exception as e:
            cur.close()
            raise RuntimeError(f"Postgres initialization failed: {e}") from e

    def get_setting(self, key: str, default: Any = None) -> Any:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else default
        except Exception:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()""",
                (key, json.dumps(value), json.dumps(value)),
            )
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save setting {key}: {e}")

    def get_all_settings(self) -> dict[str, Any]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            result = {row[0]: row[1] for row in cur.fetchall()}
            cur.close()
            return result
        except Exception:
            return {}

    def get_invite_tokens(self) -> dict[str, dict]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT token, data FROM invite_tokens")
            result = {row[0]: row[1] for row in cur.fetchall()}
            cur.close()
            return result
        except Exception:
            return {}

    def save_invite_token(self, token: str, data: dict) -> None:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO invite_tokens (token, data) VALUES (%s, %s)
                   ON CONFLICT (token) DO UPDATE SET data = %s""",
                (token, json.dumps(data), json.dumps(data)),
            )
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save invite token: {e}")

    def save_job_history(self, history: list[dict]) -> None:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM job_history")
            if history:
                cur.execute(
                    "INSERT INTO job_history (data) VALUES (%s)",
                    (json.dumps(history[-200:]),),
                )
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save job history: {e}")

    def load_job_history(self) -> list[dict]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT data FROM job_history ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            cur.close()
            return row[0] if row else []
        except Exception:
            return []


# Singleton backend instance
_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Get the active storage backend (singleton)."""
    global _backend
    if _backend is None:
        if _USE_POSTGRES and _PG_URL:
            try:
                _backend = PostgresBackend(_PG_URL)
                logger.info("Using PostgreSQL storage backend")
            except Exception as e:
                logger.warning(f"Postgres backend failed, falling back to JSON: {e}")
                _backend = JSONBackend()
        else:
            _backend = JSONBackend()
            logger.info("Using JSON file storage backend")
    return _backend
