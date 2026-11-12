# Repos de referencia y licencias

Se inspeccionó cada repositorio para identificar arquitectura, componentes reutilizables, dependencias y, de forma explícita, su **licencia y condiciones de uso comercial**. No se copian proyectos completos; la arquitectura de `audiovisual-ia` es propia e independiente. Esta verificación responde a la sección 9 del prompt maestro y a la condición de la sección 4.

## Resumen de licencias

| Repo | Licencia | ¿Reutilizable como código? | Uso comercial |
|------|----------|----------------------------|---------------|
| **lemo-opuscar** (local) | Código MIT (`core/`, `tools/`, `styleboard/`, `plugin/`); docs CC BY-NC 4.0; films CC BY 4.0; marcas/`Clawd` excluidos | **Sí, el código MIT** | Sí para el código MIT. Docs: no comercial. No reutilizar assets de terceros ni voces marcadas |
| **howznguyen/shorts-kit** | **MIT** | Sí | Sí |
| **harry0703/MoneyPrinterTurbo** | **MIT** | Sí | Sí |
| **Chamanrajragu/purffle-shorts** | **MIT** | Sí | Sí |
| **JustCarlos30/skills-videos-cortos** | **MIT** | Sí (técnicas adaptadas, no copia literal) | Sí |
| **remotion-dev/template-prompt-to-video** | **UNLICENSED** (en `package.json`) | **No** (solo referencia de arquitectura) | No reutilizar su fuente |

> **Remotion en sí** es un producto de empresa con nivel gratuito pero **condiciones de licencia para empresas/uso comercial**. Debe revisarse por separado antes de cualquier explotación comercial (Fase 4). Ver `RISKS-ASSUMPTIONS.md`.

## Detalle por repo

### lemo-opuscar (referencia principal, ya clonado en `/projects/sandbox/lemo-opuscar`)

No es una plataforma web: es una **biblioteca de estilos de cine dirigida por código** (Node ≥20 + Python + FFmpeg). Su `LICENSE`/`REUSE.toml` separa por tipo de archivo: **el código es MIT** (`core/`, `tools/`, etc.), la documentación es CC BY-NC 4.0, los films son CC BY 4.0, y hay exclusiones explícitas (seis narraciones de demos, el personaje `Clawd` de Anthropic, marcas de terceros).

**Reutilizable (MIT) para nuestro motor de voz/audio y render:**

- `core/tts/tts.py` — Kokoro **offline** (TTS). Entrada `lines.json` `[{id,text,voice,speed,lang}]`, salida `out/<id>.wav` + `dur.json` con duraciones. Base del adaptador `KokoroTTS`.
- `core/tts/tts_zh.py` — edge-tts (online, gratuito), misma interfaz. Base del adaptador `EdgeTTS`.
- `core/tts/asr_check.py` — verificación/alineación con faster-whisper. Base del adaptador `FasterWhisper` para subtítulos.
- `core/render/srt.py` — exporta cues JSON a `.srt`. Reutilizable tal cual para subtítulos.
- `core/render/mux.sh` — mezcla final con FFmpeg: loudnorm a −14 LUFS, limitador true-peak, padding/−shortest. Patrón de referencia para `AudioMixer`/export. Requiere FFmpeg 7+ (disponible).
- `core/audio/sfx.py`, `sampler.py`, `pluck.py` — SFX y audio sintetizado (tier música usa numba).

Precaución documentada: Kokoro usa espeak-ng, que exige que la ruta de datos (tras symlinks) tenga < 160 bytes; `tts.py` ya hace una copia a `~/.cache` si la ruta es muy larga. No reutilizar las voces de demos excluidas ni assets de terceros.

### howznguyen/shorts-kit — MIT

Shorts verticales narrados con Remotion + Bun. Útil como referencia de: estructuras narrativas (>20 *story flows*), SFX libres de derechos, flujo de storyboard→voz→render con Remotion, scripts (`storyboard.ts`, `tts.ts`, `voice-lines.ts`, `beats.ts`). Deps: Remotion 4.0.540, React 19, `@remotion/three`, tailwind, `edge-tts-universal`. Al ser MIT, sus SFX y patrones de estructura narrativa pueden reutilizarse citando la licencia.

### harry0703/MoneyPrinterTurbo — MIT

Generación automatizada de video (Python + FastAPI + Streamlit). Útil como referencia de: generación de guion, **búsqueda de recursos de stock**, subtítulos sincronizados, video vertical y por lotes. Deps relevantes: `fastapi`, `edge_tts`, `faster-whisper`, `moviepy`, `openai`, `google-genai`, `redis` (cola). Confirma nuestras elecciones (FastAPI + edge-tts + faster-whisper) y el patrón de cola con Redis para Fase 4.

### Chamanrajragu/purffle-shorts — MIT

Generador open-source de Shorts en Python. Útil como referencia de: guion con **cualquier LLM (incluido Ollama)**, historias narradas con imágenes IA, simulaciones de chat, stock vs imágenes IA conmutables, servidor MCP. Refuerza la decisión de LLM conmutable con Ollama por defecto.

### JustCarlos30/skills-videos-cortos — MIT (técnicas adaptadas, Opción A)

Paquete en **español** de *skills* en Markdown para agentes de IA (Claude Code / OpenCode) para pensar, escribir, grabar y animar videos cortos, más un script Python real de post-producción de voz. **No es TTS** (no genera voz desde texto): procesa **voz grabada** por una persona. Licencia **MIT** → reutilizable con atribución, incluido uso comercial. El repo aclara que **no incluye** assets externos (música, SFX, modelos, fuentes, clips): esos tienen sus propios permisos y quedan fuera de esta licencia.

Contenido: `skills/cuestionar-guion`, `skills/voz-whisper`, `skills/referencias-opus`, `skills/motion`, `skills/sonido` (guías editoriales en Markdown) y `content-creator/` (workflows/plantillas). El único **código** es `skills/voz-whisper/scripts/voz.py`.

**`voz.py`** — pipeline de 8 pasos: entrada (incl. parser `.aup3` de Audacity) → limpieza FFmpeg (EQ/de-noise/de-esser/compresor) → detección de silencios → transcripción con **whisper.cpp (`whisper-cli`)** en español → extracción de **timings palabra por palabra** con diccionario de correcciones `{"mal":"bien"}` → alineación con tramos de habla → recorte de silencios con **re-mapeo de timings** → masterización a **−14 LUFS** (`loudnorm` de 2 pasadas + `alimiter`).

**Decisión de uso (Opción A — integración limpia, no copia literal):** se **adaptan tres técnicas** al motor de voz/audio (FEAT-004) como **código propio** coherente con nuestra arquitectura, acreditando el origen MIT:
1. Cadena de limpieza + **masterización a −14 LUFS** (loudnorm de 2 pasadas + limitador) → `engines/voice/master.py`.
2. **Recorte de silencios con re-mapeo de timings** → `engines/voice/trim.py`.
3. **Subtítulos palabra por palabra** + diccionario de correcciones `{"mal":"bien"}` (cubre la sección 3.G del prompt maestro) + backend opcional de **voz propia grabada**.

**No se adopta:** el binario `whisper-cli`/whisper.cpp (mantenemos **faster-whisper** como ASR por defecto del MVP; whisper.cpp queda como posible backend conmutable futuro), la estructura `projects/<slug>` + `video.json`, ni el parser `.aup3` de Audacity (fuera de alcance del MVP). Las *skills* en Markdown (criterios de guion, motion, sonido) quedan disponibles como **conocimiento/plantillas** editoriales, no como código.

### remotion-dev/template-prompt-to-video — UNLICENSED (solo arquitectura)

`package.json` declara `"license": "UNLICENSED"` y depende de `@elevenlabs/elevenlabs-js` por defecto. **No se reutiliza su código.** Se toma solo como **referencia de arquitectura**: prompt→guion, imagen por escena, locución (ElevenLabs/OpenAI), **timeline en JSON** y render con Remotion. Nuestra implementación del flujo timeline-JSON→Remotion es independiente y con defaults locales/gratuitos.

## Reglas aplicadas

- Analizar antes de incorporar; revisar dependencias y versiones; verificar licencia y uso comercial. Hecho.
- No copiar proyectos completos; mantener arquitectura propia. El único código que se envuelve directamente es el **MIT de lemo-opuscar** (voz/ASR/mux), con su atribución.
- Documentar integraciones (este archivo) y evitar duplicar funcionalidades ya implementadas (reutilizamos TTS/ASR/mux en vez de reescribirlos).
