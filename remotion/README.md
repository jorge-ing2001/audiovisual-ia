# Remotion — audiovisual-ia

Proyecto Remotion que compone el video final a partir del timeline JSON producido por el
backend (imagen + voz + subtítulos por escena). El andamiaje (FEAT-001) incluye una
composición mínima (`Timeline`) que acepta props de timeline sin lógica de render todavía;
el ensamblado y el export MP4 se implementan en FEAT-005.

## Instalación

```bash
cd remotion
npm install
```

## Comandos

```bash
npm run studio      # abre Remotion Studio (previsualización)
npm run render      # render (requiere FFmpeg; ruta vía AIV_FFMPEG_BIN en el PC del usuario)
npm run typecheck   # tsc --noEmit
```
