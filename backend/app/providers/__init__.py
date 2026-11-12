"""Capa de proveedores conmutables (base + implementaciones + registry).

Cada capacidad (LLM, imágenes, TTS, ASR) es una interfaz (Protocol) resuelta por un
registry según ``AIV_*_BACKEND``, con defaults locales/gratuitos y degradación
(stock->placeholder, kokoro->edge). Se implementa en FEAT-002/003/004.
"""
