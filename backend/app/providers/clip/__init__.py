"""Proveedores de la capacidad 'clip' (motor 4 — 3D/vídeo por escena, Fase 3).

Cada proveedor implementa el ``ClipProvider`` de ``providers.base`` (``clip_for``). El
default local/gratuito es ``blender`` (:class:`BlenderClipProvider`, que envuelve
``BlenderEngine3D`` de ``app.engines.engine3d``). Los proveedores de pago (Veo/Runway/
Kling) quedan como stubs conmutables (``paid.py``) que lanzan ``NotImplementedError`` al
construirse, preservando la vía de pago futura (ver docs/ENGINE-3D-ARCHITECTURE.md §3.2 y
§10) exactamente como conviven ``ElevenLabsTTS``/``OpenAITTS`` con los defaults locales.
"""
