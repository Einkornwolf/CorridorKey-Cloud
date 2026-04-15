"""Persisted per-node UI settings (CRKY-177).

Factored out of the two previous copies in routes/nodes.py and
routes/nodes_mgmt.py, which each did a non-atomic load -> mutate ->
save on ``ck.settings["node_configs"]``. The store now writes one
row per node in ck.node_configs so parallel saves for different
nodes are independent.

The stored payload is a small dict (paused, visibility, schedule,
accepted_types); callers pass it in already-serialized so this
module stays out of the NodeInfo shape. A JSON-blob fallback is kept
for dev/test setups without Postgres.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def save_node_config(node_id: str, config: dict[str, Any]) -> None:
    """Upsert the persisted UI config for a node.

    Postgres path uses INSERT ... ON CONFLICT DO UPDATE so parallel
    writers for different node_ids are independent rows and a writer
    for the same node can replace the prior payload without touching
    any other row.
    """
    from .database import get_pg_conn

    payload = json.dumps(config or {})

    with get_pg_conn() as conn:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO ck.node_configs (node_id, config, updated_at)
                   VALUES (%s, %s::jsonb, NOW())
                   ON CONFLICT (node_id) DO UPDATE SET
                       config = EXCLUDED.config,
                       updated_at = NOW()""",
                (node_id, payload),
            )
            cur.close()
            return

    from .database import get_storage

    storage = get_storage()
    configs = storage.get_setting("node_configs", {})
    configs[node_id] = config or {}
    storage.set_setting("node_configs", configs)


def load_node_config(node_id: str) -> dict[str, Any] | None:
    """Read the persisted UI config for a node, or None if none stored."""
    from .database import get_pg_conn

    with get_pg_conn() as conn:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                "SELECT config FROM ck.node_configs WHERE node_id = %s",
                (node_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None

    from .database import get_storage

    configs = get_storage().get_setting("node_configs", {})
    return configs.get(node_id)
