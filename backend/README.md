# Backend — audiovisual-ia

API FastAPI de la plataforma prompt-to-video (Fase 1 / MVP 2D). Este paquete contiene
el andamiaje: app, configuración (Pydantic Settings) y los límites de módulo (motores,
proveedores, jobs, storage). La lógica de negocio se implementa en FEAT-002 en adelante.

## Arranque local

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # o: pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
# GET http://localhost:8000/health  ->  {"status": "ok"}
```

## Pruebas

```bash
cd backend
python -m pytest -q
```
