# Riesgos y supuestos

Nota breve y honesta de riesgos y supuestos que afectan a la Fase 1 y a la evolución futura.

## Riesgos

1. **Licencia de Remotion para uso comercial (Fase 4).** Remotion tiene nivel gratuito pero **condiciones de licencia para empresas**. Para el MVP (desarrollo/evaluación) es viable; antes de cualquier explotación comercial hay que revisar y, si aplica, contratar la licencia de empresa de Remotion. **Acción pendiente del usuario.**

2. **Cómputo local / GPU no garantizado.** Ollama con modelos grandes y Stable Diffusion local requieren CPU/GPU que **no están garantizados en el sandbox**. Mitigación de diseño: LLM usa un modelo pequeño (`llama3.2`) y el **default de imágenes degrada a stock/placeholder**; SD local y modelos grandes son opcionales y pluggables.

3. **Gestor de paquetes del sistema de solo lectura.** No se pueden instalar paquetes de sistema ni escribir en `$HOME` en el sandbox. FFmpeg es un binario estático en `/projects/sandbox/.localbin`. Mitigación: rutas de binarios configurables por env var; dependencias Python/Node en entornos de proyecto (venv, node_modules), no en el sistema.

4. **APIs de imágenes de stock gratuitas requieren clave y/o atribución.** Pexels/Pixabay son gratuitas pero necesitan registro de una clave gratuita y pueden exigir atribución. Mitigación: degradación automática a `placeholder` local cuando no hay clave o red; registrar la atribución del asset en `Asset.meta`.

5. **Ollama no preinstalado.** No está en el sandbox. La tarea de setup debe instalarlo/arrancarlo o, si no es posible, dejar el backend LLM claramente marcado como no disponible y el resto del pipeline verificable con un guion de prueba inyectado. **No se simulan resultados presentados como terminados** (sección 9); un guion de prueba se etiqueta como tal.

6. **Kokoro y rutas largas.** El TTS offline (espeak-ng) falla si la ruta de datos supera ~160 bytes. `tts.py` ya mitiga copiando a `~/.cache`; en el sandbox `$HOME` es de solo lectura, así que puede requerir configurar una ruta de caché escribible o usar el fallback edge-tts.

## Supuestos

- Hay acceso a red (web + registros de paquetes) durante el desarrollo; el runtime en producción puede ser offline salvo para proveedores online activados.
- Node v22, npm, bun 1.2.14, Python 3.11.15 y FFmpeg 7.0.2 están disponibles (verificado).
- El código MIT de lemo-opuscar puede reutilizarse con atribución (verificado en `REFERENCE-REPOS-LICENSES.md`).
- SQLite es suficiente para el MVP local; se migrará a Postgres en Fase 4 sin cambiar las interfaces de almacenamiento.
- Esta es una entrega **solo de planificación**: la implementación se realiza tarea por tarea desde `.agents/tasks/` en entregas posteriores.
