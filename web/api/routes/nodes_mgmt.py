"""Node management endpoints for the frontend (CRKY-53).

Org-scoped node visibility with role-based management actions.
Uses JWT auth (via middleware). Separate from the node agent endpoints
in nodes.py which use CK_AUTH_TOKEN.

- Members see nodes belonging to their org (read-only)
- Org admins can manage nodes (pause, schedule, remove, accepted types)
- Platform admins see and manage all nodes
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import AUTH_ENABLED, UserContext, get_current_user
from ..database import get_storage
from ..nodes import NodeInfo, NodeSchedule, registry
from ..orgs import get_org_store
from ..tier_guard import require_member
from ..ws import manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/farm", tags=["farm"], dependencies=[Depends(require_member)])


def _get_user(request: Request) -> UserContext | None:
    return get_current_user(request)


def _user_can_see_node(user: UserContext | None, node: NodeInfo) -> bool:
    """Check if a user can see this node based on org membership."""
    if not AUTH_ENABLED or user is None:
        return True
    if user.is_admin:
        return True
    if not node.org_id:
        return False  # Unscoped nodes only visible to platform admin
    store = get_org_store()
    return store.is_member(node.org_id, user.user_id)


def _user_can_manage_node(user: UserContext | None, node: NodeInfo) -> bool:
    """Check if a user can manage (pause/schedule/remove) this node."""
    if not AUTH_ENABLED or user is None:
        return True
    if user.is_admin:
        return True
    if not node.org_id:
        return user.is_admin  # Unscoped nodes only manageable by platform admin
    store = get_org_store()
    return store.is_org_admin(node.org_id, user.user_id)


def _require_node_access(request: Request, node_id: str, manage: bool = False) -> NodeInfo:
    """Get a node and verify user access. Raises 404/403."""
    node = registry.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    user = _get_user(request)
    if not _user_can_see_node(user, node):
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    if manage and not _user_can_manage_node(user, node):
        raise HTTPException(status_code=403, detail="Only org admins can manage this node")
    return node


def _save_node_config(node_id: str, node: NodeInfo) -> None:
    """Persist UI-configurable node settings."""
    storage = get_storage()
    configs = storage.get_setting("node_configs", {})
    configs[node_id] = {
        "paused": node.paused,
        "schedule": node.schedule.to_dict(),
        "accepted_types": node.accepted_types,
    }
    storage.set_setting("node_configs", configs)


# --- Listing ---


@router.get("")
def list_managed_nodes(request: Request):
    """List nodes visible to the current user, filtered by org membership.

    Sensitive fields (host IP) are redacted for members who can't manage
    the node. Only org admins/owners and platform admins see full details.
    """
    user = _get_user(request)
    all_nodes = registry.list_nodes()
    visible = [n for n in all_nodes if _user_can_see_node(user, n)]
    manageable = {n.node_id for n in all_nodes if _user_can_manage_node(user, n)}
    result = []
    for n in visible:
        data = n.to_dict()
        can_manage = n.node_id in manageable
        data["can_manage"] = can_manage
        if not can_manage:
            # Redact infrastructure/operational details for read-only members.
            # Members see: name, status, GPU names, busy state — enough to
            # understand queue behavior. Not IPs, logs, or config.
            data["host"] = "***"
            data["shared_storage"] = None
            data["capabilities"] = []
            data["cpu_stats"] = {}
            data["accepted_types"] = []
        result.append(data)
    return result


# --- Operational info (require org admin) ---


@router.get("/{node_id}/health")
def get_node_health(node_id: str, request: Request):
    """Get health history for a node. Requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    return {"history": node.health_history}


@router.get("/{node_id}/logs")
def get_node_logs(node_id: str, request: Request):
    """Get recent log lines from a node. Requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    return {"logs": node.recent_logs}


# --- Management actions (require org admin) ---


class NodeScheduleRequest(BaseModel):
    enabled: bool = False
    start: str = "00:00"
    end: str = "23:59"


class AcceptedTypesRequest(BaseModel):
    accepted_types: list[str] = []


@router.post("/{node_id}/pause")
def pause_node(node_id: str, request: Request):
    """Pause a node — requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    node.paused = True
    _save_node_config(node_id, node)
    manager.send_node_update(node.to_dict())
    return {"status": "paused"}


@router.post("/{node_id}/resume")
def resume_node(node_id: str, request: Request):
    """Resume a paused node — requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    node.paused = False
    _save_node_config(node_id, node)
    manager.send_node_update(node.to_dict())
    return {"status": "resumed"}


@router.get("/{node_id}/schedule")
def get_node_schedule(node_id: str, request: Request):
    node = _require_node_access(request, node_id)
    return node.schedule.to_dict()


@router.put("/{node_id}/schedule")
def set_node_schedule(node_id: str, req: NodeScheduleRequest, request: Request):
    """Set a node's active hours schedule — requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    node.schedule = NodeSchedule(enabled=req.enabled, start=req.start, end=req.end)
    _save_node_config(node_id, node)
    manager.send_node_update(node.to_dict())
    return node.schedule.to_dict()


@router.put("/{node_id}/accepted-types")
def set_accepted_types(node_id: str, req: AcceptedTypesRequest, request: Request):
    """Set which job types a node will accept — requires org admin."""
    node = _require_node_access(request, node_id, manage=True)
    node.accepted_types = req.accepted_types
    _save_node_config(node_id, node)
    manager.send_node_update(node.to_dict())
    return {"accepted_types": node.accepted_types}


@router.delete("/{node_id}")
def unregister_node(node_id: str, request: Request):
    """Remove a node — requires org admin."""
    _require_node_access(request, node_id, manage=True)
    registry.unregister(node_id)
    manager.send_node_offline(node_id)
    return {"status": "unregistered"}
