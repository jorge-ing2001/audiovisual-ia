"""Cola de jobs respaldada por BD, worker y pipeline prompt->video.

Cola en proceso respaldada por la tabla ``Job`` con workers; granularidad por escena.
Implementada en FEAT-002 (cola/worker); el pipeline end-to-end llega en FEAT-006.
"""

from __future__ import annotations

from app.jobs.queue import JobQueue
from app.jobs.worker import (
    get_handler,
    register_handler,
    run_job_to_completion,
    worker_loop,
)

__all__ = [
    "JobQueue",
    "register_handler",
    "get_handler",
    "run_job_to_completion",
    "worker_loop",
]
