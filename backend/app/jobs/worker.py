"""Worker asyncio que ejecuta handlers de jobs registrados (§6).

Flujo: reclama un job ``pending`` de la ``JobQueue``, busca el handler por
``job.type`` y lo ejecuta. Si lanza excepción, el job se reintenta con backoff
exponencial hasta agotar ``max_retries``; entonces queda ``failed`` con el
mensaje de error. Al terminar bien, queda ``succeeded`` con el resultado.

Un handler recibe ``(payload, job_id)`` y devuelve un ``dict`` serializable (o
``None``). Se registran con ``register_handler``. Incluye un handler no-op de
ejemplo (``noop``).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.jobs.queue import JobQueue
from app.models.entities import Job
from app.storage.db import session_scope

# Un handler recibe (payload, job_id) y devuelve un dict (o None). Puede ser
# síncrono o una corrutina; el worker normaliza ambos casos.
Handler = Callable[[dict | None, int], Any | Awaitable[Any]]

_HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    """Registra el handler que procesa un tipo de job."""
    _HANDLERS[job_type] = handler


def get_handler(job_type: str) -> Handler | None:
    """Devuelve el handler registrado para un tipo de job, si existe."""
    return _HANDLERS.get(job_type)


def noop_handler(payload: dict | None, job_id: int) -> dict:
    """Handler no-op de ejemplo: no hace trabajo real, solo confirma ejecución."""
    return {"noop": True, "job_id": job_id, "payload": payload}


# Registro del handler de ejemplo al importar el módulo.
register_handler("noop", noop_handler)


async def _run_handler(handler: Handler, payload: dict | None, job_id: int) -> Any:
    """Ejecuta handlers síncronos en un hilo y admite corrutinas."""
    if inspect.iscoroutinefunction(handler):
        return await handler(payload, job_id)

    result = await asyncio.to_thread(handler, payload, job_id)

    if inspect.isawaitable(result):
        return await result

    return result

def _job_type_and_payload(job_id: int) -> tuple[str, dict | None]:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise KeyError(f"Job {job_id} no existe")
        return job.type, job.payload


async def process_one(queue: JobQueue, *, base_backoff: float = 0.0) -> int | None:
    """Reclama y procesa un job. Devuelve el id procesado o ``None`` si no había.

    Mantiene la política de reintentos/backoff; ``base_backoff`` en 0 (default en
    tests) evita esperas. El worker real usa un backoff mayor.
    """
    job_id = queue.claim()
    if job_id is None:
        return None

    job_type, payload = _job_type_and_payload(job_id)
    handler = get_handler(job_type)
    if handler is None:
        queue.fail(job_id, f"No hay handler registrado para el tipo {job_type!r}", retry=False)
        return job_id

    try:
        result = await _run_handler(handler, payload, job_id)
    except Exception as exc:  # noqa: BLE001 — el worker aísla el fallo del handler
        outcome = queue.fail(job_id, f"{type(exc).__name__}: {exc}")
        if outcome == "pending" and base_backoff > 0:
            # Backoff exponencial por número de reintentos ya consumidos.
            retries = (queue.get(job_id).retries if queue.get(job_id) else 1)
            await asyncio.sleep(base_backoff * (2 ** (retries - 1)))
        return job_id

    result_dict = result if isinstance(result, dict) or result is None else {"value": result}
    queue.complete(job_id, result_dict)
    return job_id


async def run_job_to_completion(queue: JobQueue, *, base_backoff: float = 0.0) -> None:
    """Procesa un job repetidamente hasta que deje de estar ``pending``.

    Cada intento fallido con reintentos disponibles devuelve el job a ``pending``;
    este bucle lo vuelve a reclamar hasta que acabe ``succeeded`` o ``failed``. Es
    la unidad que usan los tests del ciclo de vida.
    """
    while True:
        job_id = await process_one(queue, base_backoff=base_backoff)
        if job_id is None:
            return
        job = queue.get(job_id)
        if job is None or job.status in ("succeeded", "failed"):
            return


async def worker_loop(
    queue: JobQueue,
    *,
    poll_interval: float = 0.5,
    base_backoff: float = 1.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Bucle principal del worker: drena la cola y espera cuando está vacía.

    Pensado para arrancar junto a uvicorn (una o varias instancias). Se detiene de
    forma limpia señalizando ``stop_event``.
    """
    while stop_event is None or not stop_event.is_set():
        processed = await process_one(queue, base_backoff=base_backoff)
        if processed is None:
            await asyncio.sleep(poll_interval)
