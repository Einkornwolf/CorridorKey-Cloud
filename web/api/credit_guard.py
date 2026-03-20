"""GPU credit enforcement — blocks job submission if over ratio (CRKY-37).

Checks the requesting user's org credit balance before allowing job
submission. If the org has consumed more than the configurable ratio
allows relative to their contributions, the job is rejected.

Settings:
- CK_CREDIT_RATIO: max consumed/contributed ratio (default 2.0).
  Set to 0 to disable enforcement.
  Example: 2.0 means an org can consume 2x what they've contributed.
- CK_CREDIT_GRACE: free GPU-seconds before enforcement kicks in
  (default 3600 = 1 hour). Allows new orgs to submit jobs before
  they've set up nodes.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

from .auth import AUTH_ENABLED, get_current_user
from .gpu_credits import get_org_credits
from .orgs import get_org_store

logger = logging.getLogger(__name__)

# Configurable ratio: 0 = disabled, 2.0 = can consume 2x contributed
CREDIT_RATIO = float(os.environ.get("CK_CREDIT_RATIO", "2.0").strip())
# Grace period: free GPU-seconds before enforcement (default 1 hour)
CREDIT_GRACE = float(os.environ.get("CK_CREDIT_GRACE", "3600").strip())


def check_credit_balance(request: Request) -> None:
    """Check if the user's org has sufficient GPU credits.

    Raises HTTP 402 (Payment Required) if the org has exceeded their
    earn-to-use ratio. No-op when:
    - Auth is disabled
    - Credit enforcement is disabled (CK_CREDIT_RATIO=0)
    - User is platform_admin
    - Org hasn't exhausted the grace period
    """
    if not AUTH_ENABLED or CREDIT_RATIO <= 0:
        return

    user = get_current_user(request)
    if not user:
        return
    if user.is_admin:
        return

    # Find the user's primary org
    org_store = get_org_store()
    user_orgs = org_store.list_user_orgs(user.user_id)
    if not user_orgs:
        return  # No org — can't enforce

    # Check the first (primary) org
    org = user_orgs[0]
    credits = get_org_credits(org.org_id)

    # Grace period: allow free usage up to CREDIT_GRACE seconds
    if credits.consumed_seconds <= CREDIT_GRACE:
        return

    # If org has never contributed, block after grace
    if credits.contributed_seconds <= 0:
        raise HTTPException(
            status_code=402,
            detail=f"Your organization has used {credits.consumed_seconds / 3600:.1f} GPU-hours "
            f"but hasn't contributed any compute. Add a node to earn credits.",
        )

    # Check ratio
    if credits.ratio > CREDIT_RATIO:
        raise HTTPException(
            status_code=402,
            detail=f"Your organization has consumed {credits.consumed_seconds / 3600:.1f} GPU-hours "
            f"but only contributed {credits.contributed_seconds / 3600:.1f} GPU-hours "
            f"(ratio: {credits.ratio:.1f}x, limit: {CREDIT_RATIO:.1f}x). "
            f"Add more nodes or wait for current jobs to earn credits.",
        )
