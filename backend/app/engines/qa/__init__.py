"""Motor de Control de Calidad (QA) del render (Bloque 4, FEAT-004).

Produce un ``qa_report`` REAL por render ejecutando ffprobe/ffmpeg sobre el MP4 y
evaluando 5 controles con 4 estados (``pass``/``warn``/``fail``/``skipped``). Separa de
forma estricta:

- ``checks.py``: EVALUACIÓN PURA (sin ffmpeg). Dadas las mediciones (o un ``skip_reason``)
  y los metadatos del proyecto, clasifica cada control y calcula el ``overall`` conservador
  (``fail > warn > skipped > pass``; ``pass_with_skips`` si lo único no-pass son skipped).
- ``measure.py``: MEDICIÓN con ffprobe/ffmpeg (loudness ebur128, duración/resolución,
  blackdetect). Si faltan binarios o datos, devuelve ``skip_reason`` (no finge medición).
- ``report.py``: ORQUESTA (mide sobre el MP4 del render y evalúa) y arma el reporte con
  trazabilidad completa (``generated_at``, ``asset_id``, ``render_hash`` sha256,
  ``qa_engine_version``, ``thresholds``, ``checks[]``, ``skipped_checks[]``).

Ver ``docs/ADMIN-QUALITY.md`` §2.
"""

from __future__ import annotations

#: Versión del motor de QA. Se incluye en cada ``qa_report`` (``qa_engine_version``) para
#: saber con qué lógica/umbrales se evaluó un reporte antiguo. Se incrementa al cambiar la
#: lógica de clasificación o el conjunto de controles.
qa_engine_version = "1.0.0"

#: Esquema del documento de reporte persistido en ``Asset.meta['qa_report']``.
QA_REPORT_SCHEMA = "qa_report/v1"

__all__ = ["qa_engine_version", "QA_REPORT_SCHEMA"]
