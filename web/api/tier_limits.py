"""Per-tier resource limits (CRKY-114).

Enforces frame count and concurrent job limits based on user tier.
Checked at job submission time before _stamp_job.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from .auth import AUTH_ENABLED, get_current_user
from .deps import get_queue

logger = logging.getLogger(__name__)

# Limits per tier: max_frames per clip, max concurrent jobs (running + queued)
TIER_LIMITS = {
    "pending": {"max_frames": 0, "max_concurrent": 0},  # can't submit
    "member": {"max_frames": 500, "max_concurrent": 3},
    "contributor": {"max_frames": 2000, "max_concurrent": 5},
    "org_admin": {"max_frames": 5000, "max_concurrent": 10},
    "platform_admin": {"max_frames": 0, "max_concurrent": 0},  # 0 = unlimited
}


def check_tier_limits(request: Request, frame_count: int = 0) -> None:
    """Check if the user's tier allows this job submission.

    Raises HTTP 403 if frame count exceeds tier limit or too many concurrent jobs.
    No-op when auth is disabled or user is platform_admin.
    """
    if not AUTH_ENABLED:
        return

    user = get_current_user(request)
    if not user:
        return
    if user.is_admin:
        return

    limits = TIER_LIMITS.get(user.tier, TIER_LIMITS["pending"])
    max_frames = limits["max_frames"]
    max_concurrent = limits["max_concurrent"]

    # Frame count check
    if max_frames > 0 and frame_count > max_frames:
        raise HTTPException(
            status_code=403,
            detail=f"Your tier ({user.tier}) is limited to {max_frames} frames per clip. "
            f"This clip has {frame_count} frames. Contribute a GPU node to increase your limit.",
        )

    # Concurrent job check
    if max_concurrent > 0:
        queue = get_queue()
        user_running = sum(1 for j in queue.running_jobs if j.submitted_by == user.user_id)
        user_queued = sum(1 for j in queue.queue_snapshot if j.submitted_by == user.user_id)
        total = user_running + user_queued
        if total >= max_concurrent:
            raise HTTPException(
                status_code=429,
                detail=f"You have {total} jobs running/queued (limit: {max_concurrent} for {user.tier} tier). "
                f"Wait for current jobs to finish or contribute a GPU to increase your limit.",
            )
