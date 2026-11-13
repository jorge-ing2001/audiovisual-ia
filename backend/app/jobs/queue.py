"""Cola de jobs respaldada por la tabla ``Job`` (ver docs/ARCHITECTURE.md §6).

Interfaz ``JobQueue`` (enqueue/claim/complete/fail) sobre SQLite. La interfaz es
deliberadamente mínima para poder cambiar a Redis/RQ o Celery en Fase 4 sin tocar
los motores ni el worker.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.base import JobStatus
from app.models.entities import Job
from app.storage.db import session_scope


class JobQueue:
    """Cola en proceso respaldada por BD.

    Cada método abre su propia sesión transaccional; el worker y la API pueden
    compartir la misma cola sin acoplarse a una sesión concreta.
    """

    def enqueue(
        self,
        type: str,
        *,
        project_id: int | None = None,
        payload: dict | None = None,
        max_retries: int = 3,
    ) -> int:
        """Crea un job en estado ``pending`` y devuelve su id."""
        with session_scope() as session:
            job = Job(
                type=type,
                project_id=project_id,
                payload=payload,
                max_retries=max_retries,
                status=JobStatus.PENDING.value,
            )
            session.add(job)
            session.flush()
            return job.id

    def claim(self) -> int | None:
        """Reclama el job ``pending`` más antiguo y lo marca ``running``.

        Devuelve el id reclamado, o ``None`` si no hay trabajo pendiente.
        """
        with session_scope() as session:
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.PENDING.value)
                .order_by(Job.id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            try:
                job = session.execute(stmt).scalar_one_or_none()
            except Exception:
                # SQLite no soporta SELECT ... FOR UPDATE; recae en el select simple.
                job = session.execute(
                    select(Job)
                    .where(Job.status == JobStatus.PENDING.value)
                    .order_by(Job.id.asc())
                    .limit(1)
                ).scalar_one_or_none()
            if job is None:
                return None
            job.status = JobStatus.RUNNING.value
            return job.id

    def complete(self, job_id: int, result: dict | None = None) -> None:
        """Marca un job como ``succeeded`` con su resultado y progreso completo."""
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(f"Job {job_id} no existe")
            job.status = JobStatus.SUCCEEDED.value
            job.result = result
            job.progress = 1.0
            job.error = None

    def fail(self, job_id: int, error: str, *, retry: bool = True) -> str:
        """Registra un fallo de ejecución.

        Si quedan reintentos y ``retry`` es verdadero, incrementa el contador y
        devuelve el job a ``pending`` (devuelve ``"pending"``). En caso contrario
        lo marca ``failed`` con el mensaje (devuelve ``"failed"``).
        """
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(f"Job {job_id} no existe")
            job.error = error
            if retry and job.retries < job.max_retries:
                job.retries += 1
                job.status = JobStatus.PENDING.value
                return JobStatus.PENDING.value
            job.status = JobStatus.FAILED.value
            return JobStatus.FAILED.value

    def get(self, job_id: int) -> Job | None:
        """Lee un job por id (sesión de solo lectura, instancia desacoplada)."""
        with session_scope() as session:
            return session.get(Job, job_id)
