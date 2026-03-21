"""Job orphan reaper — requeues jobs claimed by dead nodes.

Runs as a background thread on the server. Every 30 seconds, checks
all running jobs: if the claiming node's heartbeat has timed out,
the job is returned to the front of the queue.
"""

from __future__ import annotations

import logging
import threading

from backend.job_queue import GPUJobQueue, JobStatus

from .nodes import registry
from .ws import manager

logger = logging.getLogger(__name__)

_REAP_INTERVAL = 30  # seconds


def _reap_once(queue: GPUJobQueue) -> None:
    """Check for orphaned jobs and requeue them.

    Scans ALL running jobs (not just the first) to handle multi-GPU
    nodes that may have multiple jobs in flight when they die.
    """
    for job in list(queue.running_jobs):
        if job.status != JobStatus.RUNNING or not job.claimed_by:
            continue
        if job.claimed_by == "local":
            continue  # local jobs are managed by the worker thread

        node = registry.get_node(job.claimed_by)
        if node is None or not node.is_alive:
            logger.warning(f"Reaping orphan job [{job.id}]: node '{job.claimed_by}' is dead, requeuing")
            queue.requeue_job(job)
            manager.send_job_status(job.id, JobStatus.QUEUED.value, org_id=job.org_id)
            # Ding the node's reputation for dropping a job
            from .node_reputation import record_job_failed

            record_job_failed(job.claimed_by)
            if node:
                registry.set_idle(job.claimed_by)


def reaper_loop(queue: GPUJobQueue, stop_event: threading.Event) -> None:
    """Background thread that periodically checks for orphaned jobs."""
    logger.info(f"Job reaper started (interval: {_REAP_INTERVAL}s)")
    while not stop_event.is_set():
        stop_event.wait(_REAP_INTERVAL)
        if not stop_event.is_set():
            try:
                _reap_once(queue)
            except Exception:
                logger.exception("Reaper error")


def start_reaper(queue: GPUJobQueue, stop_event: threading.Event) -> threading.Thread:
    """Start the reaper daemon thread."""
    thread = threading.Thread(
        target=reaper_loop,
        args=(queue, stop_event),
        daemon=True,
        name="job-reaper",
    )
    thread.start()
    return thread
