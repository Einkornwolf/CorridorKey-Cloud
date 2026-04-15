"""Add node_configs table (CRKY-177).

Revision ID: 011
Revises: 010
Create Date: 2026-04-14

Moves per-node persisted UI settings (paused, visibility, schedule,
accepted_types) out of the ck.settings JSON blob into a per-node row.
Previously _save_node_config loaded the whole dict, mutated one
node's entry, and saved the whole blob, so two parallel saves for
different nodes could lose one of the updates — the classic "I
saved my node settings and when I reloaded they were gone" bug.
"""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ck.node_configs (
            node_id TEXT PRIMARY KEY,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
                EXECUTE 'GRANT ALL ON TABLE ck.node_configs TO postgres';
            END IF;
        END $$
    """)

    op.execute("""
        INSERT INTO ck.node_configs (node_id, config)
        SELECT entry.key, entry.value
        FROM ck.settings s,
             LATERAL jsonb_each(s.value) AS entry
        WHERE s.key = 'node_configs'
          AND jsonb_typeof(s.value) = 'object'
          AND jsonb_typeof(entry.value) = 'object'
        ON CONFLICT (node_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ck.node_configs")
