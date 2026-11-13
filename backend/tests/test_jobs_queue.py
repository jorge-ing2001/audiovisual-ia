"""Ciclo de vida de jobs: no-op succeeded y job que falla y agota reintentos."""

from __future__ import annotations

import asyncio

from app.jobs.queue import JobQueue
from app.jobs.worker import register_handler, run_job_to_completion


def test_noop_job_lifecycle_pending_running_succeeded(db_ready):
    queue = JobQueue()
    job_id = queue.enqueue("noop", payload={"hello": "world"})

    # Recién encolado: pending.
    assert queue.get(job_id).status == "pending"

    asyncio.run(run_job_to_completion(queue))

    job = queue.get(job_id)
    assert job.status == "succeeded"
    assert job.progress == 1.0
    assert job.result == {"noop": True, "job_id": job_id, "payload": {"hello": "world"}}
    assert job.error is None


def test_failing_job_exhausts_retries_and_fails(db_ready):
    attempts = {"count": 0}

    def boom(payload, job_id):
        attempts["count"] += 1
        raise RuntimeError("fallo intencionado")

    register_handler("boom", boom)

    queue = JobQueue()
    job_id = queue.enqueue("boom", max_retries=2)

    asyncio.run(run_job_to_completion(queue))

    job = queue.get(job_id)
    assert job.status == "failed"
    assert job.retries == 2  # agotó los reintentos
    assert attempts["count"] == 3  # intento inicial + 2 reintentos
    assert "fallo intencionado" in job.error
    assert "RuntimeError" in job.error
