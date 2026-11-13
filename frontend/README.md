# Frontend — audiovisual-ia

Interfaz Next.js (App Router) + TypeScript + Tailwind de la plataforma prompt-to-video
(Fase 1 / MVP 2D). El andamiaje (FEAT-001) incluye una página inicial que consulta
`GET /health` del backend y muestra el estado. Dashboard/editor se implementan en FEAT-007.

## Arranque local

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

La URL del backend es configurable con `NEXT_PUBLIC_API_URL` (por defecto `http://localhost:8000`).

## Verificación

```bash
npm run build      # build de producción
npm run lint       # next lint
npm run typecheck  # tsc --noEmit
```
