"""Redis pub/sub subscriber for cross-instance WebSocket fan-out (CRKY-105 Phase 3).

Runs as an asyncio background task in the FastAPI event loop. Listens on
Redis channels and relays messages to local WebSocket connections via
ConnectionManager.

Channels:
    ck:ws:broadcast — WS event fan-out across instances
    ck:ws:cancel    — cross-instance job cancellation
"""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_subscriber_task: asyncio.Task | None = None


async def _subscribe_loop(instance_id: str) -> None:
    """Main subscriber loop. Auto-reconnects on failure."""
    import redis.asyncio as aioredis

    from .redis_client import get_redis_url
    from .ws import manager

    url = get_redis_url()
    if not url:
        return

    while True:
        r = None
        pubsub = None
        try:
            r = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=5)
            pubsub = r.pubsub()
            await pubsub.subscribe("ck:ws:broadcast", "ck:ws:cancel")
            logger.info("Redis pub/sub subscriber connected")

            async for raw_message in pubsub.listen():
                if raw_message["type"] != "message":
                    continue

                channel = raw_message["channel"]
                try:
                    data = json.loads(raw_message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                if channel == "ck:ws:broadcast":
                    if data.get("instance_id") == instance_id:
                        continue  # echo suppression

                    msg = data.get("message")
                    if msg is None:
                        continue

                    org_id = data.get("org_id")
                    if data.get("admin_only"):
                        await manager._broadcast_admin_only(msg)
                    else:
                        await manager._broadcast(msg, org_id)

                elif channel == "ck:ws:cancel":
                    _handle_cancel(data)

        except asyncio.CancelledError:
            logger.info("Redis pub/sub subscriber shutting down")
            try:
                if pubsub:
                    await pubsub.unsubscribe()
                if r:
                    await r.aclose()
            except Exception:
                pass
            return
        except Exception:
            logger.warning("Redis pub/sub subscriber disconnected, reconnecting in 2s", exc_info=True)
            try:
                if r:
                    await r.aclose()
            except Exception:
                pass
            await asyncio.sleep(2)


def _handle_cancel(data: dict) -> None:
    """Handle cross-instance job cancellation."""
    job_id = data.get("job_id")
    if not job_id:
        return
    from .worker import cancel_local_job

    if cancel_local_job(job_id):
        logger.info(f"Cross-instance cancel: job {job_id} flagged on local worker")


async def start_subscriber() -> None:
    """Start the pub/sub subscriber as a background task."""
    global _subscriber_task
    from .redis_client import is_redis_configured
    from .ws import _INSTANCE_ID

    if not is_redis_configured():
        return

    _subscriber_task = asyncio.create_task(
        _subscribe_loop(_INSTANCE_ID),
        name="redis-pubsub-subscriber",
    )
    logger.info(f"Redis pub/sub subscriber started (instance {_INSTANCE_ID[:8]})")


async def stop_subscriber() -> None:
    """Stop the subscriber task gracefully."""
    global _subscriber_task
    if _subscriber_task is not None and not _subscriber_task.done():
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
        _subscriber_task = None
        logger.info("Redis pub/sub subscriber stopped")
