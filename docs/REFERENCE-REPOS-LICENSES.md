# Repos de referencia y licencias

Se inspeccionó cada repositorio para identificar arquitectura, componentes reutilizables, dependencias y, de forma explícita, su **licencia y condiciones de uso comercial**. No se copian proyectos completos; la arquitectura de `audiovisual-ia` es propia e independiente. Esta verificación responde a la sección 9 del prompt maestro y a la condición de la sección 4.

## Resumen de licencias

| Repo | Licencia | ¿Reutilizable como código? | Uso comercial |
|------|----------|----------------------------|---------------|
| **lemo-opuscar** (local) | Código MIT (`core/`, `tools/`, `styleboard/`, `plugin/`); docs CC BY-NC 4.0; films CC BY 4.0; marcas/`Clawd` excluidos | **Sí, el código MIT** | Sí para el código MIT. Docs: no comercial. No reutilizar assets de terceros ni voces marcadas |
| **JustCarlos30/skills-videos-cortos** | **MIT** | **Sí (técnicas adaptadas, no copia literal)** | Sí |
| **howznguyen/shorts-kit** | **MIT** | Sí | Sí |
| **harry0703/MoneyPrinterTurbo** | **MIT** | Sí | Sí |
| **Chamanrajragu/purffle-shorts** | **MIT** | Sí | Sí |
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

### JustCarlos30/skills-videos-cortos — MIT (técnicas adaptadas, Opción A)

Repositorio de "skills" para producción de vídeos cortos. De su skill de voz
(`skills/voz-whisper/scripts/voz.py`, licencia **MIT**) se **adaptan** (reimplementación
propia, **sin copiar código**) varias técnicas de audio/voz que amplían el motor 5 en
FEAT-004. La arquitectura (grafos de filtros FFmpeg propios y capa de proveedores) es
independiente; solo se toma el enfoque:

- **Cadena de limpieza + masterización a −14 LUFS** → `app/engines/voice/master.py`.
  Limpieza opcional (`highpass` ~90 Hz, `afftdn` de-noise, de-esser, EQ suave de
  presencia, `acompressor`) seguida de `loudnorm` de **dos pasadas** (medir → aplicar
  con `measured_I/TP/LRA/thresh/offset`, `linear=true`) + `alimiter` sobremuestreado,
  apuntando a I=−14 LUFS y TP ≤ −1 dBFS. El `AudioMixer` reutiliza esta cadena.
- **Recorte de silencios con re-mapeo de timings** → `app/engines/voice/trim.py`.
  Detección con `silencedetect`, construcción de tramos de habla con márgenes pre/post,
  concatenación con crossfades cortos y **re-mapeo acumulado** de los `WordTiming` al
  nuevo eje temporal (misma lógica de desplazamiento que voz.py). Activable por config
  (`AIV_TRIM_SILENCE`); no desalinea voz y subtítulos.
- **Subtítulos palabra-por-palabra + diccionario de correcciones** →
  `app/engines/voice/subtitles.py`. Cues de una sola palabra, export de `palabras.json`
  (`[{text,startMs,endMs}]`) para el render, y un diccionario `{"mal":"bien"}` que
  reemplaza tokens mal transcritos repartiendo proporcionalmente los tiempos.
- **Backend opcional de voz propia grabada** → `app/engines/voice/recorded.py`
  (`AIV_TTS_BACKEND=recorded`): audio subido → limpieza/masterización → ASR → subtítulos,
  produciendo los mismos `Asset(voice/srt)` + `palabras.json` que el camino TTS.

**No se reutiliza** la estructura `projects/<slug>`, el `video.json` ni el parser de
proyectos `.aup3` de Audacity de voz.py (fuera del alcance del MVP). Se mantiene
**faster-whisper** como ASR por defecto (voz.py usa whisper.cpp/whisper-cli; no se adopta
ese binario ahora). Atribución reflejada también en `NOTICE`.

### howznguyen/shorts-kit — MIT

Shorts verticales narrados con Remotion + Bun. Útil como referencia de: estructuras narrativas (>20 *story flows*), SFX libres de derechos, flujo de storyboard→voz→render con Remotion, scripts (`storyboard.ts`, `tts.ts`, `voice-lines.ts`, `beats.ts`). Deps: Remotion 4.0.540, React 19, `@remotion/three`, tailwind, `edge-tts-universal`. Al ser MIT, sus SFX y patrones de estructura narrativa pueden reutilizarse citando la licencia.

### harry0703/MoneyPrinterTurbo — MIT

Generación automatizada de video (Python + FastAPI + Streamlit). Útil como referencia de: generación de guion, **búsqueda de recursos de stock**, subtítulos sincronizados, video vertical y por lotes. Deps relevantes: `fastapi`, `edge_tts`, `faster-whisper`, `moviepy`, `openai`, `google-genai`, `redis` (cola). Confirma nuestras elecciones (FastAPI + edge-tts + faster-whisper) y el patrón de cola con Redis para Fase 4.

### Chamanrajragu/purffle-shorts — MIT

Generador open-source de Shorts en Python. Útil como referencia de: guion con **cualquier LLM (incluido Ollama)**, historias narradas con imágenes IA, simulaciones de chat, stock vs imágenes IA conmutables, servidor MCP. Refuerza la decisión de LLM conmutable con Ollama por defecto.

### remotion-dev/template-prompt-to-video — UNLICENSED (solo arquitectura)

`package.json` declara `"license": "UNLICENSED"` y depende de `@elevenlabs/elevenlabs-js` por defecto. **No se reutiliza su código.** Se toma solo como **referencia de arquitectura**: prompt→guion, imagen por escena, locución (ElevenLabs/OpenAI), **timeline en JSON** y render con Remotion. Nuestra implementación del flujo timeline-JSON→Remotion es independiente y con defaults locales/gratuitos.

## Reglas aplicadas

- Analizar antes de incorporar; revisar dependencias y versiones; verificar licencia y uso comercial. Hecho.
- No copiar proyectos completos; mantener arquitectura propia. El único código que se envuelve directamente es el **MIT de lemo-opuscar** (voz/ASR/mux), con su atribución.
- Documentar integraciones (este archivo) y evitar duplicar funcionalidades ya implementadas (reutilizamos TTS/ASR/mux en vez de reescribirlos).
